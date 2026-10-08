"""Pure-Python orchestration plan validation and dry-run rendering.

Does not execute cloud resources or emulate Airflow.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from research_platform.orchestration.execution import (
    EXECUTION_UNIT_CAPABILITY,
    EXECUTION_UNIT_ORDER,
    execution_unit_for,
    execution_unit_order_from_logical,
)
from research_platform.orchestration.graph import (
    OrchestrationGraph,
    build_openalex_works_graph,
    topological_order,
    validate_graph,
)
from research_platform.orchestration.messages import (
    assert_bounded_event_details,
    build_task_message,
    task_message_as_xcom_dict,
)
from research_platform.orchestration.models import (
    MAX_EVENT_JSON_BYTES,
    ORCHESTRATION_CONTRACT_VERSION,
    RECOMMENDED_PUBLICATION_STRATEGY,
    ExecutionUnit,
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
from research_platform.orchestration.retry import (
    is_retryable_failure,
    is_retryable_unit_failure,
    retry_policy_for,
    retry_policy_for_unit,
)
from research_platform.service.models import SettingsModel


class ExecutionPlanTask(SettingsModel):
    task_id: TaskId
    depends_on: tuple[TaskId, ...]
    execution_unit: ExecutionUnit
    logical_only: bool
    capability_ref: str
    retryability: str
    max_attempts: int
    quality_gate: str | None = None


class ExecutionUnitPlanStep(SettingsModel):
    unit: ExecutionUnit
    capability_ref: str
    retryability: str
    max_attempts: int
    logical_tasks: tuple[TaskId, ...]


class ExecutionPlan(SettingsModel):
    contract_version: str
    pipeline_name: str
    run_id: UUID
    publication_version: str
    scheduling_class: SchedulingClass
    publication_strategy: PublicationConcurrencyStrategy
    publication_scope_suffix: str
    logical_task_order: tuple[TaskId, ...]
    execution_unit_order: tuple[ExecutionUnit, ...]
    topological_order: tuple[TaskId, ...]  # alias of logical_task_order
    tasks: tuple[ExecutionPlanTask, ...]
    execution_units: tuple[ExecutionUnitPlanStep, ...]
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
    """Render a dry-run plan with logical and deduplicated physical orders."""
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
    logical_order = topological_order(resolved)
    unit_order = execution_unit_order_from_logical(logical_order)
    tasks = tuple(
        ExecutionPlanTask(
            task_id=spec.task_id,
            depends_on=spec.depends_on,
            execution_unit=spec.execution_unit,
            logical_only=spec.logical_only,
            capability_ref=spec.capability_ref,
            retryability=spec.retry_policy.retryability.value,
            max_attempts=spec.retry_policy.max_attempts,
            quality_gate=spec.quality_gate,
        )
        for tid in logical_order
        for spec in (resolved.spec(tid),)
    )
    unit_steps: list[ExecutionUnitPlanStep] = []
    for unit in unit_order:
        policy = retry_policy_for_unit(unit)
        logical_tasks = tuple(
            t.task_id for t in tasks if t.execution_unit is unit
        )
        unit_steps.append(
            ExecutionUnitPlanStep(
                unit=unit,
                capability_ref=EXECUTION_UNIT_CAPABILITY[unit],
                retryability=policy.retryability.value,
                max_attempts=policy.max_attempts,
                logical_tasks=logical_tasks,
            )
        )
    bindings = {
        name: physical_decision_table_name(name, scope)
        for name in (
            "work_publication_decisions",
            "accepted_work_ids",
            "relationship_publish_work_ids",
        )
    }
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
        "REGISTER/INGEST/CANONICALIZE are logical phases of WORKS_INGEST_UNIT "
        "(one ingest_works_asset invoke)",
        f"recommended publication strategy={RECOMMENDED_PUBLICATION_STRATEGY.value}",
        f"selected publication strategy={strategy.value}",
        f"physical execution units={len(unit_order)} "
        f"(logical tasks={len(logical_order)})",
    )
    return ExecutionPlan(
        contract_version=ORCHESTRATION_CONTRACT_VERSION,
        pipeline_name="openalex-works-orchestration",
        run_id=rid,
        publication_version=version,
        scheduling_class=scheduling_class,
        publication_strategy=strategy,
        publication_scope_suffix=suffix,
        logical_task_order=logical_order,
        execution_unit_order=unit_order,
        topological_order=logical_order,
        tasks=tuple(tasks),
        execution_units=tuple(unit_steps),
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
    """Build a safe observability event; rejects forbidden/oversized details."""
    payload = dict(details or {})
    assert_bounded_event_details(payload)
    event = OrchestrationEvent(
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
    raw = json.dumps(event.model_dump(mode="json"), sort_keys=True, default=str).encode(
        "utf-8"
    )
    if len(raw) > MAX_EVENT_JSON_BYTES:
        raise ValueError(f"OrchestrationEvent JSON exceeds {MAX_EVENT_JSON_BYTES} bytes")
    return event


def classify_retry(task_id: TaskId, category: FailureCategory) -> dict[str, Any]:
    """Summarize retry decision for dry-run / tests (unit-authoritative)."""
    policy = retry_policy_for(task_id)
    return {
        "task_id": task_id.value,
        "execution_unit": execution_unit_for(task_id).value,
        "category": category.value,
        "retryable": is_retryable_failure(task_id, category),
        "max_attempts": policy.max_attempts,
        "retryability": policy.retryability.value,
    }


def classify_unit_retry(
    unit: ExecutionUnit, category: FailureCategory
) -> dict[str, Any]:
    policy = retry_policy_for_unit(unit)
    return {
        "execution_unit": unit.value,
        "category": category.value,
        "retryable": is_retryable_unit_failure(unit, category),
        "max_attempts": policy.max_attempts,
        "retryability": policy.retryability.value,
    }


# Re-export for callers that inspect default physical order.
DEFAULT_EXECUTION_UNIT_ORDER = EXECUTION_UNIT_ORDER
