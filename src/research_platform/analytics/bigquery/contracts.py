"""Typed BigQuery analytical table and query contracts (Step 15 / #22).

Maps Step 11 PyArrow logical schemas to BigQuery Standard SQL types. No
``google-cloud-bigquery`` dependency and no cloud execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS

# BigQuery allows at most four clustering columns.
BIGQUERY_MAX_CLUSTER_COLUMNS = 4

LINEAGE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "source_asset_id",
        "source_checksum_sha256",
        "source_updated_date",
        "run_id",
        "processed_at",
        "activity_state",
        "deleted_at",
    }
)

FORBIDDEN_INVENTED_SOURCE_FIELDS: frozenset[str] = frozenset({"issn", "eissn"})


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class BigQueryType(StrEnum):
    STRING = "STRING"
    INT64 = "INT64"
    FLOAT64 = "FLOAT64"
    BOOL = "BOOL"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"


class IncrementalStrategy(StrEnum):
    MERGE_BY_PRIMARY_KEY = "MERGE_BY_PRIMARY_KEY"
    REPLACE_BY_WORK_ID = "REPLACE_BY_WORK_ID"
    MERGE_BY_RELATIONSHIP_KEY = "MERGE_BY_RELATIONSHIP_KEY"
    FULL_REFRESH_SMALL_DIM = "FULL_REFRESH_SMALL_DIM"


class ActiveFilterBehavior(StrEnum):
    """How consumer queries should treat activity_state on this table."""

    RETAIN_ALL_REQUIRE_CONSUMER_FILTER = "RETAIN_ALL_REQUIRE_CONSUMER_FILTER"
    DIMENSION_NO_WORK_FILTER = "DIMENSION_NO_WORK_FILTER"
    RELATIONSHIP_JOIN_ACTIVE_WORKS = "RELATIONSHIP_JOIN_ACTIVE_WORKS"


class ValidationLabel(StrEnum):
    """What offline validation proves."""

    BIGQUERY_SQL_CONTRACT = "BIGQUERY_SQL_CONTRACT"
    SEMANTIC_ONLY = "SEMANTIC_ONLY"
    NOT_YET_DEPLOYED = "NOT_YET_DEPLOYED"


class BigQueryColumn(SettingsModel):
    name: str = Field(min_length=1, max_length=128)
    bq_type: BigQueryType
    nullable: bool
    description: str = Field(default="", max_length=512)


class PartitioningSpec(SettingsModel):
    field: str | None = None
    partition_type: Literal["DATE", "NONE"] = "NONE"
    rationale: str = Field(min_length=1, max_length=1024)


class ClusteringSpec(SettingsModel):
    fields: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def within_bigquery_limit(self) -> ClusteringSpec:
        if len(self.fields) > BIGQUERY_MAX_CLUSTER_COLUMNS:
            raise ValueError(
                f"BigQuery clustering allows at most {BIGQUERY_MAX_CLUSTER_COLUMNS} "
                f"columns; got {len(self.fields)}"
            )
        return self


class BigQueryTableContract(SettingsModel):
    """Declarative physical model for one OpenAlex analytical table."""

    table_name: str = Field(min_length=1, max_length=64)
    logical_source: str = Field(min_length=1, max_length=64)
    grain: str = Field(min_length=1, max_length=256)
    columns: tuple[BigQueryColumn, ...] = Field(min_length=1)
    partitioning: PartitioningSpec
    clustering: ClusteringSpec
    primary_logical_key: tuple[str, ...] = Field(min_length=1)
    incremental_strategy: IncrementalStrategy
    active_filter_behavior: ActiveFilterBehavior
    lineage_preserved: bool = True
    cost_safety_notes: str = Field(min_length=1, max_length=1024)
    ddl_path: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def columns_cover_keys_and_lineage(self) -> BigQueryTableContract:
        names = {column.name for column in self.columns}
        for key in self.primary_logical_key:
            if key not in names:
                raise ValueError(f"{self.table_name}: primary key {key!r} missing")
        if self.partitioning.field is not None and self.partitioning.field not in names:
            raise ValueError(
                f"{self.table_name}: partition field "
                f"{self.partitioning.field!r} missing from schema"
            )
        for field in self.clustering.fields:
            if field not in names:
                raise ValueError(
                    f"{self.table_name}: cluster field {field!r} missing from schema"
                )
        if self.lineage_preserved and not LINEAGE_FIELD_NAMES.issubset(names):
            missing = sorted(LINEAGE_FIELD_NAMES - names)
            raise ValueError(f"{self.table_name}: missing lineage fields {missing}")
        forbidden = names & FORBIDDEN_INVENTED_SOURCE_FIELDS
        if forbidden:
            raise ValueError(
                f"{self.table_name}: invented forbidden fields {sorted(forbidden)}"
            )
        return self


class BigQueryQueryContract(SettingsModel):
    """Maps one Step 14 pattern_id to a BigQuery Standard SQL file."""

    pattern_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]{1,63}$")
    sql_path: str | None = Field(default=None, max_length=256)
    input_tables: tuple[str, ...] = ()
    result_grain: str = Field(min_length=1, max_length=512)
    parameters: tuple[str, ...] = ()
    active_filtering_required: bool
    expected_boundedness: str = Field(min_length=1, max_length=512)
    unresolved_dependencies: tuple[str, ...] = ()
    implementation_status: Literal["IMPLEMENTED", "UNRESOLVED"] = "IMPLEMENTED"
    notes: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def unresolved_has_no_sql(self) -> BigQueryQueryContract:
        if self.implementation_status == "UNRESOLVED":
            if self.sql_path is not None:
                raise ValueError("UNRESOLVED query contracts must not set sql_path")
            if not self.unresolved_dependencies:
                raise ValueError("UNRESOLVED query contracts require dependencies")
        elif self.sql_path is None:
            raise ValueError("IMPLEMENTED query contracts require sql_path")
        return self


def arrow_type_to_bigquery(dtype: pa.DataType) -> BigQueryType:
    """Map Step 11 PyArrow logical types to BigQuery Standard SQL types."""
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return BigQueryType.STRING
    if pa.types.is_int32(dtype) or pa.types.is_int64(dtype) or pa.types.is_integer(dtype):
        return BigQueryType.INT64
    if pa.types.is_float64(dtype) or pa.types.is_floating(dtype):
        return BigQueryType.FLOAT64
    if pa.types.is_boolean(dtype):
        return BigQueryType.BOOL
    if pa.types.is_date(dtype):
        return BigQueryType.DATE
    if pa.types.is_timestamp(dtype):
        return BigQueryType.TIMESTAMP
    raise TypeError(f"unsupported PyArrow type for BigQuery mapping: {dtype}")


def columns_from_arrow_schema(schema: pa.Schema) -> tuple[BigQueryColumn, ...]:
    """Convert a PyArrow schema to BigQuery columns, deduplicating names.

    ``works`` currently lists ``source_updated_date`` in both entity and lineage
    field lists in the PyArrow schema. BigQuery DDL keeps a single column.
    """
    seen: dict[str, BigQueryColumn] = {}
    order: list[str] = []
    for field in schema:
        column = BigQueryColumn(
            name=field.name,
            bq_type=arrow_type_to_bigquery(field.type),
            nullable=field.nullable,
        )
        if field.name in seen:
            existing = seen[field.name]
            if existing.bq_type != column.bq_type:
                raise ValueError(f"conflicting types for column {field.name}")
            # Prefer non-nullability if either side requires NOT NULL.
            if existing.nullable and not column.nullable:
                seen[field.name] = column
            continue
        seen[field.name] = column
        order.append(field.name)
    return tuple(seen[name] for name in order)


# Exact grains for every analytical table (ordinal keys retained).
TABLE_GRAINS: dict[str, str] = {
    "works": "one row per work_id",
    "authors": "one row per author_id",
    "institutions": "one row per institution_id",
    "sources": "one row per source_id",
    "publishers": "one row per publisher_id",
    "topics": "one row per topic_id",
    "funders": "one row per funder_id",
    "work_authors": "one row per (work_id, authorship_index)",
    "work_author_institutions": (
        "one row per (work_id, authorship_index, institution_index)"
    ),
    "work_topics": "one row per (work_id, topic_id)",
    "work_keywords": "one row per (work_id, keyword_id)",
    "work_references": "one row per (work_id, reference_index)",
    "work_mesh": "one row per (work_id, mesh_index)",
    "work_locations": "one row per (work_id, location_index)",
    "work_grants": "one row per (work_id, grant_index)",
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "works": ("work_id",),
    "authors": ("author_id",),
    "institutions": ("institution_id",),
    "sources": ("source_id",),
    "publishers": ("publisher_id",),
    "topics": ("topic_id",),
    "funders": ("funder_id",),
    "work_authors": ("work_id", "authorship_index"),
    "work_author_institutions": ("work_id", "authorship_index", "institution_index"),
    "work_topics": ("work_id", "topic_id"),
    "work_keywords": ("work_id", "keyword_id"),
    "work_references": ("work_id", "reference_index"),
    "work_mesh": ("work_id", "mesh_index"),
    "work_locations": ("work_id", "location_index"),
    "work_grants": ("work_id", "grant_index"),
}

DIMENSION_TABLES: frozenset[str] = frozenset(
    {"authors", "institutions", "sources", "publishers", "topics", "funders"}
)

RELATIONSHIP_TABLES: frozenset[str] = frozenset(
    {
        "work_authors",
        "work_author_institutions",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_mesh",
        "work_locations",
        "work_grants",
    }
)


def _partitioning_for(table_name: str) -> PartitioningSpec:
    if table_name == "works":
        return PartitioningSpec(
            field="publication_date",
            partition_type="DATE",
            rationale=(
                "Work-centric analytical scans (publication trends, OA trends, "
                "year-bounded publisher metrics) prune on publication_date. "
                "NULL publication_date rows land in the NULL partition. "
                "Incremental MERGE identity uses work_id + source_updated_date "
                "from the control plane, not partition replacement alone."
            ),
        )
    if table_name in RELATIONSHIP_TABLES:
        return PartitioningSpec(
            field="source_updated_date",
            partition_type="DATE",
            rationale=(
                "Relationship tables have no publication_date. Partitioning uses "
                "lineage source_updated_date (OpenAlex asset/updated freshness), "
                "supporting prune of recently refreshed relationship batches. "
                "This is NOT Work publication time. Primary incremental path is "
                "REPLACE_BY_WORK_ID for the changed work_id set."
            ),
        )
    return PartitioningSpec(
        field=None,
        partition_type="NONE",
        rationale=(
            "Small dimension tables are not partitioned; full refresh or "
            "MERGE_BY_PRIMARY_KEY is cheap relative to Work-centric facts."
        ),
    )


def _clustering_for(table_name: str) -> ClusteringSpec:
    specs: dict[str, ClusteringSpec] = {
        "works": ClusteringSpec(
            fields=("work_id", "doi", "primary_publisher_id", "primary_source_id"),
            rationale=(
                "Step 14 lookups (work_id, DOI) and publisher/journal analytics "
                "filter these columns. Four-column BigQuery limit fully used."
            ),
        ),
        "work_topics": ClusteringSpec(
            fields=("work_id", "topic_id"),
            rationale="Publisher/topic and institution/topic patterns filter both keys.",
        ),
        "work_authors": ClusteringSpec(
            fields=("work_id", "author_id"),
            rationale="Journal/publisher unique-author aggregates key on these columns.",
        ),
        "work_author_institutions": ClusteringSpec(
            fields=("work_id", "institution_id"),
            rationale="Institution×topic patterns filter institution_id after work scope.",
        ),
        "work_locations": ClusteringSpec(
            fields=("work_id", "source_id"),
            rationale=(
                "Journal linkage and primary-location license metrics filter "
                "work_id / source_id (is_primary is a selective predicate, not clustered)."
            ),
        ),
        "work_references": ClusteringSpec(
            fields=("work_id", "referenced_work_id"),
            rationale="Citation listings filter source work_id; targets are optional.",
        ),
        "work_keywords": ClusteringSpec(
            fields=("work_id", "keyword_id"),
            rationale="Keyword relationship scans are work-scoped.",
        ),
        "work_mesh": ClusteringSpec(
            fields=("work_id", "descriptor_ui"),
            rationale="MeSH rows are work-scoped; descriptor_ui is the semantic id.",
        ),
        "work_grants": ClusteringSpec(
            fields=("work_id", "funder_id"),
            rationale="Grant rows are work-scoped with optional funder dimension.",
        ),
        "authors": ClusteringSpec(
            fields=("author_id",),
            rationale="Dimension point lookup by primary key.",
        ),
        "institutions": ClusteringSpec(
            fields=("institution_id",),
            rationale="Dimension point lookup by primary key.",
        ),
        "sources": ClusteringSpec(
            fields=("source_id",),
            rationale=(
                "Dimension point lookup by source_id. No issn/eissn columns exist; "
                "ISSN lookup remains UNRESOLVED (Step 14)."
            ),
        ),
        "publishers": ClusteringSpec(
            fields=("publisher_id",),
            rationale="Publisher lookup pattern filters publisher_id.",
        ),
        "topics": ClusteringSpec(
            fields=("topic_id",),
            rationale="Dimension point lookup by primary key.",
        ),
        "funders": ClusteringSpec(
            fields=("funder_id",),
            rationale="Dimension point lookup by primary key.",
        ),
    }
    return specs[table_name]


def _incremental_for(table_name: str) -> IncrementalStrategy:
    if table_name == "works":
        return IncrementalStrategy.MERGE_BY_PRIMARY_KEY
    if table_name in DIMENSION_TABLES:
        return IncrementalStrategy.MERGE_BY_PRIMARY_KEY
    return IncrementalStrategy.REPLACE_BY_WORK_ID


def _active_behavior(table_name: str) -> ActiveFilterBehavior:
    if table_name == "works":
        return ActiveFilterBehavior.RETAIN_ALL_REQUIRE_CONSUMER_FILTER
    if table_name in DIMENSION_TABLES:
        return ActiveFilterBehavior.DIMENSION_NO_WORK_FILTER
    return ActiveFilterBehavior.RELATIONSHIP_JOIN_ACTIVE_WORKS


def _cost_notes(table_name: str) -> str:
    if table_name == "works":
        return (
            "Prefer partition predicates on publication_date / publication_year "
            "bounds; filter activity_state = 'ACTIVE' early; avoid SELECT * in "
            "aggregates. Deployment should set maximum_bytes_billed."
        )
    if table_name in RELATIONSHIP_TABLES:
        return (
            "Scope by work_id sets or join ACTIVE works early; never cross-join "
            "multiple relationship tables at raw grain without pre-aggregation."
        )
    return "Small dimensions; point lookups by primary key; no SELECT * requirement."


def build_table_contracts() -> tuple[BigQueryTableContract, ...]:
    """Build declarative BigQuery contracts for all canonical OpenAlex tables."""
    contracts: list[BigQueryTableContract] = []
    for table_name, schema in CANONICAL_SCHEMAS.items():
        contracts.append(
            BigQueryTableContract(
                table_name=table_name,
                logical_source=f"canonical.openalex.{table_name}",
                grain=TABLE_GRAINS[table_name],
                columns=columns_from_arrow_schema(schema),
                partitioning=_partitioning_for(table_name),
                clustering=_clustering_for(table_name),
                primary_logical_key=PRIMARY_KEYS[table_name],
                incremental_strategy=_incremental_for(table_name),
                active_filter_behavior=_active_behavior(table_name),
                lineage_preserved=True,
                cost_safety_notes=_cost_notes(table_name),
                ddl_path=f"sql/bigquery/openalex/ddl/{table_name}.sql",
            )
        )
    return tuple(contracts)


def render_create_table_ddl(contract: BigQueryTableContract, *, dataset: str = "openalex") -> str:
    """Render BigQuery Standard SQL CREATE TABLE DDL for a contract."""
    lines = [
        f"-- BigQuery Standard SQL DDL for `{dataset}.{contract.table_name}`",
        "-- Step 15 / #22 — contract only; NOT DEPLOYED / NOT EXECUTED here.",
        f"-- Grain: {contract.grain}",
        f"-- Incremental: {contract.incremental_strategy.value}",
        "",
        f"CREATE TABLE IF NOT EXISTS `{dataset}.{contract.table_name}` (",
    ]
    col_sql: list[str] = []
    for column in contract.columns:
        null_sql = "" if column.nullable else " NOT NULL"
        col_sql.append(f"  `{column.name}` {column.bq_type.value}{null_sql}")
    lines.append(",\n".join(col_sql))
    lines.append(")")
    options: list[str] = []
    if (
        contract.partitioning.partition_type == "DATE"
        and contract.partitioning.field is not None
    ):
        options.append(f"PARTITION BY `{contract.partitioning.field}`")
    if contract.clustering.fields:
        cluster_cols = ", ".join(f"`{name}`" for name in contract.clustering.fields)
        options.append(f"CLUSTER BY {cluster_cols}")
    if options:
        lines.append("\n".join(options))
    lines.append(";")
    lines.append("")
    return "\n".join(lines)
