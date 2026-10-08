"""Typed retry policies — authoritative at ExecutionUnit granularity."""

from __future__ import annotations

from research_platform.orchestration.execution import (
    TASK_EXECUTION_UNIT,
    execution_unit_for,
)
from research_platform.orchestration.models import (
    ExecutionUnit,
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


# Authoritative physical retry ownership. Future schedulers must retry the
# ExecutionUnit once — not each logical TaskId independently.
#
# WORKS_INGEST_UNIT covers REGISTER+INGEST+CANONICALIZE as one Step-12 call to
# ingest_works_asset (claim + land + decode/map + canonical publication).
EXECUTION_UNIT_RETRY_POLICIES: dict[ExecutionUnit, RetryPolicy] = {
    ExecutionUnit.DISCOVERY_UNIT: _transient(),
    ExecutionUnit.WORKS_INGEST_UNIT: _idempotent_replay(),
    ExecutionUnit.DELETION_UNIT: RetryPolicy(
        max_attempts=2,
        retry_delay_seconds=30,
        retryability=Retryability.RETRYABLE,
        retryable_categories=(FailureCategory.TRANSIENT_TRANSPORT,),
        non_retryable_categories=(
            FailureCategory.CANONICAL_CONFLICT,
            FailureCategory.RESTORE_REQUIRED,
            FailureCategory.VALIDATION,
            FailureCategory.BOUNDS_CONFIG,
        ),
    ),
    ExecutionUnit.ANALYTICAL_PUBLICATION_UNIT: _idempotent_replay(),
    ExecutionUnit.PRE_SERVING_QUALITY_UNIT: _non_retryable_business(),
    ExecutionUnit.GOLD_UNIT: _idempotent_replay(max_attempts=2),
    ExecutionUnit.PRE_VISIBLE_QUALITY_UNIT: _non_retryable_business(),
    ExecutionUnit.CONSUMER_PUBLICATION_UNIT: RetryPolicy(
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
    ExecutionUnit.FINAL_VALIDATION_UNIT: _non_retryable_business(),
}


# Logical TaskId views inherit the unit policy (no independent CANONICALIZE retry).
DEFAULT_RETRY_POLICIES: dict[TaskId, RetryPolicy] = {
    task_id: EXECUTION_UNIT_RETRY_POLICIES[unit]
    for task_id, unit in TASK_EXECUTION_UNIT.items()
}


def retry_policy_for_unit(unit: ExecutionUnit) -> RetryPolicy:
    return EXECUTION_UNIT_RETRY_POLICIES[unit]


def retry_policy_for(task_id: TaskId) -> RetryPolicy:
    """Return the authoritative ExecutionUnit policy for this logical TaskId."""
    return retry_policy_for_unit(execution_unit_for(task_id))


def is_retryable_failure(task_id: TaskId, category: FailureCategory) -> bool:
    """Whether automatic orchestration retry is allowed for this failure."""
    return is_retryable_unit_failure(execution_unit_for(task_id), category)


def is_retryable_unit_failure(
    unit: ExecutionUnit, category: FailureCategory
) -> bool:
    policy = retry_policy_for_unit(unit)
    if policy.retryability is Retryability.NON_RETRYABLE:
        return False
    if category in policy.non_retryable_categories:
        return False
    return category in policy.retryable_categories
