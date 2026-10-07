"""Offline guards for deletion_events schema migration integrity."""

from __future__ import annotations

from pathlib import Path

from research_platform.persistence.postgres import connection as connection_mod


def test_migration_source_has_no_fabricated_deleted_date_fallback() -> None:
    source = Path(connection_mod.__file__).read_text(encoding="utf-8")
    assert "1970-01-01" not in source
    assert "COALESCE(deleted_date" not in source
    assert "source_updated_date, DATE" not in source
    assert "SchemaMigrationError" in source
    assert "trustworthy deleted_date" in source
