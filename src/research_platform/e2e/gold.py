"""Step-19 staged Gold build reusing Step-16 SEMANTIC_ONLY builders."""

from __future__ import annotations

from typing import Any

import duckdb

from research_platform.analytics.gold.semantic_build import (
    EVIDENCE_LABEL,
    build_all_gold_marts,
    build_staged_gold_audit_tables,
    fetch_gold_rows,
)
from research_platform.service.models import (
    FreshnessMetadata,
    FreshnessStatus,
    JournalMetricsRecord,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    WorkMetadataRecord,
)
from research_platform.service.repository import InMemoryConsumerRepository


def build_staged_gold(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Build all nine Gold marts + staged audit tables from analytical tables."""
    mart_ids = build_all_gold_marts(conn)
    build_staged_gold_audit_tables(conn)
    counts = {
        mid: conn.execute(f"SELECT COUNT(*) FROM gold_{mid}").fetchone()[0]
        for mid in mart_ids
    }
    return {
        "mart_ids": mart_ids,
        "row_counts": counts,
        "evidence_label": EVIDENCE_LABEL,
    }


def consumer_repository_from_gold(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    deleted_work_ids: frozenset[str],
    published: bool = True,
) -> InMemoryConsumerRepository:
    """Map actual Gold outputs into the Step-18 consumer repository.

    Does not use ``build_reference_fixture()``.
    """
    discovery_rows = fetch_gold_rows(conn, "research_discovery")
    works: list[WorkMetadataRecord] = []
    for row in discovery_rows:
        (
            work_id,
            doi,
            title,
            publication_year,
            publication_date,
            work_type,
            language,
            is_oa,
            oa_status,
            primary_source_id,
            primary_publisher_id,
            author_ids,
            topic_ids,
            institution_ids,
        ) = row
        pub_date = None
        if publication_date is not None:
            pub_date = str(publication_date)
        works.append(
            WorkMetadataRecord(
                work_id=work_id,
                doi=doi,
                title=title,
                publication_year=publication_year,
                publication_date=pub_date,
                work_type=work_type,
                language=language,
                is_oa=is_oa,
                oa_status=oa_status,
                primary_source_id=primary_source_id,
                primary_publisher_id=primary_publisher_id,
                author_ids=tuple(author_ids or ()),
                topic_ids=tuple(topic_ids or ()),
                institution_ids=tuple(institution_ids or ()),
            )
        )

    journals = tuple(
        JournalMetricsRecord(
            source_id=r[0],
            unique_author_count=int(r[1]),
            active_work_count=int(r[2]),
        )
        for r in fetch_gold_rows(conn, "journal_author_stats")
    )
    publishers = tuple(
        PublisherSummaryRecord(
            publisher_id=r[0],
            unique_author_count=int(r[1]),
            active_work_count=int(r[2]),
        )
        for r in fetch_gold_rows(conn, "publisher_author_stats")
    )
    topics = tuple(
        PublisherTopicMetricRecord(
            publisher_id=r[0],
            topic_id=r[1],
            publication_year=r[2],
            active_work_count=int(r[3]),
        )
        for r in fetch_gold_rows(conn, "publisher_topic_year_stats")
    )

    return InMemoryConsumerRepository(
        works=tuple(works),
        deleted_work_ids=deleted_work_ids,
        journal_metrics=journals,
        publisher_summaries=publishers,
        publisher_topic_metrics=topics,
        freshness=FreshnessMetadata(
            status=FreshnessStatus.KNOWN,
            as_of_run_id=run_id,
            notes="SEMANTIC_ONLY Step-19 publication from staged Gold",
        ),
        published=published,
    )
