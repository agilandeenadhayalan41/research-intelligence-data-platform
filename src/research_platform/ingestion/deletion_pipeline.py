"""Bounded idempotent OpenAlex Works deletion processing (Step 13 / #21)."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Protocol
from uuid import UUID, uuid4

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.deletions import RestoreRequiredError
from research_platform.canonical.store import CanonicalConflictError, CanonicalStore
from research_platform.config.loader import load_config
from research_platform.config.models import PlatformConfig
from research_platform.control.errors import (
    ChecksumConflictError,
    ClaimConflictError,
    ControlError,
    IdempotencyConflictError,
    StaleClaimError,
)
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import (
    ControlStatus,
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
    SourceFileControl,
)
from research_platform.control.reconciliation import RegistrationOutcome
from research_platform.control.store import ControlStore
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletions_csv import iter_deleted_work_ids
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionDecodeError,
    IngestionError,
    IngestionFormatError,
)
from research_platform.persistence.deletion_unit_of_work import (
    DeletionCounters,
    DeletionPublishRequest,
)
from research_platform.persistence.memory_deletion_unit_of_work import (
    publish_claimed_deletions_memory,
)
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.openalex.deletion_metadata import (
    OpenAlexDeletionAssetMetadata,
)
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import ObjectConflictError, ObjectStoreError
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_deletion_raw_object_key

if TYPE_CHECKING:
    import psycopg

PIPELINE_NAME = "openalex-works-deletions"
DEFAULT_LEASE = timedelta(minutes=15)
DEFAULT_WORKER_ID = "local-worker"
PersistenceBackend = Literal["memory", "postgres"]


class _Fetchable(Protocol):
    def fetch(self, asset: object) -> BinaryIO: ...


@dataclass(frozen=True)
class DeletionIngestStats:
    asset_id: str
    rows_seen: int = 0
    unique_ids: int = 0
    duplicates: int = 0
    deleted: int = 0
    already_deleted: int = 0
    unknown: int = 0
    stale: int = 0
    conflicts: int = 0


@dataclass(frozen=True)
class LocalDeletionsIngestResult:
    run: PipelineRun
    source_file: SourceFileControl | None
    stats: DeletionIngestStats | None
    skipped_reason: str | None = None
    persistence_backend: PersistenceBackend = "memory"


def run_openalex_deletions_local_ingest(
    config_path: Path | str,
    *,
    backend: PersistenceBackend = "memory",
    deletion_asset: OpenAlexDeletionAssetMetadata,
    control_store: ControlStore | None = None,
    canonical_store: CanonicalStore | None = None,
    object_store: ObjectStore | None = None,
    connector: _Fetchable | None = None,
    postgres_connection: Any | None = None,
    worker_id: str = DEFAULT_WORKER_ID,
    lease_ttl: timedelta = DEFAULT_LEASE,
    now: Callable[[], datetime] | None = None,
    decode_limits: CsvDeletionDecodeLimits | None = None,
) -> LocalDeletionsIngestResult:
    """Process one bounded deletion CSV.GZ against an explicitly selected config.

    ``deletion_asset`` must be supplied by the caller (manifest discovery for
    deletions is not part of this slice; URI template uncertainty is documented).
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    _require_local_config(config)
    limits = decode_limits or CsvDeletionDecodeLimits()

    owns_connection = False
    connection: Any | None = None
    if backend == "postgres":
        from research_platform.persistence.postgres import (
            PostgresCanonicalStore,
            PostgresControlStore,
            apply_ingestion_schema,
            connect_postgres,
            postgres_dsn_from_env,
        )

        if control_store is not None or canonical_store is not None:
            raise IngestionConfigError(
                "postgres backend manages its own ControlStore/CanonicalStore"
            )
        connection = postgres_connection
        if connection is None:
            connection = connect_postgres(
                postgres_dsn_from_env(config.warehouse.postgres_dsn_env)
            )
            owns_connection = True
            apply_ingestion_schema(connection)
        control: ControlStore = PostgresControlStore(connection)
        canonical: CanonicalStore = PostgresCanonicalStore(connection)
    else:
        control = control_store or InMemoryControlStore()
        canonical = canonical_store or InMemoryCanonicalStore()

    objects = object_store or LocalObjectStore(config.storage)
    if connector is None:
        raise IngestionConfigError(
            "deletion ingest requires an explicit connector/fetch source"
        )

    started = clock()
    try:
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
            result = _ingest_one_deletion_asset(
                asset=deletion_asset,
                run=run,
                config=config,
                control=control,
                canonical=canonical,
                objects=objects,
                connector=connector,
                worker_id=worker_id,
                lease_ttl=lease_ttl,
                clock=clock,
                decode_limits=limits,
                backend=backend,
                postgres_connection=connection,
            )
            finished = control.finish_pipeline_run(
                run.run_id,
                status=PipelineRunStatus.SUCCESS,
                completed_at=clock(),
            )
            return LocalDeletionsIngestResult(
                run=finished,
                source_file=result[0],
                stats=result[1],
                skipped_reason=result[2],
                persistence_backend=backend,
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
                | CanonicalConflictError
                | IdempotencyConflictError
                | RestoreRequiredError,
            ):
                raise
            raise IngestionError(message) from error
    finally:
        if owns_connection and connection is not None:
            connection.close()


def _ingest_one_deletion_asset(
    *,
    asset: OpenAlexDeletionAssetMetadata,
    run: PipelineRun,
    config: PlatformConfig,
    control: ControlStore,
    canonical: CanonicalStore,
    objects: ObjectStore,
    connector: _Fetchable,
    worker_id: str,
    lease_ttl: timedelta,
    clock: Callable[[], datetime],
    decode_limits: CsvDeletionDecodeLimits,
    backend: PersistenceBackend,
    postgres_connection: Any | None,
) -> tuple[SourceFileControl, DeletionIngestStats | None, str | None]:
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
            "source checksum conflicts for an existing deletion asset identity"
        )
    if outcome is RegistrationOutcome.ALREADY_IN_PROGRESS:
        try:
            control.recover_stale_claim(asset.asset_id, now=clock())
        except ClaimConflictError:
            raise ClaimConflictError(
                "deletion source file already claimed by another worker"
            ) from None

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

    raw_key = openalex_deletion_raw_object_key(asset)
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
        processed_at = clock()
        retrieval_provenance = IngestionProvenance.model_validate(
            {
                "run_id": run.run_id,
                "source": "openalex",
                "source_uri": asset.file_uri,
                "retrieved_at": retrieved_at,
                "sha256": checksum,
            }
        )
        publish_request = DeletionPublishRequest(
            asset_id=asset.asset_id,
            claim_token=claim_token,
            now=clock(),
            processed_at=processed_at,
            raw_object_key=raw_key,
            source_checksum_sha256=checksum,
            retrieval_provenance=retrieval_provenance,
            source_uri=asset.file_uri,
            source_updated_date=asset.updated_date,
            open_work_ids=_id_stream_factory(
                objects=objects,
                raw_key=raw_key,
                decode_limits=decode_limits,
            ),
        )
        if backend == "postgres":
            from research_platform.persistence.postgres.deletion_unit_of_work import (
                publish_claimed_deletions,
            )

            assert postgres_connection is not None
            published = publish_claimed_deletions(postgres_connection, publish_request)
        else:
            assert isinstance(control, InMemoryControlStore)
            assert isinstance(canonical, InMemoryCanonicalStore)
            published = publish_claimed_deletions_memory(
                control, canonical, publish_request
            )
        stats = _stats(asset.asset_id, published.counters)
        return published.source_file, stats, None
    except Exception as error:
        category, message = _classify_failure(error)
        try:
            control.mark_source_file_failed(
                asset.asset_id,
                claim_token=claim_token,
                processed_at=clock(),
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
            | CanonicalConflictError
            | ChecksumConflictError
            | IdempotencyConflictError
            | RestoreRequiredError,
        ):
            raise
        raise IngestionError(message) from error


def _id_stream_factory(
    *,
    objects: ObjectStore,
    raw_key: str,
    decode_limits: CsvDeletionDecodeLimits,
) -> Callable[[], Iterator[str]]:
    def open_work_ids() -> Iterator[str]:
        with objects.open(raw_key) as handle:
            yield from iter_deleted_work_ids(handle, limits=decode_limits)

    return open_work_ids


def _land_raw(
    *,
    asset: OpenAlexDeletionAssetMetadata,
    run_id: UUID,
    connector: _Fetchable,
    objects: ObjectStore,
    raw_key: str,
    max_bytes: int,
    clock: Callable[[], datetime],
) -> tuple[str, datetime]:
    retrieved_at = clock()
    source_asset = asset.to_source_asset()
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(prefix="oa-del-", suffix=".csv.gz") as tmp:
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
            if not _raw_checksum_matches(objects, raw_key, checksum):
                raise
        return checksum, retrieved_at


def _raw_checksum_matches(objects: ObjectStore, raw_key: str, checksum: str) -> bool:
    digest = hashlib.sha256()
    with objects.open(raw_key) as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest() == checksum


def _stats(asset_id: str, counters: DeletionCounters) -> DeletionIngestStats:
    return DeletionIngestStats(
        asset_id=asset_id,
        rows_seen=counters.rows_seen,
        unique_ids=counters.unique_ids,
        duplicates=counters.duplicates,
        deleted=counters.deleted,
        already_deleted=counters.already_deleted,
        unknown=counters.unknown,
        stale=counters.stale,
        conflicts=counters.conflicts,
    )


def _require_local_config(config: PlatformConfig) -> None:
    if config.environment != "local":
        raise IngestionConfigError("local deletion ingest requires environment=local")
    if config.storage.backend != "local":
        raise IngestionConfigError("local deletion ingest requires storage.backend=local")
    if config.sample_selection.max_files != 1:
        raise IngestionConfigError(
            "default local deletion ingest requires sample_selection.max_files=1"
        )
    if config.sample_selection.max_file_size_bytes > 25_000_000:
        raise IngestionConfigError(
            "default local deletion ingest requires max_file_size_bytes <= 25000000"
        )


def _classify_failure(error: BaseException) -> tuple[FailureCategory, str]:
    if isinstance(error, ChecksumConflictError | ObjectConflictError):
        return FailureCategory.CHECKSUM, "source checksum or landing conflict"
    if isinstance(error, ClaimConflictError | StaleClaimError):
        return FailureCategory.CLAIM, "claim conflict"
    if isinstance(error, IdempotencyConflictError):
        return FailureCategory.VALIDATION, "source identity metadata conflict"
    if isinstance(error, RestoreRequiredError | CanonicalConflictError):
        return FailureCategory.CANONICAL, "canonical deletion/version conflict"
    if isinstance(error, IngestionDecodeError):
        return FailureCategory.CANONICAL, "deletion source decode failed"
    if isinstance(error, IngestionFormatError):
        return FailureCategory.VALIDATION, "unsupported or invalid deletion format"
    if isinstance(error, IngestionConfigError):
        return FailureCategory.VALIDATION, "invalid local deletion configuration"
    if isinstance(error, ObjectStoreError):
        return FailureCategory.LANDING, "immutable landing failed"
    return FailureCategory.UNKNOWN, "deletion ingest failed"
