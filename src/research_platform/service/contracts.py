"""Typed consumer request/response contracts (Step 18 / #26)."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from research_platform.service.models import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PUBLICATION_YEAR,
    MIN_PUBLICATION_YEAR,
    JournalMetricsRecord,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    SettingsModel,
    WorkMetadataRecord,
)


class PageRequest(SettingsModel):
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    cursor: str | None = None


class WorkMetadataRequest(SettingsModel):
    work_id: str | None = Field(default=None, min_length=1, max_length=128)
    doi: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def exactly_one_identifier(self) -> Self:
        has_work = bool(self.work_id and self.work_id.strip())
        has_doi = bool(self.doi and self.doi.strip())
        if has_work and has_doi:
            raise ValueError("provide exactly one of work_id or doi")
        if not has_work and not has_doi:
            raise ValueError("provide exactly one of work_id or doi")
        if self.work_id is not None and not self.work_id.strip():
            raise ValueError("work_id must be non-empty")
        if self.doi is not None and not self.doi.strip():
            raise ValueError("doi must be non-empty")
        return self


class ResearchDiscoveryRequest(SettingsModel):
    page: PageRequest = Field(default_factory=PageRequest)
    publication_year_from: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    publication_year_to: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    primary_publisher_id: str | None = Field(default=None, min_length=1, max_length=128)
    primary_source_id: str | None = Field(default=None, min_length=1, max_length=128)
    work_type: str | None = Field(default=None, min_length=1, max_length=64)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    oa_status: str | None = Field(default=None, min_length=1, max_length=64)
    topic_id: str | None = Field(default=None, min_length=1, max_length=128)
    institution_id: str | None = Field(default=None, min_length=1, max_length=128)
    author_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def year_range_order(self) -> Self:
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("publication_year_from must be <= publication_year_to")
        return self


class JournalMetricsRequest(SettingsModel):
    source_id: str = Field(min_length=1, max_length=128)


class PublisherSummaryRequest(SettingsModel):
    publisher_id: str = Field(min_length=1, max_length=128)


class PublisherTopicAnalyticsRequest(SettingsModel):
    publisher_id: str = Field(min_length=1, max_length=128)
    page: PageRequest = Field(default_factory=PageRequest)
    publication_year_from: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    publication_year_to: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    topic_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def year_range_order(self) -> Self:
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("publication_year_from must be <= publication_year_to")
        return self


# Response payload aliases (typed records reused as consumer data).
WorkMetadataData = WorkMetadataRecord
JournalMetricsData = JournalMetricsRecord
PublisherSummaryData = PublisherSummaryRecord
PublisherTopicMetricData = PublisherTopicMetricRecord
