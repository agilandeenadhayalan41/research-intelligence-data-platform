"""Bounded local OpenAlex Works ingestion and deletions (Steps 12–13)."""

from research_platform.ingestion.deletion_pipeline import (
    DeletionIngestStats,
    LocalDeletionsIngestResult,
    ingest_deletion_asset,
    run_openalex_deletions_local_ingest,
)
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionDecodeError,
    IngestionError,
    IngestionFormatError,
    IngestionSkippedError,
)
from research_platform.ingestion.pipeline import (
    FileIngestStats,
    LocalWorksIngestResult,
    ingest_works_asset,
    run_openalex_works_local_ingest,
)

__all__ = [
    "DeletionIngestStats",
    "FileIngestStats",
    "IngestionConfigError",
    "IngestionDecodeError",
    "IngestionError",
    "IngestionFormatError",
    "IngestionSkippedError",
    "LocalDeletionsIngestResult",
    "LocalWorksIngestResult",
    "ingest_deletion_asset",
    "ingest_works_asset",
    "run_openalex_deletions_local_ingest",
    "run_openalex_works_local_ingest",
]
