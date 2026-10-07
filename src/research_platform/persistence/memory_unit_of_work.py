"""Transactional publish helper for in-memory stores (offline tests / demo)."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.store import CanonicalUpsertOutcome
from research_platform.control.lifecycle import assert_claim_owned
from research_platform.control.memory import InMemoryControlStore
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    AssetPublishResult,
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
    """Apply canonical + provenance + SUCCESS with rollback on failure.

    Emulates the PostgreSQL unit-of-work for offline tests. Not durable across
    process exit.
    """
    existing = control.get_source_file(request.asset_id)
    if existing is None:
        raise LookupError(f"source file not found: {request.asset_id}")
    assert_claim_owned(existing, claim_token=request.claim_token, now=request.now)

    snap = _snapshot_canonical(canonical)
    provenance_before = list(control._provenance)  # noqa: SLF001
    try:
        outcomes: list[CanonicalUpsertOutcome] = []
        for bundle in request.bundles:
            outcomes.append(canonical.upsert_work_bundle(bundle))
        for row in request.provenance_rows:
            control.record_provenance(row)
        succeeded = control.mark_source_file_success(
            request.asset_id,
            claim_token=request.claim_token,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            retrieval_provenance=request.retrieval_provenance,
        )
        return AssetPublishResult(source_file=succeeded, outcomes=tuple(outcomes))
    except Exception:
        _restore_canonical(canonical, snap)
        control._provenance = provenance_before  # noqa: SLF001
        raise


def _snapshot_canonical(store: InMemoryCanonicalStore) -> _CanonicalSnapshot:
    return _CanonicalSnapshot(
        works=copy.deepcopy(store._works),  # noqa: SLF001
        authors=copy.deepcopy(store._authors),  # noqa: SLF001
        institutions=copy.deepcopy(store._institutions),  # noqa: SLF001
        sources=copy.deepcopy(store._sources),  # noqa: SLF001
        publishers=copy.deepcopy(store._publishers),  # noqa: SLF001
        topics=copy.deepcopy(store._topics),  # noqa: SLF001
        funders=copy.deepcopy(store._funders),  # noqa: SLF001
        work_authors=copy.deepcopy(store._work_authors),  # noqa: SLF001
        work_author_institutions=copy.deepcopy(store._work_author_institutions),  # noqa: SLF001
        work_topics=copy.deepcopy(store._work_topics),  # noqa: SLF001
        work_keywords=copy.deepcopy(store._work_keywords),  # noqa: SLF001
        work_references=copy.deepcopy(store._work_references),  # noqa: SLF001
        work_mesh=copy.deepcopy(store._work_mesh),  # noqa: SLF001
        work_locations=copy.deepcopy(store._work_locations),  # noqa: SLF001
        work_grants=copy.deepcopy(store._work_grants),  # noqa: SLF001
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
