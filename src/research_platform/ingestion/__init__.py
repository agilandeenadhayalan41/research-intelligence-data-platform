"""Bounded local OpenAlex Works ingestion and deletions (Steps 12–13)."""

from research_platform.ingestion.deletion_pipeline import (
    DeletionIngestStats,
    LocalDeletionsIngestResult,
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
    "run_openalex_deletions_local_ingest",
    "run_openalex_works_local_ingest",
]
