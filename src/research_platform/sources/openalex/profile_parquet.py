"""Footer-first, bounded PyArrow inspection of genuine Parquet files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq

from research_platform.sources.openalex.profile_models import (
    EvidenceType,
    FieldProfile,
    OpenAlexMalformedParquetError,
    child_path,
)

PARQUET_MAGIC = b"PAR1"


@dataclass(frozen=True)
class ParquetInspection:
    """Exact footer metadata plus sampled counts from one bounded record batch."""

    row_count: int
    row_group_count: int
    column_count: int
    physical_column_count: int
    compression: str | None
    statistics_available: Literal["all", "partial", "none"] | None
    created_by: str | None
    sampled_record_count: int
    fields: tuple[FieldProfile, ...]


def inspect_parquet(path: Path, *, max_records: int) -> ParquetInspection:
    """Read footer metadata first, then at most one batch of ``max_records`` rows.

    The file is never converted to pandas or read as a whole table.
    """
    if type(max_records) is not int or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    _check_magic(path)
    try:
        with pq.ParquetFile(path) as parquet_file:
            metadata = parquet_file.metadata
            schema = parquet_file.schema_arrow
            compression, statistics = _column_chunk_facts(metadata)
            sampled_counts: dict[str, tuple[int, int]] = {}
            sampled_records = 0
            if metadata.num_rows:
                batch = next(parquet_file.iter_batches(batch_size=max_records), None)
                if batch is not None:
                    batch = batch.slice(0, max_records)
                    sampled_records = batch.num_rows
                    for index, field in enumerate(schema):
                        sampled_counts[child_path("", field.name)] = (
                            batch.num_rows,
                            batch.column(index).null_count,
                        )
            fields: list[FieldProfile] = []
            for field in schema:
                _walk(child_path("", field.name), field, fields, sampled_counts)
            return ParquetInspection(
                row_count=metadata.num_rows,
                row_group_count=metadata.num_row_groups,
                column_count=len(schema),
                physical_column_count=metadata.num_columns,
                compression=compression,
                statistics_available=statistics,
                created_by=metadata.created_by or None,
                sampled_record_count=sampled_records,
                fields=tuple(sorted(fields, key=lambda item: item.path)),
            )
    except (pa.ArrowException, OSError, ValueError):
        raise OpenAlexMalformedParquetError("Parquet footer or data cannot be read") from None


def _check_magic(path: Path) -> None:
    size = path.stat().st_size
    if size < 2 * len(PARQUET_MAGIC) + 4:
        raise OpenAlexMalformedParquetError("Parquet file is truncated")
    with path.open("rb") as handle:
        head = handle.read(len(PARQUET_MAGIC))
        handle.seek(-len(PARQUET_MAGIC), 2)
        tail = handle.read(len(PARQUET_MAGIC))
    if head != PARQUET_MAGIC:
        raise OpenAlexMalformedParquetError("Parquet file has no leading magic bytes")
    if tail != PARQUET_MAGIC:
        raise OpenAlexMalformedParquetError("Parquet file is truncated or has no footer")


def _column_chunk_facts(
    metadata: pq.FileMetaData,
) -> tuple[str | None, Literal["all", "partial", "none"] | None]:
    codecs: set[str] = set()
    with_stats = 0
    chunks = 0
    for group_index in range(metadata.num_row_groups):
        group = metadata.row_group(group_index)
        for column_index in range(group.num_columns):
            column = group.column(column_index)
            codecs.add(str(column.compression))
            chunks += 1
            with_stats += 1 if column.is_stats_set else 0
    if not chunks:
        return None, None
    statistics: Literal["all", "partial", "none"] = (
        "all" if with_stats == chunks else "partial" if with_stats else "none"
    )
    return ",".join(sorted(codecs)), statistics


def _walk(
    path: str,
    field: pa.Field,
    out: list[FieldProfile],
    sampled_counts: dict[str, tuple[int, int]],
) -> None:
    data_type = field.type
    children: list[tuple[str, pa.Field]] = []
    if pa.types.is_struct(data_type):
        kind = "struct"
        type_name = "struct"
        children = [
            (child_path(path, data_type.field(index).name), data_type.field(index))
            for index in range(data_type.num_fields)
        ]
    elif pa.types.is_map(data_type):
        kind = "map"
        type_name = "map"
        children = [
            (f"{path}{{key}}", data_type.key_field),
            (f"{path}{{value}}", data_type.item_field),
        ]
    elif (
        pa.types.is_list(data_type)
        or pa.types.is_large_list(data_type)
        or pa.types.is_fixed_size_list(data_type)
        or pa.types.is_list_view(data_type)
        or pa.types.is_large_list_view(data_type)
    ):
        kind = "list"
        type_name = "list"
        children = [(f"{path}[]", data_type.value_field)]
    else:
        kind = "scalar"
        type_name = str(data_type)
    present, nulls = sampled_counts.get(path, (None, None))
    out.append(
        FieldProfile(
            path=path,
            data_type=type_name,
            kind=kind,
            nullable=field.nullable,
            evidence=EvidenceType.EXACT_FILE_METADATA,
            sampled_present_count=present,
            sampled_null_count=nulls,
        )
    )
    for nested_path, child in children:
        _walk(nested_path, child, out, {})
