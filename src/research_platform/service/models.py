"""Storage-independent Data Service domain models (Step 18 / #26)."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_SERVICE_CONTRACT_VERSION = "data-service-contract-v1"

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MIN_PUBLICATION_YEAR = 1000
MAX_PUBLICATION_YEAR = 3000


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WorkloadClass(StrEnum):
    POINT_LOOKUP = "POINT_LOOKUP"
    PAGED_DISCOVERY = "PAGED_DISCOVERY"
    AGGREGATE_LOOKUP = "AGGREGATE_LOOKUP"
    PAGED_ANALYTICS = "PAGED_ANALYTICS"


class PaginationMode(StrEnum):
    NONE = "NONE"
    CURSOR = "CURSOR"


class FreshnessStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class ConsistencySemantics(StrEnum):
    """Consumer reads quality-approved published snapshot only."""

    PUBLISHED_SNAPSHOT = "PUBLISHED_SNAPSHOT"


class ServiceErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_CURSOR = "INVALID_CURSOR"
    NOT_FOUND = "NOT_FOUND"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNSUPPORTED_FILTER = "UNSUPPORTED_FILTER"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    BACKEND_ERROR = "BACKEND_ERROR"
    QUALITY_NOT_PUBLISHED = "QUALITY_NOT_PUBLISHED"


class FreshnessMetadata(SettingsModel):
    status: FreshnessStatus = FreshnessStatus.UNKNOWN
    generated_at: str | None = None
    source_max_updated_at: str | None = None
    as_of_run_id: str | None = None
    notes: str = Field(default="", max_length=512)


class ServiceError(SettingsModel):
    code: ServiceErrorCode
    message: str = Field(min_length=1, max_length=512)
    capability_id: str = Field(min_length=1, max_length=64)
    retryable: bool = False
    field: str | None = None


class ServiceErrorException(Exception):
    """Domain exception carrying a safe ServiceError payload."""

    def __init__(self, error: ServiceError) -> None:
        super().__init__(error.message)
        self.error = error


class PageInfo(SettingsModel):
    limit: int = Field(ge=1, le=MAX_PAGE_LIMIT)
    next_cursor: str | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def cursor_has_more_invariant(self) -> PageInfo:
        if self.next_cursor is not None and not self.next_cursor.strip():
            raise ValueError("next_cursor must be non-empty when provided")
        if self.has_more:
            if self.next_cursor is None or not self.next_cursor.strip():
                raise ValueError("next_cursor required when has_more is True")
        elif self.next_cursor is not None:
            raise ValueError("next_cursor must be None when has_more is False")
        return self


T = TypeVar("T")


class Page(SettingsModel, Generic[T]):
    items: tuple[T, ...] = ()
    page: PageInfo

    @model_validator(mode="after")
    def bounded_items(self) -> Page[T]:
        if len(self.items) > self.page.limit:
            raise ValueError("page items exceed limit")
        # PageInfo already enforces has_more <-> next_cursor invariant.
        return self


class ServiceResponse(SettingsModel, Generic[T]):
    """Stable consumer envelope — no storage technology fields."""

    contract_version: str = Field(default=DATA_SERVICE_CONTRACT_VERSION, min_length=1)
    capability_id: str = Field(min_length=1, max_length=64)
    data: T
    freshness: FreshnessMetadata = Field(default_factory=FreshnessMetadata)
    consistency: ConsistencySemantics = ConsistencySemantics.PUBLISHED_SNAPSHOT


# --- Storage-neutral records ---


class WorkMetadataRecord(SettingsModel):
    work_id: str
    doi: str | None = None
    title: str | None = None
    publication_year: int | None = None
    publication_date: str | None = None
    work_type: str | None = None
    language: str | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    primary_source_id: str | None = None
    primary_publisher_id: str | None = None
    author_ids: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    institution_ids: tuple[str, ...] = ()


class JournalMetricsRecord(SettingsModel):
    source_id: str
    unique_author_count: int = Field(ge=0)
    active_work_count: int = Field(ge=0)


class PublisherSummaryRecord(SettingsModel):
    publisher_id: str
    unique_author_count: int = Field(ge=0)
    active_work_count: int = Field(ge=0)


class PublisherTopicMetricRecord(SettingsModel):
    publisher_id: str
    topic_id: str
    publication_year: int | None = None
    active_work_count: int = Field(ge=0)
