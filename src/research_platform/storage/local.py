"""Immutable local filesystem ObjectStore with atomic content/provenance publish."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Final

from research_platform.config.models import StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.keys import resolve_under_root, validate_object_key

_PROVENANCE_NAME: Final = "provenance.json"
_CHUNK_SIZE: Final = 64 * 1024
_STAGING_DIRNAME: Final = ".staging"


def dumps_provenance(provenance: IngestionProvenance) -> bytes:
    """Serialize provenance as deterministic UTF-8 JSON with a trailing newline."""
    payload = provenance.model_dump(mode="json")
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{text}\n".encode("utf-8")


def load_provenance(path: Path) -> IngestionProvenance:
    """Load and validate committed provenance JSON."""
    try:
        return IngestionProvenance.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise IncompleteObjectError("committed provenance cannot be read") from error


class LocalObjectStore(ObjectStore):
    """Local landing root that publishes content and provenance atomically.

    Publication strategy (POSIX directory rename):

    1. Stream bytes into ``landing/.staging/<op-id>/<basename>`` while hashing.
    2. Verify SHA-256 against ``IngestionProvenance.sha256``.
    3. Write sibling ``provenance.json`` in the same staging directory.
    4. ``fsync`` files and the staging directory.
    5. ``os.rename`` the staging directory onto the final object directory
       (atomic visibility of content + provenance together).
    6. ``fsync`` the final object's parent directory so the directory entry is
       durable on supported POSIX filesystems.

    On Linux, renaming a directory onto a non-existent destination is atomic and
    fails with ``FileExistsError`` if another writer already published. Content and
    provenance therefore become visible together, or not at all. Identical replay
    after a lost race is a no-op; conflicting state raises ``ObjectConflictError``.

    ``open`` re-validates committed provenance and re-hashes content before
    returning a stream; corrupt committed state raises ``IncompleteObjectError``
    and is never silently returned, repaired, overwritten, or deleted.

    Limitations: atomic directory rename and parent-directory durability assume a
    POSIX-compatible local filesystem with staging and destination on the same
    device. Cross-device landing roots are unsupported.
    """

    def __init__(self, config: StorageConfig) -> None:
        if config.backend != "local":
            raise ValueError("LocalObjectStore requires storage.backend='local'")
        self.config = config
        self.root = Path(config.landing_path)

    def put_if_absent(
        self, key: str, content: BinaryIO, provenance: IngestionProvenance
    ) -> None:
        object_dir, content_path, content_name = self._resolve(key)
        if content_name == _PROVENANCE_NAME:
            raise InvalidObjectKeyError("object key basename is reserved for provenance")

        if self._is_committed(object_dir, content_path):
            self._replay_or_conflict(object_dir, content_path, provenance)
            return

        self.root.mkdir(parents=True, exist_ok=True)
        staging_root = self.root / _STAGING_DIRNAME
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = staging_root / uuid.uuid4().hex
        staging_content = staging_dir / content_name
        staging_provenance = staging_dir / _PROVENANCE_NAME

        try:
            staging_dir.mkdir(parents=False, exist_ok=False)
            digest = self._stream_to_path(content, staging_content)
            if digest != provenance.sha256:
                raise ChecksumMismatchError("streamed bytes do not match provenance sha256")
            self._write_bytes(staging_provenance, dumps_provenance(provenance))
            self._fsync_tree(staging_dir, staging_content, staging_provenance)

            object_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(staging_dir, object_dir)
            except FileExistsError:
                self._cleanup_path(staging_dir)
                self._replay_or_conflict(object_dir, content_path, provenance)
                return
            except OSError as error:
                if getattr(error, "errno", None) == getattr(os, "ENOTEMPTY", 39):
                    self._cleanup_path(staging_dir)
                    self._replay_or_conflict(object_dir, content_path, provenance)
                    return
                raise
            # Rename made the object visible; fsync the parent for durable entry.
            self._fsync_directory(object_dir.parent)
        except Exception:
            self._cleanup_path(staging_dir)
            raise

    def open(self, key: str) -> BinaryIO:
        object_dir, content_path, content_name = self._resolve(key)
        if content_name == _PROVENANCE_NAME:
            raise InvalidObjectKeyError("object key basename is reserved for provenance")
        if not self._is_committed(object_dir, content_path):
            if object_dir.exists() or content_path.exists():
                raise IncompleteObjectError("committed object is incomplete")
            raise ObjectNotFoundError("object not found")
        self._verify_committed_integrity(object_dir, content_path)
        # Caller owns the returned read-only binary stream and must close it.
        return content_path.open("rb")

    def _resolve(self, key: str) -> tuple[Path, Path, str]:
        validate_object_key(key)
        self._reject_symlink_escape(key)
        return resolve_under_root(self.root, key)

    def _reject_symlink_escape(self, key: str) -> None:
        relative = validate_object_key(key)
        root_resolved = self.root.resolve(strict=False)
        cursor = root_resolved
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                target = cursor.resolve(strict=False)
                try:
                    target.relative_to(root_resolved)
                except ValueError as error:
                    raise InvalidObjectKeyError(
                        "object key escapes the landing root via symlink"
                    ) from error

    def _is_committed(self, object_dir: Path, content_path: Path) -> bool:
        provenance_path = object_dir / _PROVENANCE_NAME
        content_ok = content_path.is_file() and not content_path.is_symlink()
        provenance_ok = provenance_path.is_file() and not provenance_path.is_symlink()
        if content_ok and provenance_ok:
            return True
        if content_ok or provenance_ok or object_dir.exists():
            raise IncompleteObjectError("committed object is incomplete")
        return False

    def _verify_committed_integrity(
        self, object_dir: Path, content_path: Path
    ) -> IngestionProvenance:
        """Load provenance and confirm committed bytes match ``sha256``.

        Does not repair, overwrite, or delete corrupt objects.
        """
        committed = load_provenance(object_dir / _PROVENANCE_NAME)
        digest = self._hash_file(content_path)
        if digest != committed.sha256:
            raise IncompleteObjectError("committed content checksum is inconsistent")
        return committed

    def _replay_or_conflict(
        self,
        object_dir: Path,
        content_path: Path,
        provenance: IngestionProvenance,
    ) -> None:
        if not self._is_committed(object_dir, content_path):
            raise IncompleteObjectError("committed object is incomplete")
        committed = self._verify_committed_integrity(object_dir, content_path)
        if committed == provenance and committed.sha256 == provenance.sha256:
            return
        raise ObjectConflictError("immutable object already exists with different state")

    def _stream_to_path(self, content: BinaryIO, destination: Path) -> str:
        digest = hashlib.sha256()
        with destination.open("wb") as handle:
            while True:
                chunk = content.read(_CHUNK_SIZE)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("content stream must yield bytes")
                data = bytes(chunk)
                digest.update(data)
                handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return digest.hexdigest()

    def _write_bytes(self, path: Path, payload: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _fsync_directory(self, directory: Path) -> None:
        """Durably persist a directory entry on supported POSIX filesystems."""
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _fsync_tree(self, directory: Path, *files: Path) -> None:
        for path in files:
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        self._fsync_directory(directory)

    def _cleanup_path(self, path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=False)
            return
        path.unlink(missing_ok=True)
