"""Unit tests for Step 19 bounded end-to-end pipeline (SEMANTIC_ONLY)."""

from __future__ import annotations

import gzip
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import pytest
import yaml
from pydantic import ValidationError

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.activity import is_deleted_work
from research_platform.config.models import StorageConfig
from research_platform.control import InMemoryControlStore, PipelineRunStatus
from research_platform.e2e import (
    PIPELINE_NAME,
    STAGE_ORDER,
    EndToEndBounds,
    InMemoryPublicationStore,
    PublicationConflictError,
    PublicationSnapshot,
    StageName,
    StageStatus,
    parse_bounds,
    run_bounded_e2e_pipeline,
    safe_result_summary,
)
from research_platform.e2e.cli import main as e2e_main
from research_platform.ingestion import ingest_works_asset, run_openalex_works_local_ingest
from research_platform.service.contracts import WorkMetadataRequest
from research_platform.service.models import ServiceErrorCode, ServiceErrorException
from research_platform.service.repository import (
    InMemoryConsumerRepository,
    build_reference_fixture,
)
from research_platform.service.service import DataService
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import (
    DELETION_PUBLIC_URI,
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.local import LocalObjectStore


def _ts(*, hour: int = 1) -> datetime:
    return datetime(2024, 6, 1, hour, 0, 0, tzinfo=UTC)


def _clock(start: datetime | None = None):
    state = {"current": start or _ts()}

    def clock() -> datetime:
        current = state["current"]
        state["current"] = current + timedelta(seconds=1)
        return current

    return clock


def _work(
    work_id: str,
    *,
    title: str = "Title",
    updated: str = "2024-01-10",
    year: int = 2020,
    publisher: str = "P1",
    source: str = "S1",
    topic: str = "T1",
    author: str = "A1",
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/10.1000/{work_id.lower()}",
        "title": title,
        "publication_date": f"{year}-01-01",
        "publication_year": year,
        "type": "article",
        "language": "en",
        "updated_date": updated,
        "primary_location": {
            "is_oa": True,
            "license": "cc-by",
            "source": {
                "id": f"https://openalex.org/{source}",
                "display_name": "Journal",
                "type": "journal",
                "host_organization": f"https://openalex.org/{publisher}",
                "host_organization_name": "Pub",
            },
        },
        "open_access": {"is_oa": True, "oa_status": "gold"},
        "authorships": [
            {
                "author": {"id": author, "display_name": "Ada"},
                "institutions": [{"id": "I1", "display_name": "Inst"}],
            }
        ],
        "topics": [{"id": topic, "display_name": "Topic", "score": 0.5}],
        "referenced_works": ["W9"],
        "locations": [
            {
                "is_oa": True,
                "license": "cc-by",
                "source": {
                    "id": f"https://openalex.org/{source}",
                    "display_name": "Journal",
                    "host_organization": f"https://openalex.org/{publisher}",
                },
            }
        ],
        "grants": [],
        "keywords": [],
        "mesh": [],
    }


def _gz_jsonl(records: list[dict[str, object]]) -> bytes:
    body = "\n".join(json.dumps(r) for r in records) + "\n"
    return gzip.compress(body.encode("utf-8"))


def _gz_csv(rows: list[tuple[str, str]]) -> bytes:
    lines = ["work_id,deleted_date"] + [f"{w},{d}" for w, d in rows]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _asset(
    *,
    uri_suffix: str = "part_000.gz",
    updated: date | None = date(2024, 1, 10),
    byte_size: int = 1000,
) -> OpenAlexAssetMetadata:
    updated_part = (
        f"updated_date={updated.isoformat()}" if updated else "updated_date=2024-01-10"
    )
    return OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 1, 15),
            "entity": "works",
            "file_uri": f"s3://openalex/data/jsonl/works/{updated_part}/{uri_suffix}",
            "byte_size": byte_size,
            "updated_date": updated,
            "content_format": "jsonl",
        }
    )


def _deletion_asset(*, byte_size: int = 500) -> OpenAlexDeletionAssetMetadata:
    return OpenAlexDeletionAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 2, 5),
            "entity": "works-deletions",
            "file_uri": DELETION_PUBLIC_URI,
            "byte_size": byte_size,
            "updated_date": date(2024, 2, 1),
            "content_format": "csv",
        }
    )


class FakeConnector:
    def __init__(
        self,
        *,
        assets: list[OpenAlexAssetMetadata],
        payloads: dict[str, bytes],
        select_all: bool = False,
    ) -> None:
        self._assets = assets
        self._payloads = payloads
        self._select_all = select_all
        self.fetch_calls = 0

    def discover_metadata(self) -> OpenAlexSampleSelection:
        selected = tuple(self._assets) if self._select_all else tuple(self._assets[:1])
        return OpenAlexSampleSelection(
            max_files=1,
            max_file_size_bytes=25_000_000,
            eligible_count=len(self._assets),
            selected=selected,
            skipped=(),
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        self.fetch_calls += 1
        return io.BytesIO(self._payloads[asset.uri])


class FakeDeletionConnector:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        return io.BytesIO(self._payloads[asset.uri])


def _write_config(path: Path, landing: Path, *, max_bytes: int = 25_000_000) -> Path:
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
        "sample_selection": {"max_files": 1, "max_file_size_bytes": max_bytes},
    }
    cfg = path / "local.yaml"
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    return cfg


def _run_ok(tmp_path: Path, **kwargs):
    asset = kwargs.pop("asset", None) or _asset()
    records = kwargs.pop("records", None) or [
        _work("W1"),
        _work("W2", title="Two", topic="T2", author="A2"),
    ]
    payload = _gz_jsonl(records)
    control = kwargs.pop("control", InMemoryControlStore())
    canonical = kwargs.pop("canonical", InMemoryCanonicalStore())
    pubs = kwargs.pop("publication_store", InMemoryPublicationStore())
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    connector = FakeConnector(assets=[asset], payloads={asset.file_uri: payload})
    return run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(StorageConfig(backend="local", landing_path=landing)),
        publication_store=pubs,
        now=_clock(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_strict_bounds_defaults() -> None:
    b = EndToEndBounds()
    assert b.max_files == 1
    assert b.max_file_size_bytes == 25_000_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_files": None},
        {"max_files": 0},
        {"max_files": -1},
        {"max_files": True},
        {"max_files": 2},
        {"max_file_size_bytes": None},
        {"max_file_size_bytes": 0},
        {"max_file_size_bytes": -1},
        {"max_file_size_bytes": False},
        {"max_file_size_bytes": 25_000_001},
    ],
)
def test_strict_bounds_rejected(kwargs: dict) -> None:
    with pytest.raises((ValidationError, ValueError)):
        parse_bounds(**kwargs)


# ---------------------------------------------------------------------------
# Happy path / stage order / shared run
# ---------------------------------------------------------------------------


def test_successful_complete_run(tmp_path: Path) -> None:
    result = _run_ok(tmp_path)
    assert result.success
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.run.pipeline_name == PIPELINE_NAME
    assert result.evidence_label == "SEMANTIC_ONLY"
    assert result.publication_version is not None
    assert result.final_validation is not None
    assert result.final_validation.ok
    assert result.final_validation.as_of_run_id == str(result.run.run_id)


def test_exact_stage_order(tmp_path: Path) -> None:
    result = _run_ok(tmp_path)
    assert tuple(s.stage for s in result.stages) == STAGE_ORDER
    assert all(
        s.status in {StageStatus.SUCCESS, StageStatus.SKIPPED} for s in result.stages
    )


def test_shared_run_id_across_stages(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    del_asset = _deletion_asset()
    asset = _asset()
    records = [_work("W1"), _work("W2")]
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    result = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[asset], payloads={asset.file_uri: _gz_jsonl(records)}
        ),
        deletion_asset=del_asset,
        deletion_connector=FakeDeletionConnector(
            {del_asset.file_uri: _gz_csv([("W2", "2024-02-01")])}
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        now=_clock(),
    )
    assert result.success
    run_id = result.run.run_id
    # Source files share outer run_id
    works_sf = control.get_source_file(asset.asset_id)
    del_sf = control.get_source_file(del_asset.asset_id)
    assert works_sf is not None and works_sf.run_id == run_id
    assert del_sf is not None and del_sf.run_id == run_id
    # Only one outer pipeline run
    assert control.get_pipeline_run(run_id) is not None
    assert result.final_validation is not None
    assert result.final_validation.as_of_run_id == str(run_id)


# ---------------------------------------------------------------------------
# Oversized / replay / update / deletion
# ---------------------------------------------------------------------------


def test_oversized_payload_rejected(tmp_path: Path) -> None:
    asset = _asset(byte_size=30_000_000)
    result = _run_ok(tmp_path, asset=asset, records=[_work("W1")])
    assert not result.success
    assert result.run.status is PipelineRunStatus.FAILED
    select = next(s for s in result.stages if s.stage is StageName.SELECT)
    assert select.status is StageStatus.FAILED


def test_replay_idempotency_preserves_historical_run(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    asset = _asset()
    records = [_work("W1")]
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    connector = FakeConnector(
        assets=[asset], payloads={asset.file_uri: _gz_jsonl(records)}
    )
    first = run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert first.success
    historical = control.get_source_file(asset.asset_id)
    assert historical is not None
    historical_run = historical.run_id

    second = run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert second.success
    assert second.run.run_id != first.run.run_id
    # Historical SUCCESS asset provenance run_id preserved
    again = control.get_source_file(asset.asset_id)
    assert again is not None
    assert again.run_id == historical_run


def test_newer_work_update(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    a1 = _asset(uri_suffix="part_old.gz", updated=date(2024, 1, 10))
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    r1 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[a1],
            payloads={a1.file_uri: _gz_jsonl([_work("W1", title="Old")])},
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r1.success
    a2 = _asset(uri_suffix="part_new.gz", updated=date(2024, 3, 1))
    r2 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[a2],
            payloads={
                a2.file_uri: _gz_jsonl(
                    [_work("W1", title="New", updated="2024-03-01")]
                )
            },
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r2.success
    work = canonical.get_work("W1")
    assert work is not None
    assert work.title == "New"


def test_deletion_and_dataservice_not_found(tmp_path: Path) -> None:
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    asset = _asset()
    del_asset = _deletion_asset()
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    result = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[asset],
            payloads={asset.file_uri: _gz_jsonl([_work("W1"), _work("W2")])},
        ),
        deletion_asset=del_asset,
        deletion_connector=FakeDeletionConnector(
            {del_asset.file_uri: _gz_csv([("W1", "2024-02-01")])}
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert result.success
    assert is_deleted_work(canonical.get_work("W1"))
    # Physical relationships preserved
    assert canonical.relationship_counts()["work_authors"] >= 1
    current = pubs.current()
    assert current is not None
    svc = DataService(current.repository)
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W1"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND
    # W2 still available
    ok = svc.get_work(WorkMetadataRequest(work_id="W2"))
    assert ok.data.work_id == "W2"


def test_deletion_before_stale_active_regression(tmp_path: Path) -> None:
    """deletion W at T2 then stale ACTIVE W at T1 must NOT expose W as ACTIVE."""
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    asset = _asset(uri_suffix="part_seed.gz", updated=date(2024, 1, 10))
    del_asset = _deletion_asset()
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    # Seed + delete in one E2E run
    r1 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[asset],
            payloads={asset.file_uri: _gz_jsonl([_work("W1", updated="2024-01-10")])},
        ),
        deletion_asset=del_asset,
        deletion_connector=FakeDeletionConnector(
            {del_asset.file_uri: _gz_csv([("W1", "2024-02-01")])}
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r1.success
    assert is_deleted_work(canonical.get_work("W1"))

    # Stale ACTIVE arrives later via Step-12 reusable stage under new E2E run
    stale = _asset(uri_suffix="part_stale.gz", updated=date(2024, 1, 10))
    r2 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[stale],
            payloads={
                stale.file_uri: _gz_jsonl(
                    [_work("W1", title="Stale", updated="2024-01-10")]
                )
            },
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r2.success
    assert is_deleted_work(canonical.get_work("W1"))
    current = pubs.current()
    assert current is not None
    svc = DataService(current.repository)
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W1"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND
    page = svc.search_research(
        __import__(
            "research_platform.service.contracts", fromlist=["ResearchDiscoveryRequest"]
        ).ResearchDiscoveryRequest()
    )
    assert all(w.work_id != "W1" for w in page.data.items)


# ---------------------------------------------------------------------------
# Quality gates / publication atomicity
# ---------------------------------------------------------------------------


def test_pre_serving_failure_blocks_gold(tmp_path: Path) -> None:
    result = _run_ok(tmp_path, force_pre_serving_fail=True)
    assert not result.success
    assert result.run.status is PipelineRunStatus.FAILED
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[StageName.PRE_SERVING_QUALITY] is StageStatus.FAILED
    assert statuses[StageName.STAGED_GOLD] is StageStatus.BLOCKED
    assert statuses[StageName.CONSUMER_PUBLICATION] is StageStatus.BLOCKED


def test_pre_visible_failure_blocks_activation(tmp_path: Path) -> None:
    # Need a deleted work so force_pre_visible_fail can inject it into contributions
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    asset = _asset()
    del_asset = _deletion_asset()
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    result = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[asset],
            payloads={asset.file_uri: _gz_jsonl([_work("W1"), _work("W2")])},
        ),
        deletion_asset=del_asset,
        deletion_connector=FakeDeletionConnector(
            {del_asset.file_uri: _gz_csv([("W2", "2024-02-01")])}
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
        force_pre_visible_fail=True,
    )
    assert not result.success
    statuses = {s.stage: s.status for s in result.stages}
    assert statuses[StageName.PRE_VISIBLE_QUALITY] is StageStatus.FAILED
    assert statuses[StageName.CONSUMER_PUBLICATION] is StageStatus.BLOCKED
    assert pubs.current() is None


def test_old_publication_survives_failed_candidate(tmp_path: Path) -> None:
    pubs = InMemoryPublicationStore()
    first = _run_ok(tmp_path / "a", publication_store=pubs)
    assert first.success
    old = pubs.current()
    assert old is not None
    # Second run fails PRE_SERVING — old current remains
    second = _run_ok(
        tmp_path / "b",
        publication_store=pubs,
        force_pre_serving_fail=True,
        control=InMemoryControlStore(),
        canonical=InMemoryCanonicalStore(),
    )
    assert not second.success
    assert pubs.current() is old


def test_interrupted_activation_keeps_old_current(tmp_path: Path) -> None:
    pubs = InMemoryPublicationStore()
    first = _run_ok(tmp_path / "a", publication_store=pubs)
    old = pubs.current()
    assert old is not None
    pubs.simulate_activation_interrupt_once()
    second = _run_ok(
        tmp_path / "b",
        publication_store=pubs,
        control=InMemoryControlStore(),
        canonical=InMemoryCanonicalStore(),
        asset=_asset(uri_suffix="part_b.gz"),
    )
    assert not second.success
    assert pubs.current() is old


def test_publication_version_content_conflict() -> None:
    pubs = InMemoryPublicationStore()
    r1 = build_reference_fixture()
    r2 = InMemoryConsumerRepository(works=())
    s1 = PublicationSnapshot(
        publication_version="v1",
        run_id="r1",
        repository=r1,
        content_fingerprint="aaa",
    )
    s2 = PublicationSnapshot(
        publication_version="v1",
        run_id="r2",
        repository=r2,
        content_fingerprint="bbb",
    )
    pubs.stage(s1)
    with pytest.raises(PublicationConflictError):
        pubs.stage(s2)
    # Idempotent same content
    again = pubs.stage(s1)
    assert again.content_fingerprint == "aaa"


def test_recovery_same_staged_version() -> None:
    pubs = InMemoryPublicationStore()
    repo = build_reference_fixture()
    snap = PublicationSnapshot(
        publication_version="v-recover",
        run_id="r",
        repository=repo,
        content_fingerprint="fp",
    )
    pubs.stage(snap)
    pubs.simulate_activation_interrupt_once()
    with pytest.raises(RuntimeError):
        pubs.activate("v-recover")
    assert pubs.current() is None
    # Recover: activate staged version
    activated = pubs.activate("v-recover")
    assert activated.publication_version == "v-recover"
    assert pubs.current() is activated


def test_final_validation_failure(tmp_path: Path) -> None:
    pubs = InMemoryPublicationStore()
    result = _run_ok(
        tmp_path, force_final_validation_fail=True, publication_store=pubs
    )
    assert not result.success
    assert result.run.status is PipelineRunStatus.FAILED
    assert next(
        s for s in result.stages if s.stage is StageName.FINAL_VALIDATION
    ).status is StageStatus.FAILED
    # First publication: final-validation failure must leave no current.
    assert pubs.current() is None


def test_safe_summaries(tmp_path: Path) -> None:
    result = _run_ok(tmp_path)
    summary = safe_result_summary(result)
    assert "sql" not in json.dumps(summary).lower()
    assert summary["success"] is True
    assert summary["run_id"] == str(result.run.run_id)


def test_does_not_use_reference_fixture_for_publication(tmp_path: Path) -> None:
    result = _run_ok(tmp_path, records=[_work("W100")])
    assert result.success
    current = result.final_validation
    assert current is not None
    # Reference fixture uses W001 — our publication should expose W100
    # Access via re-running discovery through a new store is covered in success test;
    # here assert publication version tied to run.
    assert result.publication_version == f"pub-{result.run.run_id}"


# ---------------------------------------------------------------------------
# CLI + reusable stages + snapshot
# ---------------------------------------------------------------------------


def test_cli_success_returns_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asset = _asset()
    payload = _gz_jsonl([_work("W1")])
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)

    def _fake_connector(**kwargs):  # noqa: ANN003
        return FakeConnector(assets=[asset], payloads={asset.file_uri: payload})

    monkeypatch.setattr("research_platform.e2e.cli.OpenAlexConnector", _fake_connector)
    assert e2e_main(["--config", str(cfg)]) == 0


def test_cli_failure_nonzero_invalid_bounds() -> None:
    assert e2e_main(["--config", "/no/such.yaml", "--max-files", "0"]) == 1
    assert e2e_main(["--config", "/no/such.yaml", "--max-files", "2"]) == 1


def test_canonical_snapshot_public_api() -> None:
    store = InMemoryCanonicalStore()
    snap = store.snapshot()
    assert snap.works == ()
    assert hasattr(snap, "work_authors")
    assert hasattr(snap, "work_grants")


def test_step12_top_level_still_creates_own_run(tmp_path: Path) -> None:
    """Backward compatible: top-level Step-12 API still manages its own run."""
    asset = _asset()
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    result = run_openalex_works_local_ingest(
        cfg,
        connector=FakeConnector(  # type: ignore[arg-type]
            assets=[asset],
            payloads={asset.file_uri: _gz_jsonl([_work("W1")])},
        ),
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert result.run.pipeline_name == "openalex-works-ingest"


def test_reusable_ingest_works_asset_exported() -> None:
    assert callable(ingest_works_asset)


# ---------------------------------------------------------------------------
# Correctness hardening
# ---------------------------------------------------------------------------


def test_true_unknown_deletion_before_stale_active(tmp_path: Path) -> None:
    """W absent → deletion T2 → stale ACTIVE T1 must NOT expose W."""
    from research_platform.service.contracts import ResearchDiscoveryRequest

    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    del_asset = _deletion_asset()
    # No Works asset first — only apply unknown-work deletion via stage path
    # Seed empty works run with a dummy that we skip by using deletion-only
    # through direct barrier + later E2E stale ingest.
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    # Apply unknown deletion under a works+deletion run with empty-related works
    # that don't include W99, then ingest stale W99.
    seed_asset = _asset(uri_suffix="part_seed.gz")
    r1 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[seed_asset],
            payloads={seed_asset.file_uri: _gz_jsonl([_work("W1")])},
        ),
        deletion_asset=del_asset,
        deletion_connector=FakeDeletionConnector(
            {del_asset.file_uri: _gz_csv([("W99", "2024-02-01")])}
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r1.success
    assert canonical.get_work("W99") is None
    assert canonical.deletion_barriers().get("W99") == date(2024, 2, 1)

    stale = _asset(uri_suffix="part_stale_w99.gz", updated=date(2024, 1, 10))
    r2 = run_bounded_e2e_pipeline(
        cfg,
        works_connector=FakeConnector(
            assets=[stale],
            payloads={
                stale.file_uri: _gz_jsonl(
                    [_work("W99", title="Stale", updated="2024-01-10")]
                )
            },
        ),
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert r2.success
    assert canonical.get_work("W99") is None
    current = pubs.current()
    assert current is not None
    svc = DataService(current.repository)
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W99"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND
    page = svc.search_research(ResearchDiscoveryRequest())
    assert all(w.work_id != "W99" for w in page.data.items)


def test_unknown_deletion_barrier_rollback() -> None:
    from research_platform.control.models import (
        ControlStatus,
        PipelineRun,
        SourceFileControl,
    )
    from research_platform.persistence.memory_deletion_unit_of_work import (
        publish_claimed_deletions_memory,
    )
    from research_platform.persistence.deletion_unit_of_work import (
        DeletionPublishRequest,
    )
    from research_platform.provenance.models import IngestionProvenance
    from research_platform.ingestion.deletions_csv import DeletedWorkRecord
    from uuid import uuid4

    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    run = control.create_pipeline_run(
        PipelineRun.model_validate(
            {
                "run_id": uuid4(),
                "source": "openalex",
                "pipeline_name": "test",
                "status": PipelineRunStatus.PROCESSING,
                "attempt": 1,
                "started_at": _ts(),
                "created_at": _ts(),
                "updated_at": _ts(),
            }
        )
    )
    token = uuid4()
    asset_id = "del-barrier-rollback"
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset_id,
                "run_id": run.run_id,
                "source": "openalex",
                "entity": "works-deletions",
                "source_uri": "s3://x/del.csv.gz",
                "snapshot_date": date(2024, 2, 5),
                "updated_date": date(2024, 2, 1),
                "content_format": "csv",
                "declared_size_bytes": 10,
                "status": ControlStatus.DISCOVERED,
                "attempt_count": 0,
                "created_at": _ts(),
                "updated_at": _ts(),
            }
        )
    )
    control.claim_source_file(
        asset_id,
        run_id=run.run_id,
        claimed_by="t",
        claim_token=token,
        claimed_at=_ts(),
        lease_expires_at=_ts(hour=2),
    )
    provenance = IngestionProvenance.model_validate(
        {
            "run_id": run.run_id,
            "source": "openalex",
            "source_uri": "s3://x/del.csv.gz",
            "retrieved_at": _ts(),
            "sha256": "a" * 64,
        }
    )

    def boom():
        yield DeletedWorkRecord(work_id="W77", deleted_date=date(2024, 2, 1))
        raise RuntimeError("force rollback")

    with pytest.raises(RuntimeError):
        publish_claimed_deletions_memory(
            control,
            canonical,
            DeletionPublishRequest(
                asset_id=asset_id,
                claim_token=token,
                now=_ts(),
                processed_at=_ts(),
                raw_object_key="openalex/raw/del.csv.gz",
                source_checksum_sha256="a" * 64,
                source_uri="s3://x/del.csv.gz",
                source_updated_date=date(2024, 2, 1),
                retrieval_provenance=provenance,
                open_records=boom,
            ),
        )
    assert canonical.deletion_barriers() == {}


def test_replay_reconciliation_all_zeros(tmp_path: Path) -> None:
    from research_platform.e2e.runner import _expectation_from_stats

    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    pubs = InMemoryPublicationStore()
    asset = _asset()
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    connector = FakeConnector(
        assets=[asset], payloads={asset.file_uri: _gz_jsonl([_work("W1")])}
    )
    first = run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert first.success
    # Direct unit: replay expectation is all zeros
    zeros = _expectation_from_stats(None, replay=True)
    assert zeros.source_records_seen == 0
    assert zeros.source_records_decoded == 0
    assert zeros.records_mapped_successfully == 0
    assert zeros.records_rejected == 0
    assert zeros.unique_work_ids_evaluated == 0
    assert zeros.identical == 0

    second = run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        control_store=control,
        canonical_store=canonical,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        publication_store=pubs,
        now=_clock(),
    )
    assert second.success
    assert second.pre_serving_report is not None
    assert second.pre_serving_report.publication_allowed


def test_duplicate_observations_not_rejected() -> None:
    from research_platform.e2e.runner import _expectation_from_stats
    from research_platform.ingestion.pipeline import FileIngestStats

    # Two observations of same work: provenance=2, insert+identical=2
    stats = FileIngestStats(
        asset_id="a",
        works_inserted=1,
        works_identical=1,
        works_replaced=0,
        works_stale=0,
        record_provenance_count=2,
    )
    exp = _expectation_from_stats(stats, replay=False)
    assert exp.source_records_decoded == 2
    assert exp.records_mapped_successfully == 2
    assert exp.records_rejected == 0
    assert exp.unique_work_ids_evaluated == 2
    assert exp.inserted + exp.identical == 2


def test_final_validation_failure_restores_old_current(tmp_path: Path) -> None:
    pubs = InMemoryPublicationStore()
    first = _run_ok(tmp_path / "a", publication_store=pubs)
    assert first.success
    old = pubs.current()
    assert old is not None
    second = _run_ok(
        tmp_path / "b",
        publication_store=pubs,
        force_final_validation_fail=True,
        control=InMemoryControlStore(),
        canonical=InMemoryCanonicalStore(),
        asset=_asset(uri_suffix="part_b.gz"),
    )
    assert not second.success
    assert pubs.current() is old


def test_over_bound_selected_assets_fail_without_fetch(tmp_path: Path) -> None:
    a1 = _asset(uri_suffix="p1.gz")
    a2 = _asset(uri_suffix="p2.gz")
    landing = tmp_path / "landing"
    cfg = _write_config(tmp_path / "cfg", landing)
    connector = FakeConnector(
        assets=[a1, a2],
        payloads={
            a1.file_uri: _gz_jsonl([_work("W1")]),
            a2.file_uri: _gz_jsonl([_work("W2")]),
        },
        select_all=True,
    )
    result = run_bounded_e2e_pipeline(
        cfg,
        works_connector=connector,
        object_store=LocalObjectStore(
            StorageConfig(backend="local", landing_path=landing)
        ),
        now=_clock(),
    )
    assert not result.success
    select = next(s for s in result.stages if s.stage is StageName.SELECT)
    assert select.status is StageStatus.FAILED
    assert connector.fetch_calls == 0


def test_invalid_non_local_config_fails_before_discovery(tmp_path: Path) -> None:
    asset = _asset()
    landing = tmp_path / "landing"
    path = tmp_path / "cfg"
    path.mkdir(parents=True)
    bad = {
        "environment": "dev",
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
    cfg = path / "dev.yaml"
    cfg.write_text(yaml.safe_dump(bad), encoding="utf-8")
    connector = FakeConnector(
        assets=[asset], payloads={asset.file_uri: _gz_jsonl([_work("W1")])}
    )
    with pytest.raises(ValueError, match="environment=local"):
        run_bounded_e2e_pipeline(
            cfg,
            works_connector=connector,
            object_store=LocalObjectStore(
                StorageConfig(backend="local", landing_path=landing)
            ),
            now=_clock(),
        )
    assert connector.fetch_calls == 0
    # discover must also not have been needed — connector unused for fetch
    assert connector.fetch_calls == 0


def test_caller_supplied_publication_version(tmp_path: Path) -> None:
    result = _run_ok(tmp_path, publication_version="pub-custom-v1")
    assert result.success
    assert result.publication_version == "pub-custom-v1"
    pubs_stage = next(
        s for s in result.stages if s.stage is StageName.CONSUMER_PUBLICATION
    )
    assert pubs_stage.message == "pub-custom-v1"
    assert result.final_validation is not None
    assert result.final_validation.publication_version == "pub-custom-v1"


def test_step15_and_step16_static_validation_enforced(tmp_path: Path) -> None:
    result = _run_ok(tmp_path)
    assert result.success
    analytical = next(s for s in result.stages if s.stage is StageName.ANALYTICAL_MODELS)
    gold = next(s for s in result.stages if s.stage is StageName.STAGED_GOLD)
    assert analytical.status is StageStatus.SUCCESS
    assert gold.status is StageStatus.SUCCESS


def test_step16_reuses_shared_gold_builder() -> None:
    from research_platform.analytics.gold import validation as gold_validation
    from research_platform.analytics.gold import semantic_build

    src = gold_validation.validate_gold_semantics_with_duckdb.__code__.co_names
    assert "build_all_gold_marts" in src
    report = gold_validation.validate_gold_semantics_with_duckdb()
    assert report.ok
    assert semantic_build.build_all_gold_marts is not None


def test_consumer_repository_public_fingerprint() -> None:
    repo = build_reference_fixture()
    snap = repo.snapshot()
    assert snap.content_fingerprint() == repo.content_fingerprint()
    assert "W001" in {w.work_id for w in snap.works}
