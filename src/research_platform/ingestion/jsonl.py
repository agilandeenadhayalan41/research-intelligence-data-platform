"""Bounded streaming decode of landed OpenAlex Works JSONL.GZ objects."""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterator, Mapping
from typing import BinaryIO

from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError

_CHUNK = 64 * 1024


def iter_jsonl_gz_records(
    stream: BinaryIO,
    *,
    limits: JsonlDecodeLimits | None = None,
) -> Iterator[Mapping[str, object]]:
    """Yield one JSON object per line from a gzip-compressed JSONL stream.

    Enforces finite ``max_decompressed_bytes``, ``max_records``, and
    ``max_record_bytes``. Does not materialize the full decompressed payload.
    The caller owns ``stream``.
    """
    bounds = limits or JsonlDecodeLimits()
    decoded_total = 0
    records = 0
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as reader:
            while True:
                line, consumed = _read_bounded_line(
                    reader,
                    max_record_bytes=bounds.max_record_bytes,
                    max_decompressed_remaining=bounds.max_decompressed_bytes
                    - decoded_total,
                )
                if consumed == 0:
                    break
                decoded_total += consumed
                if decoded_total > bounds.max_decompressed_bytes:
                    raise IngestionDecodeError(
                        "JSONL stream exceeds max_decompressed_bytes"
                    )
                if not line.strip():
                    continue
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise IngestionDecodeError(
                        "malformed UTF-8 in JSONL record"
                    ) from error
                text = text.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    raise IngestionDecodeError("malformed JSONL record") from error
                if not isinstance(record, Mapping):
                    raise IngestionDecodeError("JSONL record must be an object")
                records += 1
                if records > bounds.max_records:
                    raise IngestionDecodeError("JSONL stream exceeds max_records")
                yield record
    except IngestionDecodeError:
        raise
    except EOFError as error:
        raise IngestionFormatError("truncated gzip JSONL stream") from error
    except (OSError, zlib.error) as error:
        raise IngestionFormatError("gzip JSONL stream could not be decoded") from error


def _read_bounded_line(
    reader: gzip.GzipFile,
    *,
    max_record_bytes: int,
    max_decompressed_remaining: int,
) -> tuple[bytes, int]:
    """Read one newline-terminated record with chunked accumulation."""
    if max_decompressed_remaining <= 0:
        raise IngestionDecodeError("JSONL stream exceeds max_decompressed_bytes")
    parts: list[bytes] = []
    total = 0
    while True:
        remaining_record = max_record_bytes - total
        remaining_stream = max_decompressed_remaining - total
        if remaining_record <= 0 or remaining_stream <= 0:
            raise IngestionDecodeError(
                "JSONL record exceeds max_record_bytes"
                if remaining_record <= 0
                else "JSONL stream exceeds max_decompressed_bytes"
            )
        to_read = min(_CHUNK, remaining_record + 1, remaining_stream + 1)
        chunk = reader.readline(to_read)
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
        if total > max_record_bytes:
            raise IngestionDecodeError("JSONL record exceeds max_record_bytes")
        if total > max_decompressed_remaining:
            raise IngestionDecodeError("JSONL stream exceeds max_decompressed_bytes")
        if chunk.endswith(b"\n"):
            break
        if len(chunk) < to_read:
            break
    return b"".join(parts), total
