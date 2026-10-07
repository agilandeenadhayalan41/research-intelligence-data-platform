"""LocalObjectStore namespace, staging, and filesystem-specific tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from research_platform.config.models import StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.errors import InvalidObjectKeyError, ObjectNotFoundError
from research_platform.storage.local import LocalObjectStore


def _store(root: Path) -> LocalObjectStore:
    return LocalObjectStore(StorageConfig(backend="local", landing_path=root))


def _provenance(content: bytes) -> IngestionProvenance:
    return IngestionProvenance.model_validate(
        {
            "run_id": uuid4(),
            "source": "openalex",
            "source_uri": "s3://openalex/data/jsonl/works/part.gz",
            "retrieved_at": datetime(2024, 6, 1, tzinfo=UTC),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )


@pytest.mark.parametrize(
    "key",
    [
        "/abs/source.bin",
        "C:/windows/source.bin",
        "raw/../outside/source.bin",
        "raw/./source.bin",
        "raw//source.bin",
        "",
        " ",
        "source.bin",
        ".staging/x/source.bin",
        "raw/.hidden/source.bin",
        "raw/source.bin/",
        "raw\\source.bin",
        "raw/has space/source.bin",
    ],
)
def test_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        store.put_if_absent(key, BytesIO(b"x"), _provenance(b"x"))


def test_accepts_nested_safe_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    body = b"nested"
    key = (
        "openalex/works/snapshot_date=2024-01-01/updated_date=none/"
        "oa-" + ("a" * 64) + "/source.gz"
    )
    store.put_if_absent(key, BytesIO(body), _provenance(body))
    with store.open(key) as handle:
        assert handle.read() == body


def test_absolute_path_rejection_on_open(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        store.open("/etc/passwd")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path.parent / f"outside-{uuid4().hex}"
    outside.mkdir()
    try:
        landing = tmp_path / "landing"
        landing.mkdir()
        store = _store(landing)
        link = landing / "escape"
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(InvalidObjectKeyError):
            store.put_if_absent(
                "escape/nested/source.bin", BytesIO(b"x"), _provenance(b"x")
            )
        assert list(outside.iterdir()) == []
    finally:
        if outside.exists():
            outside.rmdir()


def test_interrupted_temporary_write_leaves_no_committed_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    body = b"partial"
    key = "raw/item/source.bin"

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated interrupt")

    monkeypatch.setattr(store, "_write_bytes", boom)
    with pytest.raises(RuntimeError, match="simulated interrupt"):
        store.put_if_absent(key, BytesIO(body), _provenance(body))
    assert not (tmp_path / "raw").exists()
    staging = tmp_path / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_failure_cleanup_does_not_remove_other_committed_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    kept = b"keep-me"
    store.put_if_absent("raw/keep/source.bin", BytesIO(kept), _provenance(kept))

    def boom(*args: object, **kwargs: object) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_stream_to_path", boom)
    with pytest.raises(OSError, match="disk full"):
        store.put_if_absent("raw/other/source.bin", BytesIO(b"new"), _provenance(b"new"))
    with store.open("raw/keep/source.bin") as handle:
        assert handle.read() == kept
    staging = tmp_path / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_jsonl_gz_and_parquet_extensions_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    gz = b"\x1f\x8b\x08\x00synthetic"
    parquet = b"PAR1" + b"\0" * 8 + b"PAR1"
    store.put_if_absent("raw/jsonl/source.gz", BytesIO(gz), _provenance(gz))
    store.put_if_absent("raw/parquet/source.parquet", BytesIO(parquet), _provenance(parquet))
    assert (tmp_path / "raw/jsonl/source.gz").read_bytes() == gz
    assert (tmp_path / "raw/parquet/source.parquet").read_bytes() == parquet
    assert (tmp_path / "raw/jsonl/source.gz").suffix == ".gz"
    assert (tmp_path / "raw/parquet/source.parquet").suffix == ".parquet"


def test_requires_local_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local"):
        LocalObjectStore(StorageConfig(backend="gcs", landing_path=tmp_path, bucket="b"))


def test_missing_after_empty_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        store.open("raw/item/source.bin")


def test_reserved_provenance_basename_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        store.put_if_absent(
            "raw/item/provenance.json", BytesIO(b"{}"), _provenance(b"{}")
        )
