"""Caps, gating, fault injection and value-free recording for the opt-in live GCS test.

Used by ``tests/integration/test_approved_gcs_raw_landing.py`` (DE-06, #127) and
``tests/integration/test_canonical_gcs_release.py`` (DE-07, #128), and unit-tested
offline in ``tests/unit/test_gcs_live_harness.py`` and
``tests/unit/test_canonical_gcs_release_harness.py``. The module constants are the
DE-06 caps and prefix; a caller passes its own through the keyword parameters.
Nothing here runs in default CI against a real bucket. Every refusal is ``pytest.fail(...,
pytrace=False)`` with a message that never repeats a supplied value, so no
project, bucket or account name reaches the console.

The test code never lists or removes objects and never changes IAM or bucket
settings. The client library may try to remove an object after it reports a
checksum mismatch; ``FaultSession`` refuses that request before it is sent.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Final, NoReturn

import pytest

from research_platform.analytics.bigquery.sandbox.live import evidence_dir
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.keys import validate_object_key

LIVE_PREFIX: Final = "de06-live"
MAX_PUT_ATTEMPTS: Final = 16
MAX_OBJECT_BYTES: Final = 262_144
MAX_TOTAL_PUT_BYTES: Final = 1_048_576
MAX_HTTP_REQUESTS: Final = 200
READ_WRITE_SCOPE: Final = "https://www.googleapis.com/auth/devstorage.read_write"
PROJECT_CLASSES: Final = frozenset({"operator-sandbox", "personal"})
LABEL_CANDIDATE: Final = "CLOUD_SYNTHETIC_MEASURED"
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")


class InjectedAbort(OSError):
    """An injected transport failure; not retried by the client library."""


class RefusedDelete(PermissionError):
    """The harness never forwards a DELETE request."""


def refuse(message: str) -> NoReturn:
    """Stop the run with a value-free message and no traceback."""
    pytest.fail(message, pytrace=False)


@dataclass(frozen=True)
class LiveSettings:
    project: str
    bucket: str
    project_class: str
    evidence_dir: Path


def live_settings_from_env(env: Mapping[str, str], repo: Path) -> LiveSettings:
    """Validate the operator's confirmation variables before any client exists."""
    project = env.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project or project != env.get("GCS_LIVE_CONFIRM_PROJECT", "").strip():
        refuse("GOOGLE_CLOUD_PROJECT must be set and equal GCS_LIVE_CONFIRM_PROJECT")
    bucket = env.get("GCS_LIVE_BUCKET", "").strip()
    if not _BUCKET_NAME.fullmatch(bucket):
        refuse("GCS_LIVE_BUCKET must be the bare name of an existing bucket")
    if bucket != env.get("GCS_LIVE_CONFIRM_BUCKET", "").strip():
        refuse("GCS_LIVE_BUCKET must equal GCS_LIVE_CONFIRM_BUCKET")
    project_class = env.get("GCS_LIVE_PROJECT_CLASS", "").strip()
    if project_class not in PROJECT_CLASSES:
        refuse("GCS_LIVE_PROJECT_CLASS must be operator-sandbox or personal")
    raw_dir = env.get("GCS_LIVE_EVIDENCE_DIR", "").strip()
    if not raw_dir:
        refuse("GCS_LIVE_EVIDENCE_DIR must be set to a directory outside the repository")
    try:
        out = evidence_dir(Path(raw_dir), repo)
    except SystemExit:
        refuse("GCS_LIVE_EVIDENCE_DIR must be outside the repository")
    return LiveSettings(project=project, bucket=bucket, project_class=project_class, evidence_dir=out)


def require_real_cloud_client(credentials: object, env: Mapping[str, str]) -> None:
    """Refuse anonymous credentials and emulator endpoints: the run must reach real GCS."""
    from google.auth.credentials import AnonymousCredentials

    if isinstance(credentials, AnonymousCredentials):
        refuse("live run refused: anonymous credentials")
    if env.get("STORAGE_EMULATOR_HOST", "").strip():
        refuse("live run refused: STORAGE_EMULATOR_HOST is set")


def open_live_client(
    env: Mapping[str, str], repo: Path, *, max_http_requests: int = MAX_HTTP_REQUESTS
) -> tuple[LiveSettings, Any, FaultSession, str]:
    """Return settings, a storage client over a ``FaultSession``, and the credential type name.

    Settings are validated before Application Default Credentials are read.
    """
    settings = live_settings_from_env(env, repo)
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import storage

    credentials, _ = google.auth.default(scopes=[READ_WRITE_SCOPE])
    require_real_cloud_client(credentials, env)
    session = FaultSession(AuthorizedSession(credentials), max_requests=max_http_requests)
    client = storage.Client(project=settings.project, credentials=credentials, _http=session)
    return settings, client, session, type(credentials).__name__


def run_token(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"{moment.strftime('%Y%m%dt%H%M%Sz')}-{uuid.uuid4().hex[:8]}"


class FaultSession:
    """Wraps an HTTP session: caps requests, refuses DELETE, injects chunk faults.

    Only chunk PUTs to a resumable ``upload_id`` URL are touched; ``bytes */N``
    status queries pass through. ``abort_next_chunk_before_send`` raises
    ``InjectedAbort`` instead of sending (the session never finalizes).
    ``raise_after_next_final_chunk`` sends the final chunk, then raises
    ``InjectedAbort`` (a non-retryable error after the object committed).
    """

    def __init__(self, inner: Any, *, max_requests: int = MAX_HTTP_REQUESTS) -> None:
        self._inner = inner
        self.max_requests = max_requests
        self.abort_next_chunk_before_send = False
        self.raise_after_next_final_chunk = False
        self.http_requests = 0
        self.library_delete_attempts = 0
        self.precondition_failed_at: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        if self.http_requests + 1 > self.max_requests:
            refuse("live budget exceeded: refusing to send")
        self.http_requests += 1
        if method.upper() == "DELETE":
            self.library_delete_attempts += 1
            raise RefusedDelete("the live harness refuses DELETE requests")
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        content_range = str(headers.get("content-range", ""))
        chunk = method.upper() == "PUT" and "upload_id=" in url and not content_range.startswith("bytes */")
        if chunk and self.abort_next_chunk_before_send:
            self.abort_next_chunk_before_send = False
            raise InjectedAbort("injected abort before the chunk was sent")
        response = self._inner.request(method, url, *args, **kwargs)
        if getattr(response, "status_code", None) == 412 and "/upload/" in url:
            self.precondition_failed_at.append("initiation" if method.upper() == "POST" else "finalization")
        if chunk and self.raise_after_next_final_chunk and _is_final_chunk(content_range):
            self.raise_after_next_final_chunk = False
            raise InjectedAbort("injected error after the final chunk was sent")
        return response


def _is_final_chunk(content_range: str) -> bool:
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
    return match is not None and int(match[2]) + 1 == int(match[3])


class BudgetedPrefixStore(ObjectStore):
    """Confines every key under ``<live_prefix>/<token>/`` and enforces the put caps in code.

    A cap breach fails the run before the inner store is called. ``keys`` records
    the logical (unprefixed) keys that were attempted.
    """

    def __init__(
        self,
        inner: ObjectStore,
        token: str,
        *,
        live_prefix: str = LIVE_PREFIX,
        max_put_attempts: int = MAX_PUT_ATTEMPTS,
        max_object_bytes: int = MAX_OBJECT_BYTES,
        max_total_put_bytes: int = MAX_TOTAL_PUT_BYTES,
    ) -> None:
        self.inner = inner
        self.prefix = f"{live_prefix}/{token}"
        self.max_put_attempts = max_put_attempts
        self.max_object_bytes = max_object_bytes
        self.max_total_put_bytes = max_total_put_bytes
        validate_object_key(f"{self.prefix}/probe")
        self.put_attempts = 0
        self.put_bytes = 0
        self.keys: list[str] = []

    def physical_key(self, key: str) -> str:
        return str(validate_object_key(f"{self.prefix}/{key}"))

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        start = content.tell()
        size = content.seek(0, io.SEEK_END) - start
        content.seek(start)
        if (
            self.put_attempts + 1 > self.max_put_attempts
            or size > self.max_object_bytes
            or self.put_bytes + size > self.max_total_put_bytes
        ):
            refuse("live budget exceeded: refusing to upload")
        physical = self.physical_key(key)
        self.put_attempts += 1
        self.put_bytes += size
        self.keys.append(key)
        self.inner.put_if_absent(physical, content, provenance)

    def open(self, key: str) -> BinaryIO:
        return self.inner.open(self.physical_key(key))

    def stat(self, key: str) -> ObjectStat | None:
        return self.inner.stat(self.physical_key(key))


@dataclass
class CheckRecorder:
    """Records PASS, or FAIL with the exception type name only, for each named check."""

    results: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def check(self, name: str) -> Iterator[dict[str, Any]]:
        facts: dict[str, Any] = {}
        try:
            yield facts
        except BaseException as error:
            self.results.append({"check": name, "result": "FAIL", "error_type": type(error).__name__})
            raise
        self.results.append({"check": name, "result": "PASS", **facts})

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result["result"] == "PASS")


def contains_any(texts: Iterable[str], forbidden: Iterable[str]) -> bool:
    """True when any non-empty forbidden string occurs in any text; never echoes either."""
    needles = [needle for needle in forbidden if needle]
    return any(needle in text for text in texts for needle in needles)


def write_summary(path: Path, summary: Mapping[str, Any], forbidden: Iterable[str]) -> None:
    """Write ``summary.json`` only when it holds none of the forbidden identifiers."""
    text = json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    if contains_any([text], forbidden):
        refuse("identifier found in the evidence summary: nothing written")
    path.write_text(text, encoding="utf-8")
