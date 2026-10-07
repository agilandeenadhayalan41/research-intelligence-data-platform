"""PostgreSQL ControlStore: durable claims without Warehouse.query."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from research_platform.control.errors import (
    ClaimConflictError,
    ControlNotFoundError,
    IdempotencyConflictError,
)
from research_platform.control.lifecycle import (
    apply_pipeline_run_finish,
    apply_pipeline_run_retry,
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
from research_platform.persistence.postgres.row_codec import (
    pipeline_run_from_row,
    record_provenance_from_row,
    source_file_from_row,
)
from research_platform.provenance.models import IngestionProvenance

_SOURCE_FILE_COLUMNS = (
    "asset_id, run_id, source, entity, source_uri, snapshot_date, updated_date, "
    "content_format, declared_size_bytes, source_checksum_sha256, raw_object_key, "
    "status, attempt_count, claimed_by, claim_token, claimed_at, lease_expires_at, "
    "processed_at, failure_category, failure_message, created_at, updated_at"
)


class PostgresControlStore(ControlStore):
    """Durable control plane backed by PostgreSQL row locks / conditional updates."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._conn = connection

    def get_pipeline_run(self, run_id: UUID) -> PipelineRun | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM pipeline_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        return None if row is None else pipeline_run_from_row(row)

    def get_source_file(self, asset_id: str) -> SourceFileControl | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_SOURCE_FILE_COLUMNS} FROM source_files WHERE asset_id = %s",
                (asset_id,),
            )
            row = cur.fetchone()
        return None if row is None else source_file_from_row(row)

    def list_record_provenance(
        self, *, asset_id: str | None = None
    ) -> tuple[RecordProvenance, ...]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            if asset_id is None:
                cur.execute("SELECT * FROM record_provenance ORDER BY record_id")
            else:
                cur.execute(
                    "SELECT * FROM record_provenance WHERE asset_id = %s ORDER BY record_id",
                    (asset_id,),
                )
            rows = cur.fetchall()
        return tuple(record_provenance_from_row(row) for row in rows)

    def create_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        with self._conn.transaction():
            try:
                with self._conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        INSERT INTO pipeline_runs (
                            run_id, source, pipeline_name, status, attempt,
                            started_at, completed_at, created_at, updated_at,
                            failure_category, failure_message
                        ) VALUES (
                            %(run_id)s, %(source)s, %(pipeline_name)s, %(status)s, %(attempt)s,
                            %(started_at)s, %(completed_at)s, %(created_at)s, %(updated_at)s,
                            %(failure_category)s, %(failure_message)s
                        )
                        RETURNING *
                        """,
                        _pipeline_run_params(run),
                    )
                    row = cur.fetchone()
            except psycopg.errors.UniqueViolation as error:
                raise IdempotencyConflictError("pipeline run already exists") from error
        assert row is not None
        return pipeline_run_from_row(row)

    def finish_pipeline_run(
        self,
        run_id: UUID,
        *,
        status: Literal[PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED],
        completed_at: datetime,
        failure_category: FailureCategory | None = None,
        failure_message: str | None = None,
    ) -> PipelineRun:
        with self._conn.transaction():
            existing = self._lock_run(run_id)
            finished = apply_pipeline_run_finish(
                existing,
                status=status,
                completed_at=completed_at,
                updated_at=completed_at,
                failure_category=failure_category,
                failure_message=failure_message,
            )
            return self._upsert_run(finished)

    def retry_pipeline_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
    ) -> PipelineRun:
        with self._conn.transaction():
            existing = self._lock_run(run_id)
            retried = apply_pipeline_run_retry(
                existing, started_at=started_at, updated_at=started_at
            )
            return self._upsert_run(retried)

    def register_source_file(
        self, control: SourceFileControl
    ) -> tuple[SourceFileControl, RegistrationOutcome]:
        with self._conn.transaction():
            existing = self._lock_file(control.asset_id, missing_ok=True)
            outcome = reconcile_registration(existing, control, raise_on_conflict=False)
            if outcome is RegistrationOutcome.CREATED:
                stored = self._insert_file(control)
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
        """Atomic claim via SELECT FOR UPDATE + lifecycle helper + UPDATE."""
        with self._conn.transaction():
            existing = self._lock_file(asset_id, missing_ok=False)
            assert existing is not None
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
            return self._update_file(claimed)

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
        del retrieval_provenance
        with self._conn.transaction():
            existing = self._lock_file(asset_id, missing_ok=False)
            assert existing is not None
            succeeded = apply_source_file_success(
                existing,
                claim_token=claim_token,
                processed_at=processed_at,
                raw_object_key=raw_object_key,
                source_checksum_sha256=source_checksum_sha256,
                updated_at=processed_at,
                now=processed_at,
            )
            return self._update_file(succeeded)

    def mark_source_file_failed(
        self,
        asset_id: str,
        *,
        claim_token: UUID,
        processed_at: datetime,
        failure_category: FailureCategory,
        failure_message: str,
    ) -> SourceFileControl:
        with self._conn.transaction():
            existing = self._lock_file(asset_id, missing_ok=False)
            assert existing is not None
            failed = apply_source_file_failure(
                existing,
                claim_token=claim_token,
                processed_at=processed_at,
                failure_category=failure_category,
                failure_message=failure_message,
                updated_at=processed_at,
                now=processed_at,
            )
            return self._update_file(failed)

    def recover_stale_claim(
        self, asset_id: str, *, now: datetime
    ) -> SourceFileControl:
        with self._conn.transaction():
            existing = self._lock_file(asset_id, missing_ok=False)
            assert existing is not None
            recovered = apply_stale_claim_recovery(
                existing, now=now, updated_at=now
            )
            return self._update_file(recovered)

    def record_provenance(self, record: RecordProvenance) -> RecordProvenance:
        with self._conn.transaction():
            return self._upsert_provenance(record)

    def _upsert_provenance(self, record: RecordProvenance) -> RecordProvenance:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO record_provenance (
                    record_id, entity_type, asset_id, source_checksum_sha256,
                    run_id, source_uri, processed_at, source_updated_date
                ) VALUES (
                    %(record_id)s, %(entity_type)s, %(asset_id)s, %(source_checksum_sha256)s,
                    %(run_id)s, %(source_uri)s, %(processed_at)s, %(source_updated_date)s
                )
                ON CONFLICT (entity_type, record_id, asset_id, source_checksum_sha256)
                DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    source_uri = EXCLUDED.source_uri,
                    processed_at = EXCLUDED.processed_at,
                    source_updated_date = EXCLUDED.source_updated_date
                RETURNING *
                """,
                {
                    "record_id": record.record_id,
                    "entity_type": record.entity_type,
                    "asset_id": record.asset_id,
                    "source_checksum_sha256": record.source_checksum_sha256,
                    "run_id": record.run_id,
                    "source_uri": record.source_uri,
                    "processed_at": record.processed_at,
                    "source_updated_date": record.source_updated_date,
                },
            )
            row = cur.fetchone()
        assert row is not None
        return record_provenance_from_row(row)

    def _lock_run(self, run_id: UUID) -> PipelineRun:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM pipeline_runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ControlNotFoundError(f"pipeline run not found: {run_id}")
        return pipeline_run_from_row(row)

    def _lock_file(
        self, asset_id: str, *, missing_ok: bool
    ) -> SourceFileControl | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_SOURCE_FILE_COLUMNS} FROM source_files "
                "WHERE asset_id = %s FOR UPDATE",
                (asset_id,),
            )
            row = cur.fetchone()
        if row is None:
            if missing_ok:
                return None
            raise ControlNotFoundError(f"source file not found: {asset_id}")
        return source_file_from_row(row)

    def _upsert_run(self, run: PipelineRun) -> PipelineRun:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE pipeline_runs SET
                    source = %(source)s,
                    pipeline_name = %(pipeline_name)s,
                    status = %(status)s,
                    attempt = %(attempt)s,
                    started_at = %(started_at)s,
                    completed_at = %(completed_at)s,
                    created_at = %(created_at)s,
                    updated_at = %(updated_at)s,
                    failure_category = %(failure_category)s,
                    failure_message = %(failure_message)s
                WHERE run_id = %(run_id)s
                RETURNING *
                """,
                _pipeline_run_params(run),
            )
            row = cur.fetchone()
        assert row is not None
        return pipeline_run_from_row(row)

    def _insert_file(self, control: SourceFileControl) -> SourceFileControl:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO source_files ({_SOURCE_FILE_COLUMNS})
                VALUES (
                    %(asset_id)s, %(run_id)s, %(source)s, %(entity)s, %(source_uri)s,
                    %(snapshot_date)s, %(updated_date)s, %(content_format)s,
                    %(declared_size_bytes)s, %(source_checksum_sha256)s, %(raw_object_key)s,
                    %(status)s, %(attempt_count)s, %(claimed_by)s, %(claim_token)s,
                    %(claimed_at)s, %(lease_expires_at)s, %(processed_at)s,
                    %(failure_category)s, %(failure_message)s, %(created_at)s, %(updated_at)s
                )
                RETURNING {_SOURCE_FILE_COLUMNS}
                """,
                _source_file_params(control),
            )
            row = cur.fetchone()
        assert row is not None
        return source_file_from_row(row)

    def _update_file(self, control: SourceFileControl) -> SourceFileControl:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE source_files SET
                    run_id = %(run_id)s,
                    source = %(source)s,
                    entity = %(entity)s,
                    source_uri = %(source_uri)s,
                    snapshot_date = %(snapshot_date)s,
                    updated_date = %(updated_date)s,
                    content_format = %(content_format)s,
                    declared_size_bytes = %(declared_size_bytes)s,
                    source_checksum_sha256 = %(source_checksum_sha256)s,
                    raw_object_key = %(raw_object_key)s,
                    status = %(status)s,
                    attempt_count = %(attempt_count)s,
                    claimed_by = %(claimed_by)s,
                    claim_token = %(claim_token)s,
                    claimed_at = %(claimed_at)s,
                    lease_expires_at = %(lease_expires_at)s,
                    processed_at = %(processed_at)s,
                    failure_category = %(failure_category)s,
                    failure_message = %(failure_message)s,
                    updated_at = %(updated_at)s
                WHERE asset_id = %(asset_id)s
                RETURNING {_SOURCE_FILE_COLUMNS}
                """,
                _source_file_params(control),
            )
            row = cur.fetchone()
        assert row is not None
        return source_file_from_row(row)


def _pipeline_run_params(run: PipelineRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "source": run.source,
        "pipeline_name": run.pipeline_name,
        "status": run.status.value,
        "attempt": run.attempt,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "failure_category": None
        if run.failure_category is None
        else run.failure_category.value,
        "failure_message": run.failure_message,
    }


def _source_file_params(control: SourceFileControl) -> dict[str, Any]:
    return {
        "asset_id": control.asset_id,
        "run_id": control.run_id,
        "source": control.source,
        "entity": control.entity,
        "source_uri": control.source_uri,
        "snapshot_date": control.snapshot_date,
        "updated_date": control.updated_date,
        "content_format": control.content_format,
        "declared_size_bytes": control.declared_size_bytes,
        "source_checksum_sha256": control.source_checksum_sha256,
        "raw_object_key": control.raw_object_key,
        "status": control.status.value,
        "attempt_count": control.attempt_count,
        "claimed_by": control.claimed_by,
        "claim_token": control.claim_token,
        "claimed_at": control.claimed_at,
        "lease_expires_at": control.lease_expires_at,
        "processed_at": control.processed_at,
        "failure_category": None
        if control.failure_category is None
        else control.failure_category.value,
        "failure_message": control.failure_message,
        "created_at": control.created_at,
        "updated_at": control.updated_at,
    }
