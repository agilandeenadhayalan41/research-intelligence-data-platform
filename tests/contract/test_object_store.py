"""Reusable ObjectStore contract tests (local now; GCS later)."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from research_platform.config.models import StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.local import LocalObjectStore, dumps_provenance

StoreFactory = Callable[[Path], LocalObjectStore]


@pytest.fixture
def store_factory() -> StoreFactory:
    """Local factory today; GCS can supply an equivalent fixture later."""

    def factory(root: Path) -> LocalObjectStore:
        return LocalObjectStore(StorageConfig(backend="local", landing_path=root))

    return factory


def _provenance(content: bytes, **overrides: object) -> IngestionProvenance:
    payload = {
        "run_id": uuid4(),
        "source": "openalex",
        "source_uri": "s3://openalex/data/jsonl/works/updated_date=2024-01-01/part.gz",
        "retrieved_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    payload.update(overrides)
    return IngestionProvenance.model_validate(payload)


def test_successful_immutable_write_and_read(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = b"raw-bytes-v1"
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(body), _provenance(body))
    with store.open(key) as handle:
        assert handle.read() == body
        assert handle.writable() is False


def test_sha256_verification_and_mismatch(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = b"abc"
    bad = _provenance(body, sha256="0" * 64)
    with pytest.raises(ChecksumMismatchError):
        store.put_if_absent("raw/item/source.bin", BytesIO(body), bad)
    assert list(tmp_path.glob("raw/**/*")) == []
    assert list((tmp_path / ".staging").glob("*")) == [] if (tmp_path / ".staging").exists() else True


def test_identical_replay_is_noop(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = b"same"
    key = "raw/item/source.bin"
    provenance = _provenance(body)
    store.put_if_absent(key, BytesIO(body), provenance)
    store.put_if_absent(key, BytesIO(body), provenance)
    with store.open(key) as handle:
        assert handle.read() == body


def test_content_conflict(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    key = "raw/item/source.bin"
    first = b"one"
    second = b"two"
    store.put_if_absent(key, BytesIO(first), _provenance(first))
    with pytest.raises(ObjectConflictError):
        store.put_if_absent(key, BytesIO(second), _provenance(second))
    with store.open(key) as handle:
        assert handle.read() == first


def test_provenance_conflict(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    key = "raw/item/source.bin"
    body = b"same-bytes"
    store.put_if_absent(key, BytesIO(body), _provenance(body))
    other = _provenance(body, run_id=uuid4())
    with pytest.raises(ObjectConflictError):
        store.put_if_absent(key, BytesIO(body), other)


def test_missing_object(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        store.open("raw/item/source.bin")


def test_incomplete_committed_state(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    object_dir = tmp_path / "raw" / "item"
    object_dir.mkdir(parents=True)
    (object_dir / "source.bin").write_bytes(b"orphan")
    with pytest.raises(IncompleteObjectError):
        store.open("raw/item/source.bin")
    with pytest.raises(IncompleteObjectError):
        store.put_if_absent("raw/item/source.bin", BytesIO(b"x"), _provenance(b"x"))


def test_caller_owned_read_stream(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = b"owned"
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(body), _provenance(body))
    handle = store.open(key)
    try:
        assert handle.read(2) == b"ow"
        assert handle.read() == b"ned"
    finally:
        handle.close()
    with pytest.raises(ValueError):
        handle.read()


def test_source_bytes_unchanged(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = bytes(range(256)) + b"\n\0\xff"
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(body), _provenance(body))
    with store.open(key) as handle:
        assert handle.read() == body


def test_concurrent_identical_writers(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    body = b"concurrent-same"
    key = "raw/item/source.bin"
    provenance = _provenance(body)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            store.put_if_absent(key, BytesIO(body), provenance)
        except BaseException as error:  # noqa: BLE001 - collect for assertion
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []
    with store.open(key) as handle:
        assert handle.read() == body


def test_concurrent_conflicting_writers(tmp_path: Path, store_factory: StoreFactory) -> None:
    store = store_factory(tmp_path)
    key = "raw/item/source.bin"
    bodies = (b"writer-a", b"writer-b")
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(body: bytes) -> None:
        try:
            barrier.wait(timeout=5)
            store.put_if_absent(key, BytesIO(body), _provenance(body))
            with lock:
                outcomes.append("ok")
        except ObjectConflictError:
            with lock:
                outcomes.append("conflict")

    threads = [threading.Thread(target=worker, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["conflict", "ok"]
    with store.open(key) as handle:
        assert handle.read() in bodies


def test_deterministic_provenance_bytes(tmp_path: Path, store_factory: StoreFactory) -> None:
    body = b"prov"
    provenance = _provenance(body)
    first = dumps_provenance(provenance)
    second = dumps_provenance(provenance)
    assert first == second
    assert first.endswith(b"\n")
    store = store_factory(tmp_path)
    store.put_if_absent("raw/item/source.bin", BytesIO(body), provenance)
    on_disk = (tmp_path / "raw" / "item" / "provenance.json").read_bytes()
    assert on_disk == first
