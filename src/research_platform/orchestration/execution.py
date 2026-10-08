"""Physical execution units vs logical DAG stages (Step 20 hardening).

Logical TaskIds from #25 remain stable for DAG shape. Physical invocation is
owned by ExecutionUnit so a future Airflow mapper cannot call
``ingest_works_asset`` once for REGISTER, again for INGEST, and again for
CANONICALIZE.
"""

from __future__ import annotations

from research_platform.orchestration.models import (
    TASK_INVENTORY,
    ExecutionUnit,
    TaskId,
)
from research_platform.service.models import SettingsModel


EXECUTION_UNIT_ORDER: tuple[ExecutionUnit, ...] = (
    ExecutionUnit.DISCOVERY_UNIT,
    ExecutionUnit.WORKS_INGEST_UNIT,
    ExecutionUnit.DELETION_UNIT,
    ExecutionUnit.ANALYTICAL_PUBLICATION_UNIT,
    ExecutionUnit.PRE_SERVING_QUALITY_UNIT,
    ExecutionUnit.GOLD_UNIT,
    ExecutionUnit.PRE_VISIBLE_QUALITY_UNIT,
    ExecutionUnit.CONSUMER_PUBLICATION_UNIT,
    ExecutionUnit.FINAL_VALIDATION_UNIT,
)


# One capability reference per physical unit (not per logical TaskId).
EXECUTION_UNIT_CAPABILITY: dict[ExecutionUnit, str] = {
    ExecutionUnit.DISCOVERY_UNIT: (
        "research_platform.sources.openalex.connector.OpenAlexConnector.discover_metadata"
    ),
    ExecutionUnit.WORKS_INGEST_UNIT: (
        "research_platform.ingestion.pipeline.ingest_works_asset"
    ),
    ExecutionUnit.DELETION_UNIT: (
        "research_platform.ingestion.deletion_pipeline.ingest_deletion_asset"
    ),
    ExecutionUnit.ANALYTICAL_PUBLICATION_UNIT: (
        "research_platform.analytics.bigquery contracts + "
        "research_platform.e2e.analytical.project_canonical_to_duckdb (local SEMANTIC_ONLY)"
    ),
    ExecutionUnit.PRE_SERVING_QUALITY_UNIT: (
        "research_platform.quality.runner.run_quality_checks (PRE_SERVING_BUILD)"
    ),
    ExecutionUnit.GOLD_UNIT: (
        "research_platform.analytics.gold.semantic_build.build_all_gold_marts"
    ),
    ExecutionUnit.PRE_VISIBLE_QUALITY_UNIT: (
        "research_platform.quality.runner.run_quality_checks (PRE_VISIBLE_PUBLICATION)"
    ),
    ExecutionUnit.CONSUMER_PUBLICATION_UNIT: (
        "research_platform.e2e.publication.PublicationStore.activate"
    ),
    ExecutionUnit.FINAL_VALIDATION_UNIT: (
        "research_platform.e2e.runner final validation (Step 19)"
    ),
}


TASK_EXECUTION_UNIT: dict[TaskId, ExecutionUnit] = {
    TaskId.DISCOVER: ExecutionUnit.DISCOVERY_UNIT,
    # REGISTER / INGEST / CANONICALIZE are logical phases of ONE Step-12 unit:
    # claim + immutable landing + decode/map + canonical publication.
    TaskId.REGISTER: ExecutionUnit.WORKS_INGEST_UNIT,
    TaskId.INGEST: ExecutionUnit.WORKS_INGEST_UNIT,
    TaskId.CANONICALIZE: ExecutionUnit.WORKS_INGEST_UNIT,
    TaskId.APPLY_DELETIONS: ExecutionUnit.DELETION_UNIT,
    TaskId.ANALYTICAL_PUBLICATION: ExecutionUnit.ANALYTICAL_PUBLICATION_UNIT,
    TaskId.PRE_SERVING_QUALITY: ExecutionUnit.PRE_SERVING_QUALITY_UNIT,
    TaskId.STAGE_GOLD: ExecutionUnit.GOLD_UNIT,
    TaskId.PRE_VISIBLE_QUALITY: ExecutionUnit.PRE_VISIBLE_QUALITY_UNIT,
    TaskId.PUBLISH_SUCCESS: ExecutionUnit.CONSUMER_PUBLICATION_UNIT,
    TaskId.FINAL_VALIDATION: ExecutionUnit.FINAL_VALIDATION_UNIT,
}


# Exactly one logical TaskId per unit owns the physical invoke marker.
PRIMARY_TASK_FOR_UNIT: dict[ExecutionUnit, TaskId] = {
    ExecutionUnit.DISCOVERY_UNIT: TaskId.DISCOVER,
    ExecutionUnit.WORKS_INGEST_UNIT: TaskId.INGEST,
    ExecutionUnit.DELETION_UNIT: TaskId.APPLY_DELETIONS,
    ExecutionUnit.ANALYTICAL_PUBLICATION_UNIT: TaskId.ANALYTICAL_PUBLICATION,
    ExecutionUnit.PRE_SERVING_QUALITY_UNIT: TaskId.PRE_SERVING_QUALITY,
    ExecutionUnit.GOLD_UNIT: TaskId.STAGE_GOLD,
    ExecutionUnit.PRE_VISIBLE_QUALITY_UNIT: TaskId.PRE_VISIBLE_QUALITY,
    ExecutionUnit.CONSUMER_PUBLICATION_UNIT: TaskId.PUBLISH_SUCCESS,
    ExecutionUnit.FINAL_VALIDATION_UNIT: TaskId.FINAL_VALIDATION,
}


class ExecutionUnitSpec(SettingsModel):
    """Physical invoke contract for one ExecutionUnit."""

    unit: ExecutionUnit
    capability_ref: str
    logical_tasks: tuple[TaskId, ...]
    primary_task: TaskId


def execution_unit_for(task_id: TaskId) -> ExecutionUnit:
    return TASK_EXECUTION_UNIT[task_id]


def is_logical_checkpoint(task_id: TaskId) -> bool:
    """True when TaskId is a logical phase, not an independent Python invoke."""
    unit = execution_unit_for(task_id)
    return PRIMARY_TASK_FOR_UNIT[unit] is not task_id


def capability_ref_for_task(task_id: TaskId) -> str:
    unit = execution_unit_for(task_id)
    if is_logical_checkpoint(task_id):
        return f"logical_checkpoint:{task_id.value} ({unit.value})"
    return EXECUTION_UNIT_CAPABILITY[unit]


def execution_unit_spec(unit: ExecutionUnit) -> ExecutionUnitSpec:
    logical = {tid for tid, u in TASK_EXECUTION_UNIT.items() if u is unit}
    ordered = tuple(tid for tid in TASK_INVENTORY if tid in logical)
    return ExecutionUnitSpec(
        unit=unit,
        capability_ref=EXECUTION_UNIT_CAPABILITY[unit],
        logical_tasks=ordered,
        primary_task=PRIMARY_TASK_FOR_UNIT[unit],
    )


def execution_unit_order_from_logical(
    logical_order: tuple[TaskId, ...],
) -> tuple[ExecutionUnit, ...]:
    """Deduplicate logical topo order into physical ExecutionUnit order."""
    seen: set[ExecutionUnit] = set()
    ordered: list[ExecutionUnit] = []
    for task_id in logical_order:
        unit = execution_unit_for(task_id)
        if unit in seen:
            continue
        seen.add(unit)
        ordered.append(unit)
    expected = list(EXECUTION_UNIT_ORDER)
    if ordered != expected:
        raise ValueError(
            f"execution unit order mismatch: got {ordered!r}, expected {expected!r}"
        )
    return tuple(ordered)


def physical_invocations(
    logical_order: tuple[TaskId, ...],
) -> tuple[tuple[ExecutionUnit, str], ...]:
    """Future scheduler mapping: one (unit, capability_ref) per physical invoke."""
    units = execution_unit_order_from_logical(logical_order)
    return tuple((unit, EXECUTION_UNIT_CAPABILITY[unit]) for unit in units)
