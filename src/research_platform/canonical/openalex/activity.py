"""Active / deleted Work predicates for consumer filtering (Step 13)."""

from __future__ import annotations

from research_platform.canonical.openalex.models import CanonicalActivityState, Work


def is_active_work(work: Work) -> bool:
    """True when a Work may appear in consumer-facing active models."""
    return work.lineage.activity_state is CanonicalActivityState.ACTIVE


def is_deleted_work(work: Work) -> bool:
    """True when a Work carries a canonical tombstone."""
    return work.lineage.activity_state is CanonicalActivityState.DELETED
