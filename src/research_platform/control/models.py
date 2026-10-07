"""Validated pipeline-control and record-lineage models (contracts only)."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_SHA256 = r"^[a-f0-9]{64}$"
_SAFE_TOKEN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ControlStatus(StrEnum):
    """Mutable source-file control states."""

    DISCOVERED = "DISCOVERED"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PipelineRunStatus(StrEnum):
    """Mutable pipeline-run control states."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class FailureCategory(StrEnum):
    """Safe, non-sensitive failure categories for control diagnostics."""

    RETRIEVAL = "RETRIEVAL"
    CHECKSUM = "CHECKSUM"
    LANDING = "LANDING"
    CANONICAL = "CANONICAL"
    CLAIM = "CLAIM"
    STALE_CLAIM = "STALE_CLAIM"
    VALIDATION = "VALIDATION"
    UNKNOWN = "UNKNOWN"


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class PipelineRun(SettingsModel):
    """One pipeline execution identity and mutable run status."""

    run_id: UUID
    source: str = Field(min_length=1, max_length=128, pattern=_SAFE_TOKEN)
    pipeline_name: str = Field(min_length=1, max_length=128, pattern=_SAFE_TOKEN)
    status: PipelineRunStatus
    attempt: int = Field(strict=True, ge=1)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    failure_category: FailureCategory | None = None
    failure_message: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_run_lifecycle(self) -> Self:
        # created_at <= started_at <= completed_at <= updated_at (when present)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.started_at is not None:
            if self.started_at < self.created_at:
                raise ValueError("started_at must not precede created_at")
            if self.updated_at < self.started_at:
                raise ValueError("updated_at must not precede started_at")
        if self.completed_at is not None:
            if self.started_at is None:
                raise ValueError("completed_at requires started_at")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not precede started_at")
            if self.updated_at < self.completed_at:
                raise ValueError("updated_at must not precede completed_at")

        if self.status is PipelineRunStatus.PENDING:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("PENDING runs must not set started_at or completed_at")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("PENDING runs must not set failure diagnostics")
        elif self.status is PipelineRunStatus.PROCESSING:
            if self.started_at is None:
                raise ValueError("PROCESSING runs require started_at")
            if self.completed_at is not None:
                raise ValueError("PROCESSING runs must not set completed_at")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("PROCESSING runs must not set failure diagnostics")
        elif self.status is PipelineRunStatus.SUCCESS:
            if self.started_at is None or self.completed_at is None:
                raise ValueError("SUCCESS runs require started_at and completed_at")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("SUCCESS runs must not set failure diagnostics")
        elif self.status is PipelineRunStatus.FAILED:
            if self.started_at is None or self.completed_at is None:
                raise ValueError("FAILED runs require started_at and completed_at")
            if self.failure_category is None:
                raise ValueError("FAILED runs require failure_category")
            if self.failure_message is None or not self.failure_message.strip():
                raise ValueError("FAILED runs require a non-empty failure_message")
            if _looks_sensitive(self.failure_message):
                raise ValueError("failure_message must not contain sensitive material")
        return self


class SourceFileControl(SettingsModel):
    """Mutable control row for one stable source asset identity.

    Does not carry payload bytes. Immutable retrieval metadata remains
    ``IngestionProvenance`` after raw landing.
    """

    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    run_id: UUID
    source: str = Field(min_length=1, max_length=128, pattern=_SAFE_TOKEN)
    entity: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    source_uri: str = Field(min_length=1, max_length=2048)
    snapshot_date: date
    updated_date: date | None = None
    content_format: Literal["jsonl", "parquet", "csv"]
    declared_size_bytes: int | None = Field(default=None, strict=True, ge=0)
    source_checksum_sha256: str | None = Field(default=None, pattern=_SHA256)
    raw_object_key: str | None = Field(default=None, min_length=1, max_length=1024)
    status: ControlStatus
    attempt_count: int = Field(strict=True, ge=0)
    claimed_by: str | None = Field(default=None, min_length=1, max_length=128, pattern=_SAFE_TOKEN)
    claim_token: UUID | None = None
    claimed_at: AwareDatetime | None = None
    lease_expires_at: AwareDatetime | None = None
    processed_at: AwareDatetime | None = None
    failure_category: FailureCategory | None = None
    failure_message: str | None = Field(default=None, max_length=512)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("snapshot_date", "updated_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
        if value is not None and not isinstance(value, (date, str)):
            raise ValueError("date fields must be calendar dates or YYYY-MM-DD strings")
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("date fields must use YYYY-MM-DD") from error
            if parsed.isoformat() != value:
                raise ValueError("date fields must use YYYY-MM-DD")
            return parsed
        return value

    @field_validator("raw_object_key")
    @classmethod
    def validate_raw_object_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or value.endswith("/"):
            raise ValueError("raw_object_key must be a normalized relative path")
        if value.startswith("/") or value.startswith("\\") or "\\" in value:
            raise ValueError("raw_object_key must be a relative POSIX path")
        if "\x00" in value or any(character.isspace() for character in value):
            raise ValueError("raw_object_key contains unsafe characters")
        parts = value.split("/")
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("raw_object_key must be a nested relative path")
        return value

    @model_validator(mode="after")
    def validate_control_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if "\x00" in self.source_uri or any(ch.isspace() and ch not in " \t" for ch in self.source_uri):
            raise ValueError("source_uri contains unsafe characters")
        if self.failure_message is not None:
            if not self.failure_message.strip():
                raise ValueError("failure_message must be non-empty when set")
            if _looks_sensitive(self.failure_message):
                raise ValueError("failure_message must not contain sensitive material")

        claim_fields = (
            self.claimed_by,
            self.claim_token,
            self.claimed_at,
            self.lease_expires_at,
        )
        claim_set = [field is not None for field in claim_fields]
        if any(claim_set) and not all(claim_set):
            raise ValueError("claim fields must be set together")
        if self.claimed_at is not None:
            if self.claimed_at < self.created_at:
                raise ValueError("claimed_at must not precede created_at")
            if self.updated_at < self.claimed_at:
                raise ValueError("updated_at must not precede claimed_at")
        if self.claimed_at is not None and self.lease_expires_at is not None:
            if self.lease_expires_at <= self.claimed_at:
                raise ValueError("lease_expires_at must be after claimed_at")
        if self.processed_at is not None:
            if self.processed_at < self.created_at:
                raise ValueError("processed_at must not precede created_at")
            if self.updated_at < self.processed_at:
                raise ValueError("updated_at must not precede processed_at")
            if self.claimed_at is not None and self.processed_at < self.claimed_at:
                raise ValueError("processed_at must not precede claimed_at")

        if self.status is ControlStatus.DISCOVERED:
            if any(claim_set):
                raise ValueError("DISCOVERED files must not hold an active claim")
            if self.processed_at is not None:
                raise ValueError("DISCOVERED files must not set processed_at")
            if self.raw_object_key is not None:
                raise ValueError("DISCOVERED files must not set raw_object_key")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("DISCOVERED files must not set failure diagnostics")
            if self.attempt_count != 0:
                raise ValueError("DISCOVERED files require attempt_count=0")
        elif self.status is ControlStatus.PROCESSING:
            if not all(claim_set):
                raise ValueError("PROCESSING files require a complete active claim")
            if self.processed_at is not None:
                raise ValueError("PROCESSING files must not set processed_at")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("PROCESSING files must not set failure diagnostics")
            if self.attempt_count < 1:
                raise ValueError("PROCESSING files require attempt_count >= 1")
        elif self.status is ControlStatus.SUCCESS:
            if any(claim_set):
                raise ValueError("SUCCESS files must release claim ownership")
            if self.processed_at is None:
                raise ValueError("SUCCESS files require processed_at")
            if self.source_checksum_sha256 is None:
                raise ValueError("SUCCESS files require source_checksum_sha256")
            if self.raw_object_key is None:
                raise ValueError("SUCCESS files require raw_object_key")
            if self.failure_category is not None or self.failure_message is not None:
                raise ValueError("SUCCESS files must not set failure diagnostics")
            if self.attempt_count < 1:
                raise ValueError("SUCCESS files require attempt_count >= 1")
        elif self.status is ControlStatus.FAILED:
            if any(claim_set):
                raise ValueError("FAILED files must release claim ownership")
            if self.processed_at is None:
                raise ValueError("FAILED files require processed_at")
            if self.failure_category is None or self.failure_message is None:
                raise ValueError("FAILED files require failure diagnostics")
            if self.attempt_count < 1:
                raise ValueError("FAILED files require attempt_count >= 1")
        return self


class RecordProvenance(SettingsModel):
    """Record-level lineage for later canonical ingestion (no payload bytes)."""

    record_id: str = Field(min_length=1, max_length=256)
    entity_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    source_checksum_sha256: str = Field(pattern=_SHA256)
    run_id: UUID
    source_uri: str = Field(min_length=1, max_length=2048)
    processed_at: AwareDatetime
    source_updated_date: date | None = None

    @field_validator("source_updated_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
        if value is not None and not isinstance(value, (date, str)):
            raise ValueError("date fields must be calendar dates or YYYY-MM-DD strings")
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("date fields must use YYYY-MM-DD") from error
            if parsed.isoformat() != value:
                raise ValueError("date fields must use YYYY-MM-DD")
            return parsed
        return value


def dumps_control_model(model: BaseModel) -> bytes:
    """Deterministic UTF-8 JSON serialization with sorted keys and a trailing newline."""
    payload = model.model_dump(mode="json")
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{text}\n".encode("utf-8")


def _looks_sensitive(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "password",
        "secret",
        "api_key",
        "apikey",
        "private_key",
        "begin rsa",
        "authorization:",
        "bearer ",
        "postgres_dsn",
        "credential",
    )
    return any(marker in lowered for marker in markers)
