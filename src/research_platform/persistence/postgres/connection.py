"""PostgreSQL connection helpers for local Step 12/13 ingestion."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CONTROL_DDL = _REPO_ROOT / "sql" / "control" / "001_pipeline_control.sql"
_DELETION_DDL = _REPO_ROOT / "sql" / "control" / "002_deletion_events.sql"
_CANONICAL_DDL = _REPO_ROOT / "sql" / "canonical" / "001_openalex_canonical.sql"

_LEGACY_DELETED_DATE_ERROR = (
    "legacy deletion_events rows lack a trustworthy deleted_date; "
    "refusing to fabricate source evidence from file-level "
    "source_updated_date or a sentinel date. Truncate the local "
    "deletion_events table (or drop it) and replay immutable raw "
    "deleted_ids.csv.gz assets under the work_id,deleted_date contract, "
    "then re-run schema apply."
)


class SchemaMigrationError(RuntimeError):
    """Local PostgreSQL schema cannot be upgraded without inventing data."""


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
        _migrate_deletion_events_deleted_date(connection)


def _migrate_deletion_events_deleted_date(connection: psycopg.Connection) -> None:
    """Ensure ``deleted_date`` exists without fabricating source evidence.

    Fresh tables already define ``deleted_date DATE NOT NULL``.
    Legacy empty tables receive a nullable column, then NOT NULL.
    Legacy tables with rows missing ``deleted_date`` fail explicitly so
    operators can replay immutable raw deletion assets.
    """
    connection.execute(
        """
        ALTER TABLE deletion_events
        ADD COLUMN IF NOT EXISTS deleted_date DATE
        """
    )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM deletion_events WHERE deleted_date IS NULL"
        )
        null_count = int(cur.fetchone()[0])
    if null_count > 0:
        raise SchemaMigrationError(_LEGACY_DELETED_DATE_ERROR)

    connection.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'deletion_events'
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
