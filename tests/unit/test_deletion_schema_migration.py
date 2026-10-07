"""Offline guards for deletion_events schema migration integrity.

Must not import ``research_platform.persistence.postgres`` package init or any
module that eagerly imports ``psycopg`` — default CI installs only ``.[dev]``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_platform.persistence.postgres.migration_policy import (
    SchemaMigrationError,
    assert_deleted_date_rows_migratable,
)


def test_migration_policy_module_is_driver_free() -> None:
    import research_platform.persistence.postgres.migration_policy as policy

    source = Path(policy.__file__).read_text(encoding="utf-8")
    assert "import psycopg" not in source
    assert "from psycopg" not in source
    assert "1970-01-01" not in source
    assert "COALESCE" not in source


def test_empty_legacy_state_is_migratable() -> None:
    assert_deleted_date_rows_migratable(0)


def test_null_deleted_date_rows_require_replay() -> None:
    with pytest.raises(SchemaMigrationError, match="trustworthy deleted_date"):
        assert_deleted_date_rows_migratable(1)
    with pytest.raises(SchemaMigrationError, match="immutable raw"):
        assert_deleted_date_rows_migratable(3)


def test_negative_null_count_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        assert_deleted_date_rows_migratable(-1)
