"""Offline tests for SUCCESS replay identity / declared metadata rules."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from research_platform.control.errors import ChecksumConflictError, IdempotencyConflictError
from research_platform.control.models import ControlStatus, SourceFileControl
from research_platform.control.reconciliation import (
    RegistrationOutcome,
    reconcile_registration,
)


def _ts(hour: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, tzinfo=UTC)


def _success(
    *,
    declared_size_bytes: int | None = 1000,
    source_checksum_sha256: str | None = "aa" * 32,
    content_format: str = "jsonl",
    updated_date: date | None = date(2024, 1, 10),
    source_uri: str = "s3://openalex/data/jsonl/works/updated_date-2024-01-10/part.gz",
) -> SourceFileControl:
    return SourceFileControl.model_validate(
        {
            "asset_id": "openalex:works:2024-01-15:updated_date-2024-01-10:part.gz",
            "run_id": uuid4(),
            "source": "openalex",
            "entity": "works",
            "source_uri": source_uri,
            "snapshot_date": date(2024, 1, 15),
            "updated_date": updated_date,
            "content_format": content_format,
            "declared_size_bytes": declared_size_bytes,
            "source_checksum_sha256": source_checksum_sha256,
            "raw_object_key": "raw/openalex/works/x.gz",
            "status": ControlStatus.SUCCESS,
            "attempt_count": 1,
            "processed_at": _ts(2),
            "created_at": _ts(0),
            "updated_at": _ts(2),
        }
    )


def _incoming(
    existing: SourceFileControl,
    **updates: object,
) -> SourceFileControl:
    return SourceFileControl.model_validate(
        {
            "asset_id": existing.asset_id,
            "run_id": uuid4(),
            "source": existing.source,
            "entity": existing.entity,
            "source_uri": existing.source_uri,
            "snapshot_date": existing.snapshot_date,
            "updated_date": existing.updated_date,
            "content_format": existing.content_format,
            "declared_size_bytes": existing.declared_size_bytes,
            "source_checksum_sha256": None,
            "status": ControlStatus.DISCOVERED,
            "attempt_count": 0,
            "created_at": _ts(3),
            "updated_at": _ts(3),
            **updates,
        }
    )


def test_same_identity_identical_metadata_already_success() -> None:
    existing = _success()
    incoming = _incoming(
        existing,
        declared_size_bytes=existing.declared_size_bytes,
        source_checksum_sha256=existing.source_checksum_sha256,
    )
    assert (
        reconcile_registration(existing, incoming) is RegistrationOutcome.ALREADY_SUCCESS
    )


def test_same_identity_changed_declared_size_fails() -> None:
    existing = _success(declared_size_bytes=1000)
    incoming = _incoming(existing, declared_size_bytes=2000)
    with pytest.raises(IdempotencyConflictError, match="declared_size"):
        reconcile_registration(existing, incoming)


def test_same_identity_conflicting_format_fails() -> None:
    existing = _success(content_format="jsonl")
    incoming = _incoming(existing, content_format="parquet")
    with pytest.raises(IdempotencyConflictError, match="identity metadata"):
        reconcile_registration(existing, incoming)


def test_same_identity_conflicting_update_metadata_fails() -> None:
    existing = _success(updated_date=date(2024, 1, 10))
    incoming = _incoming(existing, updated_date=date(2024, 2, 10))
    with pytest.raises(IdempotencyConflictError, match="identity metadata"):
        reconcile_registration(existing, incoming)


def test_same_identity_known_conflicting_checksum() -> None:
    existing = _success(source_checksum_sha256="aa" * 32)
    incoming = _incoming(existing, source_checksum_sha256="bb" * 32)
    assert (
        reconcile_registration(existing, incoming)
        is RegistrationOutcome.CHECKSUM_CONFLICT
    )
    with pytest.raises(ChecksumConflictError):
        reconcile_registration(existing, incoming, raise_on_conflict=True)
