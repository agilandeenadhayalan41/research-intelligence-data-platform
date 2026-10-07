"""BigQuery analytical registry: tables + Step 14 pattern → SQL mappings."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_platform.analytics.bigquery.contracts import (
    BigQueryQueryContract,
    BigQueryTableContract,
    build_table_contracts,
)
from research_platform.benchmarks.registry import load_query_pattern_registry

_REPO_ROOT = Path(__file__).resolve().parents[4]
SQL_ROOT = _REPO_ROOT / "sql" / "bigquery" / "openalex"

# Step 14 patterns that must remain without BigQuery SQL in Step 15.
UNRESOLVED_PATTERN_IDS: frozenset[str] = frozenset({"issn-source-lookup"})

# Logical deployment-time safety default (not enforced without BigQuery jobs).
DEFAULT_MAXIMUM_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GiB


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class BigQueryAnalyticalRegistry(SettingsModel):
    """Repository-local registry of BigQuery analytical contracts."""

    version: str = Field(min_length=1, max_length=32)
    dataset: str = "openalex"
    tables: tuple[BigQueryTableContract, ...] = Field(min_length=1)
    queries: tuple[BigQueryQueryContract, ...] = Field(min_length=1)
    active_works_view_path: str = "sql/bigquery/openalex/models/active_works.sql"
    merge_contracts: tuple[str, ...] = (
        "sql/bigquery/openalex/models/classify_works_staging.sql",
        "sql/bigquery/openalex/models/accepted_work_ids.sql",
        "sql/bigquery/openalex/models/merge_works.sql",
        "sql/bigquery/openalex/models/merge_works_preconditions.sql",
        "sql/bigquery/openalex/models/merge_work_topics.sql",
    )
    maximum_bytes_billed: int = Field(default=DEFAULT_MAXIMUM_BYTES_BILLED, gt=0)
    validation_scope_note: str = Field(
        default=(
            "DEFINED IN STEP 15: SQL/schema contracts, grains, partition/cluster "
            "choices, MERGE strategy, cost rules, DuckDB SEMANTIC_ONLY fixtures. "
            "NOT YET DEPLOYED / MEASURED: real BigQuery jobs, pruning, cost, "
            "latency, clustering effectiveness."
        ),
        max_length=2048,
    )

    @model_validator(mode="after")
    def unique_table_and_pattern_ids(self) -> BigQueryAnalyticalRegistry:
        table_names = [table.table_name for table in self.tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("table_name values must be unique")
        pattern_ids = [query.pattern_id for query in self.queries]
        if len(pattern_ids) != len(set(pattern_ids)):
            raise ValueError("pattern_id values must be unique")
        return self


def _query_contracts() -> tuple[BigQueryQueryContract, ...]:
    """Declare one contract per Step 14 pattern_id (no silent omissions)."""
    return (
        BigQueryQueryContract(
            pattern_id="doi-work-lookup",
            sql_path="sql/bigquery/openalex/queries/doi_work_lookup.sql",
            input_tables=("works",),
            result_grain="zero or one ACTIVE Work row",
            parameters=("doi",),
            active_filtering_required=True,
            expected_boundedness="point lookup by DOI; LIMIT 1",
        ),
        BigQueryQueryContract(
            pattern_id="openalex-work-id-lookup",
            sql_path="sql/bigquery/openalex/queries/openalex_work_id_lookup.sql",
            input_tables=("works",),
            result_grain="zero or one ACTIVE Work row",
            parameters=("work_id",),
            active_filtering_required=True,
            expected_boundedness="point lookup by work_id; LIMIT 1",
        ),
        BigQueryQueryContract(
            pattern_id="issn-source-lookup",
            sql_path=None,
            input_tables=("sources",),
            result_grain="UNRESOLVED — no canonical ISSN/eISSN mapping",
            parameters=("issn",),
            active_filtering_required=False,
            expected_boundedness="n/a",
            unresolved_dependencies=(
                "canonical Source exposes only issn_l; ISSN/eISSN identity "
                "mapping does not exist; do not invent sources.issn/eissn",
            ),
            implementation_status="UNRESOLVED",
            notes=(
                "Preserved from Step 14. Step 15 must not emit fake BigQuery SQL "
                "against nonexistent source fields."
            ),
        ),
        BigQueryQueryContract(
            pattern_id="publisher-lookup",
            sql_path="sql/bigquery/openalex/queries/publisher_lookup.sql",
            input_tables=("publishers",),
            result_grain="zero or one Publisher row",
            parameters=("publisher_id",),
            active_filtering_required=False,
            expected_boundedness="dimension point lookup; LIMIT 1",
        ),
        BigQueryQueryContract(
            pattern_id="unique-authors-per-journal",
            sql_path="sql/bigquery/openalex/queries/unique_authors_per_journal.sql",
            input_tables=("works", "work_locations", "work_authors"),
            result_grain="one row per source_id with unique_author_count",
            parameters=("source_id",),
            active_filtering_required=True,
            expected_boundedness="scoped to one source_id; authors aggregated independently",
        ),
        BigQueryQueryContract(
            pattern_id="unique-authors-per-publisher",
            sql_path="sql/bigquery/openalex/queries/unique_authors_per_publisher.sql",
            input_tables=("works", "work_authors"),
            result_grain="one row per publisher_id with unique_author_count",
            parameters=("publisher_id",),
            active_filtering_required=True,
            expected_boundedness="scoped to one publisher_id; authors aggregated independently",
        ),
        BigQueryQueryContract(
            pattern_id="publisher-topic-counts",
            sql_path="sql/bigquery/openalex/queries/publisher_topic_counts.sql",
            input_tables=("works", "work_topics"),
            result_grain="one row per publisher_id + topic_id",
            parameters=("publisher_id",),
            active_filtering_required=True,
            expected_boundedness="scoped to one publisher_id; works ⋈ topics only",
        ),
        BigQueryQueryContract(
            pattern_id="publisher-topic-license-year",
            sql_path=(
                "sql/bigquery/openalex/queries/publisher_topic_license_year.sql"
            ),
            input_tables=("works", "work_topics", "work_locations"),
            result_grain=(
                "one row per publisher_id + topic_id + primary_location_license "
                "+ publication_year"
            ),
            parameters=("publisher_id", "year_from", "year_to"),
            active_filtering_required=True,
            expected_boundedness=(
                "optional publisher + year bounds; primary location only for license"
            ),
        ),
        BigQueryQueryContract(
            pattern_id="institution-topic-relationships",
            sql_path=(
                "sql/bigquery/openalex/queries/institution_topic_relationships.sql"
            ),
            input_tables=("works", "work_author_institutions", "work_topics"),
            result_grain="one row per institution_id + topic_id",
            parameters=("institution_id",),
            active_filtering_required=True,
            expected_boundedness=(
                "scoped to one institution_id; DISTINCT works before topic join"
            ),
        ),
        BigQueryQueryContract(
            pattern_id="citation-relationships",
            sql_path="sql/bigquery/openalex/queries/citation_relationships.sql",
            input_tables=("works", "work_references"),
            result_grain="one row per (source_work_id, reference_index)",
            parameters=("work_id",),
            active_filtering_required=True,
            expected_boundedness=(
                "scoped to one source work_id; LEFT JOIN target works"
            ),
        ),
        BigQueryQueryContract(
            pattern_id="publication-trends",
            sql_path="sql/bigquery/openalex/queries/publication_trends.sql",
            input_tables=("works",),
            result_grain="one row per publication_year",
            parameters=("year_from", "year_to"),
            active_filtering_required=True,
            expected_boundedness=(
                "prefer year_from/year_to partition-friendly bounds; ACTIVE only"
            ),
        ),
        BigQueryQueryContract(
            pattern_id="open-access-trends",
            sql_path="sql/bigquery/openalex/queries/open_access_trends.sql",
            input_tables=("works",),
            result_grain="one row per publication_year + oa_status",
            parameters=("year_from", "year_to"),
            active_filtering_required=True,
            expected_boundedness=(
                "prefer year_from/year_to bounds; ACTIVE only; explicit columns"
            ),
        ),
    )


def load_bigquery_analytical_registry() -> BigQueryAnalyticalRegistry:
    """Build and validate the BigQuery analytical registry against Step 14."""
    step14 = load_query_pattern_registry()
    queries = _query_contracts()
    mapped = {query.pattern_id for query in queries}
    required = {pattern.pattern_id for pattern in step14.patterns}
    missing = required - mapped
    if missing:
        raise ValueError(f"BigQuery registry missing Step 14 patterns: {sorted(missing)}")
    extra = mapped - required
    if extra:
        raise ValueError(f"BigQuery registry has unknown pattern_ids: {sorted(extra)}")
    for pattern in step14.patterns:
        if pattern.pattern_id in UNRESOLVED_PATTERN_IDS:
            contract = next(q for q in queries if q.pattern_id == pattern.pattern_id)
            if contract.implementation_status != "UNRESOLVED":
                raise ValueError(
                    f"{pattern.pattern_id} must remain UNRESOLVED in Step 15"
                )
    return BigQueryAnalyticalRegistry(
        version="1.0.0",
        tables=build_table_contracts(),
        queries=queries,
    )


def repo_path(relative: str) -> Path:
    """Resolve a repo-relative path."""
    return _REPO_ROOT / relative


def sql_root() -> Path:
    return SQL_ROOT
