"""Unit tests for GCSObjectStore (fake client only; no network/credentials)."""

from __future__ import annotations

import hashlib
import logging
import socket
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest

from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.local import dumps_provenance
from tests.support.fake_gcs import (
    FakeForbidden,
    FakeGCSClient,
    FakeTransportError,
)


def _prov(content: bytes, **overrides: object) -> IngestionProvenance:
    payload = {
        "run_id": uuid4(),
        "source": "openalex",
        "source_uri": "s3://openalex/data/jsonl/works/part.gz",
        "retrieved_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    payload.update(overrides)
    return IngestionProvenance.model_validate(payload)


def _store(client: FakeGCSClient | None = None) -> tuple[GCSObjectStore, FakeGCSClient]:
    fake = client or FakeGCSClient()
    store = GCSObjectStore(
        StorageConfig(backend="gcs", bucket="synthetic-bucket"),
        CloudConfig(project_id="synthetic-project"),
        client=fake,
    )
    return store, fake


def test_construction_requires_gcs_backend_and_identifiers() -> None:
    fake = FakeGCSClient()
    with pytest.raises(ValueError, match="backend='gcs'"):
        GCSObjectStore(
            StorageConfig(backend="local", landing_path="data/landing"),
            CloudConfig(project_id="synthetic-project"),
            client=fake,
        )
    with pytest.raises(ValueError, match="project_id"):
        GCSObjectStore(
            StorageConfig(backend="gcs", bucket="synthetic-bucket"),
            CloudConfig(project_id=None),
            client=fake,
        )


def test_construction_is_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("GCSObjectStore construction must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    store = GCSObjectStore(
        StorageConfig(backend="gcs", bucket="synthetic-bucket"),
        CloudConfig(project_id="synthetic-project"),
        client=None,
    )
    assert store.storage.bucket == "synthetic-bucket"


def test_create_only_precondition_is_sent() -> None:
    store, fake = _store()
    body = b"create-only"
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(body), _prov(body))
    bucket = fake.bucket("synthetic-bucket")
    assert bucket.upload_calls
    assert bucket.upload_calls[0]["if_generation_match"] == 0
    assert bucket.upload_calls[0]["blob_name"] == key
    assert bucket.upload_calls[0]["content_type"] == "application/octet-stream"


def test_does_not_use_exists_then_unconditional_upload() -> None:
    """Regression: create must send if_generation_match=0, not exists()+upload."""
    store, fake = _store()
    body = b"guard"
    store.put_if_absent("raw/item/source.bin", BytesIO(body), _prov(body))
    for call in fake.bucket("synthetic-bucket").upload_calls:
        assert call["if_generation_match"] == 0


def test_identical_replay_after_precondition_loss() -> None:
    store, fake = _store()
    body = b"same"
    key = "raw/item/source.bin"
    provenance = _prov(body)
    store.put_if_absent(key, BytesIO(body), provenance)
    # Second call hits if_generation_match=0 failure then classifies replay.
    store.put_if_absent(key, BytesIO(body), provenance)
    assert len(fake.bucket("synthetic-bucket").upload_calls) == 2
    assert all(
        c["if_generation_match"] == 0
        for c in fake.bucket("synthetic-bucket").upload_calls
    )


def test_conflict_after_precondition_loss() -> None:
    store, _fake = _store()
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(b"a"), _prov(b"a"))
    with pytest.raises(ObjectConflictError):
        store.put_if_absent(key, BytesIO(b"b"), _prov(b"b"))


def test_checksum_mismatch_does_not_create_object() -> None:
    store, fake = _store()
    body = b"abc"
    with pytest.raises(ChecksumMismatchError):
        store.put_if_absent(
            "raw/item/source.bin", BytesIO(body), _prov(body, sha256="0" * 64)
        )
    assert fake.bucket("synthetic-bucket").objects == {}
    assert fake.bucket("synthetic-bucket").upload_calls == []


def test_permission_denied_is_not_swallowed() -> None:
    store, fake = _store()
    fake.bucket("synthetic-bucket").fail_permission = True
    with pytest.raises(FakeForbidden):
        store.put_if_absent("raw/item/source.bin", BytesIO(b"x"), _prov(b"x"))


def test_transport_failure_on_upload_is_not_swallowed() -> None:
    store, fake = _store()
    fake.bucket("synthetic-bucket").fail_transport_on_upload = True
    with pytest.raises(FakeTransportError):
        store.put_if_absent("raw/item/source.bin", BytesIO(b"x"), _prov(b"x"))


def test_transport_failure_on_open_is_not_swallowed() -> None:
    store, fake = _store()
    body = b"ok"
    key = "raw/item/source.bin"
    store.put_if_absent(key, BytesIO(body), _prov(body))
    fake.bucket("synthetic-bucket").fail_transport_on_open = True
    with pytest.raises(FakeTransportError):
        store.open(key)


def test_safe_keys_reject_uri_and_traversal() -> None:
    store, _fake = _store()
    body = b"x"
    provenance = _prov(body)
    for key in (
        "gs://bucket/object",
        "https://example.com/o",
        "../escape/source.bin",
        "/abs/source.bin",
        "raw/item/provenance.json",
    ):
        with pytest.raises(InvalidObjectKeyError):
            store.put_if_absent(key, BytesIO(body), provenance)


def test_logs_do_not_include_secrets(caplog: pytest.LogCaptureFixture) -> None:
    store, _fake = _store()
    body = b"logged"
    key = "raw/item/source.bin"
    provenance = _prov(body)
    with caplog.at_level(logging.INFO, logger="research_platform.storage.gcs"):
        store.put_if_absent(key, BytesIO(body), provenance)
        store.put_if_absent(key, BytesIO(body), provenance)
    blob = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "private_key" not in blob
    assert "authorization" not in blob
    assert "bearer" not in blob
    assert "begin rsa" not in blob
    assert body.decode() not in blob


def test_open_missing_maps_to_object_not_found() -> None:
    store, _fake = _store()
    with pytest.raises(ObjectNotFoundError):
        store.open("raw/item/source.bin")


def test_seeded_object_without_provenance_is_incomplete() -> None:
    store, fake = _store()
    fake.bucket("synthetic-bucket").seed("raw/item/source.bin", b"x", metadata={})
    with pytest.raises(IncompleteObjectError):
        store.open("raw/item/source.bin")


def test_provenance_metadata_round_trip() -> None:
    store, fake = _store()
    body = b"meta"
    provenance = _prov(body)
    store.put_if_absent("raw/item/source.bin", BytesIO(body), provenance)
    stored = fake.bucket("synthetic-bucket").objects["raw/item/source.bin"]
    assert stored.metadata["rp_provenance"].encode("utf-8") == dumps_provenance(
        provenance
    )
