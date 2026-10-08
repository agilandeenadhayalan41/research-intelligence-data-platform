"""Step 19 bounded end-to-end pipeline models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from research_platform.control.models import PipelineRun
from research_platform.quality.models import QualityReport
from research_platform.service.models import SettingsModel


PIPELINE_NAME = "openalex-works-e2e"
EVIDENCE_LABEL = "SEMANTIC_ONLY"
DATA_SERVICE_CONTRACT = "data-service-contract-v1"

DEFAULT_MAX_FILES = 1
DEFAULT_MAX_FILE_SIZE_BYTES = 25_000_000


class StageName(StrEnum):
    DISCOVER = "DISCOVER"
    SELECT = "SELECT"
    INGEST = "INGEST"
    IMMUTABLE_LANDING = "IMMUTABLE_LANDING"
    CANONICALIZE = "CANONICALIZE"
    APPLY_DELETIONS = "APPLY_DELETIONS"
    ANALYTICAL_MODELS = "ANALYTICAL_MODELS"
    PRE_SERVING_QUALITY = "PRE_SERVING_QUALITY"
    STAGED_GOLD = "STAGED_GOLD"
    PRE_VISIBLE_QUALITY = "PRE_VISIBLE_QUALITY"
    CONSUMER_PUBLICATION = "CONSUMER_PUBLICATION"
    FINAL_VALIDATION = "FINAL_VALIDATION"


STAGE_ORDER: tuple[StageName, ...] = (
    StageName.DISCOVER,
    StageName.SELECT,
    StageName.INGEST,
    StageName.IMMUTABLE_LANDING,
    StageName.CANONICALIZE,
    StageName.APPLY_DELETIONS,
    StageName.ANALYTICAL_MODELS,
    StageName.PRE_SERVING_QUALITY,
    StageName.STAGED_GOLD,
    StageName.PRE_VISIBLE_QUALITY,
    StageName.CONSUMER_PUBLICATION,
    StageName.FINAL_VALIDATION,
)


class StageStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class EndToEndBounds(SettingsModel):
    """Strict sample bounds — no silent clamping, no unlimited, no coercion."""

    max_files: int = Field(default=DEFAULT_MAX_FILES, strict=True)
    max_file_size_bytes: int = Field(default=DEFAULT_MAX_FILE_SIZE_BYTES, strict=True)

    @field_validator("max_files", "max_file_size_bytes", mode="before")
    @classmethod
    def reject_bool_and_none(cls, value: object) -> object:
        if value is None:
            raise ValueError("bounds must not be None (unlimited is forbidden)")
        if isinstance(value, bool):
            raise ValueError("bounds must not be bool")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> EndToEndBounds:
        if self.max_files < 1:
            raise ValueError("max_files must be >= 1")
        if self.max_files > DEFAULT_MAX_FILES:
            raise ValueError(f"max_files must be <= {DEFAULT_MAX_FILES}")
        if self.max_file_size_bytes < 1:
            raise ValueError("max_file_size_bytes must be >= 1")
        if self.max_file_size_bytes > DEFAULT_MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"max_file_size_bytes must be <= {DEFAULT_MAX_FILE_SIZE_BYTES}"
            )
        return self


class RecoveryAction(StrEnum):
    """Safe, typed recovery guidance for Step-19 failure summaries."""

    CORRECT_BOUNDS_AND_RERUN = "correct bounds/config and rerun"
    RETRY_SAME_IMMUTABLE_ASSET = (
        "correct source/landing failure and retry same immutable asset"
    )
    RESOLVE_CANONICAL_BEFORE_RETRY = (
        "resolve canonical conflict/restore decision before retry"
    )
    INSPECT_QUALITY_DO_NOT_PUBLISH = (
        "inspect aggregate quality diagnostics; do not publish candidate"
    )
    RETRY_ACTIVATE_STAGED_VERSION = (
        "retry/activate the same staged publication_version after validation"
    )
    PREVIOUS_RESTORED_INSPECT_VALIDATION = (
        "previous publication restored; inspect validation before retry"
    )
    INSPECT_FAILURE_AND_RERUN = "inspect failure and rerun"


class StageResult(SettingsModel):
    stage: StageName
    status: StageStatus
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class FinalValidationReport(SettingsModel):
    ok: bool
    publication_version: str | None = None
    as_of_run_id: str | None = None
    active_work_count: int = 0
    errors: tuple[str, ...] = ()


class EndToEndResult(SettingsModel):
    run: PipelineRun
    stages: tuple[StageResult, ...]
    evidence_label: str = EVIDENCE_LABEL
    publication_version: str | None = None
    pre_serving_report: QualityReport | None = None
    pre_visible_report: QualityReport | None = None
    final_validation: FinalValidationReport | None = None
    safe_error: str | None = None
    recovery_action: str | None = None

    @property
    def success(self) -> bool:
        return all(
            s.status in {StageStatus.SUCCESS, StageStatus.SKIPPED} for s in self.stages
        ) and (
            self.final_validation is not None and self.final_validation.ok
        )


class EndToEndRunContext(SettingsModel):
    """Shared runtime context for one outer Step-19 PipelineRun."""

    run_id: UUID
    started_at: datetime
    bounds: EndToEndBounds = Field(default_factory=EndToEndBounds)
    publication_version: str | None = None
