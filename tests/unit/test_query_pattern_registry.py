"""Offline tests for Step 14 query-pattern / benchmark registry."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from research_platform.benchmarks.fanout import (
    demo_graph,
    safe_unique_author_count,
    safe_unique_topic_count,
    safe_work_metrics,
    topic_work_counts_excluding_deleted,
    unsafe_author_count_via_topics,
    unsafe_join_row_count,
)
from research_platform.benchmarks.harness import (
    fixture_timing_result,
    run_citation_fixture,
    run_publication_trends_fixture,
    run_unsafe_vs_safe_author_topic_fixture,
)
from research_platform.benchmarks.models import (
    ActiveWorkFilter,
    BenchmarkEvidenceType,
    BenchmarkResult,
    EvidenceStatus,
    Placement,
    QueryCategory,
    QueryPattern,
    QueryPatternRegistry,
)
from research_platform.benchmarks.registry import (
    REQUIRED_CATEGORIES,
    load_query_pattern_registry,
    registry_to_sorted_dict,
)


def test_registry_loads_all_required_categories() -> None:
    registry = load_query_pattern_registry()
    present = {pattern.category for pattern in registry.patterns}
    assert present == REQUIRED_CATEGORIES
    assert len(registry.patterns) >= 12


def test_unique_pattern_ids_and_allowed_placements() -> None:
    registry = load_query_pattern_registry()
    ids = [p.pattern_id for p in registry.patterns]
    assert len(ids) == len(set(ids))
    allowed = set(Placement)
    for pattern in registry.patterns:
        assert pattern.placement in allowed
        assert pattern.expected_result_grain.strip()
        assert pattern.relevant_entities
        assert pattern.evidence_status in set(EvidenceStatus)


def test_active_filtering_declared_for_work_patterns() -> None:
    registry = load_query_pattern_registry()
    for pattern in registry.patterns:
        if pattern.category in {
            QueryCategory.DOI_LOOKUP,
            QueryCategory.OPENALEX_ID_LOOKUP,
            QueryCategory.PUBLICATION_TRENDS,
            QueryCategory.OPEN_ACCESS_TRENDS,
            QueryCategory.AUTHORS_PER_JOURNAL,
            QueryCategory.CITATION_RELATIONSHIPS,
        }:
            assert pattern.active_work_filter is not ActiveWorkFilter.NOT_APPLICABLE


def test_optional_operational_store_requires_measured_evidence() -> None:
    base = load_query_pattern_registry().patterns[0].model_dump(mode="json")
    base.update(
        {
            "pattern_id": "bad-operational",
            "placement": Placement.OPTIONAL_OPERATIONAL_STORE.value,
            "evidence_status": EvidenceStatus.ASSUMED.value,
            "placement_rationale": "should fail",
        }
    )
    with pytest.raises(ValueError, match="OPTIONAL_OPERATIONAL_STORE"):
        QueryPattern.model_validate(base)


def test_unknown_assumptions_remain_explicit() -> None:
    registry = load_query_pattern_registry()
    lookup = next(p for p in registry.patterns if p.category is QueryCategory.DOI_LOOKUP)
    assert "UNKNOWN" in lookup.freshness_requirement or lookup.known_scale == "UNKNOWN"
    assert "UNKNOWN" in lookup.service_level.notes


def test_local_benchmark_result_cannot_claim_measured_bigquery() -> None:
    with pytest.raises(ValueError, match="FIXTURE_ONLY"):
        BenchmarkResult.model_validate(
            {
                "result_id": uuid4(),
                "pattern_id": "doi-work-lookup",
                "engine": "duckdb",
                "dataset_scale": "fixture",
                "started_at": datetime(2024, 1, 1, tzinfo=UTC),
                "finished_at": datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                "latency_ms": 1.0,
                "evidence_type": BenchmarkEvidenceType.MEASURED,
            }
        )
    ok = fixture_timing_result(pattern_id="doi-work-lookup", latency_ms=1.25)
    assert ok.evidence_type is BenchmarkEvidenceType.FIXTURE_ONLY


def test_fanout_fixture_unsafe_vs_safe() -> None:
    graph = demo_graph()
    assert unsafe_join_row_count(graph, work_id="W1") == 6
    assert unsafe_author_count_via_topics(graph, work_id="W1") == 6
    assert safe_unique_author_count(graph, work_id="W1") == 2
    assert safe_unique_topic_count(graph, work_id="W1") == 3
    metrics = safe_work_metrics(graph)
    assert "W2" not in metrics  # DELETED excluded
    assert metrics["W1"] == {"unique_authors": 2, "unique_topics": 3}
    assert topic_work_counts_excluding_deleted(graph) == {"T1": 1, "T2": 1, "T3": 1}


def test_duckdb_harness_grains_and_fanout() -> None:
    trends = run_publication_trends_fixture()
    assert trends.evidence_type is BenchmarkEvidenceType.FIXTURE_ONLY
    assert trends.rows == ((2020, 1), (2021, 1))  # DELETED 2021 W2 excluded

    citations = run_citation_fixture(work_id="W1")
    # Target W2 is DELETED but reference row remains; W9 unresolved/absent.
    targets = {row[2]: row[4] for row in citations.rows}
    assert targets["W2"] == "DELETED"
    assert targets["W9"] is None

    fanout = run_unsafe_vs_safe_author_topic_fixture()
    assert fanout["unsafe_join_rows"] == 6
    assert fanout["safe_unique_authors"] == 2
    assert fanout["safe_unique_topics"] == 3


def test_registry_serialization_deterministic() -> None:
    registry = load_query_pattern_registry()
    first = json.dumps(registry_to_sorted_dict(registry), sort_keys=True)
    second = json.dumps(
        registry_to_sorted_dict(QueryPatternRegistry.model_validate(json.loads(first))),
        sort_keys=True,
    )
    assert first == second


def test_no_optional_operational_store_without_measured() -> None:
    registry = load_query_pattern_registry()
    for pattern in registry.patterns:
        if pattern.placement is Placement.OPTIONAL_OPERATIONAL_STORE:
            assert pattern.evidence_status is EvidenceStatus.MEASURED
        # Step 14 has no measured operational-store evidence.
        assert pattern.placement is not Placement.OPTIONAL_OPERATIONAL_STORE
