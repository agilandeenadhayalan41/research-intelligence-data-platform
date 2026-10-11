"""Offline tests for the #103 deterministic synthetic cycle and batch-2 delta composer.

No network, cloud or BigQuery calls. Every file is written under ``tmp_path``;
the full cycle runs at ``TINY_SCALE`` only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from research_platform.analytics.bigquery.contracts import (
    PublicationDecision,
    decide_analytical_works_merge,
)
from research_platform.analytics.bigquery.sandbox.synthetic import (
    DEFAULT_ANCHOR_DATE,
    DEFAULT_SCALE,
    DEFAULT_SEED,
    DELTA_CASE_DECISIONS,
    EXPECTED_DECISIONS_FILE_NAME,
    LARGE_SCALE,
    MANIFEST_FILE_NAME,
    MAX_RAW_ASSET_BYTES,
    SYNTHETIC_ID_BASE,
    SYNTHETIC_SCALES,
    TINY_SCALE,
    WORK_TOPICS_STAGING_INJECTION_SCHEMA,
    DeltaCase,
    SyntheticCycleError,
    SyntheticCycleResult,
    SyntheticScale,
    _PinnedRunControlStore,
    build_batch1_raw_assets,
    build_batch2_raw_assets,
    check_raw_asset_limits,
    compose_delta_staging,
    is_synthetic_id,
    main,
    plan_synthetic_cycle,
    resolve_scale,
    synthetic_id,
    write_synthetic_cycle,
)
from research_platform.canonical.openalex import CANONICAL_SCHEMAS
from research_platform.canonical.openalex.export import (
    CANONICAL_TABLE_NAMES,
    CanonicalExportError,
    read_canonical_parquet,
)
from research_platform.control.models import PipelineRun, PipelineRunStatus
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletions_csv import iter_deleted_work_records
from research_platform.ingestion.jsonl import iter_jsonl_gz_records

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODELS_DIR = _REPO_ROOT / "sql/bigquery/openalex/models"

_ID_COLUMNS = {
    "works": ("work_id",),
    "authors": ("author_id",),
    "institutions": ("institution_id",),
    "sources": ("source_id", "host_publisher_id"),
    "publishers": ("publisher_id",),
    "topics": ("topic_id",),
    "funders": ("funder_id",),
    "work_authors": ("work_id", "author_id"),
    "work_author_institutions": ("work_id", "institution_id"),
    "work_topics": ("work_id", "topic_id"),
    "work_keywords": ("work_id",),
    "work_references": ("work_id",),
    "work_mesh": ("work_id",),
    "work_locations": ("work_id", "source_id"),
    "work_grants": ("work_id", "funder_id"),
}


@pytest.fixture(scope="module")
def cycle(tmp_path_factory: pytest.TempPathFactory) -> SyntheticCycleResult:
    return write_synthetic_cycle(tmp_path_factory.mktemp("cycle") / "out", scale="tiny")


def _expected_json(cycle: SyntheticCycleResult) -> dict[str, object]:
    return json.loads(cycle.path_for(EXPECTED_DECISIONS_FILE_NAME).read_text(encoding="utf-8"))


def _batch1(cycle: SyntheticCycleResult, table: str) -> pa.Table:
    return read_canonical_parquet(table, cycle.path_for(f"batch1/{table}.parquet"))


def _staging(cycle: SyntheticCycleResult, table: str) -> pa.Table:
    return read_canonical_parquet(table, cycle.path_for(f"batch2/{table}_staging.parquet"))


def _decisions(cycle: SyntheticCycleResult) -> dict[str, dict[str, object]]:
    return {row["work_id"]: row for row in _expected_json(cycle)["batch2_decisions"]}


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- scale and plan (no ingest) -------------------------------------------------


def test_default_scale_matches_the_plan_delta_composition() -> None:
    scale = DEFAULT_SCALE
    assert (scale.batch1_assets, scale.works_per_asset) == (4, 2_500)
    assert scale.case_counts() == {
        DeltaCase.INSERT_NEW: 200,
        DeltaCase.IDENTICAL_REPLAY: 200,
        DeltaCase.APPLY_UPDATE_NEWER: 200,
        DeltaCase.APPLY_UPDATE_TOMBSTONE: 100,
        DeltaCase.STALE_OLDER: 100,
        DeltaCase.STALE_NULL_SOURCE_DATE: 10,
        DeltaCase.CONFLICT_EQUAL_DATE: 100,
        DeltaCase.CONFLICT_NULL_DATES: 10,
        DeltaCase.RESTORE_REQUIRED: 100,
    }
    assert scale.batch1_works == 10_010
    assert scale.batch2_works == 1_020
    assert scale.dataset_scale == "synthetic-10010-works"
    assert set(DELTA_CASE_DECISIONS) == set(DeltaCase)
    assert set(DELTA_CASE_DECISIONS.values()) == set(PublicationDecision)


@pytest.mark.parametrize(
    "overrides",
    [
        {"restore_required": 300},
        {"conflict_null_dates": 11},
        {"works_per_asset": 150},
        {"works_per_asset": 50_001},
        {"batch1_deletions": 9_500},
        {"insert_new": 0},
        {"name": "Not A Name"},
        {"works_per_asset": 2_500.0},
    ],
)
def test_scale_rejects_mixes_that_do_not_fit(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SyntheticScale.model_validate({**DEFAULT_SCALE.model_dump(), **overrides})


def test_large_scale_is_ten_times_the_default_and_plans_consistently() -> None:
    """Validators and planned counts only: generating the large cycle takes hours."""
    scale = LARGE_SCALE
    assert SYNTHETIC_SCALES["large"] is scale and resolve_scale("large") is scale
    assert SyntheticScale.model_validate(scale.model_dump()) == scale
    assert (scale.batch1_assets, scale.works_per_asset) == (8, 12_500)
    assert scale.case_counts() == {
        case: 10 * count for case, count in DEFAULT_SCALE.case_counts().items()
    }
    assert (scale.undated_works, scale.batch1_deletions) == (100, 2_500)
    assert scale.batch1_works == 100_100
    assert scale.batch2_works == 10 * DEFAULT_SCALE.batch2_works == 10_200
    assert scale.dataset_scale == "synthetic-100100-works"
    # The batch-2 "newer" works asset and the deletion ledgers stay within the decoder counts.
    newer = scale.insert_new + scale.apply_update_newer + scale.restore_required
    assert newer <= JsonlDecodeLimits().max_records
    deletion_ids = scale.batch1_deletions + scale.apply_update_tombstone
    assert deletion_ids <= CsvDeletionDecodeLimits().max_ids

    plan = plan_synthetic_cycle(scale="large")
    assert [len(works) for works in plan.batch1_asset_works] == [12_500] * 8
    assert {case: len(numbers) for case, numbers in plan.cases.items()} == scale.case_counts()
    roles = [
        set(numbers)
        for case, numbers in plan.cases.items()
        if case is not DeltaCase.RESTORE_REQUIRED
    ]
    assert sum(len(role) for role in roles) == len(set().union(*roles))
    deleted = set(plan.batch1_deleted_works)
    assert len(deleted) == scale.batch1_deletions
    assert set(plan.cases[DeltaCase.RESTORE_REQUIRED]) <= deleted
    assert not deleted & set().union(*roles)
    assert len(set(plan.batch1_dates)) == 8
    assert min(plan.cases[DeltaCase.INSERT_NEW]) > scale.batch1_works


def test_resolve_scale_accepts_presets_and_instances_only() -> None:
    assert resolve_scale("tiny") is TINY_SCALE
    assert resolve_scale(DEFAULT_SCALE) is DEFAULT_SCALE
    with pytest.raises(SyntheticCycleError):
        resolve_scale("huge")


def test_synthetic_ids_use_the_reserved_range() -> None:
    assert synthetic_id("W", 1) == f"W{SYNTHETIC_ID_BASE + 1}"
    assert is_synthetic_id(synthetic_id("T", 42))
    assert not is_synthetic_id("W2741809807")  # real-looking 10-digit OpenAlex id
    assert not is_synthetic_id("keywords/x")
    with pytest.raises(SyntheticCycleError):
        synthetic_id("W", 0)
    with pytest.raises(SyntheticCycleError):
        synthetic_id("X", 1)


def test_plan_roles_are_disjoint_sized_and_seed_stable() -> None:
    plan = plan_synthetic_cycle()
    assert plan == plan_synthetic_cycle(seed=DEFAULT_SEED, scale="default")
    assert {case: len(numbers) for case, numbers in plan.cases.items()} == (
        DEFAULT_SCALE.case_counts()
    )
    roles = [
        set(numbers)
        for case, numbers in plan.cases.items()
        if case is not DeltaCase.RESTORE_REQUIRED
    ]
    assert sum(len(role) for role in roles) == len(set().union(*roles))
    deleted = set(plan.batch1_deleted_works)
    assert len(deleted) == DEFAULT_SCALE.batch1_deletions
    assert set(plan.cases[DeltaCase.RESTORE_REQUIRED]) <= deleted
    assert not deleted & set().union(*roles)
    equal_asset = set(plan.batch1_asset_works[plan.equal_date_asset_index])
    assert set(plan.cases[DeltaCase.CONFLICT_EQUAL_DATE]) <= equal_asset
    assert set(plan.cases[DeltaCase.APPLY_UPDATE_TOMBSTONE]) <= equal_asset
    assert set(plan.cases[DeltaCase.CONFLICT_NULL_DATES]) <= set(plan.undated_works)
    assert min(plan.cases[DeltaCase.INSERT_NEW]) > DEFAULT_SCALE.batch1_works
    # Distinct, recent lineage dates around the anchor, oldest first.
    assert len(set(plan.batch1_dates)) == DEFAULT_SCALE.batch1_assets
    assert plan.older_date < min(plan.batch1_dates) < max(plan.batch1_dates) < plan.newer_date
    assert max(plan.batch1_dates) < plan.batch1_deleted_date <= DEFAULT_ANCHOR_DATE
    assert (DEFAULT_ANCHOR_DATE - plan.older_date).days <= 60
    assert plan.run_id("b1_works_1") == plan_synthetic_cycle().run_id("b1_works_1")
    assert plan.run_id("b1_works_1") != plan.run_id("b1_works_2")
    other = plan_synthetic_cycle(seed=DEFAULT_SEED + 1)
    assert other.cases != plan.cases
    assert other.run_id("b1_works_1") != plan.run_id("b1_works_1")


def test_default_scale_raw_assets_stay_within_decode_limits() -> None:
    plan = plan_synthetic_cycle()
    jsonl = JsonlDecodeLimits()
    deletions = CsvDeletionDecodeLimits()
    batch1 = build_batch1_raw_assets(plan)
    batch2 = build_batch2_raw_assets(plan)
    works_assets = [asset for asset in batch1 if asset.kind == "works"]
    assert [asset.record_count for asset in works_assets] == [2_500] * 4 + [10]
    dated = [asset.metadata.updated_date for asset in works_assets if asset.metadata.updated_date]
    assert len(set(dated)) == 4 and works_assets[-1].metadata.updated_date is None
    for asset in (*batch1, *batch2):
        assert len(asset.payload) <= MAX_RAW_ASSET_BYTES
        assert asset.metadata.byte_size == len(asset.payload)
        assert asset.decompressed_bytes == len(gzip.decompress(asset.payload))
        if asset.kind == "works":
            assert asset.decompressed_bytes <= jsonl.max_decompressed_bytes
            assert asset.record_count <= jsonl.max_records
            assert asset.max_record_bytes <= jsonl.max_record_bytes
        else:
            assert asset.decompressed_bytes <= deletions.max_decompressed_bytes
            assert asset.record_count <= deletions.max_ids
            assert asset.max_record_bytes <= deletions.max_row_bytes
    assert sum(asset.record_count for asset in batch2 if asset.kind == "works") == (
        DEFAULT_SCALE.batch2_works - DEFAULT_SCALE.identical_replay
    )


def test_limit_check_refuses_assets_the_decoders_would_reject() -> None:
    asset = build_batch1_raw_assets(plan_synthetic_cycle(scale="tiny"))[0]
    with pytest.raises(SyntheticCycleError, match="max_records"):
        check_raw_asset_limits(asset, jsonl_limits=JsonlDecodeLimits(max_records=29))
    with pytest.raises(SyntheticCycleError, match="max_record_bytes"):
        check_raw_asset_limits(asset, jsonl_limits=JsonlDecodeLimits(max_record_bytes=64))
    with pytest.raises(SyntheticCycleError, match="max_file_size_bytes"):
        check_raw_asset_limits(asset, max_file_size_bytes=len(asset.payload) - 1)
    check_raw_asset_limits(
        asset,
        jsonl_limits=JsonlDecodeLimits(
            max_decompressed_bytes=asset.decompressed_bytes,
            max_records=asset.record_count,
            max_record_bytes=asset.max_record_bytes,
        ),
        max_file_size_bytes=len(asset.payload),
    )


def test_raw_bytes_are_seed_stable_and_seed_sensitive() -> None:
    first = build_batch1_raw_assets(plan_synthetic_cycle(scale="tiny"))
    again = build_batch1_raw_assets(plan_synthetic_cycle(scale="tiny"))
    other = build_batch1_raw_assets(plan_synthetic_cycle(scale="tiny", seed=DEFAULT_SEED + 1))
    assert [asset.sha256 for asset in first] == [asset.sha256 for asset in again]
    assert all(a.sha256 != b.sha256 for a, b in zip(first, other, strict=True))


def test_pinned_control_store_requires_an_explicit_run_id() -> None:
    store = _PinnedRunControlStore()
    now = datetime(2026, 10, 2, tzinfo=UTC)
    run = PipelineRun.model_validate(
        {
            "run_id": uuid4(),
            "source": "openalex",
            "pipeline_name": "test",
            "status": PipelineRunStatus.PROCESSING,
            "attempt": 1,
            "started_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    with pytest.raises(SyntheticCycleError):
        store.create_pipeline_run(run)
    pinned = UUID("00000000-0000-5000-8000-000000000103")
    store.pin_next_run_id(pinned)
    assert store.create_pipeline_run(run).run_id == pinned
    with pytest.raises(SyntheticCycleError):
        store.create_pipeline_run(run)


# --- full tiny cycle --------------------------------------------------------------


def test_tiny_raw_assets_decode_with_default_limits(cycle: SyntheticCycleResult) -> None:
    for entry in cycle.manifest["raw_assets"]:
        payload = cycle.path_for(entry["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        if entry["kind"] == "works":
            records = list(iter_jsonl_gz_records(io.BytesIO(payload), limits=JsonlDecodeLimits()))
            assert len(records) == entry["record_count"]
            assert all(is_synthetic_id(str(record["id"]).rsplit("/", 1)[-1]) for record in records)
        else:
            rows = list(iter_deleted_work_records(io.BytesIO(payload)))
            assert len(rows) == entry["record_count"]
            assert all(is_synthetic_id(row.work_id) for row in rows)


def test_batch1_runs_through_the_real_ingest_path_one_asset_per_run(
    cycle: SyntheticCycleResult,
) -> None:
    runs = [run for run in cycle.manifest["ingest_runs"] if run["batch"] == 1]
    assert [run["name"] for run in runs] == [
        "b1_works_1",
        "b1_works_2",
        "b1_works_3",
        "b1_works_4",
        "b1_works_undated",
        "b1_deletions",
    ]
    assert {run["status"] for run in runs} == {PipelineRunStatus.SUCCESS.value}
    assert len({run["run_id"] for run in runs}) == len(runs)
    assert runs[-1]["stats"]["deleted"] == TINY_SCALE.batch1_deletions
    assert runs[-1]["stats"]["unknown"] == runs[-1]["stats"]["stale"] == 0
    assert sum(run["stats"].get("works_inserted", 0) for run in runs) == TINY_SCALE.batch1_works

    works = _batch1(cycle, "works")
    assert works.num_rows == TINY_SCALE.batch1_works
    states = Counter(works.column("activity_state").to_pylist())
    assert states == {"ACTIVE": TINY_SCALE.batch1_works - 6, "DELETED": 6}
    # Each dated asset is one lineage date; the undated asset is NULL.
    topics = _batch1(cycle, "work_topics")
    lineage_dates = set(topics.column("lineage_source_updated_date").to_pylist())
    plan = plan_synthetic_cycle(scale="tiny")
    assert lineage_dates <= {*plan.batch1_dates, None}
    assert {*plan.batch1_dates} <= lineage_dates
    assert {run["run_id"] for run in runs} >= set(works.column("run_id").to_pylist())


def test_batch1_export_populates_every_table_with_synthetic_ids(
    cycle: SyntheticCycleResult,
) -> None:
    counts = cycle.manifest["counts"]["batch1_tables"]
    assert list(counts) == list(CANONICAL_TABLE_NAMES)
    assert all(count > 0 for count in counts.values())
    for name in CANONICAL_TABLE_NAMES:
        table = _batch1(cycle, name)
        assert table.schema.equals(CANONICAL_SCHEMAS[name], check_metadata=False)
        assert table.num_rows == counts[name]
        for column in _ID_COLUMNS[name]:
            values = [value for value in table.column(column).to_pylist() if value is not None]
            assert values and all(is_synthetic_id(value) for value in values)
    dois = [value for value in _batch1(cycle, "works").column("doi").to_pylist() if value]
    assert dois and all(value.startswith("10.5555/") for value in dois)
    institutions = _batch1(cycle, "institutions")
    assert set(institutions.column("ror").to_pylist()) == {None}
    assert set(_batch1(cycle, "authors").column("orcid").to_pylist()) == {None}
    assert set(_batch1(cycle, "sources").column("issn_l").to_pylist()) == {None}


def test_expected_decision_counts_match_decide_analytical_works_merge(
    cycle: SyntheticCycleResult,
) -> None:
    targets = {row["work_id"]: row for row in _batch1(cycle, "works").to_pylist()}
    recomputed: dict[str, str] = {}
    for source in _staging(cycle, "works").to_pylist():
        target = targets.get(source["work_id"])
        recomputed[source["work_id"]] = decide_analytical_works_merge(
            target_exists=target is not None,
            target_checksum=target and target["source_checksum_sha256"],
            target_lineage_date=target and target["lineage_source_updated_date"],
            target_activity_state=target and target["activity_state"],
            source_checksum=source["source_checksum_sha256"],
            source_lineage_date=source["lineage_source_updated_date"],
            source_activity_state=source["activity_state"],
        ).value
    decisions = _decisions(cycle)
    assert recomputed == {
        work_id: row["publication_decision"] for work_id, row in decisions.items()
    }
    expected = _expected_json(cycle)
    counts = Counter(recomputed.values())
    assert expected["publication"]["batch2"]["decision_counts"] == {
        decision.value: counts[decision.value] for decision in PublicationDecision
    }
    assert cycle.manifest["counts"]["batch2_decisions"] == expected["publication"]["batch2"][
        "decision_counts"
    ]
    for row in decisions.values():
        planned = DELTA_CASE_DECISIONS[DeltaCase(row["delta_case"])]
        assert row["publication_decision"] == planned.value
    assert expected["evidence_label"] == "SEMANTIC_ONLY"


def test_all_six_outcomes_and_the_null_date_edges_are_present(
    cycle: SyntheticCycleResult,
) -> None:
    counts = cycle.manifest["counts"]["batch2_decisions"]
    scale = TINY_SCALE
    assert counts == {
        "INSERT": scale.insert_new,
        "IDENTICAL": scale.identical_replay,
        "APPLY_UPDATE": scale.apply_update_newer + scale.apply_update_tombstone,
        "STALE": scale.stale_older + scale.stale_null_source_date,
        "CONFLICT": scale.conflict_equal_date + scale.conflict_null_dates,
        "RESTORE_REQUIRED": scale.restore_required,
    }
    assert all(value > 0 for value in counts.values())
    assert cycle.manifest["counts"]["batch2_delta_cases"] == {
        case.value: count for case, count in scale.case_counts().items()
    }

    targets = {row["work_id"]: row for row in _batch1(cycle, "works").to_pylist()}
    staged = {row["work_id"]: row for row in _staging(cycle, "works").to_pylist()}
    by_case: dict[str, list[str]] = {}
    for work_id, row in _decisions(cycle).items():
        by_case.setdefault(row["delta_case"], []).append(work_id)
    for work_id in by_case["STALE_NULL_SOURCE_DATE"]:
        assert staged[work_id]["lineage_source_updated_date"] is None
        assert targets[work_id]["lineage_source_updated_date"] is not None
    for work_id in by_case["CONFLICT_NULL_DATES"]:
        assert staged[work_id]["lineage_source_updated_date"] is None
        assert targets[work_id]["lineage_source_updated_date"] is None
    for work_id in by_case["APPLY_UPDATE_TOMBSTONE"]:
        assert staged[work_id]["activity_state"] == "DELETED"
        assert staged[work_id]["deleted_at"] is not None
        assert targets[work_id]["activity_state"] == "ACTIVE"
        assert (
            staged[work_id]["lineage_source_updated_date"]
            == targets[work_id]["lineage_source_updated_date"]
        )
    for work_id in by_case["RESTORE_REQUIRED"]:
        assert targets[work_id]["activity_state"] == "DELETED"
        assert staged[work_id]["activity_state"] == "ACTIVE"
    for work_id in by_case["IDENTICAL_REPLAY"]:
        assert staged[work_id] == targets[work_id]
    for work_id in by_case["INSERT_NEW"]:
        assert work_id not in targets


def test_staging_tables_are_unique_and_carry_rows_that_must_be_ignored(
    cycle: SyntheticCycleResult,
) -> None:
    works = _staging(cycle, "works")
    topics = _staging(cycle, "work_topics")
    work_ids = works.column("work_id").to_pylist()
    assert len(work_ids) == len(set(work_ids)) == TINY_SCALE.batch2_works
    assert work_ids == sorted(work_ids)
    keys = list(zip(topics.column("work_id").to_pylist(), topics.column("topic_id").to_pylist()))
    assert len(keys) == len(set(keys))
    assert {work_id for work_id, _ in keys} <= set(work_ids)
    decisions = _decisions(cycle)
    topic_work_cases = {decisions[work_id]["delta_case"] for work_id, _ in keys}
    assert "APPLY_UPDATE_TOMBSTONE" in topic_work_cases
    assert {"STALE_OLDER", "CONFLICT_EQUAL_DATE", "RESTORE_REQUIRED"} & topic_work_cases
    assert pq.ParquetFile(cycle.path_for("batch2/works_staging.parquet")).metadata.num_rows == (
        works.num_rows
    )


def test_expected_publication_effects_match_an_independent_replay(
    cycle: SyntheticCycleResult,
) -> None:
    works = _batch1(cycle, "works").to_pylist()
    state = {row["work_id"]: row["activity_state"] for row in works}
    b1_topics = _batch1(cycle, "work_topics").to_pylist()
    published = [
        (row["work_id"], row["topic_id"])
        for row in b1_topics
        if state[row["work_id"]] == "ACTIVE"
    ]
    publication = _expected_json(cycle)["publication"]
    assert publication["batch1"]["merge_work_topics"] == {"deleted": 0, "inserted": len(published)}
    assert publication["batch1"]["works_deleted_after"] == TINY_SCALE.batch1_deletions

    decisions = _decisions(cycle)
    eligible = {w for w, row in decisions.items() if row["relationship_refresh_eligible"]}
    staged_topics = [
        (row["work_id"], row["topic_id"]) for row in _staging(cycle, "work_topics").to_pylist()
    ]
    after_topics = [key for key in published if key[0] not in eligible]
    after_topics += [key for key in staged_topics if key[0] in eligible]
    batch2 = publication["batch2"]
    assert batch2["work_topics_rows_after"] == len(after_topics)
    assert batch2["merge_work_topics"]["deleted"] == len(published) - len(
        [key for key in published if key[0] not in eligible]
    )
    tombstones = {
        w for w, row in decisions.items() if row["delta_case"] == "APPLY_UPDATE_TOMBSTONE"
    }
    preserved = [key for key in after_topics if key[0] in tombstones]
    assert preserved and batch2["tombstone_work_topics_preserved"] == len(preserved)
    assert batch2["merge_works"]["inserted"] == TINY_SCALE.insert_new
    assert batch2["merge_works"]["updated"] == (
        TINY_SCALE.identical_replay
        + TINY_SCALE.apply_update_newer
        + TINY_SCALE.apply_update_tombstone
    )
    assert batch2["works_deleted_after"] == (
        TINY_SCALE.batch1_deletions + TINY_SCALE.apply_update_tombstone
    )


def test_injection_has_one_null_topic_id_for_an_eligible_work(
    cycle: SyntheticCycleResult,
) -> None:
    path = cycle.path_for("injection/work_topics_staging_null_topic_id.parquet")
    injected = pq.read_table(path)
    clean = _staging(cycle, "work_topics")
    assert injected.schema.equals(WORK_TOPICS_STAGING_INJECTION_SCHEMA, check_metadata=False)
    assert all(item.nullable for item in injected.schema)
    assert injected.num_rows == clean.num_rows
    assert injected.column("topic_id").null_count == 1
    row = injected.column("topic_id").to_pylist().index(None)
    info = _expected_json(cycle)["injection"]
    assert info["row_index"] == row == cycle.injection.row_index
    assert info["work_id"] == injected.column("work_id")[row].as_py()
    assert info["original_topic_id"] == clean.column("topic_id")[row].as_py()
    decision = _decisions(cycle)[info["work_id"]]
    assert decision["relationship_refresh_eligible"] is True
    assert decision["delta_case"] == DeltaCase.APPLY_UPDATE_NEWER.value
    # Every other cell is untouched.
    restored = injected.to_pylist()
    restored[row]["topic_id"] = info["original_topic_id"]
    assert restored == clean.to_pylist()
    # The Stage 1 exporter refuses this shape by design.
    with pytest.raises(CanonicalExportError):
        read_canonical_parquet("work_topics", path)
    clean_meta = pq.ParquetFile(cycle.path_for("batch2/work_topics_staging.parquet")).metadata
    injected_meta = pq.ParquetFile(path).metadata
    assert injected_meta.format_version == clean_meta.format_version
    assert injected_meta.row_group(0).column(0).compression == (
        clean_meta.row_group(0).column(0).compression
    )


def test_manifest_hashes_every_file_with_relative_paths_only(cycle: SyntheticCycleResult) -> None:
    text = cycle.manifest_path.read_text(encoding="utf-8")
    assert str(cycle.output_dir) not in text
    assert cycle.output_dir.as_posix() not in text
    manifest = json.loads(text)
    assert manifest == json.loads(json.dumps(cycle.manifest))
    assert manifest["seed"] == DEFAULT_SEED
    assert manifest["scale"] == TINY_SCALE.model_dump(mode="json")
    assert manifest["dataset_scale"] == "synthetic-123-works"
    assert manifest["data_class"] == "SYNTHETIC"
    hashes = _tree_hashes(cycle.output_dir)
    assert MANIFEST_FILE_NAME in hashes
    del hashes[MANIFEST_FILE_NAME]
    assert {entry["path"]: entry["sha256"] for entry in manifest["files"]} == hashes
    assert all(not Path(entry["path"]).is_absolute() for entry in manifest["files"])
    kinds = Counter(entry["kind"] for entry in manifest["files"])
    assert kinds == {
        "raw_works_asset": 9,
        "raw_deletion_asset": 2,
        "batch1_canonical_parquet": 15,
        "batch2_staging_parquet": 2,
        "injection_parquet": 1,
        "expected_decisions": 1,
    }


def test_rerun_with_the_same_seed_is_byte_identical(
    cycle: SyntheticCycleResult, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "again"), "--scale", "tiny"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert str(tmp_path) not in json.dumps(summary)
    manifest_sha256 = hashlib.sha256(cycle.manifest_path.read_bytes()).hexdigest()
    assert summary["manifest_sha256"] == manifest_sha256
    assert _tree_hashes(tmp_path / "again") == _tree_hashes(cycle.output_dir)


def test_compose_rejects_batch1_tables_from_another_seed(cycle: SyntheticCycleResult) -> None:
    with pytest.raises(SyntheticCycleError, match="do not match this plan|missing planned"):
        compose_delta_staging(
            _batch1(cycle, "works"),
            _batch1(cycle, "work_topics"),
            seed=DEFAULT_SEED + 1,
            scale="tiny",
        )


def test_write_refuses_a_non_empty_output_dir_and_unknown_scale(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SyntheticCycleError, match="non-empty"):
        write_synthetic_cycle(tmp_path, scale="tiny")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]
    with pytest.raises(SyntheticCycleError, match="unknown synthetic scale"):
        write_synthetic_cycle(tmp_path / "new", scale="huge")
    assert not (tmp_path / "new").exists()


def _duck_model_sql(name: str) -> str:
    """Repo model SQL with BigQuery quoting mapped to DuckDB (SEMANTIC_ONLY parity)."""
    text = (_MODELS_DIR / name).read_text(encoding="utf-8").replace("`", '"')
    return text.replace('MERGE "openalex.works"', 'MERGE INTO "openalex.works"')


def _duck_rows(conn: Any, table: str) -> dict[str, list[tuple[object, ...]]]:
    # Arrow fetch: TIMESTAMPTZ -> Python rows without DuckDB's optional pytz.
    result = conn.execute(f'SELECT * FROM "openalex.{table}" ORDER BY ALL').to_arrow_table()
    rows: dict[str, list[tuple[object, ...]]] = {}
    for row in result.to_pylist():
        values = tuple(row.values())
        rows.setdefault(values[0], []).append(values)
    return rows


def test_repo_publish_sql_in_duckdb_reproduces_the_expectations(
    cycle: SyntheticCycleResult,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(database=":memory:")
    for table in ("works", "work_topics"):
        conn.execute(
            f'CREATE TABLE "openalex.{table}" AS SELECT * FROM read_parquet(?) LIMIT 0',
            [str(cycle.path_for(f"batch1/{table}.parquet"))],
        )
        for item in CANONICAL_SCHEMAS[table]:
            if not item.nullable:
                conn.execute(
                    f'ALTER TABLE "openalex.{table}" ALTER COLUMN "{item.name}" SET NOT NULL'
                )

    def stage(works: str, topics: str) -> None:
        for table, relative in (("works_staging", works), ("work_topics_staging", topics)):
            conn.execute(
                f'CREATE OR REPLACE TABLE "openalex.{table}" AS SELECT * FROM read_parquet(?)',
                [str(cycle.path_for(relative))],
            )

    def decide() -> dict[str, str]:
        for name in (
            "work_publication_decisions.sql",
            "accepted_work_ids.sql",
            "relationship_publish_work_ids.sql",
        ):
            conn.execute(_duck_model_sql(name))
        return dict(
            conn.execute(
                'SELECT work_id, publication_decision FROM "openalex.work_publication_decisions"'
            ).fetchall()
        )

    publication = _expected_json(cycle)["publication"]
    stage("batch1/works.parquet", "batch1/work_topics.parquet")
    assert set(decide().values()) == {"INSERT"}
    (affected,) = conn.execute(_duck_model_sql("merge_works.sql")).fetchone()
    assert affected == publication["batch1"]["merge_works"]["inserted"]
    conn.execute(_duck_model_sql("merge_work_topics.sql"))
    topics_after_b1 = _duck_rows(conn, "work_topics")
    rows_after_b1 = sum(map(len, topics_after_b1.values()))
    assert rows_after_b1 == publication["batch1"]["work_topics_rows_after"]

    stage("batch2/works_staging.parquet", "batch2/work_topics_staging.parquet")
    decisions = _decisions(cycle)
    assert decide() == {work_id: row["publication_decision"] for work_id, row in decisions.items()}
    works_before = _duck_rows(conn, "works")
    (affected,) = conn.execute(_duck_model_sql("merge_works.sql")).fetchone()
    merge = publication["batch2"]["merge_works"]
    assert affected == merge["inserted"] + merge["updated"]
    works_after = _duck_rows(conn, "works")
    staged = _duck_rows(conn, "works_staging")
    for work_id, row in decisions.items():
        if row["publication_decision"] in {"STALE", "CONFLICT", "RESTORE_REQUIRED", "IDENTICAL"}:
            assert works_after[work_id] == works_before[work_id]
        else:
            assert works_after[work_id] == staged[work_id]

    # Injection 1: the NULL topic_id aborts the REPLACE transaction; nothing changes.
    stage("batch2/works_staging.parquet", "injection/work_topics_staging_null_topic_id.parquet")
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(_duck_model_sql("merge_work_topics.sql"))
    conn.execute("ROLLBACK")
    assert _duck_rows(conn, "work_topics") == topics_after_b1

    stage("batch2/works_staging.parquet", "batch2/work_topics_staging.parquet")
    conn.execute(_duck_model_sql("merge_work_topics.sql"))
    topics_after_b2 = _duck_rows(conn, "work_topics")
    rows_after_b2 = sum(map(len, topics_after_b2.values()))
    assert rows_after_b2 == publication["batch2"]["work_topics_rows_after"]
    tombstones = [
        w for w, row in decisions.items() if row["delta_case"] == "APPLY_UPDATE_TOMBSTONE"
    ]
    assert all(topics_after_b2.get(w) == topics_after_b1.get(w) for w in tombstones)
    assert sum(len(topics_after_b2.get(w, [])) for w in tombstones) == (
        publication["batch2"]["tombstone_work_topics_preserved"]
    )
    (deleted,) = conn.execute(
        """SELECT COUNT(*) FROM "openalex.works" WHERE activity_state = 'DELETED'"""
    ).fetchone()
    assert deleted == publication["batch2"]["works_deleted_after"]
    conn.close()


def test_anchor_date_shifts_every_lineage_date() -> None:
    plan = plan_synthetic_cycle(scale="tiny", anchor_date=date(2027, 1, 15))
    base = plan_synthetic_cycle(scale="tiny")
    shift = date(2027, 1, 15) - DEFAULT_ANCHOR_DATE
    assert plan.batch1_dates == tuple(value + shift for value in base.batch1_dates)
    assert plan.cases == base.cases
    assets = build_batch2_raw_assets(plan)
    assert {asset.metadata.updated_date for asset in assets} >= {
        plan.newer_date,
        plan.older_date,
        plan.equal_date,
        None,
    }
