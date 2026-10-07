"""Local PostgreSQL write adapters for Step 12/13 ingestion transactions.

Imports that require the optional ``psycopg`` extra are resolved lazily so
driver-free submodules (e.g. ``migration_policy``) remain importable under the
default ``.[dev]`` offline test environment.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AssetPublishRequest",
    "AssetPublishResult",
    "PostgresCanonicalStore",
    "PostgresControlStore",
    "PublishCounters",
    "SchemaMigrationError",
    "StreamedWorkRecord",
    "apply_ingestion_schema",
    "connect_postgres",
    "postgres_dsn_from_env",
    "publish_claimed_asset",
]


def __getattr__(name: str) -> Any:
    if name == "SchemaMigrationError":
        from research_platform.persistence.postgres.migration_policy import (
            SchemaMigrationError,
        )

        return SchemaMigrationError
    if name in {
        "apply_ingestion_schema",
        "connect_postgres",
        "postgres_dsn_from_env",
    }:
        from research_platform.persistence.postgres import connection as connection_mod

        return getattr(connection_mod, name)
    if name == "PostgresCanonicalStore":
        from research_platform.persistence.postgres.canonical_store import (
            PostgresCanonicalStore,
        )

        return PostgresCanonicalStore
    if name == "PostgresControlStore":
        from research_platform.persistence.postgres.control_store import (
            PostgresControlStore,
        )

        return PostgresControlStore
    if name == "publish_claimed_asset":
        from research_platform.persistence.postgres.unit_of_work import (
            publish_claimed_asset,
        )

        return publish_claimed_asset
    if name in {
        "AssetPublishRequest",
        "AssetPublishResult",
        "PublishCounters",
        "StreamedWorkRecord",
    }:
        from research_platform.persistence import unit_of_work as uow_mod

        return getattr(uow_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
