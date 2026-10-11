"""Offline guards for in-memory ingest at scale.

``InMemoryCanonicalStore`` nests relationship rows by work_id, the control
store indexes record provenance, and the in-memory unit of work snapshots the
canonical tables with shallow copies. These tests pin the semantics those
changes must keep: exact rollback (including a rolled-back REPLACE), the
provenance index on append and across rollbacks, REPLACE/merge row sets, rows
keyed to another work, and that no per-record step scans the whole store.

``_FlatMapOracle`` restates the pre-nesting flat-map relationship semantics so
the nested maps can be compared against them. No network or cloud calls.
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import types
import typing
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from functools import cache
from typing import Any
from uuid import UUID

import pytest
from pydantic import AwareDatetime, BaseModel

from research_platform.canonical.memory import CanonicalSnapshot, InMemoryCanonicalStore
from research_platform.canonical.openalex import (
    Author,
    CanonicalActivityState,
    CanonicalLineage,
    CanonicalWorkBundle,
    Funder,
    Institution,
    Publisher,
    Source,
    Topic,
    Work,
    WorkAuthor,
    WorkAuthorInstitution,
    WorkGrant,
    WorkKeyword,
    WorkLocation,
    WorkMesh,
    WorkReference,
    WorkTopic,
    map_openalex_work,
)
from research_platform.canonical.store import CanonicalUpsertOutcome
from research_platform.control import InMemoryControlStore
from research_platform.control.models import (
    ControlStatus,
    RecordProvenance,
    SourceFileControl,
)
from research_platform.ingestion.deletions_csv import DeletedWorkRecord
from research_platform.ingestion.errors import IngestionDecodeError
from research_platform.persistence.deletion_unit_of_work import DeletionPublishRequest
from research_platform.persistence.memory_deletion_unit_of_work import (
    publish_claimed_deletions_memory,
)
from research_platform.persistence.memory_unit_of_work import (
    _CanonicalSnapshot,
    publish_claimed_asset_memory,
)
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    AssetPublishResult,
    StreamedWorkRecord,
)
from research_platform.provenance.models import IngestionProvenance

_RUN_ID = UUID("00000000-0000-4000-8000-0000000000a1")
_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
# Each asset is a newer source version than the one before it.
_ASSET_DATES = {
    "asset-a": date(2024, 1, 1),
    "asset-b": date(2024, 2, 1),
    "asset-c": date(2024, 3, 1),
}

# Grain key of every relationship table; snapshot() sorts by the same keys.
_GRAIN: dict[str, Callable[[Any], tuple[Any, ...]]] = {
    "work_authors": lambda r: (r.work_id, r.authorship_index),
    "work_author_institutions": lambda r: (
        r.work_id,
        r.authorship_index,
        r.institution_index,
    ),
    "work_topics": lambda r: (r.work_id, r.topic_id),
    "work_keywords": lambda r: (r.work_id, r.keyword_id),
    "work_references": lambda r: (r.work_id, r.reference_index),
    "work_mesh": lambda r: (r.work_id, r.mesh_index),
    "work_locations": lambda r: (r.work_id, r.location_index),
    "work_grants": lambda r: (r.work_id, r.grant_index),
}

_STORED_MODELS = (
    Work,
    Author,
    Institution,
    Source,
    Publisher,
    Topic,
    Funder,
    WorkAuthor,
    WorkAuthorInstitution,
    WorkTopic,
    WorkKeyword,
    WorkReference,
    WorkMesh,
    WorkLocation,
    WorkGrant,
)


class _ForcedFailure(RuntimeError):
    pass


def _checksum(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode()).hexdigest()


def _lineage(asset_id: str) -> CanonicalLineage:
    return CanonicalLineage.model_validate(
        {
            "source_asset_id": asset_id,
            "source_checksum_sha256": _checksum(asset_id),
            "source_updated_date": _ASSET_DATES[asset_id],
            "run_id": _RUN_ID,
            "processed_at": _NOW,
            "activity_state": CanonicalActivityState.ACTIVE,
        }
    )


def _raw_work(number: int, rows: int) -> dict[str, Any]:
    """One OpenAlex Work with ``rows`` entries in every relationship array."""
    work_id = f"W{number}"
    return {
        "id": f"https://openalex.org/{work_id}",
        "title": f"Work {number}",
        "authorships": [
            {
                "author": {
                    "id": f"https://openalex.org/A{number * 100 + i}",
                    "display_name": f"Author {number * 100 + i}",
                },
                "institutions": [
                    {"id": f"https://openalex.org/I{i + 1}", "display_name": f"Inst {i + 1}"}
                ],
            }
            for i in range(rows)
        ],
        "topics": [
            {
                "id": f"https://openalex.org/T{i + 1}",
                "display_name": f"Topic {i + 1}",
                "score": 0.5,
            }
            for i in range(rows)
        ],
        "keywords": [
            {
                "id": f"https://openalex.org/keywords/kw-{i}",
                "display_name": f"Keyword {i}",
                "score": 0.5,
            }
            for i in range(rows)
        ],
        "referenced_works": [f"https://openalex.org/W{9000 + i}" for i in range(rows)],
        "mesh": [
            {"descriptor_ui": f"D{i:06d}", "descriptor_name": f"Descriptor {i}"}
            for i in range(rows)
        ],
        "locations": [
            {"source": None, "landing_page_url": f"https://example.org/{work_id}/{i}"}
            for i in range(rows)
        ],
        "grants": [
            {
                "funder": f"https://openalex.org/F{i + 1}",
                "funder_display_name": f"Funder {i + 1}",
                "award_id": f"AWARD-{i}",
            }
            for i in range(rows)
        ],
    }


@cache
def _bundle(number: int, rows: int, asset_id: str = "asset-a") -> CanonicalWorkBundle:
    bundle = map_openalex_work(_raw_work(number, rows), lineage=_lineage(asset_id))
    assert all(len(getattr(bundle, table)) == rows for table in _GRAIN)
    return bundle


def _rekeyed(row: Any, table: str, work_id: str, variant: int) -> Any:
    """``row`` moved to grain key ``(work_id, <variant>)`` of ``table``."""
    if table == "work_topics":
        return row.model_copy(update={"work_id": work_id, "topic_id": f"T{variant + 1}"})
    if table == "work_keywords":
        return row.model_copy(update={"work_id": work_id, "keyword_id": f"kw-{variant}"})
    field = {
        "work_authors": "authorship_index",
        "work_author_institutions": "authorship_index",
        "work_references": "reference_index",
        "work_mesh": "mesh_index",
        "work_locations": "location_index",
        "work_grants": "grant_index",
    }[table]
    return row.model_copy(update={"work_id": work_id, field: variant})


def _with_stray_rows(
    bundle: CanonicalWorkBundle, work_id: str, *, variants: Sequence[int]
) -> CanonicalWorkBundle:
    """Append rows keyed to ``work_id`` and a later duplicate of the first key.

    ``CanonicalWorkBundle`` does not require a row's work_id to equal the
    bundle's Work, and inside one bundle the last row for a key wins.
    """
    duplicate_lineage = _lineage("asset-c")
    updates: dict[str, tuple[Any, ...]] = {}
    for table in _GRAIN:
        rows = getattr(bundle, table)
        if not rows:
            continue
        stray = tuple(_rekeyed(rows[0], table, work_id, variant) for variant in variants)
        duplicate = rows[0].model_copy(update={"lineage": duplicate_lineage})
        updates[table] = (*rows, *stray, duplicate)
    return bundle.model_copy(update=updates)


def _rows_for(snapshot: CanonicalSnapshot, table: str, work_id: str) -> tuple[Any, ...]:
    return tuple(row for row in getattr(snapshot, table) if row.work_id == work_id)


def _sorted_rows(table: str, rows: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(rows, key=_GRAIN[table]))


class _FlatMapOracle:
    """Pre-nesting semantics: one flat dict per table keyed by the full grain key."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple[Any, ...], Any]] = {table: {} for table in _GRAIN}

    def apply(self, bundle: CanonicalWorkBundle, *, replace_relationships: bool) -> None:
        work_id = bundle.work.work_id
        for table, key in _GRAIN.items():
            rows = self.tables[table]
            if replace_relationships:
                rows = {k: v for k, v in rows.items() if k[0] != work_id}
            for row in getattr(bundle, table):
                rows[key(row)] = row
            self.tables[table] = rows

    def assert_matches(self, store: InMemoryCanonicalStore) -> None:
        snapshot = store.snapshot()
        for table, key in _GRAIN.items():
            expected = tuple(sorted(self.tables[table].values(), key=key))
            assert getattr(snapshot, table) == expected, table
        counts = store.relationship_counts()
        assert {table: counts[table] for table in _GRAIN} == {
            table: len(rows) for table, rows in self.tables.items()
        }


def _claim(control: InMemoryControlStore, asset_id: str, *, entity: str = "works") -> UUID:
    control.register_source_file(
        SourceFileControl.model_validate(
            {
                "asset_id": asset_id,
                "run_id": _RUN_ID,
                "source": "openalex",
                "entity": entity,
                "source_uri": f"s3://openalex/data/{entity}/{asset_id}",
                "snapshot_date": date(2024, 6, 1),
                "content_format": "csv" if entity == "works-deletions" else "jsonl",
                "status": ControlStatus.DISCOVERED,
                "attempt_count": 0,
                "created_at": _NOW,
                "updated_at": _NOW,
            }
        )
    )
    token = UUID(bytes=hashlib.sha256(asset_id.encode()).digest()[:16], version=4)
    control.claim_source_file(
        asset_id,
        run_id=_RUN_ID,
        claimed_by="test-worker",
        claim_token=token,
        claimed_at=_NOW,
        lease_expires_at=_NOW + timedelta(hours=1),
    )
    return token


def _retrieval(asset_id: str) -> IngestionProvenance:
    return IngestionProvenance.model_validate(
        {
            "run_id": _RUN_ID,
            "source": "openalex",
            "source_uri": f"s3://openalex/data/works/{asset_id}",
            "retrieved_at": _NOW,
            "sha256": _checksum(asset_id),
        }
    )


def _provenance(work_id: str, asset_id: str) -> RecordProvenance:
    return RecordProvenance.model_validate(
        {
            "record_id": work_id,
            "entity_type": "work",
            "asset_id": asset_id,
            "source_checksum_sha256": _checksum(asset_id),
            "run_id": _RUN_ID,
            "source_uri": f"s3://openalex/data/works/{asset_id}",
            "processed_at": _NOW,
        }
    )


def _publish(
    control: InMemoryControlStore,
    canonical: InMemoryCanonicalStore,
    asset_id: str,
    claim_token: UUID,
    bundles: Sequence[CanonicalWorkBundle],
    *,
    fail: bool = False,
    before_failure: Callable[[], None] | None = None,
) -> AssetPublishResult:
    """Publish ``bundles`` as one Works asset; ``fail`` raises after the last record."""

    def records() -> Iterator[StreamedWorkRecord]:
        for bundle in bundles:
            yield StreamedWorkRecord(
                bundle=bundle, provenance=_provenance(bundle.work.work_id, asset_id)
            )
        if fail:
            if before_failure is not None:
                before_failure()
            raise _ForcedFailure(f"forced failure after the last record of {asset_id}")

    return publish_claimed_asset_memory(
        control,
        canonical,
        AssetPublishRequest(
            asset_id=asset_id,
            claim_token=claim_token,
            now=_NOW,
            processed_at=_NOW,
            raw_object_key=f"openalex/works/{asset_id}.jsonl.gz",
            source_checksum_sha256=_checksum(asset_id),
            retrieval_provenance=_retrieval(asset_id),
            open_records=records,
        ),
    )


def _seeded(
    *bundles: CanonicalWorkBundle,
) -> tuple[InMemoryControlStore, InMemoryCanonicalStore]:
    """Fresh stores with ``bundles`` published as asset-a."""
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    _publish(control, canonical, "asset-a", _claim(control, "asset-a"), bundles)
    return control, canonical


def _publish_deletions(
    control: InMemoryControlStore,
    canonical: InMemoryCanonicalStore,
    asset_id: str,
    claim_token: UUID,
    records: Sequence[DeletedWorkRecord],
) -> None:
    publish_claimed_deletions_memory(
        control,
        canonical,
        DeletionPublishRequest(
            asset_id=asset_id,
            claim_token=claim_token,
            now=_NOW,
            processed_at=_NOW,
            raw_object_key=f"openalex/works-deletions/{asset_id}.csv.gz",
            source_checksum_sha256=_checksum(asset_id),
            retrieval_provenance=_retrieval(asset_id),
            source_uri=f"s3://openalex/data/works-deletions/{asset_id}",
            source_updated_date=None,
            open_records=lambda: iter(records),
        ),
    )


def _provenance_keys(control: InMemoryControlStore) -> list[tuple[str, str]]:
    return [(row.record_id, row.asset_id) for row in control.list_record_provenance()]


# ---------------------------------------------------------------------------
# Rollback.
# ---------------------------------------------------------------------------


def test_failed_replace_rolls_back_exactly_and_a_later_replace_keeps_only_new_rows() -> None:
    control, canonical = _seeded(_bundle(1, 3), _bundle(2, 2))
    before = canonical.snapshot()
    counts_before = canonical.relationship_counts()
    provenance_before = control.list_record_provenance()
    replacement = _bundle(1, 1, "asset-b")

    mid_publish: list[dict[str, int]] = []
    token = _claim(control, "asset-b")
    with pytest.raises(_ForcedFailure):
        _publish(
            control,
            canonical,
            "asset-b",
            token,
            [replacement],
            fail=True,
            before_failure=lambda: mid_publish.append(canonical.relationship_counts()),
        )
    # The failed publish had replaced W1's three rows per table with one.
    assert mid_publish == [
        {**counts_before, **{table: counts_before[table] - 2 for table in _GRAIN}}
    ]
    assert canonical.snapshot() == before
    assert canonical.relationship_counts() == counts_before
    assert control.list_record_provenance() == provenance_before

    # A REPLACE after the rollback must not leave any of W1's old rows behind
    # (a per-work key index that the rollback did not restore would).
    result = _publish(control, canonical, "asset-b", token, [replacement])
    assert result.counters.works_replaced == 1
    after = canonical.snapshot()
    for table in _GRAIN:
        expected = [*getattr(replacement, table), *_rows_for(before, table, "W2")]
        assert getattr(after, table) == _sorted_rows(table, expected), table
    assert canonical.get_work("W1") == replacement.work


def test_failed_publish_rolls_back_rows_it_wrote_under_another_work() -> None:
    control, canonical = _seeded(_bundle(1, 3), _bundle(2, 2))
    before = canonical.snapshot()
    # W1's replacement also carries rows keyed to W2, which merge into W2's
    # existing rows rather than replacing them; the rollback snapshot shares
    # W2's rows, so they must be copied before that merge, not mutated.
    stray = _with_stray_rows(_bundle(1, 1, "asset-b"), "W2", variants=(0, 7))
    with pytest.raises(_ForcedFailure):
        _publish(control, canonical, "asset-b", _claim(control, "asset-b"), [stray], fail=True)
    assert canonical.snapshot() == before


def test_provenance_index_stays_consistent_after_a_rolled_back_publish() -> None:
    control, canonical = _seeded(_bundle(1, 1), _bundle(2, 1))
    token_b = _claim(control, "asset-b")
    batch_b = [_bundle(1, 2, "asset-b"), _bundle(3, 1, "asset-b")]
    with pytest.raises(_ForcedFailure):
        _publish(control, canonical, "asset-b", token_b, batch_b, fail=True)
    assert _provenance_keys(control) == [("W1", "asset-a"), ("W2", "asset-a")]

    # The failed publish had indexed asset-b's keys at positions 2 and 3; asset-c
    # now takes those positions, so a stale index would overwrite its rows.
    _publish(
        control,
        canonical,
        "asset-c",
        _claim(control, "asset-c"),
        [_bundle(4, 1, "asset-c"), _bundle(5, 1, "asset-c")],
    )
    _publish(control, canonical, "asset-b", token_b, batch_b)
    assert _provenance_keys(control) == [
        ("W1", "asset-a"),
        ("W2", "asset-a"),
        ("W4", "asset-c"),
        ("W5", "asset-c"),
        ("W1", "asset-b"),
        ("W3", "asset-b"),
    ]

    # Re-recording an existing key still replaces that row in place.
    replay = _provenance("W2", "asset-a").model_copy(
        update={"processed_at": _NOW + timedelta(minutes=5)}
    )
    assert control.record_provenance(replay) == replay
    recorded = control.list_record_provenance()
    assert len(recorded) == 6
    assert recorded[1] == replay


def test_deletion_rollback_also_restores_the_provenance_index() -> None:
    control, canonical = _seeded(_bundle(1, 1), _bundle(2, 1))
    token = _claim(control, "deletions-a", entity="works-deletions")
    deletions = [
        DeletedWorkRecord(work_id="W1", deleted_date=date(2024, 4, 1)),
        DeletedWorkRecord(work_id="W2", deleted_date=date(2024, 4, 1)),
    ]
    conflicting = [*deletions, DeletedWorkRecord(work_id="W1", deleted_date=date(2024, 4, 2))]
    with pytest.raises(IngestionDecodeError, match="conflicting deleted_date"):
        _publish_deletions(control, canonical, "deletions-a", token, conflicting)
    assert _provenance_keys(control) == [("W1", "asset-a"), ("W2", "asset-a")]
    work = canonical.get_work("W1")
    assert work is not None and work.lineage.activity_state is CanonicalActivityState.ACTIVE

    _publish(control, canonical, "asset-c", _claim(control, "asset-c"), [_bundle(4, 1, "asset-c")])
    _publish_deletions(control, canonical, "deletions-a", token, deletions)
    assert _provenance_keys(control) == [
        ("W1", "asset-a"),
        ("W2", "asset-a"),
        ("W4", "asset-c"),
        ("W1", "deletions-a"),
        ("W2", "deletions-a"),
    ]


# ---------------------------------------------------------------------------
# Provenance index without a rollback.
# ---------------------------------------------------------------------------


def test_replaying_a_key_that_record_provenance_appended_replaces_it_in_place() -> None:
    # No rollback or _restore_provenance here, so every key was indexed by
    # record_provenance's own append.
    control = InMemoryControlStore()
    canonical = InMemoryCanonicalStore()
    result = _publish(
        control,
        canonical,
        "asset-a",
        _claim(control, "asset-a"),
        [_bundle(1, 1), _bundle(2, 1), _bundle(1, 1)],
    )
    # The same work twice in one asset leaves one provenance row.
    assert (result.counters.works_inserted, result.counters.works_identical) == (2, 1)
    assert _provenance_keys(control) == [("W1", "asset-a"), ("W2", "asset-a")]

    for number in (3, 4):
        control.record_provenance(_provenance(f"W{number}", "asset-a"))
    replay = _provenance("W3", "asset-a").model_copy(
        update={"processed_at": _NOW + timedelta(minutes=5)}
    )
    assert control.record_provenance(replay) == replay
    recorded = control.list_record_provenance()
    assert [row.record_id for row in recorded] == ["W1", "W2", "W3", "W4"]
    assert recorded[2] == replay


# ---------------------------------------------------------------------------
# Relationship row semantics.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("new_rows", [0, 1, 5])
def test_replace_leaves_exactly_the_new_bundles_rows(new_rows: int) -> None:
    store = InMemoryCanonicalStore()
    store.upsert_work_bundle(_bundle(1, 3))
    store.upsert_work_bundle(_bundle(2, 2))
    before = store.snapshot()
    replacement = _bundle(1, new_rows, "asset-b")

    assert store.upsert_work_bundle(replacement) is CanonicalUpsertOutcome.REPLACED
    after = store.snapshot()
    for table in _GRAIN:
        assert _rows_for(after, table, "W1") == _sorted_rows(table, getattr(replacement, table))
        assert _rows_for(after, table, "W2") == _rows_for(before, table, "W2")
    assert store.relationship_counts()["work_references"] == new_rows + 2


def test_merge_without_replace_keeps_old_rows_and_overwrites_by_key() -> None:
    store = InMemoryCanonicalStore()
    original = _bundle(1, 3)
    store.upsert_work_bundle(original)
    update = _bundle(1, 1, "asset-b")

    # Every caller passes replace_relationships=True; this pins the other branch.
    store._apply_bundle(update, replace_relationships=False)  # noqa: SLF001
    after = store.snapshot()
    for table, key in _GRAIN.items():
        merged = {key(row): row for row in getattr(original, table)}
        merged.update({key(row): row for row in getattr(update, table)})
        assert _rows_for(after, table, "W1") == _sorted_rows(table, list(merged.values()))
        assert _rows_for(after, table, "W1")[0] == getattr(update, table)[0]


def test_rows_keyed_to_another_work_follow_the_flat_map_semantics() -> None:
    store = InMemoryCanonicalStore()
    oracle = _FlatMapOracle()

    def upsert(bundle: CanonicalWorkBundle, expected: CanonicalUpsertOutcome) -> None:
        assert store.upsert_work_bundle(bundle) is expected
        oracle.apply(bundle, replace_relationships=True)
        oracle.assert_matches(store)

    upsert(_bundle(2, 3), CanonicalUpsertOutcome.INSERTED)
    # W1's bundle carries rows keyed to W2 (overwriting W2's first key and
    # adding variant 7) and a duplicate of its own first key.
    upsert(_with_stray_rows(_bundle(1, 2), "W2", variants=(0, 7)), CanonicalUpsertOutcome.INSERTED)
    first_w1_author = store.snapshot().work_authors[0]
    assert first_w1_author.work_id == "W1"
    assert first_w1_author.lineage.source_asset_id == "asset-c"  # last write won

    # Replacing W1 keeps the rows keyed to W2: they belong to W2 by key[0].
    upsert(_bundle(1, 1, "asset-b"), CanonicalUpsertOutcome.REPLACED)
    assert ("W2", 7) in {(r.work_id, r.authorship_index) for r in store.snapshot().work_authors}

    # Replacing W2 drops them with the rest of W2's rows.
    upsert(_bundle(2, 1, "asset-b"), CanonicalUpsertOutcome.REPLACED)
    assert {(r.work_id, r.authorship_index) for r in store.snapshot().work_authors} == {
        ("W1", 0),
        ("W2", 0),
    }

    # Rows keyed to a work that has no Work row still land under that work_id.
    upsert(_with_stray_rows(_bundle(4, 1), "W3", variants=(0,)), CanonicalUpsertOutcome.INSERTED)
    assert store.get_work("W3") is None
    assert ("W3", 0) in {(r.work_id, r.authorship_index) for r in store.snapshot().work_authors}


@pytest.mark.parametrize("seed", range(4))
def test_random_bundle_sequences_follow_the_flat_map_semantics(seed: int) -> None:
    rng = random.Random(seed)
    store = InMemoryCanonicalStore()
    oracle = _FlatMapOracle()
    for _ in range(40):
        bundle = _bundle(rng.randint(1, 5), rng.randint(0, 4), rng.choice(sorted(_ASSET_DATES)))
        if rng.random() < 0.3:
            bundle = _with_stray_rows(
                bundle, f"W{rng.randint(1, 5)}", variants=rng.sample(range(6), 2)
            )
        replace = rng.random() < 0.8
        store._apply_bundle(bundle, replace_relationships=replace)  # noqa: SLF001
        oracle.apply(bundle, replace_relationships=replace)
        oracle.assert_matches(store)


# ---------------------------------------------------------------------------
# Scaling guards (no timing): per-record work must not touch the whole store.
# ---------------------------------------------------------------------------


class _ScanCountingDict(dict[Any, Any]):
    """A dict that counts whole-map iteration."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.scans = 0

    def __iter__(self) -> Iterator[Any]:
        self.scans += 1
        return super().__iter__()

    def keys(self) -> Any:
        self.scans += 1
        return super().keys()

    def values(self) -> Any:
        self.scans += 1
        return super().values()

    def items(self) -> Any:
        self.scans += 1
        return super().items()


class _ScanCountingList(list[Any]):
    """A list that counts every operation that walks its elements."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.scans = 0

    def __iter__(self) -> Iterator[Any]:
        self.scans += 1
        return super().__iter__()

    def __reversed__(self) -> Iterator[Any]:
        self.scans += 1
        return super().__reversed__()

    def __contains__(self, item: object) -> bool:
        self.scans += 1
        return super().__contains__(item)

    def index(self, *args: Any) -> int:
        self.scans += 1
        return super().index(*args)

    def count(self, item: Any) -> int:
        self.scans += 1
        return super().count(item)


def test_upserting_a_work_touches_only_that_works_relationship_rows() -> None:
    store = InMemoryCanonicalStore()
    for number in (1, 2, 3):
        store.upsert_work_bundle(_bundle(number, 2))
    maps: dict[str, _ScanCountingDict] = {}
    for table in _GRAIN:
        maps[table] = _ScanCountingDict(getattr(store, f"_{table}"))
        setattr(store, f"_{table}", maps[table])
    others = {table: {w: rows[w] for w in ("W2", "W3")} for table, rows in maps.items()}

    assert store.upsert_work_bundle(_bundle(1, 1, "asset-b")) is CanonicalUpsertOutcome.REPLACED
    assert store.upsert_work_bundle(_bundle(4, 2)) is CanonicalUpsertOutcome.INSERTED

    for table, rows in maps.items():
        assert rows.scans == 0, table
        assert getattr(store, f"_{table}") is rows, table  # updated, not rebuilt
        for work_id, inner in others[table].items():
            assert rows[work_id] is inner, (table, work_id)


def test_record_provenance_does_not_scan_recorded_rows() -> None:
    control = InMemoryControlStore()
    for number in range(50):
        control.record_provenance(_provenance(f"W{number}", "asset-a"))
    rows = _ScanCountingList(control.list_record_provenance())
    control._restore_provenance(rows)  # noqa: SLF001
    rows.scans = 0  # re-indexing walks the list once; count from here

    for number in range(50, 100):
        control.record_provenance(_provenance(f"W{number}", "asset-a"))
    replay = _provenance("W7", "asset-a").model_copy(update={"source_uri": "s3://openalex/replay"})
    control.record_provenance(replay)

    assert rows.scans == 0
    recorded = control.list_record_provenance()
    assert [row.record_id for row in recorded] == [f"W{number}" for number in range(100)]
    assert recorded[7] == replay


def test_rollback_snapshot_shares_the_stored_rows_instead_of_copying_them() -> None:
    # _bundle has no primary_location; this one fills sources and publishers.
    hosted = map_openalex_work(
        {
            **_raw_work(3, 1),
            "primary_location": {
                "source": {
                    "id": "https://openalex.org/S1",
                    "display_name": "Source 1",
                    "host_organization": "https://openalex.org/P1",
                    "host_organization_name": "Publisher 1",
                }
            },
        },
        lineage=_lineage("asset-a"),
    )
    control, canonical = _seeded(_bundle(1, 2), _bundle(2, 2), hosted)
    tables = [field.name for field in dataclasses.fields(_CanonicalSnapshot)]
    assert len(tables) == 15
    # Each value: a model, or a relationship map's per-work inner dict.
    stored = {table: dict(getattr(canonical, f"_{table}")) for table in tables}
    assert [table for table, values in stored.items() if not values] == []
    with pytest.raises(_ForcedFailure):
        _publish(
            control,
            canonical,
            "asset-b",
            _claim(control, "asset-b"),
            [_bundle(1, 1, "asset-b"), _bundle(2, 1, "asset-b")],
            fail=True,
        )
    # Restored by reference: the snapshot was shallow (no per-asset deep copy).
    for table, values in stored.items():
        restored = getattr(canonical, f"_{table}")
        assert restored.keys() == values.keys(), table
        assert all(restored[key] is value for key, value in values.items()), table


_IMMUTABLE_LEAVES = (str, int, float, bool, type(None), date, datetime, UUID, AwareDatetime, Enum)


def _leaf_types(annotation: Any) -> Iterator[Any]:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        for argument in typing.get_args(annotation):
            yield from _leaf_types(argument)
    else:
        yield annotation


def _mutable_parts(model: type[BaseModel], path: str) -> list[str]:
    found = [] if model.model_config.get("frozen") is True else [f"{path} is not frozen"]
    for name, field in model.model_fields.items():
        for leaf in _leaf_types(field.annotation):
            if isinstance(leaf, type) and issubclass(leaf, BaseModel):
                found += _mutable_parts(leaf, f"{path}.{name}")
            elif not (isinstance(leaf, type) and issubclass(leaf, _IMMUTABLE_LEAVES)):
                found.append(f"{path}.{name}: {leaf!r}")
    return found


@pytest.mark.parametrize("model", _STORED_MODELS, ids=lambda model: model.__name__)
def test_stored_canonical_models_are_frozen_with_immutable_fields(model: type[BaseModel]) -> None:
    # _snapshot_canonical copies only the outer dicts; that is a snapshot only
    # while no stored value can change in place.
    assert _mutable_parts(model, model.__name__) == []
