"""Supported / deferred / unmapped OpenAlex Work field catalog.

Evidence basis: public OpenAlex Works API documentation and repository Step 08
notes. Real payload profiling remains pending public network access; this catalog
must not invent entities beyond documented public schema.
"""

from __future__ import annotations

from enum import StrEnum


class FieldSupport(StrEnum):
    SUPPORTED = "SUPPORTED"
    DEFERRED = "DEFERRED"
    UNMAPPED = "UNMAPPED"
    SOURCE_ONLY = "SOURCE_ONLY"


FIELD_CATALOG: dict[str, FieldSupport] = {
    # Supported into canonical entities/relationships
    "id": FieldSupport.SUPPORTED,
    "doi": FieldSupport.SUPPORTED,
    "title": FieldSupport.SUPPORTED,
    "display_name": FieldSupport.SUPPORTED,  # fallback title
    "publication_year": FieldSupport.SUPPORTED,
    "publication_date": FieldSupport.SUPPORTED,
    "type": FieldSupport.SUPPORTED,
    "language": FieldSupport.SUPPORTED,
    "cited_by_count": FieldSupport.SUPPORTED,
    "referenced_works_count": FieldSupport.SUPPORTED,
    "open_access": FieldSupport.SUPPORTED,
    "primary_location": FieldSupport.SUPPORTED,
    "locations": FieldSupport.SUPPORTED,
    "authorships": FieldSupport.SUPPORTED,
    "topics": FieldSupport.SUPPORTED,
    "keywords": FieldSupport.SUPPORTED,
    "mesh": FieldSupport.SUPPORTED,
    "referenced_works": FieldSupport.SUPPORTED,
    "grants": FieldSupport.SUPPORTED,
    "created_date": FieldSupport.SUPPORTED,
    "updated_date": FieldSupport.SUPPORTED,
    # Deferred: documented publicly but not modeled in Step 11
    "concepts": FieldSupport.DEFERRED,  # superseded by topics for new modeling
    "related_works": FieldSupport.DEFERRED,
    "counts_by_year": FieldSupport.DEFERRED,
    "abstract_inverted_index": FieldSupport.DEFERRED,
    "biblio": FieldSupport.DEFERRED,
    "ids": FieldSupport.DEFERRED,  # pmid/pmcid/mag deferred
    "best_oa_location": FieldSupport.DEFERRED,
    "corresponding_author_ids": FieldSupport.DEFERRED,
    "corresponding_institution_ids": FieldSupport.DEFERRED,
    "apc_list": FieldSupport.DEFERRED,
    "apc_paid": FieldSupport.DEFERRED,
    "sustainable_development_goals": FieldSupport.DEFERRED,
    "awards": FieldSupport.DEFERRED,
    # Source-only / not copied into canonical tables
    "is_retracted": FieldSupport.SOURCE_ONLY,
    "is_paratext": FieldSupport.SOURCE_ONLY,
    "has_fulltext": FieldSupport.SOURCE_ONLY,
}


UNSUPPORTED_REASON: dict[str, str] = {
    "concepts": "OpenAlex topics replace concepts for new canonical modeling.",
    "abstract_inverted_index": "Large dynamic-key map; deferred pending payload evidence.",
    "ids": "Secondary identifiers (PMID/PMCID/MAG) deferred; DOI/OpenAlex id supported.",
    "related_works": "Recommendation edges deferred; references are modeled.",
}
