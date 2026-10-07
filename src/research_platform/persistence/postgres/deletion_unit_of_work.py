"""PostgreSQL one-asset deletion publication transaction."""

from __future__ import annotations

from datetime import date

import psycopg
from psycopg.rows import dict_row

from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.control.deletion_events import DeletionEvent
from research_platform.control.lifecycle import (
    apply_source_file_success,
    assert_claim_owned,
)
from research_platform.control.models import RecordProvenance
from research_platform.ingestion.deletions_csv import DeletedWorkRecord
from research_platform.ingestion.errors import IngestionDecodeError
from research_platform.persistence.deletion_unit_of_work import (
    DeletionCounters,
    DeletionPublishRequest,
    DeletionPublishResult,
)
from research_platform.persistence.postgres.canonical_store import apply_work_deletion
from research_platform.persistence.postgres.control_store import PostgresControlStore
from research_platform.persistence.postgres.row_codec import source_file_from_row


def publish_claimed_deletions(
    connection: psycopg.Connection, request: DeletionPublishRequest
) -> DeletionPublishResult:
    """Stream deletion rows, tombstone, provenance, and SUCCESS in one transaction."""
    _ = request.retrieval_provenance
    control = PostgresControlStore(connection)
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM source_files WHERE asset_id = %s FOR UPDATE",
                (request.asset_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError(f"source file not found: {request.asset_id}")
        existing = source_file_from_row(row)
        assert_claim_owned(
            existing, claim_token=request.claim_token, now=request.now
        )

        counters = DeletionCounters()
        seen: dict[str, date] = {}
        for record in request.open_records():
            prior = seen.get(record.work_id)
            if prior is not None:
                if prior != record.deleted_date:
                    raise IngestionDecodeError(
                        "conflicting deleted_date for the same work_id in one "
                        "deletion ledger"
                    )
                counters = counters.after_duplicate()
                continue
            seen[record.work_id] = record.deleted_date
            outcome = apply_work_deletion(
                connection,
                record.work_id,
                deletion_asset_id=request.asset_id,
                source_checksum_sha256=request.source_checksum_sha256,
                run_id=request.retrieval_provenance.run_id,
                deleted_date=record.deleted_date,
                processed_at=request.processed_at,
                deleted_at=request.processed_at,
            )
            counters = counters.after_outcome(outcome)
            _upsert_deletion_event(connection, _event(request, record, outcome))
            control._upsert_provenance(  # noqa: SLF001 - same txn
                RecordProvenance.model_validate(
                    {
                        "record_id": record.work_id,
                        "entity_type": "work-deletion",
                        "asset_id": request.asset_id,
                        "source_checksum_sha256": request.source_checksum_sha256,
                        "run_id": request.retrieval_provenance.run_id,
                        "source_uri": request.source_uri,
                        "processed_at": request.processed_at,
                        "source_updated_date": record.deleted_date,
                    }
                )
            )

        succeeded = apply_source_file_success(
            existing,
            claim_token=request.claim_token,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            updated_at=request.processed_at,
            now=request.now,
        )
        stored = control._update_file(succeeded)  # noqa: SLF001
        return DeletionPublishResult(source_file=stored, counters=counters)


def _event(
    request: DeletionPublishRequest,
    record: DeletedWorkRecord,
    outcome: DeletionOutcome,
) -> DeletionEvent:
    return DeletionEvent.model_validate(
        {
            "work_id": record.work_id,
            "asset_id": request.asset_id,
            "source_checksum_sha256": request.source_checksum_sha256,
            "run_id": request.retrieval_provenance.run_id,
            "source_uri": request.source_uri,
            "deleted_date": record.deleted_date,
            "source_updated_date": request.source_updated_date,
            "processed_at": request.processed_at,
            "outcome": outcome,
        }
    )


def _upsert_deletion_event(
    connection: psycopg.Connection, event: DeletionEvent
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deletion_events (
                work_id, asset_id, source_checksum_sha256, run_id, source_uri,
                deleted_date, source_updated_date, processed_at, outcome
            ) VALUES (
                %(work_id)s, %(asset_id)s, %(source_checksum_sha256)s, %(run_id)s,
                %(source_uri)s, %(deleted_date)s, %(source_updated_date)s,
                %(processed_at)s, %(outcome)s
            )
            ON CONFLICT (work_id, asset_id, source_checksum_sha256)
            DO UPDATE SET
                run_id = EXCLUDED.run_id,
                source_uri = EXCLUDED.source_uri,
                deleted_date = EXCLUDED.deleted_date,
                source_updated_date = EXCLUDED.source_updated_date,
                processed_at = EXCLUDED.processed_at,
                outcome = EXCLUDED.outcome
            """,
            {
                "work_id": event.work_id,
                "asset_id": event.asset_id,
                "source_checksum_sha256": event.source_checksum_sha256,
                "run_id": event.run_id,
                "source_uri": event.source_uri,
                "deleted_date": event.deleted_date,
                "source_updated_date": event.source_updated_date,
                "processed_at": event.processed_at,
                "outcome": event.outcome.value,
            },
        )
