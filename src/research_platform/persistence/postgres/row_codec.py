"""Encode/decode control and lineage rows for PostgreSQL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping
from uuid import UUID

from research_platform.canonical.openalex.lineage_codec import (  # moved for DE-07; re-exported
    lineage_columns as lineage_columns,
    lineage_from_row as lineage_from_row,
)
from research_platform.canonical.openalex.models import CanonicalActivityState
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)


def pipeline_run_from_row(row: Mapping[str, Any]) -> PipelineRun:
    return PipelineRun.model_validate(dict(row))


def source_file_from_row(row: Mapping[str, Any]) -> SourceFileControl:
    return SourceFileControl.model_validate(dict(row))


def record_provenance_from_row(row: Mapping[str, Any]) -> RecordProvenance:
    return RecordProvenance.model_validate(dict(row))


def as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def as_date(value: date | None) -> date | None:
    return value


def as_dt(value: datetime) -> datetime:
    return value


def enum_or_none(value: FailureCategory | ControlStatus | PipelineRunStatus | None) -> str | None:
    if value is None:
        return None
    return value.value


def activity_value(value: CanonicalActivityState) -> str:
    return value.value
