"""Work tombstone / deletion precedence rules (Step 13 / #21).

Relationship policy (A): physical relationship rows owned by a deleted Work are
**preserved**. Consumer-facing active models must filter through parent Work
``activity_state = ACTIVE``. Shared entities are never removed because one Work
is deleted.

References: if Work A references Work B and B is tombstoned, A's observed
``work_references`` row remains (source observation). Do not treat that as an
orphan error.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from research_platform.canonical.openalex.models import (
    CanonicalActivityState,
    CanonicalLineage,
    Work,
)


class DeletionOutcome(StrEnum):
    """Per-ID result of applying one deletion observation."""

    DELETED = "DELETED"
    ALREADY_DELETED = "ALREADY_DELETED"
    UNKNOWN_WORK = "UNKNOWN_WORK"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    DUPLICATE = "DUPLICATE"


class RestoreRequiredError(ValueError):
    """A newer active observation arrived after a tombstone; no silent restore."""


def tombstone_work(
    existing: Work,
    *,
    deletion_asset_id: str,
    source_checksum_sha256: str,
    run_id: UUID,
    source_updated_date: date | None,
    processed_at: datetime,
    deleted_at: datetime,
) -> Work:
    """Return a Work copy with DELETED lineage; payload fields preserved."""
    lineage = CanonicalLineage.model_validate(
        {
            "source_asset_id": deletion_asset_id,
            "source_checksum_sha256": source_checksum_sha256,
            "source_updated_date": source_updated_date,
            "run_id": run_id,
            "processed_at": processed_at,
            "activity_state": CanonicalActivityState.DELETED,
            "deleted_at": deleted_at,
        }
    )
    return existing.model_copy(update={"lineage": lineage})


def classify_deletion(
    existing: Work | None,
    *,
    deletion_updated_date: date | None,
) -> DeletionOutcome:
    """Decide the deletion outcome before mutating state."""
    if existing is None:
        return DeletionOutcome.UNKNOWN_WORK
    if existing.lineage.activity_state is CanonicalActivityState.DELETED:
        existing_date = existing.lineage.source_updated_date
        if (
            deletion_updated_date is not None
            and existing_date is not None
            and deletion_updated_date < existing_date
        ):
            return DeletionOutcome.STALE
        return DeletionOutcome.ALREADY_DELETED
    return DeletionOutcome.DELETED


def assert_active_upsert_allowed(existing: Work, incoming: Work) -> None:
    """Block silent DELETED → ACTIVE transitions from ordinary Works upsert."""
    if existing.lineage.activity_state is not CanonicalActivityState.DELETED:
        return
    if incoming.lineage.activity_state is CanonicalActivityState.DELETED:
        return
    raise RestoreRequiredError(
        f"work {existing.work_id} is DELETED; newer active ingestion requires "
        "explicit restore reconciliation (RESTORE_REQUIRED)"
    )
