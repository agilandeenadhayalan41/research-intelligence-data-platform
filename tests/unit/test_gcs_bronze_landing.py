"""DE-06 (#127): immutable Bronze landing through ingestion, offline (LOCAL evidence only).

Pipeline-level cases run on ``LocalObjectStore`` and on ``GCSObjectStore`` over
the in-memory ``FakeGCSClient`` (``[local|gcs_fake]``), and the guard cases on
both wrappers (``[works|deletions]``). No network, no credentials, no project or
bucket identifiers beyond the synthetic placeholders below. Every refused
landing asserts the raw key is absent; every LANDING case asserts the type of
``__cause__``.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import json
import socket
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import google_crc32c
import pytest
import yaml
from google.auth.exceptions import RefreshError
from google.cloud.storage.exceptions import DataCorruption, InvalidResponse

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.store import CanonicalConflictError
from research_platform.config import SampleSelectionConfig
from research_platform.config.loader import load_config
from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.control import (
    ControlStatus,
    FailureCategory,
    InMemoryControlStore,
    PipelineRun,
    PipelineRunStatus,
)
from research_platform.ingestion import (
    ingest_deletion_asset,
    ingest_works_asset,
    pipeline,
    run_openalex_deletions_local_ingest,
    run_openalex_works_local_ingest,
)
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionError,
    IngestionFormatError,
    RawLandingError,
    SourceChecksumMismatchError,
    SourceListingMismatchError,
    SourcePayloadIncompleteError,
    SourceSizeMismatchError,
)
from research_platform.ingestion.raw_landing import write_run_landing_manifest
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.connector import (
    OpenAlexConnector,
    OpenAlexFormatError,
    OpenAlexNetworkError,
    OpenAlexTruncatedError,
)
from research_platform.sources.openalex.deletion_metadata import (
    DELETION_PUBLIC_URI,
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.errors import (
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.landing_manifest import (
    LandingManifest,
    LandingManifestEntry,
    dumps_landing_manifest,
    read_landing_manifest,
)
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import (
    openalex_deletion_raw_object_key,
    openalex_landing_manifest_key,
    openalex_raw_object_key,
)
from tests.support.fake_gcs import FAKE_CALLER, FakeBlob, FakeGCSClient, FakeTransportError

BUCKET = "synthetic-bucket"
PROJECT = "synthetic-project"
STORES = ("local", "gcs_fake")
PIPELINES = ("works", "deletions")
FORBIDDEN_IN_LINEAGE = (BUCKET, PROJECT, "gs://", "googleapis", "example.invalid")


@pytest.fixture(autouse=True)
def _no_real_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regressed guard must fail here, never by building a real client or reading ADC."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline test reached a real GCS client or ADC")

    monkeypatch.setattr("google.cloud.storage.Client", refuse)
    monkeypatch.setattr("google.auth.default", refuse)


# --- payloads, assets and connectors -------------------------------------------


def _clock() -> Callable[[], datetime]:
    state = {"now": datetime(2026, 10, 17, 1, tzinfo=UTC)}

    def clock() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return clock


def _work(work_id: str) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}", "title": "T", "publication_date": "2020-01-01",
        "updated_date": "2024-01-10", "authorships": [], "topics": [], "referenced_works": [],
        "locations": [], "grants": [], "keywords": [], "mesh": [],
    }


def _payload(*ids: str) -> bytes:
    body = "\n".join(json.dumps(_work(i)) for i in ids) + "\n"
    return gzip.compress(body.encode("utf-8"), mtime=0)


def _deletions_payload(*ids: str) -> bytes:
    rows = "".join(f"https://openalex.org/{i},2024-02-01\n" for i in ids)
    return gzip.compress(f"work_id,deleted_date\n{rows}".encode("utf-8"), mtime=0)


def _asset(
    payload: bytes | None, *, part: str = "part_000.gz", snapshot: date = date(2024, 1, 15)
) -> OpenAlexAssetMetadata:
    return OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex", "snapshot_date": snapshot, "entity": "works",
            "file_uri": f"s3://openalex/data/jsonl/works/updated_date=2024-01-10/{part}",
            "byte_size": None if payload is None else len(payload),
            "updated_date": date(2024, 1, 10), "content_format": "jsonl",
        }
    )


def _deletion_asset(payload: bytes | None) -> OpenAlexDeletionAssetMetadata:
    return OpenAlexDeletionAssetMetadata.model_validate(
        {
            "source": "openalex", "snapshot_date": date(2024, 2, 5), "entity": "works-deletions",
            "file_uri": DELETION_PUBLIC_URI, "byte_size": None if payload is None else len(payload),
            "updated_date": date(2024, 2, 1), "content_format": "csv",
        }
    )


class _Interrupted(io.RawIOBase):
    def __init__(self, error: Exception) -> None:
        self._error = error

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        raise self._error


class _Connector:
    """Injected connector with plain ``BytesIO`` payloads (no response metadata)."""

    def __init__(self, asset: Any, payloads: list[bytes | Exception]) -> None:
        self._asset = asset
        self._payloads = list(payloads)
        self.fetch_calls = 0
        self.discover_calls = 0

    def discover_metadata(self) -> OpenAlexSampleSelection:
        self.discover_calls += 1
        return OpenAlexSampleSelection(
            max_files=1, max_file_size_bytes=25_000_000, eligible_count=1, selected=(self._asset,), skipped=()
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        self.fetch_calls += 1
        item = self._payloads[0] if len(self._payloads) == 1 else self._payloads.pop(0)
        if isinstance(item, Exception):
            return io.BufferedReader(_Interrupted(item))
        return io.BytesIO(item)


class _Resp:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status, self.headers, self.body, self.closed = status, headers, io.BytesIO(body), False

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def close(self) -> None:
        self.closed = True


class _ScriptedS3:
    """In-process S3 GET responses for the REAL ``OpenAlexConnector`` (no network)."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.served: list[_Resp] = []

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> _Resp:
        response = self._responses.pop(0)
        self.served.append(response)
        return response


def _s3(body: bytes, *, content_length: int | None = -1, etag: str | None = None) -> _Resp:
    headers = {"Content-Type": "binary/octet-stream", "x-amz-version-id": "v1"}
    if content_length == -1:
        content_length = len(body)
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    headers["ETag"] = f'"{etag or hashlib.md5(body).hexdigest()}"'
    return _Resp(200, headers, body)


class _RealConnector:
    """The real ``OpenAlexConnector.fetch`` over scripted responses; discovery is fixed."""

    def __init__(self, asset: OpenAlexAssetMetadata, responses: list[_Resp]) -> None:
        self._asset = asset
        self.client = _ScriptedS3(responses)
        self._connector = OpenAlexConnector(client=self.client, max_attempts=1)

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(
            max_files=1, max_file_size_bytes=25_000_000, eligible_count=1, selected=(self._asset,), skipped=()
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        return self._connector.fetch(asset)


# --- stores and the harness ------------------------------------------------------


def _config(
    tmp_path: Path, kind: str, *, environment: str | None = None, max_bytes: int = 25_000_000, max_files: int = 1
) -> Path:
    cfg: dict[str, Any] = {
        "environment": environment or ("local" if kind == "local" else "gcp-sandbox"),
        "storage": {"backend": "local", "landing_path": str(tmp_path / "landing")},
        "warehouse": {"analytical": "duckdb"},
        "sample_selection": {"max_files": max_files, "max_file_size_bytes": max_bytes},
    }
    if kind != "local":
        cfg["storage"] = {"backend": "gcs", "bucket": BUCKET}
        cfg["cloud"] = {"project_id": PROJECT}
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{kind}-{environment or 'default'}-{max_files}-{max_bytes}.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _gcs_store(client: FakeGCSClient) -> GCSObjectStore:
    return GCSObjectStore(StorageConfig(backend="gcs", bucket=BUCKET), CloudConfig(project_id=PROJECT), client=client)


class _Harness:
    def __init__(self, tmp_path: Path, kind: str, *, max_bytes: int = 25_000_000) -> None:
        self.kind = kind
        self.root = tmp_path / "landing"
        self.config = _config(tmp_path, kind, max_bytes=max_bytes)
        self.client = FakeGCSClient()
        if kind == "local":
            self.objects: ObjectStore = LocalObjectStore(StorageConfig(backend="local", landing_path=self.root))
        else:
            self.objects = _gcs_store(self.client)

    @property
    def bucket(self) -> Any:
        return self.client.bucket(BUCKET)

    @property
    def opt_in(self) -> bool:
        return self.kind != "local"

    def works(self, connector: Any, control: InMemoryControlStore | None = None, *, objects: Any = None) -> Any:
        return run_openalex_works_local_ingest(
            self.config, control_store=control or InMemoryControlStore(),
            canonical_store=InMemoryCanonicalStore(), object_store=objects or self.objects,
            connector=connector, now=_clock(), allow_gcs_landing=self.opt_in,
        )

    def deletions(self, connector: Any, asset: OpenAlexDeletionAssetMetadata,
                  control: InMemoryControlStore | None = None) -> Any:
        return run_openalex_deletions_local_ingest(
            self.config, deletion_asset=asset, control_store=control or InMemoryControlStore(),
            canonical_store=InMemoryCanonicalStore(), object_store=self.objects, connector=connector,
            now=_clock(), allow_gcs_landing=self.opt_in,
        )

    def raw_bytes(self, key: str) -> bytes:
        with self.objects.open(key) as handle:
            return handle.read()

    def assert_absent(self, key: str) -> None:
        with pytest.raises(ObjectNotFoundError):
            self.objects.open(key)

    def manifest(self, run_id: UUID, asset: Any) -> LandingManifest:
        key = openalex_landing_manifest_key(asset.entity, run_id, asset.asset_id)
        return read_landing_manifest(self.objects, key)

    def objects_snapshot(self) -> Any:
        if self.kind == "local":
            if not self.root.exists():
                return ()
            return tuple(
                (str(path.relative_to(self.root)), path.read_bytes())
                for path in sorted(self.root.rglob("*")) if path.is_file()
            )
        return {name: (o.data, o.generation, dict(o.metadata)) for name, o in self.bucket.objects.items()}

    def manifest_keys(self) -> list[str]:
        if self.kind == "local":
            return sorted(
                str(path.relative_to(self.root)) for path in self.root.rglob("landing.json")
            ) if self.root.exists() else []
        return sorted(name for name in self.bucket.objects if name.startswith("manifests/"))


@pytest.fixture(params=STORES)
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> _Harness:
    return _Harness(tmp_path, request.param)


@pytest.fixture
def gcs(tmp_path: Path) -> _Harness:
    return _Harness(tmp_path, "gcs_fake")


def _row(control: InMemoryControlStore, asset: Any) -> Any:
    row = control.get_source_file(asset.asset_id)
    assert row is not None
    return row


def _new_run(control: InMemoryControlStore) -> PipelineRun:
    started = datetime(2026, 10, 17, tzinfo=UTC)
    return control.create_pipeline_run(
        PipelineRun.model_validate(
            {
                "run_id": uuid4(), "source": "openalex", "pipeline_name": pipeline.PIPELINE_NAME,
                "status": PipelineRunStatus.PROCESSING, "attempt": 1, "started_at": started,
                "created_at": started, "updated_at": started,
            }
        )
    )


def _stage(harness: _Harness, asset: Any, connector: Any, run: PipelineRun, control: InMemoryControlStore,
           *, objects: Any = None, clock: Callable[[], datetime] | None = None) -> Any:
    return ingest_works_asset(
        asset=asset, run=run, config=load_config(harness.config), control=control,
        canonical=InMemoryCanonicalStore(), objects=objects or harness.objects, connector=connector,
        clock=clock or _clock(),
    )


def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _crc32c_b64(data: bytes) -> str:
    return base64.b64encode(google_crc32c.value(data).to_bytes(4, "big")).decode()


class _Wrapper(ObjectStore):
    """A foreign store that delegates to a first-party one (not checked against the config)."""

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        self.inner.put_if_absent(key, content, provenance)

    def open(self, key: str) -> BinaryIO:
        return self.inner.open(key)

    def stat(self, key: str) -> ObjectStat | None:
        return self.inner.stat(key)


# --- T03: immutable write, SHA-256, immutable replay ------------------------------


def test_t03_first_landing_writes_raw_object_and_manifest(harness: _Harness) -> None:
    payload = _payload("W1", "W2")
    asset = _asset(payload)
    result = harness.works(_Connector(asset, [payload]))
    assert result.run.status is PipelineRunStatus.SUCCESS
    key = openalex_raw_object_key(asset)
    assert harness.raw_bytes(key) == payload
    manifest = harness.manifest(result.run.run_id, asset)
    assert harness.manifest_keys() == [
        f"manifests/openalex/works/run_id={result.run.run_id}/asset_id={asset.asset_id}/landing.json"
    ]
    entry = manifest.entry
    assert (entry.raw_object_key, entry.sha256, entry.size_bytes) == (
        key, hashlib.sha256(payload).hexdigest(), len(payload)
    )
    assert entry.md5_hex == hashlib.md5(payload).hexdigest()
    assert (entry.landing_outcome, entry.committed_run_id) == ("created", result.run.run_id)
    assert manifest.storage_backend == ("local" if harness.kind == "local" else "gcs")
    if harness.kind == "gcs_fake":
        assert entry.checks == (
            "declared_size", "gzip_complete", "store_crc32c", "store_md5", "store_provenance", "store_size"
        )
        assert entry.object_generation == "1"
        assert (entry.object_md5_base64, entry.object_crc32c) == (_md5_b64(payload), _crc32c_b64(payload))
        assert set(harness.bucket.objects[key].metadata) == {"rp_provenance"}
    else:
        # Local: no store digests or generation; the declared size is not enforced for plain streams (J1).
        assert entry.checks == ("gzip_complete", "store_provenance", "store_size")
        assert entry.object_generation is entry.object_crc32c is entry.object_md5_base64 is None
    raw = dumps_landing_manifest(manifest)
    for forbidden in FORBIDDEN_IN_LINEAGE:
        assert forbidden.encode() not in raw


def test_t03_real_connector_landing_records_source_length_etag_and_version_in_the_manifest(
    harness: _Harness,
) -> None:
    payload = _payload("W1", "W2")
    asset = _asset(payload)
    result = harness.works(_RealConnector(asset, [_s3(payload)]))
    entry = harness.manifest(result.run.run_id, asset).entry
    assert entry.source_content_length == entry.declared_size_bytes == len(payload)
    assert entry.source_etag == entry.md5_hex == hashlib.md5(payload).hexdigest()
    assert entry.source_version_id == "v1"
    assert {"declared_size", "source_etag_md5", "gzip_complete"} <= set(entry.checks)


def test_t03_identical_replay_fetches_and_writes_nothing(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    connector = _Connector(asset, [payload])
    harness.works(connector, control)
    before = harness.objects_snapshot()
    replay = harness.works(connector, control)
    assert replay.skipped_reason == "ALREADY_SUCCESS"
    assert connector.fetch_calls == 1
    assert harness.objects_snapshot() == before


def test_t03_new_run_with_same_bytes_verifies_the_existing_object(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    first = harness.works(_Connector(asset, [payload]))
    second = harness.works(_Connector(asset, [payload]))  # fresh control store: new run, re-fetch
    assert second.run.status is PipelineRunStatus.SUCCESS
    first_entry = harness.manifest(first.run.run_id, asset).entry
    entry = harness.manifest(second.run.run_id, asset).entry
    assert entry.landing_outcome == "existing_object_verified"
    assert entry.committed_run_id == first.run.run_id
    assert entry.committed_retrieved_at == first_entry.retrieved_at
    assert entry.object_generation == first_entry.object_generation
    assert len(harness.manifest_keys()) == 2


def test_landing_manifest_is_canonical_and_create_only(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    result = harness.works(_Connector(asset, [payload]))
    manifest = harness.manifest(result.run.run_id, asset)
    raw = dumps_landing_manifest(manifest)
    assert raw.endswith(b"\n")
    assert raw == (json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert raw == dumps_landing_manifest(LandingManifest.model_validate_json(raw))
    before = harness.objects_snapshot()
    assert write_run_landing_manifest(harness.objects, manifest) == "written"  # identical bytes: store no-op
    retried = manifest.model_copy(
        update={"entry": manifest.entry.model_copy(update={"retrieved_at": manifest.entry.retrieved_at + timedelta(1)})}
    )
    assert write_run_landing_manifest(harness.objects, retried) == "existing_kept"  # same identity
    assert harness.objects_snapshot() == before
    other = manifest.model_copy(update={"entry": manifest.entry.model_copy(update={"sha256": "0" * 64})})
    with pytest.raises(RawLandingError, match="exists with different content") as raised:
        write_run_landing_manifest(harness.objects, other)
    assert isinstance(raised.value.__cause__, ObjectConflictError)
    assert harness.manifest(result.run.run_id, asset) == manifest
    stat = harness.objects.stat(manifest.key)
    assert stat is not None and stat.provenance is not None
    assert stat.provenance.source_uri == (
        f"urn:research-platform:openalex-landing-manifest:works:{result.run.run_id}:{asset.asset_id}"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("raw_object_key", "openalex/works/other/source.gz"), ("sha256", "0" * 64), ("size_bytes", 1)],
)
def test_landing_manifest_replay_refuses_a_different_identity(
    harness: _Harness, field: str, value: object
) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    result = harness.works(_Connector(asset, [payload]))
    manifest = harness.manifest(result.run.run_id, asset)
    other = manifest.model_copy(update={"entry": manifest.entry.model_copy(update={field: value})})
    with pytest.raises(RawLandingError, match="exists with different content") as raised:
        write_run_landing_manifest(harness.objects, other)
    assert isinstance(raised.value.__cause__, ObjectConflictError)
    assert harness.manifest(result.run.run_id, asset) == manifest


def test_unreadable_existing_landing_manifest_is_landing_not_kept(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    run = _new_run(control)
    data = b"{}\n"
    harness.objects.put_if_absent(
        openalex_landing_manifest_key("works", run.run_id, asset.asset_id), io.BytesIO(data),
        IngestionProvenance.model_validate({
            "run_id": run.run_id, "source": "research-platform", "source_uri": "urn:example:manifest",
            "retrieved_at": run.created_at, "sha256": hashlib.sha256(data).hexdigest(),
        }),
    )
    with pytest.raises(RawLandingError, match="exists with different content") as raised:
        _stage(harness, asset, _Connector(asset, [payload]), run, control)
    assert isinstance(raised.value.__cause__, ObjectConflictError)
    assert _row(control, asset).failure_category is FailureCategory.LANDING
    assert len(harness.manifest_keys()) == 1


def test_landing_manifest_rejects_unsorted_checks_and_a_created_entry_from_another_run(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    result = harness.works(_Connector(asset, [payload]))
    data = harness.manifest(result.run.run_id, asset).model_dump()
    assert len(data["entry"]["checks"]) > 1
    LandingManifest.model_validate(data)
    with pytest.raises(ValueError, match="sorted and unique"):
        LandingManifestEntry.model_validate(dict(data["entry"], checks=tuple(reversed(data["entry"]["checks"]))))
    with pytest.raises(ValueError, match="committed by this run"):
        LandingManifest.model_validate(dict(data, run_id=uuid4()))


def test_same_run_retry_keeps_the_first_manifest(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    run = _new_run(control)
    real_publish = pipeline.publish_claimed_asset_memory
    calls = {"n": 0}

    def publish_once_failing(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise CanonicalConflictError("injected canonical failure after landing")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(pipeline, "publish_claimed_asset_memory", publish_once_failing)
    clock = _clock()
    with pytest.raises(CanonicalConflictError):
        _stage(harness, asset, _Connector(asset, [payload]), run, control, clock=clock)
    assert _row(control, asset).failure_category is FailureCategory.CANONICAL
    source_file, _, skipped = _stage(harness, asset, _Connector(asset, [payload]), run, control, clock=clock)
    assert source_file.status is ControlStatus.SUCCESS and skipped is None
    assert len(harness.manifest_keys()) == 1
    entry = harness.manifest(run.run_id, asset).entry
    assert (entry.landing_outcome, entry.committed_run_id) == ("created", run.run_id)


def test_two_assets_in_one_run_each_land_and_have_their_own_manifest(harness: _Harness) -> None:
    control = InMemoryControlStore()
    run = _new_run(control)
    first_payload, second_payload = _payload("W1"), _payload("W2")
    first, second = _asset(first_payload, part="part_000.gz"), _asset(second_payload, part="part_001.gz")
    for asset, payload in ((first, first_payload), (second, second_payload)):
        source_file, _, _ = _stage(harness, asset, _Connector(asset, [payload]), run, control)
        assert source_file.status is ControlStatus.SUCCESS
    keys = harness.manifest_keys()
    assert len(keys) == 2
    assert {key.rsplit("/", 2)[0] for key in keys} == {f"manifests/openalex/works/run_id={run.run_id}"}
    assert harness.manifest(run.run_id, first).entry.sha256 == hashlib.sha256(first_payload).hexdigest()
    assert harness.manifest(run.run_id, second).entry.sha256 == hashlib.sha256(second_payload).hexdigest()


class _ManifestPutFails(_Wrapper):
    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        if key.startswith("manifests/"):
            raise ConnectionError("connection reset")
        super().put_if_absent(key, content, provenance)


@pytest.mark.parametrize("failure", ["conflict", "transport"])
def test_manifest_write_failure_after_raw_landing_is_landing_and_next_run_verifies(
    harness: _Harness, failure: str
) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    run = _new_run(control)
    objects: ObjectStore = harness.objects
    if failure == "conflict":
        decoy = LandingManifest(
            entity="works", pipeline_name="decoy", run_id=run.run_id, run_created_at=run.created_at,
            storage_backend="local" if harness.kind == "local" else "gcs",
            entry=LandingManifestEntry(
                asset_id=asset.asset_id, raw_object_key="openalex/works/decoy/source.gz",
                source_uri=asset.file_uri, snapshot_date=asset.snapshot_date, updated_date=asset.updated_date,
                content_format="jsonl", declared_size_bytes=None, size_bytes=1, sha256="0" * 64,
                md5_hex="0" * 32, source_content_length=None, source_etag=None, source_version_id=None,
                retrieved_at=run.created_at, landing_outcome="existing_object_verified", committed_run_id=None,
                committed_retrieved_at=None, object_generation=None, object_crc32c=None,
                object_md5_base64=None, checks=(),
            ),
        )
        assert write_run_landing_manifest(harness.objects, decoy) == "written"
    else:
        objects = _ManifestPutFails(harness.objects)
    with pytest.raises(RawLandingError) as raised:
        _stage(harness, asset, _Connector(asset, [payload]), run, control, objects=objects)
    expected_cause = ObjectConflictError if failure == "conflict" else ConnectionError
    assert isinstance(raised.value.__cause__, expected_cause)
    assert _row(control, asset).failure_category is FailureCategory.LANDING
    assert harness.raw_bytes(openalex_raw_object_key(asset)) == payload
    retry = harness.works(_Connector(asset, [payload]))
    entry = harness.manifest(retry.run.run_id, asset).entry
    assert entry.landing_outcome == "existing_object_verified" and entry.committed_run_id == run.run_id
    assert [key for key in harness.manifest_keys() if f"run_id={retry.run.run_id}/" in key] == [
        openalex_landing_manifest_key("works", retry.run.run_id, asset.asset_id)
    ]


# --- T04: changed bytes under an existing key ---------------------------------------


def test_t04_changed_bytes_refuse_overwrite_and_keep_the_original(harness: _Harness) -> None:
    """Store-level case: a plain stream with ``byte_size=None`` so the bytes reach the store."""
    original, changed = _payload("W1"), _payload("W2")
    asset = _asset(None)
    first = harness.works(_Connector(asset, [original]))
    first_manifest = harness.manifest(first.run.run_id, asset)
    control = InMemoryControlStore()
    with pytest.raises(ObjectConflictError):
        harness.works(_Connector(asset, [changed]), control)
    row = _row(control, asset)
    assert row.status is ControlStatus.FAILED and row.failure_category is FailureCategory.CHECKSUM
    assert harness.raw_bytes(openalex_raw_object_key(asset)) == original
    if harness.kind == "gcs_fake":
        assert harness.bucket.objects[openalex_raw_object_key(asset)].generation == 1
    assert len(harness.manifest_keys()) == 1
    assert harness.manifest(first.run.run_id, asset) == first_manifest


def test_t04_same_size_rewrite_from_the_real_connector_is_checksum_and_keeps_original_object_and_manifest(
    harness: _Harness,
) -> None:
    original = _payload("W1", "W2")
    changed = bytearray(original)
    changed[-5] ^= 0xFF  # same length, different bytes; the ETag below matches the new bytes
    asset = _asset(original)
    first = harness.works(_RealConnector(asset, [_s3(original)]))
    first_manifest = harness.manifest(first.run.run_id, asset)
    control = InMemoryControlStore()
    with pytest.raises(ObjectConflictError):
        harness.works(_RealConnector(asset, [_s3(bytes(changed))]), control)
    assert _row(control, asset).failure_category is FailureCategory.CHECKSUM
    assert harness.raw_bytes(openalex_raw_object_key(asset)) == original
    assert len(harness.manifest_keys()) == 1
    assert harness.manifest(first.run.run_id, asset) == first_manifest


# --- T05: truncated or interrupted transfer, then recovery ---------------------------


def test_t05_short_clean_eof_without_content_length_lands_nothing_then_recovers(harness: _Harness) -> None:
    payload = _payload("W1", "W2", "W3")
    asset = _asset(payload)
    control = InMemoryControlStore()
    connector = _RealConnector(
        asset, [_s3(payload[:-7], content_length=None, etag=hashlib.md5(payload).hexdigest()), _s3(payload)]
    )
    before = harness.objects_snapshot()
    with pytest.raises(SourceSizeMismatchError):
        harness.works(connector, control)
    row = _row(control, asset)
    assert row.status is ControlStatus.FAILED and row.failure_category is FailureCategory.RETRIEVAL
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.objects_snapshot() == before
    retry = harness.works(connector, control)
    assert retry.run.status is PipelineRunStatus.SUCCESS
    assert harness.raw_bytes(openalex_raw_object_key(asset)) == payload


def test_t05_content_length_unlike_manifest_size_is_refused_before_reading(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    connector = _RealConnector(asset, [_s3(payload + b"x")])
    before = harness.objects_snapshot()
    with pytest.raises(SourceListingMismatchError):
        harness.works(connector, control)
    assert _row(control, asset).failure_category is FailureCategory.VALIDATION
    (response,) = connector.client.served
    assert response.body.tell() == 0 and response.closed
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.objects_snapshot() == before


def test_t05_single_part_etag_unlike_md5_is_refused_before_landing(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    before = harness.objects_snapshot()
    with pytest.raises(SourceChecksumMismatchError):
        harness.works(_RealConnector(asset, [_s3(payload, etag="0" * 32)]), control)
    assert _row(control, asset).failure_category is FailureCategory.CHECKSUM
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.objects_snapshot() == before


def test_t03_multipart_etag_is_recorded_but_not_compared_with_md5(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    multipart = "0" * 32 + "-3"  # most real OpenAlex JSONL parts carry a multipart ETag
    result = harness.works(_RealConnector(asset, [_s3(payload, etag=multipart)]))
    assert result.run.status is PipelineRunStatus.SUCCESS
    entry = harness.manifest(result.run.run_id, asset).entry
    assert entry.source_etag == multipart
    assert "source_etag_md5" not in entry.checks and "declared_size" in entry.checks


def test_t05_real_connector_truncation_after_partial_body_is_retrieval_and_lands_nothing(
    harness: _Harness,
) -> None:
    payload = _payload(*(f"W{i}" for i in range(40)))
    asset = _asset(payload)
    control = InMemoryControlStore()
    half = payload[: len(payload) // 2]
    connector = _RealConnector(asset, [_s3(half, content_length=len(payload)), _s3(payload)])
    before = harness.objects_snapshot()
    with pytest.raises(IngestionError) as raised:
        harness.works(connector, control)
    assert isinstance(raised.value.__cause__, OpenAlexTruncatedError)
    assert _row(control, asset).failure_category is FailureCategory.RETRIEVAL  # UNKNOWN before DE-06
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.objects_snapshot() == before
    assert harness.works(connector, control).run.status is PipelineRunStatus.SUCCESS


@pytest.mark.parametrize(
    "error", [OpenAlexTruncatedError("short"), OpenAlexNetworkError("reset")], ids=["truncated", "network"]
)
def test_t05_connector_stream_failures_are_retrieval(harness: _Harness, error: Exception) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(IngestionError):
        harness.works(_Connector(asset, [error, payload]), control)
    assert _row(control, asset).failure_category is FailureCategory.RETRIEVAL
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.works(_Connector(asset, [payload]), control).run.status is PipelineRunStatus.SUCCESS


def test_t05_truncated_plain_gzip_never_lands(harness: _Harness) -> None:
    payload = _payload(*(f"W{i}" for i in range(20)))
    asset = _asset(None)
    control = InMemoryControlStore()
    before = harness.objects_snapshot()
    with pytest.raises(SourcePayloadIncompleteError):
        harness.works(_Connector(asset, [payload[: len(payload) // 2]]), control)
    assert _row(control, asset).failure_category is FailureCategory.RETRIEVAL
    harness.assert_absent(openalex_raw_object_key(asset))
    assert harness.objects_snapshot() == before


def test_t05_truncated_deletion_csv_gz_never_lands(harness: _Harness) -> None:
    payload = _deletions_payload(*(f"W{i}" for i in range(50)))
    asset = _deletion_asset(None)
    control = InMemoryControlStore()
    before = harness.objects_snapshot()
    with pytest.raises(SourcePayloadIncompleteError):
        harness.deletions(_Connector(asset, [payload[:-6]]), asset, control)
    assert _row(control, asset).failure_category is FailureCategory.RETRIEVAL
    harness.assert_absent(openalex_deletion_raw_object_key(asset))
    assert harness.objects_snapshot() == before


@pytest.mark.parametrize("kind", PIPELINES)
def test_gcs_backend_requires_the_declared_size_for_plain_streams(tmp_path: Path, kind: str) -> None:
    make, describe = (_payload, _asset) if kind == "works" else (_deletions_payload, _deletion_asset)
    payload = make("W1")
    asset: Any = describe(make("W1", "W2"))  # declares more bytes than the complete gzip that arrives

    def run(harness: _Harness) -> Any:
        connector = _Connector(asset, [payload])
        return harness.works(connector) if kind == "works" else harness.deletions(connector, asset)

    gcs = _Harness(tmp_path / "gcs", "gcs_fake")
    with pytest.raises(SourceSizeMismatchError):
        run(gcs)
    assert gcs.bucket.objects == {}
    # Not enforced on the local backend, where existing fixtures declare fictitious sizes (risk J1).
    assert run(_Harness(tmp_path / "local", "local")).run.status is PipelineRunStatus.SUCCESS


def test_t05_interrupted_gcs_upload_lands_nothing_then_retry_creates(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    gcs.bucket.interrupt_upload_after_bytes = 10
    with pytest.raises(RawLandingError) as raised:
        gcs.works(_Connector(asset, [payload]), control)
    assert isinstance(raised.value.__cause__, FakeTransportError)
    row = _row(control, asset)
    assert row.status is ControlStatus.FAILED and row.failure_category is FailureCategory.LANDING
    assert gcs.bucket.objects == {}
    retry = gcs.works(_Connector(asset, [payload]), control)
    assert gcs.manifest(retry.run.run_id, asset).entry.landing_outcome == "created"


def test_t05_non_retryable_error_after_commit_recovers_on_retry(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    gcs.bucket.raise_after_upload_commit = True
    with pytest.raises(RawLandingError) as raised:
        gcs.works(_Connector(asset, [payload]), control)
    assert isinstance(raised.value.__cause__, FakeTransportError)
    failed = _row(control, asset)
    assert failed.failure_category is FailureCategory.LANDING
    assert list(gcs.bucket.objects) == [openalex_raw_object_key(asset)]
    retry = gcs.works(_Connector(asset, [payload]), control)
    entry = gcs.manifest(retry.run.run_id, asset).entry
    assert (entry.landing_outcome, entry.object_generation) == ("existing_object_verified", "1")
    assert entry.committed_run_id == failed.run_id != retry.run.run_id


# --- negative cases: identifiers and error categories ----------------------------------


def test_permission_denied_while_landing_is_landing_and_leaks_no_identifier(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    gcs.bucket.fail_permission = True
    with pytest.raises(RawLandingError) as raised:
        gcs.works(_Connector(asset, [payload]), control)
    cause = str(raised.value.__cause__)
    assert BUCKET in cause and FAKE_CALLER in cause  # the realistic provider text carries both
    row = _row(control, asset)
    assert row.failure_category is FailureCategory.LANDING
    for text in (str(raised.value), row.failure_message or ""):
        assert BUCKET not in text and "example.invalid" not in text and "googleapis" not in text


class _TypeErrorStore(_Wrapper):
    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        raise TypeError("a programming error")


def test_programming_errors_are_not_reported_as_landing(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, "local")
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(IngestionError, match="^ingestion failed$") as raised:
        harness.works(_Connector(asset, [payload]), control, objects=_TypeErrorStore(harness.objects))
    assert type(raised.value) is IngestionError and isinstance(raised.value.__cause__, TypeError)
    assert _row(control, asset).failure_category is FailureCategory.UNKNOWN


_PROVIDER_ERRORS: dict[str, Callable[[], BaseException]] = {
    "data_corruption": lambda: DataCorruption(None, "digest mismatch"),
    "invalid_response": lambda: InvalidResponse(None, "bad response"),
    "refresh_error": lambda: RefreshError("token refresh failed"),
}


class _RawPutRaises(_Wrapper):
    def __init__(self, inner: ObjectStore, make: Callable[[], BaseException]) -> None:
        super().__init__(inner)
        self._make = make

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        if key.startswith("openalex/"):
            raise self._make()
        super().put_if_absent(key, content, provenance)


@pytest.mark.parametrize("error", list(_PROVIDER_ERRORS))
def test_every_allowlisted_provider_error_is_landing(gcs: _Harness, error: str) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    expected = type(_PROVIDER_ERRORS[error]())
    with pytest.raises(RawLandingError, match="immutable landing failed") as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_RawPutRaises(gcs.objects, _PROVIDER_ERRORS[error]))
    assert type(raised.value.__cause__) is expected
    assert _row(control, asset).failure_category is FailureCategory.LANDING


class _ManifestConflictReadRaises(_Wrapper):
    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        if key.startswith("manifests/"):
            raise ObjectConflictError("exists")
        super().put_if_absent(key, content, provenance)

    def open(self, key: str) -> BinaryIO:
        if key.startswith("manifests/"):
            raise RefreshError("token refresh failed")
        return super().open(key)


def test_provider_error_reading_an_existing_manifest_is_landing(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError, match="manifest write failed") as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_ManifestConflictReadRaises(gcs.objects))
    assert isinstance(raised.value.__cause__, RefreshError)
    assert _row(control, asset).failure_category is FailureCategory.LANDING


def test_source_format_error_is_validation(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(IngestionError) as raised:
        harness.works(_Connector(asset, [OpenAlexFormatError("unsupported format")]), control)
    assert isinstance(raised.value.__cause__, OpenAlexFormatError)
    assert _row(control, asset).failure_category is FailureCategory.VALIDATION  # UNKNOWN before DE-06
    harness.assert_absent(openalex_raw_object_key(asset))


class _StatOverride(_Wrapper):
    def __init__(self, inner: ObjectStore, change: Callable[[ObjectStat], ObjectStat | None]) -> None:
        super().__init__(inner)
        self._change = change

    def stat(self, key: str) -> ObjectStat | None:
        stat = self.inner.stat(key)
        assert stat is not None
        return self._change(stat) if key.startswith("openalex/") else stat


_STAT_CHANGES: dict[str, tuple[Callable[[ObjectStat], ObjectStat], type[BaseException] | None, str]] = {
    "size": (lambda s: replace(s, size_bytes=s.size_bytes + 1), None, "size differs"),
    "md5": (lambda s: replace(s, md5_base64=_md5_b64(b"other")), None, "md5 differs"),
    "unreadable_md5": (lambda s: replace(s, md5_base64="not base64!"), binascii.Error, "md5 is unreadable"),
    "crc32c": (lambda s: replace(s, crc32c=_crc32c_b64(b"other")), None, "crc32c differs"),
    "provenance": (
        lambda s: replace(s, provenance=s.provenance.model_copy(update={"sha256": "0" * 64})),
        None,
        "provenance differs",
    ),
}


@pytest.mark.parametrize("change", list(_STAT_CHANGES))
def test_stat_mismatch_after_landing_is_landing_and_writes_no_manifest(gcs: _Harness, change: str) -> None:
    mutate, cause, message = _STAT_CHANGES[change]
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError, match=message) as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_StatOverride(gcs.objects, mutate))
    if cause is None:
        assert raised.value.__cause__ is None
    else:
        assert isinstance(raised.value.__cause__, cause)
    assert _row(control, asset).failure_category is FailureCategory.LANDING
    assert gcs.raw_bytes(openalex_raw_object_key(asset)) == payload
    assert gcs.manifest_keys() == []


def test_gcs_backend_store_without_stat_fails_closed(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError, match="store did not report object facts") as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_StatOverride(gcs.objects, lambda s: None))
    assert raised.value.__cause__ is None
    assert _row(control, asset).failure_category is FailureCategory.LANDING
    assert gcs.manifest_keys() == []


def test_local_backend_store_without_stat_lands_with_the_put_outcome_and_null_object_facts(tmp_path: Path) -> None:
    local = _Harness(tmp_path, "local")
    payload = _payload("W1")
    asset = _asset(payload)
    no_stat = _StatOverride(local.objects, lambda s: None)
    first = local.works(_Connector(asset, [payload]), objects=no_stat)
    entry = local.manifest(first.run.run_id, asset).entry
    assert (entry.landing_outcome, entry.committed_run_id, entry.object_generation) == ("created", None, None)
    assert "store_size" not in entry.checks
    second = local.works(_Connector(asset, [payload]), objects=no_stat)  # fresh control store: new run
    entry = local.manifest(second.run.run_id, asset).entry
    assert (entry.landing_outcome, entry.committed_run_id) == ("existing_object_verified", None)


class _StatRaises(_Wrapper):
    def stat(self, key: str) -> ObjectStat | None:
        raise FakeTransportError("transport interrupted")


def test_stat_provider_error_is_landing_not_unknown(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError, match="verification failed") as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_StatRaises(gcs.objects))
    assert isinstance(raised.value.__cause__, FakeTransportError)
    assert _row(control, asset).failure_category is FailureCategory.LANDING


class _OpenRaises(_Wrapper):
    def open(self, key: str) -> BinaryIO:
        raise FakeTransportError("transport interrupted")


def test_sha_rescue_provider_error_is_landing_not_unknown(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    gcs.works(_Connector(asset, [payload]))
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError, match="verification failed") as raised:
        gcs.works(_Connector(asset, [payload]), control, objects=_OpenRaises(gcs.objects))
    assert isinstance(raised.value.__cause__, FakeTransportError)
    assert _row(control, asset).failure_category is FailureCategory.LANDING


# --- deletions use the same landing ----------------------------------------------------


def test_deletion_landing_writes_its_manifest_and_classifies_retrieval(harness: _Harness) -> None:
    payload = _deletions_payload("W9")
    asset = _deletion_asset(payload)
    control = InMemoryControlStore()
    with pytest.raises(IngestionError) as raised:
        harness.deletions(_Connector(asset, [OpenAlexNetworkError("reset")]), asset, control)
    assert isinstance(raised.value.__cause__, OpenAlexNetworkError)
    assert _row(control, asset).failure_category is FailureCategory.RETRIEVAL  # UNKNOWN before DE-06
    result = harness.deletions(_Connector(asset, [payload]), asset, control)
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert harness.manifest_keys() == [
        f"manifests/openalex/works-deletions/run_id={result.run.run_id}/asset_id={asset.asset_id}/landing.json"
    ]
    manifest = harness.manifest(result.run.run_id, asset)
    assert manifest.storage_backend == ("local" if harness.kind == "local" else "gcs")
    entry = manifest.entry
    assert entry.raw_object_key == openalex_deletion_raw_object_key(asset)
    assert entry.landing_outcome == "created" and "gzip_complete" in entry.checks


def test_deletion_wrapper_refuses_a_missing_connector_before_any_run(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    with pytest.raises(IngestionConfigError, match="explicit connector"):
        run_openalex_deletions_local_ingest(
            _config(tmp_path, "local"), deletion_asset=_deletion_asset(None), control_store=control
        )
    assert control._runs == {}


# --- opt-in guards, factory, key layout ----------------------------------------------


class _CountingConnector(_Connector):
    def __init__(self) -> None:
        super().__init__(_asset(None), [b""])


def _wrapper_call(kind: str, config: Path, **kwargs: Any) -> Any:
    if kind == "works":
        return run_openalex_works_local_ingest(config, **kwargs)
    kwargs.setdefault("connector", _CountingConnector())
    return run_openalex_deletions_local_ingest(config, deletion_asset=_deletion_asset(None), **kwargs)


@pytest.mark.parametrize("kind", PIPELINES)
def test_gcs_config_without_opt_in_is_refused_before_any_client_call(gcs: _Harness, kind: str) -> None:
    connector = _CountingConnector()
    with pytest.raises(IngestionConfigError, match="requires environment=local"):
        _wrapper_call(kind, gcs.config, object_store=gcs.objects, connector=connector)
    with pytest.raises(IngestionConfigError, match="requires environment=local"):
        _wrapper_call(kind, gcs.config, connector=connector)  # factory path: no client is built
    assert gcs.client.buckets == {} and connector.fetch_calls == 0


@pytest.mark.parametrize("kind", PIPELINES)
def test_opt_in_with_local_backend_is_refused(tmp_path: Path, kind: str) -> None:
    with pytest.raises(IngestionConfigError, match="allow_gcs_landing requires storage.backend=gcs"):
        _wrapper_call(kind, _config(tmp_path, "local"), connector=_CountingConnector(), allow_gcs_landing=True)


@pytest.mark.parametrize("environment", ["local", "dev", "qa", "prod"])
@pytest.mark.parametrize("kind", PIPELINES)
def test_opt_in_allows_only_the_gcp_sandbox_environment(tmp_path: Path, kind: str, environment: str) -> None:
    config = _config(tmp_path, "gcs_fake", environment=environment)
    with pytest.raises(IngestionConfigError, match="gcp-sandbox only"):
        _wrapper_call(kind, config, connector=_CountingConnector(), allow_gcs_landing=True)


def test_opt_in_without_injected_connector_is_refused_before_any_network(
    gcs: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[object] = []

    def no_network(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    with pytest.raises(IngestionConfigError, match="requires an injected connector"):
        run_openalex_works_local_ingest(gcs.config, allow_gcs_landing=True)
    assert attempts == [] and gcs.client.buckets == {}


@pytest.mark.parametrize(("max_files", "max_bytes"), [(2, 25_000_000), (1, 25_000_001)])
@pytest.mark.parametrize("kind", PIPELINES)
def test_t01_opt_in_keeps_the_one_file_and_byte_bounds(
    tmp_path: Path, kind: str, max_files: int, max_bytes: int
) -> None:
    config = _config(tmp_path, "gcs_fake", max_files=max_files, max_bytes=max_bytes)
    with pytest.raises(IngestionConfigError, match="max_file"):
        _wrapper_call(kind, config, connector=_CountingConnector(), allow_gcs_landing=True)


@pytest.mark.parametrize("kind", STORES)
def test_t01_payload_over_the_byte_cap_is_refused_before_any_upload(tmp_path: Path, kind: str) -> None:
    harness = _Harness(tmp_path, kind, max_bytes=1000)
    payload = b"x" * 1502
    asset = _asset(None)
    control = InMemoryControlStore()
    with pytest.raises(IngestionFormatError, match="max_file_size_bytes"):
        harness.works(_Connector(asset, [payload]), control)
    assert _row(control, asset).failure_category is FailureCategory.VALIDATION
    assert harness.bucket.upload_calls == []
    assert harness.objects_snapshot() in ((), {})


def test_t01_gcs_opt_in_discovery_skips_over_limit_assets_and_fetches_only_the_selected_one(
    gcs: _Harness,
) -> None:
    payload = _payload("W1")
    base = "s3://openalex/data/jsonl/works/updated_date=2024-01-10"
    manifest = json.dumps({
        "date": "2024-01-15", "format": "jsonl", "entity": "works",
        "files": [
            {"url": f"{base}/part_000.gz", "meta": {"content_length": len(payload)}},
            {"url": f"{base}/part_001.gz", "meta": {"content_length": 25_000_001}},
        ],
    }).encode()
    client = _ScriptedS3([_Resp(200, {"Content-Type": "application/json"}, manifest), _s3(payload)])
    connector = OpenAlexConnector(client=client, max_attempts=1, sample_selection=SampleSelectionConfig())
    selection = OpenAlexConnector(
        client=_ScriptedS3([_Resp(200, {"Content-Type": "application/json"}, manifest)]), max_attempts=1
    ).discover_metadata()
    assert [asset.file_uri for asset in selection.selected] == [f"{base}/part_000.gz"]
    result = gcs.works(connector)
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert len(client.served) == 2  # the manifest and the one selected part, nothing else
    (selected,) = selection.selected
    assert sorted(gcs.bucket.objects) == sorted(
        [openalex_raw_object_key(selected), openalex_landing_manifest_key("works", result.run.run_id, selected.asset_id)]
    )


@pytest.mark.parametrize("kind", PIPELINES)
def test_injected_store_must_match_the_configured_backend(tmp_path: Path, kind: str) -> None:
    gcs_store = _gcs_store(FakeGCSClient())
    control = InMemoryControlStore()
    connector = _CountingConnector()
    with pytest.raises(IngestionConfigError, match="GCSObjectStore requires storage.backend=gcs"):
        _wrapper_call(
            kind, _config(tmp_path, "local"), object_store=gcs_store, connector=connector, control_store=control
        )
    assert connector.discover_calls == connector.fetch_calls == 0 and control._runs == {}  # refused before a run
    local_store = LocalObjectStore(StorageConfig(backend="local", landing_path=tmp_path / "landing"))
    with pytest.raises(IngestionConfigError, match="LocalObjectStore requires storage.backend=local"):
        _wrapper_call(
            kind, _config(tmp_path, "gcs_fake"), object_store=local_store, connector=_CountingConnector(),
            allow_gcs_landing=True,
        )


@pytest.mark.parametrize("kind", PIPELINES)
def test_stage_functions_refuse_a_store_that_does_not_match_the_config(tmp_path: Path, kind: str) -> None:
    client = FakeGCSClient()
    control = InMemoryControlStore()
    run = _new_run(control)
    asset: Any = _asset(None) if kind == "works" else _deletion_asset(None)
    stage = ingest_works_asset if kind == "works" else ingest_deletion_asset
    with pytest.raises(IngestionConfigError, match="GCSObjectStore requires storage.backend=gcs"):
        stage(
            asset=asset, run=run, config=load_config(_config(tmp_path, "local")), control=control,
            canonical=InMemoryCanonicalStore(), objects=_gcs_store(client), connector=_CountingConnector(),
        )
    assert control.get_source_file(asset.asset_id) is None
    assert client.buckets == {}


@pytest.mark.parametrize("environment", ["dev", "qa", "prod"])
@pytest.mark.parametrize("kind", PIPELINES)
def test_stage_functions_refuse_a_gcs_backend_outside_gcp_sandbox(
    tmp_path: Path, kind: str, environment: str
) -> None:
    client = FakeGCSClient()
    control = InMemoryControlStore()
    run = _new_run(control)
    asset: Any = _asset(None) if kind == "works" else _deletion_asset(None)
    stage = ingest_works_asset if kind == "works" else ingest_deletion_asset
    config = load_config(_config(tmp_path, "gcs_fake", environment=environment))
    with pytest.raises(IngestionConfigError, match="storage.backend=gcs requires environment=gcp-sandbox"):
        stage(
            asset=asset, run=run, config=config, control=control, canonical=InMemoryCanonicalStore(),
            objects=_gcs_store(client), connector=_CountingConnector(),
        )
    assert control.get_source_file(asset.asset_id) is None
    assert client.buckets == {}


def test_factory_selects_the_store_without_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    local_config = load_config(_config(tmp_path, "local"))
    assert isinstance(object_store_from_config(local_config), LocalObjectStore)
    store = object_store_from_config(load_config(_config(tmp_path, "gcs_fake")))
    assert isinstance(store, GCSObjectStore) and store._client is None
    with pytest.raises(ValueError, match="only valid with storage.backend=gcs"):
        object_store_from_config(local_config, gcs_client=FakeGCSClient())


def test_manifest_keys_are_leaf_directories_separate_from_raw(tmp_path: Path) -> None:
    run_id = UUID(int=7)
    asset = "oa-" + "a" * 64
    key = openalex_landing_manifest_key("works", run_id, asset)
    assert key == f"manifests/openalex/works/run_id={run_id}/asset_id={asset}/landing.json"
    assert not key.startswith("openalex/")
    assert key != openalex_landing_manifest_key("works-deletions", run_id, asset)
    for entity, asset_id in (("Works", asset), ("works/../x", asset), ("", asset), ("works", "a/b"), ("works", "")):
        with pytest.raises(InvalidObjectKeyError):
            openalex_landing_manifest_key(entity, run_id, asset_id)
    with pytest.raises(InvalidObjectKeyError):
        openalex_landing_manifest_key("works", str(run_id), asset)  # type: ignore[arg-type]
    # Leaf invariant on LocalObjectStore: a run directory only holds asset directories.
    store = LocalObjectStore(StorageConfig(backend="local", landing_path=tmp_path / "landing"))
    data = b"{}\n"
    provenance = IngestionProvenance.model_validate(
        {
            "run_id": run_id, "source": "research-platform", "source_uri": "urn:example:manifest",
            "retrieved_at": datetime(2026, 10, 17, tzinfo=UTC), "sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    store.put_if_absent(key, io.BytesIO(data), provenance)
    with pytest.raises(IncompleteObjectError):
        store.put_if_absent(f"manifests/openalex/works/run_id={run_id}/landing.json", io.BytesIO(data), provenance)


# --- rollback compatibility and prior-release visibility --------------------------------


def test_raw_provenance_shape_is_unchanged_for_rollback(harness: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    harness.works(_Connector(asset, [payload]))
    key = openalex_raw_object_key(asset)
    if harness.kind == "local":
        text = (harness.root / key).parent.joinpath("provenance.json").read_text(encoding="utf-8")
    else:
        text = harness.bucket.objects[key].metadata["rp_provenance"]
    assert set(json.loads(text)) == set(IngestionProvenance.model_fields) == {
        "run_id", "source", "source_uri", "retrieved_at", "sha256"
    }


def test_prior_release_stays_readable_after_a_failed_next_release(harness: _Harness) -> None:
    prior_payload, next_payload = _payload("W1"), _payload("W1", "W2")
    prior = _asset(prior_payload, snapshot=date(2026, 9, 23))
    nxt = _asset(next_payload, snapshot=date(2026, 10, 14))
    first = harness.works(_Connector(prior, [prior_payload]))
    prior_manifest = harness.manifest(first.run.run_id, prior)
    truncated = _s3(next_payload[:-5], content_length=None, etag=hashlib.md5(next_payload).hexdigest())
    with pytest.raises(SourceSizeMismatchError):
        harness.works(_RealConnector(nxt, [truncated]))
    assert harness.raw_bytes(openalex_raw_object_key(prior)) == prior_payload
    assert harness.manifest(first.run.run_id, prior) == prior_manifest
    harness.assert_absent(openalex_raw_object_key(nxt))
    assert len(harness.manifest_keys()) == 1


def test_next_release_lands_beside_prior_release_with_both_manifests(harness: _Harness) -> None:
    prior_payload, next_payload = _payload("W1"), _payload("W1", "W2")
    prior = _asset(prior_payload, snapshot=date(2026, 9, 23))
    nxt = _asset(next_payload, snapshot=date(2026, 10, 14))
    first = harness.works(_Connector(prior, [prior_payload]))
    second = harness.works(_Connector(nxt, [next_payload]))
    assert openalex_raw_object_key(prior) != openalex_raw_object_key(nxt)
    assert harness.raw_bytes(openalex_raw_object_key(prior)) == prior_payload
    assert harness.raw_bytes(openalex_raw_object_key(nxt)) == next_payload
    assert harness.manifest(first.run.run_id, prior).entry.raw_object_key == openalex_raw_object_key(prior)
    assert harness.manifest(second.run.run_id, nxt).entry.raw_object_key == openalex_raw_object_key(nxt)
    assert len(harness.manifest_keys()) == 2


def test_stat_reports_no_version_when_the_store_has_none(gcs: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    real_load = FakeBlob._load

    def load_without_generation(self: FakeBlob, stored: Any) -> None:
        real_load(self, stored)
        self.generation = None

    monkeypatch.setattr(FakeBlob, "_load", load_without_generation)
    payload = _payload("W1")
    asset = _asset(payload)
    result = gcs.works(_Connector(asset, [payload]))
    stat = gcs.objects.stat(openalex_raw_object_key(asset))
    assert stat is not None and stat.version is None
    entry = gcs.manifest(result.run.run_id, asset).entry
    assert entry.object_generation is None and entry.landing_outcome == "created"


def test_gcs_stat_without_a_reported_size_is_incomplete(gcs: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    gcs.works(_Connector(asset, [payload]))
    real_load = FakeBlob._load

    def load_without_size(self: FakeBlob, stored: Any) -> None:
        real_load(self, stored)
        self.size = None

    monkeypatch.setattr(FakeBlob, "_load", load_without_size)
    with pytest.raises(IncompleteObjectError, match="did not report the object size"):
        gcs.objects.stat(openalex_raw_object_key(asset))


def test_retention_context_is_declared_not_enforced(gcs: _Harness) -> None:
    payload = _payload("W1")
    asset = _asset(payload)
    result = gcs.works(_Connector(asset, [payload]))
    retention = json.loads(dumps_landing_manifest(gcs.manifest(result.run.run_id, asset)))["retention"]
    assert retention == {
        "bronze_rule": "adr-0001-d1-bronze-retained-in-full",
        "bucket_retention_policy_checked": False,
        "object_holds_set": False,
        "storage_lifecycle_implements_source_deletions": False,
    }
    assert {name: set(o.metadata) for name, o in gcs.bucket.objects.items()} == {
        name: {"rp_provenance"} for name in gcs.bucket.objects
    }
