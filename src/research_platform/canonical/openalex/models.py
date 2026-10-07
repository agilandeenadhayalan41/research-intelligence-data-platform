"""Validated portable canonical OpenAlex entities and relationships."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CanonicalActivityState(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class ReferenceStatus(StrEnum):
    """Status of a referenced work id after normalization."""

    RESOLVED_ID = "RESOLVED_ID"  # well-formed OpenAlex work id
    MALFORMED = "MALFORMED"
    MISSING = "MISSING"


class ArrayPresence(StrEnum):
    """How a multi-valued source array was observed."""

    MISSING = "MISSING"  # key absent
    NULL = "NULL"  # key present with JSON null
    EMPTY = "EMPTY"  # key present with []
    PRESENT = "PRESENT"  # key present with one or more elements


class CanonicalLineage(SettingsModel):
    """Lineage shared by canonical rows produced from one source asset."""

    source_asset_id: str = Field(min_length=1, max_length=128)
    source_checksum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_updated_date: date | None = None
    run_id: UUID
    processed_at: AwareDatetime
    activity_state: CanonicalActivityState = CanonicalActivityState.ACTIVE
    deleted_at: AwareDatetime | None = None

    @field_validator("source_updated_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
        if value is not None and not isinstance(value, (date, str)):
            raise ValueError("date fields must be calendar dates or YYYY-MM-DD strings")
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("date fields must use YYYY-MM-DD")
            return parsed
        return value

    @model_validator(mode="after")
    def validate_activity(self) -> Self:
        if self.activity_state is CanonicalActivityState.ACTIVE:
            if self.deleted_at is not None:
                raise ValueError("ACTIVE rows must not set deleted_at")
            return self
        if self.deleted_at is None:
            raise ValueError("DELETED rows require deleted_at")
        if self.deleted_at < self.processed_at:
            raise ValueError("deleted_at must not precede processed_at")
        return self


class Work(SettingsModel):
    """Canonical Work entity (not a flattened join table)."""

    work_id: str = Field(pattern=r"^W\d+$")
    work_id_url: str = Field(min_length=1)
    doi: str | None = None
    title: str | None = None
    publication_year: int | None = Field(default=None, strict=True, ge=1000, le=3000)
    publication_date: date | None = None
    work_type: str | None = None
    language: str | None = None
    cited_by_count: int | None = Field(default=None, strict=True, ge=0)
    referenced_works_count: int | None = Field(default=None, strict=True, ge=0)
    is_oa: bool | None = None
    oa_status: str | None = None
    primary_source_id: str | None = Field(default=None, pattern=r"^S\d+$")
    primary_publisher_id: str | None = Field(default=None, pattern=r"^P\d+$")
    source_created_date: date | None = None
    source_updated_date: date | None = None
    authorships_presence: ArrayPresence
    topics_presence: ArrayPresence
    keywords_presence: ArrayPresence
    mesh_presence: ArrayPresence
    referenced_works_presence: ArrayPresence
    locations_presence: ArrayPresence
    grants_presence: ArrayPresence
    lineage: CanonicalLineage

    @field_validator(
        "publication_date",
        "source_created_date",
        "source_updated_date",
        mode="before",
    )
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
        if value is not None and not isinstance(value, (date, str)):
            raise ValueError("date fields must be calendar dates or YYYY-MM-DD strings")
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("date fields must use YYYY-MM-DD")
            return parsed
        return value


class Author(SettingsModel):
    author_id: str = Field(pattern=r"^A\d+$")
    author_id_url: str = Field(min_length=1)
    display_name: str | None = None
    orcid: str | None = None
    lineage: CanonicalLineage


class Institution(SettingsModel):
    institution_id: str = Field(pattern=r"^I\d+$")
    institution_id_url: str = Field(min_length=1)
    display_name: str | None = None
    ror: str | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    institution_type: str | None = None
    lineage: CanonicalLineage


class Source(SettingsModel):
    source_id: str = Field(pattern=r"^S\d+$")
    source_id_url: str = Field(min_length=1)
    display_name: str | None = None
    source_type: str | None = None
    issn_l: str | None = None
    host_publisher_id: str | None = Field(default=None, pattern=r"^P\d+$")
    lineage: CanonicalLineage


class Publisher(SettingsModel):
    publisher_id: str = Field(pattern=r"^P\d+$")
    publisher_id_url: str = Field(min_length=1)
    display_name: str | None = None
    lineage: CanonicalLineage


class Topic(SettingsModel):
    topic_id: str = Field(pattern=r"^T\d+$")
    topic_id_url: str = Field(min_length=1)
    display_name: str | None = None
    lineage: CanonicalLineage


class Funder(SettingsModel):
    funder_id: str = Field(pattern=r"^F\d+$")
    funder_id_url: str = Field(min_length=1)
    display_name: str | None = None
    lineage: CanonicalLineage


class WorkAuthor(SettingsModel):
    """Authorship grain: one row per (work_id, authorship_index)."""

    work_id: str = Field(pattern=r"^W\d+$")
    authorship_index: int = Field(strict=True, ge=0)
    author_id: str | None = Field(default=None, pattern=r"^A\d+$")
    author_position: str | None = None
    is_corresponding: bool | None = None
    raw_author_name: str | None = None
    lineage: CanonicalLineage


class WorkAuthorInstitution(SettingsModel):
    """Preserves author↔institution association within an authorship."""

    work_id: str = Field(pattern=r"^W\d+$")
    authorship_index: int = Field(strict=True, ge=0)
    institution_index: int = Field(strict=True, ge=0)
    institution_id: str | None = Field(default=None, pattern=r"^I\d+$")
    lineage: CanonicalLineage


class WorkTopic(SettingsModel):
    work_id: str = Field(pattern=r"^W\d+$")
    topic_id: str = Field(pattern=r"^T\d+$")
    score: float | None = None
    lineage: CanonicalLineage


class WorkKeyword(SettingsModel):
    work_id: str = Field(pattern=r"^W\d+$")
    keyword_id: str = Field(min_length=1)
    display_name: str | None = None
    score: float | None = None
    lineage: CanonicalLineage


class WorkReference(SettingsModel):
    work_id: str = Field(pattern=r"^W\d+$")
    referenced_work_id: str | None = Field(default=None, pattern=r"^W\d+$")
    raw_reference: str | None = None
    reference_status: ReferenceStatus
    lineage: CanonicalLineage


class WorkMesh(SettingsModel):
    work_id: str = Field(pattern=r"^W\d+$")
    descriptor_ui: str = Field(min_length=1)
    descriptor_name: str | None = None
    qualifier_ui: str | None = None
    qualifier_name: str | None = None
    is_major_topic: bool | None = None
    lineage: CanonicalLineage


class WorkLocation(SettingsModel):
    work_id: str = Field(pattern=r"^W\d+$")
    location_index: int = Field(strict=True, ge=0)
    source_id: str | None = Field(default=None, pattern=r"^S\d+$")
    is_oa: bool | None = None
    landing_page_url: str | None = None
    pdf_url: str | None = None
    license: str | None = None
    version: str | None = None
    is_primary: bool = False
    lineage: CanonicalLineage


class WorkGrant(SettingsModel):
    """Grain: (work_id, funder_id, award_id) with empty award_id when absent."""

    work_id: str = Field(pattern=r"^W\d+$")
    funder_id: str | None = Field(default=None, pattern=r"^F\d+$")
    award_id: str = ""
    funder_display_name: str | None = None
    lineage: CanonicalLineage


class CanonicalWorkBundle(SettingsModel):
    """Normalized canonical outputs for one mapped OpenAlex Work record."""

    work: Work
    authors: tuple[Author, ...] = ()
    institutions: tuple[Institution, ...] = ()
    sources: tuple[Source, ...] = ()
    publishers: tuple[Publisher, ...] = ()
    topics: tuple[Topic, ...] = ()
    funders: tuple[Funder, ...] = ()
    work_authors: tuple[WorkAuthor, ...] = ()
    work_author_institutions: tuple[WorkAuthorInstitution, ...] = ()
    work_topics: tuple[WorkTopic, ...] = ()
    work_keywords: tuple[WorkKeyword, ...] = ()
    work_references: tuple[WorkReference, ...] = ()
    work_mesh: tuple[WorkMesh, ...] = ()
    work_locations: tuple[WorkLocation, ...] = ()
    work_grants: tuple[WorkGrant, ...] = ()
