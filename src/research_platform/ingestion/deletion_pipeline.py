"""Bounded idempotent OpenAlex Works deletion processing (Step 13 / #21)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Protocol
from uuid import uuid4

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
from research_platform.ingestion.deletions_csv import (
    DeletedWorkRecord,
    iter_deleted_work_records,
)
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionDecodeError,
    IngestionError,
    IngestionFormatError,
)
from research_platform.ingestion.raw_landing import (
    GCS_LANDING_ENVIRONMENTS,
    classify_retrieval_or_landing,
    land_raw_asset,
    require_store_matches_config,
    write_run_landing_manifest,
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
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.landing_manifest import LandingManifest
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
    allow_gcs_landing: bool = False,
) -> LocalDeletionsIngestResult:
    """Process one bounded deletion CSV.GZ against an explicitly selected config.

    ``deletion_asset`` must be supplied by the caller (manifest discovery for
    deletions is not part of this slice; URI template uncertainty is documented).
    The store follows ``storage.backend``; ``allow_gcs_landing=True`` (DE-06) is
    the only way to land into GCS (``storage.backend=gcs``,
    ``environment=gcp-sandbox``).
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    _require_local_config(config, allow_gcs_landing=allow_gcs_landing)
    if connector is None:
        raise IngestionConfigError(
            "deletion ingest requires an explicit connector/fetch source"
        )
    objects = object_store or object_store_from_config(config)
    require_store_matches_config(config, objects, label="deletion ingest")
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
            result = ingest_deletion_asset(
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


def ingest_deletion_asset(
    *,
    asset: OpenAlexDeletionAssetMetadata,
    run: PipelineRun,
    config: PlatformConfig,
    control: ControlStore,
    canonical: CanonicalStore,
    objects: ObjectStore,
    connector: _Fetchable,
    worker_id: str = DEFAULT_WORKER_ID,
    lease_ttl: timedelta = DEFAULT_LEASE,
    clock: Callable[[], datetime] | None = None,
    decode_limits: CsvDeletionDecodeLimits | None = None,
    backend: PersistenceBackend = "memory",
    postgres_connection: Any | None = None,
) -> tuple[SourceFileControl, DeletionIngestStats | None, str | None]:
    """Reusable Stage: land + apply one deletion asset under an existing run.

    Does not create or finish ``PipelineRun``. Callers own run lifecycle.
    """
    require_store_matches_config(config, objects, label="deletion ingest")
    if clock is None:
        clock = lambda: datetime.now(tz=UTC)  # noqa: E731
    if decode_limits is None:
        decode_limits = CsvDeletionDecodeLimits()
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
        landed = land_raw_asset(
            asset=asset,
            run_id=run.run_id,
            connector=connector,
            objects=objects,
            raw_key=raw_key,
            max_bytes=config.sample_selection.max_file_size_bytes,
            decompressed_limit=decode_limits.max_decompressed_bytes,
            storage_backend=config.storage.backend,
            clock=clock,
            temp_prefix="oa-del-",
            temp_suffix=".csv.gz",
        )
        checksum, retrieved_at = landed.checksum, landed.retrieved_at
        # Bronze facts, written before (and independent of) canonical publication.
        write_run_landing_manifest(
            objects,
            LandingManifest(
                entity=asset.entity,
                pipeline_name=run.pipeline_name,
                run_id=run.run_id,
                run_created_at=run.created_at,
                storage_backend=config.storage.backend,
                entry=landed.entry,
            ),
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
            open_records=_record_stream_factory(
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


def _record_stream_factory(
    *,
    objects: ObjectStore,
    raw_key: str,
    decode_limits: CsvDeletionDecodeLimits,
) -> Callable[[], Iterator[DeletedWorkRecord]]:
    def open_records() -> Iterator[DeletedWorkRecord]:
        with objects.open(raw_key) as handle:
            yield from iter_deleted_work_records(handle, limits=decode_limits)

    return open_records


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


def _require_local_config(
    config: PlatformConfig, *, allow_gcs_landing: bool = False
) -> None:
    if allow_gcs_landing:
        if config.storage.backend != "gcs":
            raise IngestionConfigError("allow_gcs_landing requires storage.backend=gcs")
        if config.environment not in GCS_LANDING_ENVIRONMENTS:
            raise IngestionConfigError(
                "GCS landing is allowed for environment=gcp-sandbox only"
            )
    else:
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
    typed = classify_retrieval_or_landing(error)
    if typed is not None:
        return typed
    return FailureCategory.UNKNOWN, "deletion ingest failed"
