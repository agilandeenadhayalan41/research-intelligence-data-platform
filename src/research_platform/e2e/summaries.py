"""Safe consumer-facing summaries for Step-19 results (no secrets/SQL)."""

from __future__ import annotations

from typing import Any

from research_platform.e2e.models import EndToEndResult, StageStatus


def safe_result_summary(result: EndToEndResult) -> dict[str, Any]:
    """Return a redacted, deterministic summary suitable for logs/CLI."""
    return {
        "run_id": str(result.run.run_id),
        "pipeline_status": result.run.status.value,
        "evidence_label": result.evidence_label,
        "publication_version": result.publication_version,
        "success": result.success,
        "safe_error": result.safe_error,
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
