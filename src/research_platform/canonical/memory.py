"""In-memory CanonicalStore for offline local Works ingestion tests."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
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


@dataclass(frozen=True)
class CanonicalSnapshot:
    """Immutable public snapshot of all canonical tables (Step 19)."""

    works: tuple[Work, ...]
    authors: tuple[Author, ...]
    institutions: tuple[Institution, ...]
    sources: tuple[Source, ...]
    publishers: tuple[Publisher, ...]
    topics: tuple[Topic, ...]
    funders: tuple[Funder, ...]
    work_authors: tuple[WorkAuthor, ...]
    work_author_institutions: tuple[WorkAuthorInstitution, ...]
    work_topics: tuple[WorkTopic, ...]
    work_keywords: tuple[WorkKeyword, ...]
    work_references: tuple[WorkReference, ...]
    work_mesh: tuple[WorkMesh, ...]
    work_locations: tuple[WorkLocation, ...]
    work_grants: tuple[WorkGrant, ...]


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
        # Relationship rows nest by the grain key's work_id (key[0]):
        # work_id -> {grain key -> row}, so clearing a work is one pop per map.
        # Inner dicts are copy-on-write values, never mutated in place, so a
        # shallow copy of an outer map is a consistent rollback snapshot
        # (persistence/memory_unit_of_work.py).
        self._work_authors: dict[str, dict[tuple[str, int], WorkAuthor]] = {}
        self._work_author_institutions: dict[
            str, dict[tuple[str, int, int], WorkAuthorInstitution]
        ] = {}
        self._work_topics: dict[str, dict[tuple[str, str], WorkTopic]] = {}
        self._work_keywords: dict[str, dict[tuple[str, str], WorkKeyword]] = {}
        self._work_references: dict[str, dict[tuple[str, int], WorkReference]] = {}
        self._work_mesh: dict[str, dict[tuple[str, int], WorkMesh]] = {}
        self._work_locations: dict[str, dict[tuple[str, int], WorkLocation]] = {}
        self._work_grants: dict[str, dict[tuple[str, int], WorkGrant]] = {}
        # Unknown-work deletion ledger: work_id -> authoritative deleted_date.
        # Not a fabricated bibliographic Work row; blocks stale ACTIVE resurrection.
        self._deletion_barriers: dict[str, date] = {}

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
                "work_authors": _row_count(self._work_authors),
                "work_author_institutions": _row_count(self._work_author_institutions),
                "work_topics": _row_count(self._work_topics),
                "work_keywords": _row_count(self._work_keywords),
                "work_references": _row_count(self._work_references),
                "work_mesh": _row_count(self._work_mesh),
                "work_locations": _row_count(self._work_locations),
                "work_grants": _row_count(self._work_grants),
            }

    def snapshot(self) -> CanonicalSnapshot:
        """Return an immutable copy of all canonical tables for analytical build."""
        with self._lock:
            return CanonicalSnapshot(
                works=tuple(sorted(self._works.values(), key=lambda w: w.work_id)),
                authors=tuple(sorted(self._authors.values(), key=lambda a: a.author_id)),
                institutions=tuple(
                    sorted(self._institutions.values(), key=lambda i: i.institution_id)
                ),
                sources=tuple(sorted(self._sources.values(), key=lambda s: s.source_id)),
                publishers=tuple(
                    sorted(self._publishers.values(), key=lambda p: p.publisher_id)
                ),
                topics=tuple(sorted(self._topics.values(), key=lambda t: t.topic_id)),
                funders=tuple(sorted(self._funders.values(), key=lambda f: f.funder_id)),
                work_authors=tuple(
                    sorted(
                        _rows(self._work_authors),
                        key=lambda r: (r.work_id, r.authorship_index),
                    )
                ),
                work_author_institutions=tuple(
                    sorted(
                        _rows(self._work_author_institutions),
                        key=lambda r: (
                            r.work_id,
                            r.authorship_index,
                            r.institution_index,
                        ),
                    )
                ),
                work_topics=tuple(
                    sorted(
                        _rows(self._work_topics),
                        key=lambda r: (r.work_id, r.topic_id),
                    )
                ),
                work_keywords=tuple(
                    sorted(
                        _rows(self._work_keywords),
                        key=lambda r: (r.work_id, r.keyword_id),
                    )
                ),
                work_references=tuple(
                    sorted(
                        _rows(self._work_references),
                        key=lambda r: (r.work_id, r.reference_index),
                    )
                ),
                work_mesh=tuple(
                    sorted(
                        _rows(self._work_mesh),
                        key=lambda r: (r.work_id, r.mesh_index),
                    )
                ),
                work_locations=tuple(
                    sorted(
                        _rows(self._work_locations),
                        key=lambda r: (r.work_id, r.location_index),
                    )
                ),
                work_grants=tuple(
                    sorted(
                        _rows(self._work_grants),
                        key=lambda r: (r.work_id, r.grant_index),
                    )
                ),
            )

    def deletion_barriers(self) -> dict[str, date]:
        """Public copy of unknown-work deletion barriers (work_id -> deleted_date)."""
        with self._lock:
            return dict(self._deletion_barriers)

    def upsert_work_bundle(self, bundle: CanonicalWorkBundle) -> CanonicalUpsertOutcome:
        with self._lock:
            existing = self._works.get(bundle.work.work_id)
            if existing is None:
                barrier_block = _barrier_blocks_active_insert(
                    self._deletion_barriers.get(bundle.work.work_id),
                    bundle.work,
                )
                if barrier_block is not None:
                    return barrier_block
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
        deleted_date: date,
        processed_at: datetime,
        deleted_at: datetime,
    ) -> DeletionOutcome:
        with self._lock:
            existing = self._works.get(work_id)
            outcome = classify_deletion(existing, deleted_date=deleted_date)
            if outcome is DeletionOutcome.UNKNOWN_WORK:
                # Retain authoritative deletion barrier without fabricating a Work.
                prior = self._deletion_barriers.get(work_id)
                if prior is None or deleted_date >= prior:
                    self._deletion_barriers[work_id] = deleted_date
                return outcome
            if outcome in {DeletionOutcome.ALREADY_DELETED, DeletionOutcome.STALE}:
                return outcome
            assert existing is not None
            self._works[work_id] = tombstone_work(
                existing,
                deletion_asset_id=deletion_asset_id,
                source_checksum_sha256=source_checksum_sha256,
                run_id=run_id,
                deleted_date=deleted_date,
                processed_at=processed_at,
                deleted_at=deleted_at,
            )
            # Keep barrier aligned with known tombstone for consistent lookups.
            self._deletion_barriers[work_id] = deleted_date
            return DeletionOutcome.DELETED

    def _apply_bundle(
        self, bundle: CanonicalWorkBundle, *, replace_relationships: bool
    ) -> None:
        self._upsert_shared_entities(bundle)
        work_id = bundle.work.work_id
        self._works[work_id] = bundle.work
        if replace_relationships:
            self._clear_work_relationships(work_id)
        _merge_rows(
            self._work_authors,
            bundle.work_authors,
            lambda r: (r.work_id, r.authorship_index),
        )
        _merge_rows(
            self._work_author_institutions,
            bundle.work_author_institutions,
            lambda r: (r.work_id, r.authorship_index, r.institution_index),
        )
        _merge_rows(
            self._work_topics, bundle.work_topics, lambda r: (r.work_id, r.topic_id)
        )
        _merge_rows(
            self._work_keywords,
            bundle.work_keywords,
            lambda r: (r.work_id, r.keyword_id),
        )
        _merge_rows(
            self._work_references,
            bundle.work_references,
            lambda r: (r.work_id, r.reference_index),
        )
        _merge_rows(
            self._work_mesh, bundle.work_mesh, lambda r: (r.work_id, r.mesh_index)
        )
        _merge_rows(
            self._work_locations,
            bundle.work_locations,
            lambda r: (r.work_id, r.location_index),
        )
        _merge_rows(
            self._work_grants, bundle.work_grants, lambda r: (r.work_id, r.grant_index)
        )

    def _clear_work_relationships(self, work_id: str) -> None:
        # Drops every row whose key[0] is work_id, as the flat-map filter did,
        # without scanning other works or mutating a shared inner dict.
        self._work_authors.pop(work_id, None)
        self._work_author_institutions.pop(work_id, None)
        self._work_topics.pop(work_id, None)
        self._work_keywords.pop(work_id, None)
        self._work_references.pop(work_id, None)
        self._work_mesh.pop(work_id, None)
        self._work_locations.pop(work_id, None)
        self._work_grants.pop(work_id, None)

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


def _row_count(table: Mapping[str, Mapping[Any, Any]]) -> int:
    return sum(len(rows) for rows in table.values())


def _rows[R](table: Mapping[str, Mapping[Any, R]]) -> Iterator[R]:
    """Every row of a nested relationship map; callers sort by the grain key."""
    for rows in table.values():
        yield from rows.values()


def _merge_rows[K: tuple[Any, ...], R](
    table: dict[str, dict[K, R]],
    rows: Iterable[R],
    key: Callable[[R], K],
) -> None:
    """Upsert ``rows`` by grain key (last write wins), copy-on-write per work.

    Rows group by their own ``key[0]`` (``row.work_id``), which may differ
    from the bundle's Work, exactly as the flat map keyed them. Each touched
    work gets a new inner dict holding its current rows plus these; the
    current inner dict is never mutated, because a rollback snapshot may
    share it.
    """
    updated: dict[str, dict[K, R]] = {}
    for row in rows:
        row_key = key(row)
        work_id = row_key[0]
        work_rows = updated.get(work_id)
        if work_rows is None:
            work_rows = updated[work_id] = dict(table.get(work_id, {}))
        work_rows[row_key] = row
    table.update(updated)


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


def _barrier_blocks_active_insert(
    barrier_date: date | None, incoming: Work
) -> CanonicalUpsertOutcome | None:
    """Block insert when an unknown-work deletion barrier precedes the observation."""
    if barrier_date is None:
        return None
    if incoming.lineage.activity_state is CanonicalActivityState.DELETED:
        return None
    incoming_date = incoming.lineage.source_updated_date
    if incoming_date is None or incoming_date <= barrier_date:
        return CanonicalUpsertOutcome.STALE
    raise RestoreRequiredError(
        f"work {incoming.work_id} has unknown-work deletion barrier at "
        f"{barrier_date.isoformat()}; newer active ingestion requires "
        "explicit restore reconciliation (RESTORE_REQUIRED)"
    )
