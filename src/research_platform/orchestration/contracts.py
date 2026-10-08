"""Cross-cutting orchestration contracts (scheduling, quality, publication order)."""

from __future__ import annotations

from research_platform.orchestration.graph import (
    OrchestrationGraph,
    build_openalex_works_graph,
    tasks_blocked_by_quality_failure,
)
from research_platform.orchestration.models import (
    ANALYTICAL_PUBLICATION_ORDER,
    ORCHESTRATION_CONTRACT_VERSION,
    RECOMMENDED_PUBLICATION_STRATEGY,
    STEP15_DECISION_CONTRACT_NAMES,
    AnalyticalPublicationStep,
    BackfillRequest,
    FailureCategory,
    OrchestrationEvent,
    OrchestrationEventType,
    PublicationConcurrencyStrategy,
    PublicationScope,
    SchedulingClass,
    TaskId,
    TaskMessage,
)
from research_platform.orchestration.publication_scope import (
    build_publication_scope,
    physical_decision_table_name,
    publication_scope_suffix,
)
from research_platform.quality.models import ExecutionStage


# Maps orchestration quality tasks to Step-17 execution stages.
QUALITY_GATE_STAGES: dict[TaskId, ExecutionStage] = {
    TaskId.PRE_SERVING_QUALITY: ExecutionStage.PRE_SERVING_BUILD,
    TaskId.PRE_VISIBLE_QUALITY: ExecutionStage.PRE_VISIBLE_PUBLICATION,
}


def analytical_publication_order() -> tuple[AnalyticalPublicationStep, ...]:
    return ANALYTICAL_PUBLICATION_ORDER


def quality_failure_blocks(task_id: TaskId) -> tuple[TaskId, ...]:
    return tasks_blocked_by_quality_failure(task_id)


def default_orchestration_graph() -> OrchestrationGraph:
    return build_openalex_works_graph()


__all__ = [
    "ANALYTICAL_PUBLICATION_ORDER",
    "ORCHESTRATION_CONTRACT_VERSION",
    "QUALITY_GATE_STAGES",
    "RECOMMENDED_PUBLICATION_STRATEGY",
    "STEP15_DECISION_CONTRACT_NAMES",
    "AnalyticalPublicationStep",
    "BackfillRequest",
    "FailureCategory",
    "OrchestrationEvent",
    "OrchestrationEventType",
    "OrchestrationGraph",
    "PublicationConcurrencyStrategy",
    "PublicationScope",
    "SchedulingClass",
    "TaskId",
    "TaskMessage",
    "analytical_publication_order",
    "build_publication_scope",
    "default_orchestration_graph",
    "physical_decision_table_name",
    "publication_scope_suffix",
    "quality_failure_blocks",
]
