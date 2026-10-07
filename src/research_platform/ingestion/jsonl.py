"""Bounded streaming decode of landed OpenAlex Works JSONL.GZ objects."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator, Mapping
from typing import BinaryIO

from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError

_CHUNK = 64 * 1024


def iter_jsonl_gz_records(stream: BinaryIO) -> Iterator[Mapping[str, object]]:
    """Yield one JSON object per line from a gzip-compressed JSONL stream.

    Does not materialize the full decompressed payload. The caller owns ``stream``.
    """
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as reader:
            while True:
                line = reader.readline(_CHUNK)
                if not line:
                    break
                # Allow long lines by continuing until newline if chunk-limited.
                while not line.endswith(b"\n") and line:
                    more = reader.readline(_CHUNK)
                    if not more:
                        break
                    line += more
                    if len(line) > 16 * 1024 * 1024:
                        raise IngestionDecodeError("JSONL record exceeds 16 MiB decode bound")
                text = line.decode("utf-8").strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as error:
                    raise IngestionDecodeError("malformed JSONL record") from error
                if not isinstance(record, Mapping):
                    raise IngestionDecodeError("JSONL record must be an object")
                yield record
    except OSError as error:
        raise IngestionFormatError("gzip JSONL stream could not be decoded") from error
