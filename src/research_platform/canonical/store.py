"""Canonical write protocol for local ingestion (not Warehouse.query)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.canonical.openalex.models import CanonicalWorkBundle, Work


class CanonicalUpsertOutcome(StrEnum):
    """Per-Work result of applying one mapped bundle."""

    INSERTED = "INSERTED"
    IDENTICAL = "IDENTICAL"
    REPLACED = "REPLACED"
    STALE = "STALE"
    CONFLICT = "CONFLICT"


class CanonicalConflictError(ValueError):
    """Incoming canonical data conflicts with an existing row."""


class CanonicalStore(ABC):
    """Portable canonical write surface used by Step 12/13 ingestion."""

    @abstractmethod
    def upsert_work_bundle(self, bundle: CanonicalWorkBundle) -> CanonicalUpsertOutcome:
        """Apply one Work bundle with Work versioning and shared-entity reconciliation.

        Must not silently resurrect a DELETED Work to ACTIVE (Step 13).
        """
        raise NotImplementedError

    @abstractmethod
    def apply_work_deletion(
        self,
        work_id: str,
        *,
        deletion_asset_id: str,
        source_checksum_sha256: str,
        run_id: UUID,
        deleted_date: date,
        processed_at: datetime,
        deleted_at: datetime,
    ) -> DeletionOutcome:
        """Apply a tombstone for ``work_id`` without removing physical history.

        ``deleted_date`` is the per-row OpenAlex source deletion calendar date.
        """
        raise NotImplementedError

    @abstractmethod
    def get_work(self, work_id: str) -> Work | None:
        raise NotImplementedError

    @abstractmethod
    def work_count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def relationship_counts(self) -> dict[str, int]:
        """Return counts for relationship tables (authors links, refs, etc.)."""
        raise NotImplementedError
