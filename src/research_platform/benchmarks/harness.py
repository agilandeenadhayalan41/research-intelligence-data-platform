"""Tiny deterministic offline harness for registry grains (FIXTURE_ONLY).

Uses DuckDB against synthetic tables. Local timings are never BigQuery evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import duckdb

from research_platform.benchmarks.models import (
    BenchmarkEvidenceType,
    BenchmarkResult,
)


@dataclass(frozen=True)
class FixtureQueryResult:
    pattern_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    evidence_type: BenchmarkEvidenceType = BenchmarkEvidenceType.FIXTURE_ONLY


def _connect() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(database=":memory:")
    connection.execute(
        """
        CREATE TABLE works AS
        SELECT * FROM (VALUES
          ('W1', '10.1/aaa', 'ACTIVE', 2020, 'gold', 'P1'),
          ('W2', '10.1/bbb', 'DELETED', 2021, 'closed', 'P1'),
          ('W3', '10.1/ccc', 'ACTIVE', 2021, 'green', 'P2')
        ) AS t(work_id, doi, activity_state, publication_year, oa_status, primary_publisher_id);

        CREATE TABLE work_authors AS
        SELECT * FROM (VALUES
          ('W1', 0, 'A1'),
          ('W1', 1, 'A2'),
          ('W2', 0, 'A1'),
          ('W3', 0, 'A3')
        ) AS t(work_id, authorship_index, author_id);

        CREATE TABLE work_topics AS
        SELECT * FROM (VALUES
          ('W1', 'T1'),
          ('W1', 'T2'),
          ('W1', 'T3'),
          ('W2', 'T1'),
          ('W3', 'T1')
        ) AS t(work_id, topic_id);

        CREATE TABLE work_references AS
        SELECT * FROM (VALUES
          ('W1', 0, 'W9', 'RESOLVED'),
          ('W1', 1, 'W2', 'RESOLVED'),
          ('W3', 0, NULL, 'UNRESOLVED')
        ) AS t(work_id, reference_index, referenced_work_id, reference_status);
        """
    )
    return connection


def run_publication_trends_fixture() -> FixtureQueryResult:
    connection = _connect()
    try:
        relation = connection.execute(
            """
            SELECT publication_year, COUNT(*)::INTEGER AS work_count
            FROM works
            WHERE activity_state = 'ACTIVE'
            GROUP BY publication_year
            ORDER BY publication_year
            """
        )
        rows = tuple(tuple(row) for row in relation.fetchall())
        columns = tuple(col[0] for col in relation.description)
        return FixtureQueryResult(
            pattern_id="publication-trends",
            columns=columns,
            rows=rows,
        )
    finally:
        connection.close()


def run_citation_fixture(*, work_id: str = "W1") -> FixtureQueryResult:
    connection = _connect()
    try:
        relation = connection.execute(
            """
            SELECT wr.work_id AS source_work_id,
                   wr.reference_index,
                   wr.referenced_work_id,
                   wr.reference_status,
                   tw.activity_state AS target_activity_state
            FROM work_references AS wr
            JOIN works AS sw
              ON sw.work_id = wr.work_id
             AND sw.activity_state = 'ACTIVE'
            LEFT JOIN works AS tw
              ON tw.work_id = wr.referenced_work_id
            WHERE wr.work_id = ?
            ORDER BY wr.reference_index
            """,
            [work_id],
        )
        rows = tuple(tuple(row) for row in relation.fetchall())
        columns = tuple(col[0] for col in relation.description)
        return FixtureQueryResult(
            pattern_id="citation-relationships",
            columns=columns,
            rows=rows,
        )
    finally:
        connection.close()


def run_unsafe_vs_safe_author_topic_fixture() -> dict[str, int]:
    """Return unsafe join inflation vs safe independent counts for ACTIVE W1."""
    connection = _connect()
    try:
        unsafe = connection.execute(
            """
            SELECT COUNT(*)::INTEGER
            FROM works AS w
            JOIN work_authors AS wa ON wa.work_id = w.work_id
            JOIN work_topics AS wt ON wt.work_id = w.work_id
            WHERE w.work_id = 'W1' AND w.activity_state = 'ACTIVE'
            """
        ).fetchone()[0]
        authors = connection.execute(
            """
            SELECT COUNT(DISTINCT wa.author_id)::INTEGER
            FROM works AS w
            JOIN work_authors AS wa ON wa.work_id = w.work_id
            WHERE w.work_id = 'W1' AND w.activity_state = 'ACTIVE'
            """
        ).fetchone()[0]
        topics = connection.execute(
            """
            SELECT COUNT(DISTINCT wt.topic_id)::INTEGER
            FROM works AS w
            JOIN work_topics AS wt ON wt.work_id = w.work_id
            WHERE w.work_id = 'W1' AND w.activity_state = 'ACTIVE'
            """
        ).fetchone()[0]
        return {
            "unsafe_join_rows": int(unsafe),
            "safe_unique_authors": int(authors),
            "safe_unique_topics": int(topics),
        }
    finally:
        connection.close()


def fixture_timing_result(*, pattern_id: str, latency_ms: float) -> BenchmarkResult:
    """Build a FIXTURE_ONLY result — never MEASURED BigQuery evidence."""
    started = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    finished = datetime(2024, 6, 1, 12, 0, 1, tzinfo=UTC)
    return BenchmarkResult.model_validate(
        {
            "result_id": uuid4(),
            "pattern_id": pattern_id,
            "engine": "duckdb",
            "dataset_scale": "synthetic-fixture",
            "started_at": started,
            "finished_at": finished,
            "cold_or_warm": "n/a",
            "latency_ms": latency_ms,
            "bytes_scanned": None,
            "estimated_cost": None,
            "result_rows": None,
            "result_bytes": None,
            "concurrency": 1,
            "freshness_lag": None,
            "evidence_type": BenchmarkEvidenceType.FIXTURE_ONLY,
            "notes": "Local fixture timing only; not BigQuery production evidence.",
        }
    )
