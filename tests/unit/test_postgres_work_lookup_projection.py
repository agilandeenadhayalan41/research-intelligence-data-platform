"""Offline checks for the #23 PROTOTYPE work lookup projection (no PostgreSQL)."""

from __future__ import annotations

import dataclasses

import pytest

from research_platform.service.models import (
    ServiceErrorCode,
    ServiceErrorException,
    WorkMetadataRecord,
)
from research_platform.service.repository import build_reference_fixture
from research_platform.serving import postgres_work_lookup as lookup


def snapshot():
    return build_reference_fixture().snapshot()


def test_rows_are_active_works_only_sorted_by_work_id() -> None:
    snap = snapshot()
    rows = lookup.projection_rows(snap)
    ids = [r[0] for r in rows]
    assert ids == sorted(ids)
    assert not set(ids) & snap.deleted_work_ids
    assert set(ids) == {w.work_id for w in snap.works} - snap.deleted_work_ids


def test_rows_round_trip_to_the_same_records() -> None:
    snap = snapshot()
    by_id = {w.work_id: w for w in snap.works}
    for row in lookup.projection_rows(snap):
        assert lookup._record(row) == by_id[row[0]]


def test_arrays_are_lists_for_the_driver() -> None:
    row = lookup.projection_rows(snapshot())[0]
    for column in lookup.ARRAY_COLUMNS:
        assert isinstance(row[lookup.COLUMNS.index(column)], list)


def test_fingerprint_is_stable_and_content_sensitive() -> None:
    rows = lookup.projection_rows(snapshot())
    assert lookup.rows_fingerprint(rows) == lookup.rows_fingerprint(tuple(rows))
    changed = (rows[0][:2] + ("another title",) + rows[0][3:], *rows[1:])
    assert lookup.rows_fingerprint(changed) != lookup.rows_fingerprint(rows)


def test_unpublished_snapshot_is_refused() -> None:
    with pytest.raises(ValueError, match="unpublished"):
        lookup.projection_rows(dataclasses.replace(snapshot(), published=False))


def test_repeated_work_id_is_refused() -> None:
    snap = snapshot()
    dup = dataclasses.replace(snap, works=(*snap.works, WorkMetadataRecord(work_id=snap.works[0].work_id)))
    with pytest.raises(ValueError, match="repeats a work_id"):
        lookup.projection_rows(dup)


@pytest.mark.parametrize(
    "method", ["search_research", "get_journal_metrics", "get_publisher_summary", "list_publisher_topic_metrics"]
)
def test_other_capabilities_are_refused_without_touching_the_database(method: str) -> None:
    repo = lookup.PostgresWorkLookupRepository(connection=None)
    with pytest.raises(ServiceErrorException) as info:
        getattr(repo, method)(None)
    assert info.value.error.code is ServiceErrorCode.DATA_UNAVAILABLE


def test_ddl_is_selective() -> None:
    ddl = lookup.DDL_PATH.read_text(encoding="utf-8").lower()
    assert "create table if not exists serving.work_lookup (" in ddl
    for relationship in ("work_authors", "work_topics", "work_references", "work_author_institutions"):
        assert relationship not in ddl
