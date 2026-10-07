"""Domain DataService — storage-independent consumer capabilities."""

from __future__ import annotations

import inspect

from pydantic import ValidationError

from research_platform.service.contracts import (
    JournalMetricsRequest,
    PublisherSummaryRequest,
    PublisherTopicAnalyticsRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import (
    DATA_SERVICE_CONTRACT_VERSION,
    ConsistencySemantics,
    FreshnessMetadata,
    JournalMetricsRecord,
    Page,
    PageInfo,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    ServiceError,
    ServiceErrorCode,
    ServiceErrorException,
    ServiceResponse,
    WorkMetadataRecord,
)
from research_platform.service.repository import ConsumerDataRepository


class DataService:
    """Consumer-facing domain service.

    Depends only on ``ConsumerDataRepository``. No raw SQL, no HTTP framework,
    no storage technology fields in responses.
    """

    def __init__(self, repository: ConsumerDataRepository) -> None:
        self._repo = repository

    def get_work(
        self, request: WorkMetadataRequest
    ) -> ServiceResponse[WorkMetadataRecord]:
        try:
            record = self._repo.get_work_metadata(request)
        except ServiceErrorException:
            raise
        except Exception:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message="backend error while loading work metadata",
                    capability_id="work_metadata_lookup",
                    retryable=True,
                )
            ) from None
        if record is None:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.NOT_FOUND,
                    message="work not found",
                    capability_id="work_metadata_lookup",
                )
            )
        return self._ok("work_metadata_lookup", record)

    def search_research(
        self, request: ResearchDiscoveryRequest
    ) -> ServiceResponse[Page[WorkMetadataRecord]]:
        try:
            items, next_cursor, has_more = self._repo.search_research(request)
        except ServiceErrorException:
            raise
        except Exception:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message="backend error while searching research",
                    capability_id="research_discovery",
                    retryable=True,
                )
            ) from None
        page = Page(
            items=items,
            page=PageInfo(
                limit=request.page.limit,
                next_cursor=next_cursor,
                has_more=has_more,
            ),
        )
        return self._ok("research_discovery", page)

    def get_journal_metrics(
        self, request: JournalMetricsRequest
    ) -> ServiceResponse[JournalMetricsRecord]:
        try:
            record = self._repo.get_journal_metrics(request)
        except ServiceErrorException:
            raise
        except Exception:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message="backend error while loading journal metrics",
                    capability_id="journal_metrics",
                    retryable=True,
                )
            ) from None
        if record is None:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.NOT_FOUND,
                    message="journal metrics not found",
                    capability_id="journal_metrics",
                )
            )
        return self._ok("journal_metrics", record)

    def get_publisher_summary(
        self, request: PublisherSummaryRequest
    ) -> ServiceResponse[PublisherSummaryRecord]:
        try:
            record = self._repo.get_publisher_summary(request)
        except ServiceErrorException:
            raise
        except Exception:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message="backend error while loading publisher summary",
                    capability_id="publisher_summary",
                    retryable=True,
                )
            ) from None
        if record is None:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.NOT_FOUND,
                    message="publisher summary not found",
                    capability_id="publisher_summary",
                )
            )
        return self._ok("publisher_summary", record)

    def list_publisher_topic_analytics(
        self, request: PublisherTopicAnalyticsRequest
    ) -> ServiceResponse[Page[PublisherTopicMetricRecord]]:
        try:
            items, next_cursor, has_more = self._repo.list_publisher_topic_metrics(
                request
            )
        except ServiceErrorException:
            raise
        except Exception:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message="backend error while listing publisher topic analytics",
                    capability_id="publisher_topic_analytics",
                    retryable=True,
                )
            ) from None
        page = Page(
            items=items,
            page=PageInfo(
                limit=request.page.limit,
                next_cursor=next_cursor,
                has_more=has_more,
            ),
        )
        return self._ok("publisher_topic_analytics", page)

    def _ok[T](self, capability_id: str, data: T) -> ServiceResponse[T]:
        freshness = self._safe_freshness()
        return ServiceResponse(
            contract_version=DATA_SERVICE_CONTRACT_VERSION,
            capability_id=capability_id,
            data=data,
            freshness=freshness,
            consistency=ConsistencySemantics.PUBLISHED_SNAPSHOT,
        )

    def _safe_freshness(self) -> FreshnessMetadata:
        try:
            return self._repo.freshness()
        except Exception:
            return FreshnessMetadata()


def map_validation_error(
    exc: ValidationError, *, capability_id: str
) -> ServiceErrorException:
    """Map Pydantic validation failures to safe consumer errors."""
    errors = exc.errors()
    field = None
    if errors:
        loc = errors[0].get("loc") or ()
        if loc:
            field = ".".join(str(x) for x in loc)
    msg = "invalid request"
    for err in errors:
        etype = err.get("type", "")
        if "greater_than" in etype or "less_than" in etype or "le" in etype or "ge" in etype:
            if field and "limit" in field:
                return ServiceErrorException(
                    ServiceError(
                        code=ServiceErrorCode.LIMIT_EXCEEDED
                        if "less_than" in etype or etype.endswith("le")
                        else ServiceErrorCode.INVALID_ARGUMENT,
                        message="page limit out of bounds",
                        capability_id=capability_id,
                        field=field,
                    )
                )
        if "extra_forbidden" in etype:
            return ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.UNSUPPORTED_FILTER,
                    message="unsupported filter or field",
                    capability_id=capability_id,
                    field=field,
                )
            )
    return ServiceErrorException(
        ServiceError(
            code=ServiceErrorCode.INVALID_ARGUMENT,
            message=msg,
            capability_id=capability_id,
            field=field,
        )
    )


def assert_no_raw_sql_consumer_methods(service_cls: type = DataService) -> None:
    """Static guard: public DataService methods must not accept SQL parameters."""
    banned = {"sql", "query_text", "raw_sql", "query"}
    for name, method in inspect.getmembers(service_cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        params = set(inspect.signature(method).parameters)
        overlap = params & banned
        if overlap:
            raise AssertionError(
                f"DataService.{name} exposes banned SQL parameter(s): {sorted(overlap)}"
            )
