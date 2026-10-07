"""Bounded streaming decode of landed OpenAlex deleted_ids.csv.gz objects."""

from __future__ import annotations

import csv
import gzip
import zlib
from collections.abc import Iterator
from typing import BinaryIO

from research_platform.canonical.openalex.errors import IdentifierError
from research_platform.canonical.openalex.identifiers import normalize_openalex_id
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError

_CHUNK = 64 * 1024
_REQUIRED_HEADER = "id"


def iter_deleted_work_ids(
    stream: BinaryIO,
    *,
    limits: CsvDeletionDecodeLimits | None = None,
) -> Iterator[str]:
    """Yield normalized Work IDs from a gzip CSV deletion stream.

    Contract (Step 13):
    - Required header column: ``id``
    - Values: OpenAlex Work URL or short ``W…`` form
    - Blank rows skipped
    - Duplicate IDs are yielded once; callers may count duplicates via a seen-set
      or by inspecting row order (this iterator yields every valid occurrence so
      the publisher can classify DUPLICATE)

    Does not fabricate IDs from malformed values.
    """
    bounds = limits or CsvDeletionDecodeLimits()
    decoded_total = 0
    rows_seen = 0
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as reader:
            header_fields: list[str] | None = None
            id_index: int | None = None
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

                if header_fields is None:
                    header_fields = [item.strip().lower() for item in fields]
                    if _REQUIRED_HEADER not in header_fields:
                        raise IngestionDecodeError(
                            "deletion CSV missing required 'id' header"
                        )
                    id_index = header_fields.index(_REQUIRED_HEADER)
                    continue

                assert id_index is not None
                if len(fields) <= id_index:
                    raise IngestionDecodeError("deletion CSV row missing id column")
                # Extra columns are tolerated after the required id column.
                raw_id = fields[id_index].strip()
                if not raw_id:
                    continue
                rows_seen += 1
                if rows_seen > bounds.max_ids:
                    raise IngestionDecodeError("deletion CSV exceeds max_ids")
                try:
                    yield normalize_openalex_id(
                        raw_id, expected_prefix="W", field_name="id"
                    )
                except IdentifierError as error:
                    raise IngestionDecodeError(
                        "malformed OpenAlex Work id in deletion CSV"
                    ) from error
    except IngestionDecodeError:
        raise
    except EOFError as error:
        raise IngestionFormatError("truncated gzip deletion CSV stream") from error
    except (OSError, zlib.error) as error:
        raise IngestionFormatError(
            "gzip deletion CSV stream could not be decoded"
        ) from error


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
