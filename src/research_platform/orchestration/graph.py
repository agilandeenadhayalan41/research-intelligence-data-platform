"""Deterministic orchestration graph contract (thin DAG — no business logic)."""

from __future__ import annotations

from research_platform.orchestration.models import (
    TASK_INVENTORY,
    TaskId,
    TaskSpec,
)
from research_platform.orchestration.retry import retry_policy_for
from research_platform.service.models import SettingsModel


# Capability refs point at existing public APIs — not duplicated here.
_CAPABILITY = {
    TaskId.DISCOVER: "research_platform.sources.openalex.connector.OpenAlexConnector.discover_metadata",
    TaskId.REGISTER: "research_platform.control.store.ControlStore (source file claim/register)",
    TaskId.INGEST: "research_platform.ingestion.pipeline.ingest_works_asset",
    TaskId.CANONICALIZE: "research_platform.ingestion.pipeline.ingest_works_asset (canonical upsert path)",
    TaskId.APPLY_DELETIONS: "research_platform.ingestion.deletion_pipeline.ingest_deletion_asset",
    TaskId.ANALYTICAL_PUBLICATION: (
        "research_platform.analytics.bigquery contracts + "
        "research_platform.e2e.analytical.project_canonical_to_duckdb (local SEMANTIC_ONLY)"
    ),
    TaskId.PRE_SERVING_QUALITY: "research_platform.quality.runner.run_quality_checks (PRE_SERVING_BUILD)",
    TaskId.STAGE_GOLD: "research_platform.analytics.gold.semantic_build.build_all_gold_marts",
    TaskId.PRE_VISIBLE_QUALITY: (
        "research_platform.quality.runner.run_quality_checks (PRE_VISIBLE_PUBLICATION)"
    ),
    TaskId.PUBLISH_SUCCESS: "research_platform.e2e.publication.PublicationStore.activate",
    TaskId.FINAL_VALIDATION: "research_platform.e2e.runner final validation (Step 19)",
}


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


def build_openalex_works_graph() -> OrchestrationGraph:
    """Canonical OpenAlex Works orchestration graph for future Airflow mapping."""
    specs = (
        TaskSpec(
            task_id=TaskId.DISCOVER,
            depends_on=(),
            retry_policy=retry_policy_for(TaskId.DISCOVER),
            capability_ref=_CAPABILITY[TaskId.DISCOVER],
            description="Discover bounded source asset metadata",
        ),
        TaskSpec(
            task_id=TaskId.REGISTER,
            depends_on=(TaskId.DISCOVER,),
            retry_policy=retry_policy_for(TaskId.REGISTER),
            capability_ref=_CAPABILITY[TaskId.REGISTER],
            description="Idempotent asset registration / claim",
        ),
        TaskSpec(
            task_id=TaskId.INGEST,
            depends_on=(TaskId.REGISTER,),
            retry_policy=retry_policy_for(TaskId.INGEST),
            capability_ref=_CAPABILITY[TaskId.INGEST],
            description="Immutable landing + stream ingest (Step 12)",
        ),
        TaskSpec(
            task_id=TaskId.CANONICALIZE,
            depends_on=(TaskId.INGEST,),
            retry_policy=retry_policy_for(TaskId.CANONICALIZE),
            capability_ref=_CAPABILITY[TaskId.CANONICALIZE],
            description="Canonical upsert path (Step 11/12)",
        ),
        TaskSpec(
            task_id=TaskId.APPLY_DELETIONS,
            depends_on=(TaskId.CANONICALIZE,),
            retry_policy=retry_policy_for(TaskId.APPLY_DELETIONS),
            capability_ref=_CAPABILITY[TaskId.APPLY_DELETIONS],
            description="Deletion tombstones / unknown-work barriers (Step 13)",
        ),
        TaskSpec(
            task_id=TaskId.ANALYTICAL_PUBLICATION,
            depends_on=(TaskId.APPLY_DELETIONS,),
            retry_policy=retry_policy_for(TaskId.ANALYTICAL_PUBLICATION),
            capability_ref=_CAPABILITY[TaskId.ANALYTICAL_PUBLICATION],
            description="Analytical projection under PublicationScope (Step 15)",
        ),
        TaskSpec(
            task_id=TaskId.PRE_SERVING_QUALITY,
            depends_on=(TaskId.ANALYTICAL_PUBLICATION,),
            retry_policy=retry_policy_for(TaskId.PRE_SERVING_QUALITY),
            capability_ref=_CAPABILITY[TaskId.PRE_SERVING_QUALITY],
            description="PRE_SERVING_BUILD hard gate (Step 17)",
            quality_gate="PRE_SERVING_BUILD",
        ),
        TaskSpec(
            task_id=TaskId.STAGE_GOLD,
            depends_on=(TaskId.PRE_SERVING_QUALITY,),
            retry_policy=retry_policy_for(TaskId.STAGE_GOLD),
            capability_ref=_CAPABILITY[TaskId.STAGE_GOLD],
            description="Stage Gold marts/views (Step 16) — blocked if PRE_SERVING fails",
        ),
        TaskSpec(
            task_id=TaskId.PRE_VISIBLE_QUALITY,
            depends_on=(TaskId.STAGE_GOLD,),
            retry_policy=retry_policy_for(TaskId.PRE_VISIBLE_QUALITY),
            capability_ref=_CAPABILITY[TaskId.PRE_VISIBLE_QUALITY],
            description="PRE_VISIBLE_PUBLICATION hard gate (Step 17)",
            quality_gate="PRE_VISIBLE_PUBLICATION",
        ),
        TaskSpec(
            task_id=TaskId.PUBLISH_SUCCESS,
            depends_on=(TaskId.PRE_VISIBLE_QUALITY,),
            retry_policy=retry_policy_for(TaskId.PUBLISH_SUCCESS),
            capability_ref=_CAPABILITY[TaskId.PUBLISH_SUCCESS],
            description="Atomic consumer publication activation (Step 19)",
        ),
        TaskSpec(
            task_id=TaskId.FINAL_VALIDATION,
            depends_on=(TaskId.PUBLISH_SUCCESS,),
            retry_policy=retry_policy_for(TaskId.FINAL_VALIDATION),
            capability_ref=_CAPABILITY[TaskId.FINAL_VALIDATION],
            description="Post-activation consumer coherence checks (Step 19)",
        ),
    )
    graph = OrchestrationGraph(tasks=specs)
    validate_graph(graph)
    return graph


def validate_graph(graph: OrchestrationGraph) -> None:
    """Assert inventory, acyclicity, and required quality-gate edges."""
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

    # Required chain edges
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

    # No publish without PRE_VISIBLE; no gold before PRE_SERVING
    if TaskId.PRE_VISIBLE_QUALITY not in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on:
        raise ValueError("PUBLISH_SUCCESS must depend on PRE_VISIBLE_QUALITY")
    if TaskId.PRE_SERVING_QUALITY not in graph.spec(TaskId.STAGE_GOLD).depends_on:
        raise ValueError("STAGE_GOLD must depend on PRE_SERVING_QUALITY")

    # No shortcut INGEST -> PUBLISH_SUCCESS
    if TaskId.INGEST in graph.spec(TaskId.PUBLISH_SUCCESS).depends_on:
        raise ValueError("PUBLISH_SUCCESS must not depend directly on INGEST")

    # Cycle detection via topological sort
    topological_order(graph)


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
    """True if ``end`` is reachable from ``start`` following dependency edges reverse.

    Edge meaning: B depends_on A means A -> B in execution order.
    """
    # Build forward adjacency: dep -> task
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
