"""Storage-independent ConsumerDataRepository protocol + in-memory reference."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from research_platform.service.contracts import (
    JournalMetricsRequest,
    PublisherSummaryRequest,
    PublisherTopicAnalyticsRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import (
    MAX_PUBLICATION_YEAR,
    MIN_PUBLICATION_YEAR,
    FreshnessMetadata,
    FreshnessStatus,
    JournalMetricsRecord,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    ServiceError,
    ServiceErrorCode,
    ServiceErrorException,
    WorkMetadataRecord,
)
from research_platform.service.pagination import decode_cursor, encode_cursor, page_slice


@dataclass(frozen=True)
class ConsumerRepositorySnapshot:
    """Immutable public view of consumer repository content for fingerprinting."""

    works: tuple[WorkMetadataRecord, ...]
    deleted_work_ids: frozenset[str]
    journal_metrics: tuple[JournalMetricsRecord, ...]
    publisher_summaries: tuple[PublisherSummaryRecord, ...]
    publisher_topic_metrics: tuple[PublisherTopicMetricRecord, ...]
    freshness: FreshnessMetadata
    published: bool

    def content_fingerprint(self) -> str:
        payload = {
            "works": [w.model_dump(mode="json") for w in self.works],
            "deleted_work_ids": sorted(self.deleted_work_ids),
            "journals": [j.model_dump(mode="json") for j in self.journal_metrics],
            "publishers": [p.model_dump(mode="json") for p in self.publisher_summaries],
            "topics": [t.model_dump(mode="json") for t in self.publisher_topic_metrics],
        }
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@runtime_checkable
class ConsumerDataRepository(Protocol):
    """Storage-independent read protocol for consumer capabilities.

    Implementations may back onto BigQuery Gold, cache, or a future selective
    operational store. No raw SQL is accepted here.
    """

    def freshness(self) -> FreshnessMetadata: ...

    def get_work_metadata(
        self, request: WorkMetadataRequest
    ) -> WorkMetadataRecord | None: ...

    def search_research(
        self, request: ResearchDiscoveryRequest
    ) -> tuple[tuple[WorkMetadataRecord, ...], str | None, bool]: ...

    def get_journal_metrics(
        self, request: JournalMetricsRequest
    ) -> JournalMetricsRecord | None: ...

    def get_publisher_summary(
        self, request: PublisherSummaryRequest
    ) -> PublisherSummaryRecord | None: ...

    def list_publisher_topic_metrics(
        self, request: PublisherTopicAnalyticsRequest
    ) -> tuple[tuple[PublisherTopicMetricRecord, ...], str | None, bool]: ...


class InMemoryConsumerRepository:
    """Deterministic local reference repository (SEMANTIC_ONLY).

    Proves the storage-independent contract without BigQuery/Postgres/DuckDB.
    """

    def __init__(
        self,
        *,
        works: tuple[WorkMetadataRecord, ...] = (),
        deleted_work_ids: frozenset[str] = frozenset(),
        journal_metrics: tuple[JournalMetricsRecord, ...] = (),
        publisher_summaries: tuple[PublisherSummaryRecord, ...] = (),
        publisher_topic_metrics: tuple[PublisherTopicMetricRecord, ...] = (),
        freshness: FreshnessMetadata | None = None,
        published: bool = True,
    ) -> None:
        self._works = works
        self._deleted_work_ids = deleted_work_ids
        self._journal = {r.source_id: r for r in journal_metrics}
        self._publishers = {r.publisher_id: r for r in publisher_summaries}
        self._publisher_topics = publisher_topic_metrics
        self._freshness = freshness or FreshnessMetadata(status=FreshnessStatus.UNKNOWN)
        self._published = published

    def freshness(self) -> FreshnessMetadata:
        return self._freshness

    @property
    def is_published(self) -> bool:
        return self._published

    def snapshot(self) -> ConsumerRepositorySnapshot:
        """Public immutable snapshot — prefer this over private field access."""
        return ConsumerRepositorySnapshot(
            works=self._works,
            deleted_work_ids=self._deleted_work_ids,
            journal_metrics=tuple(self._journal[k] for k in sorted(self._journal)),
            publisher_summaries=tuple(
                self._publishers[k] for k in sorted(self._publishers)
            ),
            publisher_topic_metrics=self._publisher_topics,
            freshness=self._freshness,
            published=self._published,
        )

    def content_fingerprint(self) -> str:
        return self.snapshot().content_fingerprint()

    def get_work_metadata(
        self, request: WorkMetadataRequest
    ) -> WorkMetadataRecord | None:
        self._require_published()
        if request.work_id:
            wid = request.work_id.strip()
            if wid in self._deleted_work_ids:
                return None
            for work in self._works:
                if work.work_id == wid:
                    return work
            return None
        assert request.doi is not None
        doi = request.doi.strip()
        for work in self._works:
            if work.doi is not None and work.doi == doi:
                if work.work_id in self._deleted_work_ids:
                    return None
                return work
        return None

    def search_research(
        self, request: ResearchDiscoveryRequest
    ) -> tuple[tuple[WorkMetadataRecord, ...], str | None, bool]:
        self._require_published()
        after_work_id: str | None = None
        if request.page.cursor is not None:
            keys = decode_cursor(
                request.page.cursor,
                expected_capability_id="research_discovery",
            )
            after_work_id = keys.get("last_work_id")
            if not isinstance(after_work_id, str) or not after_work_id.strip():
                raise ServiceErrorException(
                    ServiceError(
                        code=ServiceErrorCode.INVALID_CURSOR,
                        message="cursor missing last_work_id",
                        capability_id="research_discovery",
                        field="cursor",
                    )
                )

        filtered: list[WorkMetadataRecord] = []
        for work in sorted(self._works, key=lambda w: w.work_id):
            if work.work_id in self._deleted_work_ids:
                continue
            if after_work_id is not None and work.work_id <= after_work_id:
                continue
            if not _discovery_matches(work, request):
                continue
            filtered.append(work)

        page_items, has_more = page_slice(filtered, limit=request.page.limit)
        next_cursor = None
        if has_more and page_items:
            next_cursor = encode_cursor(
                capability_id="research_discovery",
                keys={"last_work_id": page_items[-1].work_id},
            )
        return tuple(page_items), next_cursor, has_more

    def get_journal_metrics(
        self, request: JournalMetricsRequest
    ) -> JournalMetricsRecord | None:
        self._require_published()
        return self._journal.get(request.source_id.strip())

    def get_publisher_summary(
        self, request: PublisherSummaryRequest
    ) -> PublisherSummaryRecord | None:
        self._require_published()
        return self._publishers.get(request.publisher_id.strip())

    def list_publisher_topic_metrics(
        self, request: PublisherTopicAnalyticsRequest
    ) -> tuple[tuple[PublisherTopicMetricRecord, ...], str | None, bool]:
        self._require_published()
        after_year: int | None | object = _MISSING
        after_topic: str | None = None
        if request.page.cursor is not None:
            keys = decode_cursor(
                request.page.cursor,
                expected_capability_id="publisher_topic_analytics",
            )
            after_year, after_topic = parse_publisher_topic_cursor_keys(keys)

        pid = request.publisher_id.strip()
        rows = [
            r
            for r in self._publisher_topics
            if r.publisher_id == pid and _topic_metric_matches(r, request)
        ]
        # publication_year ASC NULLS LAST, topic_id ASC
        rows.sort(
            key=lambda r: (
                r.publication_year is None,
                r.publication_year if r.publication_year is not None else 0,
                r.topic_id,
            )
        )

        if after_year is not _MISSING:
            assert isinstance(after_topic, str)
            year_key: int | None = after_year  # type: ignore[assignment]
            filtered: list[PublisherTopicMetricRecord] = []
            for r in rows:
                if _after_topic_cursor(r, year_key, after_topic):
                    filtered.append(r)
            rows = filtered

        page_items, has_more = page_slice(list(rows), limit=request.page.limit)
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = encode_cursor(
                capability_id="publisher_topic_analytics",
                keys={
                    "last_publication_year": last.publication_year,
                    "last_topic_id": last.topic_id,
                },
            )
        return tuple(page_items), next_cursor, has_more

    def _require_published(self) -> None:
        if not self._published:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.QUALITY_NOT_PUBLISHED,
                    message="no quality-approved published view is available",
                    capability_id="*",
                    retryable=False,
                )
            )


_MISSING = object()


def parse_publisher_topic_cursor_keys(keys: dict[str, object]) -> tuple[int | None, str]:
    """Validate publisher_topic_analytics cursor sort keys.

    last_publication_year: int in [1000, 3000] or NULL (NULLS LAST bucket).
    bool is rejected (bool is a subclass of int).
    last_topic_id: non-empty string; NULL/empty invalid.
    """
    if "last_publication_year" not in keys or "last_topic_id" not in keys:
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="cursor missing sort keys",
                capability_id="publisher_topic_analytics",
                field="cursor",
            )
        )
    year = keys["last_publication_year"]
    topic = keys["last_topic_id"]

    if year is not None:
        # bool is a subclass of int — reject explicitly.
        if isinstance(year, bool) or not isinstance(year, int):
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.INVALID_CURSOR,
                    message="cursor last_publication_year invalid",
                    capability_id="publisher_topic_analytics",
                    field="cursor",
                )
            )
        if year < MIN_PUBLICATION_YEAR or year > MAX_PUBLICATION_YEAR:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.INVALID_CURSOR,
                    message="cursor last_publication_year out of range",
                    capability_id="publisher_topic_analytics",
                    field="cursor",
                )
            )

    if not isinstance(topic, str) or not topic.strip():
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="cursor last_topic_id invalid",
                capability_id="publisher_topic_analytics",
                field="cursor",
            )
        )
    return year, topic


def _discovery_matches(work: WorkMetadataRecord, request: ResearchDiscoveryRequest) -> bool:
    if request.publication_year_from is not None or request.publication_year_to is not None:
        if work.publication_year is None:
            return False
        if (
            request.publication_year_from is not None
            and work.publication_year < request.publication_year_from
        ):
            return False
        if (
            request.publication_year_to is not None
            and work.publication_year > request.publication_year_to
        ):
            return False
    if (
        request.primary_publisher_id is not None
        and work.primary_publisher_id != request.primary_publisher_id
    ):
        return False
    if (
        request.primary_source_id is not None
        and work.primary_source_id != request.primary_source_id
    ):
        return False
    if request.work_type is not None and work.work_type != request.work_type:
        return False
    if request.language is not None and work.language != request.language:
        return False
    if request.oa_status is not None and work.oa_status != request.oa_status:
        return False
    if request.topic_id is not None and request.topic_id not in work.topic_ids:
        return False
    if (
        request.institution_id is not None
        and request.institution_id not in work.institution_ids
    ):
        return False
    if request.author_id is not None and request.author_id not in work.author_ids:
        return False
    return True


def _topic_metric_matches(
    row: PublisherTopicMetricRecord, request: PublisherTopicAnalyticsRequest
) -> bool:
    if request.topic_id is not None and row.topic_id != request.topic_id:
        return False
    if request.publication_year_from is not None or request.publication_year_to is not None:
        if row.publication_year is None:
            return False
        if (
            request.publication_year_from is not None
            and row.publication_year < request.publication_year_from
        ):
            return False
        if (
            request.publication_year_to is not None
            and row.publication_year > request.publication_year_to
        ):
            return False
    return True


def _after_topic_cursor(
    row: PublisherTopicMetricRecord,
    after_year: int | None,
    after_topic: str,
) -> bool:
    """Strictly after (publication_year ASC NULLS LAST, topic_id ASC)."""
    # NULLS LAST: non-null years come first.
    row_null = row.publication_year is None
    after_null = after_year is None
    if row_null != after_null:
        # row is after only if after is non-null and row is null (NULLS LAST)
        return (not after_null) and row_null
    if not row_null:
        assert row.publication_year is not None and after_year is not None
        if row.publication_year != after_year:
            return row.publication_year > after_year
    # same year bucket (including both NULL): compare topic_id
    return row.topic_id > after_topic


def build_reference_fixture() -> InMemoryConsumerRepository:
    """Tiny deterministic fixture for unit tests (SEMANTIC_ONLY)."""
    works = (
        WorkMetadataRecord(
            work_id="W001",
            doi="10.1000/aaa",
            title="Alpha paper",
            publication_year=2020,
            publication_date="2020-01-15",
            work_type="article",
            language="en",
            is_oa=True,
            oa_status="gold",
            primary_source_id="S1",
            primary_publisher_id="P1",
            author_ids=("A1", "A2"),
            topic_ids=("T1",),
            institution_ids=("I1",),
        ),
        WorkMetadataRecord(
            work_id="W002",
            doi=None,
            title="Beta paper",
            publication_year=2021,
            publication_date=None,
            work_type="article",
            language="en",
            is_oa=None,
            oa_status=None,
            primary_source_id="S1",
            primary_publisher_id="P1",
            author_ids=(),
            topic_ids=(),
            institution_ids=(),
        ),
        WorkMetadataRecord(
            work_id="W003",
            doi="10.1000/ccc",
            title="Gamma paper",
            publication_year=None,
            publication_date=None,
            work_type="book",
            language="fr",
            is_oa=False,
            oa_status="closed",
            primary_source_id="S2",
            primary_publisher_id="P2",
            author_ids=("A3",),
            topic_ids=("T2", "T3"),
            institution_ids=("I2",),
        ),
        WorkMetadataRecord(
            work_id="W004",
            doi="10.1000/ddd",
            title="Delta paper",
            publication_year=2021,
            work_type="article",
            language="en",
            is_oa=True,
            oa_status="green",
            primary_source_id="S2",
            primary_publisher_id="P1",
            author_ids=("A1",),
            topic_ids=("T1", "T2"),
            institution_ids=(),
        ),
        WorkMetadataRecord(
            work_id="W005",
            doi="10.1000/eee",
            title="Epsilon paper",
            publication_year=2022,
            work_type="article",
            language="en",
            primary_source_id="S1",
            primary_publisher_id="P1",
            author_ids=("A2",),
            topic_ids=("T1",),
            institution_ids=("I1",),
        ),
        WorkMetadataRecord(
            work_id="W006",
            doi="10.1000/fff",
            title="Zeta paper",
            publication_year=2022,
            work_type="article",
            language="de",
            primary_source_id="S3",
            primary_publisher_id="P2",
            author_ids=("A4",),
            topic_ids=("T3",),
            institution_ids=("I3",),
        ),
    )
    # DELETED canonical work — present only in tombstone set, never in ACTIVE works
    deleted = frozenset({"W999"})
    journals = (
        JournalMetricsRecord(source_id="S1", unique_author_count=3, active_work_count=3),
        JournalMetricsRecord(source_id="S2", unique_author_count=2, active_work_count=2),
        JournalMetricsRecord(source_id="S_ZERO", unique_author_count=0, active_work_count=0),
    )
    publishers = (
        PublisherSummaryRecord(
            publisher_id="P1", unique_author_count=3, active_work_count=4
        ),
        PublisherSummaryRecord(
            publisher_id="P2", unique_author_count=2, active_work_count=2
        ),
    )
    # Multiple rows same year for tie-break on topic_id; include NULL year
    topics = (
        PublisherTopicMetricRecord(
            publisher_id="P1", topic_id="T1", publication_year=2020, active_work_count=1
        ),
        PublisherTopicMetricRecord(
            publisher_id="P1", topic_id="T1", publication_year=2021, active_work_count=2
        ),
        PublisherTopicMetricRecord(
            publisher_id="P1", topic_id="T2", publication_year=2021, active_work_count=1
        ),
        PublisherTopicMetricRecord(
            publisher_id="P1", topic_id="T1", publication_year=2022, active_work_count=1
        ),
        PublisherTopicMetricRecord(
            publisher_id="P1", topic_id="T9", publication_year=None, active_work_count=1
        ),
        PublisherTopicMetricRecord(
            publisher_id="P2", topic_id="T2", publication_year=None, active_work_count=1
        ),
        PublisherTopicMetricRecord(
            publisher_id="P2", topic_id="T3", publication_year=2022, active_work_count=1
        ),
    )
    return InMemoryConsumerRepository(
        works=works,
        deleted_work_ids=deleted,
        journal_metrics=journals,
        publisher_summaries=publishers,
        publisher_topic_metrics=topics,
        freshness=FreshnessMetadata(
            status=FreshnessStatus.KNOWN,
            generated_at="2020-01-01T00:00:00Z",
            source_max_updated_at="2020-01-01T00:00:00Z",
            as_of_run_id="fixture-run-1",
            notes="SEMANTIC_ONLY reference fixture",
        ),
        published=True,
    )
