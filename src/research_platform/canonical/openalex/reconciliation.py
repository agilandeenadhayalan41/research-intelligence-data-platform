"""Deterministic reconciliation for shared canonical entities across Works."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from research_platform.canonical.openalex.models import (
    Author,
    CanonicalLineage,
    Funder,
    Institution,
    Publisher,
    Source,
    Topic,
)

SharedEntity = Author | Institution | Source | Publisher | Topic | Funder
T = TypeVar("T", Author, Institution, Source, Publisher, Topic, Funder)


class EntityReconciliation(StrEnum):
    """Outcome of reconciling two rows that share the same entity id."""

    IDENTICAL = "IDENTICAL"
    ENRICH = "ENRICH"
    NEWER = "NEWER"
    STALE = "STALE"
    CONFLICT = "CONFLICT"


_ID_FIELDS = {
    Author: "author_id",
    Institution: "institution_id",
    Source: "source_id",
    Publisher: "publisher_id",
    Topic: "topic_id",
    Funder: "funder_id",
}


def _lineage_precedence(
    existing: CanonicalLineage, incoming: CanonicalLineage
) -> EntityReconciliation:
    existing_date = existing.source_updated_date
    incoming_date = incoming.source_updated_date
    if existing_date is not None and incoming_date is not None:
        if incoming_date > existing_date:
            return EntityReconciliation.NEWER
        if incoming_date < existing_date:
            return EntityReconciliation.STALE
        return EntityReconciliation.CONFLICT
    if incoming_date is not None and existing_date is None:
        return EntityReconciliation.NEWER
    if incoming_date is None and existing_date is not None:
        return EntityReconciliation.STALE
    return EntityReconciliation.CONFLICT


def reconcile_shared_entity(existing: T, incoming: T) -> EntityReconciliation:
    """Reconcile two mapped rows for the same shared OpenAlex entity id.

    Payload fields (excluding lineage) are compared first:

    - identical values → ``IDENTICAL``
    - complementary null/non-null with no non-null conflicts → ``ENRICH``
      (prefer keeping non-null values from either side; do not overwrite richer
      data with a poorer embedded summary)
    - conflicting non-null values → lineage/version precedence:
      ``NEWER`` / ``STALE`` / ``CONFLICT``

    Work-level version precedence remains separate in ``compare_work_versions``.
    """
    if type(existing) is not type(incoming):
        raise TypeError("shared-entity reconciliation requires the same entity type")
    id_field = _ID_FIELDS[type(existing)]
    existing_id = getattr(existing, id_field)
    incoming_id = getattr(incoming, id_field)
    if existing_id != incoming_id:
        raise ValueError(f"entity id mismatch for {id_field}")

    existing_payload = existing.model_dump(exclude={"lineage"})
    incoming_payload = incoming.model_dump(exclude={"lineage"})
    if existing_payload == incoming_payload:
        return EntityReconciliation.IDENTICAL

    has_enrichment = False
    has_conflict = False
    for key, existing_value in existing_payload.items():
        incoming_value = incoming_payload[key]
        if existing_value == incoming_value:
            continue
        if existing_value is None and incoming_value is not None:
            has_enrichment = True
            continue
        if existing_value is not None and incoming_value is None:
            # Incoming is poorer for this field; not a conflict by itself.
            has_enrichment = True
            continue
        has_conflict = True

    if has_conflict:
        return _lineage_precedence(existing.lineage, incoming.lineage)
    if has_enrichment:
        return EntityReconciliation.ENRICH
    return EntityReconciliation.IDENTICAL


def merge_shared_entity(existing: T, incoming: T) -> T:
    """Merge complementary null/non-null fields for an ``ENRICH`` outcome.

    Non-null conflicts raise ``ValueError``; callers must not merge ``CONFLICT``
    rows. Lineage is taken from the side that contributes any newly filled
    non-null field when possible; otherwise from ``existing``.
    """
    outcome = reconcile_shared_entity(existing, incoming)
    if outcome is EntityReconciliation.IDENTICAL:
        return existing
    if outcome is not EntityReconciliation.ENRICH:
        raise ValueError(f"cannot merge shared entity for outcome {outcome.value}")

    existing_payload = existing.model_dump(mode="python")
    incoming_payload = incoming.model_dump(mode="python")
    merged = dict(existing_payload)
    used_incoming_lineage = False
    for key, existing_value in existing_payload.items():
        if key == "lineage":
            continue
        incoming_value = incoming_payload[key]
        if existing_value is None and incoming_value is not None:
            merged[key] = incoming_value
            used_incoming_lineage = True
        elif (
            existing_value is not None
            and incoming_value is not None
            and existing_value != incoming_value
        ):
            raise ValueError("cannot merge conflicting non-null entity fields")
    if used_incoming_lineage:
        merged["lineage"] = incoming.lineage
    return type(existing).model_validate(merged)
