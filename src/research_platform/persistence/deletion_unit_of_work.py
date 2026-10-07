"""Streaming deletion publication request/result types."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.control.models import SourceFileControl
from research_platform.provenance.models import IngestionProvenance


@dataclass(frozen=True)
class DeletionCounters:
    rows_seen: int = 0
    unique_ids: int = 0
    duplicates: int = 0
    deleted: int = 0
    already_deleted: int = 0
    unknown: int = 0
    stale: int = 0
    conflicts: int = 0

    def after_duplicate(self) -> DeletionCounters:
        return DeletionCounters(
            rows_seen=self.rows_seen + 1,
            unique_ids=self.unique_ids,
            duplicates=self.duplicates + 1,
            deleted=self.deleted,
            already_deleted=self.already_deleted,
            unknown=self.unknown,
            stale=self.stale,
            conflicts=self.conflicts,
        )

    def after_outcome(self, outcome: DeletionOutcome) -> DeletionCounters:
        return DeletionCounters(
            rows_seen=self.rows_seen + 1,
            unique_ids=self.unique_ids + 1,
            duplicates=self.duplicates,
            deleted=self.deleted + (1 if outcome is DeletionOutcome.DELETED else 0),
            already_deleted=self.already_deleted
            + (1 if outcome is DeletionOutcome.ALREADY_DELETED else 0),
            unknown=self.unknown + (1 if outcome is DeletionOutcome.UNKNOWN_WORK else 0),
            stale=self.stale + (1 if outcome is DeletionOutcome.STALE else 0),
            conflicts=self.conflicts + (1 if outcome is DeletionOutcome.CONFLICT else 0),
        )


@dataclass(frozen=True)
class DeletionPublishRequest:
    asset_id: str
    claim_token: UUID
    now: datetime
    processed_at: datetime
    raw_object_key: str
    source_checksum_sha256: str
    retrieval_provenance: IngestionProvenance
    source_uri: str
    source_updated_date: date | None
    open_work_ids: Callable[[], Iterator[str]]


@dataclass(frozen=True)
class DeletionPublishResult:
    source_file: SourceFileControl
    counters: DeletionCounters
