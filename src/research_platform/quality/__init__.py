"""Data quality gates and metrics (Step 17 / #24).

Composable HARD_GATE / INFORMATIONAL_METRIC checks for canonical integrity,
reconciliation balance, and staged Gold deleted-source exclusion.

Observes and reports only — never silently repairs data.
A check that cannot run is ERROR, never PASS.
"""

from research_platform.quality.models import (
    QUALITY_CONTRACT_VERSION,
    BackendSupport,
    ErrorClassification,
    ExecutionStage,
    QualityCheckKind,
    QualityCheckContract,
    QualityMetricPoint,
    QualityReport,
    QualityResult,
    QualityScope,
    QualityStatus,
    ReconciliationExpectation,
    ValidationLabel,
    compute_publication_allowed,
    serialize_report,
)
from research_platform.quality.registry import (
    GOLD_MART_IDS,
    checks_for_stage,
    load_quality_registry,
    required_hard_gate_ids,
    required_hard_gate_ids_for_stage,
    serialize_quality_registry,
)
from research_platform.quality.runner import (
    DuckDBQualityExecutor,
    QualityExecutor,
    execute_check,
    run_quality_checks,
)
from research_platform.quality.validation import (
    seed_quality_semantic_fixture,
    validate_static_quality_contracts,
)

__all__ = [
    "QUALITY_CONTRACT_VERSION",
    "BackendSupport",
    "DuckDBQualityExecutor",
    "ErrorClassification",
    "ExecutionStage",
    "GOLD_MART_IDS",
    "QualityCheckContract",
    "QualityCheckKind",
    "QualityExecutor",
    "QualityMetricPoint",
    "QualityReport",
    "QualityResult",
    "QualityScope",
    "QualityStatus",
    "ReconciliationExpectation",
    "ValidationLabel",
    "checks_for_stage",
    "compute_publication_allowed",
    "execute_check",
    "load_quality_registry",
    "required_hard_gate_ids",
    "required_hard_gate_ids_for_stage",
    "run_quality_checks",
    "seed_quality_semantic_fixture",
    "serialize_quality_registry",
    "serialize_report",
    "validate_static_quality_contracts",
]
