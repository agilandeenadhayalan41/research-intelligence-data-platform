"""Bounded streaming sampler for JSONL and gzip-compressed JSONL sources."""

import gzip
import json
import zlib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal

from research_platform.sources.openalex.profile_models import (
    EvidenceType,
    FieldProfile,
    OpenAlexDecompressionError,
    OpenAlexEmptySourceError,
    OpenAlexMalformedJSONLError,
    OpenAlexProfileLimitError,
    ProfileBudget,
    ProfilingLimits,
    child_path,
    sampled_count_evidence,
)

# Objects whose keys are data rather than schema (OpenAlex abstract word index).
DYNAMIC_KEY_OBJECTS = frozenset({"abstract_inverted_index"})


_DRAIN_CHUNK = 64 * 1024


@dataclass(frozen=True)
class JsonlSample:
    """Sampled observations from at most ``max_records`` leading records.

    ``total_record_count`` and ``total_decoded_bytes`` are set only when EOF was
    reached (by the sample itself or by the optional drain); drained lines are
    counted by newline, not parsed.
    """

    compression: Literal["gzip"] | None
    sampled_record_count: int
    reached_eof: bool
    decoded_bytes: int
    fields: tuple[FieldProfile, ...]
    duplicate_key_records: int = 0
    total_record_count: int | None = None
    total_decoded_bytes: int | None = None
    drained: bool = False
    unterminated_final_line: bool = False


def sample_jsonl(
    stream: BinaryIO,
    *,
    compression: Literal["gzip"] | None,
    max_records: int,
    max_decoded_bytes: int,
    limits: ProfilingLimits = ProfilingLimits(),
    drain_to_eof: bool | None = None,
    max_drain_decoded_bytes: int | None = None,
    _budget: ProfileBudget | None = None,
) -> JsonlSample:
    """Stream leading JSONL records without materializing the payload.

    The caller owns ``stream``. Decompression wrappers created here are closed on
    success and failure. Decoded accounting includes the EOF probe; at most one
    additional byte is consumed to detect overflow. Structural preflight bounds
    record allocation before JSON decoding. Time checks are cooperative.

    Without ``drain_to_eof`` the sampler stops after the record bound and does not
    read to EOF. With it, the remainder is read in bounded chunks (at most
    ``max_drain_decoded_bytes`` decoded bytes), newline-terminated lines are
    counted (a blank line is rejected, as in the sample), and gzip's CRC32/ISIZE
    trailer check runs at the member end. ``drain_to_eof`` and
    ``max_drain_decoded_bytes`` default to ``limits`` and are validated by
    ``ProfilingLimits`` like every other bound.
    """
    if compression not in {"gzip", None}:
        raise ValueError("JSONL compression must be gzip or None")
    overrides: dict[str, object] = {
        "max_profile_records": max_records,
        "max_decoded_sample_bytes": max_decoded_bytes,
    }
    if drain_to_eof is not None:
        overrides["drain_jsonl_to_eof"] = drain_to_eof
    if max_drain_decoded_bytes is not None:
        overrides["max_drain_decoded_bytes"] = max_drain_decoded_bytes
    limits = ProfilingLimits.model_validate({**limits.model_dump(), **overrides})
    drain_to_eof = limits.drain_jsonl_to_eof
    drain_limit = limits.max_drain_decoded_bytes
    budget = _budget if _budget is not None else ProfileBudget(limits)

    reader: BinaryIO = (
        gzip.GzipFile(fileobj=stream, mode="rb")  # type: ignore[assignment]
        if compression == "gzip"
        else stream
    )
    observer = _Observer(budget)
    decoded = 0
    records = 0
    duplicate_records = 0
    reached_eof = False
    drained = False
    total_records: int | None = None
    total_decoded: int | None = None
    unterminated = False
    try:
        while records < max_records:
            budget.check()
            line = _read_line(
                reader, min(max_decoded_bytes - decoded, limits.max_record_bytes)
            )
            budget.check()
            if not line:
                reached_eof = True
                break
            decoded += len(line)
            _preflight_record(line, budget)
            record, had_duplicates = _decode_record(line)
            duplicate_records += 1 if had_duplicates else 0
            budget.check()
            observer.observe_record(record)
            records += 1
        probe = b""
        if not reached_eof:
            budget.check()
            probe = _read_line(reader, 1, probe=True)
            budget.check()
            decoded += len(probe)
            if decoded > max_decoded_bytes:
                raise OpenAlexProfileLimitError("JSONL sample exceeds the decoded byte limit")
            reached_eof = not probe
        if reached_eof:
            total_records = records
            total_decoded = decoded
        elif drain_to_eof and records:
            newlines, drained_bytes, last = _drain(reader, probe, drain_limit, budget)
            unterminated = last not in (b"", b"\n")
            total_records = records + newlines + (1 if unterminated else 0)
            total_decoded = decoded - len(probe) + drained_bytes
            reached_eof = True
            drained = True
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
        duplicate_key_records=duplicate_records,
        total_record_count=total_records,
        total_decoded_bytes=total_decoded,
        drained=drained,
        unterminated_final_line=unterminated,
    )


def _drain(
    reader: BinaryIO, probe: bytes, limit: int, budget: ProfileBudget
) -> tuple[int, int, bytes]:
    """Count newlines in the decoded remainder (which starts with ``probe``).

    The last sampled line ended with a newline, so the carried tail starts as
    ``b"\\n"``. An empty line (``\\n\\n`` or ``\\n\\r\\n``) anywhere in the remainder,
    including across chunk boundaries, is rejected as the sample rejects it, so a
    blank line is never counted as a record.
    """
    tail = b"\n"
    newlines = 0
    drained = 0
    last = b""
    pending = probe
    while True:
        if pending:
            drained += len(pending)
            if drained > limit:
                raise OpenAlexProfileLimitError("JSONL drain exceeds its decoded byte limit")
            window = tail + pending
            if b"\n\n" in window or b"\n\r\n" in window:
                raise OpenAlexMalformedJSONLError("JSONL contains a blank line")
            tail = window[-2:]
            newlines += pending.count(b"\n")
            last = pending[-1:]
        budget.check()
        pending = _read_chunk(reader, min(_DRAIN_CHUNK, limit - drained + 1))
        budget.check()
        if not pending:
            return newlines, drained, last


def _preflight_record(line: bytes, budget: ProfileBudget) -> None:
    """Bound JSON structure without allocating its object tree; decoding validates syntax."""
    depth = 0
    in_string = False
    escaped = False
    in_scalar = False
    for index, char in enumerate(line):
        if index % 4096 == 0:
            budget.check()
        if in_string:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                in_string = False
            continue
        if char == 34:
            budget.visit()
            in_string = True
            in_scalar = False
        elif char in (91, 123):
            budget.visit()
            depth += 1
            if depth > budget.limits.max_nesting_depth:
                raise OpenAlexProfileLimitError("JSONL record exceeds the nesting limit")
            in_scalar = False
        elif char in (93, 125):
            depth -= 1
            in_scalar = False
        elif char in (9, 10, 13, 32, 44, 58):
            in_scalar = False
        elif not in_scalar:
            budget.visit()
            in_scalar = True
    budget.check()


def _read_chunk(reader: BinaryIO, size: int) -> bytes:
    try:
        return reader.read(size)
    except EOFError:
        raise OpenAlexDecompressionError("gzip stream is truncated") from None
    except (gzip.BadGzipFile, zlib.error):
        raise OpenAlexDecompressionError("gzip stream cannot be decompressed") from None


def _read_line(reader: BinaryIO, remaining: int, *, probe: bool = False) -> bytes:
    try:
        line = reader.read(1) if probe else reader.readline(remaining + 1)
    except EOFError:
        raise OpenAlexDecompressionError("gzip stream is truncated") from None
    except (gzip.BadGzipFile, zlib.error):
        raise OpenAlexDecompressionError("gzip stream cannot be decompressed") from None
    if not probe and len(line) > remaining:
        raise OpenAlexProfileLimitError("JSONL record or sample exceeds its decoded byte limit")
    return line


def _decode_record(line: bytes) -> tuple[dict[str, Any], bool]:
    """Decode one strict JSON object line; report whether a dynamic-key map repeated a key.

    Duplicate keys are tolerated only directly inside a top-level
    ``DYNAMIC_KEY_OBJECTS`` member (real OpenAlex abstracts repeat a word key in
    rare records; ``json`` keeps the last value). Anywhere else they are rejected.
    """
    content = line.rstrip(b"\r\n")
    if not content.strip():
        raise OpenAlexMalformedJSONLError("JSONL contains a blank line")
    with_duplicates: list[dict[str, Any]] = []

    def collect(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        duplicated = False
        for key, value in pairs:
            if key in result:
                duplicated = True
            result[key] = value
        if duplicated:
            with_duplicates.append(result)
        return result

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise OpenAlexMalformedJSONLError("JSONL line is not valid UTF-8 JSON") from None
    try:
        record = json.loads(text, object_pairs_hook=collect, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise OpenAlexMalformedJSONLError("JSONL line is not valid UTF-8 JSON") from None
    if not isinstance(record, dict):
        raise OpenAlexMalformedJSONLError("JSONL line is not a JSON object")
    for container in with_duplicates:
        if not any(record.get(name) is container for name in DYNAMIC_KEY_OBJECTS):
            raise OpenAlexMalformedJSONLError(
                "JSONL object has a duplicate key outside abstract_inverted_index"
            )
    return record, bool(with_duplicates)


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
    def __init__(self, budget: ProfileBudget) -> None:
        self._budget = budget
        self._types: dict[str, set[str]] = defaultdict(set)
        self._present: dict[str, int] = defaultdict(int)
        self._nulls: dict[str, int] = defaultdict(int)
        self._object_counts: dict[str, int] = defaultdict(int)
        self._parents: dict[str, str] = {}
        self._repeated: set[str] = set()
        self._maps: set[str] = set()

    def observe_record(self, record: dict[str, Any]) -> None:
        stack = [self._children("", record)]
        while stack:
            self._budget.check()
            try:
                path, value = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            self._record(path, value)
            if isinstance(value, (dict, list)):
                stack.append(self._children(path, value))

    def _children(self, path: str, value: object) -> Iterator[tuple[str, object]]:
        if isinstance(value, dict) and path in DYNAMIC_KEY_OBJECTS:
            self._maps.add(path)
            value_path = f"{path}{{value}}"
            self._repeated.add(value_path)
            for child in value.values():
                yield value_path, child
        elif isinstance(value, dict):
            self._object_counts[path] += 1
            for key, child in value.items():
                member_path = child_path(path, key)
                self._parents.setdefault(member_path, path)
                yield member_path, child
        elif isinstance(value, list):
            element_path = f"{path}[]"
            self._repeated.add(element_path)
            for child in value:
                yield element_path, child

    def _record(self, path: str, value: object) -> None:
        if path not in self._types and len(self._types) >= self._budget.limits.max_schema_fields:
            raise OpenAlexProfileLimitError("JSONL sample exceeds the field path limit")
        self._types[path].add(_json_type(value))
        self._present[path] += 1
        if value is None:
            self._nulls[path] += 1

    def fields(self) -> tuple[FieldProfile, ...]:
        profiles = []
        for path in sorted(self._types):
            self._budget.check()
            types = self._types[path]
            missing = None
            if path not in self._repeated:
                missing = self._object_counts[self._parents[path]] - self._present[path]
            nulls = self._nulls[path]
            profiles.append(
                FieldProfile(
                    path=path,
                    data_type="|".join(sorted(types)),
                    kind="map" if path in self._maps else _kind(types),
                    nullable=True if nulls or missing else None,
                    evidence=EvidenceType.SAMPLED_OBSERVATION,
                    sampled_present_count=self._present[path],
                    sampled_missing_count=missing,
                    sampled_null_count=nulls,
                    sampled_evidence=sampled_count_evidence(self._present[path], missing, nulls),
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
