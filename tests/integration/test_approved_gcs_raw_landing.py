"""DE-06 (#127): LIVE, opt-in Bronze landing checks against a real GCS bucket.

Deselected by ``make test`` (``-m "not gcs_live"``) and skipped unless
``GCS_LIVE_TESTS=1``. Run only through ``make test-gcs-live``, by the repository
owner, in a project the owner controls, after the owner's own approval.
Nothing here reads OpenAlex: payloads are synthetic and reach the Works pipeline
through an injected source that wraps the real ``OpenAlexConnector.fetch`` over
scripted in-process responses. Writes go only under ``de06-live/<run-token>/``
in the named, existing bucket, within the caps in ``tests/support/
gcs_live_harness.py``. The test code never lists or removes objects; the owner
removes the run prefix afterwards.

Evidence: ``GCS_LIVE_EVIDENCE_DIR/<run-token>/summary.json`` (outside the
repository) records PASS or FAIL per check without project, bucket or account
names. ``label_candidate`` is only a candidate: the PR that records a run picks
the label. A run is never enterprise evidence. Post ``summary.json`` only; never
paste console output or ``junit.xml``, which can hold the bucket, project, caller
and host names.

What a run proves: ``GCSObjectStore`` plus ``land_raw_asset`` plus landing
manifests through the Works wrapper against real GCS (create-only, a real 412,
an interrupted session, a non-retryable error after commit, ``stat`` facts, no
identifiers). It does not prove the production key layout (keys are prefixed),
the store/config guard (the harness store is a foreign type), the deletion path,
multi-chunk sessions, concurrent writers or a lost response recovered by the
library.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import platform
from collections.abc import Iterator
from datetime import date
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO

import pytest
import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.config.loader import load_config
from research_platform.control import ControlStatus, FailureCategory, InMemoryControlStore
from research_platform.ingestion import run_openalex_works_local_ingest
from research_platform.ingestion.errors import RawLandingError, SourceSizeMismatchError
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.connector import OpenAlexConnector
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.errors import ObjectConflictError, ObjectNotFoundError
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.landing_manifest import LandingManifestEntry, read_landing_manifest
from research_platform.storage.openalex_layout import (
    openalex_landing_manifest_key,
    openalex_raw_object_key,
)
from tests.support import gcs_live_harness as harness

LIVE = os.environ.get("GCS_LIVE_TESTS", "").strip() == "1"
pytestmark = [
    pytest.mark.gcs_live,
    pytest.mark.skipif(not LIVE, reason="set GCS_LIVE_TESTS=1 (make test-gcs-live) to write to a real bucket"),
]
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = date(2000, 1, 1)  # synthetic partition: never a real OpenAlex release
CHECKS_EXPECTED = 6


def _work(n: int) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/W99{n:08d}", "title": f"synthetic {n}", "publication_date": "2000-01-01",
        "updated_date": "2000-01-01", "authorships": [], "topics": [], "referenced_works": [],
        "locations": [], "grants": [], "keywords": [], "mesh": [],
    }


def _payload(*ns: int) -> bytes:
    return gzip.compress(("\n".join(json.dumps(_work(n)) for n in ns) + "\n").encode(), mtime=0)


def _asset(token: str, name: str, payload: bytes) -> OpenAlexAssetMetadata:
    return OpenAlexAssetMetadata.model_validate({
        "source": "openalex", "snapshot_date": SNAPSHOT, "entity": "works",
        "file_uri": f"s3://openalex/data/jsonl/works/updated_date=2000-01-01/part_{token}_{name}.gz",
        "byte_size": len(payload), "updated_date": SNAPSHOT, "content_format": "jsonl",
    })


class _Resp:
    def __init__(self, body: bytes, *, content_length: bool) -> None:
        self.status, self._body, self.closed = 200, io.BytesIO(body), False
        self.headers = {"Content-Type": "binary/octet-stream"}
        if content_length:
            self.headers["Content-Length"] = str(len(body))

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


class _Source:
    """Synthetic source: the REAL ``OpenAlexConnector.fetch`` over scripted responses,
    or a plain stream with no response metadata (``plain=True``)."""

    def __init__(self, asset: OpenAlexAssetMetadata, bodies: list[tuple[bytes, bool]], *, plain: bool = False) -> None:
        self.asset, self._bodies, self._plain = asset, list(bodies), plain
        self._connector = OpenAlexConnector(client=self, max_attempts=1)

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> _Resp:
        body, content_length = self._bodies.pop(0)
        return _Resp(body, content_length=content_length)

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(max_files=1, max_file_size_bytes=harness.MAX_OBJECT_BYTES, eligible_count=1,
                                       selected=(self.asset,), skipped=())

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        if self._plain:
            return io.BytesIO(self._bodies.pop(0)[0])
        return self._connector.fetch(asset)


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    settings, client, session, credential_type = harness.open_live_client(os.environ, REPO_ROOT)
    token = harness.run_token()
    out = settings.evidence_dir / token
    out.mkdir(parents=True, exist_ok=False)
    config_path = tmp_path_factory.mktemp("cfg") / "sandbox.yaml"
    config_path.write_text(yaml.safe_dump({  # placeholders only: identifiers stay in the environment
        "environment": "gcp-sandbox", "storage": {"backend": "gcs", "bucket": "${GCS_LIVE_BUCKET}"},
        "cloud": {"project_id": "${GOOGLE_CLOUD_PROJECT}"}, "warehouse": {"analytical": "duckdb"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": harness.MAX_OBJECT_BYTES},
    }), encoding="utf-8")
    inner = object_store_from_config(load_config(config_path), gcs_client=client)
    if not isinstance(inner, GCSObjectStore):
        harness.refuse("the configured store is not a GCSObjectStore")
    state = SimpleNamespace(
        token=token, config=config_path, session=session, client=client, settings=settings,
        store=harness.BudgetedPrefixStore(inner, token), recorder=harness.CheckRecorder(),
    )
    try:
        yield state
    finally:
        summary = {
            "run_token": token, "prefix": state.store.prefix, "credential_type": credential_type,
            "project_class": settings.project_class, "label_candidate": harness.LABEL_CANDIDATE,
            "checks_expected": CHECKS_EXPECTED, "checks": state.recorder.results,
            "checks_passed": state.recorder.passed, "put_attempts": state.store.put_attempts,
            "put_bytes": state.store.put_bytes, "http_requests": session.http_requests,
            "library_delete_attempts": session.library_delete_attempts,
            "precondition_failed_at": session.precondition_failed_at, "logical_keys": state.store.keys,
            "google_cloud_storage_version": version("google-cloud-storage"),
            "python_version": platform.python_version(),
        }
        harness.write_summary(out / "summary.json", summary, (settings.bucket, settings.project))


def _ingest(live: SimpleNamespace, source: _Source, control: InMemoryControlStore) -> Any:
    return run_openalex_works_local_ingest(
        live.config, control_store=control, canonical_store=InMemoryCanonicalStore(),
        object_store=live.store, connector=source, allow_gcs_landing=True,
    )


def _entry(live: SimpleNamespace, run_id: Any, asset: OpenAlexAssetMetadata) -> LandingManifestEntry:
    key = openalex_landing_manifest_key("works", run_id, asset.asset_id)
    return read_landing_manifest(live.store, key).entry


def _absent(live: SimpleNamespace, key: str) -> bool:
    try:
        live.store.stat(key)
    except ObjectNotFoundError:
        return True
    return False


@pytest.fixture(scope="module")
def landed_a(live: SimpleNamespace) -> SimpleNamespace:
    payload = _payload(1, 2, 3)
    asset = _asset(live.token, "a", payload)
    control = InMemoryControlStore()
    try:
        result = _ingest(live, _Source(asset, [(payload, True)]), control)
        entry = _entry(live, result.run.run_id, asset)
    except Exception as error:
        harness.refuse(f"landing asset A failed ({type(error).__name__})")
    if entry.landing_outcome != "created":
        harness.refuse("landing asset A did not create the object")
    return SimpleNamespace(asset=asset, payload=payload, control=control, run_id=result.run.run_id, entry=entry)


def test_live_partial_file_lands_nothing_then_recovery_creates(live: SimpleNamespace) -> None:
    with live.recorder.check("partial_file_lands_nothing_then_recovery_creates") as facts:
        payload = _payload(10, 11, 12)
        asset = _asset(live.token, "partial", payload)
        control = InMemoryControlStore()
        source = _Source(asset, [(payload[:-9], False), (payload, True)])
        with pytest.raises(SourceSizeMismatchError):
            _ingest(live, source, control)
        row = control.get_source_file(asset.asset_id)
        assert row.status is ControlStatus.FAILED and row.failure_category is FailureCategory.RETRIEVAL
        assert _absent(live, openalex_raw_object_key(asset))
        result = _ingest(live, source, control)
        entry = _entry(live, result.run.run_id, asset)
        assert entry.landing_outcome == "created" and entry.sha256 == hashlib.sha256(payload).hexdigest()
        assert entry.object_generation and entry.object_crc32c and entry.object_md5_base64
        assert entry.committed_run_id == result.run.run_id
        facts["checks"] = list(entry.checks)


def test_live_identical_replay_is_a_no_op_and_a_new_run_verifies(live: SimpleNamespace, landed_a: SimpleNamespace) -> None:
    with live.recorder.check("identical_replay_is_a_no_op_and_a_new_run_verifies") as facts:
        puts, requests = live.store.put_attempts, live.session.http_requests
        replay = _ingest(live, _Source(landed_a.asset, [(landed_a.payload, True)]), landed_a.control)
        assert replay.skipped_reason == "ALREADY_SUCCESS"
        assert (live.store.put_attempts, live.session.http_requests) == (puts, requests)
        before_412 = len(live.session.precondition_failed_at)
        again = _ingest(live, _Source(landed_a.asset, [(landed_a.payload, True)]), InMemoryControlStore())
        entry = _entry(live, again.run.run_id, landed_a.asset)
        assert entry.landing_outcome == "existing_object_verified"
        assert entry.object_generation == landed_a.entry.object_generation
        assert entry.committed_run_id == landed_a.run_id
        assert len(live.session.precondition_failed_at) > before_412, "a new run must meet the create-only precondition"
        facts["precondition_failed_at"] = live.session.precondition_failed_at[before_412]


def test_live_conflicting_bytes_refuse_and_keep_the_original(live: SimpleNamespace, landed_a: SimpleNamespace) -> None:
    with live.recorder.check("conflicting_bytes_refuse_and_keep_the_original"):
        changed = bytearray(landed_a.payload)
        changed[-5] ^= 0xFF  # same length, different bytes and hash; a plain stream, so no ETag check
        control = InMemoryControlStore()
        with pytest.raises(ObjectConflictError):
            _ingest(live, _Source(landed_a.asset, [(bytes(changed), False)], plain=True), control)
        assert control.get_source_file(landed_a.asset.asset_id).failure_category is FailureCategory.CHECKSUM
        key = openalex_raw_object_key(landed_a.asset)
        with live.store.open(key) as handle:
            assert handle.read() == landed_a.payload
        assert live.store.stat(key).version == landed_a.entry.object_generation


def test_live_interrupted_upload_leaves_no_object_then_retry_creates(live: SimpleNamespace) -> None:
    with live.recorder.check("interrupted_upload_leaves_no_object_then_retry_creates"):
        payload = _payload(20, 21)
        asset = _asset(live.token, "interrupted", payload)
        control = InMemoryControlStore()
        live.session.abort_next_chunk_before_send = True
        with pytest.raises(RawLandingError) as raised:
            _ingest(live, _Source(asset, [(payload, True)]), control)
        assert isinstance(raised.value.__cause__, harness.InjectedAbort)
        assert control.get_source_file(asset.asset_id).failure_category is FailureCategory.LANDING
        assert _absent(live, openalex_raw_object_key(asset))
        result = _ingest(live, _Source(asset, [(payload, True)]), control)
        assert _entry(live, result.run.run_id, asset).landing_outcome == "created"


def test_live_non_retryable_error_after_commit_recovers_on_retry(live: SimpleNamespace) -> None:
    with live.recorder.check("non_retryable_error_after_commit_recovers_on_retry"):
        payload = _payload(30)
        asset = _asset(live.token, "after-commit", payload)
        control = InMemoryControlStore()
        live.session.raise_after_next_final_chunk = True
        with pytest.raises(RawLandingError) as raised:
            _ingest(live, _Source(asset, [(payload, True)]), control)
        assert isinstance(raised.value.__cause__, harness.InjectedAbort)
        failed_run = control.get_source_file(asset.asset_id).run_id
        assert not _absent(live, openalex_raw_object_key(asset))
        result = _ingest(live, _Source(asset, [(payload, True)]), control)
        entry = _entry(live, result.run.run_id, asset)
        assert entry.landing_outcome == "existing_object_verified" and entry.committed_run_id == failed_run


def test_live_lineage_holds_no_identifiers(live: SimpleNamespace, landed_a: SimpleNamespace) -> None:
    with live.recorder.check("lineage_holds_no_identifiers") as facts:
        keys = sorted({key for key in live.store.keys if key.startswith(("openalex/", "manifests/"))})
        texts: list[str] = []
        checked = 0
        for key in keys:
            if _absent(live, key):
                continue
            blob = live.client.bucket(live.settings.bucket).blob(live.store.physical_key(key))
            blob.reload()
            texts.append(json.dumps(blob.metadata or {}, sort_keys=True))
            if key.startswith("manifests/"):
                with live.store.open(key) as handle:
                    texts.append(handle.read().decode("utf-8"))
            checked += 1
        assert checked >= 2
        forbidden = (live.settings.bucket, live.settings.project, "gs://", "googleapis.com")
        if harness.contains_any(texts, forbidden):
            harness.refuse("identifier found in lineage")
        facts["objects_checked"] = checked
