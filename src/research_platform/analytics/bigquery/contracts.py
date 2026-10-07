"""Typed BigQuery analytical table and query contracts (Step 15 / #22).

Maps Step 11 PyArrow logical schemas to BigQuery Standard SQL types. No
``google-cloud-bigquery`` dependency and no cloud execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS

# BigQuery allows at most four clustering columns.
BIGQUERY_MAX_CLUSTER_COLUMNS = 4

# Flattened physical lineage column names (nested Pydantic keeps
# CanonicalLineage.source_updated_date).
LINEAGE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "source_asset_id",
        "source_checksum_sha256",
        "lineage_source_updated_date",
        "run_id",
        "processed_at",
        "activity_state",
        "deleted_at",
    }
)

# Canonical publication_year domain (matches Work model ge/le).
PUBLICATION_YEAR_RANGE_START = 1000
PUBLICATION_YEAR_RANGE_END_EXCLUSIVE = 3001
PUBLICATION_YEAR_RANGE_INTERVAL = 1

FORBIDDEN_INVENTED_SOURCE_FIELDS: frozenset[str] = frozenset({"issn", "eissn"})

# Step 14 year-bounded patterns that must filter the works partition key.
YEAR_BOUNDED_PATTERN_IDS: frozenset[str] = frozenset(
    {
        "publication-trends",
        "open-access-trends",
        "publisher-topic-license-year",
    }
)


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


class AnalyticalMergeDecision(StrEnum):
    """Offline mirror of analytical works MERGE WHEN MATCHED outcomes."""

    IDENTICAL_NOOP = "IDENTICAL_NOOP"
    APPLY_UPDATE = "APPLY_UPDATE"
    SKIP_STALE = "SKIP_STALE"
    SKIP_RESTORE_REQUIRED = "SKIP_RESTORE_REQUIRED"
    SKIP_CONFLICT = "SKIP_CONFLICT"
    INSERT = "INSERT"


class BigQueryColumn(SettingsModel):
    name: str = Field(min_length=1, max_length=128)
    bq_type: BigQueryType
    nullable: bool
    description: str = Field(default="", max_length=512)


class PartitioningSpec(SettingsModel):
    field: str | None = None
    partition_type: Literal["DATE", "INTEGER_RANGE", "NONE"] = "NONE"
    range_start: int | None = None
    range_end: int | None = None
    range_interval: int | None = None
    rationale: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_range_fields(self) -> Self:
        if self.partition_type == "INTEGER_RANGE":
            if self.field is None:
                raise ValueError("INTEGER_RANGE partitioning requires field")
            if (
                self.range_start is None
                or self.range_end is None
                or self.range_interval is None
            ):
                raise ValueError(
                    "INTEGER_RANGE requires range_start, range_end, range_interval"
                )
            if self.range_interval <= 0:
                raise ValueError("range_interval must be positive")
            if self.range_end <= self.range_start:
                raise ValueError("range_end must be > range_start")
        elif self.partition_type == "DATE":
            if self.field is None:
                raise ValueError("DATE partitioning requires field")
        return self


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
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.table_name}: duplicate column names")
        name_set = set(names)
        for key in self.primary_logical_key:
            if key not in name_set:
                raise ValueError(f"{self.table_name}: primary key {key!r} missing")
        if self.partitioning.field is not None and self.partitioning.field not in name_set:
            raise ValueError(
                f"{self.table_name}: partition field "
                f"{self.partitioning.field!r} missing from schema"
            )
        for field in self.clustering.fields:
            if field not in name_set:
                raise ValueError(
                    f"{self.table_name}: cluster field {field!r} missing from schema"
                )
        if self.lineage_preserved and not LINEAGE_FIELD_NAMES.issubset(name_set):
            missing = sorted(LINEAGE_FIELD_NAMES - name_set)
            raise ValueError(f"{self.table_name}: missing lineage fields {missing}")
        forbidden = name_set & FORBIDDEN_INVENTED_SOURCE_FIELDS
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
    """Convert a PyArrow schema to BigQuery columns.

    Schemas must already have unique field names (Work record
    ``source_updated_date`` vs flattened ``lineage_source_updated_date``).
    """
    names = list(schema.names)
    if len(names) != len(set(names)):
        dupes = sorted({name for name in names if names.count(name) > 1})
        raise ValueError(f"PyArrow schema has duplicate field names: {dupes}")
    return tuple(
        BigQueryColumn(
            name=field.name,
            bq_type=arrow_type_to_bigquery(field.type),
            nullable=field.nullable,
        )
        for field in schema
    )


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
            field="publication_year",
            partition_type="INTEGER_RANGE",
            range_start=PUBLICATION_YEAR_RANGE_START,
            range_end=PUBLICATION_YEAR_RANGE_END_EXCLUSIVE,
            range_interval=PUBLICATION_YEAR_RANGE_INTERVAL,
            rationale=(
                "Step 14 trend/year patterns filter publication_year "
                "(@year_from/@year_to). Integer-range partitioning on that "
                "column enables partition pruning for those predicates. "
                "Canonical publication_year domain is 1000..3000; NULL years "
                "use the BigQuery NULL partition. MERGE identity uses work_id "
                "+ lineage_source_updated_date / checksum precedence, not "
                "partition replacement."
            ),
        )
    if table_name in RELATIONSHIP_TABLES:
        return PartitioningSpec(
            field="lineage_source_updated_date",
            partition_type="DATE",
            rationale=(
                "Relationship tables have no publication_year. Partitioning uses "
                "flattened lineage_source_updated_date (OpenAlex asset/updated "
                "freshness / deletion date for tombstones). This is NOT Work "
                "publication time. Primary incremental path is "
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
            "Prefer partition predicates on publication_year "
            "(@year_from/@year_to); filter activity_state = 'ACTIVE' early; "
            "avoid SELECT * in aggregates. Deployment should set "
            "maximum_bytes_billed."
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


def render_partition_clause(spec: PartitioningSpec) -> str | None:
    """Render BigQuery PARTITION BY clause body (without leading keyword)."""
    if spec.partition_type == "NONE" or spec.field is None:
        return None
    if spec.partition_type == "DATE":
        return f"`{spec.field}`"
    assert spec.range_start is not None
    assert spec.range_end is not None
    assert spec.range_interval is not None
    return (
        f"RANGE_BUCKET(`{spec.field}`, GENERATE_ARRAY("
        f"{spec.range_start}, {spec.range_end}, {spec.range_interval}))"
    )


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
    partition_expr = render_partition_clause(contract.partitioning)
    if partition_expr is not None:
        options.append(f"PARTITION BY {partition_expr}")
    if contract.clustering.fields:
        cluster_cols = ", ".join(f"`{name}`" for name in contract.clustering.fields)
        options.append(f"CLUSTER BY {cluster_cols}")
    if options:
        lines.append("\n".join(options))
    lines.append(";")
    lines.append("")
    return "\n".join(lines)


def decide_analytical_works_merge(
    *,
    target_exists: bool,
    target_checksum: str | None,
    target_lineage_date: object | None,
    target_activity_state: str | None,
    source_checksum: str,
    source_lineage_date: object | None,
    source_activity_state: str,
) -> AnalyticalMergeDecision:
    """Offline decision helper mirroring merge_works.sql WHEN MATCHED guards.

    Does not execute BigQuery. Used to lock MERGE precedence contracts in tests.
    """
    if not target_exists:
        return AnalyticalMergeDecision.INSERT
    assert target_checksum is not None
    assert target_activity_state is not None
    if source_checksum == target_checksum:
        return AnalyticalMergeDecision.IDENTICAL_NOOP
    if target_activity_state == "DELETED" and source_activity_state == "ACTIVE":
        return AnalyticalMergeDecision.SKIP_RESTORE_REQUIRED
    if target_lineage_date is not None and source_lineage_date is not None:
        if source_lineage_date < target_lineage_date:
            return AnalyticalMergeDecision.SKIP_STALE
        if source_lineage_date == target_lineage_date:
            return AnalyticalMergeDecision.SKIP_CONFLICT
        return AnalyticalMergeDecision.APPLY_UPDATE
    if source_lineage_date is not None and target_lineage_date is None:
        return AnalyticalMergeDecision.APPLY_UPDATE
    if source_lineage_date is None and target_lineage_date is not None:
        return AnalyticalMergeDecision.SKIP_STALE
    return AnalyticalMergeDecision.SKIP_CONFLICT
