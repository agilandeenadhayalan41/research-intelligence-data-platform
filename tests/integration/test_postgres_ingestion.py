"""Separately invoked PostgreSQL integration tests for Step 12 transactions.

Run via ``make test-postgres-ingestion`` when ``POSTGRES_DSN`` points at a local
PostgreSQL instance. Default ``make test`` excludes these (``not postgres``).
"""

from __future__ import annotations

import gzip
import io
import json
import os
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import uuid4

import pytest
import yaml

pytest.importorskip("psycopg")

import psycopg

from research_platform.canonical.openalex.models import (
    CanonicalActivityState,
    CanonicalLineage,
    CanonicalWorkBundle,
)
from research_platform.canonical.openalex import map_openalex_work
from research_platform.config.models import StorageConfig
from research_platform.control.errors import ClaimConflictError, IdempotencyConflictError
from research_platform.control.models import (
    ControlStatus,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)
from research_platform.ingestion.errors import IngestionDecodeError
from research_platform.ingestion.pipeline import run_openalex_works_local_ingest
from research_platform.persistence.postgres import (
    PostgresCanonicalStore,
    PostgresControlStore,
    apply_ingestion_schema,
    connect_postgres,
    publish_claimed_asset,
)
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    StreamedWorkRecord,
)
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_raw_object_key

DSN = os.environ.get("POSTGRES_DSN", "").strip()
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="POSTGRES_DSN unset; skipping PostgreSQL tests"),
]


def _ts(*, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, minute, second, tzinfo=UTC)


def _clock(start: datetime | None = None, *, step_seconds: int = 1):
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
        "referenced_works": ["W9"],
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
    def __init__(self, *, assets: list[OpenAlexAssetMetadata], payloads: dict[str, bytes]) -> None:
        self._assets = assets
        self._payloads = payloads
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
        return io.BytesIO(self._payloads[asset.uri])


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


def _truncate(connection: psycopg.Connection) -> None:
    tables = (
        "deletion_events",
        "record_provenance",
        "work_author_institutions",
        "work_authors",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_mesh",
        "work_locations",
        "work_grants",
        "works",
        "authors",
        "institutions",
        "sources",
        "publishers",
        "topics",
        "funders",
        "source_files",
        "pipeline_runs",
    )
    with connection.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(tables) + " CASCADE")
    connection.commit()


@pytest.fixture
def pg() -> Iterator[psycopg.Connection]:
    connection = connect_postgres(DSN)
    apply_ingestion_schema(connection)
    _truncate(connection)
    try:
        yield connection
    finally:
        _truncate(connection)
        connection.close()


def test_durable_state_survives_reconnect(tmp_path: Path, pg: psycopg.Connection) -> None:
    asset = _asset(uri_suffix="durable.gz")
    payload = _gz_jsonl([_work("W1"), _work("W2")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    result = run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.persistence_backend == "postgres"
    asset_id = asset.asset_id

    reconnect = connect_postgres(DSN)
    try:
        control = PostgresControlStore(reconnect)
        canonical = PostgresCanonicalStore(reconnect)
        row = control.get_source_file(asset_id)
        assert row is not None
        assert row.status is ControlStatus.SUCCESS
        assert canonical.work_count() == 2
        assert len(control.list_record_provenance(asset_id=asset_id)) == 2
    finally:
        reconnect.close()


def test_concurrent_connections_cannot_both_claim(pg: psycopg.Connection) -> None:
    asset = _asset(uri_suffix="claim.gz")
    control = PostgresControlStore(pg)
    run = control.create_pipeline_run(
        PipelineRun.model_validate(
            {
                "run_id": uuid4(),
                "source": "openalex",
                "pipeline_name": "openalex-works-ingest",
                "status": PipelineRunStatus.PROCESSING,
                "attempt": 1,
                "started_at": _ts(hour=1),
                "created_at": _ts(hour=1),
                "updated_at": _ts(hour=1),
            }
        )
    )
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset.asset_id,
                "run_id": run.run_id,
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
    pg.commit()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    barrier = threading.Barrier(2)
    run_id = run.run_id

    def worker(name: str) -> None:
        conn = connect_postgres(DSN)
        try:
            store = PostgresControlStore(conn)
            barrier.wait(timeout=10)
            try:
                claimed = store.claim_source_file(
                    asset.asset_id,
                    run_id=run_id,
                    claimed_by=name,
                    claim_token=uuid4(),
                    claimed_at=_ts(hour=1, minute=1),
                    lease_expires_at=_ts(hour=2),
                )
                results[name] = claimed.claimed_by
            except ClaimConflictError as error:
                results[name] = error
            except BaseException as error:  # noqa: BLE001 - surface worker failures
                errors[name] = error
        finally:
            conn.close()

    threads = [
        threading.Thread(target=worker, args=("worker-a",)),
        threading.Thread(target=worker, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"worker errors: {errors}"
    winners = [value for value in results.values() if isinstance(value, str)]
    losers = [value for value in results.values() if isinstance(value, ClaimConflictError)]
    assert len(winners) == 1
    assert len(losers) == 1


def test_mid_publish_failure_rolls_back_then_retry_succeeds(
    tmp_path: Path, pg: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.persistence.postgres import canonical_store as canonical_mod

    asset = _asset(uri_suffix="rollback.gz")
    payload = _gz_jsonl([_work("W1"), _work("W2"), _work("W3")])
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    original = canonical_mod.upsert_work_bundle
    calls = {"n": 0}

    def flaky(connection, bundle):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("forced persistence failure at record 3")
        return original(connection, bundle)

    monkeypatch.setattr(canonical_mod, "upsert_work_bundle", flaky)
    # publish_claimed_asset imports upsert_work_bundle at call time from canonical_store
    import research_platform.persistence.postgres.unit_of_work as uow_mod

    monkeypatch.setattr(uow_mod, "upsert_work_bundle", flaky)

    with pytest.raises(Exception) as raised:
        run_openalex_works_local_ingest(
            config_path,
            backend="postgres",
            postgres_connection=pg,
            object_store=object_store,
            connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
            now=_clock(),
        )
    assert "forced persistence failure" in str(raised.value) or "forced persistence failure" in str(
        raised.value.__cause__
    )

    control = PostgresControlStore(pg)
    canonical = PostgresCanonicalStore(pg)
    row = control.get_source_file(asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    assert canonical.work_count() == 0
    assert all(count == 0 for count in canonical.relationship_counts().values())
    assert control.list_record_provenance(asset_id=asset.asset_id) == ()
    key = openalex_raw_object_key(asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload

    monkeypatch.setattr(uow_mod, "upsert_work_bundle", original)
    monkeypatch.setattr(canonical_mod, "upsert_work_bundle", original)
    result = run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS
    assert canonical.work_count() == 3
    provenance = control.list_record_provenance(asset_id=asset.asset_id)
    assert len(provenance) == 3
    assert {row.record_id for row in provenance} == {"W1", "W2", "W3"}


def test_idempotent_replay_and_changed_version(tmp_path: Path, pg: psycopg.Connection) -> None:
    asset_v1 = _asset(uri_suffix="v1.gz", updated=date(2024, 1, 10))
    asset_v2 = _asset(uri_suffix="v2.gz", updated=date(2024, 2, 10))
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    first = run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[asset_v1],
            payloads={asset_v1.file_uri: _gz_jsonl([_work("W1", title="Old")])},
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert first.stats is not None
    assert first.stats.works_inserted == 1

    replay = run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[asset_v1],
            payloads={asset_v1.file_uri: _gz_jsonl([_work("W1", title="Old")])},
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert replay.skipped_reason == "ALREADY_SUCCESS"
    assert replay.source_file is not None
    assert replay.source_file.status is ControlStatus.SUCCESS

    second = run_openalex_works_local_ingest(
        _write_config(tmp_path / "v2", tmp_path / "landing2"),
        backend="postgres",
        postgres_connection=pg,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing2")
        ),
        connector=FakeConnector(
            assets=[asset_v2],
            payloads={
                asset_v2.file_uri: _gz_jsonl(
                    [_work("W1", title="New", updated="2024-02-10")]
                )
            },
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert second.run.status is PipelineRunStatus.SUCCESS
    canonical = PostgresCanonicalStore(pg)
    assert canonical.work_count() == 1
    work = canonical.get_work("W1")
    assert work is not None
    assert work.title == "New"
    assert canonical.relationship_counts()["work_authors"] == 1


def test_provenance_uniqueness_and_success_atomicity(pg: psycopg.Connection) -> None:
    asset = _asset(uri_suffix="atom.gz")
    control = PostgresControlStore(pg)
    run = control.create_pipeline_run(
        PipelineRun.model_validate(
            {
                "run_id": uuid4(),
                "source": "openalex",
                "pipeline_name": "openalex-works-ingest",
                "status": PipelineRunStatus.PROCESSING,
                "attempt": 1,
                "started_at": _ts(hour=1),
                "created_at": _ts(hour=1),
                "updated_at": _ts(hour=1),
            }
        )
    )
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset.asset_id,
                "run_id": run.run_id,
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
    claim_token = uuid4()
    claimed = control.claim_source_file(
        asset.asset_id,
        run_id=run.run_id,
        claimed_by="worker",
        claim_token=claim_token,
        claimed_at=_ts(hour=1),
        lease_expires_at=_ts(hour=2),
    )
    assert claimed.status is ControlStatus.PROCESSING

    lineage = CanonicalLineage.model_validate(
        {
            "source_asset_id": asset.asset_id,
            "source_checksum_sha256": "ab" * 32,
            "source_updated_date": asset.updated_date,
            "run_id": run.run_id,
            "processed_at": _ts(hour=1, minute=2),
            "activity_state": CanonicalActivityState.ACTIVE,
        }
    )
    bundle = map_openalex_work(_work("W1"), lineage=lineage)
    provenance = RecordProvenance.model_validate(
        {
            "record_id": "W1",
            "entity_type": "work",
            "asset_id": asset.asset_id,
            "source_checksum_sha256": "ab" * 32,
            "run_id": run.run_id,
            "source_uri": asset.file_uri,
            "processed_at": _ts(hour=1, minute=2),
            "source_updated_date": asset.updated_date,
        }
    )
    def open_one():
        yield StreamedWorkRecord(bundle=bundle, provenance=provenance)

    retrieval = IngestionProvenance.model_validate(
        {
            "run_id": run.run_id,
            "source": "openalex",
            "source_uri": asset.file_uri,
            "retrieved_at": _ts(hour=1, minute=1),
            "sha256": "ab" * 32,
        }
    )
    published = publish_claimed_asset(
        pg,
        AssetPublishRequest(
            asset_id=asset.asset_id,
            claim_token=claim_token,
            now=_ts(hour=1, minute=2),
            processed_at=_ts(hour=1, minute=2),
            raw_object_key="raw/openalex/works/atom.gz",
            source_checksum_sha256="ab" * 32,
            retrieval_provenance=retrieval,
            open_records=open_one,
        ),
    )
    assert published.source_file.status is ControlStatus.SUCCESS
    assert published.counters.record_provenance_count == 1
    rows = control.list_record_provenance(asset_id=asset.asset_id)
    assert len(rows) == 1

    # Second publish with same grain is rejected by claim ownership (no longer PROCESSING).
    with pytest.raises(Exception):
        publish_claimed_asset(
            pg,
            AssetPublishRequest(
                asset_id=asset.asset_id,
                claim_token=claim_token,
                now=_ts(hour=1, minute=3),
                processed_at=_ts(hour=1, minute=3),
                raw_object_key="raw/openalex/works/atom.gz",
                source_checksum_sha256="ab" * 32,
                retrieval_provenance=retrieval,
                open_records=open_one,
            ),
        )
    assert len(control.list_record_provenance(asset_id=asset.asset_id)) == 1


def test_changed_declared_size_not_already_success(tmp_path: Path, pg: psycopg.Connection) -> None:
    asset = _asset(uri_suffix="size.gz")
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[asset], payloads={asset.file_uri: _gz_jsonl([_work("W1")])}
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    larger = asset.model_copy(update={"byte_size": asset.byte_size + 50})
    with pytest.raises(IdempotencyConflictError, match="declared_size"):
        run_openalex_works_local_ingest(
            config_path,
            backend="postgres",
            postgres_connection=pg,
            object_store=object_store,
            connector=FakeConnector(
                assets=[larger], payloads={larger.file_uri: _gz_jsonl([_work("W1")])}
            ),  # type: ignore[arg-type]
            now=_clock(),
        )


def test_decode_failure_keeps_raw_marks_failed(tmp_path: Path, pg: psycopg.Connection) -> None:
    asset = _asset(uri_suffix="badjson.gz")
    good = json.dumps(_work("W1"))
    payload = gzip.compress((good + "\n{not-json\n").encode("utf-8"))
    landing = tmp_path / "landing"
    config_path = _write_config(tmp_path, landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    with pytest.raises(IngestionDecodeError):
        run_openalex_works_local_ingest(
            config_path,
            backend="postgres",
            postgres_connection=pg,
            object_store=object_store,
            connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
            now=_clock(),
        )
    control = PostgresControlStore(pg)
    canonical = PostgresCanonicalStore(pg)
    row = control.get_source_file(asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    assert canonical.work_count() == 0
    with object_store.open(openalex_raw_object_key(asset)) as handle:
        assert handle.read() == payload


# ---------------------------------------------------------------------------
# Step 13 — OpenAlex Works deletions (PostgreSQL)
# ---------------------------------------------------------------------------


def _gz_csv(rows: list[tuple[str, str]]) -> bytes:
    lines = ["work_id,deleted_date"] + [
        f"{work_id},{deleted_date}" for work_id, deleted_date in rows
    ]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _deletion_asset(
    *,
    updated: date | None = date(2024, 2, 1),
    snapshot: date = date(2024, 2, 5),
) -> "OpenAlexDeletionAssetMetadata":
    from research_platform.sources.openalex.deletion_metadata import (
        DELETION_PUBLIC_URI,
        OpenAlexDeletionAssetMetadata,
    )

    return OpenAlexDeletionAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": snapshot,
            "entity": "works-deletions",
            "file_uri": DELETION_PUBLIC_URI,
            "byte_size": 500,
            "updated_date": updated,
            "content_format": "csv",
        }
    )


def _seed_works_pg(
    tmp_path: Path,
    pg: psycopg.Connection,
    records: list[dict[str, object]],
    *,
    uri_suffix: str = "seed.gz",
) -> OpenAlexAssetMetadata:
    asset = _asset(uri_suffix=uri_suffix)
    landing = tmp_path / "landing-works"
    config_path = _write_config(tmp_path / "cfg-works", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    result = run_openalex_works_local_ingest(
        config_path,
        backend="postgres",
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[asset], payloads={asset.file_uri: _gz_jsonl(records)}
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    return asset


def test_deletion_tombstone_survives_reconnect(
    tmp_path: Path, pg: psycopg.Connection
) -> None:
    from research_platform.canonical.openalex.activity import is_deleted_work
    from research_platform.ingestion.deletion_pipeline import (
        run_openalex_deletions_local_ingest,
    )
    from research_platform.storage.openalex_layout import openalex_deletion_raw_object_key

    _seed_works_pg(tmp_path, pg, [_work("W1"), _work("W2")])
    del_asset = _deletion_asset()
    payload = _gz_csv([("W1", "2024-02-01"), ("W999", "2024-02-01")])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    # Commit any idle SELECT transaction so later ControlStore.transaction()
    # blocks commit durably (nested savepoints would not).
    rel_before = PostgresCanonicalStore(pg).relationship_counts()
    pg.commit()

    result = run_openalex_deletions_local_ingest(
        config_path,
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.persistence_backend == "postgres"
    assert result.stats is not None
    assert result.stats.deleted == 1
    assert result.stats.unknown == 1
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS

    reconnect = connect_postgres(DSN)
    try:
        control = PostgresControlStore(reconnect)
        canonical = PostgresCanonicalStore(reconnect)
        row = control.get_source_file(del_asset.asset_id)
        assert row is not None
        assert row.status is ControlStatus.SUCCESS
        w1 = canonical.get_work("W1")
        assert w1 is not None
        assert is_deleted_work(w1)
        assert w1.lineage.deleted_at is not None
        assert canonical.relationship_counts() == rel_before
        provenance = control.list_record_provenance(asset_id=del_asset.asset_id)
        assert any(p.entity_type == "work-deletion" for p in provenance)
        with reconnect.cursor() as cur:
            cur.execute(
                "SELECT outcome FROM deletion_events WHERE work_id = %s",
                ("W1",),
            )
            outcomes = {r[0] for r in cur.fetchall()}
        assert "DELETED" in outcomes
        with reconnect.cursor() as cur:
            cur.execute(
                "SELECT outcome FROM deletion_events WHERE work_id = %s",
                ("W999",),
            )
            assert cur.fetchone()[0] == "UNKNOWN_WORK"
    finally:
        reconnect.close()

    with object_store.open(openalex_deletion_raw_object_key(del_asset)) as handle:
        assert handle.read() == payload


def test_deletion_mid_file_rollback_then_retry(
    tmp_path: Path, pg: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_platform.canonical.openalex.activity import is_active_work
    from research_platform.ingestion.deletion_pipeline import (
        run_openalex_deletions_local_ingest,
    )
    from research_platform.persistence.postgres import canonical_store as canonical_mod
    import research_platform.persistence.postgres.deletion_unit_of_work as del_uow
    from research_platform.storage.openalex_layout import openalex_deletion_raw_object_key

    _seed_works_pg(tmp_path, pg, [_work("W1"), _work("W2"), _work("W3")])
    del_asset = _deletion_asset()
    payload = _gz_csv(
        [
            ("W1", "2024-02-01"),
            ("W2", "2024-02-01"),
            ("W3", "2024-02-01"),
        ]
    )
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    original = canonical_mod.apply_work_deletion
    calls = {"n": 0}

    def flaky(connection, work_id, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("forced deletion failure at row 3")
        return original(connection, work_id, **kwargs)

    monkeypatch.setattr(canonical_mod, "apply_work_deletion", flaky)
    monkeypatch.setattr(del_uow, "apply_work_deletion", flaky)

    with pytest.raises(Exception):
        run_openalex_deletions_local_ingest(
            config_path,
            backend="postgres",
            deletion_asset=del_asset,
            postgres_connection=pg,
            object_store=object_store,
            connector=FakeConnector(
                assets=[del_asset], payloads={del_asset.file_uri: payload}
            ),
            now=_clock(),
        )

    control = PostgresControlStore(pg)
    canonical = PostgresCanonicalStore(pg)
    row = control.get_source_file(del_asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    for work_id in ("W1", "W2", "W3"):
        work = canonical.get_work(work_id)
        assert work is not None
        assert is_active_work(work)
    with pg.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM deletion_events WHERE asset_id = %s",
            (del_asset.asset_id,),
        )
        assert cur.fetchone()[0] == 0
    assert control.list_record_provenance(asset_id=del_asset.asset_id) == ()
    with object_store.open(openalex_deletion_raw_object_key(del_asset)) as handle:
        assert handle.read() == payload

    monkeypatch.setattr(del_uow, "apply_work_deletion", original)
    monkeypatch.setattr(canonical_mod, "apply_work_deletion", original)
    result = run_openalex_deletions_local_ingest(
        config_path,
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.stats is not None
    assert result.stats.deleted == 3
    assert result.source_file is not None
    assert result.source_file.status is ControlStatus.SUCCESS


def test_deletion_idempotent_replay_and_no_resurrection(
    tmp_path: Path, pg: psycopg.Connection
) -> None:
    from research_platform.canonical.openalex.activity import is_deleted_work
    from research_platform.canonical.openalex.deletions import RestoreRequiredError
    from research_platform.ingestion.deletion_pipeline import (
        run_openalex_deletions_local_ingest,
    )

    _seed_works_pg(tmp_path, pg, [_work("W1", updated="2024-01-10")])
    del_asset = _deletion_asset(updated=date(2024, 2, 1))
    payload = _gz_csv([("W1", "2024-02-01")])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))

    first = run_openalex_deletions_local_ingest(
        config_path,
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert first.stats is not None and first.stats.deleted == 1

    second = run_openalex_deletions_local_ingest(
        config_path,
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert second.skipped_reason == "ALREADY_SUCCESS"

    stale_asset = _asset(uri_suffix="stale_after_del.gz", updated=date(2024, 1, 10))
    run_openalex_works_local_ingest(
        _write_config(tmp_path / "cfg-stale", tmp_path / "landing-stale"),
        backend="postgres",
        postgres_connection=pg,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-stale")
        ),
        connector=FakeConnector(
            assets=[stale_asset],
            payloads={stale_asset.file_uri: _gz_jsonl([_work("W1", title="Stale")])},
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    canonical = PostgresCanonicalStore(pg)
    work = canonical.get_work("W1")
    assert work is not None
    assert is_deleted_work(work)

    newer = _asset(uri_suffix="newer_after_del.gz", updated=date(2024, 3, 1))
    with pytest.raises(RestoreRequiredError):
        run_openalex_works_local_ingest(
            _write_config(tmp_path / "cfg-new", tmp_path / "landing-new"),
            backend="postgres",
            postgres_connection=pg,
            object_store=LocalObjectStore(
                StorageConfig(backend="local", landing_path=tmp_path / "landing-new")
            ),
            connector=FakeConnector(
                assets=[newer],
                payloads={
                    newer.file_uri: _gz_jsonl(
                        [_work("W1", title="New", updated="2024-03-01")]
                    )
                },
            ),  # type: ignore[arg-type]
            now=_clock(),
        )
    assert is_deleted_work(canonical.get_work("W1"))  # type: ignore[arg-type]


def test_deletion_older_than_active_work_is_stale(
    tmp_path: Path, pg: psycopg.Connection
) -> None:
    from research_platform.canonical.openalex.activity import is_active_work
    from research_platform.ingestion.deletion_pipeline import (
        run_openalex_deletions_local_ingest,
    )

    newer_asset = _asset(uri_suffix="newer_active.gz", updated=date(2026, 9, 10))
    landing_works = tmp_path / "landing-works-newer"
    run_openalex_works_local_ingest(
        _write_config(tmp_path / "cfg-works-newer", landing_works),
        backend="postgres",
        postgres_connection=pg,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing_works)
        ),
        connector=FakeConnector(
            assets=[newer_asset],
            payloads={
                newer_asset.file_uri: _gz_jsonl(
                    [_work("W1", updated="2026-09-10")]
                )
            },
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    del_asset = _deletion_asset()
    result = run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "cfg-stale-del", tmp_path / "landing-stale-del"),
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-stale-del")
        ),
        connector=FakeConnector(
            assets=[del_asset],
            payloads={del_asset.file_uri: _gz_csv([("W1", "2026-08-14")])},
        ),
        now=_clock(),
    )
    assert result.stats is not None
    assert result.stats.stale == 1
    assert result.stats.deleted == 0
    canonical = PostgresCanonicalStore(pg)
    work = canonical.get_work("W1")
    assert work is not None
    assert is_active_work(work)
    with pg.cursor() as cur:
        cur.execute(
            "SELECT outcome, deleted_date FROM deletion_events WHERE work_id = %s",
            ("W1",),
        )
        outcome, deleted_date = cur.fetchone()
    assert outcome == "STALE"
    assert deleted_date == date(2026, 8, 14)


def test_deletion_reference_to_deleted_target_preserved(
    tmp_path: Path, pg: psycopg.Connection
) -> None:
    from research_platform.canonical.openalex.activity import is_active_work
    from research_platform.ingestion.deletion_pipeline import (
        run_openalex_deletions_local_ingest,
    )

    _seed_works_pg(
        tmp_path,
        pg,
        [_work("W1"), _work("W9", title="Cited")],
        uri_suffix="refs.gz",
    )
    canonical = PostgresCanonicalStore(pg)
    refs_before = canonical.relationship_counts()["work_references"]
    assert refs_before >= 1
    pg.commit()

    del_asset = _deletion_asset()
    result = run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "d", tmp_path / "landing-d"),
        backend="postgres",
        deletion_asset=del_asset,
        postgres_connection=pg,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-d")
        ),
        connector=FakeConnector(
            assets=[del_asset],
            payloads={del_asset.file_uri: _gz_csv([("W9", "2024-02-01")])},
        ),
        now=_clock(),
    )
    assert result.stats is not None
    assert result.stats.deleted == 1
    w1 = canonical.get_work("W1")
    assert w1 is not None
    assert is_active_work(w1)
    assert canonical.relationship_counts()["work_references"] == refs_before
