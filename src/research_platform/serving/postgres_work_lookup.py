"""#23 PROTOTYPE: selective PostgreSQL/AlloyDB projection for Work point lookups.

**The #23 gate was not met, and #23 was closed not_planned (2026-10-13).** #119
recorded an operator-stated 200 ms p95 for the lookups (not a measured or
product-validated SLA). #118 timed BigQuery + an in-process cache against it,
in a personal DEV project on synthetic data: cold runs missed it, prewarmed
runs met it, and the operator chose the cache path. So this stays a local
prototype only: it does not change any registry placement, is not an approved
serving store, and is never deployed. See
``docs/architecture/serving-work-lookup-projection.md``.

Scope is the two lookup patterns ``doi-work-lookup`` and
``openalex-work-id-lookup``, behind the existing ``ConsumerDataRepository``
``get_work_metadata`` capability. Every other capability is refused: analytics
stay BigQuery-first and nothing here is a prerequisite for them.

- **Source / lineage:** a published ``ConsumerRepositorySnapshot`` (storage
  independent). The projection never reads BigQuery or canonical tables.
- **Grain:** one row per ACTIVE Work; DELETED Works are left out.
- **Refresh:** ``publish`` replaces the whole projection in one transaction, so
  readers see the previous snapshot until commit. An identical snapshot (same
  rows fingerprint and freshness) is a no-op.
- **Lookups:** parameterized only; exact ``work_id`` or exact DOI (no DOI
  canonicalization beyond the request contract). A DOI shared by several
  ACTIVE Works resolves to the lowest ``work_id``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from research_platform.service.contracts import (
    JournalMetricsRequest,
    PublisherSummaryRequest,
    PublisherTopicAnalyticsRequest,
    ResearchDiscoveryRequest,
    WorkMetadataRequest,
)
from research_platform.service.models import (
    FreshnessMetadata,
    FreshnessStatus,
    JournalMetricsRecord,
    PublisherSummaryRecord,
    PublisherTopicMetricRecord,
    ServiceError,
    ServiceErrorCode,
    ServiceErrorException,
    WorkMetadataRecord,
)
from research_platform.service.repository import ConsumerRepositorySnapshot

DDL_PATH = Path(__file__).resolve().parents[3] / "sql" / "serving" / "postgres" / "001_work_lookup.sql"
CAPABILITY_ID = "work_metadata"
COLUMNS = (
    "work_id",
    "doi",
    "title",
    "publication_year",
    "publication_date",
    "work_type",
    "language",
    "is_oa",
    "oa_status",
    "primary_source_id",
    "primary_publisher_id",
    "author_ids",
    "topic_ids",
    "institution_ids",
)
ARRAY_COLUMNS = frozenset({"author_ids", "topic_ids", "institution_ids"})
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM serving.work_lookup"
_INSERT = f"INSERT INTO serving.work_lookup ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})"
_STATE = (
    "SELECT rows_fingerprint, row_count, freshness_status, generated_at, source_max_updated_at, "
    "as_of_run_id, freshness_notes FROM serving.work_lookup_state"
)


@dataclass(frozen=True)
class PublishResult:
    changed: bool
    row_count: int
    rows_fingerprint: str


def projection_rows(snapshot: ConsumerRepositorySnapshot) -> tuple[tuple[Any, ...], ...]:
    """The projection's rows for ``snapshot``: ACTIVE Works only, sorted by work_id.

    Refuses an unpublished snapshot, and a snapshot that repeats a work_id
    (the projection's primary key).
    """
    if not snapshot.published:
        raise ValueError("refusing to project an unpublished snapshot")
    active = [w for w in snapshot.works if w.work_id not in snapshot.deleted_work_ids]
    ids = [w.work_id for w in active]
    if len(ids) != len(set(ids)):
        raise ValueError("snapshot repeats a work_id; the projection is one row per Work")
    return tuple(_row(w) for w in sorted(active, key=lambda w: w.work_id))


def rows_fingerprint(rows: tuple[tuple[Any, ...], ...]) -> str:
    payload = json.dumps([list(r) for r in rows], sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _row(work: WorkMetadataRecord) -> tuple[Any, ...]:
    data = work.model_dump()
    return tuple(list(data[c]) if c in ARRAY_COLUMNS else data[c] for c in COLUMNS)


def _record(row: tuple[Any, ...]) -> WorkMetadataRecord:
    data = dict(zip(COLUMNS, row, strict=True))
    for column in ARRAY_COLUMNS:
        data[column] = tuple(data[column] or ())
    return WorkMetadataRecord(**data)


def apply_work_lookup_schema(connection: Any) -> None:
    """Create the ``serving`` schema, projection and state tables (idempotent)."""
    with connection.transaction():
        connection.execute(DDL_PATH.read_text(encoding="utf-8"))


class PostgresWorkLookupProjection:
    """Writes a published consumer snapshot into ``serving.work_lookup``."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection  # psycopg connection, autocommit=False

    def publish(self, snapshot: ConsumerRepositorySnapshot) -> PublishResult:
        rows = projection_rows(snapshot)
        fingerprint = rows_fingerprint(rows)
        fresh = snapshot.freshness
        state = (fresh.status.value, fresh.generated_at, fresh.source_max_updated_at, fresh.as_of_run_id, fresh.notes)
        with self._connection.transaction():
            # Serialize publishers; readers are not blocked (MVCC).
            self._connection.execute("LOCK TABLE serving.work_lookup_state IN EXCLUSIVE MODE")
            current = self._connection.execute(_STATE).fetchone()
            if current is not None and current[0] == fingerprint and tuple(current[2:]) == state:
                return PublishResult(changed=False, row_count=len(rows), rows_fingerprint=fingerprint)
            if current is None or current[0] != fingerprint:
                self._connection.execute("DELETE FROM serving.work_lookup")
                with self._connection.cursor() as cur:
                    cur.executemany(_INSERT, rows)
            self._connection.execute(
                """
                INSERT INTO serving.work_lookup_state (
                    singleton, rows_fingerprint, row_count, freshness_status, generated_at,
                    source_max_updated_at, as_of_run_id, freshness_notes, published_at
                ) VALUES (true, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (singleton) DO UPDATE SET
                    rows_fingerprint = EXCLUDED.rows_fingerprint,
                    row_count = EXCLUDED.row_count,
                    freshness_status = EXCLUDED.freshness_status,
                    generated_at = EXCLUDED.generated_at,
                    source_max_updated_at = EXCLUDED.source_max_updated_at,
                    as_of_run_id = EXCLUDED.as_of_run_id,
                    freshness_notes = EXCLUDED.freshness_notes,
                    published_at = EXCLUDED.published_at
                """,
                (fingerprint, len(rows), *state, datetime.now(UTC)),
            )
        return PublishResult(changed=True, row_count=len(rows), rows_fingerprint=fingerprint)


class PostgresWorkLookupRepository:
    """``ConsumerDataRepository`` serving ``get_work_metadata`` from the projection only."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection  # psycopg connection, autocommit=False

    def freshness(self) -> FreshnessMetadata:
        state = self._read(lambda: self._connection.execute(_STATE).fetchone())
        if state is None:
            _raise(ServiceErrorCode.QUALITY_NOT_PUBLISHED, "no work lookup projection has been published", "*")
        status, generated_at, source_max_updated_at, as_of_run_id, notes = state[2:]
        return FreshnessMetadata(
            status=FreshnessStatus(status),
            generated_at=generated_at,
            source_max_updated_at=source_max_updated_at,
            as_of_run_id=as_of_run_id,
            notes=notes,
        )

    def get_work_metadata(self, request: WorkMetadataRequest) -> WorkMetadataRecord | None:
        def lookup() -> tuple[Any, ...] | None:
            if self._connection.execute("SELECT 1 FROM serving.work_lookup_state").fetchone() is None:
                return _MISSING
            if request.work_id:
                return self._connection.execute(f"{_SELECT} WHERE work_id = %s", (request.work_id.strip(),)).fetchone()
            return self._connection.execute(
                f"{_SELECT} WHERE doi = %s ORDER BY work_id LIMIT 1", (request.doi,)
            ).fetchone()

        row = self._read(lookup)
        if row is _MISSING:
            _raise(ServiceErrorCode.QUALITY_NOT_PUBLISHED, "no work lookup projection has been published", "*")
        return None if row is None else _record(row)

    def search_research(self, request: ResearchDiscoveryRequest) -> NoReturn:
        _unsupported("research_discovery")

    def get_journal_metrics(self, request: JournalMetricsRequest) -> JournalMetricsRecord | None:
        _unsupported("journal_metrics")

    def get_publisher_summary(self, request: PublisherSummaryRequest) -> PublisherSummaryRecord | None:
        _unsupported("publisher_summary")

    def list_publisher_topic_metrics(
        self, request: PublisherTopicAnalyticsRequest
    ) -> tuple[tuple[PublisherTopicMetricRecord, ...], str | None, bool]:
        _unsupported("publisher_topic_analytics")

    def _read(self, fn: Any) -> Any:
        """One read in its own transaction; driver failures become a retryable BACKEND_ERROR."""
        import psycopg

        try:
            with self._connection.transaction():
                return fn()
        except psycopg.Error as exc:
            raise ServiceErrorException(
                ServiceError(
                    code=ServiceErrorCode.BACKEND_ERROR,
                    message=f"work lookup projection read failed: {type(exc).__name__}",
                    capability_id=CAPABILITY_ID,
                    retryable=True,
                )
            ) from exc


_MISSING: Any = object()


def _unsupported(capability_id: str) -> NoReturn:
    # The message names the intended backing; no BigQuery-backed
    # ConsumerDataRepository exists yet.
    _raise(
        ServiceErrorCode.DATA_UNAVAILABLE,
        "the work lookup projection serves work_metadata only; use the BigQuery-backed repository",
        capability_id,
    )


def _raise(code: ServiceErrorCode, message: str, capability_id: str) -> NoReturn:
    raise ServiceErrorException(ServiceError(code=code, message=message, capability_id=capability_id, retryable=False))
