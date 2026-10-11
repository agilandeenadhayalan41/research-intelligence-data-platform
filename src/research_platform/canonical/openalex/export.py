"""Canonical snapshot → PyArrow → Parquet export (#103 sandbox load path).

Writes each of the 15 ``CanonicalSnapshot`` tables as one Parquet file whose
schema is exactly ``CANONICAL_SCHEMAS[name]``: the same column names, order,
Arrow types and nullability from which the Step 15 ``BigQueryTableContract``
columns are derived. Non-nullable fields are written as Parquet ``REQUIRED``
columns, so a BigQuery Parquet load sees the contract's ``REQUIRED`` modes.

Rows are flattened exactly like the PostgreSQL canonical store:
``model_dump(mode="python", exclude={"lineage"})`` merged with
``row_codec.lineage_columns``. Enums are written as ``.value`` and ``run_id``
as its canonical UUID string (BigQuery ``run_id`` is ``STRING``). Row order is
the snapshot order (``InMemoryCanonicalStore.snapshot`` sorts by grain key).

Every table is validated before anything is written: no unknown or missing
columns, schema equality, and no nulls in non-nullable columns. Parquet writer
options are pinned, so identical input yields identical bytes under the same
PyArrow version.

Exports are derived data. Write them to a scratch directory or the git-ignored
``data/sample/``, never under immutable raw-landing keys. No network, cloud or
BigQuery calls are made here.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Final, Iterable, Mapping
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, field_validator, model_validator

from research_platform.canonical.memory import CanonicalSnapshot
from research_platform.canonical.openalex.errors import CanonicalExportError
from research_platform.canonical.openalex.models import (
    Author,
    Funder,
    Institution,
    Publisher,
    SettingsModel,
    Source,
    Topic,
    Work,
    WorkAuthor,
    WorkAuthorInstitution,
    WorkGrant,
    WorkKeyword,
    WorkLocation,
    WorkMesh,
    WorkReference,
    WorkTopic,
)
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS
from research_platform.persistence.postgres.row_codec import lineage_columns

__all__ = [
    "CANONICAL_TABLE_MODELS",
    "CANONICAL_TABLE_NAMES",
    "CanonicalEntity",
    "CanonicalExportError",
    "CanonicalParquetExport",
    "CanonicalParquetFile",
    "PARQUET_COMPRESSION",
    "PARQUET_FORMAT_VERSION",
    "canonical_parquet_path",
    "canonical_row",
    "canonical_table_to_arrow",
    "export_snapshot_to_parquet",
    "read_canonical_parquet",
    "snapshot_to_arrow",
    "validate_canonical_arrow_table",
    "write_canonical_parquet",
]

CanonicalEntity = (
    Work
    | Author
    | Institution
    | Source
    | Publisher
    | Topic
    | Funder
    | WorkAuthor
    | WorkAuthorInstitution
    | WorkTopic
    | WorkKeyword
    | WorkReference
    | WorkMesh
    | WorkLocation
    | WorkGrant
)

# Interchange table name -> canonical model; order matches CANONICAL_SCHEMAS
# and the CanonicalSnapshot fields.
CANONICAL_TABLE_MODELS: Final[Mapping[str, type[CanonicalEntity]]] = {
    "works": Work,
    "authors": Author,
    "institutions": Institution,
    "sources": Source,
    "publishers": Publisher,
    "topics": Topic,
    "funders": Funder,
    "work_authors": WorkAuthor,
    "work_author_institutions": WorkAuthorInstitution,
    "work_topics": WorkTopic,
    "work_keywords": WorkKeyword,
    "work_references": WorkReference,
    "work_mesh": WorkMesh,
    "work_locations": WorkLocation,
    "work_grants": WorkGrant,
}

CANONICAL_TABLE_NAMES: Final[tuple[str, ...]] = tuple(CANONICAL_SCHEMAS)

PARQUET_COMPRESSION: Final = "snappy"
# 2.6 writes DATE and TIMESTAMP(MICROS, isAdjustedToUTC=true) logical types,
# which BigQuery loads as DATE and TIMESTAMP.
PARQUET_FORMAT_VERSION: Final = "2.6"

_PARQUET_WRITE_OPTIONS: Final[Mapping[str, Any]] = {
    "compression": PARQUET_COMPRESSION,
    "version": PARQUET_FORMAT_VERSION,
    "use_dictionary": True,
    "write_statistics": True,
    "data_page_version": "1.0",
    "coerce_timestamps": "us",
    "allow_truncated_timestamps": False,
    "store_schema": True,
}

_HASH_CHUNK_BYTES = 1024 * 1024


class CanonicalParquetFile(SettingsModel):
    """One written canonical Parquet file and its content identity."""

    table_name: str = Field(min_length=1, max_length=64)
    path: Path
    row_count: int = Field(strict=True, ge=0)
    byte_size: int = Field(strict=True, gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("table_name")
    @classmethod
    def known_table(cls, value: str) -> str:
        if value not in CANONICAL_SCHEMAS:
            raise ValueError(f"unknown canonical table {value!r}")
        return value


class CanonicalParquetExport(SettingsModel):
    """All 15 canonical tables written to one output directory."""

    output_dir: Path
    files: tuple[CanonicalParquetFile, ...]

    @model_validator(mode="after")
    def covers_every_table_once(self) -> CanonicalParquetExport:
        names = tuple(item.table_name for item in self.files)
        if names != CANONICAL_TABLE_NAMES:
            raise ValueError(
                "export must list every canonical table once in CANONICAL_SCHEMAS order"
            )
        return self

    def file_for(self, table_name: str) -> CanonicalParquetFile:
        for item in self.files:
            if item.table_name == table_name:
                return item
        raise KeyError(table_name)

    def row_counts(self) -> dict[str, int]:
        return {item.table_name: item.row_count for item in self.files}


def _schema_for(table_name: str) -> pa.Schema:
    schema = CANONICAL_SCHEMAS.get(table_name)
    if schema is None:
        raise CanonicalExportError(f"unknown canonical table {table_name!r}")
    return schema


def _arrow_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    return value


def _flatten(
    table_name: str,
    entity: object,
    *,
    model: type[CanonicalEntity],
    names: tuple[str, ...],
) -> dict[str, object]:
    if not isinstance(entity, model):
        raise CanonicalExportError(
            f"{table_name}: expected {model.__name__}, got {type(entity).__name__}"
        )
    payload = entity.model_dump(mode="python", exclude={"lineage"})
    lineage = lineage_columns(entity.lineage)
    overlap = payload.keys() & lineage.keys()
    if overlap:
        raise CanonicalExportError(
            f"{table_name}: entity fields collide with lineage columns {sorted(overlap)}"
        )
    merged = {**payload, **lineage}
    if merged.keys() != set(names):
        missing = sorted(set(names) - merged.keys())
        extra = sorted(merged.keys() - set(names))
        raise CanonicalExportError(
            f"{table_name}: row columns differ from CANONICAL_SCHEMAS "
            f"(missing={missing}, extra={extra})"
        )
    return {name: _arrow_value(merged[name]) for name in names}


def canonical_row(table_name: str, entity: CanonicalEntity) -> dict[str, object]:
    """Flatten one canonical entity into ``CANONICAL_SCHEMAS[table_name]`` column order."""
    schema = _schema_for(table_name)
    return _flatten(
        table_name,
        entity,
        model=CANONICAL_TABLE_MODELS[table_name],
        names=tuple(schema.names),
    )


def validate_canonical_arrow_table(table_name: str, table: pa.Table) -> None:
    """Require the exact interchange schema and no nulls in non-nullable columns."""
    schema = _schema_for(table_name)
    if not table.schema.equals(schema, check_metadata=False):
        raise CanonicalExportError(
            f"{table_name}: Arrow schema differs from CANONICAL_SCHEMAS[{table_name!r}]"
        )
    null_required = sorted(
        field.name
        for field in schema
        if not field.nullable and table.column(field.name).null_count > 0
    )
    if null_required:
        raise CanonicalExportError(
            f"{table_name}: non-nullable columns contain nulls {null_required}"
        )


def canonical_table_to_arrow(
    table_name: str, entities: Iterable[CanonicalEntity]
) -> pa.Table:
    """Build one validated Arrow table with schema ``CANONICAL_SCHEMAS[table_name]``."""
    schema = _schema_for(table_name)
    model = CANONICAL_TABLE_MODELS[table_name]
    names = tuple(schema.names)
    columns: dict[str, list[object]] = {name: [] for name in names}
    for entity in entities:
        row = _flatten(table_name, entity, model=model, names=names)
        for name in names:
            columns[name].append(row[name])
    arrays: list[pa.Array] = []
    for field in schema:
        try:
            arrays.append(pa.array(columns[field.name], type=field.type))
        except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
            raise CanonicalExportError(
                f"{table_name}.{field.name}: values do not convert to Arrow {field.type}"
            ) from exc
    table = pa.Table.from_arrays(arrays, schema=schema)
    validate_canonical_arrow_table(table_name, table)
    return table


def snapshot_to_arrow(snapshot: CanonicalSnapshot) -> dict[str, pa.Table]:
    """Build all 15 validated Arrow tables in ``CANONICAL_SCHEMAS`` order."""
    return {
        name: canonical_table_to_arrow(name, getattr(snapshot, name))
        for name in CANONICAL_TABLE_NAMES
    }


def canonical_parquet_path(output_dir: Path | str, table_name: str) -> Path:
    """Return ``<output_dir>/<table_name>.parquet`` for a known canonical table."""
    _schema_for(table_name)
    return Path(output_dir) / f"{table_name}.parquet"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_canonical_parquet(
    table_name: str,
    table: pa.Table,
    path: Path | str,
    *,
    overwrite: bool = False,
) -> CanonicalParquetFile:
    """Validate and write one canonical table with pinned Parquet options.

    The file is written to a temporary sibling and renamed into place, so a
    failed write never leaves a partial file at ``path``.
    """
    validate_canonical_arrow_table(table_name, table)
    target = Path(path)
    if target.exists() and not overwrite:
        raise CanonicalExportError(f"{table_name}: refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        pq.write_table(table, tmp_path, **_PARQUET_WRITE_OPTIONS)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return CanonicalParquetFile(
        table_name=table_name,
        path=target,
        row_count=table.num_rows,
        byte_size=target.stat().st_size,
        sha256=_sha256_file(target),
    )


def export_snapshot_to_parquet(
    snapshot: CanonicalSnapshot,
    output_dir: Path | str,
    *,
    overwrite: bool = False,
) -> CanonicalParquetExport:
    """Write all 15 canonical tables to ``<output_dir>/<table>.parquet``.

    Every table is built and validated before the first file is written. Without
    ``overwrite`` the export refuses to start when any target file exists.
    """
    directory = Path(output_dir)
    targets = {name: canonical_parquet_path(directory, name) for name in CANONICAL_TABLE_NAMES}
    if not overwrite:
        existing = sorted(name for name, target in targets.items() if target.exists())
        if existing:
            raise CanonicalExportError(
                f"refusing to overwrite existing canonical Parquet files {existing}"
            )
    tables = snapshot_to_arrow(snapshot)
    files = tuple(
        write_canonical_parquet(name, tables[name], targets[name], overwrite=overwrite)
        for name in CANONICAL_TABLE_NAMES
    )
    return CanonicalParquetExport(output_dir=directory, files=files)


def read_canonical_parquet(table_name: str, path: Path | str) -> pa.Table:
    """Read one canonical Parquet file and validate it against the interchange schema."""
    table = pq.read_table(Path(path))
    validate_canonical_arrow_table(table_name, table)
    return table
