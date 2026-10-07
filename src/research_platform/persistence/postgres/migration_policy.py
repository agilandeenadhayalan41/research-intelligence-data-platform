"""Driver-free deletion_events schema migration policy (Step 13).

This module must not import ``psycopg`` so offline tests can exercise migration
decisions without the optional PostgreSQL runtime dependency.
"""

from __future__ import annotations

LEGACY_DELETED_DATE_ERROR = (
    "legacy deletion_events rows lack a trustworthy deleted_date; "
    "refusing to fabricate source evidence from file-level "
    "source_updated_date or a sentinel date. Truncate the local "
    "deletion_events table (or drop it) and replay immutable raw "
    "deleted_ids.csv.gz assets under the work_id,deleted_date contract, "
    "then re-run schema apply."
)


class SchemaMigrationError(RuntimeError):
    """Local PostgreSQL schema cannot be upgraded without inventing data."""


def assert_deleted_date_rows_migratable(null_deleted_date_count: int) -> None:
    """Fail when legacy rows lack trustworthy per-row ``deleted_date`` values.

    Never fabricate dates from file-level ``source_updated_date`` or sentinels.
    Empty tables (``null_deleted_date_count == 0``) are migratable.
    """
    if null_deleted_date_count < 0:
        raise ValueError("null_deleted_date_count must be >= 0")
    if null_deleted_date_count > 0:
        raise SchemaMigrationError(LEGACY_DELETED_DATE_ERROR)
