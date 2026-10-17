"""Deterministic in-memory GCS client for ObjectStore contract tests.

No network, credentials, project, or bucket provisioning. Exercises generation
preconditions, metadata, upload/download, and mapped provider failures.
"""

from __future__ import annotations

import base64
import hashlib
import io
import threading
from dataclasses import dataclass, field
from typing import BinaryIO

import google_crc32c


class FakePreconditionFailed(Exception):
    """Mirrors google.api_core.exceptions.PreconditionFailed for offline tests."""

    code = 412


class FakeNotFound(Exception):
    code = 404


class FakeForbidden(PermissionError):
    """Mirrors a real 403: the text names the request URL (with the bucket) and the caller."""

    code = 403


class FakeTransportError(ConnectionError):
    """Generic transport interruption (an ``OSError``, like ``requests`` errors)."""


FAKE_CALLER = "synthetic-caller@example.invalid"


def _forbidden(bucket: str, blob_name: str, permission: str) -> FakeForbidden:
    method, path = (
        ("POST", f"/upload/storage/v1/b/{bucket}/o?uploadType=resumable")
        if permission == "storage.objects.create"
        else ("GET", f"/storage/v1/b/{bucket}/o/{blob_name}")
    )
    return FakeForbidden(
        f"403 {method} https://storage.googleapis.com{path}: {FAKE_CALLER} does not have "
        f"{permission} access to the Google Cloud Storage object. Permission "
        f"'{permission}' denied on resource (or it may not exist)."
    )


@dataclass
class _StoredObject:
    data: bytes
    metadata: dict[str, str]
    generation: int


@dataclass
class FakeBlob:
    name: str
    bucket: FakeBucket
    metadata: dict[str, str] | None = None
    generation: int | None = None
    # Server-reported properties, populated like GCS does after upload/reload.
    size: int | None = None
    crc32c: str | None = None
    md5_hash: str | None = None
    # Test instrumentation: last upload kwargs observed on this blob handle.
    last_upload_kwargs: dict[str, object] = field(default_factory=dict)

    def upload_from_file(
        self,
        file_obj: BinaryIO,
        *,
        rewind: bool = False,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None:
        self.last_upload_kwargs = {
            "rewind": rewind,
            "content_type": content_type,
            "if_generation_match": if_generation_match,
        }
        self.bucket.record_upload(
            self.name,
            if_generation_match=if_generation_match,
            content_type=content_type,
        )
        if self.bucket.fail_permission:
            raise _forbidden(self.bucket.name, self.name, "storage.objects.create")
        if self.bucket.fail_transport_on_upload:
            raise FakeTransportError("transport interrupted")
        if rewind and hasattr(file_obj, "seek"):
            file_obj.seek(0)
        interrupt_after = self.bucket.interrupt_upload_after_bytes
        if interrupt_after is not None:
            # One-shot: a resumable session that never finalizes stores nothing.
            self.bucket.interrupt_upload_after_bytes = None
            file_obj.read(interrupt_after)
            raise FakeTransportError("upload interrupted before finalization")
        data = file_obj.read()
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("upload must read bytes")
        meta = dict(self.metadata or {})
        with self.bucket.lock:
            existing = self.bucket.objects.get(self.name)
            if if_generation_match == 0 and existing is not None:
                raise FakePreconditionFailed(
                    f"condition if_generation_match=0 failed for {self.name}"
                )
            if (
                if_generation_match is not None
                and if_generation_match != 0
                and (existing is None or existing.generation != if_generation_match)
            ):
                raise FakePreconditionFailed("generation precondition failed")
            generation = 1 if existing is None else existing.generation + 1
            stored = _StoredObject(
                data=bytes(data), metadata=meta, generation=generation
            )
            self.bucket.objects[self.name] = stored
            self._load(stored)
        if self.bucket.raise_after_upload_commit:
            # One-shot: committed server-side, then a non-retryable transport error.
            self.bucket.raise_after_upload_commit = False
            raise FakeTransportError("transport error after the upload committed")

    def _load(self, stored: _StoredObject) -> None:
        self.metadata = dict(stored.metadata)
        self.generation = stored.generation
        self.size = len(stored.data)
        self.md5_hash = base64.b64encode(
            hashlib.md5(stored.data, usedforsecurity=False).digest()
        ).decode()
        self.crc32c = base64.b64encode(
            google_crc32c.value(stored.data).to_bytes(4, "big")
        ).decode()

    def reload(self) -> None:
        if self.bucket.fail_permission:
            raise _forbidden(self.bucket.name, self.name, "storage.objects.get")
        with self.bucket.lock:
            stored = self.bucket.objects.get(self.name)
            if stored is None:
                raise FakeNotFound(f"blob {self.name} not found")
            self._load(stored)

    def open(self, mode: str = "rb") -> BinaryIO:
        if mode not in {"rb", "r"}:
            raise ValueError("FakeBlob.open supports read modes only")
        if self.bucket.fail_permission:
            raise _forbidden(self.bucket.name, self.name, "storage.objects.get")
        if self.bucket.fail_transport_on_open:
            raise FakeTransportError("transport interrupted")
        with self.bucket.lock:
            stored = self.bucket.objects.get(self.name)
            if stored is None:
                raise FakeNotFound(f"blob {self.name} not found")
            data = stored.data
            self._load(stored)
        return io.BytesIO(data)


@dataclass
class FakeBucket:
    name: str
    objects: dict[str, _StoredObject] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    upload_calls: list[dict[str, object]] = field(default_factory=list)
    fail_permission: bool = False
    fail_transport_on_upload: bool = False
    fail_transport_on_open: bool = False
    # One-shot knobs: stop an upload before it finalizes (nothing stored), or
    # raise a transport error after the object committed.
    interrupt_upload_after_bytes: int | None = None
    raise_after_upload_commit: bool = False

    def blob(self, blob_name: str) -> FakeBlob:
        return FakeBlob(name=blob_name, bucket=self)

    def record_upload(
        self,
        blob_name: str,
        *,
        if_generation_match: int | None,
        content_type: str | None,
    ) -> None:
        self.upload_calls.append(
            {
                "blob_name": blob_name,
                "if_generation_match": if_generation_match,
                "content_type": content_type,
            }
        )

    def seed(
        self,
        name: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
        *,
        generation: int = 1,
    ) -> None:
        with self.lock:
            self.objects[name] = _StoredObject(
                data=data,
                metadata=dict(metadata or {}),
                generation=generation,
            )

    def delete(self, name: str) -> None:
        with self.lock:
            self.objects.pop(name, None)


@dataclass
class FakeGCSClient:
    """Minimal client/bucket/blob boundary for GCSObjectStore injection."""

    buckets: dict[str, FakeBucket] = field(default_factory=dict)

    def bucket(self, bucket_name: str) -> FakeBucket:
        if bucket_name not in self.buckets:
            self.buckets[bucket_name] = FakeBucket(name=bucket_name)
        return self.buckets[bucket_name]
