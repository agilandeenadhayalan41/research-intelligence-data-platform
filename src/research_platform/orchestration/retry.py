"""Typed retry policies by orchestration task class."""

from __future__ import annotations

from research_platform.orchestration.models import (
    FailureCategory,
    Retryability,
    RetryPolicy,
    TaskId,
)


def _transient(max_attempts: int = 3, delay: int = 30) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        retry_delay_seconds=delay,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(FailureCategory.TRANSIENT_TRANSPORT,),
        non_retryable_categories=(
            FailureCategory.BOUNDS_CONFIG,
            FailureCategory.VALIDATION,
        ),
    )


def _idempotent_replay(max_attempts: int = 3, delay: int = 30) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        retry_delay_seconds=delay,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(
            FailureCategory.TRANSIENT_TRANSPORT,
            FailureCategory.IDEMPOTENT_REPLAY,
        ),
        non_retryable_categories=(
            FailureCategory.BOUNDS_CONFIG,
            FailureCategory.CANONICAL_CONFLICT,
            FailureCategory.RESTORE_REQUIRED,
            FailureCategory.PUBLICATION_CONFLICT,
            FailureCategory.QUALITY_HARD_GATE,
            FailureCategory.VALIDATION,
        ),
    )


def _non_retryable_business() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=1,
        retry_delay_seconds=0,
        retryability=Retryability.NON_RETRYABLE,
        retryable_categories=(),
        non_retryable_categories=(
            FailureCategory.QUALITY_HARD_GATE,
            FailureCategory.CANONICAL_CONFLICT,
            FailureCategory.RESTORE_REQUIRED,
            FailureCategory.PUBLICATION_CONFLICT,
            FailureCategory.BOUNDS_CONFIG,
            FailureCategory.VALIDATION,
            FailureCategory.UNKNOWN,
        ),
    )


# Preserves Step 12–19 idempotency / fail-closed semantics.
DEFAULT_RETRY_POLICIES: dict[TaskId, RetryPolicy] = {
    TaskId.DISCOVER: _transient(),
    TaskId.REGISTER: _idempotent_replay(),
    TaskId.INGEST: _idempotent_replay(),
    TaskId.CANONICALIZE: RetryPolicy(
        max_attempts=2,
        retry_delay_seconds=30,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(FailureCategory.TRANSIENT_TRANSPORT,),
        non_retryable_categories=(
            FailureCategory.CANONICAL_CONFLICT,
            FailureCategory.RESTORE_REQUIRED,
            FailureCategory.VALIDATION,
        ),
    ),
    TaskId.APPLY_DELETIONS: RetryPolicy(
        max_attempts=2,
        retry_delay_seconds=30,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(FailureCategory.TRANSIENT_TRANSPORT,),
        non_retryable_categories=(
            FailureCategory.CANONICAL_CONFLICT,
            FailureCategory.RESTORE_REQUIRED,
            FailureCategory.VALIDATION,
        ),
    ),
    TaskId.ANALYTICAL_PUBLICATION: _idempotent_replay(),
    TaskId.PRE_SERVING_QUALITY: _non_retryable_business(),
    TaskId.STAGE_GOLD: _idempotent_replay(max_attempts=2),
    TaskId.PRE_VISIBLE_QUALITY: _non_retryable_business(),
    TaskId.PUBLISH_SUCCESS: RetryPolicy(
        max_attempts=2,
        retry_delay_seconds=15,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(
            FailureCategory.TRANSIENT_TRANSPORT,
            FailureCategory.IDEMPOTENT_REPLAY,
        ),
        non_retryable_categories=(
            FailureCategory.PUBLICATION_CONFLICT,
            FailureCategory.QUALITY_HARD_GATE,
            FailureCategory.VALIDATION,
        ),
    ),
    TaskId.FINAL_VALIDATION: _non_retryable_business(),
}


def retry_policy_for(task_id: TaskId) -> RetryPolicy:
    return DEFAULT_RETRY_POLICIES[task_id]


def is_retryable_failure(task_id: TaskId, category: FailureCategory) -> bool:
    """Whether automatic orchestration retry is allowed for this failure."""
    policy = retry_policy_for(task_id)
    if policy.retryability is Retryability.NON_RETRYABLE:
        return False
    if category in policy.non_retryable_categories:
        return False
    return category in policy.retryable_categories
