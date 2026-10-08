"""Reusable DuckDB SEMANTIC_ONLY Gold mart builders (Step 16 / Step 19).

These queries mirror the Step-16 Gold fan-out-safe grains. They prove local
semantics only — never BigQuery runtime, cost, or latency.
"""

from __future__ import annotations

from typing import Any

import duckdb

from research_platform.analytics.gold.registry import load_gold_registry

EVIDENCE_LABEL = "SEMANTIC_ONLY"

# SQL used by both Step-16 semantic validation and Step-19 staged Gold build.
GOLD_SQL: dict[str, str] = {
    "research_discovery": """
        CREATE OR REPLACE TABLE gold_research_discovery AS
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
        SELECT
          aw.work_id,
          aw.doi,
          aw.title,
          aw.publication_year,
          aw.publication_date,
          aw.work_type,
          aw.language,
          aw.is_oa,
          aw.oa_status,
          aw.primary_source_id,
          aw.primary_publisher_id,
          COALESCE(aa.author_ids, CAST([] AS VARCHAR[])) AS author_ids,
          COALESCE(ta.topic_ids, CAST([] AS VARCHAR[])) AS topic_ids,
          COALESCE(ia.institution_ids, CAST([] AS VARCHAR[])) AS institution_ids
        FROM active_works aw
        LEFT JOIN authors_agg aa ON aa.work_id = aw.work_id
        LEFT JOIN topics_agg ta ON ta.work_id = aw.work_id
        LEFT JOIN institutions_agg ia ON ia.work_id = aw.work_id
    """,
    "journal_author_stats": """
        CREATE OR REPLACE TABLE gold_journal_author_stats AS
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
        SELECT wc.source_id,
               COALESCE(ac.unique_author_count, 0) AS unique_author_count,
               wc.active_work_count
        FROM work_counts wc
        LEFT JOIN author_counts ac ON ac.source_id = wc.source_id
    """,
    "publisher_author_stats": """
        CREATE OR REPLACE TABLE gold_publisher_author_stats AS
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
        SELECT wc.publisher_id,
               COALESCE(ac.unique_author_count, 0) AS unique_author_count,
               wc.active_work_count
        FROM work_counts wc
        LEFT JOIN author_counts ac ON ac.publisher_id = wc.publisher_id
    """,
    "publisher_topic_year_stats": """
        CREATE OR REPLACE TABLE gold_publisher_topic_year_stats AS
        SELECT w.primary_publisher_id AS publisher_id,
               wt.topic_id,
               w.publication_year,
               COUNT(DISTINCT w.work_id) AS active_work_count
        FROM works w
        JOIN work_topics wt ON wt.work_id = w.work_id
        WHERE w.activity_state = 'ACTIVE'
          AND w.primary_publisher_id IS NOT NULL
          AND wt.topic_id IS NOT NULL
        GROUP BY 1, 2, 3
    """,
    "publisher_topic_license_year_stats": """
        CREATE OR REPLACE TABLE gold_publisher_topic_license_year_stats AS
        WITH primary_license AS (
          SELECT work_id, license AS primary_location_license
          FROM work_locations WHERE is_primary = TRUE
        )
        SELECT w.primary_publisher_id AS publisher_id,
               wt.topic_id,
               pl.primary_location_license,
               w.publication_year,
               COUNT(DISTINCT w.work_id) AS active_work_count
        FROM works w
        JOIN work_topics wt ON wt.work_id = w.work_id
        LEFT JOIN primary_license pl ON pl.work_id = w.work_id
        WHERE w.activity_state = 'ACTIVE'
          AND w.primary_publisher_id IS NOT NULL
          AND wt.topic_id IS NOT NULL
        GROUP BY 1, 2, 3, 4
    """,
    "institution_topic_stats": """
        CREATE OR REPLACE TABLE gold_institution_topic_stats AS
        WITH institution_works AS (
          SELECT DISTINCT wai.institution_id, wai.work_id
          FROM work_author_institutions wai
          JOIN works w ON w.work_id = wai.work_id
          WHERE w.activity_state = 'ACTIVE' AND wai.institution_id IS NOT NULL
        )
        SELECT iw.institution_id,
               wt.topic_id,
               COUNT(DISTINCT iw.work_id) AS active_work_count
        FROM institution_works iw
        JOIN work_topics wt ON wt.work_id = iw.work_id
        WHERE wt.topic_id IS NOT NULL
        GROUP BY 1, 2
    """,
    "publication_trends": """
        CREATE OR REPLACE TABLE gold_publication_trends AS
        SELECT publication_year, COUNT(*) AS active_work_count
        FROM works WHERE activity_state = 'ACTIVE'
        GROUP BY publication_year
    """,
    "open_access_trends": """
        CREATE OR REPLACE TABLE gold_open_access_trends AS
        SELECT publication_year, oa_status, COUNT(*) AS active_work_count
        FROM works WHERE activity_state = 'ACTIVE'
        GROUP BY 1, 2
    """,
    "citation_edges": """
        CREATE OR REPLACE TABLE gold_citation_edges AS
        SELECT wr.work_id AS source_work_id,
               wr.reference_index,
               wr.referenced_work_id,
               wr.reference_status,
               tw.activity_state AS target_activity_state
        FROM work_references wr
        JOIN works sw ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
        LEFT JOIN works tw ON tw.work_id = wr.referenced_work_id
    """,
}


def build_all_gold_marts(conn: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    """Build all registry Gold marts into ``gold_<mart_id>`` tables."""
    mart_ids = tuple(m.mart_id for m in load_gold_registry())
    for mart_id in mart_ids:
        sql = GOLD_SQL.get(mart_id)
        if sql is None:
            raise ValueError(f"missing SEMANTIC_ONLY Gold SQL for {mart_id}")
        conn.execute(sql)
    return mart_ids


def build_staged_gold_audit_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Populate staged Gold contribution/manifest tables from built marts.

    Manifest derives exactly from ``load_gold_registry()``.
    Contribution rows include only works that actually contribute to each mart —
    not every ACTIVE Work in every mart.
    """
    mart_ids = [m.mart_id for m in load_gold_registry()]
    conn.execute("CREATE OR REPLACE TABLE staged_gold_mart_manifest (mart_id VARCHAR)")
    for mid in mart_ids:
        conn.execute(
            "INSERT INTO staged_gold_mart_manifest VALUES (?)", [mid]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE staged_gold_work_contributions (
          mart_id VARCHAR,
          work_id VARCHAR
        )
        """
    )

    # research_discovery / publication_trends / open_access_trends: all ACTIVE works
    for mid in (
        "research_discovery",
        "publication_trends",
        "open_access_trends",
    ):
        conn.execute(
            f"""
            INSERT INTO staged_gold_work_contributions
            SELECT '{mid}', work_id FROM works WHERE activity_state = 'ACTIVE'
            """
        )

    # journal: ACTIVE works with a non-null location source_id
    conn.execute(
        """
        INSERT INTO staged_gold_work_contributions
        SELECT DISTINCT 'journal_author_stats', wl.work_id
        FROM work_locations wl
        JOIN works w ON w.work_id = wl.work_id
        WHERE w.activity_state = 'ACTIVE' AND wl.source_id IS NOT NULL
        """
    )

    # publisher author stats
    conn.execute(
        """
        INSERT INTO staged_gold_work_contributions
        SELECT 'publisher_author_stats', work_id
        FROM works
        WHERE activity_state = 'ACTIVE' AND primary_publisher_id IS NOT NULL
        """
    )

    # publisher topic year / license year
    for mid in ("publisher_topic_year_stats", "publisher_topic_license_year_stats"):
        conn.execute(
            f"""
            INSERT INTO staged_gold_work_contributions
            SELECT DISTINCT '{mid}', w.work_id
            FROM works w
            JOIN work_topics wt ON wt.work_id = w.work_id
            WHERE w.activity_state = 'ACTIVE'
              AND w.primary_publisher_id IS NOT NULL
              AND wt.topic_id IS NOT NULL
            """
        )

    # institution topic
    conn.execute(
        """
        INSERT INTO staged_gold_work_contributions
        SELECT DISTINCT 'institution_topic_stats', w.work_id
        FROM works w
        JOIN work_author_institutions wai ON wai.work_id = w.work_id
        JOIN work_topics wt ON wt.work_id = w.work_id
        WHERE w.activity_state = 'ACTIVE'
          AND wai.institution_id IS NOT NULL
          AND wt.topic_id IS NOT NULL
        """
    )

    # citation_edges: ACTIVE sources that have reference rows
    conn.execute(
        """
        INSERT INTO staged_gold_work_contributions
        SELECT DISTINCT 'citation_edges', wr.work_id
        FROM work_references wr
        JOIN works w ON w.work_id = wr.work_id
        WHERE w.activity_state = 'ACTIVE'
        """
    )

    conn.execute(
        """
        CREATE OR REPLACE TABLE staged_gold_citation_edges AS
        SELECT source_work_id, reference_index, referenced_work_id, target_activity_state
        FROM gold_citation_edges
        """
    )


def fetch_gold_rows(
    conn: duckdb.DuckDBPyConnection, mart_id: str
) -> list[tuple[Any, ...]]:
    return list(conn.execute(f"SELECT * FROM gold_{mart_id}").fetchall())
