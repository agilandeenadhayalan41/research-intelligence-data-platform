"""Unit tests for Step 18 Data Service domain layer (SEMANTIC_ONLY)."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from pydantic import ValidationError

from research_platform.analytics.gold.registry import load_gold_registry
from research_platform.benchmarks.registry import load_query_pattern_registry
from research_platform.service.contracts import (
    JournalMetricsRequest,
    PageRequest,
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
    FreshnessMetadata,
    FreshnessStatus,
    JournalMetricsRecord,
    PublisherSummaryRecord,
    ServiceErrorCode,
    ServiceErrorException,
    WorkMetadataRecord,
)
from research_platform.service.pagination import decode_cursor, encode_cursor
from research_platform.service.registry import (
    load_capability_registry,
    serialize_capability_registry,
)
from research_platform.service.repository import (
    InMemoryConsumerRepository,
    build_reference_fixture,
)
from research_platform.service.service import (
    DataService,
    assert_no_raw_sql_consumer_methods,
)
from research_platform.service.validation import validate_static_data_service_contracts


# ---------------------------------------------------------------------------
# Contract / registry
# ---------------------------------------------------------------------------


def test_stable_contract_version() -> None:
    assert DATA_SERVICE_CONTRACT_VERSION == "data-service-contract-v1"
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(work_id="W001"))
    assert resp.contract_version == "data-service-contract-v1"


def test_unique_capability_ids() -> None:
    caps = load_capability_registry()
    ids = [c.capability_id for c in caps]
    assert len(ids) == len(set(ids))
    assert set(ids) == {
        "work_metadata_lookup",
        "research_discovery",
        "journal_metrics",
        "publisher_summary",
        "publisher_topic_analytics",
    }


def test_deterministic_registry_serialization() -> None:
    a = serialize_capability_registry()
    b = serialize_capability_registry()
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ids = [row["capability_id"] for row in a]
    assert ids == sorted(ids)


def test_source_marts_exist_in_step16() -> None:
    gold = {m.mart_id for m in load_gold_registry()}
    for cap in load_capability_registry():
        for mart in cap.source_gold_marts:
            assert mart in gold, f"{cap.capability_id} -> {mart}"


def test_step14_pattern_references_valid() -> None:
    patterns = {p.pattern_id for p in load_query_pattern_registry().patterns}
    for cap in load_capability_registry():
        for pid in cap.source_pattern_ids:
            assert pid in patterns, f"{cap.capability_id} -> {pid}"


def test_no_issn_eissn_capability() -> None:
    for cap in load_capability_registry():
        assert "issn" not in cap.capability_id.lower()
        assert "eissn" not in cap.capability_id.lower()
        for filt in cap.supported_filters:
            token = filt.lower()
            assert "issn" not in token
            assert "eissn" not in token
        for mart in cap.source_gold_marts:
            assert "issn" not in mart.lower()


def test_no_raw_sql_consumer_capability() -> None:
    assert_no_raw_sql_consumer_methods()
    for name, method in inspect.getmembers(DataService, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        params = set(inspect.signature(method).parameters)
        assert not params & {"sql", "query_text", "raw_sql", "query"}


def test_validate_static_contracts() -> None:
    validate_static_data_service_contracts()


def test_gold_mapping_table() -> None:
    mapping = {
        c.capability_id: c.source_gold_marts for c in load_capability_registry()
    }
    assert mapping["work_metadata_lookup"] == ("research_discovery",)
    assert mapping["research_discovery"] == ("research_discovery",)
    assert mapping["journal_metrics"] == ("journal_author_stats",)
    assert mapping["publisher_summary"] == ("publisher_author_stats",)
    assert mapping["publisher_topic_analytics"] == ("publisher_topic_year_stats",)


def test_pattern_mapping_table() -> None:
    mapping = {
        c.capability_id: c.source_pattern_ids for c in load_capability_registry()
    }
    assert "doi-work-lookup" in mapping["work_metadata_lookup"]
    assert "openalex-work-id-lookup" in mapping["work_metadata_lookup"]
    assert mapping["journal_metrics"] == ("unique-authors-per-journal",)
    assert mapping["publisher_summary"] == ("unique-authors-per-publisher",)
    assert mapping["publisher_topic_analytics"] == ("publisher-topic-counts",)


# ---------------------------------------------------------------------------
# Work metadata
# ---------------------------------------------------------------------------


def test_lookup_by_work_id() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(work_id="W001"))
    assert resp.capability_id == "work_metadata_lookup"
    assert resp.data.work_id == "W001"
    assert resp.data.doi == "10.1000/aaa"
    assert resp.consistency is ConsistencySemantics.PUBLISHED_SNAPSHOT


def test_lookup_by_doi() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(doi="10.1000/aaa"))
    assert resp.data.work_id == "W001"


def test_both_identifiers_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkMetadataRequest(work_id="W001", doi="10.1000/aaa")


def test_neither_identifier_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkMetadataRequest()


def test_empty_identifier_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkMetadataRequest(work_id="   ")
    with pytest.raises(ValidationError):
        WorkMetadataRequest(doi="")


def test_active_work_returned() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(work_id="W002"))
    assert resp.data.doi is None
    assert resp.data.author_ids == ()
    assert resp.data.topic_ids == ()
    assert resp.data.institution_ids == ()
    assert resp.data.is_oa is None
    assert resp.data.oa_status is None


def test_missing_work_not_found() -> None:
    svc = DataService(build_reference_fixture())
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W_MISSING"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND
    assert "sql" not in ei.value.error.message.lower()


def test_deleted_work_not_exposed() -> None:
    svc = DataService(build_reference_fixture())
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W999"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND


def test_empty_arrays_remain_empty() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(work_id="W002"))
    assert resp.data.author_ids == ()
    assert isinstance(resp.data.author_ids, tuple)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_bounded_limit() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.search_research(
        ResearchDiscoveryRequest(page=PageRequest(limit=2))
    )
    assert len(resp.data.items) == 2
    assert resp.data.page.limit == 2
    assert resp.data.page.has_more is True
    assert resp.data.page.next_cursor is not None


def test_discovery_deterministic_sorting() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.search_research(
        ResearchDiscoveryRequest(page=PageRequest(limit=100))
    )
    ids = [w.work_id for w in resp.data.items]
    assert ids == sorted(ids)
    assert "W999" not in ids


def test_discovery_pagination_no_duplicates() -> None:
    svc = DataService(build_reference_fixture())
    page1 = svc.search_research(ResearchDiscoveryRequest(page=PageRequest(limit=2)))
    assert page1.data.page.next_cursor
    page2 = svc.search_research(
        ResearchDiscoveryRequest(
            page=PageRequest(limit=2, cursor=page1.data.page.next_cursor)
        )
    )
    ids1 = {w.work_id for w in page1.data.items}
    ids2 = {w.work_id for w in page2.data.items}
    assert ids1.isdisjoint(ids2)
    assert len(ids1) == 2


def test_malformed_cursor_rejected() -> None:
    svc = DataService(build_reference_fixture())
    with pytest.raises(ServiceErrorException) as ei:
        svc.search_research(
            ResearchDiscoveryRequest(page=PageRequest(cursor="not-a-cursor!!!"))
        )
    assert ei.value.error.code is ServiceErrorCode.INVALID_CURSOR


def test_wrong_capability_cursor_rejected() -> None:
    svc = DataService(build_reference_fixture())
    alien = encode_cursor(
        capability_id="publisher_topic_analytics",
        keys={"last_publication_year": 2020, "last_topic_id": "T1"},
    )
    with pytest.raises(ServiceErrorException) as ei:
        svc.search_research(
            ResearchDiscoveryRequest(page=PageRequest(cursor=alien))
        )
    assert ei.value.error.code is ServiceErrorCode.INVALID_CURSOR


def test_cursor_contract_version_mismatch_rejected() -> None:
    svc = DataService(build_reference_fixture())
    bad = encode_cursor(
        capability_id="research_discovery",
        keys={"last_work_id": "W001"},
        contract_version="data-service-contract-v0",
    )
    with pytest.raises(ServiceErrorException) as ei:
        svc.search_research(
            ResearchDiscoveryRequest(page=PageRequest(cursor=bad))
        )
    assert ei.value.error.code is ServiceErrorCode.INVALID_CURSOR


def test_discovery_filter_behavior() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.search_research(
        ResearchDiscoveryRequest(
            primary_publisher_id="P1",
            topic_id="T1",
            publication_year_from=2020,
            publication_year_to=2021,
            page=PageRequest(limit=50),
        )
    )
    ids = {w.work_id for w in resp.data.items}
    assert "W001" in ids
    assert "W004" in ids
    # W003 has NULL year — excluded by year filter
    assert "W003" not in ids
    # W002 has empty topics
    assert "W002" not in ids


def test_discovery_unsupported_filter_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ResearchDiscoveryRequest.model_validate(
            {"issn": "1234-5678", "page": {"limit": 10}}
        )


def test_discovery_active_only() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.search_research(
        ResearchDiscoveryRequest(page=PageRequest(limit=100))
    )
    assert all(w.work_id != "W999" for w in resp.data.items)


def test_discovery_empty_page_is_empty_list() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.search_research(
        ResearchDiscoveryRequest(
            primary_publisher_id="P_NONE",
            page=PageRequest(limit=10),
        )
    )
    assert resp.data.items == ()
    assert resp.data.page.has_more is False
    assert resp.data.page.next_cursor is None


# ---------------------------------------------------------------------------
# Journal / publisher
# ---------------------------------------------------------------------------


def test_journal_metrics_grain() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_journal_metrics(JournalMetricsRequest(source_id="S1"))
    assert resp.capability_id == "journal_metrics"
    assert resp.data.source_id == "S1"
    assert resp.data.unique_author_count == 3
    assert resp.data.active_work_count == 3


def test_journal_zero_metrics() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_journal_metrics(JournalMetricsRequest(source_id="S_ZERO"))
    assert resp.data.unique_author_count == 0
    assert resp.data.active_work_count == 0


def test_journal_missing_not_found() -> None:
    svc = DataService(build_reference_fixture())
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_journal_metrics(JournalMetricsRequest(source_id="S_MISSING"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND


def test_publisher_summary_grain() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_publisher_summary(PublisherSummaryRequest(publisher_id="P1"))
    assert resp.data.publisher_id == "P1"
    assert resp.data.unique_author_count == 3
    assert resp.data.active_work_count == 4


def test_publisher_summary_missing() -> None:
    svc = DataService(build_reference_fixture())
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_publisher_summary(PublisherSummaryRequest(publisher_id="P_X"))
    assert ei.value.error.code is ServiceErrorCode.NOT_FOUND


def test_publisher_topic_analytics_grain_and_sort() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.list_publisher_topic_analytics(
        PublisherTopicAnalyticsRequest(
            publisher_id="P1", page=PageRequest(limit=50)
        )
    )
    rows = resp.data.items
    assert all(r.publisher_id == "P1" for r in rows)
    # NULL year last; within year topic_id ASC
    years = [r.publication_year for r in rows]
    non_null = [y for y in years if y is not None]
    assert non_null == sorted(non_null)
    assert years[-1] is None
    same_year = [r for r in rows if r.publication_year == 2021]
    assert [r.topic_id for r in same_year] == sorted(r.topic_id for r in same_year)


def test_publisher_topic_year_and_topic_filters() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.list_publisher_topic_analytics(
        PublisherTopicAnalyticsRequest(
            publisher_id="P1",
            publication_year_from=2021,
            publication_year_to=2021,
            topic_id="T1",
            page=PageRequest(limit=50),
        )
    )
    assert len(resp.data.items) == 1
    assert resp.data.items[0].topic_id == "T1"
    assert resp.data.items[0].publication_year == 2021
    # NULL year excluded by year filter
    assert all(r.publication_year is not None for r in resp.data.items)


def test_publisher_topic_pagination_stable() -> None:
    svc = DataService(build_reference_fixture())
    page1 = svc.list_publisher_topic_analytics(
        PublisherTopicAnalyticsRequest(
            publisher_id="P1", page=PageRequest(limit=2)
        )
    )
    assert page1.data.page.has_more
    page2 = svc.list_publisher_topic_analytics(
        PublisherTopicAnalyticsRequest(
            publisher_id="P1",
            page=PageRequest(limit=2, cursor=page1.data.page.next_cursor),
        )
    )
    keys1 = {(r.publication_year, r.topic_id) for r in page1.data.items}
    keys2 = {(r.publication_year, r.topic_id) for r in page2.data.items}
    assert keys1.isdisjoint(keys2)


def test_no_fanout_recomputation_in_service() -> None:
    """Service methods only call repository — no JOIN/COUNT in service layer."""
    src = inspect.getsource(DataService)
    assert "COUNT(DISTINCT" not in src
    assert "work_authors" not in src
    assert "work_topics" not in src
    assert "work_author_institutions" not in src
    assert "work_locations" not in src


# ---------------------------------------------------------------------------
# Freshness / errors / limits
# ---------------------------------------------------------------------------


def test_freshness_preserved() -> None:
    svc = DataService(build_reference_fixture())
    resp = svc.get_work(WorkMetadataRequest(work_id="W001"))
    assert resp.freshness.status is FreshnessStatus.KNOWN
    assert resp.freshness.generated_at == "2020-01-01T00:00:00Z"
    assert resp.freshness.as_of_run_id == "fixture-run-1"


def test_freshness_unknown_supported() -> None:
    repo = InMemoryConsumerRepository(
        works=(
            WorkMetadataRecord(work_id="W1", author_ids=(), topic_ids=(), institution_ids=()),
        ),
        freshness=FreshnessMetadata(status=FreshnessStatus.UNKNOWN),
    )
    svc = DataService(repo)
    resp = svc.get_work(WorkMetadataRequest(work_id="W1"))
    assert resp.freshness.status is FreshnessStatus.UNKNOWN
    assert resp.freshness.generated_at is None


def test_backend_error_safe() -> None:
    class BoomRepo:
        def freshness(self) -> FreshnessMetadata:
            return FreshnessMetadata()

        def get_work_metadata(self, request: WorkMetadataRequest) -> Any:
            raise RuntimeError("psycopg connection failed at host=secret project=x")

        def search_research(self, request: ResearchDiscoveryRequest) -> Any:
            raise NotImplementedError

        def get_journal_metrics(self, request: JournalMetricsRequest) -> Any:
            raise NotImplementedError

        def get_publisher_summary(self, request: PublisherSummaryRequest) -> Any:
            raise NotImplementedError

        def list_publisher_topic_metrics(
            self, request: PublisherTopicAnalyticsRequest
        ) -> Any:
            raise NotImplementedError

    svc = DataService(BoomRepo())  # type: ignore[arg-type]
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W1"))
    err = ei.value.error
    assert err.code is ServiceErrorCode.BACKEND_ERROR
    assert "secret" not in err.message
    assert "psycopg" not in err.message
    assert "project" not in err.message
    assert err.retryable is True


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        PageRequest(limit=0)
    with pytest.raises(ValidationError):
        PageRequest(limit=MAX_PAGE_LIMIT + 1)
    assert DEFAULT_PAGE_LIMIT == 50
    assert MAX_PAGE_LIMIT == 200


def test_invalid_year_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchDiscoveryRequest(
            publication_year_from=2022, publication_year_to=2020
        )
    with pytest.raises(ValidationError):
        PublisherTopicAnalyticsRequest(
            publisher_id="P1",
            publication_year_from=999,
        )


def test_quality_not_published() -> None:
    repo = build_reference_fixture()
    repo._published = False  # noqa: SLF001 — intentional fixture toggle
    svc = DataService(repo)
    with pytest.raises(ServiceErrorException) as ei:
        svc.get_work(WorkMetadataRequest(work_id="W001"))
    assert ei.value.error.code is ServiceErrorCode.QUALITY_NOT_PUBLISHED


def test_cursor_decode_roundtrip() -> None:
    cur = encode_cursor(
        capability_id="research_discovery",
        keys={"last_work_id": "W003"},
    )
    keys = decode_cursor(cur, expected_capability_id="research_discovery")
    assert keys == {"last_work_id": "W003"}


def test_point_lookup_not_paginated() -> None:
    """Point lookups return ServiceResponse[Record], not Page."""
    svc = DataService(build_reference_fixture())
    w = svc.get_work(WorkMetadataRequest(work_id="W001"))
    j = svc.get_journal_metrics(JournalMetricsRequest(source_id="S1"))
    p = svc.get_publisher_summary(PublisherSummaryRequest(publisher_id="P1"))
    assert isinstance(w.data, WorkMetadataRecord)
    assert isinstance(j.data, JournalMetricsRecord)
    assert isinstance(p.data, PublisherSummaryRecord)
    assert not hasattr(w.data, "next_cursor")


def test_extra_fields_forbid_on_requests() -> None:
    with pytest.raises(ValidationError):
        JournalMetricsRequest.model_validate(
            {"source_id": "S1", "issn": "1234-5678"}
        )
    with pytest.raises(ValidationError):
        PublisherSummaryRequest.model_validate(
            {"publisher_id": "P1", "raw_sql": "select 1"}
        )
