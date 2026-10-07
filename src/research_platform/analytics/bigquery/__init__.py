"""BigQuery analytical contracts for OpenAlex (Step 15 / #22).

This package defines declarative table/query contracts and offline validation.
It does not create GCP resources, run paid BigQuery jobs, or prove BigQuery
optimizer/cost behavior.
"""

from research_platform.analytics.bigquery.contracts import (
    ActiveFilterBehavior,
    BigQueryColumn,
    BigQueryQueryContract,
    BigQueryTableContract,
    ClusteringSpec,
    IncrementalStrategy,
    PartitioningSpec,
)
from research_platform.analytics.bigquery.registry import (
    UNRESOLVED_PATTERN_IDS,
    load_bigquery_analytical_registry,
)

__all__ = [
    "ActiveFilterBehavior",
    "BigQueryColumn",
    "BigQueryQueryContract",
    "BigQueryTableContract",
    "ClusteringSpec",
    "IncrementalStrategy",
    "PartitioningSpec",
    "UNRESOLVED_PATTERN_IDS",
    "load_bigquery_analytical_registry",
]
