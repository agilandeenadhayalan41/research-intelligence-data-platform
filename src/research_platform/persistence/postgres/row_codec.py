"""Encode/decode control and lineage rows for PostgreSQL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping
from uuid import UUID

from research_platform.canonical.openalex.models import CanonicalActivityState, CanonicalLineage
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)


def lineage_columns(lineage: CanonicalLineage) -> dict[str, Any]:
    return {
        "source_asset_id": lineage.source_asset_id,
        "source_checksum_sha256": lineage.source_checksum_sha256,
        "lineage_source_updated_date": lineage.source_updated_date,
        "run_id": lineage.run_id,
        "processed_at": lineage.processed_at,
        "activity_state": lineage.activity_state.value,
        "deleted_at": lineage.deleted_at,
    }


def lineage_from_row(row: Mapping[str, Any]) -> CanonicalLineage:
    return CanonicalLineage.model_validate(
        {
            "source_asset_id": row["source_asset_id"],
            "source_checksum_sha256": row["source_checksum_sha256"],
            "source_updated_date": row.get("lineage_source_updated_date"),
            "run_id": row["run_id"],
            "processed_at": row["processed_at"],
            "activity_state": row["activity_state"],
            "deleted_at": row.get("deleted_at"),
        }
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
