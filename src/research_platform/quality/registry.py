"""Authoritative data-quality check registry (Step 17 / #24)."""

from __future__ import annotations

from pathlib import Path

from research_platform.quality.models import (
    BackendSupport,
    ExecutionStage,
    QualityCheckContract,
    QualityCheckKind,
    QualityScope,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUALITY_SQL = "sql/bigquery/openalex/quality"

# Owned Work relationship tables (source work_id must exist).
OWNED_RELATIONSHIP_TABLES: tuple[str, ...] = (
    "work_authors",
    "work_author_institutions",
    "work_topics",
    "work_keywords",
    "work_references",
    "work_mesh",
    "work_locations",
    "work_grants",
)

# Dimension FK checks: (relationship_table, column, dimension_table, dimension_pk)
# Keywords/mesh have no dimension entity tables in the canonical model.
DIMENSION_FK_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("work_authors", "author_id", "authors", "author_id"),
    ("work_author_institutions", "institution_id", "institutions", "institution_id"),
    ("work_topics", "topic_id", "topics", "topic_id"),
    ("work_locations", "source_id", "sources", "source_id"),
    ("work_grants", "funder_id", "funders", "funder_id"),
)

def _step16_gold_mart_ids() -> tuple[str, ...]:
    """Authoritative Gold mart inventory from Step 16 registry (no hand copy)."""
    from research_platform.analytics.gold.registry import load_gold_registry

    return tuple(m.mart_id for m in load_gold_registry())


# Step 16 consumer Gold marts for deleted-source exclusion.
GOLD_MART_IDS: tuple[str, ...] = _step16_gold_mart_ids()


def quality_check_contracts() -> tuple[QualityCheckContract, ...]:
    return (
        QualityCheckContract(
            check_id="canonical.works.work_id_not_null",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="Canonical work_id must never be NULL.",
            required=True,
            expected_rule="violation_count == 0",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/canonical_work_id_not_null.sql",
            diagnostic_fields=("violation_count",),
            threshold=0,
        ),
        QualityCheckContract(
            check_id="canonical.works.work_id_unique",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="Canonical work_id must be unique (duplicate count only).",
            required=True,
            expected_rule="duplicate_work_id_count == 0",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/canonical_work_id_unique.sql",
            diagnostic_fields=("duplicate_work_id_count",),
            threshold=0,
        ),
        QualityCheckContract(
            check_id="canonical.relationships.source_work_integrity",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.CANONICAL,
            model_name="owned_work_relationships",
            description=(
                "Owned Work relationships must reference an existing canonical Work "
                "(source orphan check). Citation TARGET absence is not an orphan."
            ),
            required=True,
            expected_rule="total_orphan_count == 0",
            input_tables=("works", *OWNED_RELATIONSHIP_TABLES),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/relationship_source_work_integrity.sql",
            diagnostic_fields=("total_orphan_count", "orphan_count_by_relationship"),
            threshold=0,
            notes=(
                "CANONICAL may retain DELETED Works and their Policy A relationships. "
                "This gate checks source work_id existence, not activity_state."
            ),
        ),
        QualityCheckContract(
            check_id="canonical.relationships.dimension_integrity",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.CANONICAL,
            model_name="dimension_references",
            description=(
                "Non-null dimension IDs in owned relationships must exist in entity "
                "tables. Allowed NULL IDs are not failures. No keyword/mesh dimension tables."
            ),
            required=True,
            expected_rule="total_orphan_count == 0",
            input_tables=(
                "work_authors",
                "authors",
                "work_author_institutions",
                "institutions",
                "work_topics",
                "topics",
                "work_locations",
                "sources",
                "work_grants",
                "funders",
            ),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/relationship_dimension_integrity.sql",
            diagnostic_fields=("total_orphan_count",),
            threshold=0,
        ),
        QualityCheckContract(
            check_id="reconciliation.decode_balance",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.RECONCILIATION,
            model_name="reconciliation_expectation",
            description=(
                "Declared decode balance: decoded == mapped_successfully + rejected. "
                "Missing required inputs → ERROR (never PASS)."
            ),
            required=True,
            expected_rule="decoded == mapped_successfully + rejected",
            input_tables=(),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.DUCKDB_SEMANTIC,
            diagnostic_fields=("decoded", "mapped_successfully", "rejected"),
            threshold=0,
        ),
        QualityCheckContract(
            check_id="reconciliation.publication_decision_balance",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.RECONCILIATION,
            model_name="reconciliation_expectation",
            description=(
                "Declared publication decision balance: unique_work_ids_evaluated == "
                "inserted+updated+identical+stale+conflict+restore_required."
            ),
            required=True,
            expected_rule=(
                "unique_work_ids_evaluated == "
                "inserted+updated+identical+stale+conflict+restore_required"
            ),
            input_tables=(),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.DUCKDB_SEMANTIC,
            diagnostic_fields=("unique_work_ids_evaluated", "decision_sum"),
            threshold=0,
        ),
        QualityCheckContract(
            check_id="gold.deleted_source_work_exclusion",
            kind=QualityCheckKind.HARD_GATE,
            scope=QualityScope.STAGED_GOLD,
            model_name="staged_gold_marts",
            description=(
                "Complete staged Gold coverage required. Every contribution / "
                "citation SOURCE work_id must exist in canonical works and be ACTIVE. "
                "Citation TARGET may be DELETED or ABSENT."
            ),
            required=True,
            expected_rule=(
                "complete mart manifest AND missing_source_work_count == 0 "
                "AND inactive_source_work_count == 0"
            ),
            input_tables=(
                "works",
                "staged_gold_mart_manifest",
                "staged_gold_work_contributions",
                "staged_gold_citation_edges",
            ),
            execution_stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/active_gold_deleted_work_exclusion.sql",
            diagnostic_fields=(
                "missing_manifest_mart_count",
                "unexpected_manifest_mart_count",
                "duplicate_manifest_mart_count",
                "invalid_manifest_mart_count",
                "missing_source_work_count",
                "inactive_source_work_count",
            ),
            threshold=0,
            notes=(
                "Manifest must contain exactly one row per Step-16 Gold mart_id "
                "(zero-row marts allowed via membership). "
                "Missing required table != empty table. "
                "citation_edges in manifest requires staged_gold_citation_edges."
            ),
        ),
        # --- Informational metrics ---
        QualityCheckContract(
            check_id="metric.active_works.missing_doi",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="ACTIVE Works missing DOI (denominator = ACTIVE works).",
            required=False,
            expected_rule="informational; no publication threshold",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_missing_doi.sql",
            diagnostic_fields=("missing_count", "active_work_denominator", "missing_rate"),
            threshold=None,
        ),
        QualityCheckContract(
            check_id="metric.active_works.missing_title",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="ACTIVE Works with NULL or empty title.",
            required=False,
            expected_rule="informational; no publication threshold",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_missing_title.sql",
            diagnostic_fields=("missing_count", "active_work_denominator", "missing_rate"),
            threshold=None,
        ),
        QualityCheckContract(
            check_id="metric.active_works.without_topics",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description=(
                "ACTIVE Works with no current non-null work_topics.topic_id. "
                "Means no usable topic relationship — not source topics_presence."
            ),
            required=False,
            expected_rule="informational; no publication threshold",
            input_tables=("works", "work_topics"),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_works_without_topics.sql",
            diagnostic_fields=("missing_count", "active_work_denominator", "missing_rate"),
            threshold=None,
            notes=(
                "Canonical topics_presence (MISSING/NULL/EMPTY/PRESENT) is a source "
                "observation state; this metric counts usable relationship rows only."
            ),
        ),
        QualityCheckContract(
            check_id="metric.active_works.without_authors",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description=(
                "ACTIVE Works with no non-null author_id in work_authors. "
                "Distinct from authorships_presence source state."
            ),
            required=False,
            expected_rule="informational; no publication threshold",
            input_tables=("works", "work_authors"),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_works_without_authors.sql",
            diagnostic_fields=("missing_count", "active_work_denominator", "missing_rate"),
            threshold=None,
        ),
        QualityCheckContract(
            check_id="metric.active_works.publication_year_distribution",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="ACTIVE Work counts by publication_year (NULL year = unknown bucket).",
            required=False,
            expected_rule="informational distribution",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_publication_year_distribution.sql",
            diagnostic_fields=("bucket_count",),
            threshold=None,
        ),
        QualityCheckContract(
            check_id="metric.active_works.work_type_distribution",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="works",
            description="ACTIVE Work counts by work_type (NULL type = unknown bucket).",
            required=False,
            expected_rule="informational distribution",
            input_tables=("works",),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_work_type_distribution.sql",
            diagnostic_fields=("bucket_count",),
            threshold=None,
        ),
        QualityCheckContract(
            check_id="metric.active_works.reference_reconciliation",
            kind=QualityCheckKind.INFORMATIONAL_METRIC,
            scope=QualityScope.CANONICAL,
            model_name="work_references",
            description=(
                "Reference categories for ACTIVE source Works: TARGET_ACTIVE, "
                "TARGET_DELETED, TARGET_ABSENT, REFERENCE_MISSING, REFERENCE_MALFORMED. "
                "Category totals must equal total source-observed reference rows. "
                "DELETED/absent targets do not fail integrity gates."
            ),
            required=False,
            expected_rule="category_sum == total_reference_rows",
            input_tables=("works", "work_references"),
            execution_stage=ExecutionStage.PRE_SERVING_BUILD,
            backend_support=BackendSupport.BOTH,
            sql_path=f"{_QUALITY_SQL}/metric_reference_reconciliation.sql",
            diagnostic_fields=(
                "total_reference_rows",
                "target_active",
                "target_deleted",
                "target_absent",
                "reference_missing",
                "reference_malformed",
            ),
            threshold=None,
        ),
    )


def load_quality_registry() -> tuple[QualityCheckContract, ...]:
    checks = quality_check_contracts()
    ids = [c.check_id for c in checks]
    if len(ids) != len(set(ids)):
        raise ValueError("check_id values must be unique")
    return checks


def required_hard_gate_ids(
    checks: tuple[QualityCheckContract, ...] | None = None,
) -> tuple[str, ...]:
    items = checks if checks is not None else load_quality_registry()
    return tuple(
        c.check_id
        for c in items
        if c.kind is QualityCheckKind.HARD_GATE and c.required
    )


def checks_for_stage(
    stage: ExecutionStage,
    checks: tuple[QualityCheckContract, ...] | None = None,
) -> tuple[QualityCheckContract, ...]:
    items = checks if checks is not None else load_quality_registry()
    return tuple(c for c in items if c.execution_stage is stage)


def required_hard_gate_ids_for_stage(
    stage: ExecutionStage,
    checks: tuple[QualityCheckContract, ...] | None = None,
) -> tuple[str, ...]:
    """Required HARD_GATE IDs belonging to one execution stage only."""
    return tuple(
        c.check_id
        for c in checks_for_stage(stage, checks)
        if c.kind is QualityCheckKind.HARD_GATE and c.required
    )


def serialize_quality_registry(
    checks: tuple[QualityCheckContract, ...] | None = None,
) -> list[dict]:
    items = checks if checks is not None else load_quality_registry()
    return [c.model_dump(mode="json") for c in sorted(items, key=lambda x: x.check_id)]


def quality_sql_path(contract: QualityCheckContract, *, repo_root: Path | None = None) -> Path | None:
    if not contract.sql_path:
        return None
    root = repo_root or _REPO_ROOT
    return root / contract.sql_path
