"""Bounded streaming sampler for JSONL and gzip-compressed JSONL sources."""

import gzip
import json
import zlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal

from research_platform.sources.openalex.profile_models import (
    EvidenceType,
    FieldProfile,
    OpenAlexDecompressionError,
    OpenAlexEmptySourceError,
    OpenAlexMalformedJSONLError,
    OpenAlexProfileLimitError,
)

_MAX_FIELD_PATHS = 10_000


@dataclass(frozen=True)
class JsonlSample:
    """Sampled observations from at most ``max_records`` leading records."""

    compression: Literal["gzip"] | None
    sampled_record_count: int
    reached_eof: bool
    decoded_bytes: int
    fields: tuple[FieldProfile, ...]


def sample_jsonl(
    stream: BinaryIO,
    *,
    compression: Literal["gzip"] | None,
    max_records: int,
    max_decoded_bytes: int,
) -> JsonlSample:
    """Stream leading JSONL records without reading to EOF or materializing the payload.

    The caller owns ``stream``. Decompression wrappers created here are closed on
    success and failure. At most ``max_decoded_bytes`` decoded bytes are consumed;
    a longer line fails explicitly instead of being truncated.
    """
    if compression not in {"gzip", None}:
        raise ValueError("JSONL compression must be gzip or None")
    if type(max_records) is not int or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    if type(max_decoded_bytes) is not int or max_decoded_bytes <= 0:
        raise ValueError("max_decoded_bytes must be a positive integer")

    reader: BinaryIO = (
        gzip.GzipFile(fileobj=stream, mode="rb")  # type: ignore[assignment]
        if compression == "gzip"
        else stream
    )
    observer = _Observer()
    decoded = 0
    records = 0
    reached_eof = False
    try:
        while records < max_records:
            line = _read_line(reader, max_decoded_bytes - decoded)
            if not line:
                reached_eof = True
                break
            decoded += len(line)
            observer.observe_record(_decode_record(line))
            records += 1
        if not reached_eof and _read_line(reader, 1, probe=True) == b"":
            reached_eof = True
    finally:
        if reader is not stream:
            reader.close()
    if records == 0:
        raise OpenAlexEmptySourceError("JSONL source contains no records")
    return JsonlSample(
        compression=compression,
        sampled_record_count=records,
        reached_eof=reached_eof,
        decoded_bytes=decoded,
        fields=observer.fields(),
    )


def _read_line(reader: BinaryIO, remaining: int, *, probe: bool = False) -> bytes:
    try:
        line = reader.read(1) if probe else reader.readline(remaining + 1)
    except EOFError:
        raise OpenAlexDecompressionError("gzip stream is truncated") from None
    except (gzip.BadGzipFile, zlib.error):
        raise OpenAlexDecompressionError("gzip stream cannot be decompressed") from None
    if not probe and len(line) > remaining:
        raise OpenAlexProfileLimitError("JSONL sample exceeds the decoded byte limit")
    return line


def _decode_record(line: bytes) -> dict[str, Any]:
    content = line.rstrip(b"\r\n")
    if not content.strip():
        raise OpenAlexMalformedJSONLError("JSONL contains a blank line")
    try:
        record = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OpenAlexMalformedJSONLError("JSONL line is not valid UTF-8 JSON") from None
    if not isinstance(record, dict):
        raise OpenAlexMalformedJSONLError("JSONL line is not a JSON object")
    return record


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


class _Observer:
    def __init__(self) -> None:
        self._types: dict[str, set[str]] = defaultdict(set)
        self._present: dict[str, int] = defaultdict(int)
        self._nulls: dict[str, int] = defaultdict(int)
        self._object_counts: dict[str, int] = defaultdict(int)
        self._parents: dict[str, str] = {}
        self._array_elements: set[str] = set()

    def observe_record(self, record: dict[str, Any]) -> None:
        stack: list[tuple[str, object]] = [("", record)]
        while stack:
            path, value = stack.pop()
            if isinstance(value, dict):
                self._object_counts[path] += 1
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else key
                    self._record(child_path, child)
                    self._parents.setdefault(child_path, path)
                    stack.append((child_path, child))
            elif isinstance(value, list):
                element_path = f"{path}[]"
                self._array_elements.add(element_path)
                for element in value:
                    self._record(element_path, element)
                    stack.append((element_path, element))

    def _record(self, path: str, value: object) -> None:
        if path not in self._types and len(self._types) >= _MAX_FIELD_PATHS:
            raise OpenAlexProfileLimitError("JSONL sample exceeds the field path limit")
        self._types[path].add(_json_type(value))
        self._present[path] += 1
        if value is None:
            self._nulls[path] += 1

    def fields(self) -> tuple[FieldProfile, ...]:
        profiles = []
        for path in sorted(self._types):
            types = self._types[path]
            missing = None
            if path not in self._array_elements:
                missing = self._object_counts[self._parents[path]] - self._present[path]
            nulls = self._nulls[path]
            profiles.append(
                FieldProfile(
                    path=path,
                    data_type="|".join(sorted(types)),
                    kind=_kind(types),
                    nullable=True if nulls or missing else None,
                    evidence=EvidenceType.SAMPLED_OBSERVATION,
                    sampled_present_count=self._present[path],
                    sampled_missing_count=missing,
                    sampled_null_count=nulls,
                )
            )
        return tuple(profiles)


def _kind(types: set[str]) -> Literal["scalar", "struct", "list"]:
    if "object" in types:
        return "struct"
    if "array" in types:
        return "list"
    return "scalar"


__all__ = ["JsonlSample", "sample_jsonl"]
