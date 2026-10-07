"""Bounded profiling of one selected OpenAlex Works source file.

Explicit opt-in public command (never run by default tests or CI)::

    python -m research_platform.sources.openalex.profiling --format jsonl

It reads the public JSONL and Parquet Works manifests (metadata only), selects at
most one bounded file of the requested representation with the existing selector,
retrieves it through the existing bounded connector, and prints a safe summary.
It writes nothing except a temporary local file for Parquet footer inspection,
which is deleted before returning.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from research_platform.sources.openalex.connector import (
    OpenAlexConnector,
    OpenAlexConnectorError,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.profile_jsonl import sample_jsonl
from research_platform.sources.openalex.profile_models import (
    EvidenceType,
    FieldProfile,
    OpenAlexDecompressionError,
    OpenAlexEmptySourceError,
    OpenAlexMalformedParquetError,
    OpenAlexProfileError,
    OpenAlexProfileLimitError,
    OpenAlexProfileRun,
    OpenAlexSizeMismatchError,
    OpenAlexSourceProfile,
    OpenAlexUnsupportedFormatError,
    ProfileBudget,
    ProfilingLimits,
    RepresentationEvidence,
)
from research_platform.sources.openalex.profile_parquet import (
    PARQUET_MAGIC,
    inspect_parquet,
)

_GZIP_MAGIC = b"\x1f\x8b"
_CHUNK_SIZE = 64 * 1024
_FORMATS = ("jsonl", "parquet")

_E = EvidenceType


class _CountingReader(io.RawIOBase):
    """Count actual payload bytes and enforce byte/declared-size bounds."""

    def __init__(
        self, stream: BinaryIO, *, max_bytes: int, declared_bytes: int | None,
        budget: ProfileBudget | None = None,
    ):
        super().__init__()
        self._stream = stream
        self._max_bytes = max_bytes
        self._declared_bytes = declared_bytes
        self._budget = budget
        self._pending = b""
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def signature(self, size: int) -> bytes:
        while len(self._pending) < size:
            chunk = self._read_raw(size - len(self._pending))
            if not chunk:
                break
            self._pending += chunk
        return self._pending[:size]

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        view = memoryview(buffer).cast("B")
        if not view:
            return 0
        if self._pending:
            size = min(len(view), len(self._pending))
            view[:size] = self._pending[:size]
            self._pending = self._pending[size:]
            return size
        chunk = self._read_raw(len(view))
        view[: len(chunk)] = chunk
        return len(chunk)

    def verify_complete(self) -> int:
        """Read any remaining bytes (still bounded) and verify the declared size."""
        while self._pending or self._read_raw(_CHUNK_SIZE):
            self._pending = b""
        if self._declared_bytes is not None and self.bytes_read != self._declared_bytes:
            raise OpenAlexSizeMismatchError("observed payload size differs from metadata")
        return self.bytes_read

    def _read_raw(self, size: int) -> bytes:
        if self._budget is not None:
            self._budget.check()
        bound = self._max_bytes
        if self._declared_bytes is not None:
            bound = min(bound, self._declared_bytes)
        if self.bytes_read > bound:
            raise OpenAlexProfileLimitError("cannot read after a payload bound was exceeded")
        chunk = self._stream.read(min(size, _CHUNK_SIZE, bound - self.bytes_read + 1))
        if self._budget is not None:
            self._budget.check()
        if not chunk:
            return b""
        self.bytes_read += len(chunk)
        if self.bytes_read > self._max_bytes:
            raise OpenAlexProfileLimitError("payload exceeds the configured byte limit")
        if self._declared_bytes is not None and self.bytes_read > self._declared_bytes:
            raise OpenAlexSizeMismatchError("payload exceeds its declared metadata size")
        return chunk


def profile_openalex_asset(
    connector: OpenAlexConnector,
    asset: OpenAlexAssetMetadata,
    limits: ProfilingLimits = ProfilingLimits(),
) -> OpenAlexSourceProfile:
    """Check metadata first, then retrieve and profile one bounded file.

    The connector's URI, content-type, Content-Length, truncation, and actual-byte
    checks remain in force; this function adds metadata, declared-size, format
    signature, decompression, and sampling bounds. The stream is always closed.
    """
    limits = ProfilingLimits.model_validate(limits.model_dump())
    budget = ProfileBudget(limits)
    compression = _check_metadata(asset, limits)
    stream = connector.fetch(asset.to_source_asset())
    try:
        budget.check()
        content_type = getattr(stream, "content_type", None)
        content_length = getattr(stream, "content_length", None)
        if content_length is not None and content_length != asset.byte_size:
            raise OpenAlexSizeMismatchError("Content-Length differs from manifest metadata")
        reader = _CountingReader(
            stream, max_bytes=limits.max_file_size_bytes, declared_bytes=asset.byte_size,
            budget=budget,
        )
        if asset.content_format == "parquet":
            return _profile_parquet(reader, asset, content_type, content_length, limits, budget)
        return _profile_jsonl(
            reader, asset, compression, content_type, content_length, limits, budget
        )
    finally:
        stream.close()


def profile_openalex_works(
    connectors: Mapping[str, OpenAlexConnector],
    *,
    profile_format: str = "jsonl",
    limits: ProfilingLimits = ProfilingLimits(),
) -> OpenAlexProfileRun:
    """Record manifest evidence per representation and profile at most one file."""
    limits = ProfilingLimits.model_validate(limits.model_dump())
    if profile_format not in _FORMATS or profile_format not in connectors:
        raise OpenAlexUnsupportedFormatError("profile format must be jsonl or parquet")
    representations: list[RepresentationEvidence] = []
    selected: tuple[OpenAlexAssetMetadata, ...] = ()
    error_category: str | None = None
    for content_format in _FORMATS:
        connector = connectors.get(content_format)
        if connector is None:
            continue
        try:
            selection = connector.discover_metadata()
        except OpenAlexConnectorError as error:
            if content_format == profile_format:
                error_category = type(error).__name__
            representations.append(
                RepresentationEvidence(
                    source_format=content_format,
                    manifest_uri=connector.manifest_uri,
                    status="UNAVAILABLE",
                    evidence=_E.UNKNOWN,
                    listed_file_count=None,
                    eligible_file_count=None,
                    smallest_eligible_size_bytes=None,
                    error_category=type(error).__name__,
                )
            )
            continue
        representations.append(
            RepresentationEvidence(
                source_format=content_format,
                manifest_uri=connector.manifest_uri,
                status="VERIFIED",
                evidence=_E.VERIFIED_METADATA,
                listed_file_count=len(selection.selected) + len(selection.skipped),
                eligible_file_count=selection.eligible_count,
                smallest_eligible_size_bytes=(
                    selection.selected[0].byte_size if selection.selected else None
                ),
                error_category=None,
            )
        )
        if content_format == profile_format:
            selected = selection.selected[: limits.max_files]

    profiles: list[OpenAlexSourceProfile] = []
    if error_category is None and not selected:
        error_category = "NoEligibleFile"
    for asset in selected:
        try:
            profiles.append(
                profile_openalex_asset(connectors[profile_format], asset, limits)
            )
        except (OpenAlexConnectorError, OpenAlexProfileError) as error:
            error_category = type(error).__name__
    return OpenAlexProfileRun(
        limits=limits,
        profile_format=profile_format,  # type: ignore[arg-type]
        representations=tuple(representations),
        selected_uri=selected[0].file_uri if selected else None,
        profiles=tuple(profiles),
        error_category=error_category,
    )


def _check_metadata(asset: OpenAlexAssetMetadata, limits: ProfilingLimits) -> str | None:
    if not isinstance(asset, OpenAlexAssetMetadata):
        raise TypeError("profiling requires validated OpenAlexAssetMetadata")
    if asset.entity != "works" or asset.content_format not in _FORMATS:
        raise OpenAlexUnsupportedFormatError("only OpenAlex Works JSONL or Parquet is supported")
    if asset.byte_size is None:
        raise OpenAlexProfileLimitError("asset size is unknown; refusing to retrieve it")
    if asset.byte_size > limits.max_file_size_bytes:
        raise OpenAlexProfileLimitError("asset metadata exceeds the configured byte limit")
    if asset.byte_size == 0:
        raise OpenAlexEmptySourceError("asset metadata declares an empty file")
    if asset.content_format == "jsonl":
        return "gzip" if asset.file_uri.endswith(".gz") else "none"
    return None


def _check_signature(signature: bytes, content_format: str, compression: str | None) -> None:
    if not signature:
        raise OpenAlexEmptySourceError("payload contains no bytes")
    if content_format == "parquet":
        if signature.startswith(_GZIP_MAGIC):
            raise OpenAlexUnsupportedFormatError("payload is gzip, not Parquet; not relabeled")
        if signature != PARQUET_MAGIC:
            raise OpenAlexMalformedParquetError("Parquet file has no leading magic bytes")
        return
    if signature == PARQUET_MAGIC:
        raise OpenAlexUnsupportedFormatError("payload is Parquet, not JSONL; not relabeled")
    if compression == "gzip" and not signature.startswith(_GZIP_MAGIC):
        raise OpenAlexDecompressionError("payload declared as gzip has no gzip header")
    if compression != "gzip" and signature.startswith(_GZIP_MAGIC):
        raise OpenAlexUnsupportedFormatError("payload is gzip but was declared uncompressed")


def _profile_jsonl(
    reader: _CountingReader,
    asset: OpenAlexAssetMetadata,
    compression: str | None,
    content_type: str | None,
    content_length: int | None,
    limits: ProfilingLimits,
    budget: ProfileBudget,
) -> OpenAlexSourceProfile:
    _check_signature(reader.signature(len(PARQUET_MAGIC)), "jsonl", compression)
    observed_size = None
    row_count = None
    with io.BufferedReader(reader, buffer_size=_CHUNK_SIZE) as buffered:
        sample = sample_jsonl(
            buffered,
            compression="gzip" if compression == "gzip" else None,
            max_records=limits.max_profile_records,
            max_decoded_bytes=limits.max_decoded_sample_bytes,
            limits=limits,
            _budget=budget,
        )
        if sample.reached_eof:
            while buffered.read(_CHUNK_SIZE):
                pass
            observed_size = reader.verify_complete()
            row_count = sample.sampled_record_count
            _check_record_count(asset, row_count)
    limitations = [
        "Profiles one bounded development file; facts do not describe the full snapshot.",
        f"Schema, nesting, types, and nullability come from at most "
        f"{limits.max_profile_records} leading records; not observing nulls does not "
        "prove a field is non-nullable.",
        "Field paths use '.' for object members and '[]' for array elements; unusual "
        "keys are JSON-quoted, and abstract_inverted_index word keys are collapsed "
        "into one map value path.",
        "Decoded bytes include the EOF probe. Limits bound consumed bytes and JSON "
        "structure, not gzip's internal read-ahead or exact Python heap usage. "
        "The deadline is cooperative and cannot interrupt a native call.",
    ]
    if not sample.reached_eof:
        limitations.append(
            "The file was not read to EOF: row_count and observed_size_bytes are unknown, "
            "and corruption after the sampled prefix is not detected."
        )
    fields = sample.fields
    return _build_profile(
        asset=asset,
        compression=compression,
        content_type=content_type,
        content_length=content_length,
        observed_size=observed_size,
        bytes_read=reader.bytes_read,
        decoded_bytes=sample.decoded_bytes,
        fields=fields,
        schema_evidence=_E.SAMPLED_OBSERVATION,
        row_count=row_count,
        row_group_count=None,
        column_count=None,
        physical_column_count=None,
        statistics_available=None,
        created_by=None,
        sampled_record_count=sample.sampled_record_count,
        limits=limits,
        limitations=limitations,
    )


def _profile_parquet(
    reader: _CountingReader,
    asset: OpenAlexAssetMetadata,
    content_type: str | None,
    content_length: int | None,
    limits: ProfilingLimits,
    budget: ProfileBudget,
) -> OpenAlexSourceProfile:
    _check_signature(reader.signature(len(PARQUET_MAGIC)), "parquet", None)
    with tempfile.TemporaryDirectory(prefix="openalex-profile-") as directory:
        path = Path(directory) / "source.parquet"
        with path.open("wb") as handle:
            while chunk := reader.read(_CHUNK_SIZE):
                handle.write(chunk)
        observed_size = reader.verify_complete()
        inspection = inspect_parquet(
            path, max_records=limits.max_profile_records, limits=limits, _budget=budget
        )
    _check_record_count(asset, inspection.row_count)
    limitations = [
        "Profiles one bounded development file; facts do not describe the full snapshot.",
        "Schema, nullability, row and row-group counts, codecs, and statistics "
        "availability come from the exact Parquet footer.",
        f"sampled_* counts cover only top-level columns in the first bounded batch of "
        f"at most {limits.max_profile_records} rows.",
        "Types are PyArrow's interpretation of the Parquet schema.",
        "Footer byte/Thrift, schema/work, conservative dictionary-expansion, and "
        "batch-byte guards apply. Footer declarations are not a hard native-memory "
        "sandbox, and cooperative deadlines cannot interrupt native Arrow calls.",
        "Bytes were spooled to a temporary local file, deleted after profiling, "
        "because the Parquet footer is at the end of the file.",
    ]
    if inspection.row_count == 0:
        limitations.append("The file contains zero rows; no values were sampled.")
    return _build_profile(
        asset=asset,
        compression=inspection.compression,
        content_type=content_type,
        content_length=content_length,
        observed_size=observed_size,
        bytes_read=reader.bytes_read,
        decoded_bytes=None,
        fields=inspection.fields,
        schema_evidence=_E.EXACT_FILE_METADATA,
        row_count=inspection.row_count,
        row_group_count=inspection.row_group_count,
        column_count=inspection.column_count,
        physical_column_count=inspection.physical_column_count,
        statistics_available=inspection.statistics_available,
        created_by=inspection.created_by,
        sampled_record_count=inspection.sampled_record_count,
        limits=limits,
        limitations=limitations,
    )


def _check_record_count(asset: OpenAlexAssetMetadata, row_count: int) -> None:
    if asset.record_count is not None and asset.record_count != row_count:
        raise OpenAlexSizeMismatchError("observed record count differs from metadata")


def _build_profile(
    *,
    asset: OpenAlexAssetMetadata,
    compression: str | None,
    content_type: str | None,
    content_length: int | None,
    observed_size: int | None,
    bytes_read: int,
    decoded_bytes: int | None,
    fields: tuple[FieldProfile, ...],
    schema_evidence: EvidenceType,
    row_count: int | None,
    row_group_count: int | None,
    column_count: int | None,
    physical_column_count: int | None,
    statistics_available: str | None,
    created_by: str | None,
    sampled_record_count: int,
    limits: ProfilingLimits,
    limitations: Sequence[str],
) -> OpenAlexSourceProfile:
    exact = _E.EXACT_FILE_METADATA
    verified = _E.VERIFIED_METADATA
    values = {
        "source_format": (asset.content_format, verified),
        "compression": (compression, exact),
        "content_type": (content_type, verified),
        "snapshot_date": (asset.snapshot_date, verified),
        "updated_date": (asset.updated_date, verified),
        "declared_size_bytes": (asset.byte_size, verified),
        "declared_record_count": (asset.record_count, verified),
        "content_length_bytes": (content_length, verified),
        "observed_size_bytes": (observed_size, exact),
        "schema_fields": (fields, schema_evidence),
        "nested_fields": (
            tuple(field.path for field in fields if field.kind != "scalar"),
            schema_evidence,
        ),
        "nullable_fields": (
            tuple(field.path for field in fields if field.nullable),
            schema_evidence,
        ),
        "row_count": (row_count, exact),
        "row_group_count": (row_group_count, exact),
        "column_count": (column_count, exact),
        "physical_column_count": (physical_column_count, exact),
        "statistics_available": (statistics_available, exact),
        "parquet_created_by": (created_by, exact),
        "sampled_record_count": (sampled_record_count, _E.SAMPLED_OBSERVATION),
    }
    evidence = {
        name: _E.UNKNOWN if value is None else kind for name, (value, kind) in values.items()
    }
    return OpenAlexSourceProfile(
        source_uri=asset.file_uri,
        bytes_read=bytes_read,
        decoded_bytes_sampled=decoded_bytes,
        limits=limits,
        evidence=evidence,
        limitations=tuple(limitations),
        **{name: value for name, (value, _) in values.items()},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly profile one bounded public OpenAlex Works file."
    )
    parser.add_argument("--format", choices=_FORMATS, default="jsonl")
    parser.add_argument("--max-file-size-bytes", type=int, default=25_000_000)
    parser.add_argument("--max-profile-records", type=int, default=100)
    arguments = parser.parse_args(argv)
    try:
        limits = ProfilingLimits(
            max_file_size_bytes=arguments.max_file_size_bytes,
            max_profile_records=arguments.max_profile_records,
        )
    except ValidationError:
        print(json.dumps({"profile": "failed", "error_category": "InvalidLimits"}))
        return 2
    connectors = {
        content_format: OpenAlexConnector(
            sample_selection=limits.sample_selection(),
            content_format=content_format,
            timeout_seconds=10,
            max_attempts=1,
        )
        for content_format in _FORMATS
    }
    run = profile_openalex_works(
        connectors, profile_format=arguments.format, limits=limits
    )
    status = "failed" if run.error_category else "profiled"
    print(json.dumps({"profile": status, **run.model_dump(mode="json")}, sort_keys=True))
    return 1 if run.error_category else 0


if __name__ == "__main__":
    raise SystemExit(main())
