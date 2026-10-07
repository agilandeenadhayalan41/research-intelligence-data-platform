"""Static Gold contract checks and DuckDB SEMANTIC_ONLY validation.

DuckDB fixtures prove relationship / activity semantics only — never BigQuery
syntax, partition pruning, optimizer behavior, cost, or latency.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from research_platform.analytics.bigquery.contracts import ValidationLabel
from research_platform.analytics.bigquery.registry import (
    load_bigquery_analytical_registry,
)
from research_platform.analytics.gold.contracts import (
    MATERIALIZATION_PROMOTION_RULE,
    EvidenceStatus,
    MaterializationMode,
)
from research_platform.analytics.gold.registry import (
    gold_sql_path,
    load_gold_registry,
    serialize_gold_registry,
)
from research_platform.benchmarks.registry import load_query_pattern_registry

_REPO_ROOT = Path(__file__).resolve().parents[4]

_MIN_MAX_LICENSE = re.compile(
    r"\b(MIN|MAX)\s*\(\s*`?license`?\s*\)", re.IGNORECASE
)
_ANY_VALUE_LICENSE = re.compile(
    r"\bANY_VALUE\s*\(\s*`?license`?\s*\)", re.IGNORECASE
)
_SELECT_STAR = re.compile(r"\bSELECT\s+\*\b", re.IGNORECASE)
_ACTIVE_PREDICATE = re.compile(
    r"`?activity_state`?\s*=\s*'ACTIVE'", re.IGNORECASE
)
_SQL_LINE_COMMENT = re.compile(r"--.*?$", re.MULTILINE)
_FORBIDDEN_ISSN = re.compile(r"\b(issn|eissn)\b", re.IGNORECASE)

# Fan-out static guards (not a full SQL parser).
_RESEARCH_DISCOVERY_CTES = (
    "authors_agg",
    "topics_agg",
    "institutions_agg",
)


def _strip_sql_comments(text: str) -> str:
    return _SQL_LINE_COMMENT.sub("", text)


@dataclass(frozen=True)
class StaticValidationReport:
    label: ValidationLabel
    errors: tuple[str, ...]
    checks_passed: int

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class SemanticValidationReport:
    label: ValidationLabel
    results: dict[str, Any]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_static_gold_contracts(
    *,
    repo_root: Path | None = None,
) -> StaticValidationReport:
    """Structural checks over Gold contracts and BigQuery SQL files (no GCP)."""
    root = repo_root or _REPO_ROOT
    marts = load_gold_registry()
    bq = load_bigquery_analytical_registry()
    bq_tables = {t.table_name for t in bq.tables}
    pattern_ids = {p.pattern_id for p in load_query_pattern_registry().patterns}

    errors: list[str] = []
    passed = 0

    def ok(condition: bool, message: str) -> None:
        nonlocal passed
        if condition:
            passed += 1
        else:
            errors.append(message)

    mart_ids = [m.mart_id for m in marts]
    ok(len(mart_ids) == len(set(mart_ids)), "duplicate mart_id values")
    ok(len(MATERIALIZATION_PROMOTION_RULE) > 20, "promotion rule missing")

    serialized_a = json.dumps(serialize_gold_registry(marts), sort_keys=True)
    serialized_b = json.dumps(serialize_gold_registry(marts), sort_keys=True)
    ok(serialized_a == serialized_b, "registry serialization not deterministic")

    for mart in marts:
        ok(bool(mart.result_grain.strip()), f"{mart.mart_id}: empty result_grain")
        ok(bool(mart.grain_keys), f"{mart.mart_id}: empty grain_keys")
        ok(mart.active_work_required is True, f"{mart.mart_id}: ACTIVE required")

        for pid in mart.source_pattern_ids:
            ok(pid in pattern_ids, f"{mart.mart_id}: unknown pattern_id {pid}")

        for table in mart.input_tables:
            ok(table in bq_tables, f"{mart.mart_id}: unknown input table {table}")

        sql_file = gold_sql_path(mart, repo_root=root)
        ok(sql_file.is_file(), f"{mart.mart_id}: missing SQL {mart.sql_path}")
        if not sql_file.is_file():
            continue

        text = sql_file.read_text(encoding="utf-8")
        stripped = _strip_sql_comments(text)
        ok(not _SELECT_STAR.search(stripped), f"{mart.mart_id}: SELECT * forbidden")
        ok(
            not _MIN_MAX_LICENSE.search(stripped),
            f"{mart.mart_id}: MIN/MAX(license) forbidden",
        )
        ok(
            not _ANY_VALUE_LICENSE.search(stripped),
            f"{mart.mart_id}: ANY_VALUE(license) forbidden",
        )
        ok(
            not _FORBIDDEN_ISSN.search(stripped),
            f"{mart.mart_id}: issn/eissn must not appear",
        )
        ok(
            _ACTIVE_PREDICATE.search(stripped) is not None,
            f"{mart.mart_id}: missing activity_state = 'ACTIVE'",
        )
        ok("`" in text, f"{mart.mart_id}: expected BigQuery backtick identifiers")

        if mart.materialization_mode is MaterializationMode.MATERIALIZED:
            ok(
                mart.evidence_status is EvidenceStatus.MEASURED,
                f"{mart.mart_id}: MATERIALIZED without MEASURED evidence",
            )
        else:
            ok(
                mart.materialization_mode
                in (
                    MaterializationMode.COMPUTE_ON_READ,
                    MaterializationMode.MATERIALIZATION_CANDIDATE,
                ),
                f"{mart.mart_id}: unexpected mode {mart.materialization_mode}",
            )

        if mart.materialization_mode is MaterializationMode.MATERIALIZATION_CANDIDATE:
            ok(
                bool(mart.future_measurement_required.strip()),
                f"{mart.mart_id}: candidate missing future_measurement_required",
            )

        # Fan-out static guards (not a full SQL parser).
        if mart.mart_id == "research_discovery":
            lower = stripped.lower()
            for cte in _RESEARCH_DISCOVERY_CTES:
                ok(cte in lower, f"research_discovery: missing independent CTE {cte}")

        if mart.mart_id in ("journal_author_stats", "institution_topic_stats"):
            ok(
                "select distinct" in stripped.lower(),
                f"{mart.mart_id}: expected DISTINCT work-grain pre-aggregation",
            )

        if mart.mart_id == "publisher_topic_license_year_stats":
            ok(
                "is_primary" in stripped.lower(),
                "publisher_topic_license_year_stats: must filter is_primary",
            )

    # No falsely MATERIALIZED marts in the current registry
    materialized = [
        m.mart_id
        for m in marts
        if m.materialization_mode is MaterializationMode.MATERIALIZED
    ]
    ok(not materialized, f"unsupported MATERIALIZED marts: {materialized}")

    return StaticValidationReport(
        label=ValidationLabel.BIGQUERY_SQL_CONTRACT,
        errors=tuple(errors),
        checks_passed=passed,
    )


def _seed_gold_semantic_fixture(conn: duckdb.DuckDBPyConnection) -> None:
    """Deterministic fixture with fan-out, deletion, NULL license, citation gaps."""
    conn.execute(
        """
        CREATE TABLE works AS SELECT * FROM (
          VALUES
            -- W1: multi author/topic/institution/location; primary license cc-by
            ('W1', '10.1/aaa', 'Alpha', 2020, DATE '2020-01-15', 'article', 'en',
             TRUE, 'gold', 'S1', 'P1', 'ACTIVE'),
            -- W2: DELETED; relationships retained physically
            ('W2', '10.1/bbb', 'Beta', 2021, DATE '2021-06-01', 'article', 'en',
             FALSE, 'closed', 'S1', 'P1', 'DELETED'),
            -- W3: NULL primary license; NULL DOI; green OA
            ('W3', CAST(NULL AS VARCHAR), 'Gamma', 2020, DATE '2020-03-01', 'article',
             'en', TRUE, 'green', 'S2', 'P2', 'ACTIVE'),
            -- W4: closed OA; same publisher/source as W1
            ('W4', '10.1/ccc', 'Delta', 2019, DATE '2019-01-01', 'article', 'en',
             FALSE, 'closed', 'S1', 'P1', 'ACTIVE'),
            -- W5: NULL oa_status (unknown — do not fabricate closed)
            ('W5', '10.1/ddd', CAST(NULL AS VARCHAR), CAST(NULL AS INTEGER),
             CAST(NULL AS DATE), 'article', 'en', CAST(NULL AS BOOLEAN),
             CAST(NULL AS VARCHAR), 'S3', 'P3', 'ACTIVE')
        ) AS t(
          work_id, doi, title, publication_year, publication_date, work_type,
          language, is_oa, oa_status, primary_source_id, primary_publisher_id,
          activity_state
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE work_authors AS SELECT * FROM (
          VALUES
            ('W1', 0, 'A1'),
            ('W1', 1, 'A2'),
            ('W1', 2, 'A1'),  -- repeated same author
            ('W2', 0, 'A1'),
            ('W3', 0, 'A2'),
            ('W3', 1, 'A3'),
            ('W4', 0, 'A1'),
            ('W5', 0, CAST(NULL AS VARCHAR))
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
            ('W4', 'T1'),
            ('W5', 'T9')
        ) AS t(work_id, topic_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_locations AS SELECT * FROM (
          VALUES
            ('W1', 0, 'S1', 'cc-by', TRUE),
            ('W1', 1, 'S9', 'cc-by-nc', FALSE),
            ('W1', 2, 'S1', 'other', FALSE),  -- multi-location same source
            ('W3', 0, 'S2', CAST(NULL AS VARCHAR), TRUE),
            ('W4', 0, 'S1', 'cc-by', TRUE),
            ('W5', 0, 'S3', 'cc0', TRUE),
            ('W2', 0, 'S1', 'cc-by', TRUE)
        ) AS t(work_id, location_index, source_id, license, is_primary)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_author_institutions AS SELECT * FROM (
          VALUES
            ('W1', 0, 0, 'I1'),
            ('W1', 1, 0, 'I1'),  -- same institution via different authorship
            ('W1', 1, 1, 'I2'),
            ('W3', 0, 0, 'I1'),
            ('W2', 0, 0, 'I1'),
            ('W4', 0, 0, 'I2')
        ) AS t(work_id, authorship_index, institution_index, institution_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE work_references AS SELECT * FROM (
          VALUES
            ('W1', 0, 'W2', 'RESOLVED_ID'),      -- DELETED target
            ('W1', 1, 'W999', 'RESOLVED_ID'),    -- unresolved target
            ('W1', 2, CAST(NULL AS VARCHAR), 'MISSING'),
            ('W2', 0, 'W3', 'RESOLVED_ID'),      -- DELETED source (excluded)
            ('W4', 0, 'W1', 'RESOLVED_ID')       -- ACTIVE → ACTIVE
        ) AS t(work_id, reference_index, referenced_work_id, reference_status)
        """
    )


def validate_gold_semantics_with_duckdb() -> SemanticValidationReport:
    """Run SEMANTIC_ONLY DuckDB checks on Gold mart logic.

    Label: SEMANTIC_ONLY — does NOT prove BigQuery compatibility or cost.
    """
    errors: list[str] = []
    results: dict[str, Any] = {
        "validation_label": ValidationLabel.SEMANTIC_ONLY.value,
        "evidence_note": (
            "DuckDB SEMANTIC_ONLY fixtures; BigQuery cost/latency not measured"
        ),
    }

    conn = duckdb.connect(database=":memory:")
    try:
        _seed_gold_semantic_fixture(conn)

        # --- research_discovery ---
        discovery = conn.execute(
            """
            WITH active_works AS (
              SELECT * FROM works WHERE activity_state = 'ACTIVE'
            ),
            authors_agg AS (
              SELECT work_id,
                     list_sort(list_distinct(list(author_id))) AS author_ids
              FROM work_authors
              WHERE author_id IS NOT NULL
                AND work_id IN (SELECT work_id FROM active_works)
              GROUP BY work_id
            ),
            topics_agg AS (
              SELECT work_id,
                     list_sort(list_distinct(list(topic_id))) AS topic_ids
              FROM work_topics
              WHERE topic_id IS NOT NULL
                AND work_id IN (SELECT work_id FROM active_works)
              GROUP BY work_id
            ),
            institutions_agg AS (
              SELECT work_id,
                     list_sort(list_distinct(list(institution_id))) AS institution_ids
              FROM work_author_institutions
              WHERE institution_id IS NOT NULL
                AND work_id IN (SELECT work_id FROM active_works)
              GROUP BY work_id
            )
            SELECT aw.work_id, aa.author_ids, ta.topic_ids, ia.institution_ids
            FROM active_works aw
            LEFT JOIN authors_agg aa ON aa.work_id = aw.work_id
            LEFT JOIN topics_agg ta ON ta.work_id = aw.work_id
            LEFT JOIN institutions_agg ia ON ia.work_id = aw.work_id
            ORDER BY aw.work_id
            """
        ).fetchall()
        results["research_discovery"] = discovery
        work_ids = [r[0] for r in discovery]
        if work_ids != ["W1", "W3", "W4", "W5"]:
            errors.append(f"discovery ACTIVE works unexpected: {work_ids}")
        if "W2" in work_ids:
            errors.append("deleted W2 must be excluded from research_discovery")
        w1 = next(r for r in discovery if r[0] == "W1")
        if list(w1[1]) != ["A1", "A2"]:
            errors.append(f"W1 author_ids not independently deduped: {w1[1]}")
        if list(w1[2]) != ["T1", "T2", "T3"]:
            errors.append(f"W1 topic_ids unexpected: {w1[2]}")
        if list(w1[3]) != ["I1", "I2"]:
            errors.append(f"W1 institution_ids unexpected: {w1[3]}")

        # Unsafe Cartesian inflation vs safe independent agg
        unsafe = conn.execute(
            """
            SELECT COUNT(*) FROM works w
            JOIN work_authors wa ON wa.work_id = w.work_id
            JOIN work_topics wt ON wt.work_id = w.work_id
            JOIN work_author_institutions wai ON wai.work_id = w.work_id
            JOIN work_locations wl ON wl.work_id = w.work_id
            WHERE w.work_id = 'W1'
            """
        ).fetchone()[0]
        results["unsafe_w1_cartesian"] = unsafe
        # authors(3) × topics(3) × institutions(3) × locations(3) = 81
        if unsafe <= 3:
            errors.append(f"fixture fan-out too weak for inflation test: {unsafe}")
        safe_rows = len([r for r in discovery if r[0] == "W1"])
        if safe_rows != 1:
            errors.append("safe discovery must remain one row per work")
        results["safe_vs_unsafe"] = {"safe_rows": safe_rows, "unsafe_join_rows": unsafe}

        # --- journal_author_stats ---
        journal = conn.execute(
            """
            WITH source_works AS (
              SELECT DISTINCT wl.source_id, wl.work_id
              FROM work_locations wl
              JOIN works w ON w.work_id = wl.work_id
              WHERE w.activity_state = 'ACTIVE' AND wl.source_id IS NOT NULL
            ),
            work_counts AS (
              SELECT source_id, COUNT(*) AS active_work_count
              FROM source_works GROUP BY source_id
            ),
            author_counts AS (
              SELECT sw.source_id, COUNT(DISTINCT wa.author_id) AS unique_author_count
              FROM source_works sw
              JOIN work_authors wa ON wa.work_id = sw.work_id
              WHERE wa.author_id IS NOT NULL
              GROUP BY sw.source_id
            )
            SELECT wc.source_id, COALESCE(ac.unique_author_count, 0), wc.active_work_count
            FROM work_counts wc
            LEFT JOIN author_counts ac ON ac.source_id = wc.source_id
            ORDER BY wc.source_id
            """
        ).fetchall()
        results["journal_author_stats"] = journal
        s1 = next(r for r in journal if r[0] == "S1")
        # S1 ACTIVE works: W1, W4 (W2 deleted). Authors A1, A2. Multi-location no inflate.
        if s1[1] != 2 or s1[2] != 2:
            errors.append(f"S1 journal stats unexpected: {s1}")

        # --- publisher_author_stats ---
        pub_auth = conn.execute(
            """
            WITH publisher_works AS (
              SELECT work_id, primary_publisher_id AS publisher_id
              FROM works
              WHERE activity_state = 'ACTIVE' AND primary_publisher_id IS NOT NULL
            ),
            work_counts AS (
              SELECT publisher_id, COUNT(*) AS active_work_count
              FROM publisher_works GROUP BY publisher_id
            ),
            author_counts AS (
              SELECT pw.publisher_id, COUNT(DISTINCT wa.author_id) AS unique_author_count
              FROM publisher_works pw
              JOIN work_authors wa ON wa.work_id = pw.work_id
              WHERE wa.author_id IS NOT NULL
              GROUP BY pw.publisher_id
            )
            SELECT wc.publisher_id, COALESCE(ac.unique_author_count, 0), wc.active_work_count
            FROM work_counts wc
            LEFT JOIN author_counts ac ON ac.publisher_id = wc.publisher_id
            ORDER BY wc.publisher_id
            """
        ).fetchall()
        results["publisher_author_stats"] = pub_auth
        p1 = next(r for r in pub_auth if r[0] == "P1")
        # P1: W1+W4 → authors A1,A2; works 2
        if p1 != ("P1", 2, 2):
            errors.append(f"P1 publisher_author_stats unexpected: {p1}")

        # --- publisher_topic_year_stats ---
        pty = conn.execute(
            """
            SELECT primary_publisher_id AS publisher_id, topic_id, publication_year,
                   COUNT(DISTINCT w.work_id)
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE' AND w.primary_publisher_id = 'P1'
            GROUP BY 1, 2, 3
            ORDER BY 2, 3
            """
        ).fetchall()
        results["publisher_topic_year_stats"] = pty
        # W1(2020): T1,T2,T3; W4(2019): T1 — no author fan-out
        if ("P1", "T1", 2020, 1) not in pty or ("P1", "T1", 2019, 1) not in pty:
            errors.append(f"publisher_topic_year unexpected: {pty}")
        if any(r[3] > 1 and r[1] == "T2" for r in pty):
            errors.append("T2 should not be inflated by authors")

        # --- publisher_topic_license_year ---
        ptl = conn.execute(
            """
            WITH primary_license AS (
              SELECT work_id, license AS primary_location_license
              FROM work_locations WHERE is_primary = TRUE
            )
            SELECT w.primary_publisher_id, wt.topic_id, pl.primary_location_license,
                   w.publication_year, COUNT(DISTINCT w.work_id)
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            LEFT JOIN primary_license pl ON pl.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE'
            GROUP BY 1, 2, 3, 4
            """
        ).fetchall()
        results["publisher_topic_license_year_stats"] = ptl
        licenses = {r[2] for r in ptl}
        if "cc-by-nc" in licenses:
            errors.append("non-primary license leaked into Gold license mart")
        null_bucket = [r for r in ptl if r[0] == "P2" and r[2] is None]
        if not null_bucket:
            errors.append("NULL primary license bucket missing for W3/P2")

        # --- institution_topic_stats ---
        it = conn.execute(
            """
            WITH institution_works AS (
              SELECT DISTINCT wai.institution_id, wai.work_id
              FROM work_author_institutions wai
              JOIN works w ON w.work_id = wai.work_id
              WHERE w.activity_state = 'ACTIVE' AND wai.institution_id IS NOT NULL
            )
            SELECT iw.institution_id, wt.topic_id, COUNT(DISTINCT iw.work_id)
            FROM institution_works iw
            JOIN work_topics wt ON wt.work_id = iw.work_id
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ).fetchall()
        results["institution_topic_stats"] = it
        # I1 × T1: W1 only (not multiplied by dual I1 authorships); W2 deleted
        i1_t1 = next((r for r in it if r[0] == "I1" and r[1] == "T1"), None)
        if i1_t1 != ("I1", "T1", 1):
            errors.append(f"I1/T1 must be 1 work after DISTINCT: {i1_t1}")

        # --- publication_trends ---
        trends = conn.execute(
            """
            SELECT publication_year, COUNT(*)
            FROM works WHERE activity_state = 'ACTIVE'
            GROUP BY publication_year
            ORDER BY publication_year NULLS LAST
            """
        ).fetchall()
        results["publication_trends"] = trends
        if any(r[0] == 2021 for r in trends):
            errors.append("deleted W2 year must not appear in publication_trends")
        if (None, 1) not in trends:
            errors.append("NULL publication_year bucket missing for W5")

        # --- open_access_trends ---
        oa = conn.execute(
            """
            SELECT publication_year, oa_status, COUNT(*)
            FROM works WHERE activity_state = 'ACTIVE'
            GROUP BY 1, 2
            ORDER BY 1 NULLS LAST, 2 NULLS LAST
            """
        ).fetchall()
        results["open_access_trends"] = oa
        if (None, None, 1) not in oa:
            errors.append("NULL oa_status / NULL year bucket missing")
        if any(r[1] == "closed" and r[0] is None for r in oa):
            errors.append("must not fabricate closed for unknown OA")

        # --- citation_edges ---
        cites = conn.execute(
            """
            SELECT wr.work_id AS source_work_id, wr.reference_index,
                   wr.referenced_work_id, wr.reference_status,
                   tw.activity_state AS target_activity_state
            FROM work_references wr
            JOIN works sw ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
            LEFT JOIN works tw ON tw.work_id = wr.referenced_work_id
            ORDER BY 1, 2
            """
        ).fetchall()
        results["citation_edges"] = cites
        sources = {r[0] for r in cites}
        if "W2" in sources:
            errors.append("deleted source W2 must be excluded from citation_edges")
        w1_cites = [r for r in cites if r[0] == "W1"]
        if len(w1_cites) != 3:
            errors.append(f"W1 must retain 3 citation edges incl unresolved: {w1_cites}")
        deleted_target = next(r for r in w1_cites if r[2] == "W2")
        if deleted_target[4] != "DELETED":
            errors.append("DELETED target activity_state missing")
        unresolved = next(r for r in w1_cites if r[2] == "W999")
        if unresolved[4] is not None:
            errors.append("unresolved target must LEFT JOIN to NULL activity_state")

    finally:
        conn.close()

    return SemanticValidationReport(
        label=ValidationLabel.SEMANTIC_ONLY,
        results=results,
        errors=tuple(errors),
    )
