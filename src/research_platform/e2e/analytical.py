"""Local DuckDB SEMANTIC_ONLY analytical projection from canonical snapshot."""

from __future__ import annotations

import duckdb

from research_platform.canonical.memory import CanonicalSnapshot
from research_platform.e2e.models import EVIDENCE_LABEL


def open_analytical_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def project_canonical_to_duckdb(
    conn: duckdb.DuckDBPyConnection,
    snapshot: CanonicalSnapshot,
) -> dict[str, int]:
    """Load Step-15 analytical table names from an immutable canonical snapshot.

    Evidence: SEMANTIC_ONLY — not BigQuery runtime.
    """
    _ = EVIDENCE_LABEL
    conn.execute(
        """
        CREATE OR REPLACE TABLE works (
          work_id VARCHAR,
          doi VARCHAR,
          title VARCHAR,
          publication_year INTEGER,
          publication_date DATE,
          work_type VARCHAR,
          language VARCHAR,
          is_oa BOOLEAN,
          oa_status VARCHAR,
          primary_source_id VARCHAR,
          primary_publisher_id VARCHAR,
          activity_state VARCHAR
        )
        """
    )
    for w in snapshot.works:
        conn.execute(
            """
            INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                w.work_id,
                w.doi,
                w.title,
                w.publication_year,
                w.publication_date,
                w.work_type,
                w.language,
                w.is_oa,
                w.oa_status,
                w.primary_source_id,
                w.primary_publisher_id,
                w.lineage.activity_state.value,
            ],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE authors (
          author_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for a in snapshot.authors:
        conn.execute(
            "INSERT INTO authors VALUES (?, ?)", [a.author_id, a.display_name]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE institutions (
          institution_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for i in snapshot.institutions:
        conn.execute(
            "INSERT INTO institutions VALUES (?, ?)",
            [i.institution_id, i.display_name],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE sources (
          source_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for s in snapshot.sources:
        conn.execute(
            "INSERT INTO sources VALUES (?, ?)", [s.source_id, s.display_name]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE publishers (
          publisher_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for p in snapshot.publishers:
        conn.execute(
            "INSERT INTO publishers VALUES (?, ?)",
            [p.publisher_id, p.display_name],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE topics (
          topic_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for t in snapshot.topics:
        conn.execute(
            "INSERT INTO topics VALUES (?, ?)", [t.topic_id, t.display_name]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE funders (
          funder_id VARCHAR, display_name VARCHAR
        )
        """
    )
    for f in snapshot.funders:
        conn.execute(
            "INSERT INTO funders VALUES (?, ?)", [f.funder_id, f.display_name]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_authors (
          work_id VARCHAR, authorship_index INTEGER, author_id VARCHAR
        )
        """
    )
    for r in snapshot.work_authors:
        conn.execute(
            "INSERT INTO work_authors VALUES (?, ?, ?)",
            [r.work_id, r.authorship_index, r.author_id],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_author_institutions (
          work_id VARCHAR, authorship_index INTEGER,
          institution_index INTEGER, institution_id VARCHAR
        )
        """
    )
    for r in snapshot.work_author_institutions:
        conn.execute(
            "INSERT INTO work_author_institutions VALUES (?, ?, ?, ?)",
            [r.work_id, r.authorship_index, r.institution_index, r.institution_id],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_topics (
          work_id VARCHAR, topic_id VARCHAR
        )
        """
    )
    for r in snapshot.work_topics:
        conn.execute(
            "INSERT INTO work_topics VALUES (?, ?)", [r.work_id, r.topic_id]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_keywords (
          work_id VARCHAR, keyword_id VARCHAR
        )
        """
    )
    for r in snapshot.work_keywords:
        conn.execute(
            "INSERT INTO work_keywords VALUES (?, ?)", [r.work_id, r.keyword_id]
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_references (
          work_id VARCHAR, reference_index INTEGER,
          referenced_work_id VARCHAR, reference_status VARCHAR
        )
        """
    )
    for r in snapshot.work_references:
        conn.execute(
            "INSERT INTO work_references VALUES (?, ?, ?, ?)",
            [
                r.work_id,
                r.reference_index,
                r.referenced_work_id,
                r.reference_status.value,
            ],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_mesh (
          work_id VARCHAR, mesh_index INTEGER, descriptor_ui VARCHAR
        )
        """
    )
    for r in snapshot.work_mesh:
        conn.execute(
            "INSERT INTO work_mesh VALUES (?, ?, ?)",
            [r.work_id, r.mesh_index, r.descriptor_ui],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_locations (
          work_id VARCHAR, location_index INTEGER, source_id VARCHAR,
          license VARCHAR, is_primary BOOLEAN
        )
        """
    )
    for r in snapshot.work_locations:
        conn.execute(
            "INSERT INTO work_locations VALUES (?, ?, ?, ?, ?)",
            [r.work_id, r.location_index, r.source_id, r.license, r.is_primary],
        )

    conn.execute(
        """
        CREATE OR REPLACE TABLE work_grants (
          work_id VARCHAR, grant_index INTEGER, funder_id VARCHAR
        )
        """
    )
    for r in snapshot.work_grants:
        conn.execute(
            "INSERT INTO work_grants VALUES (?, ?, ?)",
            [r.work_id, r.grant_index, r.funder_id],
        )

    return {
        "works": len(snapshot.works),
        "work_authors": len(snapshot.work_authors),
        "work_topics": len(snapshot.work_topics),
        "work_locations": len(snapshot.work_locations),
        "work_references": len(snapshot.work_references),
    }
