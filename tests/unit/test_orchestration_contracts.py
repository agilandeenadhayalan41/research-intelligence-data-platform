"""Unit tests for Step 20 orchestration contracts (no Airflow/Composer/GCP)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from research_platform.orchestration import (
    ANALYTICAL_PUBLICATION_ORDER,
    FORBIDDEN_MESSAGE_KEYS,
    RECOMMENDED_PUBLICATION_STRATEGY,
    STEP15_DECISION_CONTRACT_NAMES,
    TASK_INVENTORY,
    BackfillRequest,
    FailureCategory,
    OrchestrationEventType,
    PublicationConcurrencyStrategy,
    SchedulingClass,
    TaskId,
    assert_safe_message_keys,
    assert_safe_scope_suffix,
    build_openalex_works_graph,
    build_orchestration_event,
    build_publication_scope,
    build_task_message,
    depends_on_path,
    is_retryable_failure,
    physical_decision_table_name,
    publication_scope_suffix,
    quality_failure_blocks,
    render_execution_plan,
    retry_policy_for,
    scopes_collide,
    task_message_as_xcom_dict,
    topological_order,
    validate_orchestration_plan,
)
from research_platform.quality.models import ExecutionStage


def test_stable_task_inventory() -> None:
    assert TASK_INVENTORY == (
        TaskId.DISCOVER,
        TaskId.REGISTER,
        TaskId.INGEST,
        TaskId.CANONICALIZE,
        TaskId.APPLY_DELETIONS,
        TaskId.ANALYTICAL_PUBLICATION,
        TaskId.PRE_SERVING_QUALITY,
        TaskId.STAGE_GOLD,
        TaskId.PRE_VISIBLE_QUALITY,
        TaskId.PUBLISH_SUCCESS,
        TaskId.FINAL_VALIDATION,
    )
    graph = build_openalex_works_graph()
    assert graph.task_ids == TASK_INVENTORY


def test_graph_is_acyclic_and_deterministic_order() -> None:
    graph = validate_orchestration_plan()
    order = topological_order(graph)
    assert order == TASK_INVENTORY
    assert topological_order(graph) == order


def test_required_dependency_chain() -> None:
    graph = build_openalex_works_graph()
    chain = [
        TaskId.DISCOVER,
        TaskId.REGISTER,
        TaskId.INGEST,
        TaskId.CANONICALIZE,
        TaskId.APPLY_DELETIONS,
        TaskId.ANALYTICAL_PUBLICATION,
        TaskId.PRE_SERVING_QUALITY,
        TaskId.STAGE_GOLD,
        TaskId.PRE_VISIBLE_QUALITY,
        TaskId.PUBLISH_SUCCESS,
    ]
    for upstream, downstream in zip(chain[:-1], chain[1:], strict=True):
        assert upstream in graph.spec(downstream).depends_on
        assert depends_on_path(graph, upstream, downstream)


def test_publish_requires_pre_visible_and_gold_requires_pre_serving() -> None:
    graph = build_openalex_works_graph()
    assert TaskId.PRE_VISIBLE_QUALITY in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on
    assert TaskId.PRE_SERVING_QUALITY in graph.spec(TaskId.STAGE_GOLD).depends_on
    assert TaskId.INGEST not in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on
    assert not depends_on_path(graph, TaskId.PUBLISH_SUCCESS, TaskId.INGEST)


def test_no_mandatory_postgres_serving_task() -> None:
    ids = {t.value for t in TASK_INVENTORY}
    assert "POSTGRES_SERVING" not in ids
    assert "HTTP_API" not in ids
    assert "COMPOSER" not in ids
    assert "DATAFORM" not in ids


def test_task_message_forbids_payload_dsn_sql_credentials() -> None:
    msg = build_task_message(
        run_id=uuid4(),
        publication_version="v1",
        source="openalex",
        task_id=TaskId.DISCOVER,
        attempt=1,
        environment="local",
    )
    data = task_message_as_xcom_dict(msg)
    for key in FORBIDDEN_MESSAGE_KEYS:
        assert key not in data
    with pytest.raises(ValueError, match="forbidden"):
        assert_safe_message_keys({"dsn": "secret"})
    with pytest.raises(ValueError, match="forbidden"):
        assert_safe_message_keys({"SQL": "select 1"})
    with pytest.raises(ValueError, match="forbidden"):
        assert_safe_message_keys({"payload_bytes": b"x"})
    with pytest.raises(ValueError, match="forbidden"):
        assert_safe_message_keys({"credentials": {"token": "x"}})


def test_retry_policies_are_bounded() -> None:
    for task_id in TASK_INVENTORY:
        policy = retry_policy_for(task_id)
        assert 1 <= policy.max_attempts <= 5


def test_quality_failures_non_retryable() -> None:
    assert not is_retryable_failure(
        TaskId.PRE_SERVING_QUALITY, FailureCategory.QUALITY_HARD_GATE
    )
    assert not is_retryable_failure(
        TaskId.PRE_VISIBLE_QUALITY, FailureCategory.QUALITY_HARD_GATE
    )
    assert quality_failure_blocks(TaskId.PRE_SERVING_QUALITY) == (
        TaskId.STAGE_GOLD,
        TaskId.PRE_VISIBLE_QUALITY,
        TaskId.PUBLISH_SUCCESS,
        TaskId.FINAL_VALIDATION,
    )
    assert quality_failure_blocks(TaskId.PRE_VISIBLE_QUALITY) == (
        TaskId.PUBLISH_SUCCESS,
        TaskId.FINAL_VALIDATION,
    )


def test_publication_conflict_non_retryable_discover_transient_retryable() -> None:
    assert not is_retryable_failure(
        TaskId.PUBLISH_SUCCESS, FailureCategory.PUBLICATION_CONFLICT
    )
    assert is_retryable_failure(
        TaskId.DISCOVER, FailureCategory.TRANSIENT_TRANSPORT
    )
    assert not is_retryable_failure(
        TaskId.CANONICALIZE, FailureCategory.RESTORE_REQUIRED
    )
    assert not is_retryable_failure(
        TaskId.CANONICALIZE, FailureCategory.CANONICAL_CONFLICT
    )


def test_backfill_request_cannot_be_unbounded() -> None:
    ok = BackfillRequest(
        source="openalex",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        max_files_per_run=1,
        max_file_size_bytes=25_000_000,
        requested_by="tester",
        reason="synthetic local validation",
        dry_run=True,
    )
    assert ok.dry_run is True
    with pytest.raises((ValidationError, ValueError)):
        BackfillRequest(
            source="openalex",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            max_files_per_run=None,  # type: ignore[arg-type]
            max_file_size_bytes=25_000_000,
            requested_by="tester",
            reason="x",
        )
    with pytest.raises((ValidationError, ValueError)):
        BackfillRequest(
            source="openalex",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1) + timedelta(days=400),
            max_files_per_run=1,
            max_file_size_bytes=25_000_000,
            requested_by="tester",
            reason="too wide",
        )
    with pytest.raises((ValidationError, ValueError)):
        BackfillRequest(
            source="openalex",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 1),
            max_files_per_run=1,
            max_file_size_bytes=25_000_000,
            requested_by="tester",
            reason="inverted",
        )


def test_run_scoped_identifiers_safe_and_deterministic() -> None:
    rid = UUID("12345678-1234-5678-1234-567812345678")
    a = publication_scope_suffix(rid)
    b = publication_scope_suffix(str(rid))
    assert a == b == "r_12345678123456781234567812345678"
    assert_safe_scope_suffix(a)
    with pytest.raises(ValueError):
        assert_safe_scope_suffix("';DROP TABLE")
    with pytest.raises(ValueError):
        assert_safe_scope_suffix("r_ABC")
    with pytest.raises(ValueError):
        publication_scope_suffix("not-a-uuid")


def test_concurrent_run_scopes_do_not_collide() -> None:
    s1 = build_publication_scope(
        run_id=uuid4(),
        publication_version="v1",
        strategy=PublicationConcurrencyStrategy.RUN_SCOPED,
    )
    s2 = build_publication_scope(
        run_id=uuid4(),
        publication_version="v1",
        strategy=PublicationConcurrencyStrategy.RUN_SCOPED,
    )
    assert not scopes_collide(s1, s2)
    assert physical_decision_table_name("work_publication_decisions", s1) != (
        physical_decision_table_name("work_publication_decisions", s2)
    )
    same = build_publication_scope(
        run_id=s1.run_id,
        publication_version="v2",
        strategy=PublicationConcurrencyStrategy.RUN_SCOPED,
    )
    assert scopes_collide(s1, same)


def test_temp_tables_recommended_and_keep_contract_names() -> None:
    assert RECOMMENDED_PUBLICATION_STRATEGY is (
        PublicationConcurrencyStrategy.TEMP_TABLES
    )
    scope = build_publication_scope(
        run_id=uuid4(),
        publication_version="v1",
        strategy=PublicationConcurrencyStrategy.TEMP_TABLES,
    )
    for name in STEP15_DECISION_CONTRACT_NAMES:
        assert physical_decision_table_name(name, scope) == name


def test_analytical_publication_order_contract() -> None:
    assert ANALYTICAL_PUBLICATION_ORDER[0].value == "FREEZE_WORK_PUBLICATION_DECISIONS"
    assert ANALYTICAL_PUBLICATION_ORDER[-1].value == "REPLACE_ELIGIBLE_RELATIONSHIPS"
    assert len(ANALYTICAL_PUBLICATION_ORDER) == 5


def test_quality_gates_map_to_step17_stages() -> None:
    from research_platform.orchestration.contracts import QUALITY_GATE_STAGES

    assert (
        QUALITY_GATE_STAGES[TaskId.PRE_SERVING_QUALITY]
        is ExecutionStage.PRE_SERVING_BUILD
    )
    assert (
        QUALITY_GATE_STAGES[TaskId.PRE_VISIBLE_QUALITY]
        is ExecutionStage.PRE_VISIBLE_PUBLICATION
    )


def test_render_execution_plan_dry_run() -> None:
    plan = render_execution_plan(
        run_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        publication_version="pub-plan-1",
        scheduling_class=SchedulingClass.MANUAL,
    )
    assert plan.topological_order == TASK_INVENTORY
    assert plan.publication_version == "pub-plan-1"
    assert "CONTRACT_ONLY" in plan.notes[0]
    assert "Airflow" in plan.notes[0]
    assert plan.tasks[0].task_id is TaskId.DISCOVER
    assert plan.tasks[-1].task_id is TaskId.FINAL_VALIDATION


def test_observability_event_rejects_forbidden_details() -> None:
    event = build_orchestration_event(
        event_type=OrchestrationEventType.TASK_FAILED,
        run_id=uuid4(),
        task_id=TaskId.PRE_VISIBLE_QUALITY,
        attempt=1,
        occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
        safe_error_category=FailureCategory.QUALITY_HARD_GATE,
        recovery_action="inspect aggregate quality diagnostics; do not publish candidate",
        details={"stage": "PRE_VISIBLE_QUALITY"},
    )
    assert event.event_type is OrchestrationEventType.TASK_FAILED
    with pytest.raises(ValueError, match="forbidden"):
        build_orchestration_event(
            event_type=OrchestrationEventType.TASK_FAILED,
            run_id=uuid4(),
            details={"password": "x"},
        )


def test_no_airflow_dependency_imported() -> None:
    import importlib
    import sys

    # Ensure orchestration package does not pull apache-airflow / google composer.
    def _airflowish(name: str) -> bool:
        return (
            name == "airflow"
            or name.startswith("airflow.")
            or name.startswith("apache.airflow")
            or name.startswith("composer.")
        )

    assert [m for m in sys.modules if _airflowish(m)] == []
    importlib.import_module("research_platform.orchestration")
    assert [m for m in sys.modules if _airflowish(m)] == []
    # Runtime dependency must not be declared either.
    import importlib.metadata as md

    reqs = {
        d.split("[")[0].split("==")[0].lower()
        for d in md.requires("research-intelligence-data-platform") or []
    }
    assert "apache-airflow" not in reqs
    assert "composer" not in reqs


def test_scheduling_classes_exist() -> None:
    assert set(SchedulingClass) == {
        SchedulingClass.MANUAL,
        SchedulingClass.SCHEDULED_INCREMENTAL,
        SchedulingClass.BACKFILL,
    }


def test_thin_dag_capability_refs_point_outside_orchestration() -> None:
    graph = build_openalex_works_graph()
    for spec in graph.tasks:
        assert "research_platform.orchestration" not in spec.capability_ref
        assert spec.capability_ref
        # No embedded SQL / DSN in task specs
        assert "postgres://" not in spec.capability_ref.lower()
        assert "select " not in spec.capability_ref.lower()
