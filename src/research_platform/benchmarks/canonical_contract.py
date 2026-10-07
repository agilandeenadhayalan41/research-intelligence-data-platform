"""Canonical entity/field contract used to validate query-pattern registries.

Keeps Step 14 patterns aligned with the Step 11 logical model without parsing
SQL. Field names are the analytical/logical identifiers used in registry query
shapes (including denormalized ``works.activity_state`` from lineage).
"""

from __future__ import annotations

import re

from research_platform.benchmarks.models import (
    Placement,
    QueryCategory,
    QueryPattern,
    QueryPatternRegistry,
)

CANONICAL_ENTITIES: frozenset[str] = frozenset(
    {
        "works",
        "authors",
        "institutions",
        "sources",
        "publishers",
        "topics",
        "funders",
    }
)

CANONICAL_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "work_authors",
        "work_author_institutions",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_locations",
        "work_grants",
        "work_mesh",
    }
)

# Logical fields available to implementable registry query shapes today.
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        "works.work_id",
        "works.doi",
        "works.activity_state",
        "works.publication_year",
        "works.oa_status",
        "works.primary_publisher_id",
        "works.primary_source_id",
        "authors.author_id",
        "institutions.institution_id",
        "sources.source_id",
        "sources.issn_l",
        "publishers.publisher_id",
        "topics.topic_id",
        "funders.funder_id",
        "work_authors.work_id",
        "work_authors.author_id",
        "work_author_institutions.work_id",
        "work_author_institutions.institution_id",
        "work_topics.work_id",
        "work_topics.topic_id",
        "work_keywords.work_id",
        "work_references.work_id",
        "work_references.reference_index",
        "work_references.referenced_work_id",
        "work_references.reference_status",
        "work_locations.work_id",
        "work_locations.source_id",
        "work_locations.license",
        "work_locations.is_primary",
        "work_grants.work_id",
        "work_mesh.work_id",
    }
)

# Fragments that must not appear in implementable query shapes.
_FORBIDDEN_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsources\.issn\b", re.IGNORECASE),
    re.compile(r"\bsources\.eissn\b", re.IGNORECASE),
    re.compile(r"\bMIN\s*\(\s*license\s*\)", re.IGNORECASE),
    re.compile(r"\bMAX\s*\(\s*license\s*\)", re.IGNORECASE),
)


def assert_registry_matches_canonical_contract(
    registry: QueryPatternRegistry,
) -> None:
    """Validate entities, relationships, fields, candidates, and shape hygiene."""
    candidate_ids = {item.candidate_id for item in registry.materialization_candidates}
    for pattern in registry.patterns:
        _assert_entities(pattern)
        _assert_relationships(pattern)
        _assert_required_fields(pattern)
        _assert_materialization_candidate(pattern, candidate_ids)
        _assert_shape_hygiene(pattern)
        _assert_issn_gap_policy(pattern)


def _assert_entities(pattern: QueryPattern) -> None:
    unknown = set(pattern.relevant_entities) - CANONICAL_ENTITIES
    if unknown:
        raise ValueError(
            f"{pattern.pattern_id}: unknown entities {sorted(unknown)}; "
            f"allowed={sorted(CANONICAL_ENTITIES)}"
        )


def _assert_relationships(pattern: QueryPattern) -> None:
    names = {item.relationship for item in pattern.relevant_relationships}
    unknown = names - CANONICAL_RELATIONSHIPS
    if unknown:
        raise ValueError(
            f"{pattern.pattern_id}: unknown relationships {sorted(unknown)}; "
            f"allowed={sorted(CANONICAL_RELATIONSHIPS)}"
        )


def _assert_required_fields(pattern: QueryPattern) -> None:
    unknown = set(pattern.required_fields) - CANONICAL_FIELDS
    if unknown:
        raise ValueError(
            f"{pattern.pattern_id}: required_fields not in canonical contract: "
            f"{sorted(unknown)}"
        )


def _assert_materialization_candidate(
    pattern: QueryPattern, candidate_ids: set[str]
) -> None:
    candidate = pattern.candidate_materialization
    if candidate is None:
        return
    if candidate not in candidate_ids:
        raise ValueError(
            f"{pattern.pattern_id}: candidate_materialization={candidate!r} "
            "does not exist in materialization_candidates"
        )


def _assert_shape_hygiene(pattern: QueryPattern) -> None:
    # Notes/rationale may document forbidden columns; shapes/filters must not use them.
    text = "\n".join((pattern.logical_query_shape, "\n".join(pattern.filters)))
    for compiled in _FORBIDDEN_SHAPE_PATTERNS:
        if compiled.search(text):
            raise ValueError(
                f"{pattern.pattern_id}: forbidden fragment {compiled.pattern!r} "
                "in query shape/filters (canonical gap or unsafe aggregation)"
            )


def _assert_issn_gap_policy(pattern: QueryPattern) -> None:
    if pattern.category is not QueryCategory.ISSN_LOOKUP:
        return
    if pattern.placement is not Placement.UNRESOLVED:
        raise ValueError(
            "ISSN_LOOKUP must be UNRESOLVED until canonical Source exposes "
            "ISSN/eISSN identifiers (currently only issn_l exists)"
        )
    if pattern.required_fields:
        raise ValueError(
            "ISSN_LOOKUP must not declare required_fields until a canonical "
            "source-identifier mapping exists; do not substitute issn_l"
        )
