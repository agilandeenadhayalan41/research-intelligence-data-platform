"""Typed consumer request/response contracts (Step 18 / #26)."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

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


def _trim_nonempty_str(value: object) -> object:
    """Trim surrounding whitespace; reject empty/whitespace-only strings.

    Chosen behavior: store the trimmed value. Whitespace-only input is invalid
    (INVALID_ARGUMENT via ValidationError), never a silent NOT_FOUND lookup.
    """
    if value is None or not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("must be non-empty after trimming whitespace")
    return trimmed


NonEmptyStr = Annotated[str, BeforeValidator(_trim_nonempty_str)]


class PageRequest(SettingsModel):
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    cursor: str | None = None


class WorkMetadataRequest(SettingsModel):
    work_id: str | None = Field(default=None, min_length=1, max_length=128)
    doi: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def exactly_one_identifier(self) -> Self:
        # Preserve explicit semantic checks; also trim stored identifiers.
        work_raw = self.work_id
        doi_raw = self.doi
        work_trim = work_raw.strip() if work_raw is not None else None
        doi_trim = doi_raw.strip() if doi_raw is not None else None
        has_work = bool(work_trim)
        has_doi = bool(doi_trim)
        if has_work and has_doi:
            raise ValueError("provide exactly one of work_id or doi")
        if not has_work and not has_doi:
            raise ValueError("provide exactly one of work_id or doi")
        if work_raw is not None and not work_trim:
            raise ValueError("work_id must be non-empty")
        if doi_raw is not None and not doi_trim:
            raise ValueError("doi must be non-empty")
        object.__setattr__(self, "work_id", work_trim)
        object.__setattr__(self, "doi", doi_trim)
        return self


class ResearchDiscoveryRequest(SettingsModel):
    page: PageRequest = Field(default_factory=PageRequest)
    publication_year_from: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    publication_year_to: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    primary_publisher_id: NonEmptyStr | None = Field(default=None, max_length=128)
    primary_source_id: NonEmptyStr | None = Field(default=None, max_length=128)
    work_type: NonEmptyStr | None = Field(default=None, max_length=64)
    language: NonEmptyStr | None = Field(default=None, max_length=32)
    oa_status: NonEmptyStr | None = Field(default=None, max_length=64)
    topic_id: NonEmptyStr | None = Field(default=None, max_length=128)
    institution_id: NonEmptyStr | None = Field(default=None, max_length=128)
    author_id: NonEmptyStr | None = Field(default=None, max_length=128)

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
    source_id: NonEmptyStr = Field(max_length=128)


class PublisherSummaryRequest(SettingsModel):
    publisher_id: NonEmptyStr = Field(max_length=128)


class PublisherTopicAnalyticsRequest(SettingsModel):
    publisher_id: NonEmptyStr = Field(max_length=128)
    page: PageRequest = Field(default_factory=PageRequest)
    publication_year_from: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    publication_year_to: int | None = Field(
        default=None, ge=MIN_PUBLICATION_YEAR, le=MAX_PUBLICATION_YEAR
    )
    topic_id: NonEmptyStr | None = Field(default=None, max_length=128)

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
