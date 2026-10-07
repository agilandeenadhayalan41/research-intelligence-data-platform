"""One-file idempotent local OpenAlex Works ingestion (Step 12 / #20)."""

from __future__ import annotations

import hashlib
import tempfile
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
from research_platform.storage.local import LocalObjectStore
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
) -> LocalWorksIngestResult:
    """Run one bounded local Works ingestion against an explicitly selected config.

    Persistence:

    - ``backend="memory"``: ephemeral in-process stores (tests/demo only)
    - ``backend="postgres"``: durable local PostgreSQL ControlStore/CanonicalStore
      behind one asset publication transaction (not ``Warehouse.query``)

    After immutable raw landing, publication is:

    ``BEGIN → re-check claim → upsert canonical → provenance → SUCCESS → COMMIT``

    Raw ObjectStore bytes are outside that DB transaction and may remain after rollback.
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    _require_local_config(config)
    if content_format != "jsonl":
        raise IngestionFormatError("Step 12 local ingestion supports jsonl only")
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

    objects = object_store or LocalObjectStore(config.storage)
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
                | IdempotencyConflictError,
            ):
                raise
            raise IngestionError(message) from error
    finally:
        if owns_connection and connection is not None:
            connection.close()


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
    decode_limits: JsonlDecodeLimits,
    backend: PersistenceBackend,
    postgres_connection: Any | None,
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
            | ChecksumConflictError
            | IdempotencyConflictError,
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
            # ObjectStore treats differing retrieval provenance (e.g. new run_id) as
            # conflict even when bytes match. For FAILED→retry recovery, accept an
            # already-landed immutable object whose content checksum matches.
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


def _require_local_config(config: PlatformConfig) -> None:
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
