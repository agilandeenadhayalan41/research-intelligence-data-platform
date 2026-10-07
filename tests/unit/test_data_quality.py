"""Unit tests for Step 17 data-quality gates and metrics."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from research_platform.quality.models import (
    QUALITY_CONTRACT_VERSION,
    ErrorClassification,
    QualityCheckKind,
    QualityReport,
    QualityResult,
    QualityScope,
    QualityStatus,
    ReconciliationExpectation,
    compute_publication_allowed,
    serialize_report,
)
from research_platform.quality.registry import (
    GOLD_MART_IDS,
    load_quality_registry,
    required_hard_gate_ids,
    serialize_quality_registry,
)
from research_platform.quality.runner import (
    DuckDBQualityExecutor,
    execute_check,
    run_quality_checks,
)
from research_platform.quality.validation import (
    seed_quality_semantic_fixture,
    validate_static_quality_contracts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_BALANCED = ReconciliationExpectation(
    source_records_seen=10,
    source_records_decoded=10,
    records_mapped_successfully=8,
    records_rejected=2,
    unique_work_ids_evaluated=6,
    inserted=2,
    updated=1,
    identical=1,
    stale=1,
    conflict=1,
    restore_required=0,
)


def _conn_with_fixture(**kwargs) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(database=":memory:")
    seed_quality_semantic_fixture(conn, **kwargs)
    return conn


def _run(**kwargs) -> QualityReport:
    conn = _conn_with_fixture(**kwargs)
    try:
        return run_quality_checks(
            DuckDBQualityExecutor(conn),
            run_id="test-run",
            expectation=_BALANCED,
        )
    finally:
        conn.close()


def _result(report: QualityReport, check_id: str) -> QualityResult:
    return next(r for r in report.results if r.check_id == check_id)


def test_stable_unique_check_ids() -> None:
    checks = load_quality_registry()
    ids = [c.check_id for c in checks]
    assert len(ids) == len(set(ids))
    assert QUALITY_CONTRACT_VERSION == "quality-contract-v1"


def test_hard_gate_vs_metric_separation() -> None:
    for c in load_quality_registry():
        if c.kind is QualityCheckKind.HARD_GATE:
            assert c.required is True
        else:
            assert c.required is False
            assert c.kind is QualityCheckKind.INFORMATIONAL_METRIC


def test_static_validation_passes() -> None:
    report = validate_static_quality_contracts(repo_root=REPO_ROOT)
    assert report.ok, report.errors


def test_registry_serialization_deterministic() -> None:
    a = serialize_quality_registry()
    b = serialize_quality_registry()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_work_id_not_null_pass_and_fail() -> None:
    ok = _run()
    assert _result(ok, "canonical.works.work_id_not_null").status is QualityStatus.PASS
    bad = _run(include_null_work_id=True)
    r = _result(bad, "canonical.works.work_id_not_null")
    assert r.status is QualityStatus.FAIL
    assert r.observed_value == 1
    assert bad.publication_allowed is False


def test_work_id_unique_pass_and_fail() -> None:
    ok = _run()
    assert _result(ok, "canonical.works.work_id_unique").status is QualityStatus.PASS
    bad = _run(include_duplicate_work_id=True)
    r = _result(bad, "canonical.works.work_id_unique")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["duplicate_work_id_count"] == 1
    # Never log the duplicate ID values in diagnostics.
    assert list(r.diagnostic_counts) == ["duplicate_work_id_count"]


def test_source_orphan_fails() -> None:
    report = _run(include_source_orphan=True)
    r = _result(report, "canonical.relationships.source_work_integrity")
    assert r.status is QualityStatus.FAIL
    assert r.observed_value >= 1
    assert report.publication_allowed is False


def test_dimension_orphan_fails() -> None:
    report = _run(include_dimension_orphan=True)
    r = _result(report, "canonical.relationships.dimension_integrity")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["total_orphan_count"] >= 1


def test_citation_target_absent_and_deleted_do_not_fail_orphans() -> None:
    report = _run()
    # Baseline fixture has TARGET_DELETED (W2) and TARGET_ABSENT (W999).
    assert _result(report, "canonical.relationships.source_work_integrity").status is QualityStatus.PASS
    ref = _result(report, "metric.active_works.reference_reconciliation")
    assert ref.diagnostic_counts["target_deleted"] >= 1
    assert ref.diagnostic_counts["target_absent"] >= 1
    assert ref.status is QualityStatus.PASS


def test_deleted_canonical_work_allowed() -> None:
    report = _run()
    # Integrity gates pass with DELETED W2 present in canonical.
    assert _result(report, "canonical.works.work_id_not_null").status is QualityStatus.PASS
    assert _result(report, "canonical.relationships.source_work_integrity").status is QualityStatus.PASS


def test_deleted_work_in_staged_gold_fails() -> None:
    report = _run(include_deleted_in_gold=True)
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert report.publication_allowed is False


def test_deleted_citation_target_allowed_source_fails() -> None:
    ok = _run()
    assert _result(ok, "gold.deleted_source_work_exclusion").status is QualityStatus.PASS
    # Deleted source citation edge is injected by include_deleted_in_gold.
    bad = _run(include_deleted_in_gold=True)
    assert _result(bad, "gold.deleted_source_work_exclusion").diagnostic_counts[
        "deleted_citation_source_rows"
    ] >= 1


def test_missing_doi_metric() -> None:
    report = _run()
    r = _result(report, "metric.active_works.missing_doi")
    # ACTIVE: W1,W3,W4,W5 → 4; W3 missing DOI → 1
    assert r.diagnostic_counts["missing_count"] == 1
    assert r.diagnostic_counts["active_work_denominator"] == 4
    assert r.metric_points[0].rate == pytest.approx(0.25)
    assert r.status is QualityStatus.PASS  # informational


def test_missing_title_metric() -> None:
    report = _run()
    r = _result(report, "metric.active_works.missing_title")
    # W4 NULL title, W5 empty → 2
    assert r.diagnostic_counts["missing_count"] == 2
    assert r.diagnostic_counts["active_work_denominator"] == 4


def test_without_topics_and_authors_metrics() -> None:
    report = _run()
    topics = _result(report, "metric.active_works.without_topics")
    authors = _result(report, "metric.active_works.without_authors")
    # ACTIVE without topics: W3, W5 → 2
    assert topics.diagnostic_counts["missing_count"] == 2
    # ACTIVE without authors: W3, W5 → 2
    assert authors.diagnostic_counts["missing_count"] == 2


def test_publication_year_and_type_distributions_include_null() -> None:
    report = _run()
    years = _result(report, "metric.active_works.publication_year_distribution")
    types = _result(report, "metric.active_works.work_type_distribution")
    year_dims = {p.dimension_value for p in years.metric_points}
    type_dims = {p.dimension_value for p in types.metric_points}
    assert None in year_dims  # W3
    assert None in type_dims  # W3
    assert "0" not in year_dims
    assert "unknown" not in year_dims


def test_reference_categories_sum() -> None:
    report = _run()
    r = _result(report, "metric.active_works.reference_reconciliation")
    assert r.diagnostic_counts["category_sum"] == r.diagnostic_counts["total_reference_rows"]
    assert r.status is QualityStatus.PASS


def test_empty_works_semantics() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE works AS SELECT * FROM (
          SELECT CAST(NULL AS VARCHAR) AS work_id, CAST(NULL AS VARCHAR) AS doi,
                 CAST(NULL AS VARCHAR) AS title, CAST(NULL AS INTEGER) AS publication_year,
                 CAST(NULL AS VARCHAR) AS work_type, CAST(NULL AS VARCHAR) AS activity_state
        ) WHERE 1 = 0
        """
    )
    for t in (
        "work_authors",
        "work_author_institutions",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_mesh",
        "work_locations",
        "work_grants",
        "authors",
        "institutions",
        "topics",
        "sources",
        "funders",
        "staged_gold_work_contributions",
        "staged_gold_citation_edges",
    ):
        if t.startswith("work_authors"):
            conn.execute(
                "CREATE TABLE work_authors AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS authorship_index, "
                "CAST(NULL AS VARCHAR) AS author_id) WHERE 1=0"
            )
        elif t == "work_author_institutions":
            conn.execute(
                "CREATE TABLE work_author_institutions AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS authorship_index, "
                "0 AS institution_index, CAST(NULL AS VARCHAR) AS institution_id) WHERE 1=0"
            )
        elif t == "work_topics":
            conn.execute(
                "CREATE TABLE work_topics AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, CAST(NULL AS VARCHAR) AS topic_id) WHERE 1=0"
            )
        elif t == "work_keywords":
            conn.execute(
                "CREATE TABLE work_keywords AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, CAST(NULL AS VARCHAR) AS keyword_id) WHERE 1=0"
            )
        elif t == "work_references":
            conn.execute(
                "CREATE TABLE work_references AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS reference_index, "
                "CAST(NULL AS VARCHAR) AS referenced_work_id, "
                "CAST(NULL AS VARCHAR) AS reference_status) WHERE 1=0"
            )
        elif t == "work_mesh":
            conn.execute(
                "CREATE TABLE work_mesh AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS mesh_index, "
                "CAST(NULL AS VARCHAR) AS descriptor_ui) WHERE 1=0"
            )
        elif t == "work_locations":
            conn.execute(
                "CREATE TABLE work_locations AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS location_index, "
                "CAST(NULL AS VARCHAR) AS source_id, TRUE AS is_primary) WHERE 1=0"
            )
        elif t == "work_grants":
            conn.execute(
                "CREATE TABLE work_grants AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS work_id, 0 AS grant_index, "
                "CAST(NULL AS VARCHAR) AS funder_id) WHERE 1=0"
            )
        elif t in {"authors", "institutions", "topics", "sources", "funders"}:
            pk = {
                "authors": "author_id",
                "institutions": "institution_id",
                "topics": "topic_id",
                "sources": "source_id",
                "funders": "funder_id",
            }[t]
            conn.execute(
                f"CREATE TABLE {t} AS SELECT * FROM "
                f"(SELECT CAST(NULL AS VARCHAR) AS {pk}) WHERE 1=0"
            )
        elif t == "staged_gold_work_contributions":
            conn.execute(
                "CREATE TABLE staged_gold_work_contributions AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS mart_id, "
                "CAST(NULL AS VARCHAR) AS work_id) WHERE 1=0"
            )
        elif t == "staged_gold_citation_edges":
            conn.execute(
                "CREATE TABLE staged_gold_citation_edges AS SELECT * FROM "
                "(SELECT CAST(NULL AS VARCHAR) AS source_work_id, 0 AS reference_index, "
                "CAST(NULL AS VARCHAR) AS referenced_work_id, "
                "CAST(NULL AS VARCHAR) AS target_activity_state) WHERE 1=0"
            )
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="empty",
        expectation=_BALANCED,
    )
    conn.close()
    assert _result(report, "canonical.works.work_id_not_null").status is QualityStatus.PASS
    assert _result(report, "canonical.works.work_id_unique").status is QualityStatus.PASS
    doi = _result(report, "metric.active_works.missing_doi")
    assert doi.diagnostic_counts["missing_count"] == 0
    assert doi.diagnostic_counts["active_work_denominator"] == 0
    assert doi.metric_points[0].rate is None


def test_relationship_only_no_works_fails() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE work_authors AS SELECT * FROM (VALUES ('W1', 0, 'A1')) "
        "AS t(work_id, authorship_index, author_id)"
    )
    # Minimal empty works
    conn.execute(
        """
        CREATE TABLE works AS SELECT * FROM (
          SELECT CAST(NULL AS VARCHAR) AS work_id, CAST(NULL AS VARCHAR) AS activity_state
        ) WHERE 1 = 0
        """
    )
    contract = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.relationships.source_work_integrity"
    )
    result = execute_check(
        contract, DuckDBQualityExecutor(conn), run_id="orphan-only"
    )
    conn.close()
    assert result.status is QualityStatus.FAIL
    assert result.observed_value == 1


def test_reconciliation_balances_pass_and_fail() -> None:
    ok = _run()
    assert _result(ok, "reconciliation.decode_balance").status is QualityStatus.PASS
    assert _result(ok, "reconciliation.publication_decision_balance").status is QualityStatus.PASS

    conn = _conn_with_fixture()
    bad_exp = ReconciliationExpectation(
        source_records_decoded=10,
        records_mapped_successfully=7,
        records_rejected=2,  # 7+2 != 10
        unique_work_ids_evaluated=5,
        inserted=1,
        updated=1,
        identical=1,
        stale=1,
        conflict=1,
        restore_required=0,  # sum 5 == evaluated — decode fails
    )
    report = run_quality_checks(
        DuckDBQualityExecutor(conn), run_id="bad-recon", expectation=bad_exp
    )
    conn.close()
    assert _result(report, "reconciliation.decode_balance").status is QualityStatus.FAIL
    assert report.publication_allowed is False


def test_missing_reconciliation_input_error() -> None:
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn), run_id="no-recon", expectation=None
    )
    conn.close()
    r = _result(report, "reconciliation.decode_balance")
    assert r.status is QualityStatus.ERROR
    assert r.error_classification is ErrorClassification.MISSING_INPUT
    assert report.publication_allowed is False


def test_missing_column_produces_error() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE works AS SELECT 1 AS not_work_id")
    contract = next(
        c for c in load_quality_registry() if c.check_id == "canonical.works.work_id_not_null"
    )
    result = execute_check(contract, DuckDBQualityExecutor(conn), run_id="bad-col")
    conn.close()
    assert result.status is QualityStatus.ERROR
    assert result.error_classification in {
        ErrorClassification.MISSING_COLUMN,
        ErrorClassification.QUERY_ERROR,
        ErrorClassification.EXECUTOR_ERROR,
    }


def test_error_required_gate_blocks_publication() -> None:
    conn = duckdb.connect(":memory:")
    # No tables at all → multiple ERROR hard gates
    report = run_quality_checks(
        DuckDBQualityExecutor(conn), run_id="empty-db", expectation=None
    )
    conn.close()
    assert report.publication_allowed is False
    assert report.hard_gate_passed is False
    assert any(r.status is QualityStatus.ERROR for r in report.results)


def test_missing_required_hard_gate_result_blocks() -> None:
    # Simulate missing result
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=("canonical.works.work_id_not_null",),
        results=(),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_informational_metric_never_blocks_or_permits_alone() -> None:
    # Only informational results — required hard gates missing → blocked
    info = QualityResult(
        check_id="metric.active_works.missing_doi",
        check_kind=QualityCheckKind.INFORMATIONAL_METRIC,
        scope=QualityScope.CANONICAL,
        model_name="works",
        run_id="x",
        status=QualityStatus.PASS,
        observed_value=0,
    )
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=required_hard_gate_ids(),
        results=(info,),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_report_serialization_deterministic_and_safe() -> None:
    report = _run()
    a = serialize_report(report)
    b = serialize_report(report)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    blob = json.dumps(a).lower()
    assert "password" not in blob
    assert "postgres://" not in blob
    # No raw author names / titles as diagnostic keys
    for r in report.results:
        for key in r.diagnostic_counts:
            assert key not in {"title", "doi", "author_name", "payload", "sql"}


def test_gold_mart_inventory_aligned_with_step16() -> None:
    assert "research_discovery" in GOLD_MART_IDS
    assert "citation_edges" in GOLD_MART_IDS
    assert len(GOLD_MART_IDS) == 9


def test_sql_files_exist() -> None:
    for c in load_quality_registry():
        if c.sql_path:
            assert (REPO_ROOT / c.sql_path).is_file(), c.sql_path


def test_docs_exist() -> None:
    assert (REPO_ROOT / "docs/architecture/data-quality.md").is_file()


def test_healthy_fixture_allows_publication() -> None:
    report = _run()
    assert report.contract_version == QUALITY_CONTRACT_VERSION
    assert report.validation_label.value == "SEMANTIC_ONLY"
    assert report.hard_gate_passed is True
    assert report.publication_allowed is True
