"""GCS ObjectStore adapter with create-only immutable landing semantics.

Construction and import are side-effect free. Cloud I/O and ADC resolution occur
only when a storage operation is invoked (or when an injected ``client`` performs
I/O). No buckets, projects, IAM, or secrets are created here.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import PurePosixPath
from typing import BinaryIO, Final, Protocol, runtime_checkable

from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.keys import validate_object_key
from research_platform.storage.local import dumps_provenance

_PROVENANCE_NAME: Final = "provenance.json"
_CHUNK_SIZE: Final = 64 * 1024
_META_PROVENANCE: Final = "rp_provenance"
_LOGGER = logging.getLogger("research_platform.storage.gcs")


@runtime_checkable
class _Blob(Protocol):
    name: str
    metadata: dict[str, str] | None

    def upload_from_file(
        self,
        file_obj: BinaryIO,
        *,
        rewind: bool = False,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None: ...

    def reload(self) -> None: ...

    def open(self, mode: str = "rb") -> BinaryIO: ...


@runtime_checkable
class _Bucket(Protocol):
    def blob(self, blob_name: str) -> _Blob: ...


@runtime_checkable
class _Client(Protocol):
    def bucket(self, bucket_name: str) -> _Bucket: ...


class _CallerOwnedReadStream:
    """Read-only stream wrapper; caller must close it."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner

    def read(self, size: int = -1) -> bytes:
        return self._inner.read(size)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        seekable = getattr(self._inner, "seekable", None)
        return bool(seekable()) if callable(seekable) else False

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._inner.seek(offset, whence)

    def close(self) -> None:
        self._inner.close()

    @property
    def closed(self) -> bool:
        return bool(getattr(self._inner, "closed", False))

    def __enter__(self) -> _CallerOwnedReadStream:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class GCSObjectStore(ObjectStore):
    """Immutable GCS landing via generation precondition create-only uploads.

    Content bytes are stored at the logical object key. Required
    ``IngestionProvenance`` is stored in object custom metadata
    (``rp_provenance``) so a successful create publishes content and provenance
    as one coherent object record.

    Create uses ``if_generation_match=0`` (must not already exist). Lost races
    classify identical replay vs ``ObjectConflictError`` from authoritative
    metadata + content digest — never via check-then-write.
    """

    def __init__(
        self,
        storage: StorageConfig,
        cloud: CloudConfig,
        *,
        client: _Client | None = None,
    ) -> None:
        if storage.backend != "gcs":
            raise ValueError("GCSObjectStore requires storage.backend='gcs'")
        if not storage.bucket:
            raise ValueError("GCSObjectStore requires storage.bucket")
        if not cloud.project_id:
            raise ValueError("GCSObjectStore requires cloud.project_id")
        self.storage = storage
        self.cloud = cloud
        self._client = client
        self._bucket_name = storage.bucket

    def put_if_absent(
        self, key: str, content: BinaryIO, provenance: IngestionProvenance
    ) -> None:
        object_name, content_name = self._resolve_key(key)
        if content_name == _PROVENANCE_NAME:
            raise InvalidObjectKeyError("object key basename is reserved for provenance")

        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_name = tmp.name
        try:
            digest = self._stream_to_file(content, tmp)
            if digest != provenance.sha256:
                raise ChecksumMismatchError(
                    "streamed bytes do not match provenance sha256"
                )
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.seek(0)

            blob = self._blob(object_name)
            blob.metadata = {
                _META_PROVENANCE: dumps_provenance(provenance).decode("utf-8"),
            }
            try:
                blob.upload_from_file(
                    tmp,
                    rewind=True,
                    content_type="application/octet-stream",
                    if_generation_match=0,
                )
            except Exception as error:
                if not _is_precondition_failed(error):
                    raise
                _LOGGER.info(
                    "gcs put_if_absent precondition conflict; classifying replay",
                    extra={"object_key": object_name, "operation": "put_if_absent"},
                )
                self._replay_or_conflict(object_name, provenance)
                return
            _LOGGER.info(
                "gcs put_if_absent created",
                extra={"object_key": object_name, "operation": "put_if_absent"},
            )
        finally:
            tmp.close()
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def open(self, key: str) -> BinaryIO:
        object_name, content_name = self._resolve_key(key)
        if content_name == _PROVENANCE_NAME:
            raise InvalidObjectKeyError("object key basename is reserved for provenance")
        blob = self._blob(object_name)
        try:
            blob.reload()
        except Exception as error:
            if _is_not_found(error):
                raise ObjectNotFoundError("object not found") from error
            raise
        committed = self._provenance_from_blob(blob)
        digest, buffer = self._read_and_hash(blob)
        if digest != committed.sha256:
            buffer.close()
            raise IncompleteObjectError("committed content checksum is inconsistent")
        buffer.seek(0)
        return _CallerOwnedReadStream(buffer)

    def _resolve_key(self, key: str) -> tuple[str, str]:
        relative = validate_object_key(key)
        # Reject URI-shaped keys that could imply credential-bearing locations.
        lowered = key.lower()
        if lowered.startswith(("gs://", "s3://", "http://", "https://")):
            raise InvalidObjectKeyError("object key must not be a storage URI")
        content_name = PurePosixPath(relative).name
        return key, content_name

    def _client_or_create(self) -> _Client:
        if self._client is not None:
            return self._client
        # Lazy import + ADC: only on first real operation without injection.
        from google.cloud import storage  # type: ignore[import-untyped]

        self._client = storage.Client(project=self.cloud.project_id)
        return self._client

    def _blob(self, object_name: str) -> _Blob:
        return self._client_or_create().bucket(self._bucket_name).blob(object_name)

    def _replay_or_conflict(
        self, object_name: str, provenance: IngestionProvenance
    ) -> None:
        blob = self._blob(object_name)
        try:
            blob.reload()
        except Exception as error:
            if _is_not_found(error):
                raise IncompleteObjectError(
                    "precondition failed but object is missing"
                ) from error
            raise
        committed = self._provenance_from_blob(blob)
        digest, buffer = self._read_and_hash(blob)
        buffer.close()
        if digest != committed.sha256:
            raise IncompleteObjectError("committed content checksum is inconsistent")
        if committed == provenance:
            _LOGGER.info(
                "gcs put_if_absent identical replay",
                extra={"object_key": object_name, "operation": "put_if_absent"},
            )
            return
        raise ObjectConflictError(
            "immutable object already exists with different state"
        )

    def _provenance_from_blob(self, blob: _Blob) -> IngestionProvenance:
        metadata = blob.metadata or {}
        raw = metadata.get(_META_PROVENANCE)
        if raw is None or not str(raw).strip():
            raise IncompleteObjectError("committed provenance is missing")
        try:
            text = str(raw)
            if not text.endswith("\n"):
                text = f"{text}\n"
            return IngestionProvenance.model_validate_json(text.encode("utf-8"))
        except (TypeError, ValueError) as error:
            raise IncompleteObjectError(
                "committed provenance cannot be read"
            ) from error

    def _stream_to_file(self, content: BinaryIO, destination: BinaryIO) -> str:
        digest = hashlib.sha256()
        while True:
            chunk = content.read(_CHUNK_SIZE)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("content stream must yield bytes")
            data = bytes(chunk)
            digest.update(data)
            destination.write(data)
        return digest.hexdigest()

    def _read_and_hash(self, blob: _Blob) -> tuple[str, BinaryIO]:
        digest = hashlib.sha256()
        buffer = tempfile.SpooledTemporaryFile(max_size=_CHUNK_SIZE * 16)
        try:
            with blob.open("rb") as handle:
                while True:
                    chunk = handle.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    data = bytes(chunk)
                    digest.update(data)
                    buffer.write(data)
        except Exception as error:
            buffer.close()
            if _is_not_found(error):
                raise ObjectNotFoundError("object not found") from error
            raise
        return digest.hexdigest(), buffer


def _is_precondition_failed(error: BaseException) -> bool:
    name = type(error).__name__
    if name in {"PreconditionFailed", "Conflict"}:
        return True
    code = getattr(error, "code", None)
    if code in {412, 409}:
        return True
    status = getattr(error, "status_code", None)
    return status in {412, 409}


def _is_not_found(error: BaseException) -> bool:
    name = type(error).__name__
    if name in {"NotFound", "ObjectNotFoundError"}:
        return True
    code = getattr(error, "code", None)
    if code == 404:
        return True
    status = getattr(error, "status_code", None)
    return status == 404
