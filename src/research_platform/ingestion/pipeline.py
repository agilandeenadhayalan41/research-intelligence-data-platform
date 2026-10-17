"""One-file idempotent local OpenAlex Works ingestion (Step 12 / #20)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Protocol
from uuid import UUID, uuid4

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import map_openalex_work
from research_platform.canonical.openalex.models import (
    CanonicalActivityState,
    CanonicalLineage,
)
from research_platform.canonical.openalex.deletions import RestoreRequiredError
from research_platform.canonical.store import (
    CanonicalConflictError,
    CanonicalStore,
)
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
    RecordProvenance,
    SourceFileControl,
)
from research_platform.control.reconciliation import RegistrationOutcome
from research_platform.control.store import ControlStore
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.errors import (
    IngestionConfigError,
    IngestionDecodeError,
    IngestionError,
    IngestionFormatError,
)
from research_platform.ingestion.jsonl import iter_jsonl_gz_records
from research_platform.ingestion.raw_landing import (
    GCS_LANDING_ENVIRONMENTS,
    classify_retrieval_or_landing,
    land_raw_asset,
    require_store_matches_config,
    write_run_landing_manifest,
)
from research_platform.persistence.memory_unit_of_work import publish_claimed_asset_memory
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    PublishCounters,
    StreamedWorkRecord,
)
from research_platform.provenance.models import IngestionProvenance
from research_platform.sources.openalex.connector import OpenAlexConnector
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import ObjectConflictError, ObjectStoreError
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.landing_manifest import LandingManifest
from research_platform.storage.openalex_layout import openalex_raw_object_key

if TYPE_CHECKING:
    import psycopg

PIPELINE_NAME = "openalex-works-ingest"
DEFAULT_LEASE = timedelta(minutes=15)
DEFAULT_WORKER_ID = "local-worker"
PersistenceBackend = Literal["memory", "postgres"]


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
    persistence_backend: PersistenceBackend = "memory"


def run_openalex_works_local_ingest(
    config_path: Path | str,
    *,
    backend: PersistenceBackend = "memory",
    control_store: ControlStore | None = None,
    canonical_store: CanonicalStore | None = None,
    object_store: ObjectStore | None = None,
    connector: OpenAlexConnector | None = None,
    postgres_connection: Any | None = None,
    worker_id: str = DEFAULT_WORKER_ID,
    lease_ttl: timedelta = DEFAULT_LEASE,
    now: Callable[[], datetime] | None = None,
    content_format: str = "jsonl",
    decode_limits: JsonlDecodeLimits | None = None,
    allow_gcs_landing: bool = False,
) -> LocalWorksIngestResult:
    """Run one bounded local Works ingestion against an explicitly selected config.

    Persistence:

    - ``backend="memory"``: ephemeral in-process stores (tests/demo only)
    - ``backend="postgres"``: durable local PostgreSQL ControlStore/CanonicalStore
      behind one asset publication transaction (not ``Warehouse.query``)

    After immutable raw landing, publication is:

    ``BEGIN → re-check claim → upsert canonical → provenance → SUCCESS → COMMIT``

    Raw ObjectStore bytes are outside that DB transaction and may remain after rollback.

    The store follows ``storage.backend``. ``allow_gcs_landing=True`` (DE-06) is
    the only way to land into GCS: it needs ``storage.backend=gcs``,
    ``environment=gcp-sandbox`` and an injected ``connector``.
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    _require_local_config(config, allow_gcs_landing=allow_gcs_landing)
    if allow_gcs_landing and connector is None:
        raise IngestionConfigError(
            "GCS landing requires an injected connector; real OpenAlex payloads "
            "in the cloud need approval (DE-24, DE-05)"
        )
    if content_format != "jsonl":
        raise IngestionFormatError("Step 12 local ingestion supports jsonl only")
    objects = object_store or object_store_from_config(config)
    require_store_matches_config(config, objects, label="Works ingestion")
    limits = decode_limits or JsonlDecodeLimits()

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

    openalex = connector or OpenAlexConnector(
        sample_selection=config.sample_selection,
        content_format=content_format,
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
                    persistence_backend=backend,
                )
            if len(selected) > config.sample_selection.max_files:
                selected = selected[: config.sample_selection.max_files]
            asset = selected[0]
            if not isinstance(asset, OpenAlexAssetMetadata):
                raise IngestionFormatError("selected asset must be OpenAlexAssetMetadata")
            if asset.content_format != "jsonl":
                raise IngestionFormatError("selected asset must be jsonl")

            result = ingest_works_asset(
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
                decode_limits=limits,
                backend=backend,
                postgres_connection=connection,
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
                | RestoreRequiredError
                | IdempotencyConflictError,
            ):
                raise
            raise IngestionError(message) from error
    finally:
        if owns_connection and connection is not None:
            connection.close()


def ingest_works_asset(
    *,
    asset: OpenAlexAssetMetadata,
    run: PipelineRun,
    config: PlatformConfig,
    control: ControlStore,
    canonical: CanonicalStore,
    objects: ObjectStore,
    connector: OpenAlexConnector,
    worker_id: str = DEFAULT_WORKER_ID,
    lease_ttl: timedelta = DEFAULT_LEASE,
    clock: Callable[[], datetime] | None = None,
    decode_limits: JsonlDecodeLimits | None = None,
    backend: PersistenceBackend = "memory",
    postgres_connection: Any | None = None,
) -> tuple[SourceFileControl, FileIngestStats | None, str | None]:
    """Reusable Stage: land + canonicalize one Works asset under an existing run.

    Does not create or finish ``PipelineRun``. Callers (Step 12 wrapper or
    Step 19 outer runner) own run lifecycle. Uses ``run.run_id`` for control,
    landing provenance, and record lineage.
    """
    require_store_matches_config(config, objects, label="Works ingestion")
    if clock is None:
        clock = lambda: datetime.now(tz=UTC)  # noqa: E731
    if decode_limits is None:
        decode_limits = JsonlDecodeLimits()
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
        )
    if outcome is RegistrationOutcome.ALREADY_IN_PROGRESS:
        try:
            control.recover_stale_claim(asset.asset_id, now=clock())
        except ClaimConflictError:
            raise ClaimConflictError(
                "source file already claimed by another worker"
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

    raw_key = openalex_raw_object_key(asset)
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
            temp_prefix="oa-ingest-",
            temp_suffix=".gz",
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
        publish_request = AssetPublishRequest(
            asset_id=asset.asset_id,
            claim_token=claim_token,
            now=clock(),
            processed_at=processed_at,
            raw_object_key=raw_key,
            source_checksum_sha256=checksum,
            retrieval_provenance=retrieval_provenance,
            open_records=_record_stream_factory(
                asset=asset,
                run_id=run.run_id,
                checksum=checksum,
                objects=objects,
                raw_key=raw_key,
                processed_at=processed_at,
                decode_limits=decode_limits,
            ),
        )
        if backend == "postgres":
            from research_platform.persistence.postgres import publish_claimed_asset

            assert postgres_connection is not None
            published = publish_claimed_asset(postgres_connection, publish_request)
        else:
            assert isinstance(control, InMemoryControlStore)
            assert isinstance(canonical, InMemoryCanonicalStore)
            published = publish_claimed_asset_memory(control, canonical, publish_request)

        stats = _stats_from_counters(
            asset_id=asset.asset_id,
            counters=published.counters,
            canonical=canonical,
        )
        return published.source_file, stats, None
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
        del failed
        if isinstance(
            error,
            IngestionError
            | ControlError
            | ObjectStoreError
            | CanonicalConflictError
            | RestoreRequiredError
            | ChecksumConflictError
            | IdempotencyConflictError,
        ):
            raise
        raise IngestionError(message) from error


def _record_stream_factory(
    *,
    asset: OpenAlexAssetMetadata,
    run_id: UUID,
    checksum: str,
    objects: ObjectStore,
    raw_key: str,
    processed_at: datetime,
    decode_limits: JsonlDecodeLimits,
) -> Callable[[], Iterator[StreamedWorkRecord]]:
    """Return a factory that opens raw once and yields mapped records incrementally.

    Invoked inside the one-asset publication transaction. Does not collect every
    ``CanonicalWorkBundle`` / ``RecordProvenance`` for the file.
    """

    def open_records() -> Iterator[StreamedWorkRecord]:
        with objects.open(raw_key) as handle:
            for record in iter_jsonl_gz_records(handle, limits=decode_limits):
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
                provenance = RecordProvenance.model_validate(
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
                yield StreamedWorkRecord(bundle=bundle, provenance=provenance)

    return open_records


def _stats_from_counters(
    *,
    asset_id: str,
    counters: PublishCounters,
    canonical: CanonicalStore,
) -> FileIngestStats:
    return FileIngestStats(
        asset_id=asset_id,
        works_inserted=counters.works_inserted,
        works_identical=counters.works_identical,
        works_replaced=counters.works_replaced,
        works_stale=counters.works_stale,
        record_provenance_count=counters.record_provenance_count,
        relationship_counts=canonical.relationship_counts(),
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
            raise IngestionConfigError("local Works ingestion requires environment=local")
        if config.storage.backend != "local":
            raise IngestionConfigError("local Works ingestion requires storage.backend=local")
    if config.sample_selection.max_files != 1:
        raise IngestionConfigError(
            "default local ingestion requires sample_selection.max_files=1"
        )
    if config.sample_selection.max_file_size_bytes > 25_000_000:
        raise IngestionConfigError(
            "default local ingestion requires max_file_size_bytes <= 25000000"
        )


def _classify_failure(error: BaseException) -> tuple[FailureCategory, str]:
    if isinstance(error, ChecksumConflictError | ObjectConflictError):
        return FailureCategory.CHECKSUM, "source checksum or landing conflict"
    if isinstance(error, ClaimConflictError | StaleClaimError):
        return FailureCategory.CLAIM, "claim conflict"
    if isinstance(error, IdempotencyConflictError):
        return FailureCategory.VALIDATION, "source identity metadata conflict"
    if isinstance(error, RestoreRequiredError | CanonicalConflictError):
        return FailureCategory.CANONICAL, "canonical version or entity conflict"
    if isinstance(error, IngestionDecodeError):
        return FailureCategory.CANONICAL, "source decode failed"
    if isinstance(error, IngestionFormatError):
        return FailureCategory.VALIDATION, "unsupported or invalid source format"
    if isinstance(error, IngestionConfigError):
        return FailureCategory.VALIDATION, "invalid local ingestion configuration"
    if isinstance(error, ObjectStoreError):
        return FailureCategory.LANDING, "immutable landing failed"
    typed = classify_retrieval_or_landing(error)
    if typed is not None:
        return typed
    name = type(error).__name__
    if "Size" in name or "Network" in name or "Timeout" in name or "Access" in name:
        return FailureCategory.RETRIEVAL, "source retrieval failed"
    return FailureCategory.UNKNOWN, "ingestion failed"
