"""One-file idempotent local OpenAlex Works ingestion (Step 12 / #20)."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import map_openalex_work
from research_platform.canonical.openalex.models import CanonicalActivityState, CanonicalLineage
from research_platform.canonical.store import (
    CanonicalConflictError,
    CanonicalStore,
    CanonicalUpsertOutcome,
)
from research_platform.config.loader import load_config
from research_platform.config.models import PlatformConfig
from research_platform.control.errors import (
    ChecksumConflictError,
    ClaimConflictError,
    ControlError,
    StaleClaimError,
)
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    RecordProvenance,
    SourceFileControl,
)
from research_platform.control.reconciliation import RegistrationOutcome
from research_platform.control.store import ControlStore
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionDecodeError,
    IngestionError,
    IngestionFormatError,
)
from research_platform.ingestion.jsonl import iter_jsonl_gz_records
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.openalex.connector import OpenAlexConnector
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import ObjectConflictError, ObjectStoreError
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_raw_object_key

PIPELINE_NAME = "openalex-works-ingest"
DEFAULT_LEASE = timedelta(minutes=15)
DEFAULT_WORKER_ID = "local-worker"


class _Discoverable(Protocol):
    def discover_metadata(self) -> object: ...

    def fetch(self, asset: object) -> BinaryIO: ...


@dataclass(frozen=True)
class FileIngestStats:
    asset_id: str
    works_inserted: int = 0
    works_identical: int = 0
    works_replaced: int = 0
    works_stale: int = 0
    record_provenance_count: int = 0
    relationship_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalWorksIngestResult:
    run: PipelineRun
    source_file: SourceFileControl | None
    stats: FileIngestStats | None
    skipped_reason: str | None = None


def run_openalex_works_local_ingest(
    config_path: Path | str,
    *,
    control_store: ControlStore | None = None,
    canonical_store: CanonicalStore | None = None,
    object_store: ObjectStore | None = None,
    connector: OpenAlexConnector | None = None,
    worker_id: str = DEFAULT_WORKER_ID,
    lease_ttl: timedelta = DEFAULT_LEASE,
    now: Callable[[], datetime] | None = None,
    content_format: str = "jsonl",
) -> LocalWorksIngestResult:
    """Run one bounded local Works ingestion against an explicitly selected config.

    Requires ``environment=local`` and ``storage.backend=local``. Default sample
    bounds remain ``MAX_FILES=1`` and ``max_file_size_bytes=25_000_000``.

    Side-effect order for one file:

    1. register DISCOVERED
    2. transactional claim → PROCESSING
    3. stream fetch → immutable raw land (``put_if_absent``)
    4. stream decode landed JSONL.GZ → map → canonical upsert
    5. record provenance per Work
    6. mark SUCCESS (or FAILED)

    Raw landing is immutable and may outlive a rolled-back/failed control
    completion. Canonical + provenance + success share the control-store write
    boundary after landing.
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    _require_local_config(config)
    if content_format != "jsonl":
        raise IngestionFormatError("Step 12 local ingestion supports jsonl only")

    control = control_store or InMemoryControlStore()
    canonical = canonical_store or InMemoryCanonicalStore()
    objects = object_store or LocalObjectStore(config.storage)
    openalex = connector or OpenAlexConnector(
        sample_selection=config.sample_selection,
        content_format=content_format,
    )

    started = clock()
    run = control.create_pipeline_run(
        PipelineRun.model_validate(
            {
                "run_id": uuid4(),
                "source": "openalex",
                "pipeline_name": PIPELINE_NAME,
                "status": PipelineRunStatus.PROCESSING,
                "attempt": 1,
                "started_at": started,
                "created_at": started,
                "updated_at": started,
            }
        )
    )

    try:
        selection = openalex.discover_metadata()
        selected = tuple(getattr(selection, "selected", ()))
        if not selected:
            finished = control.finish_pipeline_run(
                run.run_id,
                status=PipelineRunStatus.SUCCESS,
                completed_at=clock(),
            )
            return LocalWorksIngestResult(
                run=finished,
                source_file=None,
                stats=None,
                skipped_reason="NO_ELIGIBLE_FILE",
            )
        if len(selected) > config.sample_selection.max_files:
            selected = selected[: config.sample_selection.max_files]
        asset = selected[0]
        if not isinstance(asset, OpenAlexAssetMetadata):
            raise IngestionFormatError("selected asset must be OpenAlexAssetMetadata")
        if asset.content_format != "jsonl":
            raise IngestionFormatError("selected asset must be jsonl")

        result = _ingest_one_asset(
            asset=asset,
            run=run,
            config=config,
            control=control,
            canonical=canonical,
            objects=objects,
            connector=openalex,
            worker_id=worker_id,
            lease_ttl=lease_ttl,
            clock=clock,
        )
        finished = control.finish_pipeline_run(
            run.run_id,
            status=PipelineRunStatus.SUCCESS,
            completed_at=clock(),
        )
        return LocalWorksIngestResult(
            run=finished,
            source_file=result[0],
            stats=result[1],
            skipped_reason=result[2],
        )
    except Exception as error:
        category, message = _classify_failure(error)
        try:
            control.finish_pipeline_run(
                run.run_id,
                status=PipelineRunStatus.FAILED,
                completed_at=clock(),
                failure_category=category,
                failure_message=message,
            )
        except ControlError:
            pass
        if isinstance(
            error,
            IngestionError
            | ControlError
            | ObjectStoreError
            | CanonicalConflictError,
        ):
            raise
        raise IngestionError(message) from error


def _ingest_one_asset(
    *,
    asset: OpenAlexAssetMetadata,
    run: PipelineRun,
    config: PlatformConfig,
    control: ControlStore,
    canonical: CanonicalStore,
    objects: ObjectStore,
    connector: OpenAlexConnector,
    worker_id: str,
    lease_ttl: timedelta,
    clock: Callable[[], datetime],
) -> tuple[SourceFileControl, FileIngestStats | None, str | None]:
    discovered_at = clock()
    incoming = SourceFileControl.model_validate(
        {
            "asset_id": asset.asset_id,
            "run_id": run.run_id,
            "source": "openalex",
            "entity": asset.entity,
            "source_uri": asset.file_uri,
            "snapshot_date": asset.snapshot_date,
            "updated_date": asset.updated_date,
            "content_format": asset.content_format,
            "declared_size_bytes": asset.byte_size,
            "status": ControlStatus.DISCOVERED,
            "attempt_count": 0,
            "created_at": discovered_at,
            "updated_at": discovered_at,
        }
    )
    control_row, outcome = control.register_source_file(incoming)
    if outcome is RegistrationOutcome.ALREADY_SUCCESS:
        return control_row, None, "ALREADY_SUCCESS"
    if outcome is RegistrationOutcome.CHECKSUM_CONFLICT:
        raise ChecksumConflictError(
            "source checksum conflicts for an existing asset identity"
        )  # surfaced to caller; run marked FAILED by outer handler
    if outcome is RegistrationOutcome.ALREADY_IN_PROGRESS:
        # Attempt stale recovery; a still-valid claim remains a conflict.
        try:
            control.recover_stale_claim(asset.asset_id, now=clock())
        except ClaimConflictError:
            raise ClaimConflictError("source file already claimed by another worker") from None
        control_row = (
            control.get_source_file(asset.asset_id)
            if isinstance(control, InMemoryControlStore)
            else control_row
        )

    claim_token = uuid4()
    claimed_at = clock()
    control_row = control.claim_source_file(
        asset.asset_id,
        run_id=run.run_id,
        claimed_by=worker_id,
        claim_token=claim_token,
        claimed_at=claimed_at,
        lease_expires_at=claimed_at + lease_ttl,
    )

    raw_key = openalex_raw_object_key(asset)
    try:
        checksum, retrieved_at = _land_raw(
            asset=asset,
            run_id=run.run_id,
            connector=connector,
            objects=objects,
            raw_key=raw_key,
            max_bytes=config.sample_selection.max_file_size_bytes,
            clock=clock,
        )
        stats = _canonicalize_landed(
            asset=asset,
            run_id=run.run_id,
            checksum=checksum,
            objects=objects,
            raw_key=raw_key,
            canonical=canonical,
            control=control,
            processed_at=clock(),
        )
        provenance = IngestionProvenance.model_validate(
            {
                "run_id": run.run_id,
                "source": "openalex",
                "source_uri": asset.file_uri,
                "retrieved_at": retrieved_at,
                "sha256": checksum,
            }
        )
        succeeded = control.mark_source_file_success(
            asset.asset_id,
            claim_token=claim_token,
            processed_at=clock(),
            raw_object_key=raw_key,
            source_checksum_sha256=checksum,
            retrieval_provenance=provenance,
        )
        return succeeded, stats, None
    except Exception as error:
        category, message = _classify_failure(error)
        try:
            failed = control.mark_source_file_failed(
                asset.asset_id,
                claim_token=claim_token,
                processed_at=clock(),
                failure_category=category,
                failure_message=message,
            )
        except ControlError:
            failed = control_row
        if isinstance(
            error,
            IngestionError
            | ControlError
            | ObjectStoreError
            | CanonicalConflictError
            | ChecksumConflictError,
        ):
            raise
        raise IngestionError(message) from error


def _land_raw(
    *,
    asset: OpenAlexAssetMetadata,
    run_id: UUID,
    connector: OpenAlexConnector,
    objects: ObjectStore,
    raw_key: str,
    max_bytes: int,
    clock: Callable[[], datetime],
) -> tuple[str, datetime]:
    retrieved_at = clock()
    source_asset = asset.to_source_asset()
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(prefix="oa-ingest-", suffix=".gz") as tmp:
        with connector.fetch(source_asset) as payload:
            while True:
                chunk = payload.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise IngestionFormatError(
                        "retrieved payload exceeds configured max_file_size_bytes"
                    )
                digest.update(chunk)
                tmp.write(chunk)
        tmp.flush()
        tmp.seek(0)
        checksum = digest.hexdigest()
        provenance = IngestionProvenance.model_validate(
            {
                "run_id": run_id,
                "source": "openalex",
                "source_uri": asset.file_uri,
                "retrieved_at": retrieved_at,
                "sha256": checksum,
            }
        )
        try:
            objects.put_if_absent(raw_key, tmp, provenance)
        except ObjectConflictError:
            raise
        return checksum, retrieved_at


def _canonicalize_landed(
    *,
    asset: OpenAlexAssetMetadata,
    run_id: UUID,
    checksum: str,
    objects: ObjectStore,
    raw_key: str,
    canonical: CanonicalStore,
    control: ControlStore,
    processed_at: datetime,
) -> FileIngestStats:
    inserted = identical = replaced = stale = 0
    provenance_count = 0
    with objects.open(raw_key) as handle:
        for record in iter_jsonl_gz_records(handle):
            lineage = CanonicalLineage.model_validate(
                {
                    "source_asset_id": asset.asset_id,
                    "source_checksum_sha256": checksum,
                    "source_updated_date": asset.updated_date,
                    "run_id": run_id,
                    "processed_at": processed_at,
                    "activity_state": CanonicalActivityState.ACTIVE,
                }
            )
            bundle = map_openalex_work(record, lineage=lineage)
            outcome = canonical.upsert_work_bundle(bundle)
            if outcome is CanonicalUpsertOutcome.INSERTED:
                inserted += 1
            elif outcome is CanonicalUpsertOutcome.IDENTICAL:
                identical += 1
            elif outcome is CanonicalUpsertOutcome.REPLACED:
                replaced += 1
            elif outcome is CanonicalUpsertOutcome.STALE:
                stale += 1
            control.record_provenance(
                RecordProvenance.model_validate(
                    {
                        "record_id": bundle.work.work_id,
                        "entity_type": "work",
                        "asset_id": asset.asset_id,
                        "source_checksum_sha256": checksum,
                        "run_id": run_id,
                        "source_uri": asset.file_uri,
                        "processed_at": processed_at,
                        "source_updated_date": asset.updated_date,
                    }
                )
            )
            provenance_count += 1
    return FileIngestStats(
        asset_id=asset.asset_id,
        works_inserted=inserted,
        works_identical=identical,
        works_replaced=replaced,
        works_stale=stale,
        record_provenance_count=provenance_count,
        relationship_counts=canonical.relationship_counts(),
    )


def _require_local_config(config: PlatformConfig) -> None:
    if config.environment != "local":
        raise IngestionConfigError("local Works ingestion requires environment=local")
    if config.storage.backend != "local":
        raise IngestionConfigError("local Works ingestion requires storage.backend=local")
    if config.sample_selection.max_files != 1:
        raise IngestionConfigError("default local ingestion requires sample_selection.max_files=1")
    if config.sample_selection.max_file_size_bytes > 25_000_000:
        raise IngestionConfigError(
            "default local ingestion requires max_file_size_bytes <= 25000000"
        )


def _classify_failure(error: BaseException) -> tuple[FailureCategory, str]:
    if isinstance(error, ChecksumConflictError | ObjectConflictError):
        return FailureCategory.CHECKSUM, "source checksum or landing conflict"
    if isinstance(error, ClaimConflictError | StaleClaimError):
        return FailureCategory.CLAIM, "claim conflict"
    if isinstance(error, CanonicalConflictError):
        return FailureCategory.CANONICAL, "canonical version or entity conflict"
    if isinstance(error, IngestionDecodeError):
        return FailureCategory.CANONICAL, "source decode failed"
    if isinstance(error, IngestionFormatError):
        return FailureCategory.VALIDATION, "unsupported or invalid source format"
    if isinstance(error, IngestionConfigError):
        return FailureCategory.VALIDATION, "invalid local ingestion configuration"
    if isinstance(error, ObjectStoreError):
        return FailureCategory.LANDING, "immutable landing failed"
    name = type(error).__name__
    if "Size" in name or "Network" in name or "Timeout" in name or "Access" in name:
        return FailureCategory.RETRIEVAL, "source retrieval failed"
    return FailureCategory.UNKNOWN, "ingestion failed"
