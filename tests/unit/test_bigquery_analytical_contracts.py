"""Offline tests for Step 15 BigQuery analytical contracts (#22)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow as pa
import pytest

from datetime import date

from research_platform.analytics.bigquery.contracts import (
    BIGQUERY_MAX_CLUSTER_COLUMNS,
    FORBIDDEN_INVENTED_SOURCE_FIELDS,
    LINEAGE_FIELD_NAMES,
    PRIMARY_KEYS,
    TABLE_GRAINS,
    YEAR_BOUNDED_PATTERN_IDS,
    ActiveFilterBehavior,
    ACCEPTED_PUBLICATION_DECISIONS,
    BigQueryTableContract,
    ClusteringSpec,
    IncrementalStrategy,
    PartitioningSpec,
    PublicationDecision,
    arrow_type_to_bigquery,
    build_table_contracts,
    columns_from_arrow_schema,
    decide_analytical_works_merge,
    is_accepted_publication_decision,
    render_create_table_ddl,
    render_partition_clause,
)
from research_platform.analytics.bigquery.registry import (
    UNRESOLVED_PATTERN_IDS,
    load_bigquery_analytical_registry,
    repo_path,
)
from research_platform.analytics.bigquery.validation import (
    assert_contracts_valid,
    validate_semantics_with_duckdb,
    validate_stale_relationship_publication_guard,
    validate_static_contracts,
)
from research_platform.benchmarks.registry import load_query_pattern_registry
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS, SOURCES_SCHEMA


def test_table_contracts_load_for_all_canonical_tables() -> None:
    contracts = build_table_contracts()
    names = {c.table_name for c in contracts}
    assert names == set(CANONICAL_SCHEMAS)
    assert len(contracts) == 15


def test_grains_unique_and_documented() -> None:
    contracts = build_table_contracts()
    for contract in contracts:
        assert contract.grain == TABLE_GRAINS[contract.table_name]
        assert contract.primary_logical_key == PRIMARY_KEYS[contract.table_name]
        # Grain text must mention the logical key pieces.
        for key in contract.primary_logical_key:
            assert key in contract.grain


def test_bigquery_types_valid_from_arrow() -> None:
    assert arrow_type_to_bigquery(pa.string()).value == "STRING"
    assert arrow_type_to_bigquery(pa.int32()).value == "INT64"
    assert arrow_type_to_bigquery(pa.int64()).value == "INT64"
    assert arrow_type_to_bigquery(pa.float64()).value == "FLOAT64"
    assert arrow_type_to_bigquery(pa.bool_()).value == "BOOL"
    assert arrow_type_to_bigquery(pa.date32()).value == "DATE"
    assert arrow_type_to_bigquery(pa.timestamp("us", tz="UTC")).value == "TIMESTAMP"
    with pytest.raises(TypeError):
        arrow_type_to_bigquery(pa.list_(pa.string()))


def test_partition_and_cluster_fields_exist() -> None:
    for contract in build_table_contracts():
        names = {c.name for c in contract.columns}
        if contract.partitioning.field is not None:
            assert contract.partitioning.field in names
        for field in contract.clustering.fields:
            assert field in names
        assert len(contract.clustering.fields) <= BIGQUERY_MAX_CLUSTER_COLUMNS


def test_lineage_fields_present_on_every_table() -> None:
    for contract in build_table_contracts():
        names = {c.name for c in contract.columns}
        assert LINEAGE_FIELD_NAMES.issubset(names)
        assert contract.lineage_preserved is True


def test_sources_preserves_issn_l_gap() -> None:
    columns = columns_from_arrow_schema(SOURCES_SCHEMA)
    names = {c.name for c in columns}
    assert "issn_l" in names
    assert FORBIDDEN_INVENTED_SOURCE_FIELDS.isdisjoint(names)


def test_all_implementable_step14_patterns_mapped() -> None:
    step14 = load_query_pattern_registry()
    registry = load_bigquery_analytical_registry()
    mapped = {q.pattern_id: q for q in registry.queries}
    assert {p.pattern_id for p in step14.patterns} == set(mapped)
    for pattern in step14.patterns:
        contract = mapped[pattern.pattern_id]
        if pattern.pattern_id in UNRESOLVED_PATTERN_IDS:
            assert contract.implementation_status == "UNRESOLVED"
            assert contract.sql_path is None
        else:
            assert contract.implementation_status == "IMPLEMENTED"
            assert contract.sql_path is not None
            assert repo_path(contract.sql_path).is_file()


def test_issn_eissn_remains_unresolved() -> None:
    registry = load_bigquery_analytical_registry()
    issn = next(q for q in registry.queries if q.pattern_id == "issn-source-lookup")
    assert issn.implementation_status == "UNRESOLVED"
    assert issn.sql_path is None
    assert any("issn_l" in dep or "ISSN" in dep for dep in issn.unresolved_dependencies)


def test_named_bigquery_parameters_use_at_syntax() -> None:
    registry = load_bigquery_analytical_registry()
    for query in registry.queries:
        if query.sql_path is None:
            continue
        text = repo_path(query.sql_path).read_text(encoding="utf-8")
        for param in query.parameters:
            assert f"@{param}" in text
            assert f":{param}" not in text


def test_no_invented_sources_issn_eissn_in_sql_tree() -> None:
    root = Path("sql/bigquery/openalex")
    for path in root.rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        assert "sources.issn" not in text
        assert "sources.eissn" not in text
        assert re.search(r"\b`issn`\b", text) is None
        assert re.search(r"\b`eissn`\b", text) is None


def test_no_min_max_license_selection() -> None:
    from research_platform.analytics.bigquery.validation import _strip_sql_comments

    license_sql = repo_path(
        "sql/bigquery/openalex/queries/publisher_topic_license_year.sql"
    ).read_text(encoding="utf-8")
    code = _strip_sql_comments(license_sql)
    assert "is_primary" in code.lower()
    assert re.search(r"\bMIN\s*\(\s*`?license`?\s*\)", code, re.I) is None
    assert re.search(r"\bMAX\s*\(\s*`?license`?\s*\)", code, re.I) is None


def test_active_filter_enforced_in_required_queries() -> None:
    registry = load_bigquery_analytical_registry()
    for query in registry.queries:
        if not query.active_filtering_required or query.sql_path is None:
            continue
        text = repo_path(query.sql_path).read_text(encoding="utf-8")
        assert "activity_state" in text and "ACTIVE" in text


def test_citation_target_left_join_semantics() -> None:
    text = repo_path(
        "sql/bigquery/openalex/queries/citation_relationships.sql"
    ).read_text(encoding="utf-8")
    assert "LEFT JOIN" in text.upper()
    assert "INNER JOIN" in text.upper()  # source work ACTIVE join


def test_incremental_merge_key_contracts_valid() -> None:
    registry = load_bigquery_analytical_registry()
    works = next(t for t in registry.tables if t.table_name == "works")
    topics = next(t for t in registry.tables if t.table_name == "work_topics")
    assert works.incremental_strategy is IncrementalStrategy.MERGE_BY_PRIMARY_KEY
    assert works.primary_logical_key == ("work_id",)
    assert topics.incremental_strategy is IncrementalStrategy.REPLACE_BY_WORK_ID
    merge_works = repo_path("sql/bigquery/openalex/models/merge_works.sql").read_text(
        encoding="utf-8"
    )
    assert "MERGE" in merge_works.upper()
    assert "work_id" in merge_works
    merge_topics = repo_path(
        "sql/bigquery/openalex/models/merge_work_topics.sql"
    ).read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION" in merge_topics.upper()
    assert "COMMIT TRANSACTION" in merge_topics.upper()
    assert "DELETE FROM" in merge_topics.upper()
    assert "INSERT INTO" in merge_topics.upper()
    assert "accepted_work_ids" in merge_topics
    assert "changed_work_ids" not in _strip_comments(merge_topics)
    assert "lineage_source_updated_date" in merge_topics


def _strip_comments(text: str) -> str:
    from research_platform.analytics.bigquery.validation import _strip_sql_comments

    return _strip_sql_comments(text)


def test_works_active_filter_behavior() -> None:
    works = next(t for t in build_table_contracts() if t.table_name == "works")
    assert (
        works.active_filter_behavior
        is ActiveFilterBehavior.RETAIN_ALL_REQUIRE_CONSUMER_FILTER
    )


def test_static_validation_passes() -> None:
    report = validate_static_contracts()
    assert report.ok, report.errors
    assert report.checks_passed > 50


def test_duckdb_semantic_validation() -> None:
    report = validate_semantics_with_duckdb()
    assert report.label.value == "SEMANTIC_ONLY"
    assert report.ok, report.errors
    assert report.results["fanout_unsafe_rows"] == 6
    assert report.results["fanout_safe_authors"] == 2
    assert report.results["journal_authors"] == 2
    assert report.results["deleted_source_citations"] == 0


def test_deleted_works_excluded_from_aggregate_fixtures() -> None:
    report = validate_semantics_with_duckdb()
    years = [row[0] for row in report.results["publication_trends"]]
    assert 2021 not in years  # W2 DELETED


def test_publisher_topic_and_primary_license_fixture_correct() -> None:
    report = validate_semantics_with_duckdb()
    assert report.results["publisher_topic"] == [
        ("P1", "T1", 2),
        ("P1", "T2", 1),
        ("P1", "T3", 1),
    ]
    licenses = {row[2] for row in report.results["primary_license"]}
    assert "cc-by" in licenses
    assert "cc-by-nc" not in licenses
    assert report.results["null_license_bucket"] == [(None, 1)]


def test_institution_topic_and_fanout_fixture_safe() -> None:
    report = validate_semantics_with_duckdb()
    assert report.results["institution_topic"] == [
        ("T1", 1),
        ("T2", 2),
        ("T3", 1),
    ]
    assert report.results["fanout_unsafe_rows"] > report.results["fanout_safe_authors"]


def test_deterministic_registry_serialization() -> None:
    registry = load_bigquery_analytical_registry()
    dump = registry.model_dump(mode="json")
    text_a = json.dumps(dump, sort_keys=True, separators=(",", ":"))
    text_b = json.dumps(
        load_bigquery_analytical_registry().model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert text_a == text_b


def test_ddl_render_matches_on_disk() -> None:
    for contract in build_table_contracts():
        rendered = render_create_table_ddl(contract)
        on_disk = repo_path(contract.ddl_path).read_text(encoding="utf-8")
        assert rendered == on_disk


def test_assert_contracts_valid_helper() -> None:
    registry = assert_contracts_valid()
    assert len(registry.tables) == 15


def test_clustering_limit_enforced_on_model() -> None:
    with pytest.raises(Exception):
        ClusteringSpec(
            fields=("a", "b", "c", "d", "e"),
            rationale="too many",
        )


def test_table_contract_rejects_missing_partition_field() -> None:
    base = build_table_contracts()[0]
    with pytest.raises(Exception):
        BigQueryTableContract(
            table_name=base.table_name,
            logical_source=base.logical_source,
            grain=base.grain,
            columns=base.columns,
            partitioning=PartitioningSpec(
                field="not_a_column",
                partition_type="DATE",
                rationale="bad",
            ),
            clustering=base.clustering,
            primary_logical_key=base.primary_logical_key,
            incremental_strategy=base.incremental_strategy,
            active_filter_behavior=base.active_filter_behavior,
            cost_safety_notes=base.cost_safety_notes,
            ddl_path=base.ddl_path,
        )


def test_canonical_schemas_have_unique_field_names() -> None:
    for name, schema in CANONICAL_SCHEMAS.items():
        names = list(schema.names)
        assert len(names) == len(set(names)), f"{name} has duplicate fields"


def test_works_keeps_both_source_and_lineage_updated_dates() -> None:
    works = CANONICAL_SCHEMAS["works"]
    assert "source_updated_date" in works.names
    assert "lineage_source_updated_date" in works.names
    assert works.names.count("source_updated_date") == 1
    assert works.names.count("lineage_source_updated_date") == 1
    contract = next(t for t in build_table_contracts() if t.table_name == "works")
    col_names = [c.name for c in contract.columns]
    assert "source_updated_date" in col_names
    assert "lineage_source_updated_date" in col_names
    ddl = repo_path(contract.ddl_path).read_text(encoding="utf-8")
    assert "`source_updated_date` DATE" in ddl
    assert "`lineage_source_updated_date` DATE" in ddl


def test_every_table_preserves_lineage_source_updated_date() -> None:
    for contract in build_table_contracts():
        assert "lineage_source_updated_date" in {c.name for c in contract.columns}
        ddl = repo_path(contract.ddl_path).read_text(encoding="utf-8")
        assert "`lineage_source_updated_date`" in ddl


def test_works_integer_range_partition_aligns_with_year_queries() -> None:
    works = next(t for t in build_table_contracts() if t.table_name == "works")
    assert works.partitioning.partition_type == "INTEGER_RANGE"
    assert works.partitioning.field == "publication_year"
    clause = render_partition_clause(works.partitioning)
    assert clause is not None
    assert "RANGE_BUCKET(`publication_year`" in clause
    assert "GENERATE_ARRAY(1000, 3001, 1)" in clause
    ddl = repo_path(works.ddl_path).read_text(encoding="utf-8")
    assert clause in ddl
    registry = load_bigquery_analytical_registry()
    for query in registry.queries:
        if query.pattern_id not in YEAR_BOUNDED_PATTERN_IDS:
            continue
        assert query.sql_path is not None
        text = repo_path(query.sql_path).read_text(encoding="utf-8")
        assert "publication_year" in text
        assert "@year_from" in text and "@year_to" in text


def test_relationship_partition_uses_lineage_source_updated_date() -> None:
    for contract in build_table_contracts():
        if contract.table_name.startswith("work_"):
            assert contract.partitioning.field == "lineage_source_updated_date"
            assert contract.partitioning.partition_type == "DATE"


def test_stale_work_merge_cannot_overwrite_newer_state() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 6, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 1, 1),
        source_activity_state="ACTIVE",
    )
    assert decision is PublicationDecision.STALE
    assert not is_accepted_publication_decision(decision)


def test_deleted_to_active_cannot_occur_through_ordinary_merge() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 2, 1),
        target_activity_state="DELETED",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 8, 1),
        source_activity_state="ACTIVE",
    )
    assert decision is PublicationDecision.RESTORE_REQUIRED
    assert not is_accepted_publication_decision(decision)


def test_active_t1_plus_deletion_t2_applies() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 1, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 2, 1),
        source_activity_state="DELETED",
    )
    assert decision is PublicationDecision.APPLY_UPDATE
    assert is_accepted_publication_decision(decision)


def test_equal_date_active_to_deleted_applies() -> None:
    """ACTIVE T2 + deletion T2 => tombstone wins (Step 13), not CONFLICT."""
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 2, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 2, 1),
        source_activity_state="DELETED",
    )
    assert decision is PublicationDecision.APPLY_UPDATE


def test_stale_deletion_skipped() -> None:
    """ACTIVE T3 + deletion T2 => STALE."""
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 3, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 2, 1),
        source_activity_state="DELETED",
    )
    assert decision is PublicationDecision.STALE


def test_equal_date_active_to_active_conflicts() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 1, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 1, 1),
        source_activity_state="ACTIVE",
    )
    assert decision is PublicationDecision.CONFLICT
    assert not is_accepted_publication_decision(decision)


def test_equal_date_deleted_to_deleted_conflicts() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 2, 1),
        target_activity_state="DELETED",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 2, 1),
        source_activity_state="DELETED",
    )
    assert decision is PublicationDecision.CONFLICT


def test_identical_checksum_does_not_accept_relationship_publish() -> None:
    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="aa" * 32,
        target_lineage_date=date(2024, 1, 1),
        target_activity_state="ACTIVE",
        source_checksum="aa" * 32,
        source_lineage_date=date(2024, 9, 1),
        source_activity_state="ACTIVE",
    )
    assert decision is PublicationDecision.IDENTICAL
    assert decision not in ACCEPTED_PUBLICATION_DECISIONS
    assert not is_accepted_publication_decision(decision)


def test_insert_is_accepted() -> None:
    decision = decide_analytical_works_merge(
        target_exists=False,
        target_checksum=None,
        target_lineage_date=None,
        target_activity_state=None,
        source_checksum="aa" * 32,
        source_lineage_date=date(2024, 1, 1),
        source_activity_state="ACTIVE",
    )
    assert decision is PublicationDecision.INSERT
    assert is_accepted_publication_decision(decision)


def test_merge_sql_encodes_precedence_guards() -> None:
    text = repo_path("sql/bigquery/openalex/models/merge_works.sql").read_text(
        encoding="utf-8"
    )
    assert "source_checksum_sha256" in text
    assert "lineage_source_updated_date" in text
    assert "DELETED" in text and "ACTIVE" in text
    # Equal-date deletion apply path present.
    assert "source.`activity_state` = 'DELETED'" in text
    assert "target.`activity_state` = 'ACTIVE'" in text
    pre = repo_path(
        "sql/bigquery/openalex/models/merge_works_preconditions.sql"
    ).read_text(encoding="utf-8")
    assert "RESTORE_REQUIRED" in pre
    assert "CONFLICT" in pre
    assert "STALE" in pre
    classify = repo_path(
        "sql/bigquery/openalex/models/classify_works_staging.sql"
    ).read_text(encoding="utf-8")
    assert "publication_decision" in classify


def test_relationship_delete_insert_inside_transaction() -> None:
    from research_platform.analytics.bigquery.validation import _strip_sql_comments

    text = repo_path(
        "sql/bigquery/openalex/models/merge_work_topics.sql"
    ).read_text(encoding="utf-8")
    code = _strip_sql_comments(text).upper()
    begin = code.index("BEGIN TRANSACTION")
    delete = code.index("DELETE FROM")
    insert = code.index("INSERT INTO")
    commit = code.index("COMMIT TRANSACTION")
    assert begin < delete < insert < commit
    assert "ACCEPTED_WORK_IDS" in code
    assert "CHANGED_WORK_IDS" not in code


def test_accepted_work_ids_excludes_rejected_outcomes() -> None:
    for decision in (
        PublicationDecision.STALE,
        PublicationDecision.CONFLICT,
        PublicationDecision.RESTORE_REQUIRED,
        PublicationDecision.IDENTICAL,
    ):
        assert not is_accepted_publication_decision(decision)
    for decision in (
        PublicationDecision.INSERT,
        PublicationDecision.APPLY_UPDATE,
    ):
        assert is_accepted_publication_decision(decision)


def test_stale_relationship_publication_guard_fixture() -> None:
    report = validate_stale_relationship_publication_guard()
    assert report.ok, report.errors
    assert report.results["stale_work_decision"] == "STALE"
    assert report.results["topics_after"] == ("T1", "T2")


def test_columns_from_arrow_rejects_duplicate_names() -> None:
    schema = pa.schema(
        [
            pa.field("source_updated_date", pa.date32()),
            pa.field("source_updated_date", pa.date32()),
        ]
    )
    with pytest.raises(ValueError, match="duplicate"):
        columns_from_arrow_schema(schema)
