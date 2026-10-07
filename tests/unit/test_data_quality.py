"""Unit tests for Step 17 data-quality gates and metrics."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from research_platform.quality.models import (
    QUALITY_CONTRACT_VERSION,
    ErrorClassification,
    ExecutionStage,
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
    OWNED_RELATIONSHIP_TABLES,
    load_quality_registry,
    required_hard_gate_ids_for_stage,
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


def _run(
    stage: ExecutionStage = ExecutionStage.PRE_SERVING_BUILD,
    *,
    expectation: ReconciliationExpectation | None = _BALANCED,
    **kwargs,
) -> QualityReport:
    conn = _conn_with_fixture(**kwargs)
    try:
        return run_quality_checks(
            DuckDBQualityExecutor(conn),
            run_id="test-run",
            stage=stage,
            expectation=expectation,
        )
    finally:
        conn.close()


def _run_gold(**kwargs) -> QualityReport:
    return _run(ExecutionStage.PRE_VISIBLE_PUBLICATION, expectation=None, **kwargs)


def _result(report: QualityReport, check_id: str) -> QualityResult:
    return next(r for r in report.results if r.check_id == check_id)


def _empty_owned_relationship_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all owned relationship tables as empty (present, not missing)."""
    specs = {
        "work_authors": (
            "work_id VARCHAR, authorship_index INTEGER, author_id VARCHAR"
        ),
        "work_author_institutions": (
            "work_id VARCHAR, authorship_index INTEGER, "
            "institution_index INTEGER, institution_id VARCHAR"
        ),
        "work_topics": "work_id VARCHAR, topic_id VARCHAR",
        "work_keywords": "work_id VARCHAR, keyword_id VARCHAR",
        "work_references": (
            "work_id VARCHAR, reference_index INTEGER, "
            "referenced_work_id VARCHAR, reference_status VARCHAR"
        ),
        "work_mesh": "work_id VARCHAR, mesh_index INTEGER, descriptor_ui VARCHAR",
        "work_locations": (
            "work_id VARCHAR, location_index INTEGER, source_id VARCHAR, is_primary BOOLEAN"
        ),
        "work_grants": "work_id VARCHAR, grant_index INTEGER, funder_id VARCHAR",
    }
    for name, cols in specs.items():
        conn.execute(f"CREATE TABLE {name} ({cols})")
        assert name in OWNED_RELATIONSHIP_TABLES


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
    report = _run_gold(include_deleted_in_gold=True)
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["inactive_source_work_count"] >= 1
    assert report.publication_allowed is False
    assert report.execution_stage is ExecutionStage.PRE_VISIBLE_PUBLICATION


def test_deleted_citation_target_allowed_source_fails() -> None:
    ok = _run_gold()
    assert _result(ok, "gold.deleted_source_work_exclusion").status is QualityStatus.PASS
    bad = _run_gold(include_deleted_in_gold=True)
    assert _result(bad, "gold.deleted_source_work_exclusion").diagnostic_counts[
        "inactive_citation_source_rows"
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
        CREATE TABLE works (
          work_id VARCHAR, doi VARCHAR, title VARCHAR,
          publication_year INTEGER, work_type VARCHAR, activity_state VARCHAR
        )
        """
    )
    _empty_owned_relationship_tables(conn)
    for t, pk in (
        ("authors", "author_id"),
        ("institutions", "institution_id"),
        ("topics", "topic_id"),
        ("sources", "source_id"),
        ("funders", "funder_id"),
    ):
        conn.execute(f"CREATE TABLE {t} ({pk} VARCHAR)")
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="empty",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        expectation=_BALANCED,
    )
    conn.close()
    assert report.execution_stage is ExecutionStage.PRE_SERVING_BUILD
    assert _result(report, "canonical.works.work_id_not_null").status is QualityStatus.PASS
    assert _result(report, "canonical.works.work_id_unique").status is QualityStatus.PASS
    assert _result(report, "canonical.relationships.source_work_integrity").status is QualityStatus.PASS
    doi = _result(report, "metric.active_works.missing_doi")
    assert doi.diagnostic_counts["missing_count"] == 0
    assert doi.diagnostic_counts["active_work_denominator"] == 0
    assert doi.metric_points[0].rate is None


def test_relationship_only_no_works_fails() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE works (
          work_id VARCHAR, activity_state VARCHAR
        )
        """
    )
    _empty_owned_relationship_tables(conn)
    conn.execute("INSERT INTO work_authors VALUES ('W1', 0, 'A1')")
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
        DuckDBQualityExecutor(conn),
        run_id="bad-recon",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        expectation=bad_exp,
    )
    conn.close()
    assert _result(report, "reconciliation.decode_balance").status is QualityStatus.FAIL
    assert report.publication_allowed is False


def test_missing_reconciliation_input_error() -> None:
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="no-recon",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        expectation=None,
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
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="empty-db",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        expectation=None,
    )
    conn.close()
    assert report.publication_allowed is False
    assert report.hard_gate_passed is False
    assert report.execution_stage is ExecutionStage.PRE_SERVING_BUILD
    assert any(r.status is QualityStatus.ERROR for r in report.results)


def test_missing_required_hard_gate_result_blocks() -> None:
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=("canonical.works.work_id_not_null",),
        results=(),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_wrong_check_kind_for_required_gate_blocks() -> None:
    wrong = QualityResult(
        check_id="canonical.works.work_id_not_null",
        check_kind=QualityCheckKind.INFORMATIONAL_METRIC,
        scope=QualityScope.CANONICAL,
        model_name="works",
        run_id="x",
        status=QualityStatus.PASS,
        observed_value=0,
    )
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=("canonical.works.work_id_not_null",),
        results=(wrong,),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_duplicate_result_ids_block() -> None:
    r1 = QualityResult(
        check_id="canonical.works.work_id_not_null",
        check_kind=QualityCheckKind.HARD_GATE,
        scope=QualityScope.CANONICAL,
        model_name="works",
        run_id="x",
        status=QualityStatus.PASS,
        observed_value=0,
    )
    r2 = QualityResult(
        check_id="canonical.works.work_id_not_null",
        check_kind=QualityCheckKind.HARD_GATE,
        scope=QualityScope.CANONICAL,
        model_name="works",
        run_id="x",
        status=QualityStatus.PASS,
        observed_value=0,
    )
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=("canonical.works.work_id_not_null",),
        results=(r1, r2),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_informational_metric_never_blocks_or_permits_alone() -> None:
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
        required_check_ids=required_hard_gate_ids_for_stage(
            ExecutionStage.PRE_SERVING_BUILD
        ),
        results=(info,),
    )
    assert hard_ok is False
    assert pub_ok is False


def test_report_serialization_deterministic_and_safe() -> None:
    report = _run()
    a = serialize_report(report)
    b = serialize_report(report)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["execution_stage"] == "PRE_SERVING_BUILD"
    blob = json.dumps(a).lower()
    assert "password" not in blob
    assert "postgres://" not in blob
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


def test_healthy_fixture_allows_both_stages() -> None:
    serving = _run()
    assert serving.contract_version == QUALITY_CONTRACT_VERSION
    assert serving.validation_label.value == "SEMANTIC_ONLY"
    assert serving.execution_stage is ExecutionStage.PRE_SERVING_BUILD
    assert serving.hard_gate_passed is True
    assert serving.publication_allowed is True
    assert "gold.deleted_source_work_exclusion" not in {
        r.check_id for r in serving.results
    }

    visible = _run_gold()
    assert visible.execution_stage is ExecutionStage.PRE_VISIBLE_PUBLICATION
    assert visible.hard_gate_passed is True
    assert visible.publication_allowed is True
    assert "canonical.works.work_id_not_null" not in {
        r.check_id for r in visible.results
    }
    gold = _result(visible, "gold.deleted_source_work_exclusion")
    assert gold.diagnostic_counts["missing_manifest_mart_count"] == 0
    assert gold.diagnostic_counts["unexpected_manifest_mart_count"] == 0
    assert gold.diagnostic_counts["duplicate_manifest_mart_count"] == 0
    assert gold.diagnostic_counts["invalid_manifest_mart_count"] == 0


def test_missing_relationship_table_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DROP TABLE work_topics")
    contract = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.relationships.source_work_integrity"
    )
    result = execute_check(contract, DuckDBQualityExecutor(conn), run_id="miss-topics")
    conn.close()
    assert result.status is QualityStatus.ERROR
    assert result.error_classification is ErrorClassification.MISSING_TABLE


def test_missing_work_references_table_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DROP TABLE work_references")
    contract = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.relationships.source_work_integrity"
    )
    result = execute_check(contract, DuckDBQualityExecutor(conn), run_id="miss-refs")
    conn.close()
    assert result.status is QualityStatus.ERROR
    assert result.error_classification is ErrorClassification.MISSING_TABLE


def test_missing_work_authors_dimension_relation_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DROP TABLE work_authors")
    contract = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.relationships.dimension_integrity"
    )
    result = execute_check(contract, DuckDBQualityExecutor(conn), run_id="miss-wa")
    conn.close()
    assert result.status is QualityStatus.ERROR
    assert result.error_classification is ErrorClassification.MISSING_TABLE


def test_missing_authors_dimension_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DROP TABLE authors")
    contract = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.relationships.dimension_integrity"
    )
    result = execute_check(contract, DuckDBQualityExecutor(conn), run_id="miss-authors")
    conn.close()
    assert result.status is QualityStatus.ERROR
    assert result.error_classification is ErrorClassification.MISSING_TABLE


def test_partial_gold_manifest_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DELETE FROM staged_gold_mart_manifest WHERE mart_id = 'citation_edges'")
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="partial-manifest",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["missing_manifest_mart_count"] == 1
    assert report.publication_allowed is False


def test_missing_citation_table_when_declared_errors() -> None:
    conn = _conn_with_fixture()
    conn.execute("DROP TABLE staged_gold_citation_edges")
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="miss-cite-table",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.ERROR
    assert r.error_classification is ErrorClassification.MISSING_TABLE
    assert report.publication_allowed is False


def test_staged_gold_source_absent_from_canonical_fails() -> None:
    conn = _conn_with_fixture()
    conn.execute(
        "INSERT INTO staged_gold_work_contributions VALUES ('research_discovery', 'W999')"
    )
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="absent-source",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["missing_source_work_count"] >= 1


def test_stage_local_required_ids() -> None:
    serving_ids = required_hard_gate_ids_for_stage(ExecutionStage.PRE_SERVING_BUILD)
    visible_ids = required_hard_gate_ids_for_stage(ExecutionStage.PRE_VISIBLE_PUBLICATION)
    assert "gold.deleted_source_work_exclusion" not in serving_ids
    assert "canonical.works.work_id_not_null" in serving_ids
    assert visible_ids == ("gold.deleted_source_work_exclusion",)
    serving = _run()
    assert all(
        c.execution_stage is ExecutionStage.PRE_SERVING_BUILD
        for c in load_quality_registry()
        if c.check_id in {r.check_id for r in serving.results}
    )


def test_checks_filter_cannot_omit_required_hard_gates() -> None:
    one = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.works.work_id_not_null"
    )
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="omit-required",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        checks=(one,),
        expectation=_BALANCED,
    )
    conn.close()
    assert report.publication_allowed is False
    assert report.hard_gate_passed is False
    assert any(
        r.check_id == "quality.configuration.required_gates"
        and r.status is QualityStatus.ERROR
        for r in report.results
    )


def test_checks_filter_may_omit_informational_metrics() -> None:
    required = tuple(
        c
        for c in load_quality_registry()
        if c.execution_stage is ExecutionStage.PRE_SERVING_BUILD
        and c.kind is QualityCheckKind.HARD_GATE
        and c.required
    )
    assert len(required) == 6
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="gates-only",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        checks=required,
        expectation=_BALANCED,
    )
    conn.close()
    assert report.publication_allowed is True
    assert all(r.check_kind is QualityCheckKind.HARD_GATE for r in report.results)


def test_omit_visible_publication_gate_blocked() -> None:
    # Supply only a PRE_SERVING informational metric; stage filter yields empty
    # PRE_VISIBLE selection → required gold gate missing → blocked.
    info = next(
        c
        for c in load_quality_registry()
        if c.check_id == "metric.active_works.missing_doi"
    )
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="omit-gold-gate",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
        checks=(info,),
    )
    conn.close()
    assert report.publication_allowed is False
    assert any(
        r.check_id == "quality.configuration.required_gates" for r in report.results
    )


def test_duplicate_manifest_mart_id_fails() -> None:
    conn = _conn_with_fixture()
    conn.execute(
        "INSERT INTO staged_gold_mart_manifest VALUES ('research_discovery')"
    )
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="dup-manifest",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["duplicate_manifest_mart_count"] == 1


def test_unexpected_manifest_mart_id_fails() -> None:
    conn = _conn_with_fixture()
    conn.execute("INSERT INTO staged_gold_mart_manifest VALUES ('not_a_real_mart')")
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="unexpected-mart",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["unexpected_manifest_mart_count"] == 1


def test_gold_mart_ids_match_step16_registry_exactly() -> None:
    from research_platform.analytics.gold.registry import load_gold_registry

    assert set(GOLD_MART_IDS) == {m.mart_id for m in load_gold_registry()}
    assert tuple(GOLD_MART_IDS) == tuple(m.mart_id for m in load_gold_registry())


def test_bigquery_gold_sql_has_exact_coverage_diagnostics() -> None:
    text = (
        REPO_ROOT
        / "sql/bigquery/openalex/quality/active_gold_deleted_work_exclusion.sql"
    ).read_text()
    assert "missing_manifest_mart_count" in text
    assert "unexpected_manifest_mart_count" in text
    assert "duplicate_manifest_mart_count" in text
    assert "invalid_manifest_mart_count" in text
    assert "UNNEST" in text
    for mart_id in GOLD_MART_IDS:
        assert mart_id in text


def test_null_manifest_mart_id_fails() -> None:
    conn = _conn_with_fixture()
    conn.execute(
        "INSERT INTO staged_gold_mart_manifest VALUES (CAST(NULL AS VARCHAR))"
    )
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="null-manifest",
        stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
    )
    conn.close()
    r = _result(report, "gold.deleted_source_work_exclusion")
    assert r.status is QualityStatus.FAIL
    assert r.diagnostic_counts["invalid_manifest_mart_count"] == 1
    assert report.publication_allowed is False


def test_unknown_custom_check_blocked() -> None:
    custom = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.works.work_id_not_null"
    ).model_copy(update={"check_id": "custom.unregistered.gate"})
    required = tuple(
        c
        for c in load_quality_registry()
        if c.execution_stage is ExecutionStage.PRE_SERVING_BUILD
        and c.kind is QualityCheckKind.HARD_GATE
        and c.required
    )
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="unknown-check",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        checks=(*required, custom),
        expectation=_BALANCED,
    )
    conn.close()
    assert report.publication_allowed is False
    assert any(
        r.check_id == "quality.configuration.required_gates"
        and r.diagnostic_counts.get("unknown_supplied_check_count") == 1
        for r in report.results
    )


def test_modified_contract_normalized_to_authoritative() -> None:
    """Same check_id with altered description is replaced by registry object."""
    required = [
        c
        for c in load_quality_registry()
        if c.execution_stage is ExecutionStage.PRE_SERVING_BUILD
        and c.kind is QualityCheckKind.HARD_GATE
        and c.required
    ]
    altered = required[0].model_copy(update={"description": "tampered description"})
    selection = (altered, *required[1:])
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="normalized",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        checks=tuple(selection),
        expectation=_BALANCED,
    )
    conn.close()
    assert report.publication_allowed is True


def test_duplicate_supplied_check_ids_blocked() -> None:
    gate = next(
        c
        for c in load_quality_registry()
        if c.check_id == "canonical.works.work_id_not_null"
    )
    required = tuple(
        c
        for c in load_quality_registry()
        if c.execution_stage is ExecutionStage.PRE_SERVING_BUILD
        and c.kind is QualityCheckKind.HARD_GATE
        and c.required
    )
    conn = _conn_with_fixture()
    report = run_quality_checks(
        DuckDBQualityExecutor(conn),
        run_id="dup-supplied",
        stage=ExecutionStage.PRE_SERVING_BUILD,
        checks=(gate, *required),
        expectation=_BALANCED,
    )
    conn.close()
    assert report.publication_allowed is False
    assert any(
        r.diagnostic_counts.get("duplicate_supplied_check_count") == 1
        for r in report.results
    )


def test_report_rejects_hard_gate_passed_with_failed_gate() -> None:
    with pytest.raises(ValueError, match="hard_gate_passed cannot be true"):
        QualityReport(
            run_id="x",
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            results=(
                QualityResult(
                    check_id="canonical.works.work_id_not_null",
                    check_kind=QualityCheckKind.HARD_GATE,
                    scope=QualityScope.CANONICAL,
                    model_name="works",
                    run_id="x",
                    status=QualityStatus.FAIL,
                    observed_value=1,
                    expected_value=0,
                ),
            ),
            hard_gate_passed=True,
            publication_allowed=True,
        )


def test_compute_blocks_when_any_hard_gate_errors() -> None:
    required_pass = QualityResult(
        check_id="canonical.works.work_id_not_null",
        check_kind=QualityCheckKind.HARD_GATE,
        scope=QualityScope.CANONICAL,
        model_name="works",
        run_id="x",
        status=QualityStatus.PASS,
        observed_value=0,
    )
    extra_error = QualityResult(
        check_id="quality.configuration.required_gates",
        check_kind=QualityCheckKind.HARD_GATE,
        scope=QualityScope.CANONICAL,
        model_name="quality_registry",
        run_id="x",
        status=QualityStatus.ERROR,
        error_classification=ErrorClassification.MISSING_INPUT,
        observed_value=1,
    )
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=("canonical.works.work_id_not_null",),
        results=(required_pass, extra_error),
    )
    assert hard_ok is False
    assert pub_ok is False
