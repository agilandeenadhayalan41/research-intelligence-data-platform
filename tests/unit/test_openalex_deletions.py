"""Offline tests for Step 13 OpenAlex Works deletion processing."""

from __future__ import annotations

import gzip
import hashlib
import inspect
import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest
import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.activity import is_active_work, is_deleted_work
from research_platform.canonical.openalex.deletions import (
    DeletionOutcome,
    RestoreRequiredError,
    classify_deletion,
)
from research_platform.canonical.openalex.models import (
    CanonicalActivityState,
    CanonicalLineage,
)
from research_platform.config.models import StorageConfig
from research_platform.control import (
    ControlStatus,
    InMemoryControlStore,
    PipelineRunStatus,
)
from research_platform.ingestion.deletion_cli import LocalFileConnector
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletion_pipeline import run_openalex_deletions_local_ingest
from research_platform.ingestion.deletions_csv import (
    DeletedWorkRecord,
    iter_deleted_work_records,
)
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError
from research_platform.ingestion.pipeline import run_openalex_works_local_ingest
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import (
    DELETION_PUBLIC_URI,
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.errors import ObjectConflictError
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_deletion_raw_object_key


def _ts(*, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, minute, tzinfo=UTC)


def _clock(start: datetime | None = None):
    state = {"current": start or _ts(hour=1)}

    def clock() -> datetime:
        current = state["current"]
        state["current"] = current + timedelta(seconds=1)
        return current

    return clock


def _work(work_id: str, *, title: str = "T", updated: str = "2024-01-10") -> dict:
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


def _gz_jsonl(records: list[dict]) -> bytes:
    body = "\n".join(__import__("json").dumps(r) for r in records) + "\n"
    return gzip.compress(body.encode("utf-8"))


def _gz_csv(
    rows: list[tuple[str, str]],
    *,
    header: str = "work_id,deleted_date",
) -> bytes:
    lines = [header] + [f"{work_id},{deleted_date}" for work_id, deleted_date in rows]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _deletion_asset(
    *,
    snapshot: date = date(2024, 2, 5),
    updated: date | None = date(2024, 2, 1),
) -> OpenAlexDeletionAssetMetadata:
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


def _works_asset() -> OpenAlexAssetMetadata:
    return OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 1, 15),
            "entity": "works",
            "file_uri": (
                "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part_000.gz"
            ),
            "byte_size": 1000,
            "updated_date": date(2024, 1, 10),
            "content_format": "jsonl",
        }
    )


class FakeConnector:
    def __init__(self, *, assets: list, payloads: dict[str, bytes]) -> None:
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


def _seed_works(tmp_path: Path, control, canonical, records: list[dict]):
    asset = _works_asset()
    payload = _gz_jsonl(records)
    landing = tmp_path / "landing-works"
    config_path = _write_config(tmp_path / "cfg-works", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    run_openalex_works_local_ingest(
        config_path,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(assets=[asset], payloads={asset.file_uri: payload}),  # type: ignore[arg-type]
        now=_clock(),
    )
    return asset


def _lineage(*, updated: date | None, state: CanonicalActivityState) -> CanonicalLineage:
    return CanonicalLineage.model_validate(
        {
            "source_asset_id": "oa-test",
            "source_checksum_sha256": "ab" * 32,
            "source_updated_date": updated,
            "run_id": uuid4(),
            "processed_at": _ts(hour=1),
            "activity_state": state,
            "deleted_at": _ts(hour=2) if state is CanonicalActivityState.DELETED else None,
        }
    )


def test_public_uri_contract_rejects_provisional_namespace() -> None:
    with pytest.raises(ValueError, match="jsonl/works"):
        OpenAlexDeletionAssetMetadata.model_validate(
            {
                "source": "openalex",
                "snapshot_date": date(2024, 2, 5),
                "entity": "works-deletions",
                "file_uri": (
                    "s3://openalex/data/csv/works-deletions/"
                    "updated_date=2024-02-01/deleted_ids.csv.gz"
                ),
                "byte_size": 500,
                "content_format": "csv",
            }
        )
    asset = _deletion_asset()
    assert asset.file_uri == DELETION_PUBLIC_URI
    assert asset.entity == "works-deletions"
    assert asset.asset_id.startswith("oad-")


def test_classify_active_stale_and_equal_date() -> None:
    from research_platform.canonical.openalex import map_openalex_work

    def mapped(updated: date | None) -> object:
        lineage = _lineage(updated=updated, state=CanonicalActivityState.ACTIVE)
        return map_openalex_work(
            _work("W1", updated=(updated or date(2024, 1, 1)).isoformat()),
            lineage=lineage,
        ).work

    assert (
        classify_deletion(mapped(date(2026, 9, 10)), deleted_date=date(2026, 8, 14))
        is DeletionOutcome.STALE
    )
    assert (
        classify_deletion(mapped(date(2026, 8, 1)), deleted_date=date(2026, 8, 14))
        is DeletionOutcome.DELETED
    )
    assert (
        classify_deletion(mapped(date(2026, 8, 1)), deleted_date=date(2026, 8, 1))
        is DeletionOutcome.DELETED
    )
    assert (
        classify_deletion(mapped(None), deleted_date=date(2026, 8, 14))
        is DeletionOutcome.STALE
    )


def test_csv_header_and_dates() -> None:
    rows = list(
        iter_deleted_work_records(
            io.BytesIO(
                _gz_csv(
                    [
                        ("https://openalex.org/W1", "2024-02-01"),
                        ("W2", "2024-03-01"),
                    ]
                )
            )
        )
    )
    assert rows == [
        DeletedWorkRecord(work_id="W1", deleted_date=date(2024, 2, 1)),
        DeletedWorkRecord(work_id="W2", deleted_date=date(2024, 3, 1)),
    ]

    with pytest.raises(IngestionDecodeError, match="header"):
        list(iter_deleted_work_records(io.BytesIO(_gz_csv([("W1", "2024-02-01")], header="id"))))

    with pytest.raises(IngestionDecodeError, match="header"):
        list(
            iter_deleted_work_records(
                io.BytesIO(
                    _gz_csv([("W1", "2024-02-01")], header="work_id,deleted_date,extra")
                )
            )
        )

    with pytest.raises(IngestionDecodeError, match="deleted_date"):
        list(iter_deleted_work_records(io.BytesIO(_gz_csv([("W1", "02/01/2024")]))))

    with pytest.raises(IngestionDecodeError, match="malformed"):
        list(iter_deleted_work_records(io.BytesIO(_gz_csv([("not-an-id", "2024-02-01")]))))


def test_valid_deletion_tombs_tones_and_preserves_relationships(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1"), _work("W2")])
    assert canonical.work_count() == 2
    rel_before = canonical.relationship_counts()

    del_asset = _deletion_asset()
    payload = _gz_csv(
        [
            ("W1", "2024-02-01"),
            ("https://openalex.org/W2", "2024-02-01"),
        ]
    )
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    result = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.stats is not None
    assert result.stats.deleted == 2
    w1 = canonical.get_work("W1")
    assert w1 is not None
    assert is_deleted_work(w1)
    assert w1.lineage.source_updated_date == date(2024, 2, 1)
    assert w1.lineage.deleted_at is not None
    assert canonical.relationship_counts() == rel_before
    key = openalex_deletion_raw_object_key(del_asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload


def test_active_newer_than_deletion_stays_active(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    # Lineage version date comes from the Works asset updated_date partition.
    newer_asset = OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2026, 9, 15),
            "entity": "works",
            "file_uri": (
                "s3://openalex/data/jsonl/works/updated_date=2026-09-10/part_000.gz"
            ),
            "byte_size": 1000,
            "updated_date": date(2026, 9, 10),
            "content_format": "jsonl",
        }
    )
    run_openalex_works_local_ingest(
        _write_config(tmp_path / "cfg-works", tmp_path / "landing-works"),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-works")
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
        _write_config(tmp_path / "cfg-del", tmp_path / "landing-del"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-del")
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
    work = canonical.get_work("W1")
    assert work is not None
    assert is_active_work(work)


def test_unknown_and_duplicate_ids(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    payload = _gz_csv(
        [
            ("W1", "2024-02-01"),
            ("W1", "2024-02-01"),
            ("W999", "2024-02-01"),
        ]
    )
    result = run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "cfg-del", tmp_path / "landing-del"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-del")
        ),
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.stats is not None
    assert result.stats.deleted == 1
    assert result.stats.duplicates == 1
    assert result.stats.unknown == 1
    assert result.stats.unique_ids == 2
    assert result.stats.rows_seen == 3


def test_conflicting_deleted_dates_fail(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    payload = _gz_csv(
        [
            ("W1", "2024-02-01"),
            ("W1", "2024-03-01"),
        ]
    )
    with pytest.raises(IngestionDecodeError, match="conflicting deleted_date"):
        run_openalex_deletions_local_ingest(
            _write_config(tmp_path / "cfg-del", tmp_path / "landing-del"),
            deletion_asset=del_asset,
            control_store=control,
            canonical_store=canonical,
            object_store=LocalObjectStore(
                StorageConfig(backend="local", landing_path=tmp_path / "landing-del")
            ),
            connector=FakeConnector(
                assets=[del_asset], payloads={del_asset.file_uri: payload}
            ),
            now=_clock(),
        )
    assert is_active_work(canonical.get_work("W1"))  # type: ignore[arg-type]


def test_already_deleted_idempotent(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    payload = _gz_csv([("W1", "2024-02-01")])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    first = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(assets=[del_asset], payloads={del_asset.file_uri: payload}),
        now=_clock(),
    )
    assert first.stats is not None and first.stats.deleted == 1
    second = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert second.skipped_reason == "ALREADY_SUCCESS"


def test_stale_active_after_deletion_does_not_resurrect(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1", updated="2024-01-10")])
    del_asset = _deletion_asset()
    run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "cfg-del", tmp_path / "landing-del"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-del")
        ),
        connector=FakeConnector(
            assets=[del_asset],
            payloads={del_asset.file_uri: _gz_csv([("W1", "2024-02-01")])},
        ),
        now=_clock(),
    )
    stale_asset = OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 1, 15),
            "entity": "works",
            "file_uri": (
                "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part_stale.gz"
            ),
            "byte_size": 1000,
            "updated_date": date(2024, 1, 10),
            "content_format": "jsonl",
        }
    )
    run_openalex_works_local_ingest(
        _write_config(tmp_path / "cfg-stale", tmp_path / "landing-stale"),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-stale")
        ),
        connector=FakeConnector(
            assets=[stale_asset],
            payloads={stale_asset.file_uri: _gz_jsonl([_work("W1", title="Stale")])},
        ),  # type: ignore[arg-type]
        now=_clock(),
    )
    work = canonical.get_work("W1")
    assert work is not None
    assert is_deleted_work(work)


def test_newer_active_after_deletion_requires_restore(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1", updated="2024-01-10")])
    del_asset = _deletion_asset()
    run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "cfg-del", tmp_path / "landing-del"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-del")
        ),
        connector=FakeConnector(
            assets=[del_asset],
            payloads={del_asset.file_uri: _gz_csv([("W1", "2024-02-01")])},
        ),
        now=_clock(),
    )
    newer = OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 3, 1),
            "entity": "works",
            "file_uri": (
                "s3://openalex/data/jsonl/works/updated_date=2024-03-01/part_new.gz"
            ),
            "byte_size": 1000,
            "updated_date": date(2024, 3, 1),
            "content_format": "jsonl",
        }
    )
    with pytest.raises(RestoreRequiredError):
        run_openalex_works_local_ingest(
            _write_config(tmp_path / "cfg-new", tmp_path / "landing-new"),
            control_store=control,
            canonical_store=canonical,
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


def test_mid_file_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1"), _work("W2"), _work("W3")])
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

    original = InMemoryCanonicalStore.apply_work_deletion
    calls = {"n": 0}

    def flaky(self, work_id, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("forced deletion failure at row 3")
        return original(self, work_id, **kwargs)

    monkeypatch.setattr(InMemoryCanonicalStore, "apply_work_deletion", flaky)
    with pytest.raises(Exception):
        run_openalex_deletions_local_ingest(
            config_path,
            deletion_asset=del_asset,
            control_store=control,
            canonical_store=canonical,
            object_store=object_store,
            connector=FakeConnector(
                assets=[del_asset], payloads={del_asset.file_uri: payload}
            ),
            now=_clock(),
        )
    row = control.get_source_file(del_asset.asset_id)
    assert row is not None
    assert row.status is ControlStatus.FAILED
    assert all(
        is_active_work(w)
        for w in (
            canonical.get_work("W1"),
            canonical.get_work("W2"),
            canonical.get_work("W3"),
        )
        if w
    )
    assert getattr(control, "_deletion_events", []) == []
    with object_store.open(openalex_deletion_raw_object_key(del_asset)) as handle:
        assert handle.read() == payload

    monkeypatch.setattr(InMemoryCanonicalStore, "apply_work_deletion", original)
    result = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.stats is not None
    assert result.stats.deleted == 3


def test_csv_decode_limits_and_malformed() -> None:
    limits = CsvDeletionDecodeLimits(
        max_ids=2, max_decompressed_bytes=10_000, max_row_bytes=1000
    )
    with pytest.raises(IngestionDecodeError, match="max_ids"):
        list(
            iter_deleted_work_records(
                io.BytesIO(
                    _gz_csv(
                        [
                            ("W1", "2024-01-01"),
                            ("W2", "2024-01-01"),
                            ("W3", "2024-01-01"),
                        ]
                    )
                ),
                limits=limits,
            )
        )

    with pytest.raises(IngestionDecodeError, match="UTF-8"):
        list(iter_deleted_work_records(io.BytesIO(gzip.compress(b"work_id,deleted_date\n\xff\n"))))

    with pytest.raises(IngestionFormatError):
        full = _gz_csv([("W1", "2024-01-01")])
        list(iter_deleted_work_records(io.BytesIO(full[:8])))


def test_exact_decompressed_boundary_accepted() -> None:
    body = b"work_id,deleted_date\nW1,2024-01-01\n"
    limits = CsvDeletionDecodeLimits(
        max_decompressed_bytes=len(body),
        max_ids=10,
        max_row_bytes=100,
    )
    assert list(
        iter_deleted_work_records(io.BytesIO(gzip.compress(body)), limits=limits)
    ) == [DeletedWorkRecord(work_id="W1", deleted_date=date(2024, 1, 1))]


def test_empty_deletion_file(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    del_asset = _deletion_asset()
    payload = gzip.compress(b"work_id,deleted_date\n")
    result = run_openalex_deletions_local_ingest(
        _write_config(tmp_path, tmp_path / "landing"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing")
        ),
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    assert result.stats is not None
    assert result.stats.rows_seen == 0
    assert result.run.status is PipelineRunStatus.SUCCESS


def test_reference_from_active_to_deleted_target_preserved(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    assert canonical.relationship_counts()["work_references"] >= 1
    del_asset = _deletion_asset()
    run_openalex_deletions_local_ingest(
        _write_config(tmp_path / "d", tmp_path / "landing-d"),
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=tmp_path / "landing-d")
        ),
        connector=FakeConnector(
            assets=[del_asset],
            payloads={del_asset.file_uri: _gz_csv([("W9", "2024-02-01")])},
        ),
        now=_clock(),
    )
    assert is_active_work(canonical.get_work("W1"))  # type: ignore[arg-type]
    assert canonical.relationship_counts()["work_references"] >= 1


def test_conflicting_raw_bytes_rejected(tmp_path: Path) -> None:
    from research_platform.provenance.models import IngestionProvenance

    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    key = openalex_deletion_raw_object_key(del_asset)
    first = _gz_csv([("W1", "2024-02-01")])
    provenance = IngestionProvenance.model_validate(
        {
            "run_id": uuid4(),
            "source": "openalex",
            "source_uri": del_asset.file_uri,
            "retrieved_at": _ts(hour=0),
            "sha256": hashlib.sha256(first).hexdigest(),
        }
    )
    object_store.put_if_absent(key, io.BytesIO(first), provenance)
    with pytest.raises(ObjectConflictError):
        run_openalex_deletions_local_ingest(
            config_path,
            deletion_asset=del_asset,
            control_store=control,
            canonical_store=canonical,
            object_store=object_store,
            connector=FakeConnector(
                assets=[del_asset],
                payloads={del_asset.file_uri: _gz_csv([("W2", "2024-02-01")])},
            ),
            now=_clock(),
        )


def test_decompression_expansion_limit() -> None:
    huge = ("work_id,deleted_date\n" + ("W1,2024-01-01\n" * 5000)).encode("utf-8")
    limits = CsvDeletionDecodeLimits(
        max_decompressed_bytes=200,
        max_ids=10_000,
        max_row_bytes=100,
    )
    with pytest.raises(IngestionDecodeError, match="decompressed"):
        list(iter_deleted_work_records(io.BytesIO(gzip.compress(huge)), limits=limits))


def test_local_file_connector_streams_without_read_bytes(tmp_path: Path) -> None:
    path = tmp_path / "deleted_ids.csv.gz"
    path.write_bytes(_gz_csv([("W1", "2024-02-01")]))
    source = inspect.getsource(LocalFileConnector)
    assert "read_bytes" not in source
    assert 'open("rb")' in source or "open('rb')" in source
    connector = LocalFileConnector(path)
    asset = SourceAsset(source="openalex", identifier="x", uri=DELETION_PUBLIC_URI)
    with connector.fetch(asset) as handle:
        assert not isinstance(handle, io.BytesIO)
        chunk = handle.read(4)
        assert chunk[:2] == b"\x1f\x8b"
