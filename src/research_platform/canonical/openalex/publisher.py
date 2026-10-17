"""Build, validate and reconcile Silver canonical releases; write publication records (ADR 0001 D1, DE-07).

``build_canonical_release`` replays the listed Bronze inputs into a fresh
in-memory state (``rebuild.replay_bronze_inputs``), writes every part create-only
through ``ObjectStore.put_if_absent``, one row group at a time inside one part,
then writes ``manifest.json`` last. A release is visible only through its
manifest; "complete" means ``validate_release`` succeeds. Every object's
provenance carries the build's intent digest, so a retry of the same build run
with the same ``built_at`` (the timestamp in the release id) is an identical
replay (``existing_kept``, no writes) or a ``ReleaseConflictError`` that keeps
what is stored. The caller persists ``built_at`` with its build run; enforcing
one release per build run is the DE-12/DE-20 control plane's job. "Published" is
reserved for the DE-12 (#133) release pointer.

``reconcile_release`` reproduces a complete release from the Bronze objects its
manifest lists, under the recorded PyArrow version and Parquet options and with
unchanged mapping code, and compares every part's SHA-256 and rows, per-table
row, ``activity_state`` and tombstoned-Work row counts, barrier counts, per-input
outcome counts and the content fingerprint. No BigQuery, PostgreSQL, network or
source fetch, and nothing is written to either store.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version as _dist_version
from typing import Any, Literal
from uuid import UUID

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import ValidationError

from research_platform.canonical.openalex.errors import (
    PublicationRecordError,
    ReconciliationMismatchError,
    ReleaseConfigError,
    ReleaseConflictError,
    ReleaseEnvironmentMismatchError,
    ReleaseManifestError,
    ReleaseNotFoundError,
    ReleaseParentError,
    ReleasePartError,
    ReleaseRebuildError,
    ReleaseSchemaMismatchError,
)
from research_platform.canonical.openalex.export import (
    PARQUET_WRITE_OPTIONS,
    canonical_table_to_arrow,
    pinned_parquet_bytes,
)
from research_platform.canonical.openalex.manifest import (
    CANONICAL_SCHEMA_MAJOR,
    FOOTER_FINGERPRINT_KEY,
    FOOTER_SCHEMA_MAJOR_KEY,
    FOOTER_TABLE_KEY,
    RELATIONSHIP_TABLE_NAMES,
    RELEASE_TABLE_NAMES,
    WORK_DELETION_BARRIERS_SCHEMA,
    WORK_DELETION_BARRIERS_TABLE,
    INTENT_CHECKED_ON_JSON,
    CanonicalReleaseManifest,
    CodeVersion,
    DecodeLimits,
    PublicationRecord,
    ReleaseFile,
    ReleaseInput,
    ReleaseIntent,
    ReleaseTable,
    ToolVersions,
    WriterSettings,
    canonical_json,
    content_fingerprint,
    dumps_publication_record,
    dumps_release_manifest,
    footer_metadata,
    publication_record_provenance,
    release_manifest_provenance,
    release_object_provenance,
    release_schema_fingerprints,
    release_table_schema,
    schema_fingerprint,
    stored_intent_sha256,
)
from research_platform.canonical.openalex.models import CanonicalActivityState
from research_platform.canonical.openalex.rebuild import (
    BronzeInput,
    ReleaseInputSpec,
    ReplayedState,
    bronze_inputs_from_manifest,
    check_release_input_specs,
    replay_bronze_inputs,
    resolve_bronze_inputs,
)
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.storage.base import ObjectStore
from research_platform.storage.canonical_layout import (
    MAX_PART_INDEX,
    canonical_manifest_key,
    canonical_part_relative_path,
    canonical_publication_record_key,
    canonical_release_root,
    release_id_for_build,
    validate_release_id,
)
from research_platform.storage.errors import (
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectStoreError,
)

__all__ = [
    "DEFAULT_ROWS_PER_PART",
    "DEFAULT_ROW_GROUP_SIZE",
    "BuiltRelease",
    "FileReconciliation",
    "ReconciliationReport",
    "RenderedPart",
    "ValidatedRelease",
    "build_canonical_release",
    "read_publication_record",
    "read_release_manifest",
    "reconcile_release",
    "render_release_parts",
    "tool_versions",
    "validate_release",
    "write_publication_record",
]

# Unmeasured starting points; DE-14/DE-20 tune them by measurement. Recorded in
# every manifest because part bytes depend on them.
DEFAULT_ROWS_PER_PART = 500_000
DEFAULT_ROW_GROUP_SIZE = 100_000

_REVISION = re.compile(r"^[a-f0-9]{40}$")
_ACTIVITY_STATES = tuple(state.value for state in CanonicalActivityState)


@dataclass(frozen=True)
class RenderedPart:
    """One encoded part. ``payload`` is empty once the part is summarized."""

    table: str
    file: ReleaseFile
    activity_state_counts: dict[str, int] | None
    rows_of_deleted_works: int | None
    payload: bytes = b""

    def summary(self) -> RenderedPart:
        return replace(self, payload=b"")


@dataclass(frozen=True)
class BuiltRelease:
    manifest: CanonicalReleaseManifest
    manifest_key: str
    manifest_sha256: str
    outcome: Literal["written", "existing_kept"]
    parts_written: int


@dataclass(frozen=True)
class ValidatedRelease:
    manifest: CanonicalReleaseManifest
    manifest_sha256: str


def tool_versions() -> ToolVersions:
    return ToolVersions(
        package=_dist_version("research-intelligence-data-platform"),
        python=platform.python_version(),
        pyarrow=pa.__version__,
        pydantic=_dist_version("pydantic"),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _activity_counts(table: pa.Table, error: type[Exception]) -> dict[str, int]:
    counts = dict.fromkeys(_ACTIVITY_STATES, 0)
    for item in pc.value_counts(table.column("activity_state")).to_pylist():
        if item["values"] not in counts:
            raise error("unknown activity_state value")
        counts[item["values"]] = item["counts"]
    return counts


def _rows_of(table: pa.Table, work_ids: pa.Array) -> int:
    return int(pc.sum(pc.is_in(table.column("work_id"), value_set=work_ids)).as_py() or 0)


def _deleted_work_ids(state: ReplayedState) -> pa.Array:
    deleted = CanonicalActivityState.DELETED
    return pa.array(
        sorted(work.work_id for work in state.snapshot.works if work.lineage.activity_state is deleted), pa.string()
    )


def _check_barrier_invariants(state: ReplayedState) -> None:
    """Every DELETED Work has a barrier; no ACTIVE Work has one (ADR 0001 D1 deletion policy)."""
    barriers = state.barriers.keys()
    for work in state.snapshot.works:
        deleted = work.lineage.activity_state is CanonicalActivityState.DELETED
        if deleted != (work.work_id in barriers):
            raise ReleaseRebuildError("deletion barriers disagree with Work activity states")


def _arrow_rows(table_name: str, rows: Sequence[Any]) -> pa.Table:
    if table_name == WORK_DELETION_BARRIERS_TABLE:
        return pa.Table.from_arrays(
            [pa.array([row[0] for row in rows], pa.string()), pa.array([row[1] for row in rows], pa.date32())],
            schema=WORK_DELETION_BARRIERS_SCHEMA,
        )
    return canonical_table_to_arrow(table_name, rows)


def _encode_part(
    table_name: str, rows: Sequence[Any], writer: WriterSettings, deleted_ids: pa.Array
) -> tuple[bytes, dict[str, int] | None, int | None]:
    """Encode one part one row group at a time; return bytes and its counts."""
    track_activity = table_name != WORK_DELETION_BARRIERS_TABLE
    activity = dict.fromkeys(_ACTIVITY_STATES, 0) if track_activity else None
    of_deleted = 0 if table_name in RELATIONSHIP_TABLE_NAMES else None
    metadata = footer_metadata(table_name)
    if not rows:
        # A writer with no write_table call emits no row group; write_table emits one.
        empty = _arrow_rows(table_name, ()).replace_schema_metadata(metadata)
        return pinned_parquet_bytes(empty, row_group_size=writer.row_group_size), activity, of_deleted
    sink = io.BytesIO()
    schema = release_table_schema(table_name).with_metadata(metadata)
    with pq.ParquetWriter(sink, schema, **PARQUET_WRITE_OPTIONS) as parquet_writer:
        for start in range(0, len(rows), writer.row_group_size):
            arrow = _arrow_rows(table_name, rows[start : start + writer.row_group_size])
            if activity is not None:
                for state, count in _activity_counts(arrow, ReleaseRebuildError).items():
                    activity[state] += count
            if of_deleted is not None:
                of_deleted += _rows_of(arrow, deleted_ids)
            parquet_writer.write_table(arrow, row_group_size=writer.row_group_size)
            del arrow
    return sink.getvalue(), activity, of_deleted


def render_release_parts(state: ReplayedState, writer: WriterSettings) -> Iterator[RenderedPart]:
    """Yield every part in release order; one part's bytes and one row group of rows are live at a time.

    Barrier invariants and the part-index limit are checked before the first
    part, so a broken state raises ``ReleaseRebuildError`` and a too small
    ``rows_per_part`` raises ``ReleaseConfigError`` before anything is written.
    """
    _check_barrier_invariants(state)
    for table_name in RELEASE_TABLE_NAMES:
        is_barrier = table_name == WORK_DELETION_BARRIERS_TABLE
        row_count = len(state.barriers) if is_barrier else len(getattr(state.snapshot, table_name))
        if -(-row_count // writer.rows_per_part) > MAX_PART_INDEX + 1:
            raise ReleaseConfigError("rows_per_part is too small for this release")
    deleted_ids = _deleted_work_ids(state)
    for table_name in RELEASE_TABLE_NAMES:
        if table_name == WORK_DELETION_BARRIERS_TABLE:
            rows: Sequence[Any] = sorted(state.barriers.items())
        else:
            rows = getattr(state.snapshot, table_name)
        starts = range(0, len(rows), writer.rows_per_part) if rows else range(1)
        for index, start in enumerate(starts):
            chunk = rows[start : start + writer.rows_per_part]
            payload, activity, of_deleted = _encode_part(table_name, chunk, writer, deleted_ids)
            meta = pq.ParquetFile(io.BytesIO(payload)).metadata
            part = RenderedPart(
                table=table_name,
                file=ReleaseFile(
                    path=canonical_part_relative_path(table_name, index),
                    part_index=index,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    row_count=meta.num_rows,
                    row_group_count=meta.num_row_groups,
                ),
                activity_state_counts=activity,
                rows_of_deleted_works=of_deleted,
                payload=payload,
            )
            del payload
            yield part
            del part


def _assemble_tables(parts: Sequence[RenderedPart]) -> tuple[ReleaseTable, ...]:
    fingerprints = release_schema_fingerprints()
    tables: list[ReleaseTable] = []
    for name in RELEASE_TABLE_NAMES:
        mine = [part for part in parts if part.table == name]
        activity: dict[str, int] | None = None
        of_deleted: int | None = None
        if name != WORK_DELETION_BARRIERS_TABLE:
            activity = dict.fromkeys(_ACTIVITY_STATES, 0)
            for part in mine:
                for state, count in (part.activity_state_counts or {}).items():
                    activity[state] += count
        if name in RELATIONSHIP_TABLE_NAMES:
            of_deleted = sum(part.rows_of_deleted_works or 0 for part in mine)
        tables.append(
            ReleaseTable(
                table=name,
                schema_fingerprint=fingerprints[name],
                row_count=sum(part.file.row_count for part in mine),
                activity_state_counts=activity,
                rows_of_deleted_works=of_deleted,
                files=tuple(part.file for part in mine),
            )
        )
    return tuple(tables)


def _manifest(
    intent: ReleaseIntent,
    intent_sha256: str,
    state: ReplayedState,
    parts: Sequence[RenderedPart],
) -> CanonicalReleaseManifest:
    tables = _assemble_tables(parts)
    works = tables[0].activity_state_counts or {}
    barrier_count = tables[-1].row_count
    header = intent.model_dump(exclude={"schema_fingerprints", "inputs"})
    try:
        return CanonicalReleaseManifest(
            **header,
            inputs=tuple(
                ReleaseInput(**item.model_dump(), counts=counts)
                for item, counts in zip(intent.inputs, state.counts, strict=True)
            ),
            tables=tables,
            barrier_count=barrier_count,
            barrier_only_count=barrier_count - works.get("DELETED", 0),
            intent_sha256=intent_sha256,
            content_fingerprint=content_fingerprint(tables),
        )
    except ValidationError as error:
        raise ReleaseRebuildError("the rebuilt release does not form a valid manifest") from error


# ---------------------------------------------------------------------------
# Reading and validating
# ---------------------------------------------------------------------------


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_object(payload: bytes, error: type[Exception]) -> dict[str, Any]:
    """Parse with the stdlib and require the stored bytes to be the canonical form."""
    try:
        text = payload.decode("ascii")
        obj = json.loads(text, object_pairs_hook=_refuse_duplicate_keys)
    except ValueError as exc:
        raise error("stored JSON is not readable") from exc
    if not isinstance(obj, dict) or f"{canonical_json(obj)}\n" != text:
        raise error("stored JSON is not in canonical form")
    return obj


def _parse_manifest(payload: bytes, release_id: str, schema_major: int) -> CanonicalReleaseManifest:
    obj = _canonical_object(payload, ReleaseManifestError)
    try:
        intent_matches = stored_intent_sha256(obj) == obj.get("intent_sha256")
    except (KeyError, TypeError, AttributeError) as error:
        raise ReleaseManifestError("release manifest fails validation") from error
    if not intent_matches:
        raise ReleaseManifestError("release manifest intent digest does not match its request")
    try:
        manifest = CanonicalReleaseManifest.model_validate(obj, context={INTENT_CHECKED_ON_JSON: True})
    except ValidationError as error:
        raise ReleaseManifestError("release manifest fails validation") from error
    if manifest.release_id != release_id or manifest.schema_major != schema_major:
        raise ReleaseManifestError("release manifest names another release")
    return manifest


def _read_manifest_bytes(silver: ObjectStore, key: str) -> bytes | None:
    try:
        with silver.open(key) as handle:
            return handle.read()
    except ObjectNotFoundError:
        return None
    except ObjectStoreError as error:
        raise ReleaseManifestError("release manifest is incomplete or unreadable") from error


def _load_manifest(
    silver: ObjectStore, release_id: str, schema_major: int
) -> tuple[CanonicalReleaseManifest, bytes]:
    key = canonical_manifest_key(schema_major, validate_release_id(release_id))
    payload = _read_manifest_bytes(silver, key)
    if payload is None:
        raise ReleaseNotFoundError("release has no manifest")
    return _parse_manifest(payload, release_id, schema_major), payload


def read_release_manifest(
    silver: ObjectStore, release_id: str, *, schema_major: int = CANONICAL_SCHEMA_MAJOR
) -> CanonicalReleaseManifest:
    """The only reader entry point: a release without a manifest is not visible."""
    return _load_manifest(silver, release_id, schema_major)[0]


def _read_part(silver: ObjectStore, key: str) -> bytes:
    try:
        with silver.open(key) as handle:
            return handle.read()
    except ObjectNotFoundError as error:
        raise ReleasePartError("a listed part is missing") from error
    except ObjectStoreError as error:
        raise ReleasePartError("a listed part is incomplete or corrupt") from error


def _check_part(
    payload: bytes, table: ReleaseTable, item: ReleaseFile, schema_major: int, deleted_ids: pa.Array
) -> tuple[pa.Table | None, dict[str, int] | None, int | None]:
    if hashlib.sha256(payload).hexdigest() != item.sha256 or len(payload) != item.size_bytes:
        raise ReleasePartError("a part differs from the manifest")
    try:
        parquet = pq.ParquetFile(io.BytesIO(payload))
        footer = parquet.schema_arrow.metadata or {}
        try:
            fingerprint = schema_fingerprint(parquet.schema_arrow)
        except KeyError as error:
            raise ReleasePartError("a part has a column type outside the release schemas") from error
        if (
            parquet.metadata.num_rows != item.row_count
            or parquet.metadata.num_row_groups != item.row_group_count
            or footer.get(FOOTER_TABLE_KEY) != table.table.encode("ascii")
            or footer.get(FOOTER_SCHEMA_MAJOR_KEY) != str(schema_major).encode("ascii")
            or footer.get(FOOTER_FINGERPRINT_KEY) != table.schema_fingerprint.encode("ascii")
            or fingerprint != table.schema_fingerprint
        ):
            raise ReleasePartError("a part's footer differs from the manifest")
        columns: list[str] = []
        if table.table == "works" or table.rows_of_deleted_works is not None:
            columns.append("work_id")
        if table.activity_state_counts is not None:
            columns.append("activity_state")
        if not set(columns) <= set(parquet.schema_arrow.names):
            raise ReleasePartError("a part lacks a column its counts need")
        if any(not parquet.schema_arrow.field(name).type.equals(pa.string()) for name in columns):
            raise ReleasePartError("a part's counted columns are not strings")
        data = parquet.read(columns=columns) if columns else None
    except pa.ArrowException as error:
        raise ReleasePartError("a part cannot be read as Parquet") from error
    activity = of_deleted = None
    if data is not None and table.activity_state_counts is not None:
        activity = _activity_counts(data, ReleasePartError)
    if data is not None and table.rows_of_deleted_works is not None:
        of_deleted = _rows_of(data, deleted_ids)
    return data, activity, of_deleted


def validate_release(
    silver: ObjectStore, release_id: str, *, schema_major: int = CANONICAL_SCHEMA_MAJOR
) -> ValidatedRelease:
    """Manifest plus every listed part: SHA-256, size, rows, row groups, footer, schema, counts.

    Schemas are checked against the fingerprints the manifest records, not the
    running code (which older schemas a reader accepts is DE-10's policy).
    """
    manifest, payload = _load_manifest(silver, release_id, schema_major)
    root = canonical_release_root(manifest.schema_major, manifest.release_id)
    deleted: list[str] = []
    deleted_ids = pa.array([], pa.string())
    for table in manifest.tables:
        activity = dict.fromkeys(_ACTIVITY_STATES, 0)
        of_deleted = 0
        for item in table.files:
            data, part_activity, part_of_deleted = _check_part(
                _read_part(silver, f"{root}/{item.path}"), table, item, manifest.schema_major, deleted_ids
            )
            for state, count in (part_activity or {}).items():
                activity[state] += count
            of_deleted += part_of_deleted or 0
            if table.table == "works" and data is not None:
                mask = pc.equal(data.column("activity_state"), CanonicalActivityState.DELETED.value)
                deleted.extend(data.column("work_id").filter(mask).to_pylist())
        if table.activity_state_counts is not None and activity != table.activity_state_counts:
            raise ReleasePartError("activity_state counts differ from the manifest")
        if table.rows_of_deleted_works is not None and of_deleted != table.rows_of_deleted_works:
            raise ReleasePartError("rows of tombstoned Works differ from the manifest")
        if table.table == "works":
            deleted_ids = pa.array(sorted(deleted), pa.string())
    return ValidatedRelease(manifest=manifest, manifest_sha256=hashlib.sha256(payload).hexdigest())


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _check_arguments(
    *,
    inputs: Sequence[ReleaseInputSpec],
    build_run_id: UUID,
    built_at: datetime,
    parent_release_id: str | None,
    rows_per_part: int,
    row_group_size: int,
    code_revision: str | None,
    works_decode_limits: JsonlDecodeLimits | None,
    deletion_decode_limits: CsvDeletionDecodeLimits | None,
) -> tuple[WriterSettings, DecodeLimits]:
    """Every argument check that needs no store call."""
    try:
        writer = WriterSettings.pinned(rows_per_part=rows_per_part, row_group_size=row_group_size)
    except ValidationError as error:
        raise ReleaseConfigError("invalid rows_per_part or row_group_size") from error
    if not isinstance(built_at, datetime) or built_at.tzinfo is None or built_at.utcoffset() is None:
        raise ReleaseConfigError("built_at must be timezone-aware")
    if built_at.microsecond != 0:
        raise ReleaseConfigError("built_at must have whole seconds")
    if not isinstance(build_run_id, UUID):
        raise ReleaseConfigError("build_run_id must be a UUID")
    if code_revision is not None and (not isinstance(code_revision, str) or not _REVISION.fullmatch(code_revision)):
        raise ReleaseConfigError("code_revision must be 40 lower-case hex digits")
    if parent_release_id is not None:
        try:
            validate_release_id(parent_release_id)
        except InvalidObjectKeyError as error:
            raise ReleaseConfigError("parent_release_id is not a release id") from error
    works_limits = JsonlDecodeLimits() if works_decode_limits is None else works_decode_limits
    deletion_limits = CsvDeletionDecodeLimits() if deletion_decode_limits is None else deletion_decode_limits
    if not isinstance(works_limits, JsonlDecodeLimits) or not isinstance(deletion_limits, CsvDeletionDecodeLimits):
        raise ReleaseConfigError("decode limits must be JsonlDecodeLimits and CsvDeletionDecodeLimits")
    check_release_input_specs(inputs)
    return writer, DecodeLimits(works=works_limits, works_deletions=deletion_limits)


def _check_parent(
    silver: ObjectStore, parent_release_id: str, built_at: datetime, inputs: Sequence[BronzeInput]
) -> None:
    try:
        parent = read_release_manifest(silver, parent_release_id, schema_major=CANONICAL_SCHEMA_MAJOR)
    except (ReleaseNotFoundError, ReleaseManifestError) as error:
        raise ReleaseParentError("parent release is not readable") from error
    if not parent.built_at < built_at:
        raise ReleaseParentError("parent release must be older than the new release")
    if parent.schema_fingerprints() != release_schema_fingerprints():
        raise ReleaseSchemaMismatchError("parent release schemas differ from this code")
    prefix = bronze_inputs_from_manifest(parent)
    if tuple(inputs[: len(prefix)]) != prefix:
        raise ReleaseParentError("parent release inputs must be the first inputs of the new release")


def _verify_listed_part(silver: ObjectStore, key: str, expected: ReleaseFile) -> None:
    """Store facts (or a re-read when the store reports none) must equal the rendered part."""
    try:
        stat = silver.stat(key)
        if stat is None:
            with silver.open(key) as handle:
                payload = handle.read()
            matches = len(payload) == expected.size_bytes and hashlib.sha256(payload).hexdigest() == expected.sha256
        else:
            matches = stat.size_bytes == expected.size_bytes and (
                stat.provenance is None or stat.provenance.sha256 == expected.sha256
            )
    except ObjectStoreError as error:
        raise ReleasePartError("a listed part is missing or incomplete") from error
    if not matches:
        raise ReleasePartError("a stored part differs from its rendered bytes")


def _refuse_unrendered_parts(silver: ObjectStore, root: str, written: Sequence[RenderedPart]) -> None:
    """The part after each table's last rendered part must be absent before the manifest is written.

    A resumed build whose code renders fewer parts than an interrupted attempt
    would otherwise complete a release beside parts it did not render.
    """
    for table_name in RELEASE_TABLE_NAMES:
        count = sum(1 for part in written if part.table == table_name)
        if count > MAX_PART_INDEX:
            continue
        key = f"{root}/{canonical_part_relative_path(table_name, count)}"
        try:
            if silver.stat(key) is None:  # a store without facts: open is the existence check
                with silver.open(key):
                    pass
        except ObjectNotFoundError:
            continue
        except IncompleteObjectError:
            pass
        raise ReleaseConflictError("the release directory holds parts this build did not render")


def build_canonical_release(
    *,
    bronze: ObjectStore,
    silver: ObjectStore,
    inputs: Sequence[ReleaseInputSpec],
    build_run_id: UUID,
    built_at: datetime,
    parent_release_id: str | None = None,
    rows_per_part: int = DEFAULT_ROWS_PER_PART,
    row_group_size: int = DEFAULT_ROW_GROUP_SIZE,
    code_revision: str | None = None,
    works_decode_limits: JsonlDecodeLimits | None = None,
    deletion_decode_limits: CsvDeletionDecodeLimits | None = None,
) -> BuiltRelease:
    """Replay ``inputs`` from Bronze and write one immutable full-snapshot release to Silver.

    Inputs must have strictly increasing UTC ``processed_at`` (the order the
    pipelines applied them; ``rebuild.release_inputs_from_source_files`` sorts
    SUCCESS control rows that way). Raises ``ReleaseConfigError``,
    ``ReleaseInputError``, ``ReleaseParentError``, ``ReleaseSchemaMismatchError``,
    ``ReleaseRebuildError``, ``ReleaseConflictError``, ``ReleaseManifestError`` or
    ``ReleasePartError``; other store errors propagate and leave the release
    invisible (no manifest).
    """
    writer, limits = _check_arguments(
        inputs=inputs,
        build_run_id=build_run_id,
        built_at=built_at,
        parent_release_id=parent_release_id,
        rows_per_part=rows_per_part,
        row_group_size=row_group_size,
        code_revision=code_revision,
        works_decode_limits=works_decode_limits,
        deletion_decode_limits=deletion_decode_limits,
    )
    built_at = built_at.astimezone(UTC)
    release_id = release_id_for_build(build_run_id, built_at)
    resolved = resolve_bronze_inputs(bronze, inputs)
    if parent_release_id is not None:
        _check_parent(silver, parent_release_id, built_at, resolved)
    tools = tool_versions()
    try:
        intent = ReleaseIntent(
            release_id=release_id,
            parent_release_id=parent_release_id,
            schema_major=CANONICAL_SCHEMA_MAJOR,
            build_run_id=build_run_id,
            built_at=built_at,
            code_version=CodeVersion(package_version=tools.package, revision=code_revision),
            tools=tools,
            writer=writer,
            decode_limits=limits,
            schema_fingerprints=release_schema_fingerprints(),
            inputs=resolved,
        )
    except ValidationError as error:
        raise ReleaseConfigError("the release request is invalid") from error
    intent_sha256 = intent.sha256
    key = canonical_manifest_key(CANONICAL_SCHEMA_MAJOR, release_id)
    root = canonical_release_root(CANONICAL_SCHEMA_MAJOR, release_id)

    existing = _read_manifest_bytes(silver, key)
    if existing is not None:
        stored = _parse_manifest(existing, release_id, CANONICAL_SCHEMA_MAJOR)
        if stored.intent_sha256 != intent_sha256:
            raise ReleaseConflictError("release id already holds a release built from another request")
        state = replay_bronze_inputs(
            bronze, resolved, works_limits=limits.works, deletion_limits=limits.works_deletions
        )
        rendered = [part.summary() for part in render_release_parts(state, writer)]
        manifest = _manifest(intent, intent_sha256, state, rendered)
        if dumps_release_manifest(manifest) != existing:
            raise ReleaseConflictError("release id already holds a different release")
        for table in manifest.tables:
            for item in table.files:
                _verify_listed_part(silver, f"{root}/{item.path}", item)
        return BuiltRelease(
            manifest=manifest,
            manifest_key=key,
            manifest_sha256=hashlib.sha256(existing).hexdigest(),
            outcome="existing_kept",
            parts_written=0,
        )

    state = replay_bronze_inputs(bronze, resolved, works_limits=limits.works, deletion_limits=limits.works_deletions)
    written: list[RenderedPart] = []
    for part in render_release_parts(state, writer):
        part_key = f"{root}/{part.file.path}"
        provenance = release_object_provenance(
            build_run_id=build_run_id,
            built_at=built_at,
            release_id=release_id,
            intent_sha256=intent_sha256,
            path=part.file.path,
            sha256=part.file.sha256,
        )
        try:
            silver.put_if_absent(part_key, io.BytesIO(part.payload), provenance)
        except ObjectConflictError as error:
            raise ReleaseConflictError("a release part already holds other content or intent") from error
        _verify_listed_part(silver, part_key, part.file)
        written.append(part.summary())
        del part
    _refuse_unrendered_parts(silver, root, written)
    manifest = _manifest(intent, intent_sha256, state, written)
    payload = dumps_release_manifest(manifest)
    try:
        silver.put_if_absent(key, io.BytesIO(payload), release_manifest_provenance(manifest, payload))
    except ObjectConflictError as error:
        raise ReleaseConflictError("release manifest already holds different content") from error
    return BuiltRelease(
        manifest=manifest,
        manifest_key=key,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        outcome="written",
        parts_written=len(written),
    )


# ---------------------------------------------------------------------------
# Reconciling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileReconciliation:
    table: str
    path: str
    expected_sha256: str
    actual_sha256: str | None
    expected_rows: int
    actual_rows: int | None

    @property
    def matches(self) -> bool:
        return self.expected_sha256 == self.actual_sha256 and self.expected_rows == self.actual_rows


@dataclass(frozen=True)
class ReconciliationReport:
    """Manifest value first, rebuilt value second, in every pair."""

    release_id: str
    manifest_sha256: str
    inputs_replayed: int
    files: tuple[FileReconciliation, ...]
    rebuilt_file_count: int
    table_rows: dict[str, tuple[int, int]]
    activity_state_counts: dict[str, tuple[dict[str, int] | None, dict[str, int] | None]]
    rows_of_deleted_works: dict[str, tuple[int | None, int | None]]
    barrier_count: tuple[int, int]
    barrier_only_count: tuple[int, int]
    input_counts: tuple[tuple[dict[str, Any], dict[str, Any]], ...]
    content_fingerprint: tuple[str, str]
    environment: dict[str, dict[str, Any]]
    mismatches: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def raise_for_mismatch(self) -> None:
        if self.mismatches:
            raise ReconciliationMismatchError(
                "rebuild from Bronze differs from the release: " + ", ".join(self.mismatches)
            )

    def summary(self) -> dict[str, Any]:
        """Value-free evidence: counts, digests and outcome; no keys or paths."""
        return {
            "release_id": self.release_id,
            "manifest_sha256": self.manifest_sha256,
            "passed": self.passed,
            "inputs_replayed": self.inputs_replayed,
            "files_compared": len(self.files),
            "files_matching": sum(1 for item in self.files if item.matches),
            "files_rebuilt": self.rebuilt_file_count,
            "table_rows": {name: list(pair) for name, pair in self.table_rows.items()},
            "barrier_count": list(self.barrier_count),
            "barrier_only_count": list(self.barrier_only_count),
            "content_fingerprint": list(self.content_fingerprint),
            "environment": self.environment,
            "mismatches": list(self.mismatches),
        }


def _check_environment(manifest: CanonicalReleaseManifest) -> None:
    if manifest.tools.pyarrow != pa.__version__:
        raise ReleaseEnvironmentMismatchError("byte reproduction needs the recorded PyArrow version")
    if manifest.writer.parquet_options != dict(PARQUET_WRITE_OPTIONS):
        raise ReleaseEnvironmentMismatchError("byte reproduction needs the recorded Parquet options")
    if (
        manifest.schema_major != CANONICAL_SCHEMA_MAJOR
        or manifest.schema_fingerprints() != release_schema_fingerprints()
    ):
        raise ReleaseSchemaMismatchError("release schemas differ from this code")


def reconcile_release(*, bronze: ObjectStore, silver: ObjectStore, release_id: str) -> ReconciliationReport:
    """Rebuild ``release_id`` from the Bronze objects its manifest lists and compare it with the release.

    Raises (cannot compare): ``ReleaseNotFoundError``, ``ReleaseManifestError``,
    ``ReleasePartError`` (release corrupt), ``ReleaseSchemaMismatchError``,
    ``ReleaseEnvironmentMismatchError`` (PyArrow or Parquet options differ),
    ``ReleaseInputError`` (Bronze input missing or changed) and
    ``ReleaseRebuildError`` (replay failed, raw line counts or barrier
    invariants broken). Returns a report; ``report.passed`` is False on any
    compared difference. Proves the release equals what its listed inputs
    produce; not that the list holds every input the control plane marked
    SUCCESS (DE-12).
    """
    validated = validate_release(silver, release_id)
    manifest = validated.manifest
    _check_environment(manifest)
    state = replay_bronze_inputs(
        bronze,
        bronze_inputs_from_manifest(manifest),
        works_limits=manifest.decode_limits.works,
        deletion_limits=manifest.decode_limits.works_deletions,
    )
    rendered = [part.summary() for part in render_release_parts(state, manifest.writer)]
    tables = _assemble_tables(rendered)
    mismatches: list[str] = []

    actual_files = {(part.table, part.file.path): part.file for part in rendered}
    files: list[FileReconciliation] = []
    for table in manifest.tables:
        for item in table.files:
            actual = actual_files.get((table.table, item.path))
            files.append(
                FileReconciliation(
                    table=table.table,
                    path=item.path,
                    expected_sha256=item.sha256,
                    actual_sha256=None if actual is None else actual.sha256,
                    expected_rows=item.row_count,
                    actual_rows=None if actual is None else actual.row_count,
                )
            )
    if len(actual_files) != len(files) or not all(item.matches for item in files):
        mismatches.append("part_files")
    pairs = list(zip(manifest.tables, tables, strict=True))
    table_rows = {e.table: (e.row_count, a.row_count) for e, a in pairs}
    if any(left != right for left, right in table_rows.values()):
        mismatches.append("table_row_counts")
    activity = {e.table: (e.activity_state_counts, a.activity_state_counts) for e, a in pairs}
    if any(left != right for left, right in activity.values()):
        mismatches.append("activity_state_counts")
    of_deleted = {
        e.table: (e.rows_of_deleted_works, a.rows_of_deleted_works)
        for e, a in pairs
        if e.table in RELATIONSHIP_TABLE_NAMES
    }
    if any(left != right for left, right in of_deleted.values()):
        mismatches.append("rows_of_deleted_works")
    rebuilt_barriers = tables[-1].row_count
    rebuilt_only = rebuilt_barriers - (tables[0].activity_state_counts or {}).get("DELETED", 0)
    if rebuilt_barriers != manifest.barrier_count:
        mismatches.append("barrier_count")
    if rebuilt_only != manifest.barrier_only_count:
        mismatches.append("barrier_only_count")
    input_counts = tuple(
        (item.counts.model_dump(), counts.model_dump())
        for item, counts in zip(manifest.inputs, state.counts, strict=True)
    )
    if any(left != right for left, right in input_counts):
        mismatches.append("input_outcome_counts")
    fingerprint = content_fingerprint(tables)
    if fingerprint != manifest.content_fingerprint:
        mismatches.append("content_fingerprint")
    current = tool_versions()
    return ReconciliationReport(
        release_id=manifest.release_id,
        manifest_sha256=validated.manifest_sha256,
        inputs_replayed=len(manifest.inputs),
        files=tuple(files),
        rebuilt_file_count=len(actual_files),
        table_rows=table_rows,
        activity_state_counts=activity,
        rows_of_deleted_works=of_deleted,
        barrier_count=(manifest.barrier_count, rebuilt_barriers),
        barrier_only_count=(manifest.barrier_only_count, rebuilt_only),
        input_counts=input_counts,
        content_fingerprint=(manifest.content_fingerprint, fingerprint),
        environment={
            "recorded": {
                "tools": manifest.tools.model_dump(mode="json"),
                "code_version": manifest.code_version.model_dump(mode="json"),
            },
            "current": {
                "tools": current.model_dump(mode="json"),
                "code_version": {"package_version": current.package, "revision": None},
            },
        },
        mismatches=tuple(mismatches),
    )


# ---------------------------------------------------------------------------
# Publication records
# ---------------------------------------------------------------------------


def write_publication_record(
    silver: ObjectStore, record: PublicationRecord
) -> Literal["written", "existing_kept"]:
    """Write one create-only publication record for a complete release.

    Call only after the DE-12 (#133) pointer commit: a record must never run
    ahead of the pointer. No production code calls this in DE-07. Finding the
    highest record needs an object listing ``ObjectStore`` does not offer
    (DE-12).
    """
    if not isinstance(record, PublicationRecord):
        raise PublicationRecordError("a publication record must be a PublicationRecord")
    _, manifest_payload = _load_manifest(silver, record.release_id, record.schema_major)
    if hashlib.sha256(manifest_payload).hexdigest() != record.manifest_sha256:
        raise PublicationRecordError("the record names a manifest with another SHA-256")
    key = canonical_publication_record_key(record.sequence, record.release_id)
    payload = dumps_publication_record(record)
    try:
        with silver.open(key) as handle:
            stored = handle.read()
    except ObjectNotFoundError:
        stored = None
    except ObjectStoreError as error:
        raise PublicationRecordError("the stored publication record is incomplete or unreadable") from error
    if stored is not None:
        if stored != payload:
            raise ReleaseConflictError("the publication record key already holds another record")
        return "existing_kept"
    try:
        silver.put_if_absent(key, io.BytesIO(payload), publication_record_provenance(record, payload))
    except ObjectConflictError as error:
        raise ReleaseConflictError("the publication record key already holds another record") from error
    return "written"


def read_publication_record(silver: ObjectStore, sequence: int, release_id: str) -> PublicationRecord:
    """Read and validate one record by its key; it must name the requested sequence and release."""
    key = canonical_publication_record_key(sequence, release_id)
    try:
        with silver.open(key) as handle:
            payload = handle.read()
    except ObjectStoreError as error:
        raise PublicationRecordError("the publication record is missing or unreadable") from error
    obj = _canonical_object(payload, PublicationRecordError)
    try:
        record = PublicationRecord.model_validate(obj)
    except ValidationError as error:
        raise PublicationRecordError("the publication record fails validation") from error
    if (record.sequence, record.release_id) != (sequence, release_id):
        raise PublicationRecordError("the publication record names another key")
    return record
