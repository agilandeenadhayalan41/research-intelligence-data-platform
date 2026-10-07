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
    OpenAlexProfileError,
    OpenAlexProfileLimitError,
    ProfileBudget,
    ProfilingLimits,
    child_path,
    sampled_count_evidence,
)

PARQUET_MAGIC = b"PAR1"
_THRIFT_SIZE_LIMIT_MARKERS = ("Exceeded size limit", "TProtocolException")


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


def inspect_parquet(
    path: Path,
    *,
    max_records: int,
    limits: ProfilingLimits = ProfilingLimits(),
    _budget: ProfileBudget | None = None,
) -> ParquetInspection:
    """Read footer metadata first, then at most one batch of ``max_records`` rows.

    Footer/schema and conservative expansion guards precede value access. Native
    Arrow calls are not a hard memory sandbox and are timed only at checkpoints.
    """
    limits = ProfilingLimits.model_validate({
        **limits.model_dump(), "max_profile_records": max_records,
    })
    budget = _budget if _budget is not None else ProfileBudget(limits)
    try:
        budget.check()
        _check_magic(path, limits)
        with pq.ParquetFile(
            path,
            thrift_string_size_limit=limits.max_parquet_footer_bytes,
            thrift_container_size_limit=limits.max_profile_nodes,
            pre_buffer=False,
        ) as parquet_file:
            budget.check()
            metadata = parquet_file.metadata
            if (
                metadata.num_columns > limits.max_schema_fields
                or metadata.num_row_groups > limits.max_profile_nodes
                or metadata.num_columns * metadata.num_row_groups > limits.max_profile_nodes
            ):
                raise OpenAlexProfileLimitError("Parquet metadata exceeds its work budget")
            compression, statistics = _column_chunk_facts(metadata, budget)
            schema = parquet_file.schema_arrow
            fields: list[FieldProfile] = []
            for field in schema:
                _walk(child_path("", field.name), field, fields, budget)
            sampled_counts: dict[str, tuple[int, int]] = {}
            sampled_records = 0
            if metadata.num_rows:
                budget.check()
                batch = next(
                    parquet_file.iter_batches(batch_size=max_records, use_threads=False), None
                )
                budget.check()
                if batch is not None:
                    if batch.nbytes > limits.max_decoded_sample_bytes:
                        raise OpenAlexProfileLimitError("Parquet batch exceeds its decoded byte limit")
                    batch = batch.slice(0, max_records)
                    sampled_records = batch.num_rows
                    for index, field in enumerate(schema):
                        budget.check()
                        sampled_counts[child_path("", field.name)] = (
                            batch.num_rows,
                            batch.column(index).null_count,
                        )
            for index, field in enumerate(fields):
                budget.check()
                if field.path in sampled_counts:
                    present, nulls = sampled_counts[field.path]
                    fields[index] = FieldProfile.model_validate({
                        **field.model_dump(),
                        "sampled_present_count": present,
                        "sampled_null_count": nulls,
                        "sampled_evidence": sampled_count_evidence(present, None, nulls),
                    })
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
    except OpenAlexProfileError:
        raise
    except (pa.ArrowException, OSError, ValueError) as exc:
        if _is_thrift_allocation_limit_error(exc):
            raise OpenAlexProfileLimitError(
                "Parquet thrift metadata exceeds its configured allocation limit"
            ) from None
        raise OpenAlexMalformedParquetError("Parquet footer or data cannot be read") from None


def _is_thrift_allocation_limit_error(exc: BaseException) -> bool:
    """True when Arrow rejected thrift string/container allocations at our caps."""
    text = str(exc)
    return "thrift" in text.lower() and any(
        marker in text for marker in _THRIFT_SIZE_LIMIT_MARKERS
    )


def _check_magic(path: Path, limits: ProfilingLimits) -> None:
    size = path.stat().st_size
    if size > limits.max_file_size_bytes:
        raise OpenAlexProfileLimitError("Parquet source exceeds the byte limit")
    if size < 2 * len(PARQUET_MAGIC) + 4:
        raise OpenAlexMalformedParquetError("Parquet file is truncated")
    with path.open("rb") as handle:
        head = handle.read(len(PARQUET_MAGIC))
        handle.seek(-8, 2)
        footer = handle.read(8)
    if head != PARQUET_MAGIC:
        raise OpenAlexMalformedParquetError("Parquet file has no leading magic bytes")
    if footer[4:] != PARQUET_MAGIC:
        raise OpenAlexMalformedParquetError("Parquet file is truncated or has no footer")
    footer_bytes = int.from_bytes(footer[:4], "little")
    if not 0 < footer_bytes <= size - 12:
        raise OpenAlexMalformedParquetError("Parquet footer length is invalid")
    if footer_bytes > limits.max_parquet_footer_bytes:
        raise OpenAlexProfileLimitError("Parquet footer exceeds the metadata byte limit")


def _column_chunk_facts(
    metadata: pq.FileMetaData, budget: ProfileBudget,
) -> tuple[str | None, Literal["all", "partial", "none"] | None]:
    codecs: set[str] = set()
    with_stats = 0
    chunks = 0
    decoded_bound = 0
    for group_index in range(metadata.num_row_groups):
        budget.check()
        group = metadata.row_group(group_index)
        for column_index in range(group.num_columns):
            budget.visit()
            column = group.column(column_index)
            if column.total_uncompressed_size < 0 or column.num_values < 0:
                raise OpenAlexMalformedParquetError("Parquet column size/count is invalid")
            # A dictionary value can be repeated many times, including inside one list row.
            decoded_bound += column.total_uncompressed_size * max(1, column.num_values)
            if decoded_bound > budget.limits.max_decoded_sample_bytes:
                raise OpenAlexProfileLimitError("Parquet values exceed the conservative expansion budget")
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
    budget: ProfileBudget,
    depth: int = 1,
) -> None:
    budget.visit()
    if depth > budget.limits.max_nesting_depth or len(out) >= budget.limits.max_schema_fields:
        raise OpenAlexProfileLimitError("Parquet schema exceeds its depth or field limit")
    data_type = field.type
    children: list[tuple[str, pa.Field]] = []
    if pa.types.is_struct(data_type):
        if len(out) + 1 + data_type.num_fields > budget.limits.max_schema_fields:
            raise OpenAlexProfileLimitError("Parquet schema exceeds its field limit")
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
    out.append(
        FieldProfile(
            path=path,
            data_type=type_name,
            kind=kind,
            nullable=field.nullable,
            evidence=EvidenceType.EXACT_FILE_METADATA,
            sampled_evidence=sampled_count_evidence(None, None, None),
        )
    )
    for nested_path, child in children:
        _walk(nested_path, child, out, budget, depth + 1)
