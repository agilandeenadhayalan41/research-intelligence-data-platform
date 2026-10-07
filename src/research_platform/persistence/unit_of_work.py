"""Shared asset-publication request/result types (backend-independent)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from research_platform.canonical.openalex.models import CanonicalWorkBundle
from research_platform.canonical.store import CanonicalUpsertOutcome
from research_platform.control.models import RecordProvenance, SourceFileControl
from research_platform.provenance.models import IngestionProvenance


@dataclass(frozen=True)
class StreamedWorkRecord:
    """One mapped Work + provenance grain for incremental publication.

    Publishers must not accumulate these for the whole file; process and discard
    per record inside the open transaction.
    """

    bundle: CanonicalWorkBundle
    provenance: RecordProvenance


@dataclass(frozen=True)
class PublishCounters:
    """Aggregate publish outcomes without retaining per-record payloads."""

    works_inserted: int = 0
    works_identical: int = 0
    works_replaced: int = 0
    works_stale: int = 0
    record_provenance_count: int = 0

    def after_outcome(self, outcome: CanonicalUpsertOutcome) -> PublishCounters:
        inserted = self.works_inserted + (
            1 if outcome is CanonicalUpsertOutcome.INSERTED else 0
        )
        identical = self.works_identical + (
            1 if outcome is CanonicalUpsertOutcome.IDENTICAL else 0
        )
        replaced = self.works_replaced + (
            1 if outcome is CanonicalUpsertOutcome.REPLACED else 0
        )
        stale = self.works_stale + (
            1 if outcome is CanonicalUpsertOutcome.STALE else 0
        )
        return PublishCounters(
            works_inserted=inserted,
            works_identical=identical,
            works_replaced=replaced,
            works_stale=stale,
            record_provenance_count=self.record_provenance_count + 1,
        )


@dataclass(frozen=True)
class AssetPublishRequest:
    """One-asset publication request with a streaming record factory.

    ``open_records`` is invoked exactly once inside the publication transaction.
    It must open the immutable raw object and yield mapped records incrementally
    so exceptions abort the same transaction.
    """

    asset_id: str
    claim_token: UUID
    now: datetime
    processed_at: datetime
    raw_object_key: str
    source_checksum_sha256: str
    retrieval_provenance: IngestionProvenance
    open_records: Callable[[], Iterator[StreamedWorkRecord]]


@dataclass(frozen=True)
class AssetPublishResult:
    source_file: SourceFileControl
    counters: PublishCounters
