"""In-memory CanonicalStore for offline local Works ingestion tests."""

from __future__ import annotations

import threading
from datetime import date, datetime
from uuid import UUID

from research_platform.canonical.openalex.deletions import (
    DeletionOutcome,
    classify_deletion,
    tombstone_work,
)
from research_platform.canonical.openalex.models import (
    Author,
    CanonicalActivityState,
    CanonicalWorkBundle,
    Funder,
    Institution,
    Publisher,
    Source,
    Topic,
    Work,
    WorkAuthor,
    WorkAuthorInstitution,
    WorkGrant,
    WorkKeyword,
    WorkLocation,
    WorkMesh,
    WorkReference,
    WorkTopic,
)
from research_platform.canonical.openalex.reconciliation import (
    EntityReconciliation,
    merge_shared_entity,
    reconcile_shared_entity,
)
from research_platform.canonical.openalex.deletions import RestoreRequiredError
from research_platform.canonical.openalex.versioning import (
    VersionComparison,
    compare_work_versions,
)
from research_platform.canonical.store import (
    CanonicalConflictError,
    CanonicalStore,
    CanonicalUpsertOutcome,
)

_Shared = Author | Institution | Source | Publisher | Topic | Funder


class InMemoryCanonicalStore(CanonicalStore):
    """Thread-safe in-memory canonical tables with deterministic upsert rules."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._works: dict[str, Work] = {}
        self._authors: dict[str, Author] = {}
        self._institutions: dict[str, Institution] = {}
        self._sources: dict[str, Source] = {}
        self._publishers: dict[str, Publisher] = {}
        self._topics: dict[str, Topic] = {}
        self._funders: dict[str, Funder] = {}
        self._work_authors: dict[tuple[str, int], WorkAuthor] = {}
        self._work_author_institutions: dict[
            tuple[str, int, int], WorkAuthorInstitution
        ] = {}
        self._work_topics: dict[tuple[str, str], WorkTopic] = {}
        self._work_keywords: dict[tuple[str, str], WorkKeyword] = {}
        self._work_references: dict[tuple[str, int], WorkReference] = {}
        self._work_mesh: dict[tuple[str, int], WorkMesh] = {}
        self._work_locations: dict[tuple[str, int], WorkLocation] = {}
        self._work_grants: dict[tuple[str, int], WorkGrant] = {}

    def get_work(self, work_id: str) -> Work | None:
        with self._lock:
            return self._works.get(work_id)

    def work_count(self) -> int:
        with self._lock:
            return len(self._works)

    def relationship_counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "authors": len(self._authors),
                "institutions": len(self._institutions),
                "sources": len(self._sources),
                "publishers": len(self._publishers),
                "topics": len(self._topics),
                "funders": len(self._funders),
                "work_authors": len(self._work_authors),
                "work_author_institutions": len(self._work_author_institutions),
                "work_topics": len(self._work_topics),
                "work_keywords": len(self._work_keywords),
                "work_references": len(self._work_references),
                "work_mesh": len(self._work_mesh),
                "work_locations": len(self._work_locations),
                "work_grants": len(self._work_grants),
            }

    def upsert_work_bundle(self, bundle: CanonicalWorkBundle) -> CanonicalUpsertOutcome:
        with self._lock:
            existing = self._works.get(bundle.work.work_id)
            if existing is None:
                self._apply_bundle(bundle, replace_relationships=True)
                return CanonicalUpsertOutcome.INSERTED

            blocked = _deletion_blocks_active_upsert(existing, bundle.work)
            if blocked is not None:
                return blocked

            comparison = compare_work_versions(existing, bundle.work)
            if comparison is VersionComparison.IDENTICAL:
                # Still enrich shared entities from this observation.
                self._upsert_shared_entities(bundle)
                return CanonicalUpsertOutcome.IDENTICAL
            if comparison is VersionComparison.STALE:
                self._upsert_shared_entities(bundle)
                return CanonicalUpsertOutcome.STALE
            if comparison is VersionComparison.CONFLICT:
                raise CanonicalConflictError(
                    f"conflicting Work versions for {bundle.work.work_id}"
                )
            # NEWER
            self._apply_bundle(bundle, replace_relationships=True)
            return CanonicalUpsertOutcome.REPLACED

    def apply_work_deletion(
        self,
        work_id: str,
        *,
        deletion_asset_id: str,
        source_checksum_sha256: str,
        run_id: UUID,
        source_updated_date: date | None,
        processed_at: datetime,
        deleted_at: datetime,
    ) -> DeletionOutcome:
        with self._lock:
            existing = self._works.get(work_id)
            outcome = classify_deletion(
                existing, deletion_updated_date=source_updated_date
            )
            if outcome is DeletionOutcome.UNKNOWN_WORK:
                return outcome
            if outcome in {DeletionOutcome.ALREADY_DELETED, DeletionOutcome.STALE}:
                return outcome
            assert existing is not None
            self._works[work_id] = tombstone_work(
                existing,
                deletion_asset_id=deletion_asset_id,
                source_checksum_sha256=source_checksum_sha256,
                run_id=run_id,
                source_updated_date=source_updated_date,
                processed_at=processed_at,
                deleted_at=deleted_at,
            )
            return DeletionOutcome.DELETED

    def _apply_bundle(
        self, bundle: CanonicalWorkBundle, *, replace_relationships: bool
    ) -> None:
        self._upsert_shared_entities(bundle)
        work_id = bundle.work.work_id
        self._works[work_id] = bundle.work
        if replace_relationships:
            self._clear_work_relationships(work_id)
        for row in bundle.work_authors:
            self._work_authors[(row.work_id, row.authorship_index)] = row
        for row in bundle.work_author_institutions:
            self._work_author_institutions[
                (row.work_id, row.authorship_index, row.institution_index)
            ] = row
        for row in bundle.work_topics:
            self._work_topics[(row.work_id, row.topic_id)] = row
        for row in bundle.work_keywords:
            self._work_keywords[(row.work_id, row.keyword_id)] = row
        for row in bundle.work_references:
            self._work_references[(row.work_id, row.reference_index)] = row
        for row in bundle.work_mesh:
            self._work_mesh[(row.work_id, row.mesh_index)] = row
        for row in bundle.work_locations:
            self._work_locations[(row.work_id, row.location_index)] = row
        for row in bundle.work_grants:
            self._work_grants[(row.work_id, row.grant_index)] = row

    def _clear_work_relationships(self, work_id: str) -> None:
        self._work_authors = {
            key: value for key, value in self._work_authors.items() if key[0] != work_id
        }
        self._work_author_institutions = {
            key: value
            for key, value in self._work_author_institutions.items()
            if key[0] != work_id
        }
        self._work_topics = {
            key: value for key, value in self._work_topics.items() if key[0] != work_id
        }
        self._work_keywords = {
            key: value for key, value in self._work_keywords.items() if key[0] != work_id
        }
        self._work_references = {
            key: value
            for key, value in self._work_references.items()
            if key[0] != work_id
        }
        self._work_mesh = {
            key: value for key, value in self._work_mesh.items() if key[0] != work_id
        }
        self._work_locations = {
            key: value
            for key, value in self._work_locations.items()
            if key[0] != work_id
        }
        self._work_grants = {
            key: value for key, value in self._work_grants.items() if key[0] != work_id
        }

    def _upsert_shared_entities(self, bundle: CanonicalWorkBundle) -> None:
        for author in bundle.authors:
            self._reconcile_entity(self._authors, author.author_id, author)
        for institution in bundle.institutions:
            self._reconcile_entity(
                self._institutions, institution.institution_id, institution
            )
        for source in bundle.sources:
            self._reconcile_entity(self._sources, source.source_id, source)
        for publisher in bundle.publishers:
            self._reconcile_entity(
                self._publishers, publisher.publisher_id, publisher
            )
        for topic in bundle.topics:
            self._reconcile_entity(self._topics, topic.topic_id, topic)
        for funder in bundle.funders:
            self._reconcile_entity(self._funders, funder.funder_id, funder)

    def _reconcile_entity(
        self, table: dict[str, _Shared], entity_id: str, incoming: _Shared
    ) -> None:
        existing = table.get(entity_id)
        if existing is None:
            table[entity_id] = incoming
            return
        outcome = reconcile_shared_entity(existing, incoming)
        if outcome is EntityReconciliation.IDENTICAL:
            return
        if outcome is EntityReconciliation.ENRICH:
            table[entity_id] = merge_shared_entity(existing, incoming)
            return
        if outcome is EntityReconciliation.NEWER:
            table[entity_id] = incoming
            return
        if outcome is EntityReconciliation.STALE:
            return
        raise CanonicalConflictError(
            f"conflicting shared entity values for {type(incoming).__name__} {entity_id}"
        )


def _deletion_blocks_active_upsert(
    existing: Work, incoming: Work
) -> CanonicalUpsertOutcome | None:
    """Enforce no silent resurrection of DELETED Works."""
    if existing.lineage.activity_state is not CanonicalActivityState.DELETED:
        return None
    if incoming.lineage.activity_state is CanonicalActivityState.DELETED:
        return None
    deletion_date = existing.lineage.source_updated_date
    incoming_date = incoming.lineage.source_updated_date
    if (
        deletion_date is not None
        and incoming_date is not None
        and incoming_date <= deletion_date
    ):
        return CanonicalUpsertOutcome.STALE
    raise RestoreRequiredError(
        f"work {existing.work_id} is DELETED; newer active ingestion requires "
        "explicit restore reconciliation (RESTORE_REQUIRED)"
    )
