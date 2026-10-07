"""Control-store write protocol (contracts only; no database adapter).

``Warehouse.query()`` remains a read interface. Pipeline control persistence and
transactional claims belong behind ``ControlStore``, not SQL string writes through
the warehouse adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal
from uuid import UUID

from research_platform.control.models import (
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)
from research_platform.control.reconciliation import RegistrationOutcome
from research_platform.provenance.models import IngestionProvenance


class ControlStore(ABC):
    """Atomic control-plane operations for later ingestion workers.

    Intended transaction boundary for one source file (later implementation)::

        begin
          claim_source_file
          ... retrieve / land immutable raw / publish canonical ...
          record_provenance (zero or more)
          mark_source_file_success  OR  mark_source_file_failed
        commit

    SUCCESS must not be published solely because a status field changed: the later
    adapter must require the current claim token and durable side effects agreed by
    the ingestion step (raw landing + canonical publication) inside the same
    transactional boundary or an equivalent two-phase protocol.

    This ABC does not open sockets, connect to PostgreSQL, or execute SQL.
    """

    @abstractmethod
    def create_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        """Persist a new PENDING or PROCESSING pipeline run."""
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def finish_pipeline_run(
        self,
        run_id: UUID,
        *,
        status: Literal[PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED],
        completed_at: datetime,
        failure_category: FailureCategory | None = None,
        failure_message: str | None = None,
    ) -> PipelineRun:
        """Atomically complete a PROCESSING run as SUCCESS or FAILED."""
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def retry_pipeline_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
    ) -> PipelineRun:
        """Explicit FAILED -> PROCESSING retry; increments ``attempt`` by exactly 1.

        Ordinary ``create``/start must not silently retry a FAILED run.
        """
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def register_source_file(
        self, control: SourceFileControl
    ) -> tuple[SourceFileControl, RegistrationOutcome]:
        """Register or reconcile a DISCOVERED source file.

        Must apply :func:`reconcile_registration` semantics: identical replay is
        idempotent; checksum conflicts are explicit; SUCCESS rows are not reset.
        """
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
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
        """Atomically acquire exclusive PROCESSING ownership.

        Only one worker may hold a valid claim. Acquisition must use a single
        conditional update (or equivalent) so concurrent claimants cannot both
        succeed. ``DISCOVERED -> PROCESSING`` uses event ``claim``;
        ``FAILED -> PROCESSING`` uses event ``retry`` and increments attempt_count.
        """
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
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
        """Mark SUCCESS only when ``claim_token`` matches the active non-expired claim.

        ``retrieval_provenance`` is the immutable landing provenance already stored
        with raw bytes; this method records control completion, not a second copy of
        payload bytes.
        """
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def mark_source_file_failed(
        self,
        asset_id: str,
        *,
        claim_token: UUID,
        processed_at: datetime,
        failure_category: FailureCategory,
        failure_message: str,
    ) -> SourceFileControl:
        """Mark FAILED for the worker that owns the current claim token."""
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def recover_stale_claim(
        self, asset_id: str, *, now: datetime
    ) -> SourceFileControl:
        """Expire a PROCESSING row whose lease has elapsed into FAILED (STALE_CLAIM).

        Must not steal a still-valid claim. A subsequent ``claim_source_file`` retry
        is required before another worker owns the asset.
        """
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    @abstractmethod
    def record_provenance(self, record: RecordProvenance) -> RecordProvenance:
        """Persist one record-level lineage row for a canonical publication."""
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")


class UnimplementedControlStore(ControlStore):
    """Fail-fast skeleton so construction stays offline and side-effect free."""

    def create_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def finish_pipeline_run(
        self,
        run_id: UUID,
        *,
        status: Literal[PipelineRunStatus.SUCCESS, PipelineRunStatus.FAILED],
        completed_at: datetime,
        failure_category: FailureCategory | None = None,
        failure_message: str | None = None,
    ) -> PipelineRun:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def retry_pipeline_run(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
    ) -> PipelineRun:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def register_source_file(
        self, control: SourceFileControl
    ) -> tuple[SourceFileControl, RegistrationOutcome]:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

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
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

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
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def mark_source_file_failed(
        self,
        asset_id: str,
        *,
        claim_token: UUID,
        processed_at: datetime,
        failure_category: FailureCategory,
        failure_message: str,
    ) -> SourceFileControl:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def recover_stale_claim(
        self, asset_id: str, *, now: datetime
    ) -> SourceFileControl:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")

    def record_provenance(self, record: RecordProvenance) -> RecordProvenance:
        raise NotImplementedError("ControlStore persistence is deferred to a later phase")
