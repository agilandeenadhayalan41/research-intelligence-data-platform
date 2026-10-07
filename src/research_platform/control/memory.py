"""In-memory ControlStore for local/offline transactional ingestion tests.

This is an explicit local write-path implementation behind the Step 10
``ControlStore`` boundary. It does not use ``Warehouse.query``.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Literal
from uuid import UUID

from research_platform.control.errors import (
    ClaimConflictError,
    ControlNotFoundError,
    IdempotencyConflictError,
)
from research_platform.control.lifecycle import (
    apply_pipeline_run_finish,
    apply_pipeline_run_retry,
    apply_pipeline_run_start,
    apply_source_file_claim,
    apply_source_file_failure,
    apply_source_file_success,
    apply_stale_claim_recovery,
)
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)
from research_platform.control.reconciliation import (
    RegistrationOutcome,
    reconcile_registration,
)
from research_platform.control.store import ControlStore
from research_platform.provenance.models import IngestionProvenance


class InMemoryControlStore(ControlStore):
    """Thread-safe in-memory control plane with atomic claim semantics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[UUID, PipelineRun] = {}
        self._files: dict[str, SourceFileControl] = {}
        self._provenance: list[RecordProvenance] = []

    def get_pipeline_run(self, run_id: UUID) -> PipelineRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_source_file(self, asset_id: str) -> SourceFileControl | None:
        with self._lock:
            return self._files.get(asset_id)

    def list_record_provenance(self, *, asset_id: str | None = None) -> tuple[RecordProvenance, ...]:
        with self._lock:
            rows = self._provenance
            if asset_id is not None:
                rows = [row for row in rows if row.asset_id == asset_id]
            return tuple(rows)

    def create_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        with self._lock:
            if run.run_id in self._runs:
                raise IdempotencyConflictError("pipeline run already exists")
            if run.status not in {PipelineRunStatus.PENDING, PipelineRunStatus.PROCESSING}:
                raise IdempotencyConflictError(
                    "create_pipeline_run accepts PENDING or PROCESSING only"
                )
            stored = PipelineRun.model_validate(run.model_dump(mode="python"))
            self._runs[run.run_id] = stored
            return stored

    def finish_pipeline_run(
        self,
        run_id: UUID,
        *,
        status: Literal[PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED],
        completed_at: datetime,
        failure_category: FailureCategory | None = None,
        failure_message: str | None = None,
    ) -> PipelineRun:
        with self._lock:
            existing = self._require_run(run_id)
            finished = apply_pipeline_run_finish(
                existing,
                status=status,
                completed_at=completed_at,
                updated_at=completed_at,
                failure_category=failure_category,
                failure_message=failure_message,
            )
            self._runs[run_id] = finished
            return finished

    def retry_pipeline_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
    ) -> PipelineRun:
        with self._lock:
            existing = self._require_run(run_id)
            retried = apply_pipeline_run_retry(
                existing, started_at=started_at, updated_at=started_at
            )
            self._runs[run_id] = retried
            return retried

    def start_pipeline_run(self, run_id: UUID, *, started_at: datetime) -> PipelineRun:
        """PENDING -> PROCESSING helper used by local ingestion."""
        with self._lock:
            existing = self._require_run(run_id)
            started = apply_pipeline_run_start(
                existing, started_at=started_at, updated_at=started_at
            )
            self._runs[run_id] = started
            return started

    def register_source_file(
        self, control: SourceFileControl
    ) -> tuple[SourceFileControl, RegistrationOutcome]:
        with self._lock:
            existing = self._files.get(control.asset_id)
            outcome = reconcile_registration(existing, control, raise_on_conflict=False)
            if outcome is RegistrationOutcome.CREATED:
                stored = SourceFileControl.model_validate(control.model_dump(mode="python"))
                self._files[control.asset_id] = stored
                return stored, outcome
            assert existing is not None
            return existing, outcome

    def claim_source_file(
        self,
        asset_id: str,
        *,
        run_id: UUID,
        claimed_by: str,
        claim_token: UUID,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> SourceFileControl:
        with self._lock:
            existing = self._require_file(asset_id)
            if existing.status is ControlStatus.DISCOVERED:
                event: Literal["claim", "retry"] = "claim"
            elif existing.status is ControlStatus.FAILED:
                event = "retry"
            else:
                raise ClaimConflictError(
                    f"cannot claim source file in status {existing.status.value}"
                )
            claimed = apply_source_file_claim(
                existing,
                run_id=run_id,
                claimed_by=claimed_by,
                claim_token=claim_token,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
                updated_at=claimed_at,
                event=event,
            )
            self._files[asset_id] = claimed
            return claimed

    def mark_source_file_success(
        self,
        asset_id: str,
        *,
        claim_token: UUID,
        processed_at: datetime,
        raw_object_key: str,
        source_checksum_sha256: str,
        retrieval_provenance: IngestionProvenance,
    ) -> SourceFileControl:
        del retrieval_provenance  # control completion only; bytes live in ObjectStore
        with self._lock:
            existing = self._require_file(asset_id)
            succeeded = apply_source_file_success(
                existing,
                claim_token=claim_token,
                processed_at=processed_at,
                raw_object_key=raw_object_key,
                source_checksum_sha256=source_checksum_sha256,
                updated_at=processed_at,
                now=processed_at,
            )
            self._files[asset_id] = succeeded
            return succeeded

    def mark_source_file_failed(
        self,
        asset_id: str,
        *,
        claim_token: UUID,
        processed_at: datetime,
        failure_category: FailureCategory,
        failure_message: str,
    ) -> SourceFileControl:
        with self._lock:
            existing = self._require_file(asset_id)
            failed = apply_source_file_failure(
                existing,
                claim_token=claim_token,
                processed_at=processed_at,
                failure_category=failure_category,
                failure_message=failure_message,
                updated_at=processed_at,
                now=processed_at,
            )
            self._files[asset_id] = failed
            return failed

    def recover_stale_claim(
        self, asset_id: str, *, now: datetime
    ) -> SourceFileControl:
        with self._lock:
            existing = self._require_file(asset_id)
            recovered = apply_stale_claim_recovery(
                existing, now=now, updated_at=now
            )
            self._files[asset_id] = recovered
            return recovered

    def record_provenance(self, record: RecordProvenance) -> RecordProvenance:
        with self._lock:
            stored = RecordProvenance.model_validate(record.model_dump(mode="python"))
            # Idempotent on (record_id, asset_id, checksum, run_id)
            for index, existing in enumerate(self._provenance):
                if (
                    existing.record_id == stored.record_id
                    and existing.asset_id == stored.asset_id
                    and existing.source_checksum_sha256 == stored.source_checksum_sha256
                    and existing.run_id == stored.run_id
                ):
                    self._provenance[index] = stored
                    return stored
            self._provenance.append(stored)
            return stored

    def _require_run(self, run_id: UUID) -> PipelineRun:
        run = self._runs.get(run_id)
        if run is None:
            raise ControlNotFoundError(f"pipeline run not found: {run_id}")
        return run

    def _require_file(self, asset_id: str) -> SourceFileControl:
        control = self._files.get(asset_id)
        if control is None:
            raise ControlNotFoundError(f"source file not found: {asset_id}")
        return control
