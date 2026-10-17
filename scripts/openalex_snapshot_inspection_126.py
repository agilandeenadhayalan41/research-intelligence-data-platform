"""Opt-in, bounded inspection of the real public OpenAlex snapshot (DE-05, #126).

Not part of ``make test`` or CI. Every network run needs ``--allow-network`` and a
session byte ledger outside the repository::

    python scripts/openalex_snapshot_inspection_126.py --allow-network \\
        --budget-ledger <ledger.json> --out <scratch dir> \\
        --expect-snapshot-date YYYY-MM-DD [--pair auto|YYYY-MM-DD/NNNN] \\
        [--max-total-bytes 12000000] [--max-requests 60] [--keep-raw]

    python scripts/openalex_snapshot_inspection_126.py --from-raw <out>/raw \\
        --budget-ledger <ledger.json> --out <scratch dir> --expect-snapshot-date YYYY-MM-DD

    python scripts/openalex_snapshot_inspection_126.py --init-ledger-only \\
        --budget-ledger <ledger.json> --ledger-seed-bytes N

It reads only ``https://openalex.s3.amazonaws.com`` anonymously (no credentials, no
cloud project): allow-listed listings, the three text files, version listings, HEAD
checksums, the combined and Works manifests (the latter through the unchanged
``OpenAlexConnector``), one JSONL/Parquet Works pair downloaded once, and If-Match
Range reads of one large Parquet footer and of a 65,536-byte deletion-ledger prefix.
Every body byte is charged to one ``SnapshotByteBudget``: at most 25,000,000 bytes
per object (cumulative per key), ``--max-total-bytes`` per run (ceiling
100,000,000), a request cap, and a size known before any non-listing GET. The
ledger reserves the run's cap before any request and settles the bytes actually
read in a ``finally`` block; a run that would push the session above 100,000,000
bytes is refused. No object above the per-object cap is requested whole.

Every response is recorded under ``<out>/raw/`` (allow-listed headers and body);
analysis reads only local bytes, and ``--from-raw`` regenerates every output with
no socket and zero network bytes. Outputs in ``--out`` (outside the repository):
``snapshot_evidence.json``, ``works_manifest_digest.json`` and
``works_redacted_sample.json`` (aggregates plus five masked, pseudonymised records).
Stdout is one JSON line without record values. The script deletes only what it
created: its own ``de05-tmp-*`` directory, and ``raw/`` only when ``raw/`` carries
the script's marker file; an existing unmarked ``<out>/raw`` is refused.

Exit codes: 0 inspected; 1 failed (``error_category``, including ``RedactionFailed``,
``RightsChanged``, checksum mismatches and ``LookupError`` for a ``--pair`` that the
Works manifests do not list); 2 refused or invalid (``NetworkOptInRequired``,
``BudgetLedgerRequired``, ``SessionBudgetExhausted``, ``InvalidLimits``,
``RawNotOwned``, ``--out`` inside the repository); 3 ``SnapshotChanged``.

Sections: errors and limits; budget and session ledger; record and replay; transport
and allow-list; checksums; evidence models; summarisers; rights and cadence;
redaction; fidelity; canonical compatibility; fixture profile; renderer; CLI.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import copy
import dataclasses
import fcntl
import gzip
import hashlib
import http.client
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import socket
import statistics
import struct
import sys
import tempfile
import zlib
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import quote
from uuid import UUID
from xml.etree import ElementTree

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from research_platform.analytics.bigquery.sandbox.live import evidence_dir
from research_platform.config import SampleSelectionConfig
from research_platform.sources.openalex.connector import (
    _MANIFEST_LIMIT,
    _MAX_TIMEOUT_SECONDS,
    AnonymousOpenAlexHTTPClient,
    OpenAlexAccessDeniedError,
    OpenAlexConnector,
    OpenAlexConnectorError,
    OpenAlexEndpointUnavailableError,
    OpenAlexNetworkError,
    OpenAlexSizeLimitError,
    OpenAlexTimeoutError,
    OpenAlexTruncatedError,
    _header,
    _open_https,
    _parse_content_length,
    _public_path,
    parse_etag,
    parse_version_id,
)
from research_platform.sources.openalex.manifest import parse_openalex_works_manifest
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.profile_models import (
    FieldProfile,
    OpenAlexProfileError,
    OpenAlexProfileLimitError,
    OpenAlexSourceProfile,
    ProfilingLimits,
)
from research_platform.sources.openalex.profile_parquet import inspect_parquet_footer
from research_platform.sources.openalex.profiling import profile_openalex_asset
from research_platform.sources.openalex.sample import select_openalex_works_sample

REPO = Path(__file__).resolve().parents[1]

# =====================================================================================
# Errors and limits
# =====================================================================================

MAX_INSPECTION_OBJECT_BYTES: Final = 25_000_000
"""User cap "<= 25 MB each" (decimal; equal to ``profile_models.MAX_FILE_SIZE_BYTES``)."""
MAX_SESSION_BYTES: Final = 100_000_000
"""User cap "<= 100 MB in total"; per-run ceiling and session ledger ceiling."""
MAX_INSPECTION_REQUESTS: Final = 200
MAX_LISTING_BYTES: Final = 1_000_000
MAX_LISTING_KEYS: Final = 1_000
MAX_LISTING_PAGES: Final = 6
MAX_VERSION_KEYS: Final = 50
MAX_TEXT_BYTES: Final = 1_000_000
MAX_LEDGER_PREFIX_BYTES: Final = 65_536
EVIDENCE_LABEL: Final = "LOCAL bounded inspection"
ENVIRONMENT: Final = "local container; anonymous HTTPS; no cloud project or credentials"
HOST: Final = "openalex.s3.amazonaws.com"
FOOTER_LIMITS: Final = ProfilingLimits()
"""``max_parquet_footer_bytes`` bounds the footer Range (plus its 8-byte trailer)."""
PAIR_MAX_FILE_BYTES: Final = 2_000_000
PAIR_MIN_RECORDS: Final = 1_000
FIXTURE_RECORDS: Final = 5
FIXTURE_MAX_RECORD_BYTES: Final = 20_000
JSONL_PROFILE_LIMITS: Final = ProfilingLimits(
    max_profile_records=300,
    max_profile_nodes=1_000_000,
    max_profile_seconds=60,
    drain_jsonl_to_eof=True,
)
PARQUET_PROFILE_LIMITS: Final = ProfilingLimits(
    max_profile_records=300,
    max_profile_nodes=1_000_000,
    max_profile_seconds=60,
    parquet_values="footer_only",
)
AUTO_PAIR_RULE: Final = (
    "latest updated_date pair with both files <= 2,000,000 bytes and >= 1,000 records"
)


class OpenAlexBudgetExceededError(OpenAlexSizeLimitError):
    """A read would exceed a per-object, per-run, request or session cap."""


class OpenAlexChecksumMismatchError(OpenAlexConnectorError):
    """Retrieved bytes differ from a checksum S3 publishes for the object."""


class OpenAlexSnapshotChangedError(OpenAlexConnectorError):
    """An object or the release changed while it was being inspected."""


class SnapshotDateMismatch(OpenAlexSnapshotChangedError):
    """A manifest date differs from another manifest or from ``--expect-snapshot-date``."""


class RedactionError(ValueError):
    """A record has a string the fail-closed redaction policy cannot classify."""


class RightsChanged(ValueError):
    """The snapshot's LICENSE.txt no longer states CC0 1.0 Universal."""


class SessionBudgetExhausted(OpenAlexBudgetExceededError):
    """The session ledger has no room for this run's reservation."""


class RawNotOwned(ValueError):
    """An existing ``raw/`` directory carries no marker written by this script."""


class InspectionLimits(BaseModel):
    """Validated per-run caps; the user's ceilings cannot be raised by configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    max_object_bytes: int = Field(
        default=MAX_INSPECTION_OBJECT_BYTES, strict=True, gt=0, le=MAX_INSPECTION_OBJECT_BYTES
    )
    max_total_bytes: int = Field(default=12_000_000, strict=True, gt=0, le=MAX_SESSION_BYTES)
    max_requests: int = Field(default=60, strict=True, gt=0, le=MAX_INSPECTION_REQUESTS)
    timeout_seconds: float = Field(default=30, gt=0, le=_MAX_TIMEOUT_SECONDS)


# =====================================================================================
# Budget and session ledger
# =====================================================================================


class SessionByteLedger:
    """A JSON file outside the repository: reserve before a run, settle after it.

    Schema ``{"ceiling_bytes": int, "entries": [{"id", "by", "started_at",
    "reserved_bytes", "used_bytes" | null}]}``. Used bytes are the settled
    ``used_bytes`` plus the ``reserved_bytes`` of unsettled entries, so a killed run
    stays counted at its full reservation. Every update holds an exclusive ``flock``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _used(data: Mapping[str, Any]) -> int:
        return sum(
            entry["reserved_bytes"] if entry.get("used_bytes") is None else entry["used_bytes"]
            for entry in data["entries"]
        )

    def _update(self, change: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
        if not self.path.exists():
            raise SessionBudgetExhausted("session byte ledger does not exist")
        with self.path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                data = json.loads(handle.read())
                if (
                    not isinstance(data, dict)
                    or type(data.get("ceiling_bytes")) is not int
                    or not 0 < data["ceiling_bytes"] <= MAX_SESSION_BYTES
                    or not isinstance(data.get("entries"), list)
                ):
                    raise SessionBudgetExhausted("session byte ledger is malformed")
                updated = change(data)
                if updated is not None:
                    handle.seek(0)
                    handle.truncate()
                    handle.write(json.dumps(updated, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    data = updated
                return data
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def create(self, seed_bytes: int, *, by: str = "pre-PR research") -> None:
        if type(seed_bytes) is not int or not 0 <= seed_bytes <= MAX_SESSION_BYTES:
            raise ValueError("ledger seed must be between 0 and 100000000 bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "ceiling_bytes": MAX_SESSION_BYTES,
                "entries": [{
                    "id": 1, "by": by, "started_at": None,
                    "reserved_bytes": seed_bytes, "used_bytes": seed_bytes,
                }],
            }, indent=2, sort_keys=True) + "\n")

    def used(self) -> int:
        return self._used(self._update(lambda data: None))

    def reserve(self, reserved_bytes: int, *, by: str, started_at: str) -> int:
        if type(reserved_bytes) is not int or reserved_bytes < 0:
            raise ValueError("reservation must be a non-negative integer")
        result: dict[str, int] = {}

        def change(data: dict[str, Any]) -> dict[str, Any]:
            if self._used(data) + reserved_bytes > data["ceiling_bytes"]:
                raise SessionBudgetExhausted("session byte ledger has no room for this run")
            entry_id = max((entry["id"] for entry in data["entries"]), default=0) + 1
            result["id"] = entry_id
            data["entries"].append({
                "id": entry_id, "by": by[:120], "started_at": started_at,
                "reserved_bytes": reserved_bytes, "used_bytes": None,
            })
            return data

        self._update(change)
        return result["id"]

    def settle(self, entry_id: int, used_bytes: int) -> None:
        def change(data: dict[str, Any]) -> dict[str, Any]:
            for entry in data["entries"]:
                if entry["id"] == entry_id:
                    if entry["used_bytes"] is not None:
                        raise ValueError("ledger entry is already settled")
                    entry["used_bytes"] = used_bytes
                    return data
            raise ValueError("ledger entry does not exist")

        self._update(change)


class SnapshotByteBudget:
    """Request and body-byte accounting shared by every read of one run."""

    def __init__(
        self,
        limits: InspectionLimits = InspectionLimits(),
        *,
        ledger: SessionByteLedger | None = None,
        label: str = "openalex-inspection",
        started_at: str = "",
        reserve: bool = True,
    ) -> None:
        self.limits = InspectionLimits.model_validate(limits.model_dump())
        self.requests = 0
        self.body_bytes = 0
        self.per_key: dict[str, int] = {}
        self._ledger = ledger
        self._closed = False
        self._entry: int | None = None
        if ledger is not None:
            self._entry = ledger.reserve(
                self.limits.max_total_bytes if reserve else 0, by=label, started_at=started_at
            )

    @property
    def remaining_bytes(self) -> int:
        return self.limits.max_total_bytes - self.body_bytes

    def start_request(self) -> None:
        if self._closed:
            raise OpenAlexBudgetExceededError("OpenAlex inspection budget is closed")
        if self.requests >= self.limits.max_requests:
            raise OpenAlexBudgetExceededError("OpenAlex inspection exceeds its request budget")
        self.requests += 1

    def admit(self, key: str, expected: int | None, declared: int | None) -> None:
        """Refuse before reading a body that cannot fit (or contradicts its known size)."""
        if expected is not None and declared is not None and declared != expected:
            raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
        size = declared if declared is not None else expected
        if size is None:
            return
        if self.per_key.get(key, 0) + size > self.limits.max_object_bytes:
            raise OpenAlexBudgetExceededError(
                "OpenAlex object exceeds the per-object inspection byte cap"
            )
        if size > self.remaining_bytes:
            raise OpenAlexBudgetExceededError("OpenAlex inspection would exceed its total byte budget")

    def read_allowance(self, key: str) -> int:
        """Largest next read that leaves room for a one-byte overflow probe."""
        per_object = self.limits.max_object_bytes - self.per_key.get(key, 0)
        return max(0, min(per_object, self.remaining_bytes)) + 1

    def charge(self, key: str, size: int) -> None:
        if size <= 0:
            return
        self.body_bytes += size
        self.per_key[key] = self.per_key.get(key, 0) + size
        if self.per_key[key] > self.limits.max_object_bytes:
            raise OpenAlexBudgetExceededError(
                "OpenAlex object exceeds the per-object inspection byte cap"
            )
        if self.body_bytes > self.limits.max_total_bytes:
            raise OpenAlexBudgetExceededError("OpenAlex inspection would exceed its total byte budget")

    def close(self, *, settle_bytes: int | None = None) -> None:
        if not self._closed:
            self._closed = True
            if self._ledger is not None and self._entry is not None:
                self._ledger.settle(
                    self._entry, self.body_bytes if settle_bytes is None else settle_bytes
                )


# =====================================================================================
# Record and replay
# =====================================================================================

_RECORDED_HEADERS: Final = (
    "Content-Length", "Content-Type", "Content-Range", "ETag", "Last-Modified",
    "x-amz-version-id", "x-amz-checksum-crc64nvme", "x-amz-checksum-type",
)
_TOKEN_QUERY = re.compile(r"(continuation-token=)[^&]*")
RAW_MARKER: Final = ".de05-inspection-raw"
"""Written into every ``raw/`` this script creates; only a marked ``raw/`` is cleared or deleted."""


def raw_is_owned(raw: Path) -> bool:
    return (Path(raw) / RAW_MARKER).is_file()


def claim_raw(raw: Path) -> bool:
    """Create ``raw`` with the marker (True), or accept an existing marked one (False).

    An existing ``raw`` without the marker belongs to someone else: ``RawNotOwned``.
    """
    raw = Path(raw)
    if raw.exists():
        if not raw_is_owned(raw):
            raise RawNotOwned("raw directory was not created by this script")
        return False
    raw.mkdir(parents=True)
    (raw / RAW_MARKER).write_text(
        "Created by scripts/openalex_snapshot_inspection_126.py (DE-05, #126).\n", encoding="utf-8"
    )
    return True


def redact_path(path: str) -> str:
    """Request paths as logged and committed: continuation tokens are not stored."""
    return _TOKEN_QUERY.sub(r"\1<token>", path)


class Recorder:
    """Writes every response as ``raw/responses/NNNN.meta.json`` + ``NNNN.body``."""

    def __init__(self, raw: Path | None) -> None:
        self.raw = raw
        self.entries: list[dict[str, Any]] = []
        if raw is not None:
            claim_raw(raw)  # never clears a directory this script did not create
            shutil.rmtree(raw / "responses", ignore_errors=True)
            (raw / "responses").mkdir(parents=True, exist_ok=True)

    def start(self, method: str, path: str, byte_range: str | None) -> dict[str, Any]:
        entry = {
            "seq": len(self.entries) + 1, "method": method, "path": redact_path(path),
            "byte_range": byte_range, "status": None, "body_bytes": 0,
            "_path": path, "_handle": None, "_buffer": bytearray(),
        }
        self.entries.append(entry)
        return entry

    def response(self, entry: dict[str, Any], response: Any) -> None:
        entry["status"] = response.status
        if self.raw is None:
            return
        headers = {
            name: value for name in _RECORDED_HEADERS
            if (value := _header(response, name)) is not None
        }
        stem = self.raw / "responses" / f"{entry['seq']:04d}"
        meta = {
            "method": entry["method"], "path": entry["_path"], "byte_range": entry["byte_range"],
            "status": response.status, "headers": headers,
        }
        stem.with_suffix(".meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        entry["_handle"] = stem.with_suffix(".body").open("wb")

    def body(self, entry: dict[str, Any], data: bytes) -> None:
        entry["body_bytes"] += len(data)
        entry["_buffer"].extend(data)
        if entry["_handle"] is not None:
            entry["_handle"].write(data)

    def close(self, entry: dict[str, Any]) -> None:
        if entry["_handle"] is not None:
            entry["_handle"].close()
            entry["_handle"] = None

    def last_body(self) -> bytes:
        """Body of the most recent response (kept in memory for the current run only)."""
        return bytes(self.entries[-1]["_buffer"])

    def release_buffers(self) -> None:
        for entry in self.entries:
            entry["_buffer"] = bytearray()

    def log(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in entry.items() if not key.startswith("_")}
            for entry in self.entries
        ]


class _ReplayResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("OpenAlex replay requires bounded reads")
        return self._body.read(size)

    def close(self) -> None:
        self._body.close()


class LocalReplayClient:
    """Serves recorded responses in order; never opens a socket.

    It is both a ``SnapshotInspectionClient`` opener and a connector ``_Client``.
    """

    def __init__(self, raw: Path) -> None:
        self._queues: dict[tuple[str, str, str | None], deque[Path]] = {}
        for meta_path in sorted((Path(raw) / "responses").glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            key = (meta["method"], meta["path"], meta["byte_range"])
            self._queues.setdefault(key, deque()).append(meta_path)

    def has(self, method: str, path: str, byte_range: str | None = None) -> bool:
        return bool(self._queues.get((method, path, byte_range)))

    def _serve(self, method: str, path: str, byte_range: str | None) -> _ReplayResponse:
        queue = self._queues.get((method, path, byte_range))
        if not queue:
            raise OpenAlexNetworkError("OpenAlex replay has no recorded response for this request")
        meta_path = queue.popleft()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".body"))
        return _ReplayResponse(meta["status"], meta["headers"], body_path.read_bytes())

    def __call__(self, method: str, path: str, *, timeout: float, headers: dict[str, str]) -> Any:
        byte_range = headers.get("Range", "").removeprefix("bytes=") or None
        return self._serve(method, path, byte_range)

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> Any:
        return self._serve("GET", _public_path(uri), None)


class LocalFileClient:
    """A connector ``_Client`` serving one verified local file (profiling input).

    ``content_type`` is the Content-Type of the GET that retrieved the file, when one
    was recorded; it is served only then, so the profile reports it as UNKNOWN
    otherwise (never an invented constant).
    """

    def __init__(self, path: Path, *, content_type: str | None = None) -> None:
        self._path = Path(path)
        self._content_type = content_type

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> Any:
        body = self._path.read_bytes()
        served = {"Content-Length": str(len(body))}
        if self._content_type:
            served["Content-Type"] = self._content_type
        return _ReplayResponse(200, served, body)


# =====================================================================================
# Transport and allow-list
# =====================================================================================

_TEXT_KEYS = frozenset({"LICENSE.txt", "README.txt", "RELEASE_NOTES.txt"})
_LIST_PREFIXES = frozenset({"", "legacy-data/merged_ids/", "data/jsonl/", "data/parquet/"})
_VERSION_KEYS = frozenset({
    "LICENSE.txt", "RELEASE_NOTES.txt", "data/jsonl/manifest.json", "data/parquet/manifest.json",
    "data/jsonl/works/manifest.json", "data/jsonl/works/deleted_ids.csv.gz",
})
_COMBINED_MANIFEST = re.compile(r"data/(jsonl|parquet)/manifest\.json")
_WORKS_MANIFEST = re.compile(r"data/(jsonl|parquet)/works/manifest\.json")
_DELETIONS = re.compile(r"data/(jsonl|parquet)/works/deleted_ids\.csv\.gz")
_WORKS_PART = re.compile(
    r"data/(?:jsonl/works/updated_date=\d{4}-\d{2}-\d{2}/part_\d{4}\.gz"
    r"|parquet/works/updated_date=\d{4}-\d{2}-\d{2}/part_\d{4}\.parquet)"
)
_PARQUET_PART = re.compile(r"data/parquet/works/updated_date=\d{4}-\d{2}-\d{2}/part_\d{4}\.parquet")
_CONTINUATION = re.compile(r"[A-Za-z0-9+/=_-]{1,1024}")
_CHUNK = 64 * 1024


def _not_allowed() -> OpenAlexEndpointUnavailableError:
    return OpenAlexEndpointUnavailableError("OpenAlex inspection path is not allow-listed")


def _check_key_text(key: str) -> None:
    if (
        not isinstance(key, str)
        or not key.isascii()
        or "%" in key
        or "\\" in key
        or key.startswith("/")
        or any(ord(character) < 33 or ord(character) == 127 for character in key)
        or any(part in {".", ".."} for part in key.split("/"))
    ):
        raise _not_allowed()


def _quote_key(key: str) -> str:
    _check_key_text(key)
    if not key:
        raise _not_allowed()
    return "/" + quote(key, safe="/=")


def _query(pairs: Sequence[tuple[str, str | None]]) -> str:
    parts = []
    for name, value in pairs:
        if value is None:
            parts.append(name)
        else:
            if name == "prefix" and value:
                _check_key_text(value)
            parts.append(f"{name}={quote(value, safe='')}")
    return "/?" + "&".join(parts)


def _status(response: Any, allowed: set[int]) -> None:
    if response.status in allowed:
        return
    if response.status in {401, 403}:
        raise OpenAlexAccessDeniedError("OpenAlex denied anonymous public access")
    raise OpenAlexEndpointUnavailableError(f"OpenAlex endpoint returned HTTP {response.status}")


class _BudgetedResponse:
    """Charges every body byte to the budget (one-byte overflow probe) and records it."""

    def __init__(
        self, response: Any, *, key: str, budget: SnapshotByteBudget,
        recorder: Recorder, entry: dict[str, Any],
    ) -> None:
        self._response = response
        self._key = key
        self._budget = budget
        self._recorder = recorder
        self._entry = entry
        self.status = response.status
        self.headers = response.headers
        self._closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("OpenAlex inspection requires bounded response reads")
        if self._closed:
            return b""
        data = self._response.read(min(size, self._budget.read_allowance(self._key)))
        if data:
            self._recorder.body(self._entry, data)
            try:
                self._budget.charge(self._key, len(data))
            except OpenAlexBudgetExceededError:
                self.close()
                raise
        return data

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._response.close()
            finally:
                self._recorder.close(self._entry)


class BudgetedOpenAlexClient:
    """A ``connector._Client`` wrapper: known size first, every body byte charged.

    ``OpenAlexConnector(client=BudgetedOpenAlexClient(AnonymousOpenAlexHTTPClient(),
    budget, recorder))`` keeps the connector's own URI, size and truncation checks.
    A request is sent only for a URI whose size was registered with ``expect``.
    """

    def __init__(self, inner: Any, budget: SnapshotByteBudget, recorder: Recorder) -> None:
        self._inner = inner
        self._budget = budget
        self._recorder = recorder
        self._expected: dict[str, int] = {}

    def expect(self, uri: str, size: int) -> None:
        _public_path(uri)
        if type(size) is not int or size < 0:
            raise ValueError("expected size must be a non-negative integer")
        if size > self._budget.limits.max_object_bytes:
            raise OpenAlexBudgetExceededError(
                "OpenAlex object exceeds the per-object inspection byte cap"
            )
        self._expected[uri] = size

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> Any:
        path = _public_path(uri)
        expected = self._expected.get(uri)
        if expected is None:
            raise OpenAlexEndpointUnavailableError("size unknown before request")
        self._budget.admit(uri, expected, None)
        self._budget.start_request()
        entry = self._recorder.start("GET", path, None)
        response = self._inner.request(uri, timeout=timeout, headers=headers)
        try:
            self._recorder.response(entry, response)
            if response.status == 200:
                declared = _parse_content_length(_header(response, "Content-Length"))
                self._budget.admit(uri, expected, declared)
            return _BudgetedResponse(
                response, key=uri, budget=self._budget, recorder=self._recorder, entry=entry
            )
        except Exception:
            response.close()
            self._recorder.close(entry)
            raise


@dataclasses.dataclass(frozen=True)
class ListedObject:
    key: str
    size: int
    etag: str | None
    last_modified: str | None
    checksum_algorithm: str | None
    checksum_type: str | None


@dataclasses.dataclass(frozen=True)
class Listing:
    prefix: str
    objects: tuple[ListedObject, ...]
    common_prefixes: tuple[str, ...]
    pages: int


@dataclasses.dataclass(frozen=True)
class ObjectVersion:
    key: str
    version_id: str | None
    is_latest: bool
    last_modified: str | None
    etag: str | None
    size: int


@dataclasses.dataclass(frozen=True)
class ObjectHead:
    key: str
    content_length: int
    etag: str | None
    version_id: str | None
    last_modified: str | None
    crc64nvme: str | None
    checksum_type: str | None


@dataclasses.dataclass(frozen=True)
class FetchedObject:
    key: str
    body: bytes
    etag: str | None
    version_id: str | None
    last_modified: str | None
    sha256: str
    md5_equals_etag: bool | None


class SnapshotInspectionClient:
    """Allow-listed anonymous reads of the snapshot's own non-payload objects."""

    def __init__(
        self,
        budget: SnapshotByteBudget,
        recorder: Recorder | None = None,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._budget = budget
        self._recorder = recorder if recorder is not None else Recorder(None)
        self._open = opener or _open_https
        self._timeout = budget.limits.timeout_seconds

    def get_text(self, key: str, *, expected_size: int | None) -> FetchedObject:
        if key not in _TEXT_KEYS:
            raise _not_allowed()
        return self._get(key, expected_size=expected_size, cap=MAX_TEXT_BYTES)

    def get_combined_manifest(self, content_format: str, *, expected_size: int | None) -> FetchedObject:
        key = f"data/{content_format}/manifest.json"
        if not _COMBINED_MANIFEST.fullmatch(key):
            raise _not_allowed()
        return self._get(key, expected_size=expected_size, cap=_MANIFEST_LIMIT)

    def list_objects(
        self, prefix: str, *, delimiter: bool, max_keys: int = MAX_LISTING_KEYS,
        max_pages: int = 1,
    ) -> Listing:
        if prefix not in _LIST_PREFIXES:
            raise _not_allowed()
        if type(max_keys) is not int or not 1 <= max_keys <= MAX_LISTING_KEYS:
            raise ValueError("max_keys must be between 1 and 1000")
        if type(max_pages) is not int or not 1 <= max_pages <= MAX_LISTING_PAGES:
            raise ValueError("max_pages must be between 1 and 6")
        objects: list[ListedObject] = []
        prefixes: list[str] = []
        token: str | None = None
        pages = 0
        while True:
            query: list[tuple[str, str | None]] = [("list-type", "2"), ("prefix", prefix)]
            if delimiter:
                query.append(("delimiter", "/"))
            query.append(("max-keys", str(max_keys)))
            if token is not None:
                query.append(("continuation-token", token))
            root = self._get_xml(_query(query), key=f"list:{prefix}:{pages}")
            pages += 1
            if _local(root.tag) != "ListBucketResult":
                raise OpenAlexEndpointUnavailableError("OpenAlex listing response is unexpected")
            for element in root:
                name = _local(element.tag)
                if name == "Contents":
                    objects.append(_listed_object(element))
                elif name == "CommonPrefixes":
                    value = _child_text(element, "Prefix")
                    if value is not None:
                        prefixes.append(value)
            truncated = _child_text(root, "IsTruncated")
            token = _child_text(root, "NextContinuationToken")
            if truncated not in {"true", "false"} or (truncated == "true") != (token is not None):
                raise OpenAlexEndpointUnavailableError("OpenAlex listing truncation is inconsistent")
            if truncated == "false":
                break
            if not _CONTINUATION.fullmatch(token or ""):
                raise OpenAlexEndpointUnavailableError("OpenAlex listing continuation is unsafe")
            if pages >= max_pages:
                raise OpenAlexBudgetExceededError("OpenAlex listing exceeds its page limit")
        return Listing(prefix, tuple(objects), tuple(prefixes), pages)

    def list_versions(self, key: str, *, max_keys: int = 10) -> tuple[ObjectVersion, ...]:
        if key not in _VERSION_KEYS:
            raise _not_allowed()
        if type(max_keys) is not int or not 1 <= max_keys <= MAX_VERSION_KEYS:
            raise ValueError("max_keys must be between 1 and 50")
        root = self._get_xml(
            _query([("versions", None), ("prefix", key), ("max-keys", str(max_keys))]),
            key=f"versions:{key}",
        )
        if _local(root.tag) != "ListVersionsResult":
            raise OpenAlexEndpointUnavailableError("OpenAlex version listing is unexpected")
        return tuple(
            ObjectVersion(
                key=key,
                version_id=parse_version_id(_child_text(element, "VersionId")),
                is_latest=_child_text(element, "IsLatest") == "true",
                last_modified=_safe_iso(_child_text(element, "LastModified")),
                etag=parse_etag(_child_text(element, "ETag")),
                size=_size(_child_text(element, "Size")),
            )
            for element in root
            if _local(element.tag) == "Version" and _child_text(element, "Key") == key
        )

    def head(self, key: str) -> ObjectHead:
        if not (
            key in _TEXT_KEYS
            or _WORKS_PART.fullmatch(key)
            or _WORKS_MANIFEST.fullmatch(key)
            or _COMBINED_MANIFEST.fullmatch(key)
            or _DELETIONS.fullmatch(key)
        ):
            raise _not_allowed()
        response = self._send(
            "HEAD", _quote_key(key), key=key, extra={"x-amz-checksum-mode": "ENABLED"}
        )
        try:
            _status(response, {200})
            length = _parse_content_length(_header(response, "Content-Length"))
            if length is None:
                raise OpenAlexEndpointUnavailableError("OpenAlex HEAD has no Content-Length")
            if response.read(1):
                raise OpenAlexEndpointUnavailableError("OpenAlex HEAD returned a body")
            crc = _header(response, "x-amz-checksum-crc64nvme")
            checksum_type = _header(response, "x-amz-checksum-type")
            return ObjectHead(
                key=key,
                content_length=length,
                etag=parse_etag(_header(response, "ETag")),
                version_id=parse_version_id(_header(response, "x-amz-version-id")),
                last_modified=_safe_http_date(_header(response, "Last-Modified")),
                crc64nvme=crc if crc and re.fullmatch(r"[A-Za-z0-9+/]{11}=", crc) else None,
                checksum_type=checksum_type if checksum_type in {"FULL_OBJECT", "COMPOSITE"} else None,
            )
        finally:
            response.close()

    def get_range(self, key: str, start: int, end: int, *, head: ObjectHead) -> bytes:
        """``bytes=start-end`` with ``If-Match``; only Parquet footers and the ledger prefix."""
        size = head.content_length
        if head.key != key or head.etag is None:
            raise ValueError("a range needs the HEAD of the same key")
        if type(start) is not int or type(end) is not int or not 0 <= start <= end < size:
            raise ValueError("range must lie inside the object")
        length = end - start + 1
        if _PARQUET_PART.fullmatch(key):
            if end != size - 1 or length > FOOTER_LIMITS.max_parquet_footer_bytes + 8:
                raise _not_allowed()
        elif key == "data/jsonl/works/deleted_ids.csv.gz":
            if start != 0 or length > MAX_LEDGER_PREFIX_BYTES:
                raise _not_allowed()
        else:
            raise _not_allowed()
        response = self._send(
            "GET", _quote_key(key), key=key, expected=length,
            extra={"Range": f"bytes={start}-{end}", "If-Match": f'"{head.etag}"'},
            byte_range=f"{start}-{end}",
        )
        try:
            if response.status == 412:
                raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
            _status(response, {206})
            if _header(response, "Content-Range") != f"bytes {start}-{end}/{size}":
                raise OpenAlexTruncatedError(
                    "OpenAlex range response does not match the requested range"
                )
            if parse_etag(_header(response, "ETag")) != head.etag:
                raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
            self._budget.admit(key, length, _parse_content_length(_header(response, "Content-Length")))
            body = self._read_all(response, length)
            if len(body) != length:
                raise OpenAlexTruncatedError(
                    "OpenAlex range response does not match the requested range"
                )
            return body
        finally:
            response.close()

    def _get(self, key: str, *, expected_size: int | None, cap: int) -> FetchedObject:
        if type(expected_size) is not int or expected_size < 0:
            raise OpenAlexEndpointUnavailableError("size unknown before request")
        if expected_size > min(cap, self._budget.limits.max_object_bytes):
            raise OpenAlexBudgetExceededError(
                "OpenAlex object exceeds the per-object inspection byte cap"
            )
        response = self._send("GET", _quote_key(key), key=key, expected=expected_size)
        try:
            _status(response, {200})
            self._budget.admit(
                key, expected_size, _parse_content_length(_header(response, "Content-Length"))
            )
            body = self._read_all(response, expected_size)
            if len(body) != expected_size:
                raise OpenAlexTruncatedError("OpenAlex object ended before its listed size")
            etag = parse_etag(_header(response, "ETag"))
            return FetchedObject(
                key=key,
                body=body,
                etag=etag,
                version_id=parse_version_id(_header(response, "x-amz-version-id")),
                last_modified=_safe_http_date(_header(response, "Last-Modified")),
                sha256=hashlib.sha256(body).hexdigest(),
                md5_equals_etag=md5_matches_etag(
                    hashlib.md5(body, usedforsecurity=False).hexdigest(), etag
                ),
            )
        finally:
            response.close()

    def _get_xml(self, path: str, *, key: str) -> ElementTree.Element:
        response = self._send("GET", path, key=key)
        try:
            _status(response, {200})
            declared = _parse_content_length(_header(response, "Content-Length"))
            if declared is not None and declared > MAX_LISTING_BYTES:
                raise OpenAlexBudgetExceededError("OpenAlex listing exceeds its byte limit")
            self._budget.admit(key, None, declared)
            body = self._read_all(response, MAX_LISTING_BYTES)
        finally:
            response.close()
        if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
            raise OpenAlexEndpointUnavailableError("OpenAlex listing contains a DTD")
        try:
            return ElementTree.fromstring(body)
        except ElementTree.ParseError:
            raise OpenAlexEndpointUnavailableError("OpenAlex listing is not valid XML") from None

    def _send(
        self, method: str, path: str, *, key: str, expected: int | None = None,
        extra: dict[str, str] | None = None, byte_range: str | None = None,
    ) -> _BudgetedResponse:
        if expected is not None:
            self._budget.admit(key, expected, None)
        self._budget.start_request()
        entry = self._recorder.start(method, path, byte_range)
        headers = {"Accept-Encoding": "identity", **(extra or {})}
        try:
            response = self._open(method, path, timeout=self._timeout, headers=headers)
        except (TimeoutError, socket.timeout):
            raise OpenAlexTimeoutError("OpenAlex inspection request timed out") from None
        except (OSError, http.client.HTTPException):
            raise OpenAlexNetworkError("OpenAlex inspection request failed") from None
        try:
            self._recorder.response(entry, response)
        except Exception:
            response.close()
            raise
        return _BudgetedResponse(
            response, key=key, budget=self._budget, recorder=self._recorder, entry=entry
        )

    @staticmethod
    def _read_all(response: _BudgetedResponse, limit: int) -> bytes:
        content = bytearray()
        try:
            while True:
                chunk = response.read(min(_CHUNK, limit + 1 - len(content)))
                if not chunk:
                    return bytes(content)
                content.extend(chunk)
                if len(content) > limit:
                    raise OpenAlexBudgetExceededError(
                        "OpenAlex object exceeds the per-object inspection byte cap"
                    )
        except (TimeoutError, socket.timeout):
            raise OpenAlexTimeoutError("OpenAlex inspection read timed out") from None
        except http.client.IncompleteRead:
            raise OpenAlexTruncatedError("OpenAlex response ended unexpectedly") from None
        except (OSError, http.client.HTTPException):
            raise OpenAlexNetworkError("OpenAlex inspection read failed") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return child.text or ""
    return None


def _size(value: str | None) -> int:
    if value is None or not re.fullmatch(r"\d{1,15}", value):
        raise OpenAlexEndpointUnavailableError("OpenAlex listing has an invalid size")
    return int(value)


def _safe_iso(value: str | None) -> str | None:
    if value is None:
        return None
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z", value) else None


def _safe_http_date(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    pattern = r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT"
    return value if re.fullmatch(pattern, value) else None


def _listed_object(element: ElementTree.Element) -> ListedObject:
    key = _child_text(element, "Key")
    if key is None:
        raise OpenAlexEndpointUnavailableError("OpenAlex listing entry has no key")
    algorithm = _child_text(element, "ChecksumAlgorithm")
    checksum_type = _child_text(element, "ChecksumType")
    return ListedObject(
        key=key,
        size=_size(_child_text(element, "Size")),
        etag=parse_etag(_child_text(element, "ETag")),
        last_modified=_safe_iso(_child_text(element, "LastModified")),
        checksum_algorithm=(
            algorithm if algorithm in {"CRC64NVME", "CRC32", "CRC32C", "SHA1", "SHA256"} else None
        ),
        checksum_type=checksum_type if checksum_type in {"FULL_OBJECT", "COMPOSITE"} else None,
    )


# =====================================================================================
# Checksums
# =====================================================================================

_MASK64: Final = 0xFFFF_FFFF_FFFF_FFFF


def _reflect(value: int, width: int = 64) -> int:
    result = 0
    for bit in range(width):
        if value >> bit & 1:
            result |= 1 << (width - 1 - bit)
    return result


def _crc64_table() -> tuple[int, ...]:
    polynomial = _reflect(0xAD93D23594C93659)
    table = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ polynomial if value & 1 else value >> 1
        table.append(value)
    return tuple(table)


_CRC64_TABLE: Final = _crc64_table()


def crc64nvme(data: bytes) -> int:
    """CRC-64/NVME: reflected ``0xad93d23594c93659``, init and xorout all ones."""
    state = _MASK64
    table = _CRC64_TABLE
    for byte in data:
        state = table[(state ^ byte) & 0xFF] ^ (state >> 8)
    return state ^ _MASK64


def crc64nvme_s3(value: int) -> str:
    """S3 ``x-amz-checksum-crc64nvme`` encoding: base64 of the 8 big-endian bytes."""
    return base64.b64encode(value.to_bytes(8, "big")).decode("ascii")


def md5_matches_etag(data_md5_hex: str, etag: str | None) -> bool | None:
    """``None`` for multipart ETags (``-N``), which are not content hashes."""
    if etag is None or "-" in etag:
        return None
    return data_md5_hex == etag


# =====================================================================================
# Evidence models
# =====================================================================================


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuotedLine(_Model):
    file: str
    line: int = Field(ge=1)
    text: str


class RequestRecord(_Model):
    seq: int = Field(ge=1)
    method: Literal["GET", "HEAD"]
    path: str
    byte_range: str | None
    status: int | None
    body_bytes: int = Field(ge=0)


class RunLimits(_Model):
    max_object_bytes: int
    max_total_bytes: int
    max_requests: int


class RunRecord(_Model):
    started_at: str
    finished_at: str
    limits: RunLimits
    requests: int
    body_bytes: int
    whole_objects: int
    range_reads: int
    largest_body_bytes: int
    request_log: tuple[RequestRecord, ...]
    tool_versions: dict[str, str]


class KeyPatternCount(_Model):
    key_pattern: str
    count: int


class ListingCoverage(_Model):
    full_listing: bool
    prefixes: tuple[str, ...]
    keys: dict[str, int]
    pages: dict[str, int]


class LayoutRecord(_Model):
    root_prefixes: tuple[str, ...]
    root_files: tuple[str, ...]
    entities: tuple[str, ...]
    parquet_entities_equal: bool
    legacy_merged_ids_prefixes: tuple[str, ...]
    listing_coverage: ListingCoverage
    unreferenced_objects: dict[str, tuple[KeyPatternCount, ...]]


class ListingCheck(_Model):
    manifest_files: int
    listed_files: int
    missing_from_listing: int
    size_mismatches: int
    etag_single_part: int
    etag_multipart: int
    checksum_algorithm: dict[str, int]
    checksum_type: dict[str, int]


class PublishedFile(_Model):
    key: str
    content_length: int
    etag: str | None
    md5_equals_etag: bool | None
    sha256: str
    last_modified: str | None
    version_id: str | None
    line_count: int


class ManifestTotals(_Model):
    record_count: int
    content_length: int
    files: int


class EntitySummary(_Model):
    entity: str
    record_count: int
    content_length: int
    files: int
    partitions: int
    updated_date_min: date | None
    updated_date_max: date | None


class CombinedManifestSummary(_Model):
    date: date
    manifest_bytes: int
    etag: str | None
    version_id: str | None
    totals: ManifestTotals
    entities: tuple[EntitySummary, ...]
    sums_equal_totals: bool


class WorksManifestSummary(_Model):
    date: date
    manifest_bytes: int
    within_manifest_limit: bool
    record_count: int
    content_length: int
    listed: int
    eligible: int
    oversized: int
    unknown_size: int
    size_min: int
    size_median_low: int
    size_max: int
    partitions: int
    updated_date_min: date
    updated_date_max: date
    default_selection_uri: str | None
    default_selection_bytes: int | None
    default_selection_records: int | None


class WorksPairs(_Model):
    pairs: int
    record_count_mismatches: int
    both_within_object_cap: int


class GzipFraming(_Model):
    kind: Literal["gzip"] = "gzip"
    header_hex: str
    members: int
    trailer_crc_ok: bool
    trailer_isize_ok: bool


class ParquetFraming(_Model):
    kind: Literal["parquet"] = "parquet"
    leading_magic: bool
    trailing_magic: bool
    created_by: str | None
    codecs: tuple[str, ...]
    row_groups: int
    statistics: str | None


class ProfiledObject(_Model):
    uri: str
    size: int
    record_count: int
    etag: str | None
    version_id: str | None
    last_modified: str | None
    crc64nvme_s3: str | None
    crc64nvme_local: str
    sha256: str
    md5_equals_etag: bool | None
    head_etag_equals_get_etag: bool
    profile: OpenAlexSourceProfile
    framing: GzipFraming | ParquetFraming


class ProfiledPair(_Model):
    key: str
    rule: str
    jsonl: ProfiledObject
    parquet: ProfiledObject


class ArrowField(_Model):
    path: str
    arrow_type: str
    nullable: bool


class LargeParquetFooter(_Model):
    uri: str
    size: int
    etag: str | None
    footer_bytes: int
    range_bytes: int
    num_rows: int
    manifest_record_count: int | None
    row_groups: int
    rows_per_group_min: int
    rows_per_group_max: int
    uncompressed_per_group_min: int
    uncompressed_per_group_max: int
    codecs: tuple[str, ...]
    created_by: str | None
    schema_equals_profiled_pair: bool


class FieldPathFidelity(_Model):
    jsonl_paths: int
    parquet_paths: int
    missing_in_parquet: tuple[str, ...]
    type_conflicts: tuple[str, ...]
    representation_rules: dict[str, str]
    unobserved_in_jsonl_sample: int


class ValueFidelity(_Model):
    records_compared: int
    jsonl_records_parsed: int
    ids_equal: bool
    row_order_equal: bool
    fields_compared: int
    mismatched_fields: dict[str, int]
    float32_widened_fields: dict[str, int]
    parquet_sub_millisecond_fields: dict[str, int]
    jsonl_absent_parquet_null_fields: dict[str, int]


class FidelityRecord(_Model):
    paths: FieldPathFidelity
    values: ValueFidelity


class CanonicalCompatibility(_Model):
    top_level_keys: tuple[str, ...]
    uncatalogued: tuple[str, ...]
    catalogued_absent: tuple[str, ...]
    records: int
    mapped_unchanged: int
    mapper_first_failures: dict[str, int]
    mapped_with_documented_normalisation: int
    normalisation: tuple[str, ...]


class DuplicateDynamicKeys(_Model):
    records_scanned: int
    records_with_duplicates: int


class ObjectVersionRecord(_Model):
    last_modified: str | None
    size: int
    is_latest: bool
    etag: str | None


class DeletionObject(_Model):
    uri: str
    size: int
    etag: str | None
    last_modified: str | None
    crc64nvme_s3: str | None
    versions: tuple[ObjectVersionRecord, ...] | None
    """``None`` when ListObjectVersions was not requested for this key (not "no versions")."""


class DeletionPrefix(_Model):
    bytes: int
    header: str
    complete_rows: int
    row_shape_ok: bool
    deleted_date_counts: dict[str, int]
    sorted_by_work_id: bool
    truncated: Literal[True] = True


class DeletionsRecord(_Model):
    current: tuple[DeletionObject, ...]
    identical_content_under_both_trees: bool
    """Equal size, ETag and CRC64NVME; the two keys are distinct S3 objects."""
    prefix: DeletionPrefix
    non_works_deletion_objects: tuple[str, ...]
    legacy_merged_ids_prefixes: tuple[str, ...]
    release_note_quotes: tuple[QuotedLine, ...]


class ManifestVersion(_Model):
    key: str
    version_id: str | None
    is_latest: bool
    last_modified: str | None
    size: int
    within_manifest_limit: bool


class PriorReleaseVisibility(_Model):
    manifest_versions: tuple[ManifestVersion, ...]
    anonymous_versioning: bool
    retention_policy_published: Literal[False] = False


class RightsRecord(_Model):
    license_title: str
    license_title_line: QuotedLine
    license_sha256: str
    license_bytes: int
    license_versions: int
    license_versions_same_etag: bool
    readme_license_line: QuotedLine | None
    third_party_rights_clause: tuple[QuotedLine, ...]
    attribution_statements: tuple[QuotedLine, ...]
    help_links: tuple[QuotedLine, ...]
    """Lines linking help.openalex.org (documentation and sync pages); not terms of use."""
    full_text_statements: tuple[QuotedLine, ...]
    full_text_field_present: bool
    full_text_indicator_paths: tuple[str, ...]
    abstract_paths: tuple[str, ...]
    abstract_notes: tuple[QuotedLine, ...]
    per_work_license_paths: tuple[str, ...]
    personal_data_paths: tuple[str, ...]
    statuses: dict[str, str]
    unverified: tuple[str, ...]


class CadenceRecord(_Model):
    stated: tuple[QuotedLine, ...]
    release_headings: int
    releases_by_year: dict[str, int]
    releases_current_year: tuple[date, ...]
    on_stated_schedule_current_year: tuple[date, ...]
    current_layout_manifest_versions: int
    inference: str


class SkipCounts(_Model):
    too_large: int = 0
    unknown_path: int = 0
    leak: int = 0
    email: int = 0
    type: int = 0


class RedactionSummary(_Model):
    rules_version: int
    policy_counts: dict[str, int]
    candidates: int
    skipped: SkipCounts
    unknown_string_paths: tuple[str, ...]


class SnapshotEvidence(_Model):
    schema_version: Literal[1] = 1
    evidence: Literal["LOCAL bounded inspection"] = EVIDENCE_LABEL
    environment: Literal["local container; anonymous HTTPS; no cloud project or credentials"] = (
        ENVIRONMENT
    )
    host: Literal["openalex.s3.amazonaws.com"] = HOST
    generated_from: Literal["network", "recorded raw"]
    run: RunRecord
    snapshot_date: date
    layout: LayoutRecord
    listing_checks: dict[str, ListingCheck]
    published_files: tuple[PublishedFile, ...]
    combined_manifests: dict[str, CombinedManifestSummary]
    works_manifests: dict[str, WorksManifestSummary]
    works_pairs: WorksPairs
    profiled_pair: ProfiledPair
    arrow_schema_fields: tuple[ArrowField, ...]
    large_parquet_footer: LargeParquetFooter
    fidelity: FidelityRecord
    canonical_compatibility: CanonicalCompatibility
    duplicate_dynamic_keys: DuplicateDynamicKeys
    deletions: DeletionsRecord
    prior_release_visibility: PriorReleaseVisibility
    rights: RightsRecord
    cadence: CadenceRecord
    redaction: RedactionSummary
    fixture_profile: OpenAlexSourceProfile
    limitations: tuple[str, ...]


class DigestTotals(_Model):
    record_count: int
    content_length: int
    files: int


DIGEST_COLUMNS: Final = ("updated_date", "part", "jsonl_bytes", "parquet_bytes", "record_count")


class WorksManifestDigest(_Model):
    schema_version: Literal[1] = 1
    evidence: Literal["LOCAL bounded inspection"] = EVIDENCE_LABEL
    snapshot_date: date
    columns: tuple[str, ...] = DIGEST_COLUMNS
    jsonl: DigestTotals
    parquet: DigestTotals
    rows: tuple[tuple[str, int, int | None, int | None, int | None], ...]


class FixtureSource(_Model):
    jsonl_uri: str
    jsonl_sha256: str
    jsonl_version_id: str | None
    parquet_uri: str
    parquet_sha256: str
    parquet_version_id: str | None
    candidate_records: int


class FixtureSelection(_Model):
    rule: str
    count: int
    skipped: SkipCounts


class FixtureRedaction(_Model):
    rules_version: int
    policy_counts: dict[str, int]


class RedactedFixture(_Model):
    schema_version: Literal[1] = 1
    evidence: Literal["LOCAL bounded inspection"] = EVIDENCE_LABEL
    snapshot_date: date
    source: FixtureSource
    selection: FixtureSelection
    redaction: FixtureRedaction
    records: tuple[dict[str, Any], ...]


def canonical_json(model: BaseModel | Mapping[str, Any]) -> str:
    data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates)


def load_snapshot_evidence(path: Path) -> SnapshotEvidence:
    return SnapshotEvidence.model_validate(_load_json(path))


def load_works_manifest_digest(path: Path) -> WorksManifestDigest:
    return WorksManifestDigest.model_validate(_load_json(path))


def load_redacted_fixture(path: Path) -> RedactedFixture:
    return RedactedFixture.model_validate(_load_json(path))


STRUCTURAL_PATH: Final = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+|\[\]|\{key\}|\{value\})*")


def structural_path_errors(paths: Iterable[str]) -> tuple[str, ...]:
    """Committed schema paths must be structural (no record value used as a key)."""
    return tuple(sorted({path for path in paths if not STRUCTURAL_PATH.fullmatch(path)}))


def evidence_schema_paths(evidence: SnapshotEvidence) -> tuple[str, ...]:
    paths: set[str] = set()
    for profile in (
        evidence.profiled_pair.jsonl.profile,
        evidence.profiled_pair.parquet.profile,
        evidence.fixture_profile,
    ):
        paths.update(field.path for field in profile.schema_fields)
    paths.update(field.path for field in evidence.arrow_schema_fields)
    return tuple(sorted(paths))


# =====================================================================================
# Summarisers
# =====================================================================================

_PART_URI = re.compile(
    r"s3://openalex/data/(jsonl|parquet)/works/updated_date=(\d{4}-\d{2}-\d{2})/"
    r"part_(\d{4})\.(gz|parquet)"
)
_DELETION_ROW = re.compile(r"https://openalex\.org/W\d+,\d{4}-\d{2}-\d{2}")


def _strict(mapping: Any, allowed: set[str], what: str) -> Mapping[str, Any]:
    if not isinstance(mapping, Mapping) or set(mapping) - allowed:
        raise ValueError(f"OpenAlex combined manifest {what} has unsupported fields")
    return mapping


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("OpenAlex combined manifest counts must be non-negative integers")
    return value


def combined_manifest_files(content: bytes) -> dict[str, int]:
    """URL to ``content_length`` for every file of a combined manifest."""
    manifest = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    return {
        item["url"]: item["meta"]["content_length"]
        for entity in manifest["entities"] for item in entity["files"]
    }


def summarize_combined_manifest(
    content: bytes, *, etag: str | None, version_id: str | None
) -> CombinedManifestSummary:
    """Summarise ``data/{jsonl,parquet}/manifest.json``; unknown fields or bad sums raise."""
    manifest = _strict(
        json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicates),
        {"date", "format", "meta", "entities"}, "root",
    )
    meta = _strict(manifest.get("meta"), {"record_count", "content_length"}, "meta")
    if not isinstance(manifest.get("date"), str):
        raise ValueError("OpenAlex combined manifest date must be YYYY-MM-DD")
    snapshot = date.fromisoformat(manifest["date"])
    if snapshot.isoformat() != manifest["date"]:
        raise ValueError("OpenAlex combined manifest date must be YYYY-MM-DD")
    if manifest.get("format") not in {"jsonl", "parquet"}:
        raise ValueError("OpenAlex combined manifest declares an unsupported format")
    if not isinstance(manifest.get("entities"), list):
        raise ValueError("OpenAlex combined manifest entities must be a list")
    entities: list[EntitySummary] = []
    total_records = total_bytes = total_files = 0
    for raw_entity in manifest["entities"]:
        entity = _strict(raw_entity, {"entity", "record_count", "content_length", "files"}, "entity")
        files = entity.get("files")
        if not isinstance(files, list) or not isinstance(entity.get("entity"), str):
            raise ValueError("OpenAlex combined manifest entity is malformed")
        records = size = 0
        dates: set[str] = set()
        for raw_item in files:
            item = _strict(raw_item, {"url", "meta"}, "file")
            file_meta = _strict(item.get("meta"), {"content_length", "record_count"}, "file meta")
            records += _count(file_meta.get("record_count"))
            size += _count(file_meta.get("content_length"))
            match = re.search(r"/updated_date=(\d{4}-\d{2}-\d{2})/", str(item.get("url")))
            if match:
                dates.add(match.group(1))
        if records != _count(entity.get("record_count")) or size != _count(entity.get("content_length")):
            raise ValueError("OpenAlex combined manifest sums differ from its totals")
        total_records += records
        total_bytes += size
        total_files += len(files)
        entities.append(EntitySummary(
            entity=entity["entity"], record_count=records, content_length=size, files=len(files),
            partitions=len(dates),
            updated_date_min=min(dates) if dates else None,
            updated_date_max=max(dates) if dates else None,
        ))
    if total_records != _count(meta.get("record_count")) or total_bytes != _count(meta.get("content_length")):
        raise ValueError("OpenAlex combined manifest sums differ from its totals")
    return CombinedManifestSummary(
        date=snapshot, manifest_bytes=len(content), etag=etag, version_id=version_id,
        totals=ManifestTotals(record_count=total_records, content_length=total_bytes, files=total_files),
        entities=tuple(sorted(entities, key=lambda item: item.entity)),
        sums_equal_totals=True,
    )


def summarize_works_manifest(
    content: bytes, content_format: str, *, config: SampleSelectionConfig = SampleSelectionConfig(),
) -> tuple[WorksManifestSummary, tuple[OpenAlexAssetMetadata, ...]]:
    """Parse with the unchanged repository parser; select with the unchanged selector."""
    decoded = json.loads(content.decode("utf-8"))
    assets = parse_openalex_works_manifest(content, content_format=content_format, entity="works")
    selection = select_openalex_works_sample(assets, config)
    reasons = Counter(item.reason for item in selection.skipped)
    sizes = sorted(asset.byte_size for asset in assets if asset.byte_size is not None)
    dates = sorted({asset.updated_date for asset in assets if asset.updated_date is not None})
    chosen = selection.selected[0] if selection.selected else None
    return (
        WorksManifestSummary(
            date=date.fromisoformat(decoded["date"]),
            manifest_bytes=len(content),
            within_manifest_limit=len(content) <= _MANIFEST_LIMIT,
            record_count=int(decoded.get("record_count") or 0),
            content_length=int(decoded.get("content_length") or 0),
            listed=len(assets),
            eligible=selection.eligible_count,
            oversized=reasons.get("OVERSIZED", 0),
            unknown_size=reasons.get("UNKNOWN_SIZE", 0),
            size_min=sizes[0],
            size_median_low=int(statistics.median_low(sizes)),
            size_max=sizes[-1],
            partitions=len(dates),
            updated_date_min=dates[0],
            updated_date_max=dates[-1],
            default_selection_uri=chosen.file_uri if chosen else None,
            default_selection_bytes=chosen.byte_size if chosen else None,
            default_selection_records=chosen.record_count if chosen else None,
        ),
        assets,
    )


def check_manifest_dates(expected: date, dates: Mapping[str, date]) -> None:
    """Every manifest date must equal the others and ``--expect-snapshot-date``."""
    if set(dates.values()) != {expected}:
        raise SnapshotDateMismatch("OpenAlex snapshot date differs from the expected date")


def works_manifest_digest(
    snapshot_date: date,
    jsonl_assets: Iterable[OpenAlexAssetMetadata],
    parquet_assets: Iterable[OpenAlexAssetMetadata],
) -> WorksManifestDigest:
    """Per-file sizes and record counts of both Works manifests (no record values)."""
    rows: dict[tuple[str, int], list[int | None]] = {}
    totals: dict[str, DigestTotals] = {}
    for name, assets in (("jsonl", tuple(jsonl_assets)), ("parquet", tuple(parquet_assets))):
        totals[name] = DigestTotals(
            record_count=sum(asset.record_count or 0 for asset in assets),
            content_length=sum(asset.byte_size or 0 for asset in assets),
            files=len(assets),
        )
        for asset in assets:
            match = _PART_URI.fullmatch(asset.file_uri)
            if match is None:
                raise ValueError("OpenAlex Works file URI does not follow the observed pattern")
            row = rows.setdefault((match.group(2), int(match.group(3))), [None, None, None, None])
            row[0 if name == "jsonl" else 1] = asset.byte_size
            row[2 if name == "jsonl" else 3] = asset.record_count
    out = []
    for (day, part), (jsonl_bytes, parquet_bytes, jsonl_records, parquet_records) in sorted(rows.items()):
        if jsonl_records is not None and parquet_records is not None and jsonl_records != parquet_records:
            raise ValueError("OpenAlex JSONL and Parquet record counts differ for one part")
        out.append((day, part, jsonl_bytes, parquet_bytes,
                    jsonl_records if jsonl_records is not None else parquet_records))
    return WorksManifestDigest(
        snapshot_date=snapshot_date, jsonl=totals["jsonl"], parquet=totals["parquet"], rows=tuple(out),
    )


def manifest_from_digest(digest: WorksManifestDigest, content_format: str) -> bytes:
    """Rebuild a real-schema per-entity Works manifest from the committed digest."""
    if content_format not in {"jsonl", "parquet"}:
        raise ValueError("content format must be jsonl or parquet")
    column = 2 if content_format == "jsonl" else 3
    suffix = "gz" if content_format == "jsonl" else "parquet"
    files = [
        {
            "url": f"s3://openalex/data/{content_format}/works/updated_date={row[0]}/part_{row[1]:04d}.{suffix}",
            "meta": {"content_length": row[column], "record_count": row[4]},
        }
        for row in digest.rows if row[column] is not None
    ]
    totals = digest.jsonl if content_format == "jsonl" else digest.parquet
    return json.dumps({
        "date": digest.snapshot_date.isoformat(), "format": content_format, "entity": "works",
        "record_count": totals.record_count, "content_length": totals.content_length, "files": files,
    }, separators=(",", ":")).encode("utf-8")


def summarize_pairs(digest: WorksManifestDigest, *, max_object_bytes: int) -> WorksPairs:
    pairs = [row for row in digest.rows if row[2] is not None and row[3] is not None]
    return WorksPairs(
        pairs=len(pairs),
        record_count_mismatches=0,
        both_within_object_cap=sum(
            1 for row in pairs if row[2] <= max_object_bytes and row[3] <= max_object_bytes  # type: ignore[operator]
        ),
    )


def select_profile_pair(
    digest: WorksManifestDigest, *, max_file_bytes: int = PAIR_MAX_FILE_BYTES,
    min_records: int = PAIR_MIN_RECORDS,
) -> tuple[str, int]:
    """The latest pair with both files within ``max_file_bytes`` (never above the cap)."""
    if not 0 < max_file_bytes <= MAX_INSPECTION_OBJECT_BYTES:
        raise ValueError("pair file bound must be positive and within the per-object cap")
    candidates = [
        row for row in digest.rows
        if row[2] is not None and row[3] is not None
        and row[2] <= max_file_bytes and row[3] <= max_file_bytes
        and (row[4] or 0) >= min_records
    ]
    if not candidates:
        raise LookupError("no JSONL/Parquet pair fits the pair rule")
    latest = max(candidates, key=lambda row: (row[0], -row[1]))
    return latest[0], latest[1]


def pair_uris(updated_date: str, part: int) -> tuple[str, str]:
    base = f"works/updated_date={updated_date}/part_{part:04d}"
    return f"s3://openalex/data/jsonl/{base}.gz", f"s3://openalex/data/parquet/{base}.parquet"


def summarize_manifest_versions(
    versions: Iterable[ObjectVersion], *, manifest_limit: int = _MANIFEST_LIMIT
) -> tuple[ManifestVersion, ...]:
    """Mark manifest versions above the connector's manifest cap; none is fetched."""
    return tuple(
        ManifestVersion(
            key=version.key, version_id=version.version_id, is_latest=version.is_latest,
            last_modified=version.last_modified, size=version.size,
            within_manifest_limit=version.size <= manifest_limit,
        )
        for version in versions
    )


def _key_pattern(key: str, prefix: str, entities: set[str]) -> str:
    relative = key.removeprefix(prefix)
    head, _, tail = relative.partition("/")
    if tail == "manifest.json" and head in entities:
        return "<entity>/manifest.json"
    return relative


def listing_check(
    listing: Listing, manifest_files: Mapping[str, int], *, content_format: str
) -> tuple[ListingCheck, tuple[KeyPatternCount, ...]]:
    """Every manifest URL must be listed with an equal size; name what is unreferenced."""
    prefix = f"data/{content_format}/"
    listed = {item.key: item for item in listing.objects}
    referenced = {uri.removeprefix("s3://openalex/"): size for uri, size in manifest_files.items()}
    missing = sum(1 for key in referenced if key not in listed)
    mismatches = sum(1 for key, size in referenced.items() if key in listed and listed[key].size != size)
    entities = {key.removeprefix(prefix).split("/", 1)[0] for key in listed if "/" in key.removeprefix(prefix)}
    patterns = Counter(
        _key_pattern(key, prefix, entities) for key in listed if key not in referenced
    )
    return (
        ListingCheck(
            manifest_files=len(referenced),
            listed_files=len(listed),
            missing_from_listing=missing,
            size_mismatches=mismatches,
            etag_single_part=sum(1 for item in listing.objects if item.etag and "-" not in item.etag),
            etag_multipart=sum(1 for item in listing.objects if item.etag and "-" in item.etag),
            checksum_algorithm=dict(sorted(Counter(
                item.checksum_algorithm or "none" for item in listing.objects).items())),
            checksum_type=dict(sorted(Counter(
                item.checksum_type or "none" for item in listing.objects).items())),
        ),
        tuple(KeyPatternCount(key_pattern=pattern, count=count) for pattern, count in sorted(patterns.items())),
    )


def gzip_framing(data: bytes) -> GzipFraming:
    """Header bytes, member count and trailer checks of a complete gzip object."""
    members = 0
    remaining = data
    crc_ok = isize_ok = True
    while remaining:
        if remaining[:2] != b"\x1f\x8b":
            raise ValueError("bytes after a gzip member are not another member")
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        crc = size = 0
        view = remaining
        while view and not decompressor.eof:
            chunk = decompressor.decompress(view[: 1 << 20])
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
            view = view[1 << 20:]
        if not decompressor.eof:
            raise ValueError("gzip member is truncated")
        rest = decompressor.unused_data + view
        consumed = len(remaining) - len(rest)
        trailer = remaining[consumed - 8: consumed]
        crc_ok &= int.from_bytes(trailer[:4], "little") == crc
        isize_ok &= int.from_bytes(trailer[4:], "little") == size % (1 << 32)
        members += 1
        remaining = rest
    return GzipFraming(
        header_hex=data[:10].hex(), members=members, trailer_crc_ok=crc_ok, trailer_isize_ok=isize_ok,
    )


def parquet_framing(body: bytes, profile: OpenAlexSourceProfile) -> ParquetFraming:
    return ParquetFraming(
        leading_magic=body[:4] == b"PAR1",
        trailing_magic=body[-4:] == b"PAR1",
        created_by=profile.parquet_created_by,
        codecs=tuple(sorted(profile.compression.split(","))) if profile.compression else (),
        row_groups=profile.row_group_count or 0,
        statistics=profile.statistics_available,
    )


def arrow_schema_fields(schema: pa.Schema) -> tuple[ArrowField, ...]:
    """Readable, diffable schema paths (no metadata)."""
    out: list[ArrowField] = []

    def walk(path: str, field: pa.Field) -> None:
        data_type = field.type
        if pa.types.is_struct(data_type):
            out.append(ArrowField(path=path, arrow_type="struct", nullable=field.nullable))
            for index in range(data_type.num_fields):
                child = data_type.field(index)
                walk(f"{path}.{child.name}", child)
        elif pa.types.is_map(data_type):
            out.append(ArrowField(path=path, arrow_type="map", nullable=field.nullable))
            walk(f"{path}{{key}}", data_type.key_field)
            walk(f"{path}{{value}}", data_type.item_field)
        elif pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
            out.append(ArrowField(path=path, arrow_type="list", nullable=field.nullable))
            walk(f"{path}[]", data_type.value_field)
        else:
            out.append(ArrowField(path=path, arrow_type=str(data_type), nullable=field.nullable))

    for field in schema.remove_metadata():
        walk(field.name, field)
    return tuple(out)


def summarize_deletion_prefix(prefix: bytes) -> DeletionPrefix:
    """Rows of a gzip CSV prefix; the final partial line is not counted."""
    text = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(prefix).decode("utf-8", errors="replace")
    lines = text.split("\n")
    header = lines[0].strip("\r")
    complete = [line.strip("\r") for line in lines[1:-1] if line.strip()]
    shape_ok = all(_DELETION_ROW.fullmatch(line) for line in complete)
    dates = Counter(line.rsplit(",", 1)[-1] for line in complete if "," in line)
    ids = [int(line.split(",", 1)[0].rsplit("W", 1)[-1]) for line in complete] if shape_ok else []
    return DeletionPrefix(
        bytes=len(prefix), header=header, complete_rows=len(complete), row_shape_ok=shape_ok,
        deleted_date_counts=dict(sorted(dates.items())), sorted_by_work_id=ids == sorted(ids),
    )


# =====================================================================================
# Rights and cadence
# =====================================================================================

FULL_TEXT_INDICATOR_PATHS: Final = (
    "has_fulltext", "has_content.pdf", "has_content.grobid_xml",
    "open_access.any_repository_has_fulltext", "open_access.oa_url",
    "locations[].pdf_url", "locations[].landing_page_url",
)
_ATTRIBUTION = re.compile(r"(?i)\battribution\b|\bplease cite\b|\bmust cite\b")
_FULL_TEXT = re.compile(r"(?i)\bfull[- ]?text\b")
_RELEASE = re.compile(r"RELEASE (\d{4}-\d{2}-\d{2})")
_DELETION_NOTE = re.compile(r"(?i)^- (delete|merged?) [^\n]*\bworks\b|merged_ids")


def _numbered(name: str, text: str) -> list[QuotedLine]:
    return [
        QuotedLine(file=name, line=index, text=line.rstrip())
        for index, line in enumerate(text.splitlines(), start=1)
    ]


def _matching(lines: Iterable[QuotedLine], pattern: re.Pattern[str]) -> tuple[QuotedLine, ...]:
    return tuple(line for line in lines if pattern.search(line.text))


def deletion_note_lines(release_notes_text: str) -> tuple[QuotedLine, ...]:
    return _matching(_numbered("RELEASE_NOTES.txt", release_notes_text), _DELETION_NOTE)


def extract_rights(
    license_text: str, readme_text: str, release_notes_text: str, *, license_sha256: str,
    license_versions: Sequence[ObjectVersion], profile_paths: Iterable[str],
) -> RightsRecord:
    """Rights exactly as the snapshot's own LICENSE/README/RELEASE_NOTES publish them."""
    license_lines = _numbered("LICENSE.txt", license_text)
    readme_lines = _numbered("README.txt", readme_text)
    notes_lines = _numbered("RELEASE_NOTES.txt", release_notes_text)
    every = license_lines + readme_lines + notes_lines
    title = next((line for line in license_lines[:10] if line.text.strip() == "CC0 1.0 Universal"), None)
    if title is None:
        title = next((line for line in license_lines if line.text.strip()), None)
    if title is None:
        raise RightsChanged("OpenAlex snapshot licence is empty")
    clause: list[QuotedLine] = []
    for index, line in enumerate(license_lines):
        if "Affirmer disclaims responsibility for clearing rights of other persons" in line.text:
            for follow in license_lines[index:]:
                if follow is not line and (re.match(r"\s*[a-z]\.\s", follow.text) or not follow.text.strip()):
                    break
                clause.append(follow)
            break
    paths = set(profile_paths)
    top_level = {re.split(r"[.\[{]", path, maxsplit=1)[0] for path in paths}
    attribution = _matching(every, _ATTRIBUTION)
    full_text = _matching(every, _FULL_TEXT)
    statuses = {
        "license": "OBSERVED",
        "readme_license_line": "OBSERVED",
        "third_party_rights_clause": "OBSERVED",
        "attribution": "PUBLISHED IN SNAPSHOT" if attribution else "NOT PUBLISHED IN SNAPSHOT",
        "terms_of_use": "NOT VERIFIED",
        "full_article_text": (
            "STATEMENT PUBLISHED IN SNAPSHOT" if full_text
            else "NOT IN SNAPSHOT; full-text rights NOT PUBLISHED"
        ),
        "abstracts": "PRESENT; no specific statement",
        "per_work_license_paths": "OBSERVED",
        "personal_data_paths": "OBSERVED",
    }
    return RightsRecord(
        license_title=title.text.strip(),
        license_title_line=title,
        license_sha256=license_sha256,
        license_bytes=len(license_text.encode("utf-8")),
        license_versions=len(license_versions),
        license_versions_same_etag=len({version.etag for version in license_versions}) <= 1,
        readme_license_line=next((line for line in readme_lines if line.text.startswith("License:")), None),
        third_party_rights_clause=tuple(clause),
        attribution_statements=attribution,
        help_links=_matching(readme_lines + notes_lines, re.compile(r"help\.openalex\.org")),
        full_text_statements=full_text,
        full_text_field_present=any(
            re.fullmatch(r"(?i)full_?text|body_?text|full_?text_?content", key) for key in top_level
        ),
        full_text_indicator_paths=tuple(path for path in FULL_TEXT_INDICATOR_PATHS if path in paths),
        abstract_paths=tuple(sorted(path for path in paths if path.startswith("abstract_inverted_index"))),
        abstract_notes=_matching(notes_lines, re.compile(r"(?i)\babstract")),
        per_work_license_paths=tuple(sorted(p for p in paths if re.search(r"\.(license|license_id)$", p))),
        personal_data_paths=MASK_PERSON_PATHS,
        statuses=statuses,
        unverified=(
            "Terms of use, attribution guidance and any full-text or abstract statement on "
            "help.openalex.org (linked from README.txt and RELEASE_NOTES.txt; that host was "
            "neither approved nor read).",
            "Whether CC0 as dedicated by OpenAlex covers third-party abstract text: LICENSE "
            "clause 4(c) disclaims clearing other persons' rights. This is an input to the "
            "governance decision in ADR 0001, not an approval.",
        ),
    )


def second_wednesday(year: int, month: int) -> date:
    offset = (calendar.WEDNESDAY - calendar.weekday(year, month, 1)) % 7
    return date(year, month, 1 + offset + 7)


def extract_cadence(
    readme_text: str, release_notes_text: str, *, manifest_versions: int, current_year: int,
) -> CadenceRecord:
    """Stated cadence (quoted) kept apart from the observed release headings."""
    readme_lines = _numbered("README.txt", readme_text)
    notes_lines = _numbered("RELEASE_NOTES.txt", release_notes_text)
    stated = _matching(
        readme_lines + notes_lines, re.compile(r"(?i)once a quarter|second Wednesday|updated quarterly"),
    )
    releases = [
        date.fromisoformat(match.group(1))
        for line in notes_lines if (match := _RELEASE.fullmatch(line.text.strip()))
    ]
    current = tuple(sorted(day for day in releases if day.year == current_year))
    return CadenceRecord(
        stated=stated,
        release_headings=len(releases),
        releases_by_year=dict(sorted(Counter(str(day.year) for day in releases).items())),
        releases_current_year=current,
        on_stated_schedule_current_year=tuple(
            day for day in current
            if day.month in {1, 4, 7, 10} and day == second_wednesday(day.year, day.month)
        ),
        current_layout_manifest_versions=manifest_versions,
        inference=(
            "Inference, not a published statement: the usable change trigger is the combined "
            "manifest's ETag, version id or date (README: manifests are written last), not a calendar."
        ),
    )


# =====================================================================================
# Redaction (fail closed over every observed string path)
# =====================================================================================

REDACTION_RULES_VERSION: Final = 1
_LOCATIONS: Final = ("primary_location", "best_oa_location", "locations[]")
MASK_PERSON_PATHS: Final = (
    "authorships[].author.id",
    "corresponding_author_ids[]",
    "authorships[].author.display_name",
    "authorships[].raw_author_name",
    "authorships[].author.orcid",
    "authorships[].author.observed_orcids[]",
    "authorships[].raw_orcid",
    "authorships[].raw_affiliation_strings[]",
    "authorships[].affiliations[].raw_affiliation_string",
)
REPLACE_TEXT_PATHS: Final = (
    "title",
    "display_name",
    *(f"{location}.landing_page_url" for location in _LOCATIONS),
    *(f"{location}.pdf_url" for location in _LOCATIONS),
    "open_access.oa_url",
    *(f"{location}.id" for location in _LOCATIONS),
    *(f"{location}.raw_source_name" for location in _LOCATIONS),
    "awards[].display_name",
    "awards[].funder_award_id",
    "abstract_inverted_index{key}",
)
MASK_PRICE_PATHS: Final = ("apc_list.currency", "apc_paid.currency")
_TOPIC_KEEP = ("id", "display_name", "domain.id", "domain.display_name", "field.id",
               "field.display_name", "subfield.id", "subfield.display_name")
KEEP_PATHS: Final = (
    "id", "doi", "type", "language", "created_date", "updated_date", "publication_date",
    "indexed_in[]", "referenced_works[]", "related_works[]", "corresponding_institution_ids[]",
    "ids.doi", "ids.mag", "ids.openalex", "ids.pmid",
    "authorships[].author_position", "authorships[].countries[]",
    *(f"authorships[].institutions[].{name}"
      for name in ("id", "display_name", "ror", "country_code", "type", "lineage[]")),
    "authorships[].affiliations[].institution_ids[]",
    *(f"{location}.{name}" for location in _LOCATIONS
      for name in ("doi", "license", "license_id", "provenance", "raw_type", "version")),
    *(f"{location}.source.{name}" for location in _LOCATIONS
      for name in ("id", "display_name", "type", "issn[]", "issn_l", "listed_in[]",
                   "host_organization", "host_organization_name",
                   "host_organization_lineage[]", "host_organization_lineage_names[]")),
    "open_access.oa_status",
    *(f"biblio.{name}" for name in ("volume", "issue", "first_page", "last_page")),
    *(f"concepts[].{name}" for name in ("id", "wikidata", "display_name")),
    "keywords[].id", "keywords[].display_name",
    *(f"topics[].{name}" for name in _TOPIC_KEEP),
    *(f"primary_topic.{name}" for name in _TOPIC_KEEP),
    *(f"mesh[].{name}" for name in ("descriptor_ui", "descriptor_name", "qualifier_ui", "qualifier_name")),
    "sustainable_development_goals[].id", "sustainable_development_goals[].display_name",
    "study_designs[].id", "study_designs[].display_name",
    "volume.id", "volume.display_name",
    "funders[].id", "funders[].display_name", "funders[].ror",
    "awards[].id", "awards[].funder_id", "awards[].funder_display_name",
)
REDACTION_POLICY: Final = {
    **{path: "mask_person" for path in MASK_PERSON_PATHS},
    **{path: "replace_text" for path in REPLACE_TEXT_PATHS},
    **{path: "mask_price" for path in MASK_PRICE_PATHS},
    **{path: "keep" for path in KEEP_PATHS},
}
"""Every observed string leaf path mapped to exactly one action (143 paths)."""
EXCLUDED_TYPES: Final = frozenset({"paratext", "erratum", "letter", "editorial", "other"})
EMAIL_PATTERN: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PLAIN_KEY = re.compile(r"[a-z0-9_]+")
_STOP_TOKENS = frozenset({"author", "authors", "unknown", "anonymous", "none", "null", "other"})
_SYNTHETIC = {
    "authorships[].author.id": re.compile(r"https://openalex\.org/A0\d{9}"),
    "corresponding_author_ids[]": re.compile(r"https://openalex\.org/A0\d{9}"),
    "authorships[].author.display_name": re.compile(r"Author \d+"),
    "authorships[].raw_author_name": re.compile(r"Author \d+"),
    "authorships[].author.orcid": re.compile(r"(https://orcid\.org/)?0000-0000-0000-\d{3}[0-9X]"),
    "authorships[].author.observed_orcids[]": re.compile(r"(https://orcid\.org/)?0000-0000-0000-\d{3}[0-9X]"),
    "authorships[].raw_orcid": re.compile(r"(https://orcid\.org/)?0000-0000-0000-\d{3}[0-9X]"),
    "authorships[].raw_affiliation_strings[]": re.compile(r"Affiliation \d+"),
    "authorships[].affiliations[].raw_affiliation_string": re.compile(r"Affiliation \d+"),
    "title": re.compile(r"Title \d+"),
    "display_name": re.compile(r"Title \d+"),
    "open_access.oa_url": re.compile(r"https://example\.org/u\d+(\.pdf)?"),
    "awards[].display_name": re.compile(r"Award \d+"),
    "awards[].funder_award_id": re.compile(r"AWARD-\d+"),
    "apc_list.currency": re.compile(r"XXX"),
    "apc_paid.currency": re.compile(r"XXX"),
    **{f"{location}.landing_page_url": re.compile(r"https://example\.org/u\d+(\.pdf)?")
       for location in _LOCATIONS},
    **{f"{location}.pdf_url": re.compile(r"https://example\.org/u\d+\.pdf") for location in _LOCATIONS},
    **{f"{location}.id": re.compile(r"loc:\d+") for location in _LOCATIONS},
    **{f"{location}.raw_source_name": re.compile(r"Source \d+") for location in _LOCATIONS},
}


def policy_counts() -> dict[str, int]:
    return dict(sorted(Counter(REDACTION_POLICY.values()).items()))


@dataclasses.dataclass
class Pseudonyms:
    """One instance per fixture, so equal originals map to equal pseudonyms."""

    maps: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)

    def number(self, kind: str, value: str) -> int:
        mapping = self.maps.setdefault(kind, {})
        if value not in mapping:
            mapping[value] = len(mapping) + 1
        return mapping[value]


def orcid_check_character(base_digits: str) -> str:
    """ISO 7064 MOD 11-2 check character for the 15 leading ORCID digits."""
    if not re.fullmatch(r"\d{15}", base_digits):
        raise ValueError("ORCID base must be 15 digits")
    total = 0
    for digit in base_digits:
        total = (total + int(digit)) * 2
    result = (12 - total % 11) % 11
    return "X" if result == 10 else str(result)


def _synthetic_orcid(number: int, *, url: bool) -> str:
    if not 1 <= number <= 999:
        raise RedactionError("fixture has more ORCIDs than synthetic values")
    bare = f"0000-0000-0000-{number:03d}{orcid_check_character(f'000000000000{number:03d}')}"
    return f"https://orcid.org/{bare}" if url else bare


_ABSTRACT: Final = "abstract_inverted_index"
_ABSTRACT_VALUE: Final = "abstract_inverted_index{value}"


def _is_word_positions(value: Any) -> bool:
    """An abstract word's value must be a list of non-negative integer positions."""
    return isinstance(value, list) and all(
        type(item) is int and item >= 0 for item in value
    )


def string_leaf_paths(record: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield ``(path, value)`` for every string leaf; abstract word keys as ``{key}``.

    Abstract word values are walked as ``abstract_inverted_index{value}``, so any
    string inside them surfaces as a path outside the redaction policy.
    """
    stack: list[tuple[str, Any]] = [("", record)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, dict):
            if path == _ABSTRACT:
                for key, child in value.items():
                    yield "abstract_inverted_index{key}", key
                    stack.append((_ABSTRACT_VALUE, child))
                continue
            for key, child in value.items():
                name = key if _PLAIN_KEY.fullmatch(key) else "<non-structural key>"
                stack.append((f"{path}.{name}" if path else name, child))
        elif isinstance(value, list):
            for child in value:
                stack.append((f"{path}[]", child))


def _abstract_value_errors(record: Mapping[str, Any]) -> set[str]:
    abstract = record.get(_ABSTRACT)
    if isinstance(abstract, dict) and not all(_is_word_positions(v) for v in abstract.values()):
        return {_ABSTRACT_VALUE}
    return set()


def unknown_string_paths(record: Mapping[str, Any]) -> tuple[str, ...]:
    """String paths outside the policy, dict keys that are not structural names, and
    ``abstract_inverted_index{value}`` when a word's value is not a list of
    non-negative integer positions."""
    unknown = {path for path, _ in string_leaf_paths(record) if path not in REDACTION_POLICY}
    unknown |= _abstract_value_errors(record)
    stack: list[tuple[str, Any]] = [("", record)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, dict) and path == _ABSTRACT:
            for child in value.values():
                stack.append((_ABSTRACT_VALUE, child))
        elif isinstance(value, dict):
            for key, child in value.items():
                if not _PLAIN_KEY.fullmatch(key):
                    unknown.add(f"{path}.<non-structural key>" if path else "<non-structural key>")
                    continue
                stack.append((f"{path}.{key}" if path else key, child))
        elif isinstance(value, list):
            for child in value:
                stack.append((f"{path}[]", child))
    return tuple(sorted(unknown))


def redact_work_record(record: Mapping[str, Any], pseudonyms: Pseudonyms) -> dict[str, Any]:
    """Return a masked deep copy; raise ``RedactionError`` on any unknown string path."""
    if not isinstance(record, Mapping):
        raise RedactionError("Works record must be an object")
    unknown = unknown_string_paths(record)
    if unknown:
        raise RedactionError("unknown string path")
    out: dict[str, Any] = copy.deepcopy(dict(record))
    _redact_node(out, "", pseudonyms, out)
    for name in ("apc_list", "apc_paid"):
        apc = out.get(name)
        if isinstance(apc, dict):
            for amount in ("value", "value_usd"):
                if apc.get(amount) is not None:
                    apc[amount] = 0
    return out


def _author_key(authorship: Mapping[str, Any]) -> str | None:
    author = authorship.get("author") if isinstance(authorship.get("author"), dict) else {}
    for candidate in (author.get("id"), author.get("display_name"), authorship.get("raw_author_name")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _replacement(path: str, value: str, pseudonyms: Pseudonyms, context: dict[str, Any]) -> str:
    action = REDACTION_POLICY[path]
    if action == "keep":
        return value
    if action == "mask_price":
        return "XXX"
    if path in {"authorships[].author.id", "corresponding_author_ids[]"}:
        return f"https://openalex.org/A{pseudonyms.number('author', value):010d}"
    if path in {"authorships[].author.display_name", "authorships[].raw_author_name"}:
        key = context.get("author_key") or value
        return f"Author {pseudonyms.number('author', key)}"
    if path.endswith("orcid") or path.endswith("observed_orcids[]"):
        bare = value.strip().rsplit("/", 1)[-1].upper()
        return _synthetic_orcid(pseudonyms.number("orcid", bare), url=value.strip().lower().startswith("http"))
    if "affiliation" in path:
        return f"Affiliation {pseudonyms.number('affiliation', value)}"
    if path in {"title", "display_name"}:
        return f"Title {pseudonyms.number('title', value)}"
    if path.endswith(("landing_page_url", "pdf_url", "oa_url")):
        number = pseudonyms.number("url", value)
        suffix = ".pdf" if path.endswith("pdf_url") or value.lower().endswith(".pdf") else ""
        return f"https://example.org/u{number}{suffix}"
    if path.endswith(".id") and path.split(".", 1)[0] in {"primary_location", "best_oa_location", "locations[]"}:
        return f"loc:{pseudonyms.number('location', value)}"
    if path.endswith("raw_source_name"):
        return f"Source {pseudonyms.number('source', value)}"
    if path == "awards[].display_name":
        return f"Award {pseudonyms.number('award', value)}"
    if path == "awards[].funder_award_id":
        return f"AWARD-{pseudonyms.number('award_id', value)}"
    raise RedactionError("unknown string path")


def _redact_node(node: Any, path: str, pseudonyms: Pseudonyms, context: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if path == "abstract_inverted_index":
            return {f"w{pseudonyms.number('word', key)}": value for key, value in node.items()}
        local = context
        if path == "authorships[]":
            local = {**context, "author_key": _author_key(node)}
        for key in list(node):
            child_path = f"{path}.{key}" if path else key
            child = node[key]
            if isinstance(child, str):
                node[key] = _replacement(child_path, child, pseudonyms, local)
            else:
                node[key] = _redact_node(child, child_path, pseudonyms, local)
        return node
    if isinstance(node, list):
        element_path = f"{path}[]"
        for index, child in enumerate(node):
            if isinstance(child, str):
                node[index] = _replacement(element_path, child, pseudonyms, context)
            else:
                node[index] = _redact_node(child, element_path, pseudonyms, context)
        return node
    return node


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^0-9a-z]+", " ", text.casefold()).split()


def person_tokens(record: Mapping[str, Any]) -> frozenset[str]:
    tokens: set[str] = set()
    for path, value in string_leaf_paths(record):
        if path in {"authorships[].author.display_name", "authorships[].raw_author_name"}:
            tokens.update(t for t in _tokens(value) if len(t) >= 4 and t not in _STOP_TOKENS)
    return frozenset(tokens)


def leak_errors(original: Mapping[str, Any], masked: Mapping[str, Any]) -> tuple[str, ...]:
    """Author-name tokens, author ids or ORCIDs of ``original`` still present in ``masked``."""
    serialized = json.dumps(masked, ensure_ascii=False, sort_keys=True).casefold()
    masked_tokens = set(_tokens(serialized))
    errors: set[str] = set()
    if person_tokens(original) & masked_tokens:
        errors.add("author name token remains")
    for path, value in string_leaf_paths(original):
        if path in {"authorships[].author.id", "corresponding_author_ids[]"}:
            if value.casefold() in serialized:
                errors.add("author id remains")
        elif path.endswith("orcid") or path.endswith("observed_orcids[]"):
            bare = value.strip().rsplit("/", 1)[-1].casefold()
            if bare and bare in serialized:
                errors.add("ORCID remains")
    return tuple(sorted(errors))


def redaction_errors(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Post-conditions every committed fixture record must satisfy (empty when clean)."""
    errors: list[str] = []
    for path, value in string_leaf_paths(record):
        if path not in REDACTION_POLICY:
            errors.append(f"{path} is not in the redaction policy")
            continue
        pattern = _SYNTHETIC.get(path)
        if pattern is not None and not pattern.fullmatch(value):
            errors.append(f"{path} is not masked")
        if path == "abstract_inverted_index{key}" and not re.fullmatch(r"w\d+", value):
            errors.append("abstract_inverted_index keys are not masked")
    if _abstract_value_errors(record):
        errors.append("abstract_inverted_index values are not lists of word positions")
    if EMAIL_PATTERN.search(json.dumps(record, ensure_ascii=False)):
        errors.append("an e-mail-shaped string remains")
    for name in ("apc_list", "apc_paid"):
        apc = record.get(name)
        if isinstance(apc, dict) and any(apc.get(k) not in (None, 0) for k in ("value", "value_usd")):
            errors.append(f"{name} amount is not masked")
    return tuple(sorted(set(errors)))


def _structural_paths(record: Mapping[str, Any]) -> frozenset[str]:
    paths: set[str] = set()
    stack: list[tuple[str, Any]] = [("", record)]
    while stack:
        path, value = stack.pop()
        if path:
            paths.add(path)
        if isinstance(value, dict):
            if path == "abstract_inverted_index":
                for child in value.values():
                    stack.append((f"{path}{{value}}", child))
                continue
            for key, child in value.items():
                stack.append((f"{path}.{key}" if path else key, child))
        elif isinstance(value, list):
            for child in value:
                stack.append((f"{path}[]", child))
    return frozenset(paths)


@dataclasses.dataclass(frozen=True)
class FixtureSelectionResult:
    records: tuple[dict[str, Any], ...]
    candidates: int
    skipped: SkipCounts
    unknown_string_paths: tuple[str, ...]


def select_fixture_records(
    records: Iterable[Mapping[str, Any]], *, count: int = FIXTURE_RECORDS,
    max_record_bytes: int = FIXTURE_MAX_RECORD_BYTES,
) -> FixtureSelectionResult:
    """Pick ``count`` records by greedy new field-path coverage (ties by id) and mask them."""
    skipped: Counter[str] = Counter()
    unknown: set[str] = set()
    candidates: list[tuple[str, frozenset[str], Mapping[str, Any]]] = []
    total = 0
    for record in records:
        total += 1
        identifier = record.get("id")
        if not isinstance(identifier, str):
            skipped["unknown_path"] += 1
            continue
        if record.get("is_paratext") is True or record.get("type") in EXCLUDED_TYPES:
            skipped["type"] += 1
            continue
        record_unknown = unknown_string_paths(record)
        if record_unknown:
            unknown.update(record_unknown)
            skipped["unknown_path"] += 1
            continue
        if len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > max_record_bytes:
            skipped["too_large"] += 1
            continue
        trial = redact_work_record(record, Pseudonyms())
        if EMAIL_PATTERN.search(json.dumps(trial, ensure_ascii=False)):
            skipped["email"] += 1
            continue
        if leak_errors(record, trial):
            skipped["leak"] += 1
            continue
        candidates.append((identifier, _structural_paths(record), record))
    chosen: list[tuple[str, Mapping[str, Any]]] = []
    covered: set[str] = set()
    remaining = sorted(candidates, key=lambda item: item[0])
    while remaining and len(chosen) < count:
        best_gain = max(len(item[1] - covered) for item in remaining)
        best = next(item for item in remaining if len(item[1] - covered) == best_gain)
        remaining.remove(best)
        covered |= best[1]
        chosen.append((best[0], best[2]))
    if len(chosen) < count:
        raise RedactionError("not enough records pass the redaction rules")
    chosen.sort(key=lambda item: item[0])
    pseudonyms = Pseudonyms()
    masked = tuple(redact_work_record(record, pseudonyms) for _, record in chosen)
    for (_, original), result in zip(chosen, masked, strict=True):
        if redaction_errors(result) or leak_errors(original, result):
            raise RedactionError("masked fixture record fails its post-conditions")
    return FixtureSelectionResult(
        records=masked, candidates=total, skipped=SkipCounts(**skipped),
        unknown_string_paths=tuple(sorted(unknown)),
    )


# =====================================================================================
# Fidelity
# =====================================================================================

REPRESENTATION_RULES: Final = {
    "abstract_inverted_index": "object -> JSON string",
    "ids": "object -> map<string,string>",
}
JSON_TO_ARROW_FAMILIES: Final = {
    "string": frozenset({"string", "large_string", "date32[day]", "timestamp[us, tz=UTC]",
                         "timestamp[ms, tz=UTC]", "timestamp[us]", "timestamp[ms]"}),
    "integer": frozenset({"int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}),
    "number": frozenset({"float", "double", "int8", "int16", "int32", "int64"}),
    "boolean": frozenset({"bool"}),
}


def _parquet_path(jsonl_path: str) -> str:
    if jsonl_path.startswith("ids."):
        return "ids{value}"
    if jsonl_path.startswith("abstract_inverted_index{value}"):
        return "abstract_inverted_index"
    return jsonl_path


def _compatible(path: str, jsonl: FieldProfile, parquet: FieldProfile) -> bool:
    types = set(jsonl.data_type.split("|")) - {"null"}
    if path in REPRESENTATION_RULES:
        expected = {"ids": "map", "abstract_inverted_index": "scalar"}[path]
        return parquet.kind == expected and types <= {"object"}
    if path.startswith("abstract_inverted_index{value}"):
        return parquet.data_type in {"string", "large_string"}
    if not types:
        return True
    if types <= {"object"}:
        return parquet.kind in {"struct", "map"}
    if types <= {"array"}:
        return parquet.kind == "list"
    if {"object", "array"} & types or parquet.kind != "scalar":
        return False
    return all(parquet.data_type in JSON_TO_ARROW_FAMILIES.get(kind, ()) for kind in types)


def compare_field_paths(
    jsonl_fields: Iterable[FieldProfile], parquet_fields: Iterable[FieldProfile]
) -> FieldPathFidelity:
    """Every sampled JSONL path must exist in the exact Parquet schema with a compatible type."""
    jsonl = {field.path: field for field in jsonl_fields}
    parquet = {field.path: field for field in parquet_fields}
    missing: list[str] = []
    conflicts: list[str] = []
    mapped: set[str] = set()
    for path, field in sorted(jsonl.items()):
        target = _parquet_path(path)
        mapped.add(target)
        if target not in parquet:
            missing.append(path)
        elif not _compatible(path, field, parquet[target]):
            conflicts.append(path)
    if "ids" in jsonl:
        mapped.add("ids{key}")
    return FieldPathFidelity(
        jsonl_paths=len(jsonl), parquet_paths=len(parquet),
        missing_in_parquet=tuple(missing), type_conflicts=tuple(conflicts),
        representation_rules=dict(REPRESENTATION_RULES),
        unobserved_in_jsonl_sample=len(set(parquet) - mapped),
    )


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _canonical(value: Any, *, float32: bool) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(child, float32=float32) for key, child in value.items()}
    if isinstance(value, list):
        return [_canonical(child, float32=float32) for child in value]
    if isinstance(value, float) and not isinstance(value, bool):
        value = _float32(value) if float32 else value
        return int(value) if value.is_integer() else value
    return value


def _format_timestamp(value: datetime) -> str:
    """JSONL form ``YYYY-MM-DDTHH:MM:SS.mmmZ``; sub-millisecond digits are truncated."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


def normalize_parquet_value(value: Any, data_type: pa.DataType) -> Any:
    """Convert one ``to_pylist`` value to the JSON shape OpenAlex publishes in JSONL."""
    if value is None:
        return None
    if pa.types.is_map(data_type):
        return {
            normalize_parquet_value(key, data_type.key_type):
                normalize_parquet_value(item, data_type.item_type)
            for key, item in value
        }
    if pa.types.is_struct(data_type):
        return {
            data_type.field(index).name: normalize_parquet_value(
                value.get(data_type.field(index).name), data_type.field(index).type)
            for index in range(data_type.num_fields)
        }
    if pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
        return [normalize_parquet_value(item, data_type.value_type) for item in value]
    if pa.types.is_timestamp(data_type):
        return _format_timestamp(value)
    if pa.types.is_date(data_type):
        return value.isoformat()
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_field_digests(record: Mapping[str, Any], *, float32: bool) -> dict[str, str]:
    return {key: _digest(_canonical(value, float32=float32)) for key, value in record.items()}


def parquet_row_to_json(row: Mapping[str, Any], schema: pa.Schema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in schema:
        value = normalize_parquet_value(row.get(field.name), field.type)
        if field.name == "abstract_inverted_index" and isinstance(value, str):
            value = json.loads(value)
        out[field.name] = value
    return out


def compare_representations(
    jsonl_records: Iterable[Mapping[str, Any]], parquet_rows: Iterable[Mapping[str, Any]],
    *, schema: pa.Schema, max_records: int = 10_000,
) -> ValueFidelity:
    """Join by ``id``; compare every top-level field exactly and after float32 rounding."""
    exact_left: dict[str, dict[str, str]] = {}
    f32_left: dict[str, dict[str, str]] = {}
    order_left: list[str] = []
    for record in jsonl_records:
        if len(order_left) >= max_records:
            raise OpenAlexProfileLimitError("fidelity comparison exceeds its record bound")
        identifier = str(record.get("id"))
        order_left.append(identifier)
        exact_left[identifier] = record_field_digests(record, float32=False)
        f32_left[identifier] = record_field_digests(record, float32=True)
    order_right: list[str] = []
    mismatched: Counter[str] = Counter()
    widened: Counter[str] = Counter()
    sub_millisecond: Counter[str] = Counter()
    absent_null: Counter[str] = Counter()
    timestamp_fields = [field.name for field in schema if pa.types.is_timestamp(field.type)]
    fields: set[str] = set()
    compared = 0
    for row in parquet_rows:
        if len(order_right) >= max_records:
            raise OpenAlexProfileLimitError("fidelity comparison exceeds its record bound")
        for name in timestamp_fields:
            value = row.get(name)
            if isinstance(value, datetime) and value.microsecond % 1000:
                sub_millisecond[name] += 1
        record = parquet_row_to_json(row, schema)
        identifier = str(record.get("id"))
        order_right.append(identifier)
        if identifier not in exact_left:
            continue
        compared += 1
        exact = record_field_digests(record, float32=False)
        rounded = record_field_digests(record, float32=True)
        for name in set(exact) | set(exact_left[identifier]):
            fields.add(name)
            if name not in exact_left[identifier]:
                (absent_null if record.get(name) is None else mismatched)[name] += 1
            elif name not in exact:
                mismatched[name] += 1
            elif exact_left[identifier][name] != exact[name]:
                if f32_left[identifier][name] == rounded[name]:
                    widened[name] += 1
                else:
                    mismatched[name] += 1
    return ValueFidelity(
        records_compared=compared,
        jsonl_records_parsed=len(order_left),
        ids_equal=sorted(order_left) == sorted(order_right),
        row_order_equal=order_left == order_right,
        fields_compared=len(fields),
        mismatched_fields=dict(sorted(mismatched.items())),
        float32_widened_fields=dict(sorted(widened.items())),
        parquet_sub_millisecond_fields=dict(sorted(sub_millisecond.items())),
        jsonl_absent_parquet_null_fields=dict(sorted(absent_null.items())),
    )


def iter_parquet_rows_bounded(
    path: Path, *, max_rows: int, max_batch_bytes: int = 50_000_000, batch_size: int = 256,
) -> Iterator[dict[str, Any]]:
    """Yield at most ``max_rows`` rows, one bounded record batch at a time."""
    yielded = 0
    with pq.ParquetFile(path, pre_buffer=False) as parquet_file:
        for batch in parquet_file.iter_batches(batch_size=batch_size, use_threads=False):
            if batch.nbytes > max_batch_bytes:
                raise OpenAlexProfileLimitError("Parquet batch exceeds its decoded byte limit")
            for row in batch.to_pylist():
                if yielded >= max_rows:
                    raise OpenAlexProfileLimitError("Parquet rows exceed the fidelity bound")
                yielded += 1
                yield row


# =====================================================================================
# Canonical compatibility (the mapper is imported by this script only)
# =====================================================================================

NORMALISATION: Final = (
    "created_date and updated_date cut to their first 10 characters (YYYY-MM-DD)",
    "a location source host_organization that is not a publisher (P) id set to null",
)
_SAFE_MESSAGE = re.compile(r"[A-Za-z]+Error: [A-Za-z0-9_. ,'-]{1,120}")


def safe_failure(error: BaseException) -> str:
    """``Class: message`` only when the message cannot carry a record value."""
    message = f"{type(error).__name__}: {error}"
    if (
        _SAFE_MESSAGE.fullmatch(message)
        and "@" not in message
        and "http" not in message
        and not re.search(r"\d{6,}", message)
    ):
        return message
    return type(error).__name__


def diagnostic_normalisation(record: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    for key in ("created_date", "updated_date"):
        if isinstance(result.get(key), str):
            result[key] = result[key][:10]
    locations = [result.get("primary_location"), result.get("best_oa_location"), *(result.get("locations") or [])]
    for location in locations:
        if isinstance(location, dict) and isinstance(location.get("source"), dict):
            host = location["source"].get("host_organization")
            if isinstance(host, str) and not host.rsplit("/", 1)[-1].startswith("P"):
                location["source"]["host_organization"] = None
    return result


def canonical_compatibility(records: Sequence[Mapping[str, Any]]) -> CanonicalCompatibility:
    from research_platform.canonical.openalex import FIELD_CATALOG, map_openalex_work
    from research_platform.canonical.openalex.models import CanonicalLineage

    lineage = CanonicalLineage.model_validate({
        "source_asset_id": "oa-" + "0" * 64, "source_checksum_sha256": "0" * 64,
        "source_updated_date": date(2000, 1, 1), "run_id": UUID(int=0),
        "processed_at": datetime(2000, 1, 1, tzinfo=UTC),
    })
    keys: set[str] = set()
    failures: Counter[str] = Counter()
    mapped = normalised = 0
    for record in records:
        keys.update(record)
        try:
            map_openalex_work(record, lineage=lineage)
            mapped += 1
        except (ValueError, TypeError) as error:
            failures[safe_failure(error)] += 1
        try:
            map_openalex_work(diagnostic_normalisation(record), lineage=lineage)
            normalised += 1
        except (ValueError, TypeError):
            pass
    return CanonicalCompatibility(
        top_level_keys=tuple(sorted(keys)),
        uncatalogued=tuple(sorted(keys - set(FIELD_CATALOG))),
        catalogued_absent=tuple(sorted(set(FIELD_CATALOG) - keys)),
        records=len(records), mapped_unchanged=mapped,
        mapper_first_failures=dict(sorted(failures.items())),
        mapped_with_documented_normalisation=normalised, normalisation=NORMALISATION,
    )


def duplicate_dynamic_keys(lines: Iterable[bytes]) -> DuplicateDynamicKeys:
    scanned = with_duplicates = 0
    for line in lines:
        if not line.strip():
            continue
        found = [False]

        def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            if len({key for key, _ in pairs}) != len(pairs):
                found[0] = True
            return dict(pairs)

        json.loads(line, object_pairs_hook=hook)
        scanned += 1
        with_duplicates += 1 if found[0] else 0
    return DuplicateDynamicKeys(records_scanned=scanned, records_with_duplicates=with_duplicates)


# =====================================================================================
# Fixture profile
# =====================================================================================

REPRODUCIBLE_PROFILE_EXCLUDES: Final = frozenset({
    "declared_size_bytes", "content_length_bytes", "observed_size_bytes", "bytes_read", "sha256",
})
"""Profile fields that depend on the local zlib build, not on the records."""


def real_framed_gzip(records: Iterable[Mapping[str, Any]]) -> bytes:
    """Frame records like the real parts: compact UTF-8 JSON lines in one gzip member
    with header ``1f8b0800000000000003`` (FLG 0, MTIME 0, XFL 0, OS 3)."""
    body = b"".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )
    compressor = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    deflated = compressor.compress(body) + compressor.flush()
    return (
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + deflated
        + zlib.crc32(body).to_bytes(4, "little") + (len(body) % (1 << 32)).to_bytes(4, "little")
    )


def _asset(uri: str, snapshot_date: date, size: int, records: int | None) -> OpenAlexAssetMetadata:
    match = _PART_URI.fullmatch(uri)
    return OpenAlexAssetMetadata(
        source="openalex", snapshot_date=snapshot_date, entity="works", file_uri=uri,
        byte_size=size, record_count=records, updated_date=match.group(2) if match else None,
        content_format="parquet" if uri.endswith(".parquet") else "jsonl",
    )


def _local_connector(
    content_format: str, path: Path, max_bytes: int, *, content_type: str | None = None
) -> OpenAlexConnector:
    return OpenAlexConnector(
        sample_selection=SampleSelectionConfig(max_file_size_bytes=max_bytes),
        content_format=content_format, max_attempts=1, retry_backoff_seconds=0,
        client=LocalFileClient(path, content_type=content_type),
    )


FIXTURE_PROFILE_LIMITATION: Final = (
    "Profile of the committed masked fixture (5 records) re-framed locally as gzip; "
    "source_uri names the part the records were selected from; size, sha256 and "
    "record/row counts describe the re-framed fixture, not that object, and no "
    "content type was served for it."
)


def profile_redacted_fixture(fixture: RedactedFixture, scratch: Path) -> tuple[OpenAlexSourceProfile, bytes]:
    """Profile the committed masked records framed as a real JSONL.GZ part (offline).

    The profile describes the re-framed fixture bytes, not the real object named by
    ``source_uri``; its first limitation says so.
    """
    body = real_framed_gzip(fixture.records)
    path = Path(scratch) / "fixture.gz"
    path.write_bytes(body)
    asset = _asset(fixture.source.jsonl_uri, fixture.snapshot_date, len(body), len(fixture.records))
    profile = profile_openalex_asset(
        _local_connector("jsonl", path, max(len(body), 1)), asset, JSONL_PROFILE_LIMITS
    )
    profile = profile.model_copy(
        update={"limitations": (FIXTURE_PROFILE_LIMITATION, *profile.limitations)}
    )
    return profile, body


# =====================================================================================
# Renderer
# =====================================================================================

SECTION_HEADING_PREFIX: Final = "## Real snapshot evidence ("


def _n(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _yes(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _code(value: str | None) -> str:
    return "-" if value is None else f"`{value}`"


def _quote_line(line: QuotedLine) -> str:
    return f"`{line.file}` line {line.line}: \"{line.text.strip()}\""


_MDX_ESCAPES: Final = str.maketrans({"<": r"\<", "{": r"\{", "}": r"\}"})


def _mdx_safe(line: str) -> str:
    """Escape ``<``, ``{`` and ``}`` outside code spans: the docs site parses Markdown as MDX."""
    parts = re.split(r"(`[^`\n]*`)", line)
    return "".join(part if index % 2 else part.translate(_MDX_ESCAPES) for index, part in enumerate(parts))


def _line_refs(lines: Sequence[QuotedLine]) -> str:
    """``README.txt`` lines 11, 12 and ``RELEASE_NOTES.txt`` line 4 (file order kept)."""
    by_file: dict[str, list[int]] = {}
    for line in lines:
        by_file.setdefault(line.file, []).append(line.line)
    return " and ".join(
        f"`{name}` line{'s' if len(numbers) > 1 else ''} {', '.join(str(n) for n in numbers)}"
        for name, numbers in by_file.items()
    ) or "none"


def render_snapshot_evidence_section(evidence: SnapshotEvidence) -> str:
    """The generated ``## Real snapshot evidence (<date>)`` section of the profiling page."""
    e = evidence
    day = e.snapshot_date.isoformat()
    pair = e.profiled_pair
    jp, pp = pair.jsonl.profile, pair.parquet.profile
    footer = e.large_parquet_footer
    lines = [
        f"{SECTION_HEADING_PREFIX}{day})",
        "",
        f"Generated from [`snapshot_evidence_{day}.json`](../../tests/fixtures/openalex_real/"
        f"snapshot_evidence_{day}.json) by `render_snapshot_evidence_section` in "
        "[`openalex_snapshot_inspection_126.py`](../../scripts/openalex_snapshot_inspection_126.py). "
        "A test compares this section with the JSON byte for byte, so regenerate it from the "
        "evidence; never edit it by hand.",
        "",
        f"**Evidence label: {e.evidence}.** {e.environment}; host `{e.host}`; generated from "
        f"{e.generated_from}; run {e.run.started_at} to {e.run.finished_at}. This is not cloud or "
        "enterprise evidence.",
        "",
        "### Request budget used",
        "",
        "| Requests | Body bytes | Whole objects | Range reads | Largest body | Caps (object / run / requests) |",
        "|---:|---:|---:|---:|---:|---|",
        f"| {e.run.requests} | {_n(e.run.body_bytes)} | {e.run.whole_objects} | {e.run.range_reads} "
        f"| {_n(e.run.largest_body_bytes)} | {_n(e.run.limits.max_object_bytes)} / "
        f"{_n(e.run.limits.max_total_bytes)} / {e.run.limits.max_requests} |",
        "",
        "No object above the per-object cap was requested whole. Tools: Python "
        f"{e.run.tool_versions.get('python', '-')}, PyArrow {e.run.tool_versions.get('pyarrow', '-')}.",
        "",
        "### Layout and full listing",
        "",
        f"- Bucket root files: {', '.join(_code(k) for k in e.layout.root_files)}; prefixes: "
        f"{', '.join(_code(p) for p in e.layout.root_prefixes)}.",
        f"- `data/jsonl/` and `data/parquet/` each hold a combined `manifest.json` and "
        f"{len(e.layout.entities)} entities ({', '.join(e.layout.entities)}); the Parquet entity "
        f"list equals the JSONL one: {_yes(e.layout.parquet_entities_equal)}.",
        f"- `legacy-data/merged_ids/` prefixes (frozen pre-2026 layout, listed only): "
        f"{', '.join(_code(p) for p in e.layout.legacy_merged_ids_prefixes) or 'none'}.",
        f"- Full key listing: {_yes(e.layout.listing_coverage.full_listing)} for "
        + ", ".join(
            f"`{p}` ({_n(e.layout.listing_coverage.keys[p.split('/')[1]])} keys, "
            f"{e.layout.listing_coverage.pages[p.split('/')[1]]} pages)"
            for p in e.layout.listing_coverage.prefixes
        )
        + ".",
        "",
        "| Format | Manifest files | Listed keys | Missing from listing | Size mismatches | Single-part / multipart ETags | Checksum algorithm | Keys not referenced by a manifest |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for name in ("jsonl", "parquet"):
        check = e.listing_checks[name]
        unreferenced = "; ".join(
            f"`{item.key_pattern}` x {item.count}" for item in e.layout.unreferenced_objects[name]
        )
        algorithms = ", ".join(f"{k} {v:,}" for k, v in check.checksum_algorithm.items())
        lines.append(
            f"| {name} | {_n(check.manifest_files)} | {_n(check.listed_files)} "
            f"| {check.missing_from_listing} | {check.size_mismatches} "
            f"| {_n(check.etag_single_part)} / {_n(check.etag_multipart)} | {algorithms} | {unreferenced} |"
        )
    lines += [
        "",
        "### Snapshot totals (combined manifests)",
        "",
        "| Format | Date | Records | Bytes | Files | Entities | Sums equal totals |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for name in ("jsonl", "parquet"):
        summary = e.combined_manifests[name]
        lines.append(
            f"| {name} | {summary.date.isoformat()} | {_n(summary.totals.record_count)} "
            f"| {_n(summary.totals.content_length)} | {_n(summary.totals.files)} "
            f"| {len(summary.entities)} | {_yes(summary.sums_equal_totals)} |"
        )
    lines += [
        "",
        "| Entity | Records | JSONL bytes | Parquet bytes | Files per format | Partitions |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    parquet_entities = {item.entity: item for item in e.combined_manifests["parquet"].entities}
    for item in e.combined_manifests["jsonl"].entities:
        other = parquet_entities.get(item.entity)
        lines.append(
            f"| `{item.entity}` | {_n(item.record_count)} | {_n(item.content_length)} "
            f"| {_n(other.content_length if other else None)} | {_n(item.files)} | {_n(item.partitions)} |"
        )
    lines += [
        "",
        "### T01: bounded discovery on the real Works manifests",
        "",
        "| Format | Manifest bytes | Within 1,000,000 | Listed | Eligible at 25,000,000 | OVERSIZED | UNKNOWN_SIZE | Smallest / median / largest file | Partitions | Default selection |",
        "|---|---:|---|---:|---:|---:|---:|---|---:|---|",
    ]
    for name in ("jsonl", "parquet"):
        w = e.works_manifests[name]
        lines.append(
            f"| {name} | {_n(w.manifest_bytes)} | {_yes(w.within_manifest_limit)} | {_n(w.listed)} "
            f"| {_n(w.eligible)} | {_n(w.oversized)} | {_n(w.unknown_size)} "
            f"| {_n(w.size_min)} / {_n(w.size_median_low)} / {_n(w.size_max)} | {_n(w.partitions)} "
            f"| {_n(w.default_selection_bytes)} B, {_n(w.default_selection_records)} record(s) |"
        )
    lines += [
        "",
        f"JSONL/Parquet Works pairs: {_n(e.works_pairs.pairs)}; record-count mismatches: "
        f"{e.works_pairs.record_count_mismatches}; pairs with both files within the per-object "
        f"cap: {_n(e.works_pairs.both_within_object_cap)}.",
        "",
        "Combined-manifest versions (seen only in version listings; prior versions are never fetched):",
        "",
        "| Key | Last modified | Bytes | Within the manifest cap | Latest |",
        "|---|---|---:|---|---|",
    ]
    for version in e.prior_release_visibility.manifest_versions:
        lines.append(
            f"| `{version.key}` | {version.last_modified or '-'} | {_n(version.size)} "
            f"| {_yes(version.within_manifest_limit)} | {_yes(version.is_latest)} |"
        )
    lines += [
        "",
        "### T02: formats verified on real bytes",
        "",
        f"Profiled pair `{pair.key}` ({pair.rule}); each file was downloaded once and profiled "
        "from the verified local copy through the unchanged connector and profiler.",
        "",
        "| Object | Bytes | Records | Framing | SHA-256 (first 16) | CRC64NVME S3 = local | MD5 = ETag | HEAD ETag = GET ETag |",
        "|---|---:|---:|---|---|---|---|---|",
    ]
    for obj in (pair.jsonl, pair.parquet):
        if isinstance(obj.framing, GzipFraming):
            framing = (
                f"gzip header `{obj.framing.header_hex}`, {obj.framing.members} member(s), "
                f"CRC32 ok {_yes(obj.framing.trailer_crc_ok)}, ISIZE ok {_yes(obj.framing.trailer_isize_ok)}"
            )
        else:
            framing = (
                f"`PAR1` at both ends: {_yes(obj.framing.leading_magic and obj.framing.trailing_magic)}; "
                f"codecs {', '.join(obj.framing.codecs)}; {obj.framing.row_groups} row group(s); "
                f"statistics {obj.framing.statistics}"
            )
        lines.append(
            f"| `{obj.uri}` | {_n(obj.size)} | {_n(obj.record_count)} | {framing} | `{obj.sha256[:16]}` "
            f"| {_yes(obj.crc64nvme_s3 == obj.crc64nvme_local)} | {_yes(obj.md5_equals_etag)} "
            f"| {_yes(obj.head_etag_equals_get_etag)} |"
        )
    lines += [
        "",
        f"- JSONL: line count {_n(jp.row_count)} (one record per line by JSONL framing; all "
        f"{_n(e.fidelity.values.jsonl_records_parsed)} lines parsed by the script) equals the manifest "
        f"record count; {_n(jp.sampled_record_count)} records sampled for the schema; "
        f"{_n(len(jp.schema_fields))} field paths, {_n(len(jp.nested_fields))} nested; "
        f"{jp.sampled_duplicate_key_records} sampled record(s) repeat an `abstract_inverted_index` word key.",
        f"- Parquet: `{pp.parquet_created_by}`; footer-only profile with {_n(pp.row_count)} rows, "
        f"{pp.column_count} top-level and {pp.physical_column_count} leaf columns and "
        f"{_n(len(pp.schema_fields))} schema paths; the profiler read no values.",
        f"- Large Parquet part `{footer.uri}` ({_n(footer.size)} B): two If-Match Range reads "
        f"({_n(footer.range_bytes)} B) gave a {_n(footer.footer_bytes)} B footer with "
        f"{_n(footer.num_rows)} rows (manifest {_n(footer.manifest_record_count)}) in "
        f"{footer.row_groups} row groups of {_n(footer.rows_per_group_min)} to "
        f"{_n(footer.rows_per_group_max)} rows; codecs {', '.join(footer.codecs)}; schema equal to "
        f"the profiled pair: {_yes(footer.schema_equals_profiled_pair)}.",
        "",
        "### Fidelity: JSONL vs Parquet on the profiled pair",
        "",
        f"- Paths: {e.fidelity.paths.jsonl_paths} sampled JSONL paths and "
        f"{e.fidelity.paths.parquet_paths} Parquet schema paths; JSONL paths missing in Parquet: "
        f"{len(e.fidelity.paths.missing_in_parquet)}; type conflicts: "
        f"{len(e.fidelity.paths.type_conflicts)}; Parquet paths not seen in the JSONL sample: "
        f"{e.fidelity.paths.unobserved_in_jsonl_sample}.",
        "- Representation rules: "
        + "; ".join(f"`{k}` {v}" for k, v in sorted(e.fidelity.paths.representation_rules.items()))
        + ".",
        f"- Values: {_n(e.fidelity.values.records_compared)} records joined by `id` over "
        f"{e.fidelity.values.fields_compared} top-level fields; same id set: "
        f"{_yes(e.fidelity.values.ids_equal)}; same row order: {_yes(e.fidelity.values.row_order_equal)}; "
        f"fields with value differences: {len(e.fidelity.values.mismatched_fields)}; fields equal only "
        "after float32 rounding: "
        + (", ".join(f"`{k}` ({v:,})" for k, v in e.fidelity.values.float32_widened_fields.items()) or "none")
        + "; Parquet timestamps with sub-millisecond digits that JSONL shows only to milliseconds "
        "(truncation vs rounding not determined): "
        + (", ".join(f"`{k}` ({v:,})" for k, v in e.fidelity.values.parquet_sub_millisecond_fields.items()) or "none")
        + ".",
        "",
        "### Canonical model compatibility",
        "",
        f"- Real Works records have {len(e.canonical_compatibility.top_level_keys)} top-level keys. "
        f"Not in `FIELD_CATALOG`: {', '.join(_code(k) for k in e.canonical_compatibility.uncatalogued) or 'none'}. "
        f"Catalogued but absent: {', '.join(_code(k) for k in e.canonical_compatibility.catalogued_absent) or 'none'}.",
        f"- The unchanged canonical mapper accepted {_n(e.canonical_compatibility.mapped_unchanged)} of "
        f"{_n(e.canonical_compatibility.records)} records of the profiled JSONL file. First failures:",
        "",
        "| First failure | Records |",
        "|---|---:|",
    ]
    for message, count in e.canonical_compatibility.mapper_first_failures.items():
        lines.append(f"| `{message}` | {_n(count)} |")
    lines += [
        "",
        f"- With the diagnostic normalisation ({'; '.join(e.canonical_compatibility.normalisation)}), "
        f"{_n(e.canonical_compatibility.mapped_with_documented_normalisation)} records map. The mapper is "
        "unchanged here (handed to DE-10 and DE-08).",
        f"- Records with a repeated `abstract_inverted_index` word key: "
        f"{e.duplicate_dynamic_keys.records_with_duplicates} of {_n(e.duplicate_dynamic_keys.records_scanned)}.",
        "",
        "### Checksums",
        "",
        "- Manifests carry no checksum field. Single-part S3 ETags equal the body MD5; multipart "
        "ETags (`-N`) are not content hashes. HEAD with `x-amz-checksum-mode: ENABLED` returns a "
        "full-object CRC64NVME, compared above with a CRC64NVME of the retrieved bytes. The platform "
        "checksum is the SHA-256 of the exact retrieved bytes.",
        "",
        "### Deletions and merges",
        "",
        "| Object | Bytes | ETag | Last modified | Versions (last modified, bytes) |",
        "|---|---:|---|---|---|",
    ]
    for item in e.deletions.current:
        versions = (
            "not listed" if item.versions is None
            else "; ".join(f"{v.last_modified or '-'}, {_n(v.size)}" for v in item.versions) or "none"
        )
        lines.append(
            f"| `{item.uri}` | {_n(item.size)} | `{item.etag}` | {item.last_modified or '-'} | {versions} |"
        )
    prefix = e.deletions.prefix
    dates = ", ".join(f"{k}: {v:,}" for k, v in prefix.deleted_date_counts.items())
    lines += [
        "",
        "- Identical content under both trees (equal size, ETag and CRC64NVME; two distinct S3 "
        f"objects): {_yes(e.deletions.identical_content_under_both_trees)}. "
        + (
            "The ledger is above the per-object cap, so only an If-Match Range prefix was read"
            if e.deletions.current and e.deletions.current[0].size > e.run.limits.max_object_bytes
            else "Only an If-Match Range prefix was read"
        )
        + ", and its total row count is UNKNOWN. \"Cumulative\" is documented on help.openalex.org "
        "(Sync page, per the Step 13 contract; not read in DE-05) and consistent with growth between "
        "versions, not proven.",
        f"- Prefix of {_n(prefix.bytes)} B: header `{prefix.header}`; {_n(prefix.complete_rows)} complete "
        f"rows; row shape `https://openalex.org/W<digits>,YYYY-MM-DD`: {_yes(prefix.row_shape_ok)}; "
        f"sorted by work id: {_yes(prefix.sorted_by_work_id)}; deleted_date counts: {dates}.",
        "- Deletion or merge objects of other entities in `data/` (full listing): "
        f"{', '.join(_code(k) for k in e.deletions.non_works_deletion_objects) or 'none'}.",
        f"- Legacy `legacy-data/merged_ids/` prefixes (frozen, listed only): "
        f"{', '.join(_code(p) for p in e.deletions.legacy_merged_ids_prefixes) or 'none'}.",
    ]
    lines += [f"- {_quote_line(q)}" for q in e.deletions.release_note_quotes]
    lines += [
        "",
        "### Prior releases",
        "",
        f"- Anonymous S3 object versioning: {_yes(e.prior_release_visibility.anonymous_versioning)}; "
        f"current-layout combined JSONL manifest versions: {e.cadence.current_layout_manifest_versions}. "
        "Manifests carry no version ids, and no retention policy is published.",
        "",
        "### Rights (as the snapshot publishes them)",
        "",
        "| Item | What the snapshot shows | Status |",
        "|---|---|---|",
        f"| Licence | {_quote_line(e.rights.license_title_line)}; SHA-256 `{e.rights.license_sha256[:16]}`, "
        f"{_n(e.rights.license_bytes)} B, {e.rights.license_versions} version(s), same ETag: "
        f"{_yes(e.rights.license_versions_same_etag)} | {e.rights.statuses['license']} |",
        "| README licence line | "
        + (_quote_line(e.rights.readme_license_line) if e.rights.readme_license_line else "-")
        + f" | {e.rights.statuses['readme_license_line']} |",
        "| Third-party rights | "
        + (_quote_line(e.rights.third_party_rights_clause[0]) + " (clause continues)"
           if e.rights.third_party_rights_clause else "-")
        + f" | {e.rights.statuses['third_party_rights_clause']} |",
        f"| Attribution | {len(e.rights.attribution_statements)} matching line(s) | {e.rights.statuses['attribution']} |",
        "| Terms of use | none in the snapshot; help.openalex.org docs/sync links at "
        + _line_refs(e.rights.help_links)
        + f" (not read) | {e.rights.statuses['terms_of_use']} |",
        f"| Full article text | full-text field present: {_yes(e.rights.full_text_field_present)}; "
        f"{len(e.rights.full_text_statements)} full-text statement(s); indicators only: "
        + ", ".join(_code(p) for p in e.rights.full_text_indicator_paths)
        + f" | {e.rights.statuses['full_article_text']} |",
        f"| Abstracts | {', '.join(_code(p) for p in e.rights.abstract_paths)}; data-quality notes at "
        f"RELEASE_NOTES lines {', '.join(str(q.line) for q in e.rights.abstract_notes)} | "
        f"{e.rights.statuses['abstracts']} |",
        f"| Per-work licence fields | {', '.join(_code(p) for p in e.rights.per_work_license_paths) or 'none sampled'}; they "
        f"describe works and grant nothing | {e.rights.statuses['per_work_license_paths']} |",
        f"| Personal data | {len(e.rights.personal_data_paths)} person field paths, masked "
        f"(pseudonymised) in the committed fixture | {e.rights.statuses['personal_data_paths']} |",
        "",
        "Not verified:",
        "",
    ]
    lines += [f"- {item}" for item in e.rights.unverified]
    lines += ["", "### Cadence", "", "Stated (quoted):", ""]
    lines += [f"- {_quote_line(q)}" for q in e.cadence.stated]
    year = str(e.snapshot_date.year)
    lines += [
        "",
        f"Observed: {e.cadence.release_headings} release headings, {e.cadence.releases_by_year.get(year, 0)} "
        f"in {year} ({', '.join(d.isoformat() for d in e.cadence.releases_current_year) or 'none'}); "
        f"on a stated second Wednesday: "
        f"{', '.join(d.isoformat() for d in e.cadence.on_stated_schedule_current_year) or 'none'}. "
        f"{e.cadence.inference}",
        "",
        "### Masked fixture",
        "",
        f"- Policy version {e.redaction.rules_version} maps every observed string path to one action: "
        + ", ".join(f"{k} {v}" for k, v in e.redaction.policy_counts.items())
        + f". {_n(e.redaction.candidates)} candidate records; skipped: "
        + ", ".join(f"{k} {v}" for k, v in e.redaction.skipped.model_dump().items())
        + f"; unknown string paths: {', '.join(_code(p) for p in e.redaction.unknown_string_paths) or 'none'}.",
        "",
        "### Limitations",
        "",
    ]
    lines += [f"- {item}" for item in e.limitations]
    lines.append("")
    return "\n".join(_mdx_safe(line) for line in lines) + "\n"


# =====================================================================================
# CLI
# =====================================================================================

LIMITATIONS: Final = (
    "LOCAL bounded inspection from a sandbox container; not cloud or enterprise evidence.",
    "One JSONL/Parquet Works pair was downloaded whole; every other part is described by the "
    "manifests and the full listing only, and one large Parquet part was read footer-only.",
    "The JSONL schema is sampled from the first 300 records of the pair; the line count comes from "
    "the drain, and every line was parsed by the script for fidelity. The Parquet schema is exact "
    "for the profiled files only.",
    "Gzip member structure is known for the downloaded pair only; for large parts it is UNKNOWN.",
    "The deletion ledger is above the per-object cap: only a 65,536-byte prefix was read, so its "
    "row count is unknown and 'cumulative' is not proven.",
    "Rights and cadence are quoted from the snapshot's own LICENSE.txt, README.txt and "
    "RELEASE_NOTES.txt only; help.openalex.org was not read.",
    "The TLS peer is the sandbox's agent proxy, so integrity rests on matching sizes, record "
    "counts, S3 CRC64NVME and ETags, not on the TLS certificate of S3 itself.",
)


def _print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded, opt-in inspection of the public OpenAlex snapshot (DE-05, #126)."
    )
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--from-raw", type=Path)
    parser.add_argument("--budget-ledger", type=Path)
    parser.add_argument("--ledger-seed-bytes", type=int)
    parser.add_argument("--init-ledger-only", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--expect-snapshot-date")
    parser.add_argument("--pair", default="auto")
    parser.add_argument("--max-total-bytes", type=int, default=12_000_000)
    parser.add_argument("--max-object-bytes", type=int, default=MAX_INSPECTION_OBJECT_BYTES)
    parser.add_argument("--max-requests", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--keep-raw", action="store_true")
    return parser


def _parse_pair(value: str) -> tuple[str, int] | None:
    if value == "auto":
        return None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})/(\d{4})", value)
    if not match:
        raise ValueError("--pair must be auto or YYYY-MM-DD/NNNN")
    date.fromisoformat(match.group(1))
    return match.group(1), int(match.group(2))


def main(
    argv: Sequence[str] | None = None,
    *,
    opener: Callable[..., Any] | None = None,
    connector_client: Any = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Run the inspection; ``opener``/``connector_client`` are injected only by offline tests."""
    args = _parser().parse_args(argv)
    if args.budget_ledger is None:
        _print({"inspection": "refused", "error_category": "BudgetLedgerRequired"})
        return 2
    try:
        ledger_path = evidence_dir(args.budget_ledger, REPO)
    except SystemExit as refusal:
        _print({"inspection": "refused", "error_category": "OutInsideRepository", "message": str(refusal)})
        return 2
    ledger = SessionByteLedger(ledger_path)
    if args.init_ledger_only:
        if args.ledger_seed_bytes is None or ledger_path.exists():
            _print({"inspection": "refused", "error_category": "LedgerExistsOrSeedMissing"})
            return 2
        try:
            ledger.create(args.ledger_seed_bytes)
        except ValueError:
            _print({"inspection": "refused", "error_category": "InvalidLimits"})
            return 2
        _print({"inspection": "ledger-created", "used_bytes": ledger.used()})
        return 0
    if not args.allow_network and args.from_raw is None:
        _print({"inspection": "refused", "error_category": "NetworkOptInRequired"})
        return 2
    if args.out is None or args.expect_snapshot_date is None:
        _print({"inspection": "refused", "error_category": "InvalidArguments"})
        return 2
    try:
        expected = date.fromisoformat(args.expect_snapshot_date)
        limits = InspectionLimits(
            max_object_bytes=args.max_object_bytes, max_total_bytes=args.max_total_bytes,
            max_requests=args.max_requests, timeout_seconds=args.timeout_seconds,
        )
        pair = _parse_pair(args.pair)
    except (ValidationError, ValueError):
        _print({"inspection": "refused", "error_category": "InvalidLimits"})
        return 2
    try:
        out = evidence_dir(args.out, REPO)
        from_raw = evidence_dir(args.from_raw, REPO) if args.from_raw is not None else None
    except SystemExit as refusal:
        _print({"inspection": "refused", "error_category": "OutInsideRepository", "message": str(refusal)})
        return 2
    raw = from_raw if from_raw is not None else out / "raw"
    if from_raw is None and raw.exists() and not raw_is_owned(raw):
        _print({"inspection": "refused", "error_category": "RawNotOwned"})
        return 2
    if not ledger_path.exists():
        if args.ledger_seed_bytes is None:
            _print({"inspection": "refused", "error_category": "BudgetLedgerRequired"})
            return 2
        ledger.create(args.ledger_seed_bytes)
    now = clock or (lambda: datetime.now(UTC))
    started = now()
    try:
        budget = SnapshotByteBudget(
            limits, ledger=ledger, label="de05-inspection-replay" if from_raw else "de05-inspection",
            started_at=started.strftime("%Y-%m-%dT%H:%M:%SZ"), reserve=from_raw is None,
        )
    except OpenAlexBudgetExceededError:
        _print({"inspection": "refused", "error_category": "SessionBudgetExhausted"})
        return 2
    out.mkdir(parents=True, exist_ok=True)
    # A fresh directory of this run's own: only it is removed afterwards.
    scratch_tmp = Path(tempfile.mkdtemp(prefix="de05-tmp-", dir=out))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(scratch_tmp)
    replay = LocalReplayClient(raw) if from_raw is not None else None
    try:
        recorder = Recorder(None if from_raw is not None else raw)
        run = _Inspection(
            out=out, raw=raw, expected=expected, pair=pair, budget=budget, recorder=recorder,
            opener=replay or opener, connector_client=replay or connector_client,
            started=started, now=now, replayed=from_raw is not None,
        )
        outputs = run.execute()
    except OpenAlexSnapshotChangedError as error:
        _print({"inspection": "failed", "error_category": "SnapshotChanged", "detail": type(error).__name__,
                "requests": budget.requests, "body_bytes": budget.body_bytes})
        return 3
    except RedactionError:
        _print({"inspection": "failed", "error_category": "RedactionFailed",
                "requests": budget.requests, "body_bytes": budget.body_bytes})
        return 1
    except RawNotOwned:
        _print({"inspection": "refused", "error_category": "RawNotOwned"})
        return 2
    except (OpenAlexConnectorError, OpenAlexProfileError, LookupError, ValueError) as error:
        _print({"inspection": "failed", "error_category": type(error).__name__,
                "requests": budget.requests, "body_bytes": budget.body_bytes})
        return 1
    finally:
        budget.close(settle_bytes=0 if from_raw is not None else None)
        tempfile.tempdir = previous_tempdir
        shutil.rmtree(scratch_tmp, ignore_errors=True)
        if from_raw is None and not args.keep_raw and raw_is_owned(raw):
            shutil.rmtree(raw, ignore_errors=True)
    _print({
        "inspection": "inspected", "snapshot_date": expected.isoformat(),
        "generated_from": "recorded raw" if from_raw else "network",
        "requests": budget.requests,
        "network_body_bytes": 0 if from_raw else budget.body_bytes,
        "outputs": sorted(path.name for path in outputs),
        "session_ledger_used_bytes": ledger.used(),
    })
    return 0


class _Inspection:
    def __init__(
        self, *, out: Path, raw: Path, expected: date, pair: tuple[str, int] | None,
        budget: SnapshotByteBudget, recorder: Recorder, opener: Callable[..., Any] | None,
        connector_client: Any, started: datetime, now: Callable[[], datetime], replayed: bool,
    ) -> None:
        self.out = out
        self.raw = raw
        self.expected = expected
        self.pair = pair
        self.budget = budget
        self.recorder = recorder
        self.client = SnapshotInspectionClient(budget, recorder, opener=opener)
        self.budgeted = BudgetedOpenAlexClient(
            connector_client or AnonymousOpenAlexHTTPClient(), budget, recorder
        )
        self.started = started
        self.now = now
        self.replayed = replayed

    def _connector(self, content_format: str) -> OpenAlexConnector:
        return OpenAlexConnector(
            sample_selection=SampleSelectionConfig(max_file_size_bytes=MAX_INSPECTION_OBJECT_BYTES),
            content_format=content_format, timeout_seconds=self.budget.limits.timeout_seconds,
            max_attempts=1, retry_backoff_seconds=0, client=self.budgeted,
        )

    def execute(self) -> list[Path]:
        c = self.client
        root = c.list_objects("", delimiter=True, max_keys=100)
        merged = c.list_objects("legacy-data/merged_ids/", delimiter=True, max_keys=100)
        listings = {
            fmt: c.list_objects(f"data/{fmt}/", delimiter=False, max_keys=MAX_LISTING_KEYS,
                                max_pages=MAX_LISTING_PAGES)
            for fmt in ("jsonl", "parquet")
        }
        sizes = {item.key: item.size for item in root.objects}
        for listing in listings.values():
            sizes.update({item.key: item.size for item in listing.objects})
        texts = {key: c.get_text(key, expected_size=sizes.get(key))
                 for key in ("LICENSE.txt", "README.txt", "RELEASE_NOTES.txt")}
        start_heads = {fmt: c.head(f"data/{fmt}/manifest.json") for fmt in ("jsonl", "parquet")}
        versions = {key: c.list_versions(key) for key in (
            "LICENSE.txt", "RELEASE_NOTES.txt", "data/jsonl/manifest.json", "data/parquet/manifest.json",
            "data/jsonl/works/manifest.json", "data/jsonl/works/deleted_ids.csv.gz",
        )}
        combined: dict[str, CombinedManifestSummary] = {}
        combined_files: dict[str, dict[str, int]] = {}
        for fmt in ("jsonl", "parquet"):
            fetched = c.get_combined_manifest(fmt, expected_size=sizes.get(f"data/{fmt}/manifest.json"))
            if fetched.etag != start_heads[fmt].etag:
                raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
            combined[fmt] = summarize_combined_manifest(fetched.body, etag=fetched.etag,
                                                        version_id=fetched.version_id)
            combined_files[fmt] = combined_manifest_files(fetched.body)
        works: dict[str, WorksManifestSummary] = {}
        assets: dict[str, tuple[OpenAlexAssetMetadata, ...]] = {}
        for fmt in ("jsonl", "parquet"):
            connector = self._connector(fmt)
            self.budgeted.expect(connector.manifest_uri, sizes[f"data/{fmt}/works/manifest.json"])
            connector.discover_metadata()
            body = self.recorder.last_body()
            works[fmt], assets[fmt] = summarize_works_manifest(body, fmt)
        check_manifest_dates(self.expected, {
            "combined jsonl": combined["jsonl"].date, "combined parquet": combined["parquet"].date,
            "works jsonl": works["jsonl"].date, "works parquet": works["parquet"].date,
        })
        digest = works_manifest_digest(self.expected, assets["jsonl"], assets["parquet"])
        day, part = self.pair or select_profile_pair(digest)
        jsonl_uri, parquet_uri = pair_uris(day, part)
        for fmt, uri in (("jsonl", jsonl_uri), ("parquet", parquet_uri)):
            # Checked before the first pair request: an operator --pair may name no part.
            if not any(item.file_uri == uri for item in assets[fmt]):
                raise LookupError("pair is not listed in the Works manifest")
        local = {
            "jsonl": self._download(jsonl_uri, assets["jsonl"]),
            "parquet": self._download(parquet_uri, assets["parquet"]),
        }
        largest = max(assets["parquet"], key=lambda asset: (asset.byte_size or 0, asset.file_uri))
        large_key = largest.file_uri.removeprefix("s3://openalex/")
        large_head = c.head(large_key)
        if large_head.content_length != largest.byte_size or large_head.etag is None:
            raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
        size = large_head.content_length
        last8 = c.get_range(large_key, size - 8, size - 1, head=large_head)
        footer_len = int.from_bytes(last8[:4], "little")
        if footer_len > FOOTER_LIMITS.max_parquet_footer_bytes:
            raise OpenAlexProfileLimitError("Parquet footer exceeds the metadata byte limit")
        tail = c.get_range(large_key, size - footer_len - 8, size - 1, head=large_head)
        deletion_heads = {fmt: c.head(f"data/{fmt}/works/deleted_ids.csv.gz") for fmt in ("jsonl", "parquet")}
        ledger_head = deletion_heads["jsonl"]
        prefix = c.get_range(ledger_head.key, 0, min(MAX_LEDGER_PREFIX_BYTES, ledger_head.content_length) - 1,
                             head=ledger_head)
        end_heads = {fmt: c.head(f"data/{fmt}/manifest.json") for fmt in ("jsonl", "parquet")}
        if any(end_heads[fmt].etag != start_heads[fmt].etag for fmt in ("jsonl", "parquet")):
            raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
        finished = self.now()
        self.recorder.release_buffers()
        if not self.replayed:
            (self.raw / "run.json").write_text(json.dumps({
                "started_at": self.started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, indent=2) + "\n", encoding="utf-8")
        # ---- local analysis only below this line --------------------------------------
        return self._analyse(
            root=root, merged=merged, listings=listings, texts=texts, versions=versions,
            combined=combined, combined_files=combined_files, works=works, assets=assets,
            digest=digest, pair_key=f"updated_date={day}/part_{part:04d}", local=local,
            large=(largest, large_head, tail, footer_len, len(last8) + len(tail)),
            deletion_heads=deletion_heads, prefix=prefix, finished=finished,
        )

    def _download(self, uri: str, assets: Sequence[OpenAlexAssetMetadata]) -> dict[str, Any]:
        """HEAD, then one GET through the unchanged connector into a verified local copy."""
        asset = next((item for item in assets if item.file_uri == uri), None)
        if asset is None:
            raise LookupError("pair is not listed in the Works manifest")
        if asset.byte_size is None or asset.byte_size > PAIR_MAX_FILE_BYTES:
            raise OpenAlexBudgetExceededError("pair file exceeds the pair byte bound")
        key = uri.removeprefix("s3://openalex/")
        head = self.client.head(key)
        if head.content_length != asset.byte_size or head.etag is None:
            raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
        objects = self.raw / "objects"
        objects.mkdir(parents=True, exist_ok=True)
        final = objects / key.replace("/", "__")
        partial = final.with_name(final.name + ".partial")
        replay = self.budgeted._inner if self.replayed else None
        recorded_get = isinstance(replay, LocalReplayClient) and replay.has("GET", _public_path(uri))
        if (
            not recorded_get
            and final.exists()
            and final.stat().st_size == asset.byte_size
            and head.crc64nvme is not None
            and crc64nvme_s3(crc64nvme(final.read_bytes())) == head.crc64nvme
        ):
            # A verified copy from an earlier run of this --out: reuse it (0 bytes).
            # No GET was made, so no response Content-Type is known for it.
            body = final.read_bytes()
            return {"asset": asset, "head": head, "path": final, "crc": head.crc64nvme,
                    "md5": md5_matches_etag(hashlib.md5(body, usedforsecurity=False).hexdigest(), head.etag),
                    "get_etag": head.etag, "version_id": head.version_id, "content_type": None}
        connector = self._connector(asset.content_format)
        self.budgeted.expect(uri, asset.byte_size)
        stream = connector.fetch(asset.to_source_asset())
        try:
            with partial.open("wb") as handle:
                while chunk := stream.read(_CHUNK):
                    handle.write(chunk)
            get_etag, get_version = stream.etag, stream.version_id
            get_content_type = stream.content_type
        finally:
            stream.close()
        body = partial.read_bytes()
        local_crc = crc64nvme_s3(crc64nvme(body))
        md5 = md5_matches_etag(hashlib.md5(body, usedforsecurity=False).hexdigest(), head.etag)
        if len(body) != asset.byte_size:
            partial.unlink()
            raise OpenAlexTruncatedError("OpenAlex payload size differs from the manifest")
        if head.crc64nvme is not None and local_crc != head.crc64nvme:
            partial.unlink()
            raise OpenAlexChecksumMismatchError("OpenAlex object checksum differs from its S3 CRC64NVME")
        if md5 is False:
            partial.unlink()
            raise OpenAlexChecksumMismatchError("OpenAlex object MD5 differs from its single-part ETag")
        if get_etag != head.etag:
            partial.unlink()
            raise OpenAlexSnapshotChangedError("OpenAlex object changed during inspection")
        os.replace(partial, final)
        return {"asset": asset, "head": head, "path": final, "crc": local_crc, "md5": md5,
                "get_etag": get_etag, "version_id": get_version or head.version_id,
                "content_type": get_content_type}

    def _profile(self, item: dict[str, Any]) -> ProfiledObject:
        asset: OpenAlexAssetMetadata = item["asset"]
        limits = JSONL_PROFILE_LIMITS if asset.content_format == "jsonl" else PARQUET_PROFILE_LIMITS
        profile = profile_openalex_asset(
            _local_connector(asset.content_format, item["path"], asset.byte_size or 1,
                             content_type=item["content_type"]),
            asset, limits,
        )
        body = item["path"].read_bytes()
        if profile.sha256 != hashlib.sha256(body).hexdigest():
            raise OpenAlexChecksumMismatchError("profiled bytes differ from the downloaded bytes")
        head: ObjectHead = item["head"]
        return ProfiledObject(
            uri=asset.file_uri, size=len(body), record_count=asset.record_count or 0,
            etag=head.etag, version_id=item["version_id"], last_modified=head.last_modified,
            crc64nvme_s3=head.crc64nvme, crc64nvme_local=item["crc"], sha256=profile.sha256,
            md5_equals_etag=item["md5"], head_etag_equals_get_etag=item["get_etag"] == head.etag,
            profile=profile,
            framing=gzip_framing(body) if asset.content_format == "jsonl" else parquet_framing(body, profile),
        )

    def _analyse(self, **parts: Any) -> list[Path]:
        jsonl_obj = self._profile(parts["local"]["jsonl"])
        parquet_obj = self._profile(parts["local"]["parquet"])
        jsonl_path = parts["local"]["jsonl"]["path"]
        parquet_path = parts["local"]["parquet"]["path"]
        lines = gzip.decompress(jsonl_path.read_bytes()).split(b"\n")
        records = [json.loads(line) for line in lines if line.strip()]
        selection = select_fixture_records(records)
        fixture = RedactedFixture(
            snapshot_date=self.expected,
            source=FixtureSource(
                jsonl_uri=jsonl_obj.uri, jsonl_sha256=jsonl_obj.sha256, jsonl_version_id=jsonl_obj.version_id,
                parquet_uri=parquet_obj.uri, parquet_sha256=parquet_obj.sha256,
                parquet_version_id=parquet_obj.version_id, candidate_records=selection.candidates,
            ),
            selection=FixtureSelection(
                rule="greedy new field-path coverage; ties by id; record JSON <= 20000 B; tokenized "
                     "leak scan; no e-mail; no paratext",
                count=len(selection.records), skipped=selection.skipped,
            ),
            redaction=FixtureRedaction(rules_version=REDACTION_RULES_VERSION, policy_counts=policy_counts()),
            records=selection.records,
        )
        for record in fixture.records:
            if redaction_errors(record):
                raise RedactionError("masked fixture record fails its post-conditions")
        with tempfile.TemporaryDirectory(prefix="de05-fixture-") as fixture_tmp:
            fixture_profile, _ = profile_redacted_fixture(fixture, Path(fixture_tmp))
        schema = pq.read_schema(parquet_path).remove_metadata()
        fidelity = FidelityRecord(
            paths=compare_field_paths(jsonl_obj.profile.schema_fields, parquet_obj.profile.schema_fields),
            values=compare_representations(
                records, iter_parquet_rows_bounded(parquet_path, max_rows=10_000), schema=pq.read_schema(parquet_path),
            ),
        )
        texts = parts["texts"]
        versions = parts["versions"]
        rights = extract_rights(
            texts["LICENSE.txt"].body.decode("utf-8"), texts["README.txt"].body.decode("utf-8"),
            texts["RELEASE_NOTES.txt"].body.decode("utf-8"), license_sha256=texts["LICENSE.txt"].sha256,
            license_versions=versions["LICENSE.txt"],
            profile_paths=[field.path for field in jsonl_obj.profile.schema_fields],
        )
        if rights.license_title != "CC0 1.0 Universal":
            raise RightsChanged("OpenAlex snapshot licence title changed")
        cadence = extract_cadence(
            texts["README.txt"].body.decode("utf-8"), texts["RELEASE_NOTES.txt"].body.decode("utf-8"),
            manifest_versions=len(versions["data/jsonl/manifest.json"]), current_year=self.expected.year,
        )
        listing_checks: dict[str, ListingCheck] = {}
        unreferenced: dict[str, tuple[KeyPatternCount, ...]] = {}
        entity_sets: dict[str, tuple[str, ...]] = {}
        for fmt in ("jsonl", "parquet"):
            listing_checks[fmt], unreferenced[fmt] = listing_check(
                parts["listings"][fmt], parts["combined_files"][fmt], content_format=fmt
            )
            entity_sets[fmt] = tuple(sorted({
                item.key.split("/")[2] for item in parts["listings"][fmt].objects
                if item.key.count("/") >= 3
            }))
        largest, large_head, tail, footer_len, range_bytes = parts["large"]
        inspection = inspect_parquet_footer(tail, file_size=large_head.content_length)
        metadata = pq.read_metadata(pa.BufferReader(b"PAR1" + tail[-(footer_len + 8):]))
        rows = [metadata.row_group(i).num_rows for i in range(metadata.num_row_groups)]
        uncompressed = [metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups)]
        deletion_heads = parts["deletion_heads"]
        root = parts["root"]
        evidence = SnapshotEvidence(
            generated_from="recorded raw" if self.replayed else "network",
            run=self._run_record(parts["finished"]),
            snapshot_date=self.expected,
            layout=LayoutRecord(
                root_prefixes=root.common_prefixes,
                root_files=tuple(item.key for item in root.objects),
                entities=entity_sets["jsonl"],
                parquet_entities_equal=entity_sets["jsonl"] == entity_sets["parquet"],
                legacy_merged_ids_prefixes=parts["merged"].common_prefixes,
                listing_coverage=ListingCoverage(
                    full_listing=True, prefixes=("data/jsonl/", "data/parquet/"),
                    keys={fmt: len(parts["listings"][fmt].objects) for fmt in ("jsonl", "parquet")},
                    pages={fmt: parts["listings"][fmt].pages for fmt in ("jsonl", "parquet")},
                ),
                unreferenced_objects=unreferenced,
            ),
            listing_checks=listing_checks,
            published_files=tuple(
                PublishedFile(
                    key=key, content_length=len(fetched.body), etag=fetched.etag,
                    md5_equals_etag=fetched.md5_equals_etag, sha256=fetched.sha256,
                    last_modified=fetched.last_modified, version_id=fetched.version_id,
                    line_count=len(fetched.body.decode("utf-8").splitlines()),
                )
                for key, fetched in texts.items()
            ),
            combined_manifests=parts["combined"],
            works_manifests=parts["works"],
            works_pairs=summarize_pairs(parts["digest"], max_object_bytes=self.budget.limits.max_object_bytes),
            profiled_pair=ProfiledPair(
                key=parts["pair_key"], rule=AUTO_PAIR_RULE if self.pair is None else "operator-chosen pair",
                jsonl=jsonl_obj, parquet=parquet_obj,
            ),
            arrow_schema_fields=arrow_schema_fields(schema),
            large_parquet_footer=LargeParquetFooter(
                uri=largest.file_uri, size=large_head.content_length, etag=large_head.etag,
                footer_bytes=footer_len, range_bytes=range_bytes, num_rows=inspection.row_count,
                manifest_record_count=largest.record_count, row_groups=inspection.row_group_count,
                rows_per_group_min=min(rows), rows_per_group_max=max(rows),
                uncompressed_per_group_min=min(uncompressed), uncompressed_per_group_max=max(uncompressed),
                codecs=tuple(sorted((inspection.compression or "").split(","))),
                created_by=inspection.created_by,
                schema_equals_profiled_pair=metadata.schema.to_arrow_schema().remove_metadata().equals(schema),
            ),
            fidelity=fidelity,
            canonical_compatibility=canonical_compatibility(records),
            duplicate_dynamic_keys=duplicate_dynamic_keys(lines),
            deletions=DeletionsRecord(
                current=tuple(
                    DeletionObject(
                        uri=f"s3://openalex/{head.key}", size=head.content_length, etag=head.etag,
                        last_modified=head.last_modified, crc64nvme_s3=head.crc64nvme,
                        versions=tuple(
                            ObjectVersionRecord(last_modified=v.last_modified, size=v.size,
                                                is_latest=v.is_latest, etag=v.etag)
                            for v in versions[head.key]
                        ) if head.key in versions else None,
                    )
                    for head in deletion_heads.values()
                ),
                identical_content_under_both_trees=(
                    deletion_heads["jsonl"].etag == deletion_heads["parquet"].etag
                    and deletion_heads["jsonl"].content_length == deletion_heads["parquet"].content_length
                    and deletion_heads["jsonl"].crc64nvme is not None
                    and deletion_heads["jsonl"].crc64nvme == deletion_heads["parquet"].crc64nvme
                ),
                prefix=summarize_deletion_prefix(parts["prefix"]),
                non_works_deletion_objects=tuple(sorted(
                    item.key_pattern for fmt in ("jsonl", "parquet") for item in unreferenced[fmt]
                    if re.search(r"(?i)delet|merge", item.key_pattern)
                    and not item.key_pattern.startswith("works/")
                )),
                legacy_merged_ids_prefixes=parts["merged"].common_prefixes,
                release_note_quotes=deletion_note_lines(texts["RELEASE_NOTES.txt"].body.decode("utf-8")),
            ),
            prior_release_visibility=PriorReleaseVisibility(
                manifest_versions=summarize_manifest_versions(
                    (*versions["data/jsonl/manifest.json"], *versions["data/parquet/manifest.json"])
                ),
                anonymous_versioning=bool(versions["data/jsonl/manifest.json"]),
            ),
            rights=rights,
            cadence=cadence,
            redaction=RedactionSummary(
                rules_version=REDACTION_RULES_VERSION, policy_counts=policy_counts(),
                candidates=selection.candidates, skipped=selection.skipped,
                unknown_string_paths=selection.unknown_string_paths,
            ),
            fixture_profile=fixture_profile,
            limitations=LIMITATIONS,
        )
        if structural_path_errors(evidence_schema_paths(evidence)):
            raise ValueError("evidence would commit non-structural schema paths")
        return [
            _write(self.out / "snapshot_evidence.json", canonical_json(evidence)),
            _write(self.out / "works_manifest_digest.json", canonical_json(parts["digest"])),
            _write(self.out / "works_redacted_sample.json", canonical_json(fixture)),
        ]

    def _run_record(self, finished: datetime) -> RunRecord:
        started_at = self.started.strftime("%Y-%m-%dT%H:%M:%SZ")
        finished_at = finished.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.replayed and (self.raw / "run.json").exists():
            recorded = json.loads((self.raw / "run.json").read_text(encoding="utf-8"))
            started_at, finished_at = recorded["started_at"], recorded["finished_at"]
        entries = self.recorder.log()
        gets = [entry for entry in entries if entry["method"] == "GET"]
        return RunRecord(
            started_at=started_at, finished_at=finished_at,
            limits=RunLimits(
                max_object_bytes=self.budget.limits.max_object_bytes,
                max_total_bytes=self.budget.limits.max_total_bytes,
                max_requests=self.budget.limits.max_requests,
            ),
            requests=self.budget.requests, body_bytes=self.budget.body_bytes,
            whole_objects=sum(1 for e in gets if e["byte_range"] is None and not e["path"].startswith("/?")),
            range_reads=sum(1 for e in gets if e["byte_range"] is not None),
            largest_body_bytes=max((e["body_bytes"] for e in entries), default=0),
            request_log=tuple(RequestRecord.model_validate(e) for e in entries),
            tool_versions=_tool_versions(),
        )


def _tool_versions() -> dict[str, str]:
    try:
        package = importlib.metadata.version("research-intelligence-data-platform")
    except importlib.metadata.PackageNotFoundError:
        package = "unknown"
    return {"python": platform.python_version(), "pyarrow": pa.__version__, "research_platform": package}


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
