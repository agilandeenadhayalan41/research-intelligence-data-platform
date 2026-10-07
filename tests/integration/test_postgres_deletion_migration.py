"""PostgreSQL migration integrity for deletion_events.deleted_date (Step 13).

Separately invoked via ``make test-postgres-ingestion`` (marker ``postgres``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Iterator
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")

import psycopg

from research_platform.persistence.postgres.connection import (
    SchemaMigrationError,
    apply_ingestion_schema,
    connect_postgres,
)

DSN = os.environ.get("POSTGRES_DSN", "").strip()
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="POSTGRES_DSN unset; skipping PostgreSQL tests"),
]

_LEGACY_DELETION_EVENTS_DDL = """
CREATE TABLE deletion_events (
    work_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL,
    source_uri TEXT NOT NULL,
    source_updated_date DATE NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL,
    CONSTRAINT deletion_events_pk
        PRIMARY KEY (work_id, asset_id, source_checksum_sha256)
);
"""


@pytest.fixture
def pg_raw() -> Iterator[psycopg.Connection]:
    """Connection without auto-applying the current schema (tests own DDL)."""
    connection = connect_postgres(DSN)
    try:
        yield connection
    finally:
        # Always restore the current schema for other postgres suites.
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS deletion_events CASCADE")
        connection.commit()
        apply_ingestion_schema(connection)
        connection.close()


def _column_nullable(connection: psycopg.Connection, column: str) -> str:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'deletion_events'
              AND column_name = %s
            """,
            (column,),
        )
        row = cur.fetchone()
    assert row is not None, f"column missing: {column}"
    return str(row[0])


def _insert_legacy_row(
    connection: psycopg.Connection,
    *,
    source_updated_date: str | None = "2024-02-01",
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deletion_events (
                work_id, asset_id, source_checksum_sha256, run_id, source_uri,
                source_updated_date, processed_at, outcome
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                "W1",
                "oad-legacy",
                "ab" * 32,
                uuid4(),
                "s3://openalex/data/jsonl/works/deleted_ids.csv.gz",
                source_updated_date,
                datetime(2024, 6, 1, tzinfo=UTC),
                "DELETED",
            ),
        )


def test_fresh_database_deleted_date_not_null(pg_raw: psycopg.Connection) -> None:
    with pg_raw.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS deletion_events CASCADE")
    pg_raw.commit()

    apply_ingestion_schema(pg_raw)
    assert _column_nullable(pg_raw, "deleted_date") == "NO"
    with pg_raw.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'deletion_events'
              AND column_name = 'deleted_date'
            """
        )
        assert cur.fetchone()[0] == 1


def test_legacy_empty_deletion_events_migrates(pg_raw: psycopg.Connection) -> None:
    with pg_raw.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS deletion_events CASCADE")
        cur.execute(_LEGACY_DELETION_EVENTS_DDL)
    pg_raw.commit()

    apply_ingestion_schema(pg_raw)
    assert _column_nullable(pg_raw, "deleted_date") == "NO"
    with pg_raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM deletion_events")
        assert cur.fetchone()[0] == 0


def test_legacy_rows_without_deleted_date_fail(pg_raw: psycopg.Connection) -> None:
    with pg_raw.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS deletion_events CASCADE")
        cur.execute(_LEGACY_DELETION_EVENTS_DDL)
    _insert_legacy_row(pg_raw, source_updated_date="2024-02-01")
    pg_raw.commit()

    with pytest.raises(SchemaMigrationError, match="trustworthy deleted_date"):
        apply_ingestion_schema(pg_raw)

    # Column may have been added inside the rolled-back transaction; restore
    # visibility and confirm no fabricated dates were committed.
    pg_raw.rollback()
    with pg_raw.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'deletion_events'
            """
        )
        columns = {row[0] for row in cur.fetchall()}
    # Rolled-back ADD COLUMN means legacy shape remains until a successful apply.
    assert "deleted_date" not in columns
    with pg_raw.cursor() as cur:
        cur.execute("SELECT source_updated_date::text, outcome FROM deletion_events")
        assert cur.fetchone() == ("2024-02-01", "DELETED")


def test_null_deleted_date_rows_fail_explicitly(pg_raw: psycopg.Connection) -> None:
    with pg_raw.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS deletion_events CASCADE")
        cur.execute(_LEGACY_DELETION_EVENTS_DDL)
        cur.execute(
            "ALTER TABLE deletion_events ADD COLUMN deleted_date DATE"
        )
    _insert_legacy_row(pg_raw, source_updated_date="2024-03-01")
    # Row has NULL deleted_date (column present but unset).
    pg_raw.commit()

    with pytest.raises(SchemaMigrationError, match="trustworthy deleted_date"):
        apply_ingestion_schema(pg_raw)

    pg_raw.rollback()
    with pg_raw.cursor() as cur:
        cur.execute("SELECT deleted_date FROM deletion_events")
        assert cur.fetchone()[0] is None
