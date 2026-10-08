"""Typed orchestration models (Step 20 / #25).

Contracts only — no Airflow/Composer/Dataform runtime dependency.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from research_platform.service.models import SettingsModel


ORCHESTRATION_CONTRACT_VERSION = "orchestration-contract-v1"
PIPELINE_NAME = "openalex-works-orchestration"
EVIDENCE_LABEL = "CONTRACT_ONLY"

# Development sample bounds remain separate (Step 19). Production backfill
# ceilings below are contract limits only — not deployed schedules.
DEFAULT_MAX_RETRY_ATTEMPTS = 3
MAX_RETRY_ATTEMPTS = 5
MAX_BACKFILL_FILES_PER_RUN = 10_000
MAX_BACKFILL_FILE_SIZE_BYTES = 5_000_000_000
MAX_BACKFILL_RANGE_DAYS = 366


class TaskId(StrEnum):
    """Stable orchestration task inventory for OpenAlex Works."""

    DISCOVER = "DISCOVER"
    REGISTER = "REGISTER"
    INGEST = "INGEST"
    CANONICALIZE = "CANONICALIZE"
    APPLY_DELETIONS = "APPLY_DELETIONS"
    ANALYTICAL_PUBLICATION = "ANALYTICAL_PUBLICATION"
    PRE_SERVING_QUALITY = "PRE_SERVING_QUALITY"
    STAGE_GOLD = "STAGE_GOLD"
    PRE_VISIBLE_QUALITY = "PRE_VISIBLE_QUALITY"
    PUBLISH_SUCCESS = "PUBLISH_SUCCESS"
    FINAL_VALIDATION = "FINAL_VALIDATION"


TASK_INVENTORY: tuple[TaskId, ...] = (
    TaskId.DISCOVER,
    TaskId.REGISTER,
    TaskId.INGEST,
    TaskId.CANONICALIZE,
    TaskId.APPLY_DELETIONS,
    TaskId.ANALYTICAL_PUBLICATION,
    TaskId.PRE_SERVING_QUALITY,
    TaskId.STAGE_GOLD,
    TaskId.PRE_VISIBLE_QUALITY,
    TaskId.PUBLISH_SUCCESS,
    TaskId.FINAL_VALIDATION,
)


class SchedulingClass(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED_INCREMENTAL = "SCHEDULED_INCREMENTAL"
    BACKFILL = "BACKFILL"


class FailureCategory(StrEnum):
    """Safe failure categories for retry classification (no payload/SQL)."""

    TRANSIENT_TRANSPORT = "TRANSIENT_TRANSPORT"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    BOUNDS_CONFIG = "BOUNDS_CONFIG"
    CANONICAL_CONFLICT = "CANONICAL_CONFLICT"
    RESTORE_REQUIRED = "RESTORE_REQUIRED"
    QUALITY_HARD_GATE = "QUALITY_HARD_GATE"
    PUBLICATION_CONFLICT = "PUBLICATION_CONFLICT"
    VALIDATION = "VALIDATION"
    UNKNOWN = "UNKNOWN"


class Retryability(StrEnum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"


class PublicationConcurrencyStrategy(StrEnum):
    """How concurrent analytical publication owns decision scratch sets.

    Step-15 SQL file names (``work_publication_decisions``,
    ``accepted_work_ids``, ``relationship_publish_work_ids``) are
    **contract addresses only**. Concurrent runs must not share one
    persistent global scratch table set.
    """

    TEMP_TABLES = "TEMP_TABLES"
    RUN_SCOPED = "RUN_SCOPED"
    SINGLE_WRITER = "SINGLE_WRITER"


# Recommended default for future GCP deployment when all dependent statements
# can execute in one BigQuery script/session. Not deployed by this package.
RECOMMENDED_PUBLICATION_STRATEGY = PublicationConcurrencyStrategy.TEMP_TABLES

STEP15_DECISION_CONTRACT_NAMES: tuple[str, ...] = (
    "work_publication_decisions",
    "accepted_work_ids",
    "relationship_publish_work_ids",
)


class AnalyticalPublicationStep(StrEnum):
    """Step-15 publication unit order (orchestration ownership only)."""

    FREEZE_WORK_PUBLICATION_DECISIONS = "FREEZE_WORK_PUBLICATION_DECISIONS"
    DERIVE_ACCEPTED_WORK_IDS = "DERIVE_ACCEPTED_WORK_IDS"
    DERIVE_RELATIONSHIP_PUBLISH_WORK_IDS = "DERIVE_RELATIONSHIP_PUBLISH_WORK_IDS"
    MERGE_WORKS = "MERGE_WORKS"
    REPLACE_ELIGIBLE_RELATIONSHIPS = "REPLACE_ELIGIBLE_RELATIONSHIPS"


ANALYTICAL_PUBLICATION_ORDER: tuple[AnalyticalPublicationStep, ...] = (
    AnalyticalPublicationStep.FREEZE_WORK_PUBLICATION_DECISIONS,
    AnalyticalPublicationStep.DERIVE_ACCEPTED_WORK_IDS,
    AnalyticalPublicationStep.DERIVE_RELATIONSHIP_PUBLISH_WORK_IDS,
    AnalyticalPublicationStep.MERGE_WORKS,
    AnalyticalPublicationStep.REPLACE_ELIGIBLE_RELATIONSHIPS,
)


class RetryPolicy(SettingsModel):
    """Bounded retry policy for one task class."""

    max_attempts: int = Field(default=DEFAULT_MAX_RETRY_ATTEMPTS, strict=True, ge=1)
    retry_delay_seconds: int = Field(default=30, strict=True, ge=0)
    retryability: Retryability
    retryable_categories: tuple[FailureCategory, ...] = ()
    non_retryable_categories: tuple[FailureCategory, ...] = ()

    @model_validator(mode="after")
    def validate_bounds(self) -> RetryPolicy:
        if self.max_attempts > MAX_RETRY_ATTEMPTS:
            raise ValueError(f"max_attempts must be <= {MAX_RETRY_ATTEMPTS}")
        if self.retryability is Retryability.NON_RETRYABLE and self.max_attempts != 1:
            raise ValueError("non-retryable policies must set max_attempts=1")
        return self


class TaskSpec(SettingsModel):
    """Thin task definition — identity, deps, retry, capability reference."""

    task_id: TaskId
    depends_on: tuple[TaskId, ...] = ()
    retry_policy: RetryPolicy
    capability_ref: str
    description: str = ""
    quality_gate: str | None = None


class TaskMessage(SettingsModel):
    """Safe bounded XCom-style task message (identifiers/metadata only)."""

    run_id: UUID
    publication_version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    task_id: TaskId
    attempt: int = Field(strict=True, ge=1)
    environment: str = Field(min_length=1)
    asset_id: str | None = None
    source_file_id: str | None = None
    object_key_ref: str | None = None
    quality_report_ref: str | None = None
    publication_scope_ref: str | None = None
    scheduling_class: SchedulingClass = SchedulingClass.MANUAL

    @field_validator(
        "publication_version",
        "source",
        "environment",
        "asset_id",
        "source_file_id",
        "object_key_ref",
        "quality_report_ref",
        "publication_scope_ref",
        mode="before",
    )
    @classmethod
    def strip_nonempty_optional(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must be non-empty when provided")
            return stripped
        return value


class PublicationScope(SettingsModel):
    """Concurrency ownership for Step-15 analytical publication scratch sets."""

    run_id: UUID
    publication_version: str = Field(min_length=1)
    strategy: PublicationConcurrencyStrategy = RECOMMENDED_PUBLICATION_STRATEGY

    @field_validator("publication_version", mode="before")
    @classmethod
    def strip_version(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("publication_version must be non-empty")
            return stripped
        return value


class BackfillRequest(SettingsModel):
    """Bounded backfill contract — no unbounded wildcard ranges."""

    source: str = Field(min_length=1)
    start_date: date
    end_date: date
    max_files_per_run: int = Field(strict=True)
    max_file_size_bytes: int = Field(strict=True)
    requested_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    dry_run: bool = True

    @field_validator("source", "requested_by", "reason", mode="before")
    @classmethod
    def strip_required(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must be non-empty")
            return stripped
        return value

    @field_validator("max_files_per_run", "max_file_size_bytes", mode="before")
    @classmethod
    def reject_bool_none(cls, value: object) -> object:
        if value is None:
            raise ValueError("bounds must not be None (unlimited is forbidden)")
        if isinstance(value, bool):
            raise ValueError("bounds must not be bool")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> BackfillRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        span = (self.end_date - self.start_date).days
        if span > MAX_BACKFILL_RANGE_DAYS:
            raise ValueError(
                f"backfill range must be <= {MAX_BACKFILL_RANGE_DAYS} days"
            )
        if self.max_files_per_run < 1:
            raise ValueError("max_files_per_run must be >= 1")
        if self.max_files_per_run > MAX_BACKFILL_FILES_PER_RUN:
            raise ValueError(
                f"max_files_per_run must be <= {MAX_BACKFILL_FILES_PER_RUN}"
            )
        if self.max_file_size_bytes < 1:
            raise ValueError("max_file_size_bytes must be >= 1")
        if self.max_file_size_bytes > MAX_BACKFILL_FILE_SIZE_BYTES:
            raise ValueError(
                f"max_file_size_bytes must be <= {MAX_BACKFILL_FILE_SIZE_BYTES}"
            )
        return self


class OrchestrationEventType(StrEnum):
    TASK_STARTED = "TaskStarted"
    TASK_SUCCEEDED = "TaskSucceeded"
    TASK_FAILED = "TaskFailed"
    PIPELINE_SUCCEEDED = "PipelineSucceeded"
    PIPELINE_FAILED = "PipelineFailed"


class OrchestrationEvent(SettingsModel):
    """Safe structured observability event (no payload/DSN/SQL/credentials)."""

    event_type: OrchestrationEventType
    run_id: UUID
    task_id: TaskId | None = None
    attempt: int | None = Field(default=None, strict=True, ge=1)
    occurred_at: datetime
    publication_version: str | None = None
    safe_error_category: FailureCategory | None = None
    recovery_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
