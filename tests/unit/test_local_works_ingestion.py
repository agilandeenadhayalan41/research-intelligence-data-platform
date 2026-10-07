"""Offline tests for Step 12 one-file local Works ingestion."""

from __future__ import annotations

import gzip
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest
import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.config.models import StorageConfig
from research_platform.control import (
    ClaimConflictError,
    ControlStatus,
    FailureCategory,
    InMemoryControlStore,
    PipelineRunStatus,
    RegistrationOutcome,
    SourceFileControl,
    reconcile_registration,
)
from research_platform.ingestion import run_openalex_works_local_ingest
from research_platform.ingestion.errors import IngestionConfigError, IngestionDecodeError
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.errors import ObjectConflictError
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_raw_object_key
from research_platform.provenance.models import IngestionProvenance
import hashlib
from io import BytesIO


def _ts(*, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, minute, second, tzinfo=UTC)


def _clock(start: datetime | None = None, *, step_seconds: int = 1):
    """Return a callable clock that advances by seconds (keeps leases valid)."""
    state = {"current": start or _ts(hour=1)}

    def clock() -> datetime:
        current = state["current"]
        state["current"] = current + timedelta(seconds=step_seconds)
        return current

    return clock


def _work(work_id: str, *, title: str = "T", updated: str = "2024-01-10") -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "title": title,
        "publication_date": "2020-01-01",
        "updated_date": updated,
        "authorships": [
            {
                "author": {"id": "A1", "display_name": "Ada"},
                "institutions": [{"id": "I1", "display_name": "Inst"}],
            }
        ],
        "topics": [{"id": "T1", "display_name": "Topic", "score": 0.5}],
        "referenced_works": ["W9", "not-a-work"],
        "locations": [],
        "grants": [],
        "keywords": [],
        "mesh": [],
    }


def _gz_jsonl(records: list[dict[str, object]]) -> bytes:
    body = "\n".join(json.dumps(record) for record in records) + "\n"
    return gzip.compress(body.encode("utf-8"))


def _asset(*, uri_suffix: str = "part_000.gz", updated: date | None = date(2024, 1, 10)) -> OpenAlexAssetMetadata:
    updated_part = (
        f"updated_date={updated.isoformat()}" if updated is not None else "updated_date=2024-01-10"
    )
    return OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 1, 15),
            "entity": "works",
            "file_uri": f"s3://openalex/data/jsonl/works/{updated_part}/{uri_suffix}",
            "byte_size": 1000,
            "updated_date": updated,
            "content_format": "jsonl",
        }
    )


class FakeConnector:
    def __init__(
        self,
        *,
        assets: list[OpenAlexAssetMetadata],
        payloads: dict[str, bytes],
        fail_after_bytes: int | None = None,
    ) -> None:
        self._assets = assets
        self._payloads = payloads
        self._fail_after_bytes = fail_after_bytes
        self.fetch_calls = 0

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(
            max_files=1,
            max_file_size_bytes=25_000_000,
            eligible_count=len(self._assets),
            selected=tuple(self._assets[:1]),
            skipped=(),
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        self.fetch_calls += 1
        payload = self._payloads[asset.uri]
        if self._fail_after_bytes is not None:
            return _FailingStream(payload, self._fail_after_bytes)
        return io.BytesIO(payload)


class _FailingStream(io.RawIOBase):
    def __init__(self, payload: bytes, fail_after: int) -> None:
        self._payload = payload
        self._fail_after = fail_after
        self._offset = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        if self._offset >= self._fail_after:
            raise OSError("simulated mid-stream retrieval failure")
        if size < 0:
            size = self._fail_after - self._offset
        size = min(size, self._fail_after - self._offset)
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _write_config(path: Path, landing: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    config = {
        "environment": "local",
        "log_level": "INFO",
        "source": "openalex",
        "storage": {"backend": "local", "landing_path": str(landing)},
        "warehouse": {
            "transactional": "postgres",
            "analytical": "duckdb",
            "postgres_dsn_env": "POSTGRES_DSN",
            "duckdb_path": ":memory:",
        },
        "sample_selection": {"max_files": 1, "max_file_size_bytes": 25_000_000},
    }
    config_path = path / "local.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _run_ingest(
    tmp_path: Path,
    *,
    records: list[dict[str, object]] | None = None,
    asset: OpenAlexAssetMetadata | None = None,
    control: InMemoryControlStore | None = None,
    canonical: InMemoryCanonicalStore | None = None,
    connector: FakeConnector | None = None,
    worker_id: str = "worker-a",
    lease_ttl: timedelta = timedelta(minutes=15),
    clock_hours: list[int] | None = None,
):
    asset = asset or _asset()
    payload = _gz_jsonl(records or [_work("W1")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = control or InMemoryControlStore()
    canonical = canonical or InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    connector = connector or FakeConnector(
        assets=[asset], payloads={asset.file_uri: payload}
    )
    del clock_hours  # reserved for future explicit sequences
    result = run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=connector,  # type: ignore[arg-type]
        worker_id=worker_id,
        lease_ttl=lease_ttl,
        now=_clock(),
    )
    return result, control, canonical, object_store, connector, asset, payload


def test_first_ingestion_lands_and_upserts(tmp_path: Path) -> None:
    result, control, canonical, object_store, connector, asset, payload = _run_ingest(
        tmp_path,
        records=[_work("W1"), _work("W2", title="Two")],
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS
    assert result.stats is not None
    assert result.stats.works_inserted == 2
    assert result.stats.record_provenance_count == 2
    assert canonical.work_count() == 2
    assert canonical.relationship_counts()["work_authors"] == 2
    assert canonical.relationship_counts()["work_references"] == 4
    key = openalex_raw_object_key(asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload
    assert connector.fetch_calls == 1
    assert len(control.list_record_provenance(asset_id=asset.asset_id)) == 2


def test_identical_replay_skips_without_duplicate_rows(tmp_path: Path) -> None:
    first, control, canonical, object_store, connector, asset, _ = _run_ingest(tmp_path)
    assert first.stats is not None
    assert first.stats.works_inserted == 1
    second, _, _, _, connector2, _, _ = _run_ingest(
        tmp_path,
        control=control,
        canonical=canonical,
        connector=FakeConnector(
            assets=[asset],
            payloads={asset.file_uri: _gz_jsonl([_work("W1")])},
        ),
    )
    assert second.skipped_reason == "ALREADY_SUCCESS"
    assert second.source_file is not None
    assert second.source_file.status is ControlStatus.SUCCESS
    assert canonical.work_count() == 1
    assert connector2.fetch_calls == 0
    # Raw object still present once.
    key = openalex_raw_object_key(asset)
    with object_store.open(key) as handle:
        assert handle.read()


def test_concurrent_claim_only_one_wins(tmp_path: Path) -> None:
    del tmp_path
    asset = _asset()
    control = InMemoryControlStore()
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset.asset_id,
                "run_id": uuid4(),
                "source": "openalex",
                "entity": "works",
                "source_uri": asset.file_uri,
                "snapshot_date": asset.snapshot_date,
                "updated_date": asset.updated_date,
                "content_format": "jsonl",
                "declared_size_bytes": asset.byte_size,
                "status": ControlStatus.DISCOVERED,
                "attempt_count": 0,
                "created_at": _ts(hour=0),
                "updated_at": _ts(hour=0),
            }
        )
    )
    claimed = control.claim_source_file(
        asset.asset_id,
        run_id=uuid4(),
        claimed_by="worker-a",
        claim_token=uuid4(),
        claimed_at=_ts(hour=1),
        lease_expires_at=_ts(hour=2),
    )
    assert claimed.status is ControlStatus.PROCESSING
    with pytest.raises(ClaimConflictError):
        control.claim_source_file(
            asset.asset_id,
            run_id=uuid4(),
            claimed_by="worker-b",
            claim_token=uuid4(),
            claimed_at=_ts(hour=1, minute=1),
            lease_expires_at=_ts(hour=2, minute=1),
        )


def test_changed_source_version_replaces_work(tmp_path: Path) -> None:
    asset_v1 = _asset(uri_suffix="part_v1.gz", updated=date(2024, 1, 10))
    asset_v2 = _asset(uri_suffix="part_v2.gz", updated=date(2024, 2, 10))
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _run_ingest(
        tmp_path,
        records=[_work("W1", title="Old", updated="2024-01-10")],
        asset=asset_v1,
        control=control,
        canonical=canonical,
    )
    assert canonical.get_work("W1") is not None
    assert canonical.get_work("W1").title == "Old"  # type: ignore[union-attr]
    _run_ingest(
        tmp_path / "v2",
        records=[_work("W1", title="New", updated="2024-02-10")],
        asset=asset_v2,
        control=control,
        canonical=canonical,
    )
    assert canonical.work_count() == 1
    assert canonical.get_work("W1").title == "New"  # type: ignore[union-attr]


def test_mid_file_failure_keeps_raw_and_marks_failed(tmp_path: Path) -> None:
    asset = _asset()
    # Valid gzip with a bad JSON line after a good record.
    good = json.dumps(_work("W1"))
    bad = "{not-json"
    payload = gzip.compress((good + "\n" + bad + "\n").encode("utf-8"))
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    connector = FakeConnector(assets=[asset], payloads={asset.file_uri: payload})
    with pytest.raises(IngestionDecodeError):
        run_openalex_works_local_ingest(
            config_path,
            control_store=control,
            canonical_store=canonical,
            object_store=object_store,
            connector=connector,  # type: ignore[arg-type]
            now=_clock(),
        )
    row = control.get_source_file(asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    assert row.failure_category is FailureCategory.CANONICAL
    key = openalex_raw_object_key(asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload
    run = control.get_pipeline_run(row.run_id)
    assert run is not None
    assert run.status is PipelineRunStatus.FAILED


def test_checksum_conflict_on_reregistration(tmp_path: Path) -> None:
    del tmp_path
    asset = _asset()
    existing = SourceFileControl.model_validate(
        {
            "asset_id": asset.asset_id,
            "run_id": uuid4(),
            "source": "openalex",
            "entity": "works",
            "source_uri": asset.file_uri,
            "snapshot_date": asset.snapshot_date,
            "updated_date": asset.updated_date,
            "content_format": "jsonl",
            "source_checksum_sha256": "aa" * 32,
            "raw_object_key": openalex_raw_object_key(asset),
            "status": ControlStatus.SUCCESS,
            "attempt_count": 1,
            "processed_at": _ts(hour=2),
            "created_at": _ts(hour=0),
            "updated_at": _ts(hour=2),
        }
    )
    incoming = SourceFileControl.model_validate(
        {
            "asset_id": asset.asset_id,
            "run_id": uuid4(),
            "source": "openalex",
            "entity": "works",
            "source_uri": asset.file_uri,
            "snapshot_date": asset.snapshot_date,
            "updated_date": asset.updated_date,
            "content_format": "jsonl",
            "source_checksum_sha256": "bb" * 32,
            "status": ControlStatus.DISCOVERED,
            "attempt_count": 0,
            "created_at": _ts(hour=3),
            "updated_at": _ts(hour=3),
        }
    )
    assert (
        reconcile_registration(existing, incoming)
        is RegistrationOutcome.CHECKSUM_CONFLICT
    )
    store = InMemoryControlStore()
    store._files[asset.asset_id] = existing  # noqa: SLF001
    _, outcome = store.register_source_file(incoming)
    assert outcome is RegistrationOutcome.CHECKSUM_CONFLICT


def test_landing_conflict_on_different_bytes(tmp_path: Path) -> None:
    asset = _asset()
    landing = tmp_path / "landing"
    store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    key = openalex_raw_object_key(asset)
    first = b"alpha-bytes"
    second = b"beta-bytes-different"
    prov1 = IngestionProvenance.model_validate(
        {
            "run_id": uuid4(),
            "source": "openalex",
            "source_uri": asset.file_uri,
            "retrieved_at": _ts(hour=1),
            "sha256": hashlib.sha256(first).hexdigest(),
        }
    )
    prov2 = IngestionProvenance.model_validate(
        {
            "run_id": uuid4(),
            "source": "openalex",
            "source_uri": asset.file_uri,
            "retrieved_at": _ts(hour=2),
            "sha256": hashlib.sha256(second).hexdigest(),
        }
    )
    store.put_if_absent(key, BytesIO(first), prov1)
    with pytest.raises(ObjectConflictError):
        store.put_if_absent(key, BytesIO(second), prov2)


def test_stale_claim_recovery_then_retry(tmp_path: Path) -> None:
    asset = _asset()
    payload = _gz_jsonl([_work("W1")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset.asset_id,
                "run_id": uuid4(),
                "source": "openalex",
                "entity": "works",
                "source_uri": asset.file_uri,
                "snapshot_date": asset.snapshot_date,
                "updated_date": asset.updated_date,
                "content_format": "jsonl",
                "declared_size_bytes": asset.byte_size,
                "status": ControlStatus.DISCOVERED,
                "attempt_count": 0,
                "created_at": _ts(hour=0),
                "updated_at": _ts(hour=0),
            }
        )
    )
    control.claim_source_file(
        asset.asset_id,
        run_id=uuid4(),
        claimed_by="dead-worker",
        claim_token=uuid4(),
        claimed_at=_ts(hour=1),
        lease_expires_at=_ts(hour=1, minute=5),
    )
    # Clock starts after lease expiry so recovery runs before claim.
    connector = FakeConnector(assets=[asset], payloads={asset.file_uri: payload})
    result = run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=connector,  # type: ignore[arg-type]
        worker_id="recovery-worker",
        now=_clock(start=_ts(hour=1, minute=10)),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS
    assert result.source_file.attempt_count >= 2


def test_rejects_non_local_environment(tmp_path: Path) -> None:
    landing = tmp_path / "landing"
    config = {
        "environment": "dev",
        "storage": {"backend": "local", "landing_path": str(landing)},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": 25_000_000},
    }
    path = tmp_path / "dev.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(IngestionConfigError):
        run_openalex_works_local_ingest(path)


def test_record_and_relationship_counts_stable_on_identical_upsert(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    asset = _asset()
    records = [_work("W1"), _work("W1", title="Same")]  # second maps same id in one file
    # Two lines same work id + checksum → second is IDENTICAL within file.
    result, _, _, _, _, _, _ = _run_ingest(
        tmp_path,
        records=records,
        asset=asset,
        control=control,
        canonical=canonical,
    )
    assert result.stats is not None
    assert result.stats.works_inserted + result.stats.works_identical == 2
    assert canonical.work_count() == 1
    counts = canonical.relationship_counts()
    # Re-ingest same asset is ALREADY_SUCCESS.
    result2, _, _, _, _, _, _ = _run_ingest(
        tmp_path / "r2",
        records=records,
        asset=asset,
        control=control,
        canonical=canonical,
    )
    assert result2.skipped_reason == "ALREADY_SUCCESS"
    assert canonical.relationship_counts() == counts


def test_no_eligible_file_completes_successfully(tmp_path: Path) -> None:
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    class EmptyConnector:
        def discover_metadata(self) -> OpenAlexSampleSelection:
            return OpenAlexSampleSelection(
                max_files=1,
                max_file_size_bytes=25_000_000,
                eligible_count=0,
                selected=(),
                skipped=(),
            )

        def fetch(self, asset: SourceAsset) -> BinaryIO:
            raise AssertionError("fetch must not be called")

    result = run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=EmptyConnector(),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.skipped_reason == "NO_ELIGIBLE_FILE"


def test_register_outcome_created(tmp_path: Path) -> None:
    del tmp_path
    control = InMemoryControlStore()
    asset = _asset()
    row, outcome = control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset.asset_id,
                "run_id": uuid4(),
                "source": "openalex",
                "entity": "works",
                "source_uri": asset.file_uri,
                "snapshot_date": asset.snapshot_date,
                "updated_date": asset.updated_date,
                "content_format": "jsonl",
                "status": ControlStatus.DISCOVERED,
                "attempt_count": 0,
                "created_at": _ts(hour=0),
                "updated_at": _ts(hour=0),
            }
        )
    )
    assert outcome is RegistrationOutcome.CREATED
    assert row.status is ControlStatus.DISCOVERED


def test_changed_declared_size_rejects_already_success_skip(tmp_path: Path) -> None:
    from research_platform.control.errors import IdempotencyConflictError

    first, control, canonical, _, _, asset, _ = _run_ingest(tmp_path)
    assert first.skipped_reason is None
    assert first.source_file is not None
    assert first.source_file.status is ControlStatus.SUCCESS
    larger = asset.model_copy(update={"byte_size": asset.byte_size + 1})
    with pytest.raises(IdempotencyConflictError, match="declared_size"):
        _run_ingest(
            tmp_path / "replay",
            asset=larger,
            control=control,
            canonical=canonical,
            connector=FakeConnector(
                assets=[larger],
                payloads={larger.file_uri: _gz_jsonl([_work("W1")])},
            ),
        )
    assert control.get_source_file(asset.asset_id).status is ControlStatus.SUCCESS  # type: ignore[union-attr]
    assert canonical.work_count() == 1


def test_conflicting_update_metadata_rejects_replay(tmp_path: Path) -> None:
    from research_platform.control.errors import IdempotencyConflictError

    first, control, canonical, _, _, asset, _ = _run_ingest(tmp_path)
    assert first.source_file is not None
    # Same asset_id identity key but conflicting updated_date metadata.
    conflicting = asset.model_copy(update={"updated_date": date(2024, 3, 1)})
    with pytest.raises(IdempotencyConflictError, match="identity metadata"):
        _run_ingest(
            tmp_path / "meta",
            asset=conflicting,
            control=control,
            canonical=canonical,
            connector=FakeConnector(
                assets=[conflicting],
                payloads={conflicting.file_uri: _gz_jsonl([_work("W1")])},
            ),
        )


def test_publish_failure_rolls_back_prior_canonical_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = _asset()
    payload = _gz_jsonl([_work("W1"), _work("W2"), _work("W3")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    connector = FakeConnector(assets=[asset], payloads={asset.file_uri: payload})

    original = InMemoryCanonicalStore.upsert_work_bundle
    calls = {"n": 0}

    def flaky(self, bundle):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("forced persistence failure at record 3")
        return original(self, bundle)

    monkeypatch.setattr(InMemoryCanonicalStore, "upsert_work_bundle", flaky)
    with pytest.raises(Exception) as raised:
        run_openalex_works_local_ingest(
            config_path,
            control_store=control,
            canonical_store=canonical,
            object_store=object_store,
            connector=connector,  # type: ignore[arg-type]
            now=_clock(),
        )
    assert "forced persistence failure" in str(raised.value) or "forced persistence failure" in str(
        raised.value.__cause__
    )
    row = control.get_source_file(asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    assert canonical.work_count() == 0
    assert all(count == 0 for count in canonical.relationship_counts().values())
    assert control.list_record_provenance(asset_id=asset.asset_id) == ()
    key = openalex_raw_object_key(asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload

    # Retry with healthy upsert path.
    monkeypatch.setattr(InMemoryCanonicalStore, "upsert_work_bundle", original)
    result = run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS
    assert canonical.work_count() == 3
    assert len(control.list_record_provenance(asset_id=asset.asset_id)) == 3


def test_publication_processes_records_incrementally_without_materializing_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record N is upserted before record N+1 is requested from the stream."""
    from research_platform.persistence import memory_unit_of_work as mem_uow

    asset = _asset(uri_suffix="stream.gz")
    payload = _gz_jsonl([_work("W1"), _work("W2"), _work("W3")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    events: list[str] = []
    original_publish = mem_uow.publish_claimed_asset_memory

    def instrumented(control_store, canonical_store, request):  # type: ignore[no-untyped-def]
        original_open = request.open_records

        def traced_open():
            for index, item in enumerate(original_open(), start=1):
                events.append(f"yield:{index}:{item.bundle.work.work_id}")
                yield item
                events.append(f"after-yield:{index}")

        from research_platform.persistence.unit_of_work import AssetPublishRequest

        traced = AssetPublishRequest(
            asset_id=request.asset_id,
            claim_token=request.claim_token,
            now=request.now,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            retrieval_provenance=request.retrieval_provenance,
            open_records=traced_open,
        )
        original_upsert = canonical_store.upsert_work_bundle

        def tracking_upsert(bundle):  # type: ignore[no-untyped-def]
            events.append(f"upsert:{bundle.work.work_id}")
            return original_upsert(bundle)

        monkeypatch.setattr(canonical_store, "upsert_work_bundle", tracking_upsert)
        return original_publish(control_store, canonical_store, traced)

    monkeypatch.setattr(mem_uow, "publish_claimed_asset_memory", instrumented)
    # Pipeline imports the symbol by name; patch the pipeline binding too.
    import research_platform.ingestion.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "publish_claimed_asset_memory", instrumented)

    result = run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    # Each record is upserted before the next yield is requested.
    assert events == [
        "yield:1:W1",
        "upsert:W1",
        "after-yield:1",
        "yield:2:W2",
        "upsert:W2",
        "after-yield:2",
        "yield:3:W3",
        "upsert:W3",
        "after-yield:3",
    ]


def test_cli_requires_backend_and_prints_persistence_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.ingestion import cli as cli_mod
    from research_platform.ingestion.pipeline import LocalWorksIngestResult
    from research_platform.control.models import PipelineRun, PipelineRunStatus

    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)

    with pytest.raises(SystemExit) as exited:
        cli_mod.main(["--config", str(config_path)])
    assert exited.value.code == 2

    def fake_run(config, *, backend="memory", **kwargs):  # type: ignore[no-untyped-def]
        del config, kwargs
        return LocalWorksIngestResult(
            run=PipelineRun.model_validate(
                {
                    "run_id": uuid4(),
                    "source": "openalex",
                    "pipeline_name": "openalex-works-ingest",
                    "status": PipelineRunStatus.SUCCESS,
                    "attempt": 1,
                    "started_at": _ts(hour=1),
                    "completed_at": _ts(hour=2),
                    "created_at": _ts(hour=1),
                    "updated_at": _ts(hour=2),
                }
            ),
            source_file=None,
            stats=None,
            skipped_reason="NO_ELIGIBLE_FILE",
            persistence_backend=backend,
        )

    monkeypatch.setattr(cli_mod, "run_openalex_works_local_ingest", fake_run)
    code = cli_mod.main(["--config", str(config_path), "--backend", "memory"])
    assert code == 0
    out = capsys.readouterr().out
    assert '"persistence": "memory"' in out
    assert '"durable": false' in out
