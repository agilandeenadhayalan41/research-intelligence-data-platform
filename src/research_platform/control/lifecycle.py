"""Explicit status-transition rules for pipeline control (pure contract logic)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from research_platform.control.errors import (
    ClaimConflictError,
    IllegalTransitionError,
    StaleClaimError,
)
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    SourceFileControl,
)

SourceFileEvent = Literal["claim", "success", "fail", "retry"]
PipelineRunEvent = Literal["start", "succeed", "fail"]

SOURCE_FILE_TRANSITIONS: dict[ControlStatus, frozenset[ControlStatus]] = {
    ControlStatus.DISCOVERED: frozenset({ControlStatus.PROCESSING}),
    ControlStatus.PROCESSING: frozenset({ControlStatus.SUCCESS, ControlStatus.FAILED}),
    ControlStatus.FAILED: frozenset({ControlStatus.PROCESSING}),
    ControlStatus.SUCCESS: frozenset(),
}

PIPELINE_RUN_TRANSITIONS: dict[PipelineRunStatus, frozenset[PipelineRunStatus]] = {
    PipelineRunStatus.PENDING: frozenset({PipelineRunStatus.PROCESSING}),
    PipelineRunStatus.PROCESSING: frozenset(
        {PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED}
    ),
    PipelineRunStatus.FAILED: frozenset({PipelineRunStatus.PROCESSING}),
    PipelineRunStatus.SUCCESS: frozenset(),
}

EVENT_TARGET_STATUS: dict[SourceFileEvent, ControlStatus] = {
    "claim": ControlStatus.PROCESSING,
    "success": ControlStatus.SUCCESS,
    "fail": ControlStatus.FAILED,
    "retry": ControlStatus.PROCESSING,
}


def assert_source_file_transition(
    current: ControlStatus, target: ControlStatus, *, event: SourceFileEvent
) -> None:
    """Reject illegal source-file transitions.

    ``FAILED -> PROCESSING`` is allowed only for the explicit ``retry`` event.
    ``SUCCESS`` is terminal.
    """
    expected = EVENT_TARGET_STATUS[event]
    if target is not expected:
        raise IllegalTransitionError(
            f"event {event!r} must target {expected.value}, not {target.value}"
        )
    allowed = SOURCE_FILE_TRANSITIONS[current]
    if target not in allowed:
        raise IllegalTransitionError(
            f"illegal transition {current.value} -> {target.value} for event {event!r}"
        )
    if event == "retry" and current is not ControlStatus.FAILED:
        raise IllegalTransitionError("retry requires FAILED status")
    if event == "claim" and current is not ControlStatus.DISCOVERED:
        raise IllegalTransitionError("claim requires DISCOVERED status")


def next_attempt_count(current: SourceFileControl, *, event: SourceFileEvent) -> int:
    """Return the attempt_count after a validated event.

    First ``claim`` from DISCOVERED sets attempt_count to 1. Explicit ``retry``
    from FAILED increments by one. Terminal success/fail leaves the counter
    unchanged for that attempt.
    """
    if event == "claim":
        return 1
    if event == "retry":
        return current.attempt_count + 1
    return current.attempt_count


def is_lease_expired(control: SourceFileControl, *, now: datetime) -> bool:
    """True when an active PROCESSING claim lease has expired."""
    if control.status is not ControlStatus.PROCESSING:
        return False
    if control.lease_expires_at is None:
        return False
    if now.tzinfo is None or control.lease_expires_at.tzinfo is None:
        raise ValueError("lease checks require timezone-aware datetimes")
    return now >= control.lease_expires_at


def assert_claim_owned(
    control: SourceFileControl,
    *,
    claim_token: UUID,
    now: datetime,
    allow_expired: bool = False,
) -> None:
    """Require the caller to present the current claim token for completion."""
    if control.status is not ControlStatus.PROCESSING:
        raise ClaimConflictError("no active claim to complete")
    if control.claim_token != claim_token:
        raise ClaimConflictError("claim token does not match active claim")
    if not allow_expired and is_lease_expired(control, now=now):
        raise StaleClaimError("claim lease has expired; recover before completing")


def apply_source_file_claim(
    control: SourceFileControl,
    *,
    run_id: UUID,
    claimed_by: str,
    claim_token: UUID,
    claimed_at: datetime,
    lease_expires_at: datetime,
    updated_at: datetime,
    event: Literal["claim", "retry"] = "claim",
) -> SourceFileControl:
    """Pure transition into PROCESSING with a new active claim."""
    target = ControlStatus.PROCESSING
    assert_source_file_transition(control.status, target, event=event)
    attempt_count = next_attempt_count(control, event=event)
    return control.model_copy(
        update={
            "run_id": run_id,
            "status": target,
            "attempt_count": attempt_count,
            "claimed_by": claimed_by,
            "claim_token": claim_token,
            "claimed_at": claimed_at,
            "lease_expires_at": lease_expires_at,
            "processed_at": None,
            "failure_category": None,
            "failure_message": None,
            "updated_at": updated_at,
        }
    )


def apply_source_file_success(
    control: SourceFileControl,
    *,
    claim_token: UUID,
    processed_at: datetime,
    raw_object_key: str,
    source_checksum_sha256: str,
    updated_at: datetime,
    now: datetime,
) -> SourceFileControl:
    """Pure PROCESSING -> SUCCESS transition requiring the current claim."""
    assert_claim_owned(control, claim_token=claim_token, now=now)
    assert_source_file_transition(
        control.status, ControlStatus.SUCCESS, event="success"
    )
    return control.model_copy(
        update={
            "status": ControlStatus.SUCCESS,
            "attempt_count": next_attempt_count(control, event="success"),
            "claimed_by": None,
            "claim_token": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "processed_at": processed_at,
            "raw_object_key": raw_object_key,
            "source_checksum_sha256": source_checksum_sha256,
            "failure_category": None,
            "failure_message": None,
            "updated_at": updated_at,
        }
    )


def apply_source_file_failure(
    control: SourceFileControl,
    *,
    claim_token: UUID,
    processed_at: datetime,
    failure_category: FailureCategory,
    failure_message: str,
    updated_at: datetime,
    now: datetime,
    allow_expired: bool = False,
) -> SourceFileControl:
    """Pure PROCESSING -> FAILED transition requiring the current claim."""
    assert_claim_owned(
        control, claim_token=claim_token, now=now, allow_expired=allow_expired
    )
    assert_source_file_transition(control.status, ControlStatus.FAILED, event="fail")
    return control.model_copy(
        update={
            "status": ControlStatus.FAILED,
            "attempt_count": next_attempt_count(control, event="fail"),
            "claimed_by": None,
            "claim_token": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "processed_at": processed_at,
            "failure_category": failure_category,
            "failure_message": failure_message,
            "updated_at": updated_at,
        }
    )


def apply_stale_claim_recovery(
    control: SourceFileControl,
    *,
    now: datetime,
    updated_at: datetime,
    failure_message: str = "claim lease expired",
) -> SourceFileControl:
    """Recover an expired PROCESSING claim into FAILED (STALE_CLAIM).

    A later explicit ``retry`` claim is required before another worker owns the
    asset. Valid (non-expired) claims cannot be stolen.
    """
    if control.status is not ControlStatus.PROCESSING:
        raise IllegalTransitionError("stale recovery requires PROCESSING status")
    if not is_lease_expired(control, now=now):
        raise ClaimConflictError("cannot recover a claim that is still valid")
    if control.claim_token is None:
        raise ClaimConflictError("PROCESSING file is missing claim_token")
    return apply_source_file_failure(
        control,
        claim_token=control.claim_token,
        processed_at=now,
        failure_category=FailureCategory.STALE_CLAIM,
        failure_message=failure_message,
        updated_at=updated_at,
        now=now,
        allow_expired=True,
    )


def assert_pipeline_run_transition(
    current: PipelineRunStatus, target: PipelineRunStatus
) -> None:
    if target not in PIPELINE_RUN_TRANSITIONS[current]:
        raise IllegalTransitionError(
            f"illegal pipeline-run transition {current.value} -> {target.value}"
        )


def apply_pipeline_run_start(
    run: PipelineRun, *, started_at: datetime, updated_at: datetime
) -> PipelineRun:
    assert_pipeline_run_transition(run.status, PipelineRunStatus.PROCESSING)
    return run.model_copy(
        update={
            "status": PipelineRunStatus.PROCESSING,
            "started_at": started_at,
            "completed_at": None,
            "failure_category": None,
            "failure_message": None,
            "updated_at": updated_at,
        }
    )


def apply_pipeline_run_finish(
    run: PipelineRun,
    *,
    status: Literal[PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED],
    completed_at: datetime,
    updated_at: datetime,
    failure_category: FailureCategory | None = None,
    failure_message: str | None = None,
) -> PipelineRun:
    assert_pipeline_run_transition(run.status, status)
    if status is PipelineRunStatus.SUCCESS:
        return run.model_copy(
            update={
                "status": status,
                "completed_at": completed_at,
                "failure_category": None,
                "failure_message": None,
                "updated_at": updated_at,
            }
        )
    return run.model_copy(
        update={
            "status": status,
            "completed_at": completed_at,
            "failure_category": failure_category,
            "failure_message": failure_message,
            "updated_at": updated_at,
        }
    )
