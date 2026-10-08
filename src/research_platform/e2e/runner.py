"""Bounded end-to-end pipeline runner (Step 19 / #27)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.models import CanonicalActivityState
from research_platform.config.loader import load_config
from research_platform.config.models import PlatformConfig
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import (
    FailureCategory,
    PipelineRun,
    PipelineRunStatus,
)
from research_platform.control.store import ControlStore
from research_platform.e2e.analytical import (
    open_analytical_connection,
    project_canonical_to_duckdb,
)
from research_platform.e2e.gold import build_staged_gold, consumer_repository_from_gold
from research_platform.e2e.models import (
    PIPELINE_NAME,
    STAGE_ORDER,
    EndToEndBounds,
    EndToEndResult,
    FinalValidationReport,
    StageName,
    StageResult,
    StageStatus,
)
from research_platform.e2e.publication import (
    InMemoryPublicationStore,
    PublicationSnapshot,
    PublicationStore,
)
from research_platform.e2e.summaries import safe_exception_message
from research_platform.ingestion.deletion_pipeline import ingest_deletion_asset
from research_platform.ingestion.pipeline import FileIngestStats, ingest_works_asset
from research_platform.quality.models import (
    ExecutionStage,
    ReconciliationExpectation,
)
from research_platform.quality.runner import DuckDBQualityExecutor, run_quality_checks
from research_platform.service.contracts import (
    PageRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import ServiceErrorCode, ServiceErrorException
from research_platform.service.service import DataService
from research_platform.sources.openalex.deletion_metadata import (
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.storage.base import ObjectStore
from research_platform.storage.local import LocalObjectStore


class _Discoverable(Protocol):
    def discover_metadata(self) -> object: ...

    def fetch(self, asset: object) -> BinaryIO: ...


class _Fetchable(Protocol):
    def fetch(self, asset: object) -> BinaryIO: ...


def run_bounded_e2e_pipeline(
    config_path: Path | str,
    *,
    works_connector: _Discoverable,
    deletion_asset: OpenAlexDeletionAssetMetadata | None = None,
    deletion_connector: _Fetchable | None = None,
    bounds: EndToEndBounds | None = None,
    control_store: ControlStore | None = None,
    canonical_store: InMemoryCanonicalStore | None = None,
    object_store: ObjectStore | None = None,
    publication_store: PublicationStore | None = None,
    now: Callable[[], datetime] | None = None,
    worker_id: str = "e2e-worker",
    lease_ttl: timedelta = timedelta(minutes=15),
    force_pre_serving_fail: bool = False,
    force_pre_visible_fail: bool = False,
    force_final_validation_fail: bool = False,
) -> EndToEndResult:
    """Execute the full Step-19 stage sequence under one outer PipelineRun.

    Memory/SEMANTIC_ONLY local path. Does not deploy BigQuery or claim MEASURED.
    """
    clock = now or (lambda: datetime.now(tz=UTC))
    config = load_config(Path(config_path))
    e2e_bounds = bounds or EndToEndBounds()
    _assert_config_within_bounds(config, e2e_bounds)

    control = control_store or InMemoryControlStore()
    canonical = canonical_store or InMemoryCanonicalStore()
    objects = object_store or LocalObjectStore(config.storage)
    publications: PublicationStore = publication_store or InMemoryPublicationStore()

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
    stages: list[StageResult] = []
    pre_serving = None
    pre_visible = None
    final_validation = None
    publication_version: str | None = None
    works_stats: FileIngestStats | None = None
    conn = None

    def _append(stage: StageName, status: StageStatus, message: str = "", **details: Any) -> None:
        stages.append(
            StageResult(stage=stage, status=status, message=message, details=details)
        )

    def _block_remaining_from(start: StageName, reason: str) -> None:
        started_blocking = False
        for name in STAGE_ORDER:
            if name is start:
                started_blocking = True
            if not started_blocking:
                continue
            if any(s.stage is name for s in stages):
                continue
            _append(name, StageStatus.BLOCKED, reason)

    try:
        # DISCOVER
        selection = works_connector.discover_metadata()
        selected = tuple(getattr(selection, "selected", ()))
        _append(
            StageName.DISCOVER,
            StageStatus.SUCCESS,
            f"discovered={len(getattr(selection, 'selected', ()) or ())}",
        )

        # SELECT
        if not selected:
            _append(StageName.SELECT, StageStatus.FAILED, "no eligible works asset")
            _block_remaining_from(StageName.INGEST, "select failed")
            return _finish_failed(
                control, run, stages, clock, "no eligible works asset",
                pre_serving, pre_visible, final_validation, publication_version,
            )
        if len(selected) > e2e_bounds.max_files:
            selected = selected[: e2e_bounds.max_files]
        asset = selected[0]
        if not isinstance(asset, OpenAlexAssetMetadata):
            raise ValueError("selected asset must be OpenAlexAssetMetadata")
        if (
            asset.byte_size is not None
            and asset.byte_size > e2e_bounds.max_file_size_bytes
        ):
            _append(
                StageName.SELECT,
                StageStatus.FAILED,
                "declared asset size exceeds max_file_size_bytes",
            )
            _block_remaining_from(StageName.INGEST, "oversized asset")
            return _finish_failed(
                control, run, stages, clock, "oversized asset",
                pre_serving, pre_visible, final_validation, publication_version,
            )
        _append(
            StageName.SELECT,
            StageStatus.SUCCESS,
            asset.asset_id,
            asset_id=asset.asset_id,
        )

        # INGEST + IMMUTABLE_LANDING + CANONICALIZE (single Stage-12 reusable call)
        try:
            source_file, works_stats, skipped = ingest_works_asset(
                asset=asset,
                run=run,
                config=config,
                control=control,
                canonical=canonical,
                objects=objects,
                connector=works_connector,  # type: ignore[arg-type]
                worker_id=worker_id,
                lease_ttl=lease_ttl,
                clock=clock,
                backend="memory",
            )
        except Exception as exc:
            msg = safe_exception_message(exc)
            _append(StageName.INGEST, StageStatus.FAILED, msg)
            _append(StageName.IMMUTABLE_LANDING, StageStatus.FAILED, msg)
            _append(StageName.CANONICALIZE, StageStatus.FAILED, msg)
            _block_remaining_from(StageName.APPLY_DELETIONS, msg)
            return _finish_failed(
                control, run, stages, clock, msg,
                pre_serving, pre_visible, final_validation, publication_version,
            )

        if skipped == "ALREADY_SUCCESS":
            _append(StageName.INGEST, StageStatus.SUCCESS, "replay ALREADY_SUCCESS")
            _append(
                StageName.IMMUTABLE_LANDING,
                StageStatus.SUCCESS,
                "historical landing preserved",
            )
            _append(
                StageName.CANONICALIZE,
                StageStatus.SUCCESS,
                "historical canonical preserved",
            )
        else:
            _append(
                StageName.INGEST,
                StageStatus.SUCCESS,
                source_file.asset_id,
                run_id=str(run.run_id),
            )
            _append(
                StageName.IMMUTABLE_LANDING,
                StageStatus.SUCCESS,
                source_file.raw_object_key or "",
            )
            _append(
                StageName.CANONICALIZE,
                StageStatus.SUCCESS,
                f"works={canonical.work_count()}",
            )

        # APPLY_DELETIONS
        if deletion_asset is None:
            _append(StageName.APPLY_DELETIONS, StageStatus.SKIPPED, "no deletion asset")
        else:
            if deletion_connector is None:
                raise ValueError("deletion_connector required when deletion_asset set")
            if (
                deletion_asset.byte_size is not None
                and deletion_asset.byte_size > e2e_bounds.max_file_size_bytes
            ):
                _append(
                    StageName.APPLY_DELETIONS,
                    StageStatus.FAILED,
                    "deletion asset exceeds max_file_size_bytes",
                )
                _block_remaining_from(StageName.ANALYTICAL_MODELS, "oversized deletion")
                return _finish_failed(
                    control, run, stages, clock, "oversized deletion",
                    pre_serving, pre_visible, final_validation, publication_version,
                )
            try:
                del_file, _del_stats, del_skipped = ingest_deletion_asset(
                    asset=deletion_asset,
                    run=run,
                    config=config,
                    control=control,
                    canonical=canonical,
                    objects=objects,
                    connector=deletion_connector,
                    worker_id=worker_id,
                    lease_ttl=lease_ttl,
                    clock=clock,
                    backend="memory",
                )
                _append(
                    StageName.APPLY_DELETIONS,
                    StageStatus.SUCCESS,
                    del_skipped or del_file.asset_id,
                    shared_run_id=str(run.run_id),
                )
            except Exception as exc:
                msg = safe_exception_message(exc)
                _append(StageName.APPLY_DELETIONS, StageStatus.FAILED, msg)
                _block_remaining_from(StageName.ANALYTICAL_MODELS, msg)
                return _finish_failed(
                    control, run, stages, clock, msg,
                    pre_serving, pre_visible, final_validation, publication_version,
                )

        # ANALYTICAL_MODELS
        snapshot = canonical.snapshot()
        conn = open_analytical_connection()
        counts = project_canonical_to_duckdb(conn, snapshot)
        _append(
            StageName.ANALYTICAL_MODELS,
            StageStatus.SUCCESS,
            "SEMANTIC_ONLY duckdb projection",
            **{k: v for k, v in counts.items()},
        )

        # PRE_SERVING_QUALITY
        expectation = _expectation_from_stats(works_stats, snapshot_work_count=len(snapshot.works))
        if force_pre_serving_fail:
            expectation = ReconciliationExpectation(
                source_records_seen=10,
                source_records_decoded=10,
                records_mapped_successfully=1,
                records_rejected=0,  # imbalance → FAIL
                unique_work_ids_evaluated=1,
                inserted=1,
                updated=0,
                identical=0,
                stale=0,
                conflict=0,
                restore_required=0,
            )
        pre_serving = run_quality_checks(
            DuckDBQualityExecutor(conn),
            run_id=str(run.run_id),
            stage=ExecutionStage.PRE_SERVING_BUILD,
            expectation=expectation,
        )
        if not pre_serving.publication_allowed:
            _append(
                StageName.PRE_SERVING_QUALITY,
                StageStatus.FAILED,
                "PRE_SERVING_BUILD gate failed",
            )
            _block_remaining_from(StageName.STAGED_GOLD, "quality blocked")
            return _finish_failed(
                control, run, stages, clock, "PRE_SERVING_BUILD failed",
                pre_serving, pre_visible, final_validation, publication_version,
            )
        _append(StageName.PRE_SERVING_QUALITY, StageStatus.SUCCESS, "publication_allowed")

        # STAGED_GOLD
        gold_meta = build_staged_gold(conn)
        _append(
            StageName.STAGED_GOLD,
            StageStatus.SUCCESS,
            f"marts={len(gold_meta['mart_ids'])}",
            mart_ids=list(gold_meta["mart_ids"]),
        )

        # PRE_VISIBLE_QUALITY
        if force_pre_visible_fail:
            # Inject a deleted work into contributions to fail the gate.
            deleted = next(
                (
                    w.work_id
                    for w in snapshot.works
                    if w.lineage.activity_state is CanonicalActivityState.DELETED
                ),
                None,
            )
            if deleted is not None:
                conn.execute(
                    """
                    INSERT INTO staged_gold_work_contributions VALUES
                    ('research_discovery', ?)
                    """,
                    [deleted],
                )
            else:
                conn.execute(
                    """
                    INSERT INTO staged_gold_work_contributions VALUES
                    ('research_discovery', 'W_MISSING_FORCE_FAIL')
                    """
                )
        pre_visible = run_quality_checks(
            DuckDBQualityExecutor(conn),
            run_id=str(run.run_id),
            stage=ExecutionStage.PRE_VISIBLE_PUBLICATION,
            expectation=None,
        )
        if not pre_visible.publication_allowed:
            _append(
                StageName.PRE_VISIBLE_QUALITY,
                StageStatus.FAILED,
                "PRE_VISIBLE_PUBLICATION gate failed",
            )
            _block_remaining_from(StageName.CONSUMER_PUBLICATION, "quality blocked")
            return _finish_failed(
                control, run, stages, clock, "PRE_VISIBLE_PUBLICATION failed",
                pre_serving, pre_visible, final_validation, publication_version,
            )
        _append(StageName.PRE_VISIBLE_QUALITY, StageStatus.SUCCESS, "publication_allowed")

        # CONSUMER_PUBLICATION
        deleted_ids = frozenset(
            w.work_id
            for w in snapshot.works
            if w.lineage.activity_state is CanonicalActivityState.DELETED
        )
        repo = consumer_repository_from_gold(
            conn,
            run_id=str(run.run_id),
            deleted_work_ids=deleted_ids,
            published=True,
        )
        fingerprint = _content_fingerprint(repo)
        publication_version = f"pub-{run.run_id}"
        snap = PublicationSnapshot(
            publication_version=publication_version,
            run_id=str(run.run_id),
            repository=repo,
            content_fingerprint=fingerprint,
        )
        try:
            publications.stage(snap)
            publications.activate(publication_version)
        except Exception as exc:
            msg = safe_exception_message(exc)
            _append(StageName.CONSUMER_PUBLICATION, StageStatus.FAILED, msg)
            _block_remaining_from(StageName.FINAL_VALIDATION, msg)
            return _finish_failed(
                control, run, stages, clock, msg,
                pre_serving, pre_visible, final_validation, publication_version,
            )
        _append(
            StageName.CONSUMER_PUBLICATION,
            StageStatus.SUCCESS,
            publication_version,
        )

        # FINAL_VALIDATION
        current = publications.current()
        final_validation = _run_final_validation(
            current,
            expected_run_id=str(run.run_id),
            expected_version=publication_version,
            force_fail=force_final_validation_fail,
        )
        if not final_validation.ok:
            _append(
                StageName.FINAL_VALIDATION,
                StageStatus.FAILED,
                "; ".join(final_validation.errors),
            )
            return _finish_failed(
                control, run, stages, clock, "final validation failed",
                pre_serving, pre_visible, final_validation, publication_version,
            )
        _append(StageName.FINAL_VALIDATION, StageStatus.SUCCESS, "ok")

        finished = control.finish_pipeline_run(
            run.run_id,
            status=PipelineRunStatus.SUCCESS,
            completed_at=clock(),
        )
        return EndToEndResult(
            run=finished,
            stages=tuple(stages),
            publication_version=publication_version,
            pre_serving_report=pre_serving,
            pre_visible_report=pre_visible,
            final_validation=final_validation,
        )
    except Exception as exc:
        msg = safe_exception_message(exc)
        if not stages:
            _append(StageName.DISCOVER, StageStatus.FAILED, msg)
        _block_remaining_from(StageName.SELECT, msg)
        return _finish_failed(
            control, run, stages, clock, msg,
            pre_serving, pre_visible, final_validation, publication_version,
        )
    finally:
        if conn is not None:
            conn.close()


def _finish_failed(
    control: ControlStore,
    run: PipelineRun,
    stages: list[StageResult],
    clock: Callable[[], datetime],
    message: str,
    pre_serving: Any,
    pre_visible: Any,
    final_validation: Any,
    publication_version: str | None,
) -> EndToEndResult:
    try:
        finished = control.finish_pipeline_run(
            run.run_id,
            status=PipelineRunStatus.FAILED,
            completed_at=clock(),
            failure_category=FailureCategory.VALIDATION,
            failure_message=message[:512],
        )
    except Exception:
        finished = run
    return EndToEndResult(
        run=finished,
        stages=tuple(stages),
        publication_version=publication_version,
        pre_serving_report=pre_serving,
        pre_visible_report=pre_visible,
        final_validation=final_validation,
        safe_error=message,
    )


def _assert_config_within_bounds(config: PlatformConfig, bounds: EndToEndBounds) -> None:
    if config.sample_selection.max_files > bounds.max_files:
        raise ValueError("config max_files exceeds EndToEndBounds")
    if config.sample_selection.max_file_size_bytes > bounds.max_file_size_bytes:
        raise ValueError("config max_file_size_bytes exceeds EndToEndBounds")


def _expectation_from_stats(
    stats: FileIngestStats | None,
    *,
    snapshot_work_count: int,
) -> ReconciliationExpectation:
    if stats is None:
        # Replay / empty publish — balanced zeros relative to current store.
        n = snapshot_work_count
        return ReconciliationExpectation(
            source_records_seen=n,
            source_records_decoded=n,
            records_mapped_successfully=n,
            records_rejected=0,
            unique_work_ids_evaluated=n,
            inserted=0,
            updated=0,
            identical=n,
            stale=0,
            conflict=0,
            restore_required=0,
        )
    inserted = stats.works_inserted
    updated = stats.works_replaced
    identical = stats.works_identical
    stale = stats.works_stale
    unique = inserted + updated + identical + stale
    seen = stats.record_provenance_count
    return ReconciliationExpectation(
        source_records_seen=seen,
        source_records_decoded=seen,
        records_mapped_successfully=unique,
        records_rejected=max(0, seen - unique),
        unique_work_ids_evaluated=unique,
        inserted=inserted,
        updated=updated,
        identical=identical,
        stale=stale,
        conflict=0,
        restore_required=0,
    )


def _content_fingerprint(repo: Any) -> str:
    works = getattr(repo, "_works", ())
    journals = getattr(repo, "_journal", {})
    publishers = getattr(repo, "_publishers", {})
    topics = getattr(repo, "_publisher_topics", ())
    payload = {
        "works": [w.model_dump(mode="json") for w in works],
        "journals": {
            k: v.model_dump(mode="json") for k, v in sorted(journals.items())
        },
        "publishers": {
            k: v.model_dump(mode="json") for k, v in sorted(publishers.items())
        },
        "topics": [t.model_dump(mode="json") for t in topics],
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run_final_validation(
    current: PublicationSnapshot | None,
    *,
    expected_run_id: str,
    expected_version: str,
    force_fail: bool = False,
) -> FinalValidationReport:
    errors: list[str] = []
    if current is None:
        return FinalValidationReport(ok=False, errors=("no current publication",))
    if current.publication_version != expected_version:
        errors.append("publication version mismatch")
    if current.run_id != expected_run_id:
        errors.append("publication run_id mismatch")

    svc = DataService(current.repository)
    page = svc.search_research(ResearchDiscoveryRequest(page=PageRequest(limit=50)))
    if page.freshness.as_of_run_id != expected_run_id:
        errors.append("freshness.as_of_run_id mismatch")
    active_ids = {w.work_id for w in page.data.items}
    for wid in current.repository._deleted_work_ids:  # noqa: SLF001
        if wid in active_ids:
            errors.append(f"deleted work {wid} exposed in discovery")
        try:
            svc.get_work(WorkMetadataRequest(work_id=wid))
            errors.append(f"deleted work {wid} returned by metadata lookup")
        except ServiceErrorException as exc:
            if exc.error.code is not ServiceErrorCode.NOT_FOUND:
                errors.append(f"deleted work {wid} unexpected error {exc.error.code}")

    # Pagination contract smoke
    if page.data.page.has_more and not page.data.page.next_cursor:
        errors.append("has_more without next_cursor")

    if force_fail:
        errors.append("forced final validation failure")

    return FinalValidationReport(
        ok=not errors,
        publication_version=current.publication_version,
        as_of_run_id=page.freshness.as_of_run_id,
        active_work_count=len(page.data.items),
        errors=tuple(errors),
    )
