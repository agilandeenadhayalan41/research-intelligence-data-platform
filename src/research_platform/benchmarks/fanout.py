"""Demonstrate relationship fan-out and safe independent aggregation.

Unsafe pattern::

    works JOIN work_authors JOIN work_topics

multiplies authors × topics per Work and double-counts when aggregating.

Safe pattern::

    aggregate work_authors independently
    aggregate work_topics independently
    join the already-aggregated results at the Work (or requested) grain
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticWorkGraph:
    """Tiny in-memory relationship fixture (not production scale)."""

    # work_id -> author_ids
    work_authors: dict[str, tuple[str, ...]]
    # work_id -> topic_ids
    work_topics: dict[str, tuple[str, ...]]
    # work_id -> activity_state
    work_activity: dict[str, str]


def demo_graph() -> SyntheticWorkGraph:
    """One ACTIVE work with 2 authors and 3 topics (fan-out factor 6)."""
    return SyntheticWorkGraph(
        work_authors={"W1": ("A1", "A2"), "W2": ("A1",)},
        work_topics={"W1": ("T1", "T2", "T3"), "W2": ("T1",)},
        work_activity={"W1": "ACTIVE", "W2": "DELETED"},
    )


def unsafe_join_row_count(graph: SyntheticWorkGraph, *, work_id: str) -> int:
    """Row count of the unaggregated authors ⋈ topics join for one Work."""
    authors = graph.work_authors.get(work_id, ())
    topics = graph.work_topics.get(work_id, ())
    return len(authors) * len(topics)


def unsafe_author_count_via_topics(graph: SyntheticWorkGraph, *, work_id: str) -> int:
    """Incorrect distinct-author estimate if counting join rows without DISTINCT."""
    return unsafe_join_row_count(graph, work_id=work_id)


def safe_unique_author_count(graph: SyntheticWorkGraph, *, work_id: str) -> int:
    if graph.work_activity.get(work_id) != "ACTIVE":
        return 0
    return len(set(graph.work_authors.get(work_id, ())))


def safe_unique_topic_count(graph: SyntheticWorkGraph, *, work_id: str) -> int:
    if graph.work_activity.get(work_id) != "ACTIVE":
        return 0
    return len(set(graph.work_topics.get(work_id, ())))


def safe_work_metrics(graph: SyntheticWorkGraph) -> dict[str, dict[str, int]]:
    """Independent aggregates joined only at Work grain (ACTIVE works only)."""
    author_counts = {
        work_id: safe_unique_author_count(graph, work_id=work_id)
        for work_id in graph.work_activity
    }
    topic_counts = {
        work_id: safe_unique_topic_count(graph, work_id=work_id)
        for work_id in graph.work_activity
    }
    metrics: dict[str, dict[str, int]] = {}
    for work_id, state in graph.work_activity.items():
        if state != "ACTIVE":
            continue
        metrics[work_id] = {
            "unique_authors": author_counts[work_id],
            "unique_topics": topic_counts[work_id],
        }
    return metrics


def topic_work_counts_excluding_deleted(
    graph: SyntheticWorkGraph,
) -> dict[str, int]:
    """Count ACTIVE works per topic without author fan-out."""
    counts: dict[str, int] = defaultdict(int)
    for work_id, topics in graph.work_topics.items():
        if graph.work_activity.get(work_id) != "ACTIVE":
            continue
        for topic_id in set(topics):
            counts[topic_id] += 1
    return dict(counts)
