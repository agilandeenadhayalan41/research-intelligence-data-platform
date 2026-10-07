"""PostgreSQL connection helpers for local Step 12 ingestion."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CONTROL_DDL = _REPO_ROOT / "sql" / "control" / "001_pipeline_control.sql"
_DELETION_DDL = _REPO_ROOT / "sql" / "control" / "002_deletion_events.sql"
_CANONICAL_DDL = _REPO_ROOT / "sql" / "canonical" / "001_openalex_canonical.sql"


def postgres_dsn_from_env(env_var: str = "POSTGRES_DSN") -> str:
    """Read a local PostgreSQL DSN from the process environment."""
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise RuntimeError(
            f"{env_var} is unset; durable local ingestion requires an explicit DSN"
        )
    return value


def connect_postgres(dsn: str) -> psycopg.Connection:
    """Open an autocommit=False connection for explicit transactions.

    Timezone is set via libpq options so the session starts idle (no outer
    transaction). Otherwise ``SET`` would open a transaction and nested
    ``connection.transaction()`` blocks would only release savepoints.
    """
    return psycopg.connect(dsn, autocommit=False, options="-c TimeZone=UTC")


def apply_ingestion_schema(connection: psycopg.Connection) -> None:
    """Apply control + canonical + deletion DDL (idempotent CREATE IF NOT EXISTS)."""
    with connection.transaction():
        for path in (_CONTROL_DDL, _DELETION_DDL, _CANONICAL_DDL):
            sql = path.read_text(encoding="utf-8")
            connection.execute(sql)
        # Widen content_format for DBs created before Step 13 added csv.
        connection.execute(
            "ALTER TABLE source_files DROP CONSTRAINT IF EXISTS source_files_format_check"
        )
        connection.execute(
            """
            ALTER TABLE source_files
            ADD CONSTRAINT source_files_format_check
            CHECK (content_format IN ('jsonl', 'parquet', 'csv'))
            """
        )
        # Harden DBs created before per-row deleted_date was required.
        # Must run before any index that references deleted_date.
        connection.execute(
            """
            ALTER TABLE deletion_events
            ADD COLUMN IF NOT EXISTS deleted_date DATE
            """
        )
        connection.execute(
            """
            UPDATE deletion_events
            SET deleted_date = COALESCE(deleted_date, source_updated_date, DATE '1970-01-01')
            WHERE deleted_date IS NULL
            """
        )
        # Only enforce NOT NULL once every row has a value (fresh tables already do).
        connection.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'deletion_events'
                      AND column_name = 'deleted_date'
                      AND is_nullable = 'YES'
                ) THEN
                    ALTER TABLE deletion_events
                    ALTER COLUMN deleted_date SET NOT NULL;
                END IF;
            END $$
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS deletion_events_deleted_date_idx
            ON deletion_events (deleted_date)
            """
        )
