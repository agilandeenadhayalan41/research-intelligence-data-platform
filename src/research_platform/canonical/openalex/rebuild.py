"""Replay landed Bronze inputs into a fresh in-memory canonical state (DE-07).

The only inputs are an ``ObjectStore`` holding Bronze objects and DE-06 landing
manifests, plus an ordered input list (from a build request or a release
manifest). Nothing is fetched from the source, nothing is written to any store,
and no control store, PostgreSQL or BigQuery is involved. Each input is applied
exactly as the Step 12/13 in-memory publish loops apply it, with the ``run_id``
and ``processed_at`` recorded for that input, so the replay reproduces the
canonical state, and therefore the release bytes, of the original build.

Inputs are applied in strictly increasing UTC ``processed_at``, the order the
pipelines applied them. Every raw object is hashed while it is decoded, and its
non-blank lines are counted by a separate gzip line counter that shares no code
with the decoders, so a decoder that loses records fails closed.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import BinaryIO, Final, Literal
from uuid import UUID

from pydantic import ValidationError

from research_platform.canonical.memory import CanonicalSnapshot, InMemoryCanonicalStore
from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.canonical.openalex.errors import (
    CanonicalModelError,
    ReleaseConfigError,
    ReleaseInputError,
    ReleaseRebuildError,
)
from research_platform.canonical.openalex.manifest import (
    ASSET_ID_PATTERN,
    MAX_ASSET_ID_LENGTH,
    CanonicalReleaseManifest,
    DeletionInputCounts,
    ReleaseInputIdentity,
    WorksInputCounts,
)
from research_platform.canonical.openalex.mapping import map_openalex_work
from research_platform.canonical.openalex.models import CanonicalActivityState, CanonicalLineage
from research_platform.canonical.store import CanonicalUpsertOutcome
from research_platform.control.models import ControlStatus, SourceFileControl
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletions_csv import iter_deleted_work_records
from research_platform.ingestion.errors import IngestionError
from research_platform.ingestion.jsonl import iter_jsonl_gz_records
from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import ObjectStoreError
from research_platform.storage.landing_manifest import LandingManifest
from research_platform.storage.openalex_layout import openalex_landing_manifest_key

_CHUNK: Final = 64 * 1024
_ENTITIES: Final = ("works", "works-deletions")
_ASSET_ID: Final = re.compile(ASSET_ID_PATTERN)
Entity = Literal["works", "works-deletions"]

# A resolved input is exactly the identity a release manifest lists for it.
BronzeInput = ReleaseInputIdentity


@dataclass(frozen=True)
class ReleaseInputSpec:
    """What a build request names per input; everything else comes from Bronze."""

    entity: Entity
    run_id: UUID
    asset_id: str
    processed_at: datetime


@dataclass(frozen=True)
class ReplayedState:
    snapshot: CanonicalSnapshot
    barriers: dict[str, date]
    counts: tuple[WorksInputCounts | DeletionInputCounts, ...]


def release_input_from_source_file(row: SourceFileControl) -> ReleaseInputSpec:
    """Spec from a SUCCESS ``SourceFileControl`` row; its run wrote the landing manifest and lineage."""
    if not isinstance(row, SourceFileControl):
        raise ReleaseConfigError("a release input must come from a source-file control row")
    if row.source != "openalex" or row.status is not ControlStatus.SUCCESS or row.processed_at is None:
        raise ReleaseConfigError("only a SUCCESS OpenAlex source file with processed_at can be a release input")
    if row.entity not in _ENTITIES:
        raise ReleaseConfigError("release inputs are works or works-deletions assets")
    return ReleaseInputSpec(
        entity=row.entity,  # type: ignore[arg-type]
        run_id=row.run_id,
        asset_id=row.asset_id,
        processed_at=row.processed_at,
    )


def release_inputs_from_source_files(rows: Sequence[SourceFileControl]) -> tuple[ReleaseInputSpec, ...]:
    """Specs in application order (increasing ``processed_at``); equal times are refused."""
    specs = sorted((release_input_from_source_file(row) for row in rows), key=lambda spec: spec.processed_at)
    for earlier, later in zip(specs, specs[1:]):
        if earlier.processed_at == later.processed_at:
            raise ReleaseConfigError("two release inputs share one processed_at")
    return tuple(specs)


def check_release_input_specs(specs: Sequence[ReleaseInputSpec]) -> None:
    """Refuse an empty list, unknown shapes, unsafe or duplicate assets and naive times (no store calls)."""
    if not specs:
        raise ReleaseConfigError("a release needs at least one input")
    seen: set[str] = set()
    for spec in specs:
        if (
            not isinstance(spec, ReleaseInputSpec)
            or spec.entity not in _ENTITIES
            or not isinstance(spec.run_id, UUID)
            or not isinstance(spec.asset_id, str)
            or not isinstance(spec.processed_at, datetime)
        ):
            raise ReleaseConfigError("release inputs must be ReleaseInputSpec values")
        if len(spec.asset_id) > MAX_ASSET_ID_LENGTH or not _ASSET_ID.fullmatch(spec.asset_id):
            raise ReleaseConfigError("release input asset ids must be safe key segments")
        if spec.processed_at.tzinfo is None or spec.processed_at.utcoffset() is None:
            raise ReleaseConfigError("processed_at must be timezone-aware")
        if spec.asset_id in seen:
            raise ReleaseConfigError("an asset may appear only once in a release")
        seen.add(spec.asset_id)


def _read_landing(bronze: ObjectStore, key: str) -> tuple[LandingManifest, str]:
    try:
        with bronze.open(key) as handle:
            payload = handle.read()
    except ObjectStoreError as error:
        raise ReleaseInputError("landing manifest is missing or incomplete") from error
    try:
        manifest = LandingManifest.model_validate_json(payload)
    except ValueError as error:
        raise ReleaseInputError("landing manifest is invalid") from error
    return manifest, hashlib.sha256(payload).hexdigest()


def resolve_bronze_inputs(bronze: ObjectStore, specs: Sequence[ReleaseInputSpec]) -> tuple[BronzeInput, ...]:
    """Read each input's landing manifest and pin its facts, in strictly increasing UTC ``processed_at``."""
    check_release_input_specs(specs)
    times = [spec.processed_at.astimezone(UTC) for spec in specs]
    for earlier, later in zip(times, times[1:]):
        if not earlier < later:
            raise ReleaseConfigError("release inputs must have strictly increasing processed_at")
    resolved: list[BronzeInput] = []
    for sequence, (spec, processed_at) in enumerate(zip(specs, times, strict=True)):
        key = openalex_landing_manifest_key(spec.entity, spec.run_id, spec.asset_id)
        landing, landing_sha = _read_landing(bronze, key)
        entry = landing.entry
        if (landing.entity, landing.run_id, entry.asset_id) != (spec.entity, spec.run_id, spec.asset_id):
            raise ReleaseInputError("landing manifest names a different input")
        if processed_at < entry.retrieved_at:
            raise ReleaseConfigError("processed_at must not precede the landing of its input")
        try:
            resolved.append(
                BronzeInput(
                    sequence=sequence,
                    entity=spec.entity,
                    asset_id=spec.asset_id,
                    run_id=spec.run_id,
                    processed_at=processed_at,
                    landing_manifest_key=key,
                    landing_manifest_sha256=landing_sha,
                    raw_object_key=entry.raw_object_key,
                    raw_sha256=entry.sha256,
                    raw_size_bytes=entry.size_bytes,
                    snapshot_date=entry.snapshot_date,
                    updated_date=entry.updated_date,
                )
            )
        except ValidationError as error:
            raise ReleaseConfigError("release input is invalid") from error
    return tuple(resolved)


def bronze_inputs_from_manifest(manifest: CanonicalReleaseManifest) -> tuple[BronzeInput, ...]:
    return manifest.intent().inputs


class _RawLineCounter:
    """Counts non-blank decompressed lines of a (multi-member) gzip stream fed compressed chunks.

    Independent of ``ingestion.jsonl`` and ``ingestion.deletions_csv``: plain
    ``zlib`` and newline splitting. A line counts when it holds a byte other
    than ASCII whitespace. The decoders also skip lines of other whitespace
    characters, which this counter counts, so such a file fails closed.
    """

    def __init__(self) -> None:
        self._inflate = zlib.decompressobj(wbits=31)
        self._partial_has_content = False
        self._fed = False
        self.failed = False
        self.lines = 0

    def feed(self, data: bytes) -> None:
        if self.failed:
            return
        self._fed = self._fed or bool(data)
        try:
            while data:
                if self._inflate.eof:
                    data = data.lstrip(b"\x00")  # gzip allows zero padding after a member
                    if not data:
                        return
                    self._inflate = zlib.decompressobj(wbits=31)
                self._count(self._inflate.decompress(data, _CHUNK))
                data = self._inflate.unused_data if self._inflate.eof else self._inflate.unconsumed_tail
        except zlib.error:
            self.failed = True

    def _count(self, out: bytes) -> None:
        *complete, last = out.split(b"\n")
        for piece in complete:
            if self._partial_has_content or piece.strip():
                self.lines += 1
            self._partial_has_content = False
        self._partial_has_content = self._partial_has_content or bool(last.strip())

    def finish(self) -> int:
        if not self._fed:
            return 0  # an empty object decodes to no records; the SHA-256 and size checks cover truncation
        if not self.failed and not self._inflate.eof:
            self._count(self._inflate.flush())
        if self.failed or not self._inflate.eof:
            raise ValueError("raw object is not a complete gzip stream")
        if self._partial_has_content:
            self.lines += 1
            self._partial_has_content = False
        return self.lines


class _HashingReader:
    """Read-through SHA-256, byte count and raw line count of everything read."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner
        self.digest = hashlib.sha256()
        self.size = 0
        self.lines = _RawLineCounter()

    def read(self, size: int = -1) -> bytes:
        data = self._inner.read(size)
        self.digest.update(data)
        self.size += len(data)
        self.lines.feed(data)
        return data

    def readable(self) -> bool:
        return True

    def drain(self) -> None:
        while self.read(_CHUNK):
            pass

    def matches(self, item: BronzeInput) -> bool:
        return self.digest.hexdigest() == item.raw_sha256 and self.size == item.raw_size_bytes


def _check_landing(bronze: ObjectStore, item: BronzeInput) -> None:
    landing, landing_sha = _read_landing(bronze, item.landing_manifest_key)
    entry = landing.entry
    if landing_sha != item.landing_manifest_sha256 or (
        landing.entity,
        landing.run_id,
        entry.asset_id,
        entry.raw_object_key,
        entry.sha256,
        entry.size_bytes,
        entry.snapshot_date,
        entry.updated_date,
    ) != (
        item.entity,
        item.run_id,
        item.asset_id,
        item.raw_object_key,
        item.raw_sha256,
        item.raw_size_bytes,
        item.snapshot_date,
        item.updated_date,
    ):
        raise ReleaseInputError("landing manifest differs from the release input")


def _replay_works(
    store: InMemoryCanonicalStore, reader: _HashingReader, item: BronzeInput, limits: JsonlDecodeLimits
) -> WorksInputCounts:
    # Same lineage and upsert as ingestion/pipeline.py's record stream and memory publish loop.
    counts = {outcome: 0 for outcome in CanonicalUpsertOutcome}
    records = 0
    for record in iter_jsonl_gz_records(reader, limits=limits):  # type: ignore[arg-type]
        lineage = CanonicalLineage.model_validate(
            {
                "source_asset_id": item.asset_id,
                "source_checksum_sha256": item.raw_sha256,
                "source_updated_date": item.updated_date,
                "run_id": item.run_id,
                "processed_at": item.processed_at,
                "activity_state": CanonicalActivityState.ACTIVE,
            }
        )
        counts[store.upsert_work_bundle(map_openalex_work(record, lineage=lineage))] += 1
        records += 1
    return WorksInputCounts.model_construct(
        raw_lines=0,
        records=records,
        inserted=counts[CanonicalUpsertOutcome.INSERTED],
        identical=counts[CanonicalUpsertOutcome.IDENTICAL],
        replaced=counts[CanonicalUpsertOutcome.REPLACED],
        stale=counts[CanonicalUpsertOutcome.STALE],
    )


def _replay_deletions(
    store: InMemoryCanonicalStore, reader: _HashingReader, item: BronzeInput, limits: CsvDeletionDecodeLimits
) -> DeletionInputCounts:
    # Same rules as persistence/memory_deletion_unit_of_work.py.
    seen: dict[str, date] = {}
    rows = duplicates = 0
    counts = {outcome: 0 for outcome in DeletionOutcome}
    for record in iter_deleted_work_records(reader, limits=limits):  # type: ignore[arg-type]
        rows += 1
        prior = seen.get(record.work_id)
        if prior is not None:
            if prior != record.deleted_date:
                raise ReleaseRebuildError("conflicting deleted_date for one work_id in one deletion asset")
            duplicates += 1
            continue
        seen[record.work_id] = record.deleted_date
        outcome = store.apply_work_deletion(
            record.work_id,
            deletion_asset_id=item.asset_id,
            source_checksum_sha256=item.raw_sha256,
            run_id=item.run_id,
            deleted_date=record.deleted_date,
            processed_at=item.processed_at,
            deleted_at=item.processed_at,
        )
        counts[outcome] += 1
    return DeletionInputCounts.model_construct(
        raw_lines=0,
        rows_seen=rows,
        unique_ids=len(seen),
        duplicates=duplicates,
        deleted=counts[DeletionOutcome.DELETED],
        already_deleted=counts[DeletionOutcome.ALREADY_DELETED],
        unknown=counts[DeletionOutcome.UNKNOWN_WORK],
        stale=counts[DeletionOutcome.STALE],
    )


def _bronze_unchanged(reader: _HashingReader, item: BronzeInput) -> bool:
    """Drain what the decoder left and compare digest and size; a read failure counts as changed."""
    try:
        reader.drain()
    except (ObjectStoreError, OSError):
        return False
    return reader.matches(item)


def _with_raw_lines(
    counts: WorksInputCounts | DeletionInputCounts, reader: _HashingReader
) -> WorksInputCounts | DeletionInputCounts:
    try:
        lines = reader.lines.finish()
    except ValueError as error:
        raise ReleaseRebuildError("the raw line count could not be taken") from error
    if isinstance(counts, DeletionInputCounts):
        lines = max(lines - 1, 0)  # the header line
    payload = {**counts.model_dump(), "raw_lines": lines}
    try:
        return type(counts).model_validate(payload)
    except ValidationError as error:
        raise ReleaseRebuildError("decoded records differ from the raw line count or outcomes") from error


def replay_bronze_inputs(
    bronze: ObjectStore,
    inputs: Sequence[BronzeInput],
    *,
    works_limits: JsonlDecodeLimits,
    deletion_limits: CsvDeletionDecodeLimits,
) -> ReplayedState:
    """Apply ``inputs`` in sequence to a fresh store. Reads only; writes nothing."""
    if tuple(item.sequence for item in inputs) != tuple(range(len(inputs))):
        raise ReleaseConfigError("inputs must be in sequence order from 0")
    store = InMemoryCanonicalStore()
    all_counts: list[WorksInputCounts | DeletionInputCounts] = []
    for item in inputs:
        _check_landing(bronze, item)
        try:
            handle = bronze.open(item.raw_object_key)
        except ObjectStoreError as error:
            raise ReleaseInputError("a Bronze raw object is missing or incomplete") from error
        with handle:
            reader = _HashingReader(handle)
            try:
                if item.entity == "works":
                    counts: WorksInputCounts | DeletionInputCounts = _replay_works(
                        store, reader, item, works_limits
                    )
                else:
                    counts = _replay_deletions(store, reader, item, deletion_limits)
                reader.drain()
            except Exception as error:
                # A listed input succeeded in its original pipeline, so a failure here is
                # either changed Bronze or divergence; check the bytes first.
                if not _bronze_unchanged(reader, item):
                    raise ReleaseInputError("a Bronze raw object differs from its recorded SHA-256") from error
                if isinstance(error, ReleaseInputError | ReleaseRebuildError):
                    raise
                if isinstance(error, ObjectStoreError):
                    raise ReleaseInputError("a Bronze raw object could not be read") from error
                if isinstance(error, CanonicalModelError | IngestionError | ValueError | LookupError):
                    raise ReleaseRebuildError("replay of a listed input failed") from error
                raise
        if not reader.matches(item):
            raise ReleaseInputError("a Bronze raw object differs from its recorded SHA-256")
        all_counts.append(_with_raw_lines(counts, reader))
    return ReplayedState(snapshot=store.snapshot(), barriers=store.deletion_barriers(), counts=tuple(all_counts))
