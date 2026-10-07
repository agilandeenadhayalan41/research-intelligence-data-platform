"""Authoritative consumer capability registry (Step 18 / #26)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from research_platform.service.contracts import (
    JournalMetricsRequest,
    PublisherSummaryRequest,
    PublisherTopicAnalyticsRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import (
    DATA_SERVICE_CONTRACT_VERSION,
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ConsistencySemantics,
    JournalMetricsRecord,
    PaginationMode,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    SettingsModel,
    WorkMetadataRecord,
    WorkloadClass,
)


class CapabilityContract(SettingsModel):
    """One storage-independent consumer capability."""

    capability_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    request_model: str = Field(min_length=1, max_length=128)
    response_model: str = Field(min_length=1, max_length=128)
    result_grain: str = Field(min_length=1, max_length=256)
    source_gold_marts: tuple[str, ...] = Field(min_length=1)
    source_pattern_ids: tuple[str, ...] = ()
    pagination_mode: PaginationMode
    default_limit: int | None = Field(default=None, ge=1, le=MAX_PAGE_LIMIT)
    max_limit: int | None = Field(default=None, ge=1, le=MAX_PAGE_LIMIT)
    supported_filters: tuple[str, ...] = ()
    sort_keys: tuple[str, ...] = ()
    freshness_semantics: str = Field(min_length=1, max_length=512)
    active_record_semantics: str = Field(min_length=1, max_length=512)
    null_semantics: str = Field(min_length=1, max_length=1024)
    workload_class: WorkloadClass
    consistency_semantics: ConsistencySemantics = ConsistencySemantics.PUBLISHED_SNAPSHOT
    notes: str = Field(default="", max_length=1024)


def capability_contracts() -> tuple[CapabilityContract, ...]:
    """Authoritative inventory of consumer-facing Data Service capabilities."""
    freshness = (
        "Repository may supply KNOWN freshness evidence "
        "(generated_at / source_max_updated_at / as_of_run_id) or UNKNOWN. "
        "No real-time freshness claim."
    )
    active_only = (
        "ACTIVE Works / aggregates only. DELETED canonical Works are not "
        "returned as normal consumer records. Tombstone internals are not exposed."
    )
    return (
        CapabilityContract(
            capability_id="work_metadata_lookup",
            name="Work metadata lookup",
            description=(
                "Point lookup of one ACTIVE Work by work_id or DOI from the "
                "Step-16 research_discovery projection."
            ),
            request_model=WorkMetadataRequest.__name__,
            response_model=WorkMetadataRecord.__name__,
            result_grain="zero or one ACTIVE Work",
            source_gold_marts=("research_discovery",),
            source_pattern_ids=("doi-work-lookup", "openalex-work-id-lookup"),
            pagination_mode=PaginationMode.NONE,
            default_limit=None,
            max_limit=None,
            supported_filters=("work_id", "doi"),
            sort_keys=(),
            freshness_semantics=freshness,
            active_record_semantics=active_only,
            null_semantics=(
                "Missing scalar fields remain NULL. "
                "Absent relationships return [] rather than NULL."
            ),
            workload_class=WorkloadClass.POINT_LOOKUP,
            notes=(
                "Exactly one of work_id or doi. NOT_FOUND for missing/inactive. "
                "Does not invent DOI canonicalization beyond existing contracts."
            ),
        ),
        CapabilityContract(
            capability_id="research_discovery",
            name="Research discovery",
            description=(
                "Bounded paged discovery over ACTIVE Works using the fan-out-safe "
                "Step-16 research_discovery projection (pre-aggregated arrays)."
            ),
            request_model=ResearchDiscoveryRequest.__name__,
            response_model=WorkMetadataRecord.__name__,
            result_grain="one ACTIVE Work per item",
            source_gold_marts=("research_discovery",),
            source_pattern_ids=("doi-work-lookup", "openalex-work-id-lookup"),
            pagination_mode=PaginationMode.CURSOR,
            default_limit=DEFAULT_PAGE_LIMIT,
            max_limit=MAX_PAGE_LIMIT,
            supported_filters=(
                "publication_year_from",
                "publication_year_to",
                "primary_publisher_id",
                "primary_source_id",
                "work_type",
                "language",
                "oa_status",
                "topic_id",
                "institution_id",
                "author_id",
            ),
            sort_keys=("work_id",),
            freshness_semantics=freshness,
            active_record_semantics=active_only,
            null_semantics=(
                "Same as work metadata. Year-bounded filters exclude NULL-year "
                "rows; without a year filter NULL-year rows may appear."
            ),
            workload_class=WorkloadClass.PAGED_DISCOVERY,
            notes=(
                "Sort: work_id ASC. No ISSN/eISSN filter. "
                "Do not reconstruct by joining relationship tables in the service."
            ),
        ),
        CapabilityContract(
            capability_id="journal_metrics",
            name="Journal metrics",
            description=(
                "Source-level unique author and ACTIVE work counts from "
                "journal_author_stats. source_id is not ISSN."
            ),
            request_model=JournalMetricsRequest.__name__,
            response_model=JournalMetricsRecord.__name__,
            result_grain="one row per source_id",
            source_gold_marts=("journal_author_stats",),
            source_pattern_ids=("unique-authors-per-journal",),
            pagination_mode=PaginationMode.NONE,
            supported_filters=("source_id",),
            sort_keys=(),
            freshness_semantics=freshness,
            active_record_semantics=(
                "Metrics reflect ACTIVE contributions only. "
                "NOT_FOUND when no current ACTIVE contribution exists for source_id."
            ),
            null_semantics="Counts are non-negative integers; mathematical zero is 0.",
            workload_class=WorkloadClass.AGGREGATE_LOOKUP,
            notes="Do not invent ISSN/eISSN journal identifiers.",
        ),
        CapabilityContract(
            capability_id="publisher_summary",
            name="Publisher summary",
            description=(
                "Publisher-level unique author and ACTIVE work counts from "
                "publisher_author_stats (independent author aggregation)."
            ),
            request_model=PublisherSummaryRequest.__name__,
            response_model=PublisherSummaryRecord.__name__,
            result_grain="one row per publisher_id",
            source_gold_marts=("publisher_author_stats",),
            source_pattern_ids=("unique-authors-per-publisher",),
            pagination_mode=PaginationMode.NONE,
            supported_filters=("publisher_id",),
            sort_keys=(),
            freshness_semantics=freshness,
            active_record_semantics=(
                "ACTIVE aggregates only. NOT_FOUND when publisher has no "
                "current ACTIVE contribution."
            ),
            null_semantics="Counts are non-negative integers; mathematical zero is 0.",
            workload_class=WorkloadClass.AGGREGATE_LOOKUP,
            notes="Preserve Step-16 independent author aggregation; no fan-out joins.",
        ),
        CapabilityContract(
            capability_id="publisher_topic_analytics",
            name="Publisher topic analytics",
            description=(
                "Paged publisher + topic + publication_year ACTIVE work counts "
                "from publisher_topic_year_stats."
            ),
            request_model=PublisherTopicAnalyticsRequest.__name__,
            response_model=PublisherTopicMetricRecord.__name__,
            result_grain="publisher_id + topic_id + publication_year",
            source_gold_marts=("publisher_topic_year_stats",),
            source_pattern_ids=("publisher-topic-counts",),
            pagination_mode=PaginationMode.CURSOR,
            default_limit=DEFAULT_PAGE_LIMIT,
            max_limit=MAX_PAGE_LIMIT,
            supported_filters=(
                "publisher_id",
                "publication_year_from",
                "publication_year_to",
                "topic_id",
            ),
            sort_keys=("publication_year", "topic_id"),
            freshness_semantics=freshness,
            active_record_semantics="ACTIVE work counts only; no author/location joins.",
            null_semantics=(
                "NULL publication_year is an explicit bucket when no year filter "
                "is applied. Bounded year ranges exclude NULL-year rows. "
                "Do not invent year 0."
            ),
            workload_class=WorkloadClass.PAGED_ANALYTICS,
            notes=(
                "Sort: publication_year ASC NULLS LAST, topic_id ASC. "
                "License dimension is a separate mart/capability if exposed later; "
                "not part of this grain."
            ),
        ),
    )


def load_capability_registry() -> tuple[CapabilityContract, ...]:
    caps = capability_contracts()
    ids = [c.capability_id for c in caps]
    if len(ids) != len(set(ids)):
        raise ValueError("capability_id values must be unique")
    return caps


def get_capability(capability_id: str) -> CapabilityContract:
    for cap in load_capability_registry():
        if cap.capability_id == capability_id:
            return cap
    raise KeyError(f"unknown capability_id: {capability_id}")


def serialize_capability_registry(
    caps: tuple[CapabilityContract, ...] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic JSON-serializable registry snapshot."""
    items = caps if caps is not None else load_capability_registry()
    return [
        {
            "contract_version": DATA_SERVICE_CONTRACT_VERSION,
            **c.model_dump(mode="json"),
        }
        for c in sorted(items, key=lambda x: x.capability_id)
    ]
