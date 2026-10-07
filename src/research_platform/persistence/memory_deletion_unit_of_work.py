"""In-memory deletion publication with rollback emulation."""

from __future__ import annotations

import copy
from datetime import date

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.control.deletion_events import DeletionEvent
from research_platform.control.lifecycle import assert_claim_owned
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import RecordProvenance
from research_platform.ingestion.deletions_csv import DeletedWorkRecord
from research_platform.ingestion.errors import IngestionDecodeError
from research_platform.persistence.deletion_unit_of_work import (
    DeletionCounters,
    DeletionPublishRequest,
    DeletionPublishResult,
)


def publish_claimed_deletions_memory(
    control: InMemoryControlStore,
    canonical: InMemoryCanonicalStore,
    request: DeletionPublishRequest,
) -> DeletionPublishResult:
    existing = control.get_source_file(request.asset_id)
    if existing is None:
        raise LookupError(f"source file not found: {request.asset_id}")
    assert_claim_owned(existing, claim_token=request.claim_token, now=request.now)

    works_before = copy.deepcopy(canonical._works)  # noqa: SLF001
    provenance_before = list(control._provenance)  # noqa: SLF001
    events_before = list(getattr(control, "_deletion_events", []))
    if not hasattr(control, "_deletion_events"):
        control._deletion_events = []  # noqa: SLF001

    try:
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
            outcome = canonical.apply_work_deletion(
                record.work_id,
                deletion_asset_id=request.asset_id,
                source_checksum_sha256=request.source_checksum_sha256,
                run_id=request.retrieval_provenance.run_id,
                deleted_date=record.deleted_date,
                processed_at=request.processed_at,
                deleted_at=request.processed_at,
            )
            counters = counters.after_outcome(outcome)
            control._deletion_events.append(  # noqa: SLF001
                _event(request, record, outcome)
            )
            control.record_provenance(
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
        succeeded = control.mark_source_file_success(
            request.asset_id,
            claim_token=request.claim_token,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            retrieval_provenance=request.retrieval_provenance,
        )
        return DeletionPublishResult(source_file=succeeded, counters=counters)
    except Exception:
        canonical._works = works_before  # noqa: SLF001
        control._provenance = provenance_before  # noqa: SLF001
        control._deletion_events = events_before  # noqa: SLF001
        raise


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
