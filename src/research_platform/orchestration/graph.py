"""Deterministic orchestration graph contract (thin DAG — no business logic)."""

from __future__ import annotations

from research_platform.orchestration.execution import (
    EXECUTION_UNIT_CAPABILITY,
    capability_ref_for_task,
    execution_unit_for,
    execution_unit_order_from_logical,
    is_logical_checkpoint,
    physical_invocations,
)
from research_platform.orchestration.models import (
    TASK_INVENTORY,
    ExecutionUnit,
    TaskId,
    TaskSpec,
)
from research_platform.orchestration.retry import retry_policy_for
from research_platform.service.models import SettingsModel


class OrchestrationGraph(SettingsModel):
    """Immutable directed acyclic orchestration graph."""

    tasks: tuple[TaskSpec, ...]

    @property
    def task_ids(self) -> tuple[TaskId, ...]:
        return tuple(t.task_id for t in self.tasks)

    def spec(self, task_id: TaskId) -> TaskSpec:
        for task in self.tasks:
            if task.task_id is task_id:
                return task
        raise KeyError(task_id)


def _spec(
    task_id: TaskId,
    depends_on: tuple[TaskId, ...],
    *,
    description: str,
    quality_gate: str | None = None,
) -> TaskSpec:
    unit = execution_unit_for(task_id)
    return TaskSpec(
        task_id=task_id,
        depends_on=depends_on,
        execution_unit=unit,
        logical_only=is_logical_checkpoint(task_id),
        retry_policy=retry_policy_for(task_id),
        capability_ref=capability_ref_for_task(task_id),
        description=description,
        quality_gate=quality_gate,
    )


def build_openalex_works_graph() -> OrchestrationGraph:
    """Canonical OpenAlex Works orchestration graph for future Airflow mapping."""
    specs = (
        _spec(
            TaskId.DISCOVER,
            (),
            description="Discover bounded source asset metadata",
        ),
        _spec(
            TaskId.REGISTER,
            (TaskId.DISCOVER,),
            description=(
                "Logical checkpoint: asset registration/claim phase of "
                "WORKS_INGEST_UNIT (owned inside ingest_works_asset)"
            ),
        ),
        _spec(
            TaskId.INGEST,
            (TaskId.REGISTER,),
            description=(
                "Primary physical invoke for WORKS_INGEST_UNIT: "
                "ingest_works_asset (claim + land + decode/map + canonical)"
            ),
        ),
        _spec(
            TaskId.CANONICALIZE,
            (TaskId.INGEST,),
            description=(
                "Logical checkpoint: canonical publication phase of "
                "WORKS_INGEST_UNIT (not a second ingest_works_asset call)"
            ),
        ),
        _spec(
            TaskId.APPLY_DELETIONS,
            (TaskId.CANONICALIZE,),
            description="Deletion tombstones / unknown-work barriers (Step 13)",
        ),
        _spec(
            TaskId.ANALYTICAL_PUBLICATION,
            (TaskId.APPLY_DELETIONS,),
            description="Analytical projection under PublicationScope (Step 15)",
        ),
        _spec(
            TaskId.PRE_SERVING_QUALITY,
            (TaskId.ANALYTICAL_PUBLICATION,),
            description="PRE_SERVING_BUILD hard gate (Step 17)",
            quality_gate="PRE_SERVING_BUILD",
        ),
        _spec(
            TaskId.STAGE_GOLD,
            (TaskId.PRE_SERVING_QUALITY,),
            description="Stage Gold marts/views (Step 16) — blocked if PRE_SERVING fails",
        ),
        _spec(
            TaskId.PRE_VISIBLE_QUALITY,
            (TaskId.STAGE_GOLD,),
            description="PRE_VISIBLE_PUBLICATION hard gate (Step 17)",
            quality_gate="PRE_VISIBLE_PUBLICATION",
        ),
        _spec(
            TaskId.PUBLISH_SUCCESS,
            (TaskId.PRE_VISIBLE_QUALITY,),
            description="Atomic consumer publication activation (Step 19)",
        ),
        _spec(
            TaskId.FINAL_VALIDATION,
            (TaskId.PUBLISH_SUCCESS,),
            description="Post-activation consumer coherence checks (Step 19)",
        ),
    )
    graph = OrchestrationGraph(tasks=specs)
    validate_graph(graph)
    return graph


def validate_graph(graph: OrchestrationGraph) -> None:
    """Assert inventory, acyclicity, execution units, and quality-gate edges."""
    ids = graph.task_ids
    if ids != TASK_INVENTORY:
        raise ValueError(
            f"task inventory mismatch: got {ids!r}, expected {TASK_INVENTORY!r}"
        )
    known = set(ids)
    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in known:
                raise ValueError(f"{task.task_id} depends on unknown {dep}")
            if dep is task.task_id:
                raise ValueError(f"{task.task_id} cannot depend on itself")
        if task.execution_unit is not execution_unit_for(task.task_id):
            raise ValueError(f"{task.task_id} execution_unit mismatch")
        if task.logical_only != is_logical_checkpoint(task.task_id):
            raise ValueError(f"{task.task_id} logical_only mismatch")
        if task.logical_only:
            if not task.capability_ref.startswith("logical_checkpoint:"):
                raise ValueError(
                    f"{task.task_id} logical checkpoint must not advertise a callable"
                )
        else:
            expected = EXECUTION_UNIT_CAPABILITY[task.execution_unit]
            if task.capability_ref != expected:
                raise ValueError(
                    f"{task.task_id} capability_ref must match unit capability"
                )

    # Exactly one physical invoke for WORKS_INGEST_UNIT among REGISTER/INGEST/CANONICALIZE
    works_tasks = [
        t
        for t in graph.tasks
        if t.execution_unit is ExecutionUnit.WORKS_INGEST_UNIT
    ]
    primaries = [t for t in works_tasks if not t.logical_only]
    if len(primaries) != 1 or primaries[0].task_id is not TaskId.INGEST:
        raise ValueError(
            "WORKS_INGEST_UNIT must have exactly one primary invoke on INGEST"
        )
    if "ingest_works_asset" not in primaries[0].capability_ref:
        raise ValueError("WORKS_INGEST_UNIT primary must reference ingest_works_asset")
    for t in works_tasks:
        if t.logical_only and "ingest_works_asset" in t.capability_ref:
            raise ValueError(
                f"{t.task_id} must not advertise ingest_works_asset as a second invoke"
            )

    required_edges = (
        (TaskId.DISCOVER, TaskId.REGISTER),
        (TaskId.REGISTER, TaskId.INGEST),
        (TaskId.INGEST, TaskId.CANONICALIZE),
        (TaskId.CANONICALIZE, TaskId.APPLY_DELETIONS),
        (TaskId.APPLY_DELETIONS, TaskId.ANALYTICAL_PUBLICATION),
        (TaskId.ANALYTICAL_PUBLICATION, TaskId.PRE_SERVING_QUALITY),
        (TaskId.PRE_SERVING_QUALITY, TaskId.STAGE_GOLD),
        (TaskId.STAGE_GOLD, TaskId.PRE_VISIBLE_QUALITY),
        (TaskId.PRE_VISIBLE_QUALITY, TaskId.PUBLISH_SUCCESS),
    )
    for upstream, downstream in required_edges:
        deps = graph.spec(downstream).depends_on
        if upstream not in deps:
            raise ValueError(f"{downstream} must depend on {upstream}")

    if TaskId.PRE_VISIBLE_QUALITY not in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on:
        raise ValueError("PUBLISH_SUCCESS must depend on PRE_VISIBLE_QUALITY")
    if TaskId.PRE_SERVING_QUALITY not in graph.spec(TaskId.STAGE_GOLD).depends_on:
        raise ValueError("STAGE_GOLD must depend on PRE_SERVING_QUALITY")
    if TaskId.INGEST in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on:
        raise ValueError("PUBLISH_SUCCESS must not depend directly on INGEST")

    order = topological_order(graph)
    execution_unit_order_from_logical(order)
    # Exactly one Step-12 capability among physical invocations.
    invokes = physical_invocations(order)
    works_invokes = [
        cap
        for unit, cap in invokes
        if unit is ExecutionUnit.WORKS_INGEST_UNIT
    ]
    if len(works_invokes) != 1:
        raise ValueError("physical plan must invoke WORKS_INGEST_UNIT exactly once")


def topological_order(graph: OrchestrationGraph) -> tuple[TaskId, ...]:
    """Deterministic Kahn topological order (stable by TASK_INVENTORY index)."""
    inventory_index = {task_id: i for i, task_id in enumerate(TASK_INVENTORY)}
    indegree: dict[TaskId, int] = {t.task_id: 0 for t in graph.tasks}
    dependents: dict[TaskId, list[TaskId]] = {t.task_id: [] for t in graph.tasks}
    for task in graph.tasks:
        for dep in task.depends_on:
            indegree[task.task_id] += 1
            dependents[dep].append(task.task_id)

    ready = sorted(
        [tid for tid, deg in indegree.items() if deg == 0],
        key=lambda tid: inventory_index[tid],
    )
    ordered: list[TaskId] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for child in sorted(dependents[node], key=lambda tid: inventory_index[tid]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=lambda tid: inventory_index[tid])

    if len(ordered) != len(graph.tasks):
        raise ValueError("orchestration graph contains a cycle")
    return tuple(ordered)


def depends_on_path(graph: OrchestrationGraph, start: TaskId, end: TaskId) -> bool:
    """True if ``end`` is reachable from ``start`` (A -> B when B depends_on A)."""
    forward: dict[TaskId, list[TaskId]] = {t.task_id: [] for t in graph.tasks}
    for task in graph.tasks:
        for dep in task.depends_on:
            forward[dep].append(task.task_id)
    seen: set[TaskId] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node is end:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(forward.get(node, ()))
    return False


def tasks_blocked_by_quality_failure(
    failed_gate: TaskId,
) -> tuple[TaskId, ...]:
    """Downstream tasks blocked when a quality hard gate fails."""
    if failed_gate is TaskId.PRE_SERVING_QUALITY:
        return (
            TaskId.STAGE_GOLD,
            TaskId.PRE_VISIBLE_QUALITY,
            TaskId.PUBLISH_SUCCESS,
            TaskId.FINAL_VALIDATION,
        )
    if failed_gate is TaskId.PRE_VISIBLE_QUALITY:
        return (TaskId.PUBLISH_SUCCESS, TaskId.FINAL_VALIDATION)
    raise ValueError("failed_gate must be a quality task")
