"""Composable quality runner against an executor boundary (Step 17 / #24).

Does not couple to BigQuery SDK. DuckDB implements the local SEMANTIC_ONLY path.
A single check ERROR becomes a result; the runner continues; the report blocks.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import duckdb

from research_platform.quality.models import (
    QUALITY_CONTRACT_VERSION,
    ErrorClassification,
    ExecutionStage,
    QualityMetricPoint,
    QualityReport,
    QualityResult,
    QualityStatus,
    ReconciliationExpectation,
    ValidationLabel,
    compute_publication_allowed,
    safe_error_message,
)
from research_platform.quality.registry import (
    DIMENSION_FK_SPECS,
    GOLD_MART_IDS,
    OWNED_RELATIONSHIP_TABLES,
    QualityCheckContract,
    checks_for_stage,
    load_quality_registry,
    required_hard_gate_ids_for_stage,
)


@runtime_checkable
class QualityExecutor(Protocol):
    """Typed executor boundary for quality checks."""

    def table_exists(self, table: str) -> bool: ...

    def execute_scalar(self, sql: str) -> Any: ...

    def execute_rows(self, sql: str) -> list[tuple[Any, ...]]: ...


class DuckDBQualityExecutor:
    """In-memory DuckDB executor for SEMANTIC_ONLY validation."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn

    def table_exists(self, table: str) -> bool:
        rows = self._conn.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = ? AND table_schema = 'main'
            LIMIT 1
            """,
            [table],
        ).fetchall()
        return bool(rows)

    def execute_scalar(self, sql: str) -> Any:
        row = self._conn.execute(sql).fetchone()
        return None if row is None else row[0]

    def execute_rows(self, sql: str) -> list[tuple[Any, ...]]:
        return list(self._conn.execute(sql).fetchall())


def _base_result(
    contract: QualityCheckContract,
    *,
    run_id: str,
    status: QualityStatus,
    **kwargs: Any,
) -> QualityResult:
    return QualityResult(
        check_id=contract.check_id,
        check_kind=contract.kind,
        scope=contract.scope,
        model_name=contract.model_name,
        run_id=run_id,
        contract_version=QUALITY_CONTRACT_VERSION,
        status=status,
        **kwargs,
    )


def _error_result(
    contract: QualityCheckContract,
    *,
    run_id: str,
    classification: ErrorClassification,
    exc: BaseException | None = None,
    message: str = "",
) -> QualityResult:
    msg = message
    if exc is not None:
        msg = safe_error_message(exc, classification=classification)
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.ERROR,
        message=msg,
        error_classification=classification,
        expected_value=contract.threshold,
    )


def _missing_rate(missing: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return missing / denominator


def check_work_id_not_null(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
) -> QualityResult:
    if not executor.table_exists("works"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        count = int(
            executor.execute_scalar(
                "SELECT COUNT(*) FROM works WHERE work_id IS NULL"
            )
            or 0
        )
    except Exception as exc:  # noqa: BLE001 — convert to ERROR result
        return _classify_query_error(contract, run_id=run_id, exc=exc)
    status = QualityStatus.PASS if count == 0 else QualityStatus.FAIL
    return _base_result(
        contract,
        run_id=run_id,
        status=status,
        observed_value=count,
        expected_value=0,
        unit="violation_count",
        message="work_id NULL violations" if status is QualityStatus.FAIL else "ok",
        diagnostic_counts={"violation_count": count},
    )


def check_work_id_unique(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
) -> QualityResult:
    if not executor.table_exists("works"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        # Count duplicate keys only — never emit the IDs.
        count = int(
            executor.execute_scalar(
                """
                SELECT COUNT(*) FROM (
                  SELECT work_id FROM works
                  WHERE work_id IS NOT NULL
                  GROUP BY work_id
                  HAVING COUNT(*) > 1
                ) d
                """
            )
            or 0
        )
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)
    status = QualityStatus.PASS if count == 0 else QualityStatus.FAIL
    return _base_result(
        contract,
        run_id=run_id,
        status=status,
        observed_value=count,
        expected_value=0,
        unit="duplicate_work_id_count",
        message="duplicate work_id keys" if status is QualityStatus.FAIL else "ok",
        diagnostic_counts={"duplicate_work_id_count": count},
    )


def check_source_work_integrity(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
) -> QualityResult:
    """Fail closed: missing required tables → ERROR (not empty/PASS)."""
    if not executor.table_exists("works"):
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_TABLE,
            message="missing required table works",
        )
    missing = [t for t in OWNED_RELATIONSHIP_TABLES if not executor.table_exists(t)]
    if missing:
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_TABLE,
            message=f"missing required relationship table(s): {', '.join(missing)}",
        )

    diagnostics: dict[str, int] = {}
    total = 0
    try:
        for table in OWNED_RELATIONSHIP_TABLES:
            orphan = int(
                executor.execute_scalar(
                    f"""
                    SELECT COUNT(*) FROM {table} r
                    LEFT JOIN works w ON w.work_id = r.work_id
                    WHERE w.work_id IS NULL
                    """
                )
                or 0
            )
            diagnostics[f"{table}_orphan_count"] = orphan
            total += orphan
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)

    # Empty existing tables → PASS. Missing tables already ERRORed above.
    status = QualityStatus.PASS if total == 0 else QualityStatus.FAIL
    diagnostics["total_orphan_count"] = total
    return _base_result(
        contract,
        run_id=run_id,
        status=status,
        observed_value=total,
        expected_value=0,
        unit="orphan_count",
        message="source work orphans" if status is QualityStatus.FAIL else "ok",
        diagnostic_counts=diagnostics,
    )


def check_dimension_integrity(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
) -> QualityResult:
    """Fail closed: missing relation or dimension table → ERROR."""
    diagnostics: dict[str, int] = {}
    total = 0
    try:
        for rel_table, col, dim_table, dim_pk in DIMENSION_FK_SPECS:
            if not executor.table_exists(rel_table):
                return _error_result(
                    contract,
                    run_id=run_id,
                    classification=ErrorClassification.MISSING_TABLE,
                    message=f"missing required relationship table {rel_table}",
                )
            if not executor.table_exists(dim_table):
                return _error_result(
                    contract,
                    run_id=run_id,
                    classification=ErrorClassification.MISSING_TABLE,
                    message=f"missing required dimension table {dim_table}",
                )
            orphan = int(
                executor.execute_scalar(
                    f"""
                    SELECT COUNT(*) FROM {rel_table} r
                    LEFT JOIN {dim_table} d ON d.{dim_pk} = r.{col}
                    WHERE r.{col} IS NOT NULL AND d.{dim_pk} IS NULL
                    """
                )
                or 0
            )
            diagnostics[f"{rel_table}.{col}_orphan_count"] = orphan
            total += orphan
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)

    status = QualityStatus.PASS if total == 0 else QualityStatus.FAIL
    diagnostics["total_orphan_count"] = total
    return _base_result(
        contract,
        run_id=run_id,
        status=status,
        observed_value=total,
        expected_value=0,
        unit="orphan_count",
        message="dimension orphans" if status is QualityStatus.FAIL else "ok",
        diagnostic_counts=diagnostics,
    )


def check_decode_balance(
    contract: QualityCheckContract,
    *,
    run_id: str,
    expectation: ReconciliationExpectation | None,
) -> QualityResult:
    if expectation is None:
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_INPUT,
            message="ReconciliationExpectation required",
        )
    needed = (
        expectation.source_records_decoded,
        expectation.records_mapped_successfully,
        expectation.records_rejected,
    )
    if any(v is None for v in needed):
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_INPUT,
            message="decode balance inputs incomplete",
        )
    decoded = int(expectation.source_records_decoded)  # type: ignore[arg-type]
    mapped = int(expectation.records_mapped_successfully)  # type: ignore[arg-type]
    rejected = int(expectation.records_rejected)  # type: ignore[arg-type]
    ok = decoded == mapped + rejected
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS if ok else QualityStatus.FAIL,
        observed_value=decoded,
        expected_value=mapped + rejected,
        unit="count",
        message="decode balance ok" if ok else "decode balance mismatch",
        diagnostic_counts={
            "decoded": decoded,
            "mapped_successfully": mapped,
            "rejected": rejected,
        },
    )


def check_publication_decision_balance(
    contract: QualityCheckContract,
    *,
    run_id: str,
    expectation: ReconciliationExpectation | None,
) -> QualityResult:
    if expectation is None:
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_INPUT,
            message="ReconciliationExpectation required",
        )
    fields = (
        expectation.unique_work_ids_evaluated,
        expectation.inserted,
        expectation.updated,
        expectation.identical,
        expectation.stale,
        expectation.conflict,
        expectation.restore_required,
    )
    if any(v is None for v in fields):
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.MISSING_INPUT,
            message="publication decision balance inputs incomplete",
        )
    evaluated = int(expectation.unique_work_ids_evaluated)  # type: ignore[arg-type]
    decision_sum = (
        int(expectation.inserted)  # type: ignore[arg-type]
        + int(expectation.updated)  # type: ignore[arg-type]
        + int(expectation.identical)  # type: ignore[arg-type]
        + int(expectation.stale)  # type: ignore[arg-type]
        + int(expectation.conflict)  # type: ignore[arg-type]
        + int(expectation.restore_required)  # type: ignore[arg-type]
    )
    ok = evaluated == decision_sum
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS if ok else QualityStatus.FAIL,
        observed_value=evaluated,
        expected_value=decision_sum,
        unit="count",
        message="decision balance ok" if ok else "decision balance mismatch",
        diagnostic_counts={
            "unique_work_ids_evaluated": evaluated,
            "decision_sum": decision_sum,
        },
    )


def check_gold_deleted_source_exclusion(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
) -> QualityResult:
    """Staged Gold coverage + source Work existence/ACTIVE hard gate.

    Required tables (missing ≠ empty):
      staged_gold_mart_manifest(mart_id)
      staged_gold_work_contributions(mart_id, work_id)
      works
      staged_gold_citation_edges — required when citation_edges is in the manifest

    Complete Step-16 publication unit: manifest must contain every GOLD_MART_IDS
    entry (zero-row marts are represented by manifest membership alone).
    """
    for table in ("works", "staged_gold_mart_manifest", "staged_gold_work_contributions"):
        if not executor.table_exists(table):
            return _error_result(
                contract,
                run_id=run_id,
                classification=ErrorClassification.MISSING_TABLE,
                message=f"missing required table {table}",
            )
    try:
        manifest_rows = executor.execute_rows(
            "SELECT mart_id FROM staged_gold_mart_manifest ORDER BY mart_id"
        )
        manifest_ids = {str(row[0]) for row in manifest_rows if row[0] is not None}
        expected = set(GOLD_MART_IDS)
        if manifest_ids != expected:
            missing = sorted(expected - manifest_ids)
            extra = sorted(manifest_ids - expected)
            return _error_result(
                contract,
                run_id=run_id,
                classification=ErrorClassification.MISSING_INPUT,
                message=(
                    "incomplete staged Gold mart coverage: "
                    f"missing={missing}; extra={extra}"
                ),
            )

        if "citation_edges" in manifest_ids and not executor.table_exists(
            "staged_gold_citation_edges"
        ):
            return _error_result(
                contract,
                run_id=run_id,
                classification=ErrorClassification.MISSING_TABLE,
                message=(
                    "staged_gold_citation_edges required when citation_edges "
                    "is in the publication manifest"
                ),
            )

        missing_contrib = int(
            executor.execute_scalar(
                """
                SELECT COUNT(*) FROM staged_gold_work_contributions g
                LEFT JOIN works w ON w.work_id = g.work_id
                WHERE w.work_id IS NULL
                """
            )
            or 0
        )
        inactive_contrib = int(
            executor.execute_scalar(
                """
                SELECT COUNT(*) FROM staged_gold_work_contributions g
                INNER JOIN works w ON w.work_id = g.work_id
                WHERE w.activity_state <> 'ACTIVE'
                """
            )
            or 0
        )
        missing_cite = 0
        inactive_cite = 0
        if executor.table_exists("staged_gold_citation_edges"):
            missing_cite = int(
                executor.execute_scalar(
                    """
                    SELECT COUNT(*) FROM staged_gold_citation_edges c
                    LEFT JOIN works w ON w.work_id = c.source_work_id
                    WHERE w.work_id IS NULL
                    """
                )
                or 0
            )
            inactive_cite = int(
                executor.execute_scalar(
                    """
                    SELECT COUNT(*) FROM staged_gold_citation_edges c
                    INNER JOIN works w ON w.work_id = c.source_work_id
                    WHERE w.activity_state <> 'ACTIVE'
                    """
                )
                or 0
            )
        # Citation TARGET DELETED/ABSENT must NOT count as a violation.
        missing_total = missing_contrib + missing_cite
        inactive_total = inactive_contrib + inactive_cite
        total = missing_total + inactive_total
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)

    status = QualityStatus.PASS if total == 0 else QualityStatus.FAIL
    return _base_result(
        contract,
        run_id=run_id,
        status=status,
        observed_value=total,
        expected_value=0,
        unit="violation_count",
        message="Gold source Work violations" if status is QualityStatus.FAIL else "ok",
        diagnostic_counts={
            "missing_source_work_count": missing_total,
            "inactive_source_work_count": inactive_total,
            "missing_contrib_rows": missing_contrib,
            "inactive_contrib_rows": inactive_contrib,
            "missing_citation_source_rows": missing_cite,
            "inactive_citation_source_rows": inactive_cite,
            "manifest_mart_count": len(manifest_ids),
        },
    )


def _active_missing_metric(
    executor: QualityExecutor,
    contract: QualityCheckContract,
    *,
    run_id: str,
    missing_sql: str,
) -> QualityResult:
    if not executor.table_exists("works"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        denominator = int(
            executor.execute_scalar(
                "SELECT COUNT(*) FROM works WHERE activity_state = 'ACTIVE'"
            )
            or 0
        )
        missing = int(executor.execute_scalar(missing_sql) or 0)
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)
    rate = _missing_rate(missing, denominator)
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS,
        observed_value=missing,
        denominator=denominator,
        unit="missing_count",
        message="informational metric",
        diagnostic_counts={
            "missing_count": missing,
            "active_work_denominator": denominator,
        },
        metric_points=(
            QualityMetricPoint(
                dimension_value=None,
                count=missing,
                denominator=denominator,
                rate=rate,
            ),
        ),
    )


def check_missing_doi(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    return _active_missing_metric(
        executor,
        contract,
        run_id=run_id,
        missing_sql=(
            "SELECT COUNT(*) FROM works WHERE activity_state = 'ACTIVE' "
            "AND doi IS NULL"
        ),
    )


def check_missing_title(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    return _active_missing_metric(
        executor,
        contract,
        run_id=run_id,
        missing_sql=(
            "SELECT COUNT(*) FROM works WHERE activity_state = 'ACTIVE' "
            "AND (title IS NULL OR title = '')"
        ),
    )


def check_without_topics(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    if not executor.table_exists("work_topics"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    return _active_missing_metric(
        executor,
        contract,
        run_id=run_id,
        missing_sql=(
            """
            SELECT COUNT(*) FROM works w
            WHERE w.activity_state = 'ACTIVE'
              AND NOT EXISTS (
                SELECT 1 FROM work_topics wt
                WHERE wt.work_id = w.work_id AND wt.topic_id IS NOT NULL
              )
            """
        ),
    )


def check_without_authors(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    if not executor.table_exists("work_authors"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    return _active_missing_metric(
        executor,
        contract,
        run_id=run_id,
        missing_sql=(
            """
            SELECT COUNT(*) FROM works w
            WHERE w.activity_state = 'ACTIVE'
              AND NOT EXISTS (
                SELECT 1 FROM work_authors wa
                WHERE wa.work_id = w.work_id AND wa.author_id IS NOT NULL
              )
            """
        ),
    )


def check_publication_year_distribution(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    if not executor.table_exists("works"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        rows = executor.execute_rows(
            """
            SELECT publication_year, COUNT(*) AS work_count
            FROM works
            WHERE activity_state = 'ACTIVE'
            GROUP BY publication_year
            ORDER BY publication_year NULLS LAST
            """
        )
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)
    points = tuple(
        QualityMetricPoint(
            dimension_value=None if year is None else str(year),
            count=int(count),
            denominator=None,
            rate=None,
        )
        for year, count in rows
    )
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS,
        observed_value=len(points),
        unit="bucket_count",
        message="informational distribution",
        diagnostic_counts={"bucket_count": len(points)},
        metric_points=points,
    )


def check_work_type_distribution(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    if not executor.table_exists("works"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        rows = executor.execute_rows(
            """
            SELECT work_type, COUNT(*) AS work_count
            FROM works
            WHERE activity_state = 'ACTIVE'
            GROUP BY work_type
            ORDER BY work_type NULLS LAST
            """
        )
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)
    points = tuple(
        QualityMetricPoint(
            dimension_value=None if work_type is None else str(work_type),
            count=int(count),
            denominator=None,
            rate=None,
        )
        for work_type, count in rows
    )
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS,
        observed_value=len(points),
        unit="bucket_count",
        message="informational distribution",
        diagnostic_counts={"bucket_count": len(points)},
        metric_points=points,
    )


def check_reference_reconciliation(
    executor: QualityExecutor, contract: QualityCheckContract, *, run_id: str
) -> QualityResult:
    if not executor.table_exists("works") or not executor.table_exists("work_references"):
        return _error_result(
            contract, run_id=run_id, classification=ErrorClassification.MISSING_TABLE
        )
    try:
        rows = executor.execute_rows(
            """
            SELECT
              CASE
                WHEN wr.reference_status = 'MISSING' THEN 'REFERENCE_MISSING'
                WHEN wr.reference_status = 'MALFORMED' THEN 'REFERENCE_MALFORMED'
                WHEN wr.reference_status = 'RESOLVED_ID' AND tw.activity_state = 'ACTIVE'
                  THEN 'TARGET_ACTIVE'
                WHEN wr.reference_status = 'RESOLVED_ID' AND tw.activity_state = 'DELETED'
                  THEN 'TARGET_DELETED'
                WHEN wr.reference_status = 'RESOLVED_ID' AND tw.work_id IS NULL
                  THEN 'TARGET_ABSENT'
                ELSE 'TARGET_ABSENT'
              END AS category,
              COUNT(*) AS cnt
            FROM work_references wr
            INNER JOIN works sw
              ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
            LEFT JOIN works tw ON tw.work_id = wr.referenced_work_id
            GROUP BY 1
            """
        )
        total = int(
            executor.execute_scalar(
                """
                SELECT COUNT(*) FROM work_references wr
                INNER JOIN works sw
                  ON sw.work_id = wr.work_id AND sw.activity_state = 'ACTIVE'
                """
            )
            or 0
        )
    except Exception as exc:  # noqa: BLE001
        return _classify_query_error(contract, run_id=run_id, exc=exc)

    counts = {str(cat): int(cnt) for cat, cnt in rows}
    for key in (
        "TARGET_ACTIVE",
        "TARGET_DELETED",
        "TARGET_ABSENT",
        "REFERENCE_MISSING",
        "REFERENCE_MALFORMED",
    ):
        counts.setdefault(key, 0)
    category_sum = sum(counts.values())
    ok = category_sum == total
    points = tuple(
        QualityMetricPoint(dimension_value=k, count=v, denominator=total, rate=None)
        for k, v in sorted(counts.items())
    )
    diagnostics = {k.lower(): v for k, v in counts.items()}
    diagnostics["total_reference_rows"] = total
    diagnostics["category_sum"] = category_sum
    return _base_result(
        contract,
        run_id=run_id,
        status=QualityStatus.PASS if ok else QualityStatus.FAIL,
        observed_value=category_sum,
        expected_value=total,
        denominator=total,
        unit="count",
        message="reference categories reconcile" if ok else "category sum mismatch",
        diagnostic_counts=diagnostics,
        metric_points=points,
    )


def _classify_query_error(
    contract: QualityCheckContract, *, run_id: str, exc: BaseException
) -> QualityResult:
    text = str(exc).lower()
    if "no such column" in text or "column" in text and "not found" in text:
        classification = ErrorClassification.MISSING_COLUMN
    elif "no such table" in text or "does not exist" in text:
        classification = ErrorClassification.MISSING_TABLE
    elif "syntax" in text or "binder" in text or "catalog" in text:
        classification = ErrorClassification.QUERY_ERROR
    else:
        classification = ErrorClassification.EXECUTOR_ERROR
    return _error_result(contract, run_id=run_id, classification=classification, exc=exc)


_CHECK_DISPATCH: dict[str, str] = {
    "canonical.works.work_id_not_null": "work_id_not_null",
    "canonical.works.work_id_unique": "work_id_unique",
    "canonical.relationships.source_work_integrity": "source_work",
    "canonical.relationships.dimension_integrity": "dimension",
    "reconciliation.decode_balance": "decode",
    "reconciliation.publication_decision_balance": "publication",
    "gold.deleted_source_work_exclusion": "gold_exclusion",
    "metric.active_works.missing_doi": "missing_doi",
    "metric.active_works.missing_title": "missing_title",
    "metric.active_works.without_topics": "without_topics",
    "metric.active_works.without_authors": "without_authors",
    "metric.active_works.publication_year_distribution": "year_dist",
    "metric.active_works.work_type_distribution": "type_dist",
    "metric.active_works.reference_reconciliation": "ref_recon",
}


def execute_check(
    contract: QualityCheckContract,
    executor: QualityExecutor,
    *,
    run_id: str,
    expectation: ReconciliationExpectation | None = None,
) -> QualityResult:
    """Execute one check; never raise — convert failures to ERROR results."""
    try:
        key = _CHECK_DISPATCH.get(contract.check_id)
        if key == "work_id_not_null":
            return check_work_id_not_null(executor, contract, run_id=run_id)
        if key == "work_id_unique":
            return check_work_id_unique(executor, contract, run_id=run_id)
        if key == "source_work":
            return check_source_work_integrity(executor, contract, run_id=run_id)
        if key == "dimension":
            return check_dimension_integrity(executor, contract, run_id=run_id)
        if key == "decode":
            return check_decode_balance(contract, run_id=run_id, expectation=expectation)
        if key == "publication":
            return check_publication_decision_balance(
                contract, run_id=run_id, expectation=expectation
            )
        if key == "gold_exclusion":
            return check_gold_deleted_source_exclusion(executor, contract, run_id=run_id)
        if key == "missing_doi":
            return check_missing_doi(executor, contract, run_id=run_id)
        if key == "missing_title":
            return check_missing_title(executor, contract, run_id=run_id)
        if key == "without_topics":
            return check_without_topics(executor, contract, run_id=run_id)
        if key == "without_authors":
            return check_without_authors(executor, contract, run_id=run_id)
        if key == "year_dist":
            return check_publication_year_distribution(executor, contract, run_id=run_id)
        if key == "type_dist":
            return check_work_type_distribution(executor, contract, run_id=run_id)
        if key == "ref_recon":
            return check_reference_reconciliation(executor, contract, run_id=run_id)
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.EXECUTOR_ERROR,
            message=f"no implementation for {contract.check_id}",
        )
    except Exception as exc:  # noqa: BLE001
        return _error_result(
            contract,
            run_id=run_id,
            classification=ErrorClassification.EXECUTOR_ERROR,
            exc=exc,
        )


def run_quality_checks(
    executor: QualityExecutor,
    *,
    run_id: str,
    stage: ExecutionStage,
    checks: tuple[QualityCheckContract, ...] | None = None,
    expectation: ReconciliationExpectation | None = None,
) -> QualityReport:
    """Run checks for one execution stage and build a stage-local report.

    ``stage`` selects which registered checks run and which required hard gates
    must PASS:

    - PRE_SERVING_BUILD: canonical + reconciliation only (no staged Gold tables)
    - PRE_VISIBLE_PUBLICATION: staged Gold gates only

    ``publication_allowed`` is stage-local:
    - PRE_SERVING_BUILD → allowed to proceed to serving/Gold build
    - PRE_VISIBLE_PUBLICATION → allowed to make staged consumer outputs visible

    Continues after per-check ERROR. Informational metrics never independently
    block or permit publication.
    """
    registry = checks if checks is not None else load_quality_registry()
    stage_checks = checks_for_stage(stage, registry)
    results: list[QualityResult] = []
    for contract in stage_checks:
        results.append(
            execute_check(
                contract, executor, run_id=run_id, expectation=expectation
            )
        )

    required_ids = required_hard_gate_ids_for_stage(stage, registry)
    hard_ok, pub_ok = compute_publication_allowed(
        required_check_ids=required_ids,
        results=tuple(results),
    )
    stage_note = {
        ExecutionStage.PRE_SERVING_BUILD: (
            "publication_allowed means proceed to serving/Gold build"
        ),
        ExecutionStage.PRE_VISIBLE_PUBLICATION: (
            "publication_allowed means staged consumer outputs may become visible"
        ),
    }[stage]
    return QualityReport(
        run_id=run_id,
        contract_version=QUALITY_CONTRACT_VERSION,
        execution_stage=stage,
        results=tuple(results),
        hard_gate_passed=hard_ok,
        publication_allowed=pub_ok,
        validation_label=ValidationLabel.SEMANTIC_ONLY,
        notes=(
            f"{stage.value}: {stage_note}. "
            "SEMANTIC_ONLY DuckDB/local validation — does not prove BigQuery "
            "syntax, cost, latency, or production scale."
        ),
    )
