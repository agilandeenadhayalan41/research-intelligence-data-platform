"""PyArrow schemas for portable OpenAlex canonical tables.

These schemas are the interchange contract. They are independent of BigQuery,
PostgreSQL, and GCS physical layout decisions.

Flattened lineage uses ``lineage_source_updated_date`` so it never collides with
entity fields such as ``Work.source_updated_date``. Nested Pydantic models keep
``CanonicalLineage.source_updated_date`` unchanged.
"""

from __future__ import annotations

import pyarrow as pa

_LINEAGE_FIELDS: list[tuple[str, pa.DataType, bool]] = [
    ("source_asset_id", pa.string(), False),
    ("source_checksum_sha256", pa.string(), False),
    ("lineage_source_updated_date", pa.date32(), True),
    ("run_id", pa.string(), False),
    ("processed_at", pa.timestamp("us", tz="UTC"), False),
    ("activity_state", pa.string(), False),
    ("deleted_at", pa.timestamp("us", tz="UTC"), True),
]


def _with_lineage(fields: list[tuple[str, pa.DataType, bool]]) -> pa.Schema:
    ordered = [*fields, *_LINEAGE_FIELDS]
    return pa.schema([pa.field(name, dtype, nullable=nullable) for name, dtype, nullable in ordered])


WORKS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("work_id_url", pa.string(), False),
        ("doi", pa.string(), True),
        ("title", pa.string(), True),
        ("publication_year", pa.int32(), True),
        ("publication_date", pa.date32(), True),
        ("work_type", pa.string(), True),
        ("language", pa.string(), True),
        ("cited_by_count", pa.int64(), True),
        ("referenced_works_count", pa.int64(), True),
        ("is_oa", pa.bool_(), True),
        ("oa_status", pa.string(), True),
        ("primary_source_id", pa.string(), True),
        ("primary_publisher_id", pa.string(), True),
        ("source_created_date", pa.date32(), True),
        ("source_updated_date", pa.date32(), True),
        ("authorships_presence", pa.string(), False),
        ("topics_presence", pa.string(), False),
        ("keywords_presence", pa.string(), False),
        ("mesh_presence", pa.string(), False),
        ("referenced_works_presence", pa.string(), False),
        ("locations_presence", pa.string(), False),
        ("grants_presence", pa.string(), False),
    ]
)

AUTHORS_SCHEMA = _with_lineage(
    [
        ("author_id", pa.string(), False),
        ("author_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
        ("orcid", pa.string(), True),
    ]
)

INSTITUTIONS_SCHEMA = _with_lineage(
    [
        ("institution_id", pa.string(), False),
        ("institution_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
        ("ror", pa.string(), True),
        ("country_code", pa.string(), True),
        ("institution_type", pa.string(), True),
    ]
)

SOURCES_SCHEMA = _with_lineage(
    [
        ("source_id", pa.string(), False),
        ("source_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
        ("source_type", pa.string(), True),
        ("issn_l", pa.string(), True),
        ("host_publisher_id", pa.string(), True),
    ]
)

PUBLISHERS_SCHEMA = _with_lineage(
    [
        ("publisher_id", pa.string(), False),
        ("publisher_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
    ]
)

TOPICS_SCHEMA = _with_lineage(
    [
        ("topic_id", pa.string(), False),
        ("topic_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
    ]
)

FUNDERS_SCHEMA = _with_lineage(
    [
        ("funder_id", pa.string(), False),
        ("funder_id_url", pa.string(), False),
        ("display_name", pa.string(), True),
    ]
)

WORK_AUTHORS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("authorship_index", pa.int32(), False),
        ("author_id", pa.string(), True),
        ("author_position", pa.string(), True),
        ("is_corresponding", pa.bool_(), True),
        ("raw_author_name", pa.string(), True),
    ]
)

WORK_AUTHOR_INSTITUTIONS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("authorship_index", pa.int32(), False),
        ("institution_index", pa.int32(), False),
        ("institution_id", pa.string(), True),
    ]
)

WORK_TOPICS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("topic_id", pa.string(), False),
        ("score", pa.float64(), True),
    ]
)

WORK_KEYWORDS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("keyword_id", pa.string(), False),
        ("display_name", pa.string(), True),
        ("score", pa.float64(), True),
    ]
)

WORK_REFERENCES_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("reference_index", pa.int32(), False),
        ("referenced_work_id", pa.string(), True),
        ("raw_reference", pa.string(), True),
        ("reference_status", pa.string(), False),
    ]
)

WORK_MESH_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("mesh_index", pa.int32(), False),
        ("descriptor_ui", pa.string(), False),
        ("descriptor_name", pa.string(), True),
        ("qualifier_ui", pa.string(), True),
        ("qualifier_name", pa.string(), True),
        ("is_major_topic", pa.bool_(), True),
    ]
)

WORK_LOCATIONS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("location_index", pa.int32(), False),
        ("location_origin", pa.string(), False),
        ("source_id", pa.string(), True),
        ("is_oa", pa.bool_(), True),
        ("landing_page_url", pa.string(), True),
        ("pdf_url", pa.string(), True),
        ("license", pa.string(), True),
        ("version", pa.string(), True),
        ("is_primary", pa.bool_(), False),
    ]
)

WORK_GRANTS_SCHEMA = _with_lineage(
    [
        ("work_id", pa.string(), False),
        ("grant_index", pa.int32(), False),
        ("funder_id", pa.string(), True),
        ("award_id", pa.string(), True),
        ("funder_display_name", pa.string(), True),
    ]
)

CANONICAL_SCHEMAS: dict[str, pa.Schema] = {
    "works": WORKS_SCHEMA,
    "authors": AUTHORS_SCHEMA,
    "institutions": INSTITUTIONS_SCHEMA,
    "sources": SOURCES_SCHEMA,
    "publishers": PUBLISHERS_SCHEMA,
    "topics": TOPICS_SCHEMA,
    "funders": FUNDERS_SCHEMA,
    "work_authors": WORK_AUTHORS_SCHEMA,
    "work_author_institutions": WORK_AUTHOR_INSTITUTIONS_SCHEMA,
    "work_topics": WORK_TOPICS_SCHEMA,
    "work_keywords": WORK_KEYWORDS_SCHEMA,
    "work_references": WORK_REFERENCES_SCHEMA,
    "work_mesh": WORK_MESH_SCHEMA,
    "work_locations": WORK_LOCATIONS_SCHEMA,
    "work_grants": WORK_GRANTS_SCHEMA,
}
