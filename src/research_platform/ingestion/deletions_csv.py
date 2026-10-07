"""Bounded streaming decode of landed OpenAlex deleted_ids.csv.gz objects."""

from __future__ import annotations

import csv
import gzip
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import BinaryIO

from research_platform.canonical.openalex.errors import IdentifierError
from research_platform.canonical.openalex.identifiers import normalize_openalex_id
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError

_CHUNK = 64 * 1024
_REQUIRED_HEADERS = ("work_id", "deleted_date")


@dataclass(frozen=True)
class DeletedWorkRecord:
    """One streamed row from the OpenAlex Works deletion ledger."""

    work_id: str
    deleted_date: date


def iter_deleted_work_records(
    stream: BinaryIO,
    *,
    limits: CsvDeletionDecodeLimits | None = None,
) -> Iterator[DeletedWorkRecord]:
    """Yield typed deletion rows from a gzip CSV deletion stream.

    Authoritative CSV contract:

    - Header exactly ``work_id,deleted_date`` (order fixed; no extra columns)
    - ``work_id``: OpenAlex Work URL or short ``W…`` form (Step 11 normalization)
    - ``deleted_date``: exact ``YYYY-MM-DD``
    - Blank rows skipped
    - Unexpected schema fails explicitly

    Does not fabricate IDs or dates from malformed values.
    """
    bounds = limits or CsvDeletionDecodeLimits()
    decoded_total = 0
    rows_seen = 0
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as reader:
            header_seen = False
            while True:
                remaining = bounds.max_decompressed_bytes - decoded_total
                line, consumed = _read_bounded_line(
                    reader,
                    max_row_bytes=bounds.max_row_bytes,
                    max_decompressed_remaining=remaining,
                )
                if consumed == 0:
                    break
                decoded_total += consumed
                if decoded_total > bounds.max_decompressed_bytes:
                    raise IngestionDecodeError(
                        "deletion CSV exceeds max_decompressed_bytes"
                    )
                if not line.strip():
                    continue
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise IngestionDecodeError(
                        "malformed UTF-8 in deletion CSV"
                    ) from error
                text = text.rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    fields = next(csv.reader([text]))
                except csv.Error as error:
                    raise IngestionDecodeError("malformed deletion CSV row") from error

                if not header_seen:
                    header = tuple(item.strip().lower() for item in fields)
                    if header != _REQUIRED_HEADERS:
                        raise IngestionDecodeError(
                            "deletion CSV header must be exactly work_id,deleted_date"
                        )
                    header_seen = True
                    continue

                if len(fields) != 2:
                    raise IngestionDecodeError(
                        "deletion CSV row must have exactly two columns"
                    )
                raw_id = fields[0].strip()
                raw_date = fields[1].strip()
                if not raw_id:
                    raise IngestionDecodeError("deletion CSV row missing work_id")
                if not raw_date:
                    raise IngestionDecodeError("deletion CSV row missing deleted_date")
                rows_seen += 1
                if rows_seen > bounds.max_ids:
                    raise IngestionDecodeError("deletion CSV exceeds max_ids")
                try:
                    work_id = normalize_openalex_id(
                        raw_id, expected_prefix="W", field_name="work_id"
                    )
                except IdentifierError as error:
                    raise IngestionDecodeError(
                        "malformed OpenAlex Work id in deletion CSV"
                    ) from error
                try:
                    deleted_date = date.fromisoformat(raw_date)
                except ValueError as error:
                    raise IngestionDecodeError(
                        "malformed deleted_date in deletion CSV"
                    ) from error
                if deleted_date.isoformat() != raw_date:
                    raise IngestionDecodeError(
                        "deleted_date must be exact YYYY-MM-DD"
                    )
                yield DeletedWorkRecord(work_id=work_id, deleted_date=deleted_date)
    except IngestionDecodeError:
        raise
    except EOFError as error:
        raise IngestionFormatError("truncated gzip deletion CSV stream") from error
    except (OSError, zlib.error) as error:
        raise IngestionFormatError(
            "gzip deletion CSV stream could not be decoded"
        ) from error


# Back-compat alias used by older call sites during hardening; prefer records.
def iter_deleted_work_ids(
    stream: BinaryIO,
    *,
    limits: CsvDeletionDecodeLimits | None = None,
) -> Iterator[str]:
    for record in iter_deleted_work_records(stream, limits=limits):
        yield record.work_id


def _read_bounded_line(
    reader: gzip.GzipFile,
    *,
    max_row_bytes: int,
    max_decompressed_remaining: int,
) -> tuple[bytes, int]:
    if max_decompressed_remaining < 0:
        raise IngestionDecodeError("deletion CSV exceeds max_decompressed_bytes")
    if max_decompressed_remaining == 0:
        probe = reader.read(1)
        if not probe:
            return b"", 0
        raise IngestionDecodeError("deletion CSV exceeds max_decompressed_bytes")

    parts: list[bytes] = []
    total = 0
    while True:
        remaining_row = max_row_bytes - total
        remaining_stream = max_decompressed_remaining - total
        if remaining_row <= 0:
            raise IngestionDecodeError("deletion CSV row exceeds max_row_bytes")
        if remaining_stream <= 0:
            break
        to_read = min(_CHUNK, remaining_row + 1, remaining_stream + 1)
        chunk = reader.readline(to_read)
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
        if total > max_row_bytes:
            raise IngestionDecodeError("deletion CSV row exceeds max_row_bytes")
        if total > max_decompressed_remaining:
            raise IngestionDecodeError("deletion CSV exceeds max_decompressed_bytes")
        if chunk.endswith(b"\n"):
            break
        if len(chunk) < to_read:
            break
    return b"".join(parts), total
