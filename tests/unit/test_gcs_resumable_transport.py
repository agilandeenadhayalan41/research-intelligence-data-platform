"""DE-06 (#127): GCSObjectStore and Works landing through the REAL google-cloud-storage
client over an in-memory JSON-API transport (offline; LOCAL evidence only).

``FakeGcsHttp`` models the resumable-upload protocol of the pinned
google-cloud-storage 3.16.0; the GCS server behaviour it encodes (precondition
until finalization, finalized-session answers) is not verified against real GCS.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import socket
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO
from uuid import UUID, uuid4

import google.api_core.retry.retry_base as retry_base
import google.api_core.retry.retry_unary as retry_unary
import google_crc32c
import pytest
import yaml
from google.api_core.exceptions import BadRequest, Forbidden, RetryError
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.control import FailureCategory, InMemoryControlStore, PipelineRunStatus
from research_platform.ingestion import run_openalex_works_local_ingest
from research_platform.ingestion.errors import RawLandingError
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.errors import ObjectConflictError
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.landing_manifest import LandingManifestEntry, read_landing_manifest
from research_platform.storage.local import dumps_provenance
from research_platform.storage.openalex_layout import (
    openalex_landing_manifest_key,
    openalex_raw_object_key,
)
from tests.support.fake_gcs_http import BUCKET, CALLER, FakeGcsHttp

PROJECT = "synthetic-project"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the C extension the library falls back to MD5 and these tests would
    # exercise a different checksum path.
    assert google_crc32c.implementation == "c"
    # No background bucket-metadata GET (it needs storage.buckets.get).
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    # One fake clock for both retry modules: sleeping advances monotonic time, so
    # retry deadlines expire without waiting.
    state = {"now": 1_000.0}

    def sleep(seconds: float) -> None:
        state["now"] += seconds

    clock = SimpleNamespace(sleep=sleep, monotonic=lambda: state["now"], time=lambda: state["now"])
    monkeypatch.setattr(retry_unary, "time", clock)
    monkeypatch.setattr(retry_base, "time", clock)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", no_network)


def _store(http: FakeGcsHttp) -> GCSObjectStore:
    client = storage.Client(project=PROJECT, credentials=AnonymousCredentials(), _http=http)
    return GCSObjectStore(StorageConfig(backend="gcs", bucket=BUCKET), CloudConfig(project_id=PROJECT), client=client)


def _payload(*ids: str) -> bytes:
    records = [
        {
            "id": f"https://openalex.org/{work_id}", "title": "T", "publication_date": "2020-01-01",
            "updated_date": "2024-01-10", "authorships": [], "topics": [], "referenced_works": [],
            "locations": [], "grants": [], "keywords": [], "mesh": [],
        }
        for work_id in ids or ("W1",)
    ]
    return gzip.compress(("\n".join(json.dumps(r) for r in records) + "\n").encode(), mtime=0)


PAYLOAD = _payload()
ASSET = OpenAlexAssetMetadata.model_validate({
    "source": "openalex", "snapshot_date": date(2024, 1, 15), "entity": "works",
    "file_uri": "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part_000.gz",
    "byte_size": len(PAYLOAD), "updated_date": date(2024, 1, 10), "content_format": "jsonl",
})
RAW_KEY = openalex_raw_object_key(ASSET)


class _Connector:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(
            max_files=1, max_file_size_bytes=25_000_000, eligible_count=1, selected=(ASSET,), skipped=()
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        return io.BytesIO(self._payload)


def _clock() -> Callable[[], datetime]:
    state = {"now": datetime(2026, 10, 17, 1, tzinfo=UTC)}

    def clock() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return clock


def _ingest(
    tmp_path: Path, store: GCSObjectStore, payload: bytes = PAYLOAD, control: InMemoryControlStore | None = None
) -> Any:
    config = tmp_path / "sandbox.yaml"
    config.write_text(yaml.safe_dump({
        "environment": "gcp-sandbox", "storage": {"backend": "gcs", "bucket": BUCKET},
        "cloud": {"project_id": PROJECT}, "warehouse": {"analytical": "duckdb"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": 25_000_000},
    }), encoding="utf-8")
    return run_openalex_works_local_ingest(
        config, control_store=control or InMemoryControlStore(), canonical_store=InMemoryCanonicalStore(),
        object_store=store, connector=_Connector(payload), now=_clock(), allow_gcs_landing=True,
    )


def _entry(store: GCSObjectStore, run_id: UUID) -> LandingManifestEntry:
    return read_landing_manifest(store, openalex_landing_manifest_key("works", run_id, ASSET.asset_id)).entry


def _provenance(data: bytes, run_id: UUID | None = None) -> IngestionProvenance:
    return IngestionProvenance.model_validate({
        "run_id": run_id or uuid4(), "source": "openalex", "source_uri": ASSET.file_uri,
        "retrieved_at": datetime(2026, 10, 17, tzinfo=UTC), "sha256": hashlib.sha256(data).hexdigest(),
    })


def _puts(http: FakeGcsHttp) -> list[tuple[str, str, dict[str, str]]]:
    return [entry for entry in http.log if entry[0] == "PUT"]


def test_every_put_is_one_resumable_create_only_session_with_a_crc32c_final_chunk() -> None:
    http = FakeGcsHttp()
    data = random.Random(0).randbytes(25_000_000)  # the per-object cap: one chunk (library chunk is 100 MiB)
    _store(http).put_if_absent("openalex/works/x/source.gz", io.BytesIO(data), _provenance(data))
    post, put = http.log
    assert post[0] == "POST" and post[2]["uploadType"] == "resumable" and post[2]["ifGenerationMatch"] == "0"
    assert put[0] == "PUT" and put[2]["content-range"] == "bytes 0-24999999/25000000"
    assert put[2]["x-goog-hash"].startswith("crc32c=")
    assert http.objects["openalex/works/x/source.gz"]["data"] == data


def test_stat_reads_metadata_only_and_reports_generation_digests_and_provenance(tmp_path: Path) -> None:
    http = FakeGcsHttp()
    store = _store(http)
    result = _ingest(tmp_path, store)
    http.log.clear()
    stat = store.stat(RAW_KEY)
    assert [(method, "/download/" in path) for method, path, _ in http.log] == [("GET", False)]
    resource = http.resource(RAW_KEY)
    assert (stat.size_bytes, stat.version) == (len(PAYLOAD), resource["generation"])
    assert (stat.crc32c, stat.md5_base64) == (resource["crc32c"], resource["md5Hash"])
    assert stat.provenance is not None
    assert (stat.provenance.run_id, stat.provenance.sha256) == (result.run.run_id, hashlib.sha256(PAYLOAD).hexdigest())


def test_dropped_chunk_is_resent_by_the_library_and_lands_once(tmp_path: Path) -> None:
    http = FakeGcsHttp(drop_next_chunk=True)
    store = _store(http)
    result = _ingest(tmp_path, store)
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert len(_puts(http)) >= 2  # the dropped chunk, the status query and the resend
    assert sorted(http.objects) == sorted([RAW_KEY, openalex_landing_manifest_key("works", result.run.run_id, ASSET.asset_id)])
    assert http.objects[RAW_KEY]["data"] == PAYLOAD
    assert _entry(store, result.run.run_id).landing_outcome == "created"


def test_rejected_chunk_leaves_no_object_is_landing_and_the_retry_creates(tmp_path: Path) -> None:
    http = FakeGcsHttp(reject_next_chunk=True)
    store = _store(http)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError) as raised:
        _ingest(tmp_path, store, control=control)
    assert isinstance(raised.value.__cause__, BadRequest)
    assert http.objects == {}
    assert control.get_source_file(ASSET.asset_id).failure_category is FailureCategory.LANDING
    retry = _ingest(tmp_path, store, control=control)
    assert _entry(store, retry.run.run_id).landing_outcome == "created"


def test_retry_exhausted_upload_is_landing_and_leaves_no_object(tmp_path: Path) -> None:
    http = FakeGcsHttp(always_503=True)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError) as raised:
        _ingest(tmp_path, _store(http), control=control)
    assert isinstance(raised.value.__cause__, RetryError)
    assert control.get_source_file(ASSET.asset_id).failure_category is FailureCategory.LANDING
    assert http.objects == {}
    assert len(_puts(http)) >= 2  # retried; the exact count depends on jitter


@pytest.mark.parametrize("point", ["initiation", "finalization"])
def test_new_run_verifies_the_existing_object_wherever_the_precondition_fails(tmp_path: Path, point: Any) -> None:
    http = FakeGcsHttp(precondition_at=point)
    store = _store(http)
    first = _ingest(tmp_path, store)
    generation = http.resource(RAW_KEY)["generation"]
    second = _ingest(tmp_path, store)  # fresh control store: a new run re-fetches and re-uploads
    assert http.precondition_failed_at == [point]
    entry = _entry(store, second.run.run_id)
    assert (entry.landing_outcome, entry.object_generation) == ("existing_object_verified", generation)
    assert entry.committed_run_id == first.run.run_id
    assert http.resource(RAW_KEY)["generation"] == generation


@pytest.mark.parametrize("racer_bytes", ["same", "different"])
def test_racing_writer_at_finalization_is_never_overwritten(tmp_path: Path, racer_bytes: str) -> None:
    data = PAYLOAD if racer_bytes == "same" else _payload("W2")
    racer_run = uuid4()
    metadata = {"rp_provenance": dumps_provenance(_provenance(data, racer_run)).decode("utf-8")}
    http = FakeGcsHttp(racer=(data, metadata))
    store = _store(http)
    control = InMemoryControlStore()
    if racer_bytes == "same":
        result = _ingest(tmp_path, store, control=control)
        entry = _entry(store, result.run.run_id)
        assert (entry.landing_outcome, entry.committed_run_id) == ("existing_object_verified", racer_run)
        assert entry.object_generation == "1"
    else:
        with pytest.raises(ObjectConflictError):
            _ingest(tmp_path, store, control=control)
        assert control.get_source_file(ASSET.asset_id).failure_category is FailureCategory.CHECKSUM
    assert http.precondition_failed_at == ["finalization"]
    stored = http.objects[RAW_KEY]
    assert (stored["data"], stored["generation"], stored["metadata"]) == (data, 1, metadata)


def test_lost_final_response_is_recovered_by_the_library_and_records_created(tmp_path: Path) -> None:
    http = FakeGcsHttp(lose_final_response_after_commit=True)
    store = _store(http)
    result = _ingest(tmp_path, store)
    assert result.run.status is PipelineRunStatus.SUCCESS
    entry = _entry(store, result.run.run_id)
    assert (entry.landing_outcome, entry.committed_run_id) == ("created", result.run.run_id)
    assert http.objects[RAW_KEY]["generation"] == 1  # stored once, not re-created


def test_reported_digest_mismatch_is_landing_writes_no_manifest_and_the_library_delete_is_refused(
    tmp_path: Path,
) -> None:
    http = FakeGcsHttp(corrupt_reported_digests=True)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError) as raised:
        _ingest(tmp_path, _store(http), control=control)
    assert isinstance(raised.value.__cause__, Forbidden)  # the library's own delete, refused
    assert control.get_source_file(ASSET.asset_id).failure_category is FailureCategory.LANDING
    assert [method for method, *_ in http.log].count("DELETE") == 1
    assert not any(name.startswith("manifests/") for name in http.objects)


def test_forbidden_create_is_landing_and_no_identifier_reaches_the_error_or_control_row(tmp_path: Path) -> None:
    http = FakeGcsHttp(forbid_create=True)
    control = InMemoryControlStore()
    with pytest.raises(RawLandingError) as raised:
        _ingest(tmp_path, _store(http), control=control)
    assert isinstance(raised.value.__cause__, Forbidden)
    cause = str(raised.value.__cause__)
    assert BUCKET in cause and CALLER in cause  # the real error text carries both
    row = control.get_source_file(ASSET.asset_id)
    assert row.failure_category is FailureCategory.LANDING
    for text in (str(raised.value), row.failure_message or ""):
        assert BUCKET not in text and CALLER not in text and "googleapis" not in text
    assert http.objects == {}


def test_identical_replay_issues_no_storage_requests(tmp_path: Path) -> None:
    http = FakeGcsHttp()
    control = InMemoryControlStore()
    store = _store(http)
    _ingest(tmp_path, store, control=control)
    before = len(http.log)
    replay = _ingest(tmp_path, store, control=control)
    assert replay.skipped_reason == "ALREADY_SUCCESS"
    assert len(http.log) == before
