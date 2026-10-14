"""Staging table contracts, DDL and Parquet load checks for the publication unit (#103).

``models/work_publication_decisions.sql``, ``merge_works.sql`` and
``merge_work_topics.sql`` read ``works_staging`` and ``work_topics_staging``,
which no canonical DDL creates. This module renders their DDL into
``sql/bigquery/openalex/staging/`` (byte-identical files, like
``render_create_table_ddl`` for ``ddl/``) and checks offline that a Parquet file
loads into a table without a mode or type change.

- ``works_staging`` is ``CREATE TABLE ... LIKE `openalex.works```: BigQuery copies
  the works column schema (REQUIRED modes included), partitioning and
  clustering, so the staging load enforces the works contract.
- ``work_topics_staging`` declares the 10 ``work_topics`` columns, all
  NULLABLE, so the #103 failure injection (a NULL ``topic_id``) loads and is
  rejected later by the REQUIRED ``work_topics.topic_id`` inside the
  ``merge_work_topics.sql`` transaction.

The Parquet check mirrors BigQuery's documented Parquet conversions for the
flat types the canonical schemas use. It is offline evidence only: whether a
live load job accepts the file is first shown by the sandbox run itself.
Nothing here touches the network, GCP or BigQuery.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import pyarrow.parquet as pq
from pydantic import Field, model_validator

from research_platform.analytics.bigquery.contracts import (
    BigQueryColumn,
    BigQueryTableContract,
    BigQueryType,
    SettingsModel,
    build_table_contracts,
)

__all__ = [
    "STAGING_SQL_DIR",
    "STAGING_TARGET_TABLES",
    "ParquetLoadCheck",
    "StagingCreateMode",
    "StagingTableContract",
    "bigquery_columns_from_parquet_schema",
    "build_staging_table_contracts",
    "check_parquet_load_compatibility",
    "render_staging_ddl",
]

STAGING_SQL_DIR: Final = "sql/bigquery/openalex/staging"

# Staging table -> the canonical target it stages for publication.
STAGING_TARGET_TABLES: Final[dict[str, str]] = {
    "works_staging": "works",
    "work_topics_staging": "work_topics",
}


class StagingCreateMode(StrEnum):
    """How the staging DDL derives its columns from the target contract."""

    LIKE_TARGET = "LIKE_TARGET"
    EXPLICIT_ALL_NULLABLE = "EXPLICIT_ALL_NULLABLE"


_CREATE_MODES: Final[dict[str, StagingCreateMode]] = {
    "works_staging": StagingCreateMode.LIKE_TARGET,
    "work_topics_staging": StagingCreateMode.EXPLICIT_ALL_NULLABLE,
}

_CONSUMERS: Final[dict[str, str]] = {
    "works_staging": "models/work_publication_decisions.sql and models/merge_works.sql",
    "work_topics_staging": "models/merge_work_topics.sql",
}

_RATIONALE: Final[dict[str, tuple[str, ...]]] = {
    "works_staging": (
        "LIKE copies the `{dataset}.works` column schema (REQUIRED modes included),",
        "partitioning and clustering, so a staging load must satisfy the works",
        "contract. Must be unique on work_id before merge_works.sql (MERGE).",
    ),
    "work_topics_staging": (
        "Every column is NULLABLE on purpose: it accepts the canonical Parquet and",
        "the #103 injection Parquet (one NULL topic_id); the REQUIRED",
        "`{dataset}.work_topics` columns reject the NULL inside the",
        "merge_work_topics.sql transaction. Unpartitioned and unclustered:",
        "staging is truncated and reloaded per batch and read in full.",
    ),
}


class StagingTableContract(SettingsModel):
    """Declarative physical model for one publication staging table."""

    table_name: str = Field(min_length=1, max_length=64)
    target_table: str = Field(min_length=1, max_length=64)
    create_mode: StagingCreateMode
    columns: tuple[BigQueryColumn, ...] = Field(min_length=1)
    ddl_path: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def mode_matches_columns(self) -> StagingTableContract:
        if self.create_mode is StagingCreateMode.EXPLICIT_ALL_NULLABLE:
            required = [column.name for column in self.columns if not column.nullable]
            if required:
                raise ValueError(f"{self.table_name}: columns must be NULLABLE: {required}")
        return self


def build_staging_table_contracts(
    tables: Sequence[BigQueryTableContract] | None = None,
) -> tuple[StagingTableContract, ...]:
    """Staging contracts derived from the canonical target contracts."""
    targets = {table.table_name: table for table in (tables or build_table_contracts())}
    contracts: list[StagingTableContract] = []
    for name, target_name in STAGING_TARGET_TABLES.items():
        target = targets[target_name]
        mode = _CREATE_MODES[name]
        if mode is StagingCreateMode.LIKE_TARGET:
            columns = target.columns
        else:
            columns = tuple(
                column.model_copy(update={"nullable": True}) for column in target.columns
            )
        contracts.append(
            StagingTableContract(
                table_name=name,
                target_table=target_name,
                create_mode=mode,
                columns=columns,
                ddl_path=f"{STAGING_SQL_DIR}/{name}.sql",
            )
        )
    return tuple(contracts)


def render_staging_ddl(contract: StagingTableContract, *, dataset: str = "openalex") -> str:
    """Render BigQuery Standard SQL DDL for one staging contract."""
    lines = [
        f"-- BigQuery Standard SQL DDL for `{dataset}.{contract.table_name}`",
        "-- #103 publication staging. Executed only by manual scripts on synthetic",
        "-- data (GCP_SANDBOX, personal DEV/QA); NOT DEPLOYED.",
        f"-- Read by {_CONSUMERS[contract.table_name]}.",
        "-- Rendered by research_platform.analytics.bigquery.staging.render_staging_ddl.",
        *(f"-- {line.format(dataset=dataset)}" for line in _RATIONALE[contract.table_name]),
        "",
    ]
    if contract.create_mode is StagingCreateMode.LIKE_TARGET:
        lines.append(
            f"CREATE TABLE IF NOT EXISTS `{dataset}.{contract.table_name}` "
            f"LIKE `{dataset}.{contract.target_table}`"
        )
    else:
        lines.append(f"CREATE TABLE IF NOT EXISTS `{dataset}.{contract.table_name}` (")
        lines.append(
            ",\n".join(f"  `{column.name}` {column.bq_type.value}" for column in contract.columns)
        )
        lines.append(")")
    lines.append(";")
    lines.append("")
    return "\n".join(lines)


def _bigquery_type_for_parquet(column: pq.ColumnSchema) -> BigQueryType:
    physical = column.physical_type
    logical = column.logical_type.type
    if physical == "BYTE_ARRAY" and logical == "STRING":
        return BigQueryType.STRING
    if physical == "INT32" and logical == "DATE":
        return BigQueryType.DATE
    if physical == "INT64" and logical == "TIMESTAMP":
        spec = json.loads(column.logical_type.to_json())
        # isAdjustedToUTC=false loads as DATETIME; other units are not canonical.
        if spec.get("isAdjustedToUTC") is not True or spec.get("timeUnit") != "microseconds":
            raise ValueError(f"{column.name}: timestamp must be UTC-adjusted microseconds")
        return BigQueryType.TIMESTAMP
    if physical in {"INT32", "INT64"} and logical in {"NONE", "INT"}:
        return BigQueryType.INT64
    if physical == "DOUBLE" and logical == "NONE":
        return BigQueryType.FLOAT64
    if physical == "BOOLEAN" and logical == "NONE":
        return BigQueryType.BOOL
    raise ValueError(f"{column.name}: unmapped Parquet column {physical}/{logical}")


def bigquery_columns_from_parquet_schema(
    schema: pq.ParquetSchema,
) -> tuple[BigQueryColumn, ...]:
    """Columns (name, type, mode) a BigQuery Parquet load derives from a flat file.

    A Parquet ``required`` column (definition level 0) is a REQUIRED column; an
    ``optional`` one is NULLABLE. Nested or repeated columns are rejected.
    """
    columns: list[BigQueryColumn] = []
    for index in range(len(schema)):
        column = schema.column(index)
        if column.max_repetition_level != 0 or column.max_definition_level not in {0, 1}:
            raise ValueError(f"{column.name}: nested or repeated Parquet columns are not supported")
        columns.append(
            BigQueryColumn(
                name=column.name,
                bq_type=_bigquery_type_for_parquet(column),
                nullable=column.max_definition_level == 1,
            )
        )
    return tuple(columns)


@dataclass(frozen=True)
class ParquetLoadCheck:
    """Offline result of comparing one Parquet file with a destination table."""

    table_name: str
    errors: tuple[str, ...]
    # File REQUIRED into a NULLABLE column: no destination change is needed.
    relaxed_columns: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def exact(self) -> bool:
        return self.ok and not self.relaxed_columns


def check_parquet_load_compatibility(
    table_name: str,
    destination: Sequence[BigQueryColumn],
    parquet_schema: pq.ParquetSchema,
) -> ParquetLoadCheck:
    """Compare a Parquet schema with destination columns for a WRITE_APPEND load.

    Names must match in order and types must be identical. A NULLABLE file
    column into a REQUIRED destination column is an error: BigQuery would have
    to relax the table (``changed mode from REQUIRED to NULLABLE``). A REQUIRED
    file column into a NULLABLE destination column is reported as relaxed.
    """
    errors: list[str] = []
    relaxed: list[str] = []
    try:
        source = bigquery_columns_from_parquet_schema(parquet_schema)
    except ValueError as exc:
        return ParquetLoadCheck(table_name=table_name, errors=(str(exc),), relaxed_columns=())
    source_names = [column.name for column in source]
    destination_names = [column.name for column in destination]
    if source_names != destination_names:
        missing = sorted(set(destination_names) - set(source_names))
        extra = sorted(set(source_names) - set(destination_names))
        errors.append(
            f"{table_name}: Parquet columns differ from the table "
            f"(missing={missing}, extra={extra}, same_order={not missing and not extra})"
        )
    by_name = {column.name: column for column in destination}
    for column in source:
        target = by_name.get(column.name)
        if target is None:
            continue
        if column.bq_type is not target.bq_type:
            errors.append(
                f"{table_name}.{column.name}: Parquet loads as {column.bq_type.value}, "
                f"table column is {target.bq_type.value}"
            )
        if column.nullable and not target.nullable:
            errors.append(
                f"{table_name}.{column.name}: NULLABLE Parquet column into REQUIRED table column"
            )
        elif not column.nullable and target.nullable:
            relaxed.append(column.name)
    return ParquetLoadCheck(
        table_name=table_name,
        errors=tuple(errors),
        relaxed_columns=tuple(relaxed),
    )
