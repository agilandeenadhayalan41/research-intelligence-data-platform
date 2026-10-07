"""Offline tests for bounded JSONL.GZ streaming decode."""

from __future__ import annotations

import gzip
import io

import pytest

from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.errors import IngestionDecodeError, IngestionFormatError
from research_platform.ingestion.jsonl import iter_jsonl_gz_records


def _gz(payload: bytes) -> io.BytesIO:
    return io.BytesIO(gzip.compress(payload))


def test_streams_records_under_limits() -> None:
    body = b'{"id":"W1"}\n{"id":"W2"}\n'
    records = list(iter_jsonl_gz_records(_gz(body)))
    assert [dict(item) for item in records] == [{"id": "W1"}, {"id": "W2"}]


def test_rejects_max_records() -> None:
    body = b'{"id":"W1"}\n{"id":"W2"}\n{"id":"W3"}\n'
    limits = JsonlDecodeLimits(max_records=2, max_decompressed_bytes=10_000, max_record_bytes=1000)
    with pytest.raises(IngestionDecodeError, match="max_records"):
        list(iter_jsonl_gz_records(_gz(body), limits=limits))


def test_rejects_max_record_bytes_without_full_materialization() -> None:
    # One oversized line: use a small limit so the fixture stays tiny.
    line = b'{"id":"' + (b"x" * 200) + b'"}\n'
    limits = JsonlDecodeLimits(max_record_bytes=64, max_decompressed_bytes=10_000, max_records=10)
    with pytest.raises(IngestionDecodeError, match="max_record_bytes"):
        list(iter_jsonl_gz_records(_gz(line), limits=limits))


def test_rejects_max_decompressed_bytes_gzip_bomb_style() -> None:
    # Highly compressible payload; compressed fixture stays small.
    payload = (b'{"k":"' + (b"a" * 1000) + b'"}\n') * 40
    compressed = gzip.compress(payload)
    assert len(compressed) < 5_000
    limits = JsonlDecodeLimits(
        max_decompressed_bytes=8_000,
        max_records=10_000,
        max_record_bytes=50_000,
    )
    with pytest.raises(IngestionDecodeError, match="max_decompressed_bytes"):
        list(iter_jsonl_gz_records(io.BytesIO(compressed), limits=limits))


def test_rejects_malformed_utf8() -> None:
    body = b'{"id":"\xff"}\n'
    with pytest.raises(IngestionDecodeError, match="UTF-8"):
        list(iter_jsonl_gz_records(_gz(body)))


def test_rejects_malformed_json() -> None:
    body = b"{not-json\n"
    with pytest.raises(IngestionDecodeError, match="malformed JSON"):
        list(iter_jsonl_gz_records(_gz(body)))


def test_rejects_truncated_gzip() -> None:
    full = gzip.compress(b'{"id":"W1"}\n')
    truncated = full[: max(8, len(full) // 3)]
    with pytest.raises(IngestionFormatError):
        list(iter_jsonl_gz_records(io.BytesIO(truncated)))


def test_rejects_non_object_json_record() -> None:
    body = b"[1,2,3]\n"
    with pytest.raises(IngestionDecodeError, match="object"):
        list(iter_jsonl_gz_records(_gz(body)))
