"""Typed limits, evidence classes, reports, and errors for OpenAlex source profiling."""

import json
import re
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_platform.config import SampleSelectionConfig

MAX_FILES = 1
MAX_FILE_SIZE_BYTES = 25_000_000
MAX_PROFILE_RECORDS = 100
MAX_DECODED_SAMPLE_BYTES = 25_000_000
_MAX_PROFILE_RECORDS_CEILING = 10_000


class EvidenceType(StrEnum):
    """How a reported fact is known."""

    VERIFIED_METADATA = "VERIFIED_METADATA"
    """Declared by validated manifest metadata or HTTP response metadata."""
    EXACT_FILE_METADATA = "EXACT_FILE_METADATA"
    """Exact for the whole profiled file (Parquet footer or fully read bytes)."""
    SAMPLED_OBSERVATION = "SAMPLED_OBSERVATION"
    """Observed only in the bounded record sample; not a whole-file fact."""
    UNKNOWN = "UNKNOWN"
    """Not determinable within the configured bounds."""


class OpenAlexProfileError(Exception):
    """Safe base exception for source-profiling failures."""


class OpenAlexUnsupportedFormatError(OpenAlexProfileError):
    """The source format is unsupported or its bytes contradict the declared format."""


class OpenAlexMalformedJSONLError(OpenAlexProfileError):
    """A sampled JSONL line is not one valid UTF-8 JSON object."""


class OpenAlexDecompressionError(OpenAlexProfileError):
    """A gzip stream is truncated, corrupt, or otherwise cannot be decompressed."""


class OpenAlexMalformedParquetError(OpenAlexProfileError):
    """A Parquet file is truncated or its footer/data cannot be read."""


class OpenAlexEmptySourceError(OpenAlexProfileError):
    """The source has no bytes or no records to profile."""


class OpenAlexProfileLimitError(OpenAlexProfileError):
    """A profiling bound (files, bytes, decoded bytes, or fields) was exceeded."""


class OpenAlexSizeMismatchError(OpenAlexProfileError):
    """Declared metadata sizes disagree with each other or with observed bytes."""


class ProfilingLimits(BaseModel):
    """Validated resource bounds; invalid or missing values are never unlimited."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    max_files: int = Field(default=MAX_FILES, strict=True, ge=1, le=MAX_FILES)
    max_file_size_bytes: int = Field(default=MAX_FILE_SIZE_BYTES, strict=True, gt=0)
    max_profile_records: int = Field(
        default=MAX_PROFILE_RECORDS, strict=True, gt=0, le=_MAX_PROFILE_RECORDS_CEILING
    )
    max_decoded_sample_bytes: int = Field(
        default=MAX_DECODED_SAMPLE_BYTES, strict=True, gt=0
    )

    def sample_selection(self) -> SampleSelectionConfig:
        return SampleSelectionConfig(
            max_files=self.max_files, max_file_size_bytes=self.max_file_size_bytes
        )


FieldKind = Literal["scalar", "struct", "list", "map"]
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_$@:-]+")


def child_path(parent: str, key: str) -> str:
    """Join an object member path; unusual keys are JSON-quoted to stay unambiguous."""
    component = key if _PLAIN_KEY.fullmatch(key) else f"[{json.dumps(key)}]"
    if not parent:
        return component
    return f"{parent}{component}" if component.startswith("[") else f"{parent}.{component}"


class FieldProfile(BaseModel):
    """One schema path. ``a.b`` is an object member, ``a[]`` an array element."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    kind: FieldKind
    nullable: bool | None
    evidence: Literal[EvidenceType.EXACT_FILE_METADATA, EvidenceType.SAMPLED_OBSERVATION]
    sampled_present_count: int | None = Field(default=None, ge=0)
    sampled_missing_count: int | None = Field(default=None, ge=0)
    sampled_null_count: int | None = Field(default=None, ge=0)


EVIDENCE_FIELDS = (
    "source_format",
    "compression",
    "content_type",
    "snapshot_date",
    "updated_date",
    "declared_size_bytes",
    "declared_record_count",
    "content_length_bytes",
    "observed_size_bytes",
    "schema_fields",
    "nested_fields",
    "nullable_fields",
    "row_count",
    "row_group_count",
    "column_count",
    "physical_column_count",
    "statistics_available",
    "parquet_created_by",
    "sampled_record_count",
)


class OpenAlexSourceProfile(BaseModel):
    """Evidence-classified profile of one bounded OpenAlex source file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_uri: str = Field(min_length=1)
    source_format: Literal["jsonl", "parquet"]
    compression: str | None
    content_type: str | None
    snapshot_date: date | None
    updated_date: date | None
    declared_size_bytes: int | None = Field(ge=0)
    declared_record_count: int | None = Field(ge=0)
    content_length_bytes: int | None = Field(ge=0)
    observed_size_bytes: int | None = Field(ge=0)
    bytes_read: int = Field(ge=0)
    decoded_bytes_sampled: int | None = Field(ge=0)
    schema_fields: tuple[FieldProfile, ...]
    nested_fields: tuple[str, ...]
    nullable_fields: tuple[str, ...]
    row_count: int | None = Field(ge=0)
    row_group_count: int | None = Field(ge=0)
    column_count: int | None = Field(ge=0)
    physical_column_count: int | None = Field(ge=0)
    statistics_available: Literal["all", "partial", "none"] | None
    parquet_created_by: str | None
    sampled_record_count: int = Field(ge=0)
    limits: ProfilingLimits
    evidence: dict[str, EvidenceType]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> "OpenAlexSourceProfile":
        if set(self.evidence) != set(EVIDENCE_FIELDS):
            raise ValueError("evidence must classify exactly the reported facts")
        for name in EVIDENCE_FIELDS:
            value = getattr(self, name)
            unknown = self.evidence[name] is EvidenceType.UNKNOWN
            if (value is None) != unknown:
                raise ValueError(f"{name} must be UNKNOWN exactly when it is unavailable")
        if self.sampled_record_count > self.limits.max_profile_records:
            raise ValueError("sampled_record_count exceeds the configured record bound")
        if self.evidence["sampled_record_count"] is not EvidenceType.SAMPLED_OBSERVATION:
            raise ValueError("sampled_record_count is always a sampled observation")
        return self


class RepresentationEvidence(BaseModel):
    """Manifest-only evidence for whether one Works representation is available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_format: Literal["jsonl", "parquet"]
    manifest_uri: str
    status: Literal["VERIFIED", "UNAVAILABLE"]
    evidence: Literal[EvidenceType.VERIFIED_METADATA, EvidenceType.UNKNOWN]
    listed_file_count: int | None = Field(ge=0)
    eligible_file_count: int | None = Field(ge=0)
    smallest_eligible_size_bytes: int | None = Field(ge=0)
    error_category: str | None


class OpenAlexProfileRun(BaseModel):
    """Safe structured result of one explicitly invoked bounded profiling run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    limits: ProfilingLimits
    profile_format: Literal["jsonl", "parquet"]
    representations: tuple[RepresentationEvidence, ...]
    selected_uri: str | None
    profiles: tuple[OpenAlexSourceProfile, ...]
    error_category: str | None

    @model_validator(mode="after")
    def enforce_file_bound(self) -> "OpenAlexProfileRun":
        if len(self.profiles) > self.limits.max_files:
            raise ValueError("profile run exceeds the configured file bound")
        return self
