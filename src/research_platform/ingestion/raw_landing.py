"""Shared immutable raw landing for the Step 12/13 pipelines (DE-06, #127).

Streams one source asset to a local temporary file while hashing, verifies it
against the declared source object and checks gzip completeness *before* any
``put_if_absent``, lands it create-only, verifies the store's own object facts,
and returns a landing-manifest entry. ``land_raw_asset`` calls the clock once
(``retrieved_at``), like the per-pipeline helpers it replaces.

Error messages never carry keys, buckets, projects or provider text. Provider
and transport failures are wrapped as ``RawLandingError`` (``LANDING``) only for
an allowlist of exception types; anything else (a programming error) propagates
and is classified ``UNKNOWN`` by the stage.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import io
import re
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, BinaryIO, Literal, Protocol
from uuid import UUID

from research_platform.config.models import PlatformConfig
from research_platform.control.models import FailureCategory
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionFormatError,
    IngestionRetrievalError,
    RawLandingError,
    SourceChecksumMismatchError,
    SourceListingMismatchError,
    SourcePayloadIncompleteError,
    SourceSizeMismatchError,
)
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.connector import (
    OpenAlexConnectorError,
    OpenAlexFormatError,
)
from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.errors import ObjectConflictError, ObjectStoreError
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.landing_manifest import (
    LandingCheck,
    LandingManifest,
    LandingManifestEntry,
    dumps_landing_manifest,
    landing_manifest_provenance,
    read_landing_manifest,
)
from research_platform.storage.local import LocalObjectStore

# Temporary allow-list until DE-13/DE-27 own environment configuration; it is
# not an enterprise approval (the #125 inventory items are still required).
GCS_LANDING_ENVIRONMENTS = frozenset({"gcp-sandbox"})

_CHUNK_SIZE = 64 * 1024
_SINGLE_PART_ETAG = re.compile(r"^[a-f0-9]{32}$")
_GZIP_MAGIC = b"\x1f\x8b"
_MISSING = object()


class _Fetcher(Protocol):
    def fetch(self, asset: SourceAsset) -> BinaryIO: ...


class _LandableAsset(Protocol):
    asset_id: str
    file_uri: str
    byte_size: int | None
    snapshot_date: date
    updated_date: date | None
    content_format: str

    def to_source_asset(self) -> SourceAsset: ...


@dataclass(frozen=True)
class DeclaredPayload:
    """Source response metadata a payload stream declares (``OpenAlexPayloadStream``)."""

    content_length: int | None
    etag: str | None
    version_id: str | None


@dataclass(frozen=True)
class LandedRaw:
    checksum: str
    retrieved_at: datetime
    entry: LandingManifestEntry


@functools.cache
def provider_errors() -> tuple[type[BaseException], ...]:
    """Provider and transport exception types that are wrapped as ``LANDING``.

    ``OSError`` covers ``requests.RequestException``, ``ConnectionError``,
    ``TimeoutError`` and ``PermissionError``. Imported lazily so importing the
    pipelines does not load the cloud client libraries.
    """
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError
    from google.cloud.storage.exceptions import DataCorruption, InvalidResponse

    return (GoogleAPIError, GoogleAuthError, DataCorruption, InvalidResponse, OSError)


def declared_payload(payload: object) -> DeclaredPayload | None:
    """Return declared metadata, or ``None`` for streams with no response metadata."""
    length = getattr(payload, "content_length", _MISSING)
    if length is _MISSING:
        return None
    etag = getattr(payload, "etag", None)
    version_id = getattr(payload, "version_id", None)
    return DeclaredPayload(
        content_length=length if isinstance(length, int) and not isinstance(length, bool) else None,
        etag=etag if isinstance(etag, str) else None,
        version_id=version_id if isinstance(version_id, str) else None,
    )


class _GzipCompleteness:
    """Streaming check that a gzip payload does not end inside a member.

    Active only when the payload starts with the gzip magic. Decompressed output
    is discarded. Zero padding between members is skipped. The check stops,
    without a verdict, at trailing bytes that do not start a member, at a corrupt
    deflate stream, or once more than ``limit`` bytes were decompressed; the
    decoder reports those later. A cut exactly at a member boundary is a valid,
    shorter gzip: only the size checks can catch it.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._decompressed = 0
        self._is_gzip: bool | None = None
        self._stopped = False
        self._inflater: Any = None
        self._pending = b""

    def update(self, chunk: bytes) -> None:
        if self._stopped:
            return
        data = self._pending + chunk
        self._pending = b""
        while data and not self._stopped:
            if self._inflater is None:
                if self._is_gzip:
                    data = data.lstrip(b"\x00")
                    if not data:
                        return
                if len(data) < len(_GZIP_MAGIC) and _GZIP_MAGIC.startswith(data):
                    self._pending = data
                    return
                if not data.startswith(_GZIP_MAGIC):
                    if self._is_gzip is None:
                        self._is_gzip = False
                    self._stopped = True
                    return
                self._is_gzip = True
                self._inflater = zlib.decompressobj(31)
            data = self._inflate(data)

    def _inflate(self, data: bytes) -> bytes:
        inflater = self._inflater
        try:
            while True:
                out = inflater.decompress(data, _CHUNK_SIZE)
                self._decompressed += len(out)
                if self._decompressed > self._limit:
                    self._stopped = True
                    return b""
                data = inflater.unconsumed_tail
                if inflater.eof or (not data and len(out) < _CHUNK_SIZE):
                    break
        except zlib.error:
            self._stopped = True
            return b""
        if inflater.eof:
            self._inflater = None
            return inflater.unused_data
        return b""

    @property
    def ends_inside_a_member(self) -> bool:
        return bool(self._is_gzip) and not self._stopped and (
            self._inflater is not None or self._pending != b""
        )

    @property
    def verified_complete(self) -> bool:
        return bool(self._is_gzip) and not self._stopped and not self.ends_inside_a_member


def _crc32c_checksum() -> Any:
    try:
        import google_crc32c
    except ImportError:  # pragma: no cover - a hard dependency of google-cloud-storage
        return None
    return google_crc32c.Checksum()


def land_raw_asset(
    *,
    asset: _LandableAsset,
    run_id: UUID,
    connector: _Fetcher,
    objects: ObjectStore,
    raw_key: str,
    max_bytes: int,
    decompressed_limit: int,
    storage_backend: str,
    clock: Callable[[], datetime],
    temp_prefix: str,
    temp_suffix: str,
) -> LandedRaw:
    """Verify, land create-only and confirm one raw object; return its manifest entry."""
    retrieved_at = clock()
    source_asset = asset.to_source_asset()
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    crc32c = _crc32c_checksum()
    gzip_check = _GzipCompleteness(decompressed_limit)
    size = 0
    checks: set[LandingCheck] = set()
    with tempfile.NamedTemporaryFile(prefix=temp_prefix, suffix=temp_suffix) as tmp:
        with connector.fetch(source_asset) as payload:
            declared = declared_payload(payload)
            if (
                declared is not None
                and declared.content_length is not None
                and asset.byte_size is not None
                and declared.content_length != asset.byte_size
            ):
                raise SourceListingMismatchError(
                    "source Content-Length differs from the asset's manifest size"
                )
            while True:
                chunk = payload.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise IngestionFormatError(
                        "retrieved payload exceeds configured max_file_size_bytes"
                    )
                sha256.update(chunk)
                md5.update(chunk)
                if crc32c is not None:
                    crc32c.update(chunk)
                gzip_check.update(chunk)
                tmp.write(chunk)
        md5_hex = md5.hexdigest()
        _verify_source(
            declared_size=asset.byte_size,
            declared=declared,
            storage_backend=storage_backend,
            size=size,
            md5_hex=md5_hex,
            checks=checks,
        )
        if gzip_check.ends_inside_a_member:
            raise SourcePayloadIncompleteError("gzip payload ends inside a member")
        if gzip_check.verified_complete:
            checks.add("gzip_complete")
        tmp.flush()
        tmp.seek(0)
        checksum = sha256.hexdigest()
        provenance = IngestionProvenance.model_validate(
            {
                "run_id": run_id,
                "source": "openalex",
                "source_uri": asset.file_uri,
                "retrieved_at": retrieved_at,
                "sha256": checksum,
            }
        )
        path_outcome: Literal["created", "existing_object_verified"] = "created"
        try:
            objects.put_if_absent(raw_key, tmp, provenance)
        except ObjectConflictError:
            # A new run_id, or a retry's new retrieved_at, differs from committed
            # provenance; accept the immutable object only when its bytes hash to
            # the same SHA-256.
            if not _raw_checksum_matches(objects, raw_key, checksum):
                raise
            path_outcome = "existing_object_verified"
        except ObjectStoreError:
            raise
        except provider_errors() as error:
            raise RawLandingError("immutable landing failed") from error

    crc32c_b64 = (
        None if crc32c is None else base64.b64encode(crc32c.digest()).decode("ascii")
    )
    stat = _stat(objects, raw_key)
    outcome = path_outcome
    committed: IngestionProvenance | None = None
    if stat is None:
        if storage_backend == "gcs":
            raise RawLandingError("store did not report object facts")
    else:
        committed = _verify_stat(
            stat,
            size=size,
            md5_hex=md5_hex,
            crc32c_b64=crc32c_b64,
            checksum=checksum,
            checks=checks,
        )
        if committed is not None:
            outcome = "created" if committed == provenance else "existing_object_verified"
    entry = LandingManifestEntry.model_validate(
        {
            "asset_id": asset.asset_id,
            "raw_object_key": raw_key,
            "source_uri": asset.file_uri,
            "snapshot_date": asset.snapshot_date,
            "updated_date": asset.updated_date,
            "content_format": asset.content_format,
            "declared_size_bytes": asset.byte_size,
            "size_bytes": size,
            "sha256": checksum,
            "md5_hex": md5_hex,
            "source_content_length": None if declared is None else declared.content_length,
            "source_etag": None if declared is None else declared.etag,
            "source_version_id": None if declared is None else declared.version_id,
            "retrieved_at": retrieved_at,
            "landing_outcome": outcome,
            "committed_run_id": None if committed is None else committed.run_id,
            "committed_retrieved_at": None if committed is None else committed.retrieved_at,
            "object_generation": None if stat is None else stat.version,
            "object_crc32c": None if stat is None else stat.crc32c,
            "object_md5_base64": None if stat is None else stat.md5_base64,
            "checks": tuple(sorted(checks)),
        }
    )
    return LandedRaw(checksum=checksum, retrieved_at=retrieved_at, entry=entry)


def _verify_source(
    *,
    declared_size: int | None,
    declared: DeclaredPayload | None,
    storage_backend: str,
    size: int,
    md5_hex: str,
    checks: set[LandingCheck],
) -> None:
    if declared is not None:
        expected = declared.content_length if declared.content_length is not None else declared_size
    elif storage_backend == "gcs":
        # A landed GCS object cannot be removed, so plain streams must match the
        # manifest size there. Not enforced locally: existing fixtures declare
        # fictitious sizes (DE-06 risk J1).
        expected = declared_size
    else:
        expected = None
    if expected is not None:
        if size != expected:
            raise SourceSizeMismatchError("retrieved byte count differs from the declared size")
        checks.add("declared_size")
    if declared is not None and declared.etag is not None and _SINGLE_PART_ETAG.fullmatch(declared.etag):
        if declared.etag != md5_hex:
            raise SourceChecksumMismatchError(
                "single-part source ETag differs from the MD5 of the retrieved bytes"
            )
        checks.add("source_etag_md5")


def _raw_checksum_matches(objects: ObjectStore, raw_key: str, checksum: str) -> bool:
    digest = hashlib.sha256()
    try:
        with objects.open(raw_key) as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except ObjectStoreError:
        raise
    except provider_errors() as error:
        raise RawLandingError("immutable landing verification failed") from error
    return digest.hexdigest() == checksum


def _stat(objects: ObjectStore, raw_key: str) -> ObjectStat | None:
    try:
        return objects.stat(raw_key)
    except ObjectStoreError:
        raise
    except provider_errors() as error:
        raise RawLandingError("immutable landing verification failed") from error


def _verify_stat(
    stat: ObjectStat,
    *,
    size: int,
    md5_hex: str,
    crc32c_b64: str | None,
    checksum: str,
    checks: set[LandingCheck],
) -> IngestionProvenance | None:
    """Compare the store's facts with the retrieved bytes; return committed provenance."""
    if stat.size_bytes != size:
        raise RawLandingError("landed object size differs from the retrieved bytes")
    checks.add("store_size")
    if stat.md5_base64 is not None:
        try:
            reported = base64.b64decode(stat.md5_base64, validate=True).hex()
        except (binascii.Error, ValueError) as error:
            raise RawLandingError("landed object md5 is unreadable") from error
        if reported != md5_hex:
            raise RawLandingError("landed object md5 differs from the retrieved bytes")
        checks.add("store_md5")
    if stat.crc32c is not None and crc32c_b64 is not None:
        if stat.crc32c != crc32c_b64:
            raise RawLandingError("landed object crc32c differs from the retrieved bytes")
        checks.add("store_crc32c")
    if stat.provenance is not None:
        if stat.provenance.sha256 != checksum:
            raise RawLandingError("landed object provenance differs from the retrieved bytes")
        checks.add("store_provenance")
    return stat.provenance


def write_run_landing_manifest(
    objects: ObjectStore, manifest: LandingManifest
) -> Literal["written", "existing_kept"]:
    """Create one run's manifest for one asset; keep an existing one with the same identity.

    A retry of the same run and asset (a task retry, a resumed run) finds the
    first manifest and keeps it when it names the same object and digest; any
    other existing content is ``RawLandingError`` (``LANDING``).
    """
    payload = dumps_landing_manifest(manifest)
    key = manifest.key
    try:
        objects.put_if_absent(
            key, io.BytesIO(payload), landing_manifest_provenance(manifest, payload)
        )
    except ObjectConflictError as error:
        try:
            existing = read_landing_manifest(objects, key)
        except ObjectStoreError:
            raise
        except provider_errors() as read_error:
            raise RawLandingError("landing manifest write failed") from read_error
        except ValueError:
            raise RawLandingError("landing manifest exists with different content") from error
        if _manifest_identity(existing) == _manifest_identity(manifest):
            return "existing_kept"
        raise RawLandingError("landing manifest exists with different content") from error
    except ObjectStoreError:
        raise
    except provider_errors() as error:
        raise RawLandingError("landing manifest write failed") from error
    return "written"


def _manifest_identity(manifest: LandingManifest) -> tuple[object, ...]:
    entry = manifest.entry
    return (
        manifest.run_id,
        manifest.entity,
        entry.asset_id,
        entry.raw_object_key,
        entry.sha256,
        entry.size_bytes,
    )


def classify_retrieval_or_landing(error: BaseException) -> tuple[FailureCategory, str] | None:
    """Typed classification shared by both pipelines; ``None`` when not applicable."""
    if isinstance(error, SourceListingMismatchError):
        return FailureCategory.VALIDATION, "source object differs from its manifest listing"
    if isinstance(error, SourceChecksumMismatchError):
        return FailureCategory.CHECKSUM, "source checksum verification failed"
    if isinstance(error, IngestionRetrievalError):
        return FailureCategory.RETRIEVAL, "source retrieval failed verification"
    if isinstance(error, RawLandingError):
        return FailureCategory.LANDING, "immutable landing failed"
    if isinstance(error, OpenAlexFormatError):
        return FailureCategory.VALIDATION, "unsupported or invalid source format"
    if isinstance(error, OpenAlexConnectorError):
        return FailureCategory.RETRIEVAL, "source retrieval failed"
    return None


def require_store_matches_config(
    config: PlatformConfig, objects: ObjectStore, *, label: str
) -> None:
    """An injected first-party store must match ``storage.backend``.

    Keeps the manifest's ``storage_backend`` truthful. Other ``ObjectStore``
    types (test doubles, wrappers) are not checked. A ``gcs`` backend is
    refused outside the GCS landing environments for every store type.
    """
    backend = config.storage.backend
    if backend == "gcs" and config.environment not in GCS_LANDING_ENVIRONMENTS:
        raise IngestionConfigError(
            f"{label} with storage.backend=gcs requires environment=gcp-sandbox"
        )
    if isinstance(objects, GCSObjectStore) and backend != "gcs":
        raise IngestionConfigError(f"{label} with a GCSObjectStore requires storage.backend=gcs")
    if isinstance(objects, LocalObjectStore) and backend != "local":
        raise IngestionConfigError(
            f"{label} with a LocalObjectStore requires storage.backend=local"
        )
