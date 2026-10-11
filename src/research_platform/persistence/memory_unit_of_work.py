"""Transactional publish helper for in-memory stores (offline tests / demo)."""

from __future__ import annotations

from dataclasses import dataclass

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.control.lifecycle import assert_claim_owned
from research_platform.control.memory import InMemoryControlStore
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    AssetPublishResult,
    PublishCounters,
)


@dataclass
class _CanonicalSnapshot:
    works: dict
    authors: dict
    institutions: dict
    sources: dict
    publishers: dict
    topics: dict
    funders: dict
    work_authors: dict
    work_author_institutions: dict
    work_topics: dict
    work_keywords: dict
    work_references: dict
    work_mesh: dict
    work_locations: dict
    work_grants: dict


def publish_claimed_asset_memory(
    control: InMemoryControlStore,
    canonical: InMemoryCanonicalStore,
    request: AssetPublishRequest,
) -> AssetPublishResult:
    """Stream canonical + provenance + SUCCESS with rollback on failure.

    Emulates the PostgreSQL unit-of-work for offline tests. Not durable across
    process exit. Does not materialize all mapped records for the file.
    """
    existing = control.get_source_file(request.asset_id)
    if existing is None:
        raise LookupError(f"source file not found: {request.asset_id}")
    assert_claim_owned(existing, claim_token=request.claim_token, now=request.now)

    snap = _snapshot_canonical(canonical)
    provenance_before = list(control._provenance)  # noqa: SLF001
    try:
        counters = PublishCounters()
        for item in request.open_records():
            outcome = canonical.upsert_work_bundle(item.bundle)
            control.record_provenance(item.provenance)
            counters = counters.after_outcome(outcome)
            del item
        succeeded = control.mark_source_file_success(
            request.asset_id,
            claim_token=request.claim_token,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            retrieval_provenance=request.retrieval_provenance,
        )
        return AssetPublishResult(source_file=succeeded, counters=counters)
    except Exception:
        _restore_canonical(canonical, snap)
        control._restore_provenance(provenance_before)  # noqa: SLF001
        raise


def _snapshot_canonical(store: InMemoryCanonicalStore) -> _CanonicalSnapshot:
    # Shallow copies are enough: every stored value is a frozen model whose
    # fields are scalars or a frozen CanonicalLineage, and the store replaces
    # relationship inner dicts (work_id -> rows) instead of mutating them.
    # A deep copy cost one model copy per stored row on every published asset.
    return _CanonicalSnapshot(
        works=dict(store._works),  # noqa: SLF001
        authors=dict(store._authors),  # noqa: SLF001
        institutions=dict(store._institutions),  # noqa: SLF001
        sources=dict(store._sources),  # noqa: SLF001
        publishers=dict(store._publishers),  # noqa: SLF001
        topics=dict(store._topics),  # noqa: SLF001
        funders=dict(store._funders),  # noqa: SLF001
        work_authors=dict(store._work_authors),  # noqa: SLF001
        work_author_institutions=dict(store._work_author_institutions),  # noqa: SLF001
        work_topics=dict(store._work_topics),  # noqa: SLF001
        work_keywords=dict(store._work_keywords),  # noqa: SLF001
        work_references=dict(store._work_references),  # noqa: SLF001
        work_mesh=dict(store._work_mesh),  # noqa: SLF001
        work_locations=dict(store._work_locations),  # noqa: SLF001
        work_grants=dict(store._work_grants),  # noqa: SLF001
    )


def _restore_canonical(store: InMemoryCanonicalStore, snap: _CanonicalSnapshot) -> None:
    store._works = snap.works  # noqa: SLF001
    store._authors = snap.authors  # noqa: SLF001
    store._institutions = snap.institutions  # noqa: SLF001
    store._sources = snap.sources  # noqa: SLF001
    store._publishers = snap.publishers  # noqa: SLF001
    store._topics = snap.topics  # noqa: SLF001
    store._funders = snap.funders  # noqa: SLF001
    store._work_authors = snap.work_authors  # noqa: SLF001
    store._work_author_institutions = snap.work_author_institutions  # noqa: SLF001
    store._work_topics = snap.work_topics  # noqa: SLF001
    store._work_keywords = snap.work_keywords  # noqa: SLF001
    store._work_references = snap.work_references  # noqa: SLF001
    store._work_mesh = snap.work_mesh  # noqa: SLF001
    store._work_locations = snap.work_locations  # noqa: SLF001
    store._work_grants = snap.work_grants  # noqa: SLF001
