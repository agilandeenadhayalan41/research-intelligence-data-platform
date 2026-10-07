"""Unit tests for Step 16 Gold analytical mart contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_platform.analytics.gold.contracts import (
    MATERIALIZATION_PROMOTION_RULE,
    EvidenceStatus,
    GoldColumn,
    GoldMartContract,
    GoldRefreshImpact,
    MaterializationMode,
    FanoutStrategy,
    RefreshStrategy,
)
from research_platform.analytics.gold.registry import (
    load_gold_registry,
    serialize_gold_registry,
)
from research_platform.analytics.gold.validation import (
    validate_gold_semantics_with_duckdb,
    validate_static_gold_contracts,
)
from research_platform.benchmarks.registry import load_query_pattern_registry

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gold_registry_loads_all_required_marts() -> None:
    marts = load_gold_registry()
    ids = {m.mart_id for m in marts}
    assert ids == {
        "research_discovery",
        "journal_author_stats",
        "publisher_author_stats",
        "publisher_topic_year_stats",
        "publisher_topic_license_year_stats",
        "institution_topic_stats",
        "publication_trends",
        "open_access_trends",
        "citation_edges",
    }


def test_unique_mart_ids_and_grains() -> None:
    marts = load_gold_registry()
    assert len(marts) == len({m.mart_id for m in marts})
    for mart in marts:
        assert mart.grain_keys
        assert mart.result_grain
        for key in mart.grain_keys:
            assert key in {c.name for c in mart.output_columns}


def test_source_pattern_ids_exist_in_step14() -> None:
    patterns = {p.pattern_id for p in load_query_pattern_registry().patterns}
    for mart in load_gold_registry():
        for pid in mart.source_pattern_ids:
            assert pid in patterns, f"{mart.mart_id} -> {pid}"


def test_no_materialized_without_measured() -> None:
    for mart in load_gold_registry():
        assert mart.materialization_mode is not MaterializationMode.MATERIALIZED
        if mart.materialization_mode is MaterializationMode.MATERIALIZATION_CANDIDATE:
            assert mart.future_measurement_required.strip()
            assert mart.evidence_status is not EvidenceStatus.MEASURED


def test_materialization_mode_matches_step14_intent() -> None:
    modes = {m.mart_id: m.materialization_mode for m in load_gold_registry()}
    assert modes["research_discovery"] is MaterializationMode.COMPUTE_ON_READ
    assert modes["institution_topic_stats"] is MaterializationMode.COMPUTE_ON_READ
    assert modes["citation_edges"] is MaterializationMode.COMPUTE_ON_READ
    for mid in (
        "journal_author_stats",
        "publisher_author_stats",
        "publisher_topic_year_stats",
        "publisher_topic_license_year_stats",
        "publication_trends",
        "open_access_trends",
    ):
        assert modes[mid] is MaterializationMode.MATERIALIZATION_CANDIDATE


def test_promotion_rule_documented() -> None:
    assert "MEASURED" in MATERIALIZATION_PROMOTION_RULE
    assert "MATERIALIZATION_CANDIDATE" in MATERIALIZATION_PROMOTION_RULE


def test_materialized_rejected_without_measured() -> None:
    with pytest.raises(ValueError, match="MEASURED"):
        GoldMartContract(
            mart_id="fake_mart",
            name="Fake",
            sql_path="sql/bigquery/openalex/gold/fake.sql",
            source_pattern_ids=("publication-trends",),
            consumer_purpose="test",
            result_grain="one row per x",
            grain_keys=("x",),
            input_tables=("works",),
            output_columns=(GoldColumn(name="x", bq_type="STRING", nullable=False),),
            fanout_strategy=FanoutStrategy.ACTIVE_WORKS_ONLY,
            null_handling="n/a",
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_YEAR_BUCKETS,
            materialization_mode=MaterializationMode.MATERIALIZED,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale="looks useful",
            freshness_notes="n/a",
            cost_safety_notes="n/a",
        )


def test_candidate_requires_future_measurement() -> None:
    with pytest.raises(ValueError, match="future_measurement_required"):
        GoldMartContract(
            mart_id="fake_mart",
            name="Fake",
            sql_path="sql/bigquery/openalex/gold/fake.sql",
            source_pattern_ids=("publication-trends",),
            consumer_purpose="test",
            result_grain="one row per x",
            grain_keys=("x",),
            input_tables=("works",),
            output_columns=(GoldColumn(name="x", bq_type="STRING", nullable=False),),
            fanout_strategy=FanoutStrategy.ACTIVE_WORKS_ONLY,
            null_handling="n/a",
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_YEAR_BUCKETS,
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale="candidate",
            future_measurement_required="",
            freshness_notes="n/a",
            cost_safety_notes="n/a",
        )


def test_no_issn_eissn_in_outputs() -> None:
    for mart in load_gold_registry():
        names = {c.name for c in mart.output_columns}
        assert "issn" not in names
        assert "eissn" not in names


def test_sql_files_exist() -> None:
    for mart in load_gold_registry():
        path = REPO_ROOT / mart.sql_path
        assert path.is_file(), mart.sql_path


def test_static_validation_passes() -> None:
    report = validate_static_gold_contracts(repo_root=REPO_ROOT)
    assert report.ok, report.errors
    assert report.checks_passed > 0


def test_semantic_validation_passes() -> None:
    report = validate_gold_semantics_with_duckdb()
    assert report.label.value == "SEMANTIC_ONLY"
    assert report.ok, report.errors
    assert "SEMANTIC_ONLY" in report.results.get("evidence_note", "")
    assert report.results.get("validation_label") == "SEMANTIC_ONLY"


def test_registry_serialization_deterministic() -> None:
    a = serialize_gold_registry()
    b = serialize_gold_registry()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_refresh_impact_contract() -> None:
    impact = GoldRefreshImpact(
        work_id="W1",
        old_publication_year=2019,
        new_publication_year=2020,
        old_publisher_id="P1",
        new_publisher_id="P2",
        deletion_transition=False,
    )
    assert "bounded partition" in impact.notes.lower() or "recomput" in impact.notes.lower()


def test_license_sql_forbids_min_max_any_value() -> None:
    text = (REPO_ROOT / "sql/bigquery/openalex/gold/publisher_topic_license_year_stats.sql").read_text()
    lower = text.lower()
    assert "is_primary" in lower
    # Strip comments before forbidding aggregate license selection phrases.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    ).lower()
    assert "min(license)" not in code
    assert "max(license)" not in code
    assert "any_value(license)" not in code


def test_research_discovery_has_independent_ctes() -> None:
    text = (REPO_ROOT / "sql/bigquery/openalex/gold/research_discovery.sql").read_text().lower()
    assert "authors_agg" in text
    assert "topics_agg" in text
    assert "institutions_agg" in text


def test_institution_topic_has_distinct_works() -> None:
    text = (REPO_ROOT / "sql/bigquery/openalex/gold/institution_topic_stats.sql").read_text()
    assert "SELECT DISTINCT" in text or "select distinct" in text.lower()


def test_citation_edges_uses_left_join_target() -> None:
    text = (REPO_ROOT / "sql/bigquery/openalex/gold/citation_edges.sql").read_text().lower()
    assert "left join" in text
    assert "activity_state" in text


def test_docs_exist() -> None:
    assert (REPO_ROOT / "docs/architecture/gold-analytical-marts.md").is_file()
