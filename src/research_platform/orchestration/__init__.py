"""Orchestration contracts for future Airflow/Composer alignment (Step 20 / #25).

Defines a thin DAG graph, logical vs physical execution units, safe
TaskMessage/XCom payloads, retry policies, backfill bounds, and analytical
publication concurrency ownership.

Does **not** deploy Airflow, Composer, Dataform, BigQuery, GCS, or Terraform.
Does **not** replace ``run_bounded_e2e_pipeline`` (Step 19 local SEMANTIC_ONLY proof).
"""

from research_platform.orchestration.contracts import (
    ANALYTICAL_PUBLICATION_ORDER,
    ORCHESTRATION_CONTRACT_VERSION,
    QUALITY_GATE_STAGES,
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
    analytical_publication_order,
    default_orchestration_graph,
    quality_failure_blocks,
)
from research_platform.orchestration.execution import (
    EXECUTION_UNIT_CAPABILITY,
    EXECUTION_UNIT_ORDER,
    ExecutionUnitSpec,
    capability_ref_for_task,
    execution_unit_for,
    execution_unit_order_from_logical,
    execution_unit_spec,
    is_logical_checkpoint,
    physical_invocations,
)
from research_platform.orchestration.graph import (
    OrchestrationGraph,
    build_openalex_works_graph,
    depends_on_path,
    topological_order,
    validate_graph,
)
from research_platform.orchestration.messages import (
    FORBIDDEN_MESSAGE_KEYS,
    assert_bounded_event_details,
    assert_safe_message_keys,
    build_task_message,
    task_message_as_xcom_dict,
)
from research_platform.orchestration.models import (
    MAX_XCOM_JSON_BYTES,
    PIPELINE_NAME,
    TASK_INVENTORY,
    ExecutionUnit,
    TaskId,
    TaskMessage,
    TaskSpec,
)
from research_platform.orchestration.publication_scope import (
    assert_safe_scope_suffix,
    build_publication_scope,
    physical_decision_table_name,
    publication_scope_suffix,
    scopes_collide,
)
from research_platform.orchestration.retry import (
    DEFAULT_RETRY_POLICIES,
    EXECUTION_UNIT_RETRY_POLICIES,
    is_retryable_failure,
    is_retryable_unit_failure,
    retry_policy_for,
    retry_policy_for_unit,
)
from research_platform.orchestration.validation import (
    ExecutionPlan,
    build_orchestration_event,
    classify_retry,
    classify_unit_retry,
    render_execution_plan,
    validate_orchestration_plan,
)

__all__ = [
    "ANALYTICAL_PUBLICATION_ORDER",
    "DEFAULT_RETRY_POLICIES",
    "EXECUTION_UNIT_CAPABILITY",
    "EXECUTION_UNIT_ORDER",
    "EXECUTION_UNIT_RETRY_POLICIES",
    "FORBIDDEN_MESSAGE_KEYS",
    "MAX_XCOM_JSON_BYTES",
    "ORCHESTRATION_CONTRACT_VERSION",
    "PIPELINE_NAME",
    "QUALITY_GATE_STAGES",
    "RECOMMENDED_PUBLICATION_STRATEGY",
    "STEP15_DECISION_CONTRACT_NAMES",
    "TASK_INVENTORY",
    "AnalyticalPublicationStep",
    "BackfillRequest",
    "ExecutionPlan",
    "ExecutionUnit",
    "ExecutionUnitSpec",
    "FailureCategory",
    "OrchestrationEvent",
    "OrchestrationEventType",
    "OrchestrationGraph",
    "PublicationConcurrencyStrategy",
    "PublicationScope",
    "SchedulingClass",
    "TaskId",
    "TaskMessage",
    "TaskSpec",
    "analytical_publication_order",
    "assert_bounded_event_details",
    "assert_safe_message_keys",
    "assert_safe_scope_suffix",
    "build_openalex_works_graph",
    "build_orchestration_event",
    "build_publication_scope",
    "build_task_message",
    "capability_ref_for_task",
    "classify_retry",
    "classify_unit_retry",
    "default_orchestration_graph",
    "depends_on_path",
    "execution_unit_for",
    "execution_unit_order_from_logical",
    "execution_unit_spec",
    "is_logical_checkpoint",
    "is_retryable_failure",
    "is_retryable_unit_failure",
    "physical_decision_table_name",
    "physical_invocations",
    "publication_scope_suffix",
    "quality_failure_blocks",
    "render_execution_plan",
    "retry_policy_for",
    "retry_policy_for_unit",
    "scopes_collide",
    "task_message_as_xcom_dict",
    "topological_order",
    "validate_graph",
    "validate_orchestration_plan",
]
