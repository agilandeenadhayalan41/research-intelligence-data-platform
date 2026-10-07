"""Local PostgreSQL write adapters for Step 12 ingestion transactions."""

from research_platform.persistence.postgres.canonical_store import PostgresCanonicalStore
from research_platform.persistence.postgres.connection import (
    SchemaMigrationError,
    apply_ingestion_schema,
    connect_postgres,
    postgres_dsn_from_env,
)
from research_platform.persistence.postgres.control_store import PostgresControlStore
from research_platform.persistence.postgres.unit_of_work import publish_claimed_asset
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    AssetPublishResult,
    PublishCounters,
    StreamedWorkRecord,
)

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
