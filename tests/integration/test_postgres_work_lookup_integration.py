"""Separately invoked tests for the #23 PROTOTYPE work lookup projection.

Run via ``make test-postgres-serving`` when ``POSTGRES_DSN`` points at a local
PostgreSQL. Default ``make test`` excludes this module (``not postgres``). Each
test uses a fresh database schema search path so it never touches other data.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

pytest.importorskip("psycopg")

import psycopg

from research_platform.persistence.postgres.connection import connect_postgres
from research_platform.service.contracts import WorkMetadataRequest
from research_platform.service.models import (
    FreshnessMetadata,
    FreshnessStatus,
    ServiceErrorCode,
    ServiceErrorException,
    WorkMetadataRecord,
)
from research_platform.service.repository import (
    InMemoryConsumerRepository,
    build_reference_fixture,
)
from research_platform.serving.postgres_work_lookup import (
    PostgresWorkLookupProjection,
    PostgresWorkLookupRepository,
    apply_work_lookup_schema,
)

DSN = os.environ.get("POSTGRES_DSN", "").strip()
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="POSTGRES_DSN unset; skipping PostgreSQL tests"),
]

_PAYLOAD = "10.1000/x'; DROP TABLE serving.work_lookup; --"


@pytest.fixture
def connection():
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute("DROP SCHEMA IF EXISTS serving CASCADE")
    conn = connect_postgres(DSN)
    try:
        yield conn
    finally:
        conn.close()
        admin.execute("DROP SCHEMA IF EXISTS serving CASCADE")
        admin.close()


def by_id(work_id: str) -> WorkMetadataRequest:
    return WorkMetadataRequest(work_id=work_id)


def by_doi(doi: str) -> WorkMetadataRequest:
    return WorkMetadataRequest(doi=doi)


def with_deleted_and_shared_doi() -> InMemoryConsumerRepository:
    base = build_reference_fixture().snapshot()
    extra = (
        WorkMetadataRecord(work_id="W900", doi="10.1000/aaa", title="Deleted twin"),
        WorkMetadataRecord(work_id="W0005", doi="10.1000/shared", title="Lower id"),
        WorkMetadataRecord(work_id="W0009", doi="10.1000/shared", title="Higher id"),
    )
    return InMemoryConsumerRepository(
        works=base.works + extra,
        deleted_work_ids=base.deleted_work_ids | {"W900"},
        freshness=base.freshness,
    )


def test_unpublished_projection_is_refused(connection) -> None:
    apply_work_lookup_schema(connection)
    repo = PostgresWorkLookupRepository(connection)
    for call in (repo.freshness, lambda: repo.get_work_metadata(by_id("W001"))):
        with pytest.raises(ServiceErrorException) as info:
            call()
        assert info.value.error.code is ServiceErrorCode.QUALITY_NOT_PUBLISHED


def test_lookups_match_the_in_memory_reference(connection) -> None:
    reference = build_reference_fixture()
    apply_work_lookup_schema(connection)
    result = PostgresWorkLookupProjection(connection).publish(reference.snapshot())
    repo = PostgresWorkLookupRepository(connection)
    works = reference.snapshot().works
    assert result.changed and result.row_count == len(works)
    for work in works:
        assert repo.get_work_metadata(by_id(work.work_id)) == reference.get_work_metadata(by_id(work.work_id))
        if work.doi:
            assert repo.get_work_metadata(by_doi(work.doi)) == reference.get_work_metadata(by_doi(work.doi))
    assert repo.get_work_metadata(by_id("W404")) is None
    assert repo.freshness() == reference.freshness()


def test_deleted_works_are_absent_and_shared_doi_takes_the_lowest_id(connection) -> None:
    apply_work_lookup_schema(connection)
    PostgresWorkLookupProjection(connection).publish(with_deleted_and_shared_doi().snapshot())
    repo = PostgresWorkLookupRepository(connection)
    assert repo.get_work_metadata(by_id("W900")) is None
    assert repo.get_work_metadata(by_doi("10.1000/aaa")).work_id == "W001"  # the ACTIVE one
    assert repo.get_work_metadata(by_doi("10.1000/shared")).work_id == "W0005"


def test_identical_publish_is_a_no_op_and_deletion_removes_the_row(connection) -> None:
    apply_work_lookup_schema(connection)
    projection = PostgresWorkLookupProjection(connection)
    snap = build_reference_fixture().snapshot()
    first = projection.publish(snap)
    again = projection.publish(snap)
    assert not again.changed and again.rows_fingerprint == first.rows_fingerprint

    projection.publish(dataclasses.replace(snap, deleted_work_ids=snap.deleted_work_ids | {"W001"}))
    repo = PostgresWorkLookupRepository(connection)
    assert repo.get_work_metadata(by_id("W001")) is None
    assert repo.get_work_metadata(by_doi("10.1000/aaa")) is None


def test_freshness_only_change_updates_state_without_rewriting_rows(connection) -> None:
    apply_work_lookup_schema(connection)
    projection = PostgresWorkLookupProjection(connection)
    snap = build_reference_fixture().snapshot()
    projection.publish(snap)
    newer = FreshnessMetadata(status=FreshnessStatus.KNOWN, generated_at="2026-10-12T00:00:00Z", as_of_run_id="run-2")
    result = projection.publish(dataclasses.replace(snap, freshness=newer))
    assert result.changed
    assert PostgresWorkLookupRepository(connection).freshness() == newer


def test_failed_publish_rolls_back_to_the_previous_snapshot(connection) -> None:
    apply_work_lookup_schema(connection)
    projection = PostgresWorkLookupProjection(connection)
    snap = build_reference_fixture().snapshot()
    projection.publish(snap)
    # PostgreSQL text cannot hold a NUL byte, so the insert fails mid-publish.
    broken = dataclasses.replace(snap, works=(*snap.works, WorkMetadataRecord(work_id="W777", title="bad\x00title")))
    with pytest.raises(psycopg.Error):
        projection.publish(broken)
    repo = PostgresWorkLookupRepository(connection)
    assert repo.get_work_metadata(by_id("W001")) is not None
    assert repo.get_work_metadata(by_id("W777")) is None


def test_readers_see_the_old_snapshot_until_commit(connection) -> None:
    apply_work_lookup_schema(connection)
    snap = build_reference_fixture().snapshot()
    PostgresWorkLookupProjection(connection).publish(snap)
    reader = connect_postgres(DSN)
    try:
        repo = PostgresWorkLookupRepository(reader)
        with connection.transaction():
            connection.execute("DELETE FROM serving.work_lookup")
            assert repo.get_work_metadata(by_id("W001")) is not None  # uncommitted delete is invisible
            raise psycopg.Rollback()
    finally:
        reader.close()


def test_lookup_parameters_are_never_interpolated(connection) -> None:
    apply_work_lookup_schema(connection)
    PostgresWorkLookupProjection(connection).publish(build_reference_fixture().snapshot())
    repo = PostgresWorkLookupRepository(connection)
    assert repo.get_work_metadata(by_doi(_PAYLOAD)) is None
    assert repo.get_work_metadata(by_id(_PAYLOAD)) is None
    assert repo.get_work_metadata(by_id("W001")) is not None  # table intact


def test_backend_failure_is_a_retryable_service_error(connection) -> None:
    apply_work_lookup_schema(connection)
    PostgresWorkLookupProjection(connection).publish(build_reference_fixture().snapshot())
    admin = psycopg.connect(DSN, autocommit=True)
    try:
        admin.execute("DROP TABLE serving.work_lookup")
    finally:
        admin.close()
    with pytest.raises(ServiceErrorException) as info:
        PostgresWorkLookupRepository(connection).get_work_metadata(by_id("W001"))
    assert info.value.error.code is ServiceErrorCode.BACKEND_ERROR and info.value.error.retryable
