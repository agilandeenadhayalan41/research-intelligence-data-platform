"""Offline tests for Step 13 OpenAlex Works deletion processing."""

from __future__ import annotations

import gzip
import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pytest
import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import map_openalex_work
from research_platform.canonical.openalex.activity import is_active_work, is_deleted_work
from research_platform.canonical.openalex.deletions import (
    DeletionOutcome,
    RestoreRequiredError,
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
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletion_pipeline import run_openalex_deletions_local_ingest
from research_platform.ingestion.deletions_csv import iter_deleted_work_ids
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError
from research_platform.ingestion.pipeline import run_openalex_works_local_ingest
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import (
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
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


def _gz_csv(ids: list[str], *, header: str = "id") -> bytes:
    lines = [header] + ids
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _deletion_asset(
    *,
    updated: date = date(2024, 2, 1),
    snapshot: date = date(2024, 2, 5),
) -> OpenAlexDeletionAssetMetadata:
    return OpenAlexDeletionAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": snapshot,
            "entity": "works-deletions",
            "file_uri": (
                f"s3://openalex/data/csv/works-deletions/"
                f"updated_date={updated.isoformat()}/deleted_ids.csv.gz"
            ),
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


def test_valid_deletion_tombs_tones_and_preserves_relationships(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1"), _work("W2")])
    assert canonical.work_count() == 2
    rel_before = canonical.relationship_counts()

    del_asset = _deletion_asset()
    payload = _gz_csv(["W1", "https://openalex.org/W2"])
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
    assert not is_active_work(w1)
    assert w1.lineage.deleted_at is not None
    assert canonical.relationship_counts() == rel_before
    key = openalex_deletion_raw_object_key(del_asset)
    with object_store.open(key) as handle:
        assert handle.read() == payload


def test_unknown_and_duplicate_ids(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    payload = _gz_csv(["W1", "W1", "W999"])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    result = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(StorageConfig(backend="local", landing_path=landing)),
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


def test_already_deleted_idempotent(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    payload = _gz_csv(["W1"])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    connector = FakeConnector(assets=[del_asset], payloads={del_asset.file_uri: payload})
    first = run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=object_store,
        connector=connector,
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
    del_asset = _deletion_asset(updated=date(2024, 2, 1))
    payload = _gz_csv(["W1"])
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    run_openalex_deletions_local_ingest(
        config_path,
        deletion_asset=del_asset,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(StorageConfig(backend="local", landing_path=landing)),
        connector=FakeConnector(
            assets=[del_asset], payloads={del_asset.file_uri: payload}
        ),
        now=_clock(),
    )
    # Stale active ingest (T1) after deletion T2.
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
    assert work.title != "Stale" or work.lineage.activity_state is CanonicalActivityState.DELETED


def test_newer_active_after_deletion_requires_restore(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1", updated="2024-01-10")])
    del_asset = _deletion_asset(updated=date(2024, 2, 1))
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
            payloads={del_asset.file_uri: _gz_csv(["W1"])},
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
    payload = _gz_csv(["W1", "W2", "W3"])
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
    assert all(is_active_work(w) for w in (canonical.get_work("W1"), canonical.get_work("W2"), canonical.get_work("W3")) if w)
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
    limits = CsvDeletionDecodeLimits(max_ids=2, max_decompressed_bytes=10_000, max_row_bytes=1000)
    with pytest.raises(IngestionDecodeError, match="max_ids"):
        list(iter_deleted_work_ids(io.BytesIO(_gz_csv(["W1", "W2", "W3"])), limits=limits))

    with pytest.raises(IngestionDecodeError, match="malformed"):
        list(iter_deleted_work_ids(io.BytesIO(_gz_csv(["not-an-id"]))))

    with pytest.raises(IngestionDecodeError, match="header"):
        body = gzip.compress(b"work_id\nW1\n")
        list(iter_deleted_work_ids(io.BytesIO(body)))

    with pytest.raises(IngestionDecodeError, match="UTF-8"):
        list(iter_deleted_work_ids(io.BytesIO(gzip.compress(b"id\n\xff\n"))))

    with pytest.raises(IngestionFormatError):
        full = _gz_csv(["W1"])
        list(iter_deleted_work_ids(io.BytesIO(full[:8])))


def test_exact_decompressed_boundary_accepted() -> None:
    body = b"id\nW1\n"
    limits = CsvDeletionDecodeLimits(
        max_decompressed_bytes=len(body),
        max_ids=10,
        max_row_bytes=100,
    )
    assert list(iter_deleted_work_ids(io.BytesIO(gzip.compress(body)), limits=limits)) == [
        "W1"
    ]


def test_empty_deletion_file(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    del_asset = _deletion_asset()
    payload = gzip.compress(b"id\n")
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
    # W1 references W9; delete W9 if present — here only W1 exists with ref to W9.
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    assert canonical.relationship_counts()["work_references"] >= 1
    # Unknown target deletion should not remove W1's reference row.
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
            assets=[del_asset], payloads={del_asset.file_uri: _gz_csv(["W9"])}
        ),
        now=_clock(),
    )
    assert canonical.get_work("W1") is not None
    assert is_active_work(canonical.get_work("W1"))  # type: ignore[arg-type]
    assert canonical.relationship_counts()["work_references"] >= 1


def test_conflicting_raw_bytes_rejected(tmp_path: Path) -> None:
    import hashlib

    from research_platform.provenance.models import IngestionProvenance
    from research_platform.storage.errors import ObjectConflictError

    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _seed_works(tmp_path, control, canonical, [_work("W1")])
    del_asset = _deletion_asset()
    landing = tmp_path / "landing-del"
    config_path = _write_config(tmp_path / "cfg-del", landing)
    object_store = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
    key = openalex_deletion_raw_object_key(del_asset)
    first = _gz_csv(["W1"])
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
                payloads={del_asset.file_uri: _gz_csv(["W2"])},
            ),
            now=_clock(),
        )


def test_decompression_expansion_limit() -> None:
    # Highly compressible payload that expands past the decode budget.
    huge = ("id\n" + ("W1\n" * 5000)).encode("utf-8")
    compressed = gzip.compress(huge)
    limits = CsvDeletionDecodeLimits(
        max_decompressed_bytes=200,
        max_ids=10_000,
        max_row_bytes=100,
    )
    with pytest.raises(IngestionDecodeError, match="decompressed"):
        list(iter_deleted_work_ids(io.BytesIO(compressed), limits=limits))
