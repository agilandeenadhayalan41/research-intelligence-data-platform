"""Pure-Python orchestration plan validation and dry-run rendering.

Does not execute cloud resources or emulate Airflow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from research_platform.orchestration.graph import (
    OrchestrationGraph,
    build_openalex_works_graph,
    topological_order,
    validate_graph,
)
from research_platform.orchestration.messages import (
    assert_safe_message_keys,
    build_task_message,
    task_message_as_xcom_dict,
)
from research_platform.orchestration.models import (
    ORCHESTRATION_CONTRACT_VERSION,
    RECOMMENDED_PUBLICATION_STRATEGY,
    FailureCategory,
    OrchestrationEvent,
    OrchestrationEventType,
    PublicationConcurrencyStrategy,
    SchedulingClass,
    TaskId,
)
from research_platform.orchestration.publication_scope import (
    build_publication_scope,
    physical_decision_table_name,
    publication_scope_suffix,
)
from research_platform.orchestration.retry import is_retryable_failure, retry_policy_for
from research_platform.service.models import SettingsModel


class ExecutionPlanTask(SettingsModel):
    task_id: TaskId
    depends_on: tuple[TaskId, ...]
    capability_ref: str
    retryability: str
    max_attempts: int
    quality_gate: str | None = None


class ExecutionPlan(SettingsModel):
    contract_version: str
    pipeline_name: str
    run_id: UUID
    publication_version: str
    scheduling_class: SchedulingClass
    publication_strategy: PublicationConcurrencyStrategy
    publication_scope_suffix: str
    topological_order: tuple[TaskId, ...]
    tasks: tuple[ExecutionPlanTask, ...]
    decision_table_bindings: dict[str, str]
    notes: tuple[str, ...] = ()


def validate_orchestration_plan(
    graph: OrchestrationGraph | None = None,
) -> OrchestrationGraph:
    """Validate graph inventory, edges, acyclicity; return the graph."""
    resolved = graph or build_openalex_works_graph()
    validate_graph(resolved)
    return resolved


def render_execution_plan(
    *,
    run_id: UUID | None = None,
    publication_version: str | None = None,
    scheduling_class: SchedulingClass = SchedulingClass.MANUAL,
    publication_strategy: PublicationConcurrencyStrategy | None = None,
    environment: str = "local",
    source: str = "openalex",
    graph: OrchestrationGraph | None = None,
) -> ExecutionPlan:
    """Render a dry-run execution plan (order, retries, scope) — no cloud I/O."""
    resolved = validate_orchestration_plan(graph)
    rid = run_id or uuid4()
    version = (publication_version or f"pub-{rid}").strip()
    if not version:
        raise ValueError("publication_version must be non-empty")
    strategy = publication_strategy or RECOMMENDED_PUBLICATION_STRATEGY
    scope = build_publication_scope(
        run_id=rid,
        publication_version=version,
        strategy=strategy,
    )
    suffix = publication_scope_suffix(rid)
    order = topological_order(resolved)
    tasks = tuple(
        ExecutionPlanTask(
            task_id=spec.task_id,
            depends_on=spec.depends_on,
            capability_ref=spec.capability_ref,
            retryability=spec.retry_policy.retryability.value,
            max_attempts=spec.retry_policy.max_attempts,
            quality_gate=spec.quality_gate,
        )
        for tid in order
        for spec in (resolved.spec(tid),)
    )
    bindings = {
        name: physical_decision_table_name(name, scope)
        for name in (
            "work_publication_decisions",
            "accepted_work_ids",
            "relationship_publish_work_ids",
        )
    }
    # Sample message schema check (not executed).
    sample = build_task_message(
        run_id=rid,
        publication_version=version,
        source=source,
        task_id=TaskId.DISCOVER,
        attempt=1,
        environment=environment,
        publication_scope_ref=suffix,
        scheduling_class=scheduling_class,
    )
    task_message_as_xcom_dict(sample)

    notes = (
        "CONTRACT_ONLY dry-run — no Airflow/Composer/Dataform/GCP execution",
        "Step 19 run_bounded_e2e_pipeline remains the local SEMANTIC_ONLY proof",
        f"recommended publication strategy={RECOMMENDED_PUBLICATION_STRATEGY.value}",
        f"selected publication strategy={strategy.value}",
    )
    return ExecutionPlan(
        contract_version=ORCHESTRATION_CONTRACT_VERSION,
        pipeline_name="openalex-works-orchestration",
        run_id=rid,
        publication_version=version,
        scheduling_class=scheduling_class,
        publication_strategy=strategy,
        publication_scope_suffix=suffix,
        topological_order=order,
        tasks=tasks,
        decision_table_bindings=bindings,
        notes=notes,
    )


def build_orchestration_event(
    *,
    event_type: OrchestrationEventType,
    run_id: UUID,
    occurred_at: datetime | None = None,
    task_id: TaskId | None = None,
    attempt: int | None = None,
    publication_version: str | None = None,
    safe_error_category: FailureCategory | None = None,
    recovery_action: str | None = None,
    details: dict[str, Any] | None = None,
) -> OrchestrationEvent:
    """Build a safe observability event; rejects forbidden detail keys."""
    payload = dict(details or {})
    assert_safe_message_keys(payload)
    return OrchestrationEvent(
        event_type=event_type,
        run_id=run_id,
        task_id=task_id,
        attempt=attempt,
        occurred_at=occurred_at or datetime.now(tz=UTC),
        publication_version=publication_version,
        safe_error_category=safe_error_category,
        recovery_action=recovery_action,
        details=payload,
    )


def classify_retry(task_id: TaskId, category: FailureCategory) -> dict[str, Any]:
    """Summarize retry decision for dry-run / tests."""
    policy = retry_policy_for(task_id)
    return {
        "task_id": task_id.value,
        "category": category.value,
        "retryable": is_retryable_failure(task_id, category),
        "max_attempts": policy.max_attempts,
        "retryability": policy.retryability.value,
    }
