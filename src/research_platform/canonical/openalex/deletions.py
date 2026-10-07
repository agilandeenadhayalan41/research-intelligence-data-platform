"""Work tombstone / deletion precedence rules (Step 13 / #21).

Relationship policy (A): physical relationship rows owned by a deleted Work are
**preserved**. Consumer-facing active models must filter through parent Work
``activity_state = ACTIVE``. Shared entities are never removed because one Work
is deleted.

References: if Work A references Work B and B is tombstoned, A's observed
``work_references`` row remains (source observation). Do not treat that as an
orphan error.

Precedence uses each deletion row's ``deleted_date`` (not a file-level date).
For tombstoned Works, ``CanonicalLineage.source_updated_date`` carries that
source ``deleted_date`` (calendar date only; no fabricated timestamp).
``deleted_at`` remains the processing timestamp.
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
    deleted_date: date,
    processed_at: datetime,
    deleted_at: datetime,
) -> Work:
    """Return a Work copy with DELETED lineage; payload fields preserved.

    ``source_updated_date`` is set to the source ``deleted_date`` for
    version precedence against later active ingestion.
    """
    lineage = CanonicalLineage.model_validate(
        {
            "source_asset_id": deletion_asset_id,
            "source_checksum_sha256": source_checksum_sha256,
            "source_updated_date": deleted_date,
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
    deleted_date: date,
) -> DeletionOutcome:
    """Decide the deletion outcome before mutating state.

    Rules (dates are calendar dates from source evidence):

    - unknown Work → ``UNKNOWN_WORK``
    - ACTIVE, deletion older than Work version → ``STALE`` (keep ACTIVE)
    - ACTIVE, deletion equal/newer → ``DELETED``
    - ACTIVE, Work version date missing → ``STALE`` (conservative; do not
      destroy newer state when ordering cannot be proven)
    - DELETED, older deletion → ``STALE``
    - DELETED, equal/newer deletion → ``ALREADY_DELETED``
    """
    if existing is None:
        return DeletionOutcome.UNKNOWN_WORK

    existing_date = existing.lineage.source_updated_date
    if existing.lineage.activity_state is CanonicalActivityState.DELETED:
        if existing_date is not None and deleted_date < existing_date:
            return DeletionOutcome.STALE
        return DeletionOutcome.ALREADY_DELETED

    # ACTIVE
    if existing_date is None:
        return DeletionOutcome.STALE
    if deleted_date < existing_date:
        return DeletionOutcome.STALE
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
