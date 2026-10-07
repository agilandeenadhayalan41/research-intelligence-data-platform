"""Offline tests for Step 10 pipeline-control contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_platform.control import (
    ChecksumConflictError,
    ClaimConflictError,
    ControlStatus,
    ControlStore,
    FailureCategory,
    IllegalTransitionError,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    RegistrationOutcome,
    SourceFileControl,
    StaleClaimError,
    UnimplementedControlStore,
    apply_pipeline_run_finish,
    apply_pipeline_run_start,
    apply_source_file_claim,
    apply_source_file_failure,
    apply_source_file_success,
    apply_stale_claim_recovery,
    assert_source_file_transition,
    dumps_control_model,
    is_lease_expired,
    next_attempt_count,
    reconcile_registration,
)


def _ts(hour: int = 1) -> datetime:
    return datetime(2024, 6, 1, hour, 0, 0, tzinfo=UTC)


def _run(**overrides: object) -> PipelineRun:
    payload: dict[str, object] = {
        "run_id": uuid4(),
        "source": "openalex",
        "pipeline_name": "openalex-works-ingest",
        "status": PipelineRunStatus.PENDING,
        "attempt": 1,
        "created_at": _ts(0),
        "updated_at": _ts(0),
    }
    payload.update(overrides)
    return PipelineRun.model_validate(payload)


def _file(**overrides: object) -> SourceFileControl:
    payload: dict[str, object] = {
        "asset_id": "oa-" + ("a" * 64),
        "run_id": uuid4(),
        "source": "openalex",
        "entity": "works",
        "source_uri": "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part.gz",
        "snapshot_date": date(2024, 1, 15),
        "updated_date": date(2024, 1, 10),
        "content_format": "jsonl",
        "declared_size_bytes": 100,
        "status": ControlStatus.DISCOVERED,
        "attempt_count": 0,
        "created_at": _ts(0),
        "updated_at": _ts(0),
    }
    payload.update(overrides)
    return SourceFileControl.model_validate(payload)


def _processing(**overrides: object) -> SourceFileControl:
    token = uuid4()
    payload: dict[str, object] = {
        "status": ControlStatus.PROCESSING,
        "attempt_count": 1,
        "claimed_by": "worker-1",
        "claim_token": token,
        "claimed_at": _ts(1),
        "lease_expires_at": _ts(3),
    }
    payload.update(overrides)
    return _file(**payload)


def test_pipeline_run_valid_pending() -> None:
    run = _run()
    assert run.status is PipelineRunStatus.PENDING
    assert run.started_at is None


def test_pipeline_run_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        _run(created_at=datetime(2024, 6, 1, 0, 0, 0), updated_at=_ts(0))


def test_pipeline_run_success_requires_completed_at() -> None:
    with pytest.raises(ValidationError, match="SUCCESS"):
        _run(
            status=PipelineRunStatus.SUCCESS,
            started_at=_ts(1),
            completed_at=None,
            updated_at=_ts(2),
        )


def test_pipeline_run_processing_rejects_completed_at() -> None:
    with pytest.raises(ValidationError, match="PROCESSING"):
        _run(
            status=PipelineRunStatus.PROCESSING,
            started_at=_ts(1),
            completed_at=_ts(2),
            updated_at=_ts(2),
        )


def test_pipeline_run_rejects_negative_attempt() -> None:
    with pytest.raises(ValidationError):
        _run(attempt=0)


def test_pipeline_run_rejects_completed_before_started() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        _run(
            status=PipelineRunStatus.FAILED,
            started_at=_ts(2),
            completed_at=_ts(1),
            updated_at=_ts(2),
            failure_category=FailureCategory.UNKNOWN,
            failure_message="boom",
        )


def test_pipeline_run_start_and_finish_lifecycle() -> None:
    pending = _run()
    processing = apply_pipeline_run_start(pending, started_at=_ts(1), updated_at=_ts(1))
    assert processing.status is PipelineRunStatus.PROCESSING
    success = apply_pipeline_run_finish(
        processing,
        status=PipelineRunStatus.SUCCESS,
        completed_at=_ts(2),
        updated_at=_ts(2),
    )
    assert success.status is PipelineRunStatus.SUCCESS
    with pytest.raises(IllegalTransitionError):
        apply_pipeline_run_finish(
            success,
            status=PipelineRunStatus.FAILED,
            completed_at=_ts(3),
            updated_at=_ts(3),
            failure_category=FailureCategory.UNKNOWN,
            failure_message="nope",
        )


def test_source_file_discovered_valid() -> None:
    control = _file()
    assert control.status is ControlStatus.DISCOVERED
    assert control.attempt_count == 0


def test_source_file_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        _file(created_at=datetime(2024, 6, 1), updated_at=_ts(0))


def test_illegal_discovered_to_success() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_source_file_transition(
            ControlStatus.DISCOVERED, ControlStatus.SUCCESS, event="success"
        )


def test_illegal_success_to_processing() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_source_file_transition(
            ControlStatus.SUCCESS, ControlStatus.PROCESSING, event="retry"
        )


def test_discovered_to_processing_claim() -> None:
    discovered = _file()
    token = uuid4()
    processing = apply_source_file_claim(
        discovered,
        run_id=discovered.run_id,
        claimed_by="worker-a",
        claim_token=token,
        claimed_at=_ts(1),
        lease_expires_at=_ts(2),
        updated_at=_ts(1),
        event="claim",
    )
    assert processing.status is ControlStatus.PROCESSING
    assert processing.attempt_count == 1
    assert processing.claim_token == token


def test_processing_to_success() -> None:
    processing = _processing()
    token = processing.claim_token
    assert token is not None
    success = apply_source_file_success(
        processing,
        claim_token=token,
        processed_at=_ts(2),
        raw_object_key="openalex/works/snapshot_date=2024-01-15/updated_date=2024-01-10/"
        f"{processing.asset_id}/source.gz",
        source_checksum_sha256="ab" * 32,
        updated_at=_ts(2),
        now=_ts(2),
    )
    assert success.status is ControlStatus.SUCCESS
    assert success.claim_token is None
    assert success.attempt_count == 1


def test_processing_to_failed() -> None:
    processing = _processing()
    token = processing.claim_token
    assert token is not None
    failed = apply_source_file_failure(
        processing,
        claim_token=token,
        processed_at=_ts(2),
        failure_category=FailureCategory.RETRIEVAL,
        failure_message="upstream timeout",
        updated_at=_ts(2),
        now=_ts(2),
    )
    assert failed.status is ControlStatus.FAILED
    assert failed.failure_category is FailureCategory.RETRIEVAL


def test_failed_to_processing_retry_increments_attempt() -> None:
    processing = _processing(attempt_count=2)
    token = processing.claim_token
    assert token is not None
    failed = apply_source_file_failure(
        processing,
        claim_token=token,
        processed_at=_ts(2),
        failure_category=FailureCategory.LANDING,
        failure_message="disk full",
        updated_at=_ts(2),
        now=_ts(2),
    )
    assert next_attempt_count(failed, event="retry") == 3
    retried = apply_source_file_claim(
        failed,
        run_id=uuid4(),
        claimed_by="worker-b",
        claim_token=uuid4(),
        claimed_at=_ts(3),
        lease_expires_at=_ts(4),
        updated_at=_ts(3),
        event="retry",
    )
    assert retried.status is ControlStatus.PROCESSING
    assert retried.attempt_count == 3


def test_success_is_terminal() -> None:
    processing = _processing()
    token = processing.claim_token
    assert token is not None
    success = apply_source_file_success(
        processing,
        claim_token=token,
        processed_at=_ts(2),
        raw_object_key="openalex/works/x/source.gz",
        source_checksum_sha256="cd" * 32,
        updated_at=_ts(2),
        now=_ts(2),
    )
    with pytest.raises(IllegalTransitionError):
        apply_source_file_claim(
            success,
            run_id=success.run_id,
            claimed_by="worker",
            claim_token=uuid4(),
            claimed_at=_ts(3),
            lease_expires_at=_ts(4),
            updated_at=_ts(3),
            event="retry",
        )


def test_lease_expiry_validation() -> None:
    processing = _processing(lease_expires_at=_ts(2))
    assert is_lease_expired(processing, now=_ts(2)) is True
    assert is_lease_expired(processing, now=_ts(1)) is False


def test_claim_token_required_for_success() -> None:
    processing = _processing()
    with pytest.raises(ClaimConflictError):
        apply_source_file_success(
            processing,
            claim_token=uuid4(),
            processed_at=_ts(2),
            raw_object_key="openalex/works/x/source.gz",
            source_checksum_sha256="ef" * 32,
            updated_at=_ts(2),
            now=_ts(2),
        )


def test_stale_claim_blocks_success_and_recovers_to_failed() -> None:
    processing = _processing(lease_expires_at=_ts(2))
    token = processing.claim_token
    assert token is not None
    with pytest.raises(StaleClaimError):
        apply_source_file_success(
            processing,
            claim_token=token,
            processed_at=_ts(3),
            raw_object_key="openalex/works/x/source.gz",
            source_checksum_sha256="11" * 32,
            updated_at=_ts(3),
            now=_ts(3),
        )
    recovered = apply_stale_claim_recovery(
        processing, now=_ts(3), updated_at=_ts(3)
    )
    assert recovered.status is ControlStatus.FAILED
    assert recovered.failure_category is FailureCategory.STALE_CLAIM
    assert recovered.claim_token is None


def test_cannot_recover_valid_claim() -> None:
    processing = _processing(lease_expires_at=_ts(5))
    with pytest.raises(ClaimConflictError, match="still valid"):
        apply_stale_claim_recovery(processing, now=_ts(2), updated_at=_ts(2))


def test_identical_asset_replay() -> None:
    first = _file()
    second = first.model_copy(update={"run_id": uuid4(), "updated_at": _ts(1)})
    assert (
        reconcile_registration(first, second)
        is RegistrationOutcome.IDEMPOTENT_REPLAY
    )


def test_changed_checksum_conflict() -> None:
    existing = _file(source_checksum_sha256="aa" * 32)
    incoming = existing.model_copy(
        update={"source_checksum_sha256": "bb" * 32, "run_id": uuid4()}
    )
    assert (
        reconcile_registration(existing, incoming)
        is RegistrationOutcome.CHECKSUM_CONFLICT
    )
    with pytest.raises(ChecksumConflictError):
        reconcile_registration(existing, incoming, raise_on_conflict=True)


def test_duplicate_registration_already_success() -> None:
    success = _file(
        status=ControlStatus.SUCCESS,
        attempt_count=1,
        processed_at=_ts(2),
        source_checksum_sha256="aa" * 32,
        raw_object_key="openalex/works/x/source.gz",
        updated_at=_ts(2),
    )
    incoming = _file(
        asset_id=success.asset_id,
        source_uri=success.source_uri,
        source_checksum_sha256="aa" * 32,
    )
    assert (
        reconcile_registration(success, incoming)
        is RegistrationOutcome.ALREADY_SUCCESS
    )


def test_retryable_failure_registration() -> None:
    failed = _file(
        status=ControlStatus.FAILED,
        attempt_count=1,
        processed_at=_ts(2),
        failure_category=FailureCategory.RETRIEVAL,
        failure_message="timeout",
        updated_at=_ts(2),
    )
    incoming = _file(asset_id=failed.asset_id, source_uri=failed.source_uri)
    assert (
        reconcile_registration(failed, incoming)
        is RegistrationOutcome.RETRYABLE_FAILURE
    )


def test_record_provenance_validation() -> None:
    record = RecordProvenance.model_validate(
        {
            "record_id": "W123",
            "entity_type": "works",
            "asset_id": "oa-" + ("a" * 64),
            "source_checksum_sha256": "ab" * 32,
            "run_id": uuid4(),
            "source_uri": "s3://openalex/data/jsonl/works/part.gz",
            "processed_at": _ts(2),
            "source_updated_date": date(2024, 1, 10),
        }
    )
    assert record.entity_type == "works"
    with pytest.raises(ValidationError):
        RecordProvenance.model_validate(
            {
                "record_id": "W123",
                "entity_type": "works",
                "asset_id": "oa-x",
                "source_checksum_sha256": "not-a-hash",
                "run_id": uuid4(),
                "source_uri": "s3://openalex/data/jsonl/works/part.gz",
                "processed_at": _ts(2),
            }
        )


def test_deterministic_serialization() -> None:
    control = _file(run_id=uuid4())
    assert dumps_control_model(control) == dumps_control_model(control)
    assert dumps_control_model(control).endswith(b"\n")


def test_control_store_skeleton_is_offline() -> None:
    store: ControlStore = UnimplementedControlStore()
    with pytest.raises(NotImplementedError, match="later phase"):
        store.create_pipeline_run(_run())
    with pytest.raises(NotImplementedError, match="later phase"):
        store.claim_source_file(
            "oa-" + ("a" * 64),
            run_id=uuid4(),
            claimed_by="worker",
            claim_token=uuid4(),
            claimed_at=_ts(1),
            lease_expires_at=_ts(2),
        )


def test_ddl_schema_contract_present(repo_root: Path) -> None:
    ddl = (repo_root / "sql/control/001_pipeline_control.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS pipeline_runs" in ddl
    assert "CREATE TABLE IF NOT EXISTS source_files" in ddl
    assert "CREATE TABLE IF NOT EXISTS record_provenance" in ddl
    assert "DISCOVERED" in ddl and "PROCESSING" in ddl
    assert "SUCCESS" in ddl and "FAILED" in ddl
    assert "claim_token" in ddl and "lease_expires_at" in ddl
    assert "source_checksum_sha256" in ddl
    assert "PRIMARY KEY" in ddl
    assert "REFERENCES pipeline_runs" in ddl
    assert "PostgreSQL" in ddl
    assert "Warehouse.query()" in ddl


def test_failure_message_rejects_sensitive_markers() -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        _file(
            status=ControlStatus.FAILED,
            attempt_count=1,
            processed_at=_ts(2),
            failure_category=FailureCategory.UNKNOWN,
            failure_message="password=super-secret",
            updated_at=_ts(2),
        )


def test_lease_expires_must_follow_claimed_at() -> None:
    with pytest.raises(ValidationError, match="lease_expires_at"):
        _processing(claimed_at=_ts(2), lease_expires_at=_ts(2))


def test_created_registration_outcome() -> None:
    assert reconcile_registration(None, _file()) is RegistrationOutcome.CREATED
