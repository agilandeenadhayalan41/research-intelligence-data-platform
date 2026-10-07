"""Static validation of Data Service contracts against Step 14/16 registries."""

from __future__ import annotations

from research_platform.analytics.gold.registry import load_gold_registry
from research_platform.benchmarks.registry import load_query_pattern_registry
from research_platform.service.models import (
    DATA_SERVICE_CONTRACT_VERSION,
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    PaginationMode,
)
from research_platform.service.registry import load_capability_registry
from research_platform.service.service import assert_no_raw_sql_consumer_methods


FORBIDDEN_FILTER_TOKENS = frozenset(
    {
        "issn",
        "eissn",
        "issn_l",
        "print_issn",
        "electronic_issn",
    }
)


def validate_static_data_service_contracts() -> None:
    """Fail fast on registry/contract inconsistencies."""
    if DATA_SERVICE_CONTRACT_VERSION != "data-service-contract-v1":
        raise ValueError("DATA_SERVICE_CONTRACT_VERSION must be data-service-contract-v1")
    if DEFAULT_PAGE_LIMIT < 1 or DEFAULT_PAGE_LIMIT > MAX_PAGE_LIMIT:
        raise ValueError("DEFAULT_PAGE_LIMIT out of bounds")

    caps = load_capability_registry()
    ids = [c.capability_id for c in caps]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate capability_id")

    gold_ids = {m.mart_id for m in load_gold_registry()}
    pattern_ids = {p.pattern_id for p in load_query_pattern_registry().patterns}

    required = {
        "work_metadata_lookup",
        "research_discovery",
        "journal_metrics",
        "publisher_summary",
        "publisher_topic_analytics",
    }
    if not required.issubset(set(ids)):
        raise ValueError(f"missing required capabilities: {sorted(required - set(ids))}")

    for cap in caps:
        for mart in cap.source_gold_marts:
            if mart not in gold_ids:
                raise ValueError(
                    f"{cap.capability_id} source_gold_mart {mart!r} not in Step-16 registry"
                )
        for pid in cap.source_pattern_ids:
            if pid not in pattern_ids:
                raise ValueError(
                    f"{cap.capability_id} source_pattern_id {pid!r} not in Step-14 registry"
                )
        if cap.pagination_mode is PaginationMode.CURSOR:
            if not cap.sort_keys:
                raise ValueError(f"{cap.capability_id} CURSOR mode requires sort_keys")
            if cap.default_limit is None or cap.max_limit is None:
                raise ValueError(f"{cap.capability_id} CURSOR mode requires limits")
            if cap.default_limit > cap.max_limit:
                raise ValueError(f"{cap.capability_id} default_limit > max_limit")
        if cap.pagination_mode is PaginationMode.NONE:
            if cap.default_limit is not None or cap.max_limit is not None:
                raise ValueError(
                    f"{cap.capability_id} point lookup must not declare page limits"
                )
        for filt in cap.supported_filters:
            token = filt.lower()
            if token in FORBIDDEN_FILTER_TOKENS or any(
                t in token for t in FORBIDDEN_FILTER_TOKENS
            ):
                raise ValueError(
                    f"{cap.capability_id} invents unsupported ISSN/eISSN filter {filt!r}"
                )
        if "issn" in cap.capability_id or "eissn" in cap.capability_id:
            raise ValueError(f"unsupported ISSN/eISSN capability: {cap.capability_id}")

    assert_no_raw_sql_consumer_methods()
