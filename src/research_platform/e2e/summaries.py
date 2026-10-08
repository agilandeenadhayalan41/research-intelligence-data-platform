"""Safe consumer-facing summaries for Step-19 results (no secrets/SQL)."""

from __future__ import annotations

from typing import Any

from research_platform.e2e.models import (
    EndToEndResult,
    RecoveryAction,
    StageName,
    StageResult,
    StageStatus,
)


_STAGE_RECOVERY: dict[StageName, RecoveryAction] = {
    StageName.DISCOVER: RecoveryAction.CORRECT_BOUNDS_AND_RERUN,
    StageName.SELECT: RecoveryAction.CORRECT_BOUNDS_AND_RERUN,
    StageName.INGEST: RecoveryAction.RETRY_SAME_IMMUTABLE_ASSET,
    StageName.IMMUTABLE_LANDING: RecoveryAction.RETRY_SAME_IMMUTABLE_ASSET,
    StageName.CANONICALIZE: RecoveryAction.RESOLVE_CANONICAL_BEFORE_RETRY,
    StageName.APPLY_DELETIONS: RecoveryAction.RESOLVE_CANONICAL_BEFORE_RETRY,
    StageName.ANALYTICAL_MODELS: RecoveryAction.INSPECT_QUALITY_DO_NOT_PUBLISH,
    StageName.PRE_SERVING_QUALITY: RecoveryAction.INSPECT_QUALITY_DO_NOT_PUBLISH,
    StageName.STAGED_GOLD: RecoveryAction.INSPECT_QUALITY_DO_NOT_PUBLISH,
    StageName.PRE_VISIBLE_QUALITY: RecoveryAction.INSPECT_QUALITY_DO_NOT_PUBLISH,
    StageName.CONSUMER_PUBLICATION: RecoveryAction.RETRY_ACTIVATE_STAGED_VERSION,
    StageName.FINAL_VALIDATION: RecoveryAction.PREVIOUS_RESTORED_INSPECT_VALIDATION,
}


def recovery_action_for_stages(stages: tuple[StageResult, ...] | list[StageResult]) -> str:
    """Return safe recovery guidance for the first FAILED stage (generic fallback)."""
    failed = next((s for s in stages if s.status is StageStatus.FAILED), None)
    if failed is None:
        return RecoveryAction.INSPECT_FAILURE_AND_RERUN.value
    return _STAGE_RECOVERY.get(
        failed.stage, RecoveryAction.INSPECT_FAILURE_AND_RERUN
    ).value


def safe_result_summary(result: EndToEndResult) -> dict[str, Any]:
    """Return a redacted, deterministic summary suitable for logs/CLI."""
    recovery = result.recovery_action
    if recovery is None and not result.success:
        recovery = recovery_action_for_stages(result.stages)
    return {
        "run_id": str(result.run.run_id),
        "pipeline_status": result.run.status.value,
        "evidence_label": result.evidence_label,
        "publication_version": result.publication_version,
        "success": result.success,
        "safe_error": result.safe_error,
        "recovery_action": recovery,
        "stages": [
            {
                "stage": s.stage.value,
                "status": s.status.value,
                "message": s.message,
            }
            for s in result.stages
        ],
        "final_validation_ok": (
            None if result.final_validation is None else result.final_validation.ok
        ),
        "blocked_stages": [
            s.stage.value for s in result.stages if s.status is StageStatus.BLOCKED
        ],
        "failed_stages": [
            s.stage.value for s in result.stages if s.status is StageStatus.FAILED
        ],
    }


def safe_exception_message(exc: BaseException) -> str:
    """Map exceptions to short consumer-safe messages."""
    name = type(exc).__name__
    # Never echo raw exception text (may contain paths/SQL).
    mapping = {
        "ValueError": "invalid argument",
        "ValidationError": "invalid argument",
        "PublicationConflictError": "publication version conflict",
        "IngestionFormatError": "source payload rejected by bounds or format",
        "IngestionDecodeError": "source decode failed",
        "IngestionConfigError": "invalid local configuration",
        "RestoreRequiredError": "canonical restore required",
        "CanonicalConflictError": "canonical conflict",
        "ChecksumConflictError": "checksum conflict",
        "KeyError": "missing publication candidate",
        "RuntimeError": "backend error",
    }
    return mapping.get(name, "pipeline failed")
