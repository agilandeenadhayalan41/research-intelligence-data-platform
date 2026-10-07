"""Gold analytical mart contracts for OpenAlex (Step 16 / #55).

Defines consumer-oriented mart contracts, evidence-honest materialization
modes, and offline SEMANTIC_ONLY validation. Does not deploy BigQuery tables
or claim MEASURED production materialization.
"""

from research_platform.analytics.gold.contracts import (
    MATERIALIZATION_PROMOTION_RULE,
    EvidenceStatus,
    FanoutStrategy,
    GoldColumn,
    GoldMartContract,
    GoldRefreshImpact,
    MaterializationMode,
    RefreshStrategy,
)
from research_platform.analytics.gold.registry import (
    gold_mart_contracts,
    load_gold_registry,
    serialize_gold_registry,
)
from research_platform.analytics.gold.validation import (
    SemanticValidationReport,
    StaticValidationReport,
    validate_gold_semantics_with_duckdb,
    validate_static_gold_contracts,
)

__all__ = [
    "MATERIALIZATION_PROMOTION_RULE",
    "EvidenceStatus",
    "FanoutStrategy",
    "GoldColumn",
    "GoldMartContract",
    "GoldRefreshImpact",
    "MaterializationMode",
    "RefreshStrategy",
    "SemanticValidationReport",
    "StaticValidationReport",
    "gold_mart_contracts",
    "load_gold_registry",
    "serialize_gold_registry",
    "validate_gold_semantics_with_duckdb",
    "validate_static_gold_contracts",
]
