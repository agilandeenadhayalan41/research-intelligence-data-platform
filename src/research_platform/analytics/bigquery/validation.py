"""Static BigQuery contract checks and DuckDB SEMANTIC_ONLY validation.

Static checks do not parse full SQL. DuckDB fixtures prove relationship /
activity semantics only — never BigQuery syntax, pruning, cost, or latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from research_platform.analytics.bigquery.contracts import (
    BIGQUERY_MAX_CLUSTER_COLUMNS,
    FORBIDDEN_INVENTED_SOURCE_FIELDS,
    LINEAGE_FIELD_NAMES,
    PUBLICATION_YEAR_RANGE_END_EXCLUSIVE,
    PUBLICATION_YEAR_RANGE_INTERVAL,
    PUBLICATION_YEAR_RANGE_START,
    YEAR_BOUNDED_PATTERN_IDS,
    ValidationLabel,
    render_partition_clause,
)
from research_platform.analytics.bigquery.registry import (
    UNRESOLVED_PATTERN_IDS,
    BigQueryAnalyticalRegistry,
    load_bigquery_analytical_registry,
    repo_path,
)

_MIN_MAX_LICENSE = re.compile(
    r"\b(MIN|MAX)\s*\(\s*`?license`?\s*\)", re.IGNORECASE
)
_SELECT_STAR = re.compile(r"\bSELECT\s+\*\b", re.IGNORECASE)
_ACTIVE_PREDICATE = re.compile(
    r"`?activity_state`?\s*=\s*'ACTIVE'", re.IGNORECASE
)
_SQL_LINE_COMMENT = re.compile(r"--.*?$", re.MULTILINE)


def _strip_sql_comments(text: str) -> str:
    """Remove `--` line comments before structural pattern checks."""
    return _SQL_LINE_COMMENT.sub("", text)


@dataclass(frozen=True)
class StaticValidationReport:
    label: ValidationLabel
    errors: tuple[str, ...]
    checks_passed: int

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_static_contracts(
    registry: BigQueryAnalyticalRegistry | None = None,
) -> StaticValidationReport:
    """Structural checks over contracts and BigQuery SQL files (no GCP)."""
    registry = registry or load_bigquery_analytical_registry()
    errors: list[str] = []
    passed = 0

    def ok(condition: bool, message: str) -> None:
        nonlocal passed
        if condition:
            passed += 1
        else:
            errors.append(message)

    for table in registry.tables:
        ddl = repo_path(table.ddl_path)
        ok(ddl.is_file(), f"missing DDL file: {table.ddl_path}")
        if ddl.is_file():
            text = ddl.read_text(encoding="utf-8")
            ok(
                f"`{table.table_name}`" in text or table.table_name in text,
                f"DDL does not mention table {table.table_name}",
            )
            for field in FORBIDDEN_INVENTED_SOURCE_FIELDS:
                ok(
                    f"`{field}`" not in text and f" {field} " not in text.lower(),
                    f"{table.table_name} DDL must not invent {field}",
                )
        ok(
            len(table.clustering.fields) <= BIGQUERY_MAX_CLUSTER_COLUMNS,
            f"{table.table_name} exceeds clustering limit",
        )
        col_names = {column.name for column in table.columns}
        ok(
            set(table.primary_logical_key).issubset(col_names),
            f"{table.table_name} primary key not in columns",
        )
        if table.partitioning.field:
            ok(
                table.partitioning.field in col_names,
                f"{table.table_name} partition field missing",
            )
        for cluster_field in table.clustering.fields:
            ok(
                cluster_field in col_names,
                f"{table.table_name} cluster field {cluster_field} missing",
            )
        ok(table.lineage_preserved, f"{table.table_name} must preserve lineage")
        ok(
            LINEAGE_FIELD_NAMES.issubset(col_names),
            f"{table.table_name} missing lineage_source_updated_date set",
        )
        if ddl.is_file():
            text = ddl.read_text(encoding="utf-8")
            ok(
                "`lineage_source_updated_date`" in text,
                f"{table.table_name} DDL missing lineage_source_updated_date",
            )

    works = next(t for t in registry.tables if t.table_name == "works")
    works_cols = {c.name for c in works.columns}
    ok("source_updated_date" in works_cols, "works must keep Work.source_updated_date")
    ok(
        "lineage_source_updated_date" in works_cols,
        "works must keep lineage_source_updated_date",
    )
    ok(
        works.partitioning.partition_type == "INTEGER_RANGE",
        "works must use INTEGER_RANGE on publication_year",
    )
    ok(
        works.partitioning.field == "publication_year",
        "works partition field must be publication_year",
    )
    works_ddl = repo_path(works.ddl_path).read_text(encoding="utf-8")
    expected_partition = render_partition_clause(works.partitioning)
    assert expected_partition is not None
    ok(
        expected_partition in works_ddl,
        "works DDL missing RANGE_BUCKET publication_year partition",
    )
    ok(
        f"GENERATE_ARRAY({PUBLICATION_YEAR_RANGE_START}, "
        f"{PUBLICATION_YEAR_RANGE_END_EXCLUSIVE}, "
        f"{PUBLICATION_YEAR_RANGE_INTERVAL})" in works_ddl,
        "works DDL missing publication_year GENERATE_ARRAY bounds",
    )

    sources = next(t for t in registry.tables if t.table_name == "sources")
    source_cols = {c.name for c in sources.columns}
    ok("issn_l" in source_cols, "sources must keep issn_l")
    ok("issn" not in source_cols, "sources must not invent issn")
    ok("eissn" not in source_cols, "sources must not invent eissn")

    for rel_path in registry.merge_contracts:
        ok(repo_path(rel_path).is_file(), f"missing MERGE contract: {rel_path}")
    ok(
        repo_path(registry.active_works_view_path).is_file(),
        f"missing active works view: {registry.active_works_view_path}",
    )

    merge_works = repo_path(
        "sql/bigquery/openalex/models/merge_works.sql"
    ).read_text(encoding="utf-8")
    merge_works_code = _strip_sql_comments(merge_works)
    ok(
        "lineage_source_updated_date" in merge_works_code,
        "merge_works must use lineage_source_updated_date",
    )
    ok(
        "DELETED" in merge_works_code and "ACTIVE" in merge_works_code,
        "merge_works must guard DELETED→ACTIVE resurrection",
    )
    ok(
        "source_checksum_sha256" in merge_works_code,
        "merge_works must compare checksums",
    )
    ok(
        ">" in merge_works_code,
        "merge_works must require newer lineage date to overwrite",
    )
    ok(
        "source.`activity_state` = 'DELETED'" in merge_works
        or "source.`activity_state` = 'DELETED'" in merge_works_code,
        "merge_works must allow equal-date ACTIVE→DELETED apply",
    )

    classify_sql = repo_path(
        "sql/bigquery/openalex/models/classify_works_staging.sql"
    ).read_text(encoding="utf-8")
    ok(
        "publication_decision" in classify_sql,
        "classify_works_staging must emit publication_decision",
    )
    ok(
        "APPLY_UPDATE" in classify_sql and "RESTORE_REQUIRED" in classify_sql,
        "classify_works_staging must encode APPLY/RESTORE decisions",
    )

    accepted_sql = repo_path(
        "sql/bigquery/openalex/models/accepted_work_ids.sql"
    ).read_text(encoding="utf-8")
    accepted_compact = accepted_sql.replace(" ", "").replace("\n", "")
    ok(
        "IN('INSERT','APPLY_UPDATE')" in accepted_compact,
        "accepted_work_ids filter must be INSERT/APPLY_UPDATE only",
    )
    ok(
        "STALE" in accepted_sql and "CONFLICT" in accepted_sql,
        "accepted_work_ids classify CASE must still name rejected outcomes",
    )

    merge_topics = repo_path(
        "sql/bigquery/openalex/models/merge_work_topics.sql"
    ).read_text(encoding="utf-8")
    topics_code = _strip_sql_comments(merge_topics).upper()
    ok("BEGIN TRANSACTION" in topics_code, "relationship replace needs BEGIN TRANSACTION")
    ok("COMMIT TRANSACTION" in topics_code, "relationship replace needs COMMIT TRANSACTION")
    begin_at = topics_code.find("BEGIN TRANSACTION")
    delete_at = topics_code.find("DELETE FROM")
    insert_at = topics_code.find("INSERT INTO")
    commit_at = topics_code.find("COMMIT TRANSACTION")
    ok(
        begin_at != -1
        and delete_at != -1
        and insert_at != -1
        and commit_at != -1
        and begin_at < delete_at < insert_at < commit_at,
        "DELETE+INSERT must sit inside BEGIN/COMMIT TRANSACTION",
    )
    ok(
        "lineage_source_updated_date" in merge_topics,
        "merge_work_topics must use lineage_source_updated_date",
    )
    ok(
        "ACCEPTED_WORK_IDS" in topics_code,
        "relationship SQL must scope to accepted_work_ids",
    )
    ok(
        "CHANGED_WORK_IDS" not in topics_code,
        "relationship SQL must not publish all changed_work_ids",
    )

    for query in registry.queries:
        if query.pattern_id in UNRESOLVED_PATTERN_IDS:
            ok(
                query.implementation_status == "UNRESOLVED",
                f"{query.pattern_id} must be UNRESOLVED",
            )
            ok(query.sql_path is None, f"{query.pattern_id} must not have SQL")
            continue
        assert query.sql_path is not None
        path = repo_path(query.sql_path)
        ok(path.is_file(), f"missing query SQL: {query.sql_path}")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        code = _strip_sql_comments(text)
        ok("@" in text or not query.parameters, f"{query.sql_path} missing @params")
        for param in query.parameters:
            ok(
                f"@{param}" in text,
                f"{query.sql_path} missing @{param}",
            )
        # Forbid SQLAlchemy/Postgres-style :name parameters in BigQuery files.
        for param in query.parameters:
            ok(
                f":{param}" not in code,
                f"{query.sql_path} must use @{param}, not :{param}",
            )
        ok(
            _MIN_MAX_LICENSE.search(code) is None,
            f"{query.sql_path} must not MIN/MAX(license)",
        )
        if "issn" in query.pattern_id or "eissn" in code.lower():
            for field in FORBIDDEN_INVENTED_SOURCE_FIELDS:
                ok(
                    f"sources.{field}" not in code and f".{field}" not in code,
                    f"{query.sql_path} must not reference invented {field}",
                )
        if query.active_filtering_required and query.pattern_id != "publisher-lookup":
            ok(
                _ACTIVE_PREDICATE.search(code) is not None
                or "active_works" in code.lower(),
                f"{query.sql_path} missing ACTIVE works predicate",
            )
        # Broad analytical aggregates must not SELECT *.
        if query.pattern_id in {
            "publication-trends",
            "open-access-trends",
            "publisher-topic-counts",
            "publisher-topic-license-year",
            "unique-authors-per-journal",
            "unique-authors-per-publisher",
            "institution-topic-relationships",
        }:
            ok(
                _SELECT_STAR.search(code) is None,
                f"{query.sql_path} must not SELECT * for aggregate workload",
            )
        if query.pattern_id == "publisher-topic-license-year":
            ok(
                "is_primary" in code.lower(),
                f"{query.sql_path} must filter primary location",
            )
        if query.pattern_id == "citation-relationships":
            ok(
                "LEFT JOIN" in code.upper(),
                f"{query.sql_path} must LEFT JOIN citation targets",
            )
        if query.pattern_id in YEAR_BOUNDED_PATTERN_IDS:
            ok(
                "publication_year" in code,
                f"{query.sql_path} must filter partition key publication_year",
            )
            ok(
                "@year_from" in text and "@year_to" in text,
                f"{query.sql_path} must expose year_from/year_to bounds",
            )

    ok(registry.maximum_bytes_billed > 0, "maximum_bytes_billed must be positive")
    return StaticValidationReport(
        label=ValidationLabel.BIGQUERY_SQL_CONTRACT,
        errors=tuple(errors),
        checks_passed=passed,
    )


def _seed_semantic_fixture(conn: duckdb.DuckDBPyConnection) -> None:
    """Tiny deterministic canonical fixture for SEMANTIC_ONLY checks."""
    conn.execute(
        """
        CREATE TABLE works AS SELECT * FROM (
          VALUES
            ('W1', 'https://openalex.org/W1', '10.1/aaa', 2020, DATE '2020-01-15',
             TRUE, 'gold', 'P1', 'S1', 'ACTIVE', CAST(NULL AS TIMESTAMP)),
            ('W2', 'https://openalex.org/W2', '10.1/bbb', 2021, DATE '2021-06-01',
             FALSE, 'closed', 'P1', 'S1', 'DELETED', TIMESTAMP '2024-01-01'),
            ('W3', 'https://openalex.org/W3', NULL, 2020, DATE '2020-03-01',
             TRUE, 'green', 'P2', 'S2', 'ACTIVE', CAST(NULL AS TIMESTAMP)),
            ('W4', 'https://openalex.org/W4', '10.1/ccc', 2019, DATE '2019-01-01',
             FALSE, 'closed', 'P1', 'S1', 'ACTIVE', CAST(NULL AS TIMESTAMP))
        ) AS t(work_id, work_id_url, doi, publication_year, publication_date,
              is_oa, oa_status, primary_publisher_id, primary_source_id,
              activity_state, deleted_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_authors AS SELECT * FROM (
          VALUES
            ('W1', 0, 'A1'),
            ('W1', 1, 'A2'),
            ('W2', 0, 'A1'),
            ('W3', 0, 'A2'),
            ('W3', 1, 'A3'),
            ('W4', 0, 'A1')
        ) AS t(work_id, authorship_index, author_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_topics AS SELECT * FROM (
          VALUES
            ('W1', 'T1'),
            ('W1', 'T2'),
            ('W1', 'T3'),
            ('W2', 'T1'),
            ('W3', 'T2'),
            ('W4', 'T1')
        ) AS t(work_id, topic_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_locations AS SELECT * FROM (
          VALUES
            ('W1', 0, 'S1', 'cc-by', TRUE),
            ('W1', 1, 'S9', 'cc-by-nc', FALSE),
            ('W3', 0, 'S2', CAST(NULL AS VARCHAR), TRUE),
            ('W4', 0, 'S1', 'cc-by', TRUE)
        ) AS t(work_id, location_index, source_id, license, is_primary)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_author_institutions AS SELECT * FROM (
          VALUES
            ('W1', 0, 0, 'I1'),
            ('W1', 1, 0, 'I1'),
            ('W1', 1, 1, 'I2'),
            ('W3', 0, 0, 'I1'),
            ('W2', 0, 0, 'I1')
        ) AS t(work_id, authorship_index, institution_index, institution_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_references AS SELECT * FROM (
          VALUES
            ('W1', 0, 'W2', 'RESOLVED_ID'),
            ('W1', 1, 'W999', 'RESOLVED_ID'),
            ('W1', 2, CAST(NULL AS VARCHAR), 'MISSING'),
            ('W2', 0, 'W3', 'RESOLVED_ID')
        ) AS t(work_id, reference_index, referenced_work_id, reference_status)
        """
    )


@dataclass(frozen=True)
class SemanticValidationReport:
    label: ValidationLabel
    results: dict[str, Any]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_semantics_with_duckdb() -> SemanticValidationReport:
    """Run SEMANTIC_ONLY DuckDB checks on synthetic fixtures.

    Does NOT prove BigQuery syntax, partition pruning, optimizer behavior,
    cost, latency, or clustering effectiveness.
    """
    errors: list[str] = []
    results: dict[str, Any] = {"validation_label": ValidationLabel.SEMANTIC_ONLY.value}

    conn = duckdb.connect(database=":memory:")
    try:
        _seed_semantic_fixture(conn)

        # Deleted works excluded from consumer aggregates.
        pub_trends = conn.execute(
            """
            SELECT publication_year, COUNT(*) AS work_count
            FROM works
            WHERE activity_state = 'ACTIVE'
            GROUP BY publication_year
            ORDER BY publication_year
            """
        ).fetchall()
        results["publication_trends"] = pub_trends
        # ACTIVE years: 2019(W4), 2020(W1,W3) — W2 DELETED excluded
        if pub_trends != [(2019, 1), (2020, 2)]:
            errors.append(f"publication trends unexpected: {pub_trends}")

        oa_trends = conn.execute(
            """
            SELECT publication_year, oa_status, COUNT(*) AS work_count
            FROM works
            WHERE activity_state = 'ACTIVE'
            GROUP BY publication_year, oa_status
            ORDER BY publication_year, oa_status
            """
        ).fetchall()
        results["oa_trends"] = oa_trends
        if (2020, "gold", 1) not in oa_trends or (2020, "green", 1) not in oa_trends:
            errors.append(f"oa trends missing expected buckets: {oa_trends}")
        if any(row[0] == 2021 for row in oa_trends):
            errors.append("deleted W2 year must not appear in OA trends")

        # Publisher-topic: W1 contributes T1,T2,T3; W4 contributes T1; W2 excluded.
        pub_topic = conn.execute(
            """
            SELECT primary_publisher_id, topic_id, COUNT(DISTINCT w.work_id)
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE' AND w.primary_publisher_id = 'P1'
            GROUP BY 1, 2
            ORDER BY 2
            """
        ).fetchall()
        results["publisher_topic"] = pub_topic
        expected_pt = [("P1", "T1", 2), ("P1", "T2", 1), ("P1", "T3", 1)]
        if pub_topic != expected_pt:
            errors.append(f"publisher-topic unexpected: {pub_topic}")

        # Primary-location license: W1 primary cc-by (not cc-by-nc); W3 NULL bucket.
        license_metric = conn.execute(
            """
            WITH primary_license AS (
              SELECT work_id, license AS primary_location_license
              FROM work_locations
              WHERE is_primary = TRUE
            )
            SELECT w.primary_publisher_id, wt.topic_id, pl.primary_location_license,
                   w.publication_year, COUNT(DISTINCT w.work_id)
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            LEFT JOIN primary_license pl ON pl.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE'
              AND w.primary_publisher_id = 'P1'
              AND w.publication_year = 2020
            GROUP BY 1, 2, 3, 4
            ORDER BY 2
            """
        ).fetchall()
        results["primary_license"] = license_metric
        licenses = {row[2] for row in license_metric}
        if "cc-by-nc" in licenses:
            errors.append("non-primary license must not appear in metric")
        if "cc-by" not in licenses:
            errors.append("primary cc-by license missing for W1")

        null_license = conn.execute(
            """
            WITH primary_license AS (
              SELECT work_id, license AS primary_location_license
              FROM work_locations WHERE is_primary = TRUE
            )
            SELECT pl.primary_location_license, COUNT(DISTINCT w.work_id)
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            LEFT JOIN primary_license pl ON pl.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE' AND w.work_id = 'W3'
            GROUP BY 1
            """
        ).fetchall()
        results["null_license_bucket"] = null_license
        if null_license != [(None, 1)]:
            errors.append(f"NULL primary license bucket expected: {null_license}")

        # Journal authors: S1 → W1,W4 (ACTIVE); distinct A1,A2.
        journal_authors = conn.execute(
            """
            WITH journal_works AS (
              SELECT DISTINCT wl.work_id
              FROM work_locations wl
              JOIN works w ON w.work_id = wl.work_id
              WHERE w.activity_state = 'ACTIVE' AND wl.source_id = 'S1'
            )
            SELECT COUNT(DISTINCT wa.author_id)
            FROM journal_works jw
            JOIN work_authors wa ON wa.work_id = jw.work_id
            WHERE wa.author_id IS NOT NULL
            """
        ).fetchone()
        results["journal_authors"] = journal_authors[0] if journal_authors else None
        if journal_authors is None or journal_authors[0] != 2:
            errors.append(f"journal unique authors expected 2, got {journal_authors}")

        # Institution-topic: I1 → DISTINCT W1,W3 then topics (not authorship fan-out).
        inst_topic = conn.execute(
            """
            WITH inst_works AS (
              SELECT DISTINCT wai.work_id
              FROM work_author_institutions wai
              JOIN works w ON w.work_id = wai.work_id
              WHERE w.activity_state = 'ACTIVE' AND wai.institution_id = 'I1'
            )
            SELECT topic_id, COUNT(DISTINCT iw.work_id)
            FROM inst_works iw
            JOIN work_topics wt ON wt.work_id = iw.work_id
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
        results["institution_topic"] = inst_topic
        # W1 has T1,T2,T3; W3 has T2 → T1:1, T2:2, T3:1
        if inst_topic != [("T1", 1), ("T2", 2), ("T3", 1)]:
            errors.append(f"institution-topic unexpected: {inst_topic}")

        # Fan-out: authors × topics for W1 without DISTINCT would be 2*3=6.
        unsafe = conn.execute(
            """
            SELECT COUNT(*) FROM work_authors wa
            JOIN work_topics wt ON wt.work_id = wa.work_id
            WHERE wa.work_id = 'W1'
            """
        ).fetchone()[0]
        safe_authors = conn.execute(
            """
            SELECT COUNT(DISTINCT author_id) FROM work_authors WHERE work_id = 'W1'
            """
        ).fetchone()[0]
        results["fanout_unsafe_rows"] = unsafe
        results["fanout_safe_authors"] = safe_authors
        if unsafe != 6:
            errors.append(f"expected unsafe fan-out 6, got {unsafe}")
        if safe_authors != 2:
            errors.append(f"expected safe author count 2, got {safe_authors}")

        # Citations: source ACTIVE; targets may be DELETED / absent.
        citations = conn.execute(
            """
            SELECT wr.work_id, wr.reference_index, wr.referenced_work_id,
                   tw.activity_state AS target_activity_state
            FROM work_references wr
            JOIN works sw ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
            LEFT JOIN works tw ON tw.work_id = wr.referenced_work_id
            WHERE wr.work_id = 'W1'
            ORDER BY wr.reference_index
            """
        ).fetchall()
        results["citations"] = citations
        if len(citations) != 3:
            errors.append(f"expected 3 citation rows, got {citations}")
        else:
            # W2 DELETED target retained; W999 absent; MISSING null target.
            if citations[0][2] != "W2" or citations[0][3] != "DELETED":
                errors.append(f"deleted target not preserved: {citations[0]}")
            if citations[1][2] != "W999" or citations[1][3] is not None:
                errors.append(f"unresolved target not preserved: {citations[1]}")
            if citations[2][2] is not None:
                errors.append(f"missing reference should keep null target: {citations[2]}")

        # Deleted source work W2 must not contribute consumer citation listing.
        deleted_source_citations = conn.execute(
            """
            SELECT COUNT(*)
            FROM work_references wr
            JOIN works sw ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
            WHERE wr.work_id = 'W2'
            """
        ).fetchone()[0]
        results["deleted_source_citations"] = deleted_source_citations
        if deleted_source_citations != 0:
            errors.append("DELETED source work must not appear in consumer citations")

    finally:
        conn.close()

    return SemanticValidationReport(
        label=ValidationLabel.SEMANTIC_ONLY,
        results=results,
        errors=tuple(errors),
    )


def validate_stale_relationship_publication_guard() -> SemanticValidationReport:
    """SEMANTIC_ONLY: stale staging must not replace newer Work relationships.

    Target W1 at T3 with topics [T1, T2]; incoming stale W1 at T2 with [T9].
    Expected: Work stays T3; relationships stay [T1, T2]; T9 never published.
    """
    from datetime import date

    from research_platform.analytics.bigquery.contracts import (
        PublicationDecision,
        decide_analytical_works_merge,
        is_accepted_publication_decision,
    )

    errors: list[str] = []
    results: dict[str, Any] = {"validation_label": ValidationLabel.SEMANTIC_ONLY.value}

    decision = decide_analytical_works_merge(
        target_exists=True,
        target_checksum="cc" * 32,
        target_lineage_date=date(2024, 3, 1),
        target_activity_state="ACTIVE",
        source_checksum="bb" * 32,
        source_lineage_date=date(2024, 2, 1),
        source_activity_state="ACTIVE",
    )
    results["stale_work_decision"] = decision.value
    if decision is not PublicationDecision.STALE:
        errors.append(f"expected STALE for older staging, got {decision}")
    if is_accepted_publication_decision(decision):
        errors.append("STALE must not be an accepted publication decision")

    # Simulate relationship REPLACE scoped to accepted_work_ids only.
    current_topics = {"W1": ("T1", "T2")}
    staging_topics = {"W1": ("T9",)}
    accepted: set[str] = set()
    if is_accepted_publication_decision(decision):
        accepted.add("W1")
    if "W1" in accepted:
        current_topics["W1"] = staging_topics["W1"]
    results["topics_after"] = current_topics["W1"]
    if current_topics["W1"] != ("T1", "T2"):
        errors.append(
            f"stale topics must not publish; got {current_topics['W1']}"
        )
    if "T9" in current_topics["W1"]:
        errors.append("T9 must never be published for rejected stale Work")

    return SemanticValidationReport(
        label=ValidationLabel.SEMANTIC_ONLY,
        results=results,
        errors=tuple(errors),
    )


def assert_contracts_valid() -> BigQueryAnalyticalRegistry:
    """Load registry and fail fast on static contract errors."""
    registry = load_bigquery_analytical_registry()
    report = validate_static_contracts(registry)
    if not report.ok:
        joined = "; ".join(report.errors)
        raise AssertionError(f"BigQuery static contract validation failed: {joined}")
    return registry
