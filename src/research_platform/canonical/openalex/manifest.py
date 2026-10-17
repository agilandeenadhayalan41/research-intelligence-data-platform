"""Silver canonical release manifest, publication record and schema fingerprints (ADR 0001 D1, DE-07).

A release is the 15 ``CANONICAL_SCHEMAS`` tables plus ``work_deletion_barriers``
(release-only: it is not added to ``CANONICAL_SCHEMAS``, so BigQuery contracts and
DDL are unchanged). ``manifest.json`` is written last, create-only, and is the only
reader entry point. It records the ordered Bronze inputs with the values a rebuild
pins, every part file with its SHA-256, size, rows and row groups, per-table counts
by ``activity_state`` and rows of tombstoned Works, barrier counts, the writer
settings with the full Parquet option set, the decode limits, tool versions, an
intent digest over everything the build was asked to do and a content fingerprint
over the part digests. It never holds a bucket name, project, URL, account or
local path.

The models stay internal until DE-32 (#153) publishes the consumer contract;
DE-10 (#131) decides when ``schema_major`` changes and which older schemas a
reader accepts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Literal
from uuid import UUID

import pyarrow as pa
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from research_platform.canonical.openalex.export import CANONICAL_TABLE_NAMES, PARQUET_WRITE_OPTIONS
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.canonical_layout import (
    MANIFEST_RELATIVE_PATH,
    MAX_PUBLICATION_SEQUENCE,
    canonical_manifest_key,
    canonical_manifest_path_from_root,
    canonical_part_relative_path,
    release_id_for_build,
    validate_release_id,
)
from research_platform.storage.openalex_layout import openalex_landing_manifest_key

RELEASE_MANIFEST_SCHEMA: Final = "openalex-canonical-release-manifest"
RELEASE_MANIFEST_VERSION: Final = 1
PUBLICATION_RECORD_SCHEMA: Final = "openalex-canonical-publication-record"
PUBLICATION_RECORD_VERSION: Final = 1
RELEASE_OBJECT_SOURCE: Final = "research-platform"
_RELEASE_URN: Final = "urn:research-platform:openalex-canonical-release"
_PUBLICATION_URN: Final = "urn:research-platform:openalex-canonical-publication"
# Validation context key: the caller checked ``intent_sha256`` on the stored JSON.
INTENT_CHECKED_ON_JSON: Final = "intent_checked_on_json"

# DE-10 (#131) decides when this changes; DE-07 only records it.
CANONICAL_SCHEMA_MAJOR: Final = 1

WORK_DELETION_BARRIERS_TABLE: Final = "work_deletion_barriers"
WORK_DELETION_BARRIERS_SCHEMA: Final = pa.schema(
    [
        pa.field("work_id", pa.string(), nullable=False),
        pa.field("deleted_date", pa.date32(), nullable=False),
    ]
)
RELEASE_TABLE_NAMES: Final[tuple[str, ...]] = (*CANONICAL_TABLE_NAMES, WORK_DELETION_BARRIERS_TABLE)
# The eight ``work_*`` tables keyed by ``work_id``: their rows of tombstoned Works
# keep ACTIVE lineage (deletions.py), so the manifest counts them separately.
RELATIONSHIP_TABLE_NAMES: Final[tuple[str, ...]] = tuple(
    name for name in CANONICAL_TABLE_NAMES if name.startswith("work_")
)

FOOTER_TABLE_KEY: Final = b"rp.table"
FOOTER_SCHEMA_MAJOR_KEY: Final = b"rp.schema_major"
FOOTER_FINGERPRINT_KEY: Final = b"rp.schema_fingerprint"

SOURCE_RIGHTS_POLICY: Final = "adr-0001-d12-a-metadata-only"
SOURCE_RIGHTS_REFERENCE: Final = "de-05-126-source-profile"

_SHA256 = r"^[a-f0-9]{64}$"
# A release input asset id is one safe object-key segment.
ASSET_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
MAX_ASSET_ID_LENGTH: Final = 128

# Project-owned type tokens: a fingerprint never depends on PyArrow's type text,
# so a PyArrow upgrade cannot make an older manifest's fingerprints unreadable. A
# type outside this table needs a new token (a DE-10 decision). Pinned with
# ``manifest_version`` 1.
_TYPE_TOKENS: Final[tuple[tuple[pa.DataType, str], ...]] = (
    (pa.string(), "string"),
    (pa.int32(), "int32"),
    (pa.int64(), "int64"),
    (pa.bool_(), "bool"),
    (pa.float64(), "float64"),
    (pa.date32(), "date32"),
    (pa.timestamp("us", tz="UTC"), "timestamp_us_utc"),
)


def canonical_json(value: Any) -> str:
    """Sorted keys, compact separators, ASCII: the one JSON form DE-07 hashes and stores."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def release_table_schema(table: str) -> pa.Schema:
    if table == WORK_DELETION_BARRIERS_TABLE:
        return WORK_DELETION_BARRIERS_SCHEMA
    schema = CANONICAL_SCHEMAS.get(table)
    if schema is None:
        raise KeyError(table)
    return schema


def _type_token(data_type: Any) -> str:
    for arrow_type, token in _TYPE_TOKENS:
        if data_type.equals(arrow_type):
            return token
    raise KeyError("Arrow type has no schema fingerprint token")


def schema_fingerprint(schema: Any) -> str:
    """SHA-256 of the ordered (name, nullable, type token) list; schema metadata ignored."""
    fields = [
        {"name": field.name, "nullable": bool(field.nullable), "type": _type_token(field.type)}
        for field in schema
    ]
    return _sha256_text(canonical_json(fields))


def release_schema_fingerprints() -> dict[str, str]:
    return {name: schema_fingerprint(release_table_schema(name)) for name in RELEASE_TABLE_NAMES}


def footer_metadata(table: str) -> dict[bytes, bytes]:
    """Release-independent Parquet key-value metadata (the same bytes in every release)."""
    return {
        FOOTER_TABLE_KEY: table.encode("ascii"),
        FOOTER_SCHEMA_MAJOR_KEY: str(CANONICAL_SCHEMA_MAJOR).encode("ascii"),
        FOOTER_FINGERPRINT_KEY: schema_fingerprint(release_table_schema(table)).encode("ascii"),
    }


def parquet_options_sha256(options: dict[str, str | bool]) -> str:
    return _sha256_text(canonical_json(options))


def _is_utc(value: datetime) -> bool:
    return value.utcoffset() == timedelta(0)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WorksInputCounts(_Frozen):
    kind: Literal["works"] = "works"
    raw_lines: int = Field(strict=True, ge=0)
    records: int = Field(strict=True, ge=0)
    inserted: int = Field(strict=True, ge=0)
    identical: int = Field(strict=True, ge=0)
    replaced: int = Field(strict=True, ge=0)
    stale: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def balances(self) -> WorksInputCounts:
        if self.records != self.raw_lines:
            raise ValueError("decoded records must equal the independent raw line count")
        if self.records != self.inserted + self.identical + self.replaced + self.stale:
            raise ValueError("works input outcome counts must sum to records")
        return self


class DeletionInputCounts(_Frozen):
    kind: Literal["works-deletions"] = "works-deletions"
    raw_lines: int = Field(strict=True, ge=0)
    rows_seen: int = Field(strict=True, ge=0)
    unique_ids: int = Field(strict=True, ge=0)
    duplicates: int = Field(strict=True, ge=0)
    deleted: int = Field(strict=True, ge=0)
    already_deleted: int = Field(strict=True, ge=0)
    unknown: int = Field(strict=True, ge=0)
    stale: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def balances(self) -> DeletionInputCounts:
        if self.rows_seen != self.raw_lines:
            raise ValueError("decoded rows must equal the independent raw line count")
        if self.rows_seen != self.unique_ids + self.duplicates:
            raise ValueError("deletion rows must equal unique ids plus duplicates")
        if self.unique_ids != self.deleted + self.already_deleted + self.unknown + self.stale:
            raise ValueError("deletion outcomes must sum to unique ids")
        return self


class ReleaseInputIdentity(_Frozen):
    """One Bronze input, in application order: what a build names and a rebuild pins."""

    sequence: int = Field(strict=True, ge=0)
    entity: Literal["works", "works-deletions"]
    asset_id: str = Field(min_length=1, max_length=MAX_ASSET_ID_LENGTH, pattern=ASSET_ID_PATTERN)
    run_id: UUID
    processed_at: AwareDatetime
    landing_manifest_key: str = Field(min_length=1)
    landing_manifest_sha256: str = Field(pattern=_SHA256)
    raw_object_key: str = Field(min_length=1)
    raw_sha256: str = Field(pattern=_SHA256)
    raw_size_bytes: int = Field(strict=True, ge=0)
    snapshot_date: date
    updated_date: date | None

    @model_validator(mode="after")
    def consistent_identity(self) -> ReleaseInputIdentity:
        if not _is_utc(self.processed_at):
            raise ValueError("processed_at must be UTC")
        if self.landing_manifest_key != openalex_landing_manifest_key(self.entity, self.run_id, self.asset_id):
            raise ValueError("landing_manifest_key must derive from the input identity")
        return self


class ReleaseInput(ReleaseInputIdentity):
    """A listed input plus its replay outcome counts."""

    counts: WorksInputCounts | DeletionInputCounts = Field(discriminator="kind")

    @model_validator(mode="after")
    def counts_match_entity(self) -> ReleaseInput:
        if self.counts.kind != self.entity:
            raise ValueError("input counts kind must match the entity")
        return self


class ReleaseFile(_Frozen):
    path: str = Field(min_length=1)
    part_index: int = Field(strict=True, ge=0)
    sha256: str = Field(pattern=_SHA256)
    size_bytes: int = Field(strict=True, gt=0)
    row_count: int = Field(strict=True, ge=0)
    row_group_count: int = Field(strict=True, ge=0)


class ReleaseTable(_Frozen):
    table: str
    schema_fingerprint: str = Field(pattern=_SHA256)
    row_count: int = Field(strict=True, ge=0)
    activity_state_counts: dict[Literal["ACTIVE", "DELETED"], Annotated[int, Field(strict=True, ge=0)]] | None
    rows_of_deleted_works: int | None = Field(strict=True, ge=0)
    files: tuple[ReleaseFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> ReleaseTable:
        if self.table not in RELEASE_TABLE_NAMES:
            raise ValueError("unknown release table")
        for index, item in enumerate(self.files):
            if item.part_index != index or item.path != canonical_part_relative_path(self.table, index):
                raise ValueError("part files must be contiguous from part 0 with layout paths")
        if self.row_count != sum(item.row_count for item in self.files):
            raise ValueError("table row_count must equal the sum of its parts")
        if self.table == WORK_DELETION_BARRIERS_TABLE:
            if self.activity_state_counts is not None:
                raise ValueError("the barrier table has no activity_state")
        elif (
            self.activity_state_counts is None
            or set(self.activity_state_counts) != {"ACTIVE", "DELETED"}
            or any(count < 0 for count in self.activity_state_counts.values())
            or sum(self.activity_state_counts.values()) != self.row_count
        ):
            raise ValueError("activity_state counts must cover ACTIVE and DELETED and sum to row_count")
        if self.table in RELATIONSHIP_TABLE_NAMES:
            if self.rows_of_deleted_works is None or self.rows_of_deleted_works > self.row_count:
                raise ValueError("relationship tables count their rows of tombstoned Works")
        elif self.rows_of_deleted_works is not None:
            raise ValueError("only relationship tables count rows of tombstoned Works")
        return self


class WriterSettings(_Frozen):
    """Part sizing and the full Parquet option set; plain types keep old manifests readable."""

    rows_per_part: int = Field(strict=True, ge=1)
    row_group_size: int = Field(strict=True, ge=1)
    parquet_options: dict[str, str | bool]
    parquet_options_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def consistent(self) -> WriterSettings:
        if self.row_group_size > self.rows_per_part:
            raise ValueError("row_group_size must not exceed rows_per_part")
        if self.parquet_options_sha256 != parquet_options_sha256(self.parquet_options):
            raise ValueError("parquet_options_sha256 does not match parquet_options")
        return self

    @classmethod
    def pinned(cls, *, rows_per_part: int, row_group_size: int) -> WriterSettings:
        """Settings with the option set ``export.py`` pins (the options the writer uses)."""
        options = dict(PARQUET_WRITE_OPTIONS)
        return cls(
            rows_per_part=rows_per_part,
            row_group_size=row_group_size,
            parquet_options=options,
            parquet_options_sha256=parquet_options_sha256(options),
        )


class DecodeLimits(_Frozen):
    works: JsonlDecodeLimits
    works_deletions: CsvDeletionDecodeLimits


class ToolVersions(_Frozen):
    package: str = Field(min_length=1)
    python: str = Field(min_length=1)
    pyarrow: str = Field(min_length=1)
    pydantic: str = Field(min_length=1)


class CodeVersion(_Frozen):
    """``revision`` null means unrecorded; a build-time revision mechanism is DE-27 (#148)."""

    package_version: str = Field(min_length=1)
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")


class SourceRights(_Frozen):
    """A reference, not a verification: OpenAlex terms of use are recorded by DE-05, not verified."""

    policy: Literal["adr-0001-d12-a-metadata-only"] = SOURCE_RIGHTS_POLICY
    rights_reference: Literal["de-05-126-source-profile"] = SOURCE_RIGHTS_REFERENCE
    abstracts_included: Literal[False] = False


def _check_input_order(inputs: tuple[ReleaseInputIdentity, ...]) -> None:
    if tuple(item.sequence for item in inputs) != tuple(range(len(inputs))):
        raise ValueError("input sequence must be contiguous from 0")
    if len({item.asset_id for item in inputs}) != len(inputs):
        raise ValueError("an asset may appear only once in a release")
    for earlier, later in zip(inputs, inputs[1:]):
        if not earlier.processed_at < later.processed_at:
            raise ValueError("inputs must have strictly increasing processed_at")


class _ReleaseHeader(_Frozen):
    manifest_schema: Literal["openalex-canonical-release-manifest"] = RELEASE_MANIFEST_SCHEMA
    manifest_version: Literal[1] = RELEASE_MANIFEST_VERSION
    source: Literal["openalex"] = "openalex"
    storage_role: Literal["silver"] = "silver"
    release_id: str
    parent_release_id: str | None
    schema_major: int = Field(strict=True, ge=1)
    build_run_id: UUID
    built_at: AwareDatetime
    code_version: CodeVersion
    tools: ToolVersions
    writer: WriterSettings
    decode_limits: DecodeLimits
    source_rights: SourceRights = SourceRights()

    @field_validator("release_id", "parent_release_id")
    @classmethod
    def valid_release_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_release_id(value)

    @model_validator(mode="after")
    def derived_release_id(self) -> _ReleaseHeader:
        if not _is_utc(self.built_at) or self.built_at.microsecond != 0:
            raise ValueError("built_at must be UTC with whole seconds")
        if self.release_id != release_id_for_build(self.build_run_id, self.built_at):
            raise ValueError("release_id must derive from build_run_id and built_at")
        if self.parent_release_id is not None and not self.parent_release_id < self.release_id:
            raise ValueError("parent_release_id must sort before release_id")
        return self


class ReleaseIntent(_ReleaseHeader):
    """Everything a build is asked to do; nothing the replay or rendering computes."""

    schema_fingerprints: dict[str, str]
    inputs: tuple[ReleaseInputIdentity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_request(self) -> ReleaseIntent:
        if tuple(self.schema_fingerprints) != RELEASE_TABLE_NAMES:
            raise ValueError("schema_fingerprints must list every release table once in order")
        _check_input_order(self.inputs)
        return self

    @property
    def sha256(self) -> str:
        return _sha256_text(canonical_json(self.model_dump(mode="json")))


class CanonicalReleaseManifest(_ReleaseHeader):
    inputs: tuple[ReleaseInput, ...] = Field(min_length=1)
    input_order: Literal["processed_at"] = "processed_at"
    tables: tuple[ReleaseTable, ...]
    barrier_count: int = Field(strict=True, ge=0)
    barrier_only_count: int = Field(strict=True, ge=0)
    intent_sha256: str = Field(pattern=_SHA256)
    content_fingerprint: str = Field(pattern=_SHA256)
    # DE-11 (#132) builds change sets; until then a release carries none (ADR 0001 D1).
    change_set: None = None

    @model_validator(mode="after")
    def consistent(self, info: ValidationInfo) -> CanonicalReleaseManifest:
        _check_input_order(self.inputs)
        if tuple(item.table for item in self.tables) != RELEASE_TABLE_NAMES:
            raise ValueError("tables must list every release table once in order")
        if self.barrier_count != self.table(WORK_DELETION_BARRIERS_TABLE).row_count:
            raise ValueError("barrier_count must equal the barrier table rows")
        if self.barrier_only_count != self.barrier_count - self.deleted_work_count:
            raise ValueError("barrier_only_count must equal barriers minus tombstoned Works")
        if self.content_fingerprint != content_fingerprint(self.tables):
            raise ValueError("content_fingerprint does not match the files")
        # A stored manifest's digest is checked on its parsed JSON (``stored_intent_sha256``),
        # so a pydantic serializer change cannot make an older manifest unreadable.
        intent_checked = bool((info.context or {}).get(INTENT_CHECKED_ON_JSON))
        if not intent_checked and self.intent_sha256 != self.intent().sha256:
            raise ValueError("intent_sha256 does not match the build request")
        return self

    @property
    def key(self) -> str:
        return canonical_manifest_key(self.schema_major, self.release_id)

    @property
    def deleted_work_count(self) -> int:
        counts = self.table("works").activity_state_counts or {}
        return counts.get("DELETED", 0)

    def table(self, name: str) -> ReleaseTable:
        for item in self.tables:
            if item.table == name:
                return item
        raise KeyError(name)

    def schema_fingerprints(self) -> dict[str, str]:
        return {item.table: item.schema_fingerprint for item in self.tables}

    def intent(self) -> ReleaseIntent:
        header = {name: getattr(self, name) for name in _ReleaseHeader.model_fields}
        return ReleaseIntent(
            **header,
            schema_fingerprints=self.schema_fingerprints(),
            inputs=tuple(
                ReleaseInputIdentity.model_validate(item.model_dump(exclude={"counts"}))
                for item in self.inputs
            ),
        )


def stored_intent_sha256(obj: Mapping[str, Any]) -> str:
    """The intent digest of a stored manifest from its stdlib-parsed JSON, without the models.

    Equal to ``ReleaseIntent.sha256`` of the manifest that was written; ``KeyError``
    or ``TypeError`` when the object lacks the fields.
    """
    intent = {name: obj[name] for name in _ReleaseHeader.model_fields}
    intent["schema_fingerprints"] = {table["table"]: table["schema_fingerprint"] for table in obj["tables"]}
    intent["inputs"] = [{key: value for key, value in item.items() if key != "counts"} for item in obj["inputs"]]
    return _sha256_text(canonical_json(intent))


def content_fingerprint(tables: tuple[ReleaseTable, ...]) -> str:
    """SHA-256 over the ordered per-table part digests: equal data and writer settings, equal value."""
    payload = [[item.table, [part.sha256 for part in item.files]] for item in tables]
    return _sha256_text(canonical_json(payload))


def dumps_release_manifest(manifest: CanonicalReleaseManifest) -> bytes:
    """Canonical bytes: sorted keys, compact separators, ASCII, trailing newline."""
    return f"{canonical_json(manifest.model_dump(mode='json'))}\n".encode("utf-8")


def release_object_provenance(
    *, build_run_id: UUID, built_at: datetime, release_id: str, intent_sha256: str, path: str, sha256: str
) -> IngestionProvenance:
    """Provenance of one part or the manifest; binds the object to the build's intent digest."""
    return IngestionProvenance.model_validate(
        {
            "run_id": build_run_id,
            "source": RELEASE_OBJECT_SOURCE,
            "source_uri": f"{_RELEASE_URN}:{release_id}:{intent_sha256}:{path}",
            "retrieved_at": built_at,
            "sha256": sha256,
        }
    )


def release_manifest_provenance(manifest: CanonicalReleaseManifest, payload: bytes) -> IngestionProvenance:
    return release_object_provenance(
        build_run_id=manifest.build_run_id,
        built_at=manifest.built_at,
        release_id=manifest.release_id,
        intent_sha256=manifest.intent_sha256,
        path=MANIFEST_RELATIVE_PATH,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class PublicationRecord(_Frozen):
    """One publication of a complete release (ADR 0001 D1); written only after the DE-12 pointer moves."""

    record_schema: Literal["openalex-canonical-publication-record"] = PUBLICATION_RECORD_SCHEMA
    record_version: Literal[1] = PUBLICATION_RECORD_VERSION
    sequence: int = Field(strict=True, ge=1, le=MAX_PUBLICATION_SEQUENCE)
    release_id: str
    schema_major: int = Field(strict=True, ge=1)
    manifest_path: str
    manifest_sha256: str = Field(pattern=_SHA256)
    # A record exists only after a pointer move, which requires the gates to pass.
    quality_gate_outcome: Literal["PASSED"] = "PASSED"
    published_at: AwareDatetime
    previous_release_id: str | None
    publication_run_id: UUID

    @field_validator("release_id", "previous_release_id")
    @classmethod
    def valid_release_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_release_id(value)

    @model_validator(mode="after")
    def consistent(self) -> PublicationRecord:
        if self.manifest_path != canonical_manifest_path_from_root(self.schema_major, self.release_id):
            raise ValueError("manifest_path must be the release manifest path under openalex/canonical")
        if not _is_utc(self.published_at):
            raise ValueError("published_at must be UTC")
        if self.previous_release_id == self.release_id:
            raise ValueError("previous_release_id must differ from release_id")
        return self


def dumps_publication_record(record: PublicationRecord) -> bytes:
    return f"{canonical_json(record.model_dump(mode='json'))}\n".encode("utf-8")


def publication_record_provenance(record: PublicationRecord, payload: bytes) -> IngestionProvenance:
    return IngestionProvenance.model_validate(
        {
            "run_id": record.publication_run_id,
            "source": RELEASE_OBJECT_SOURCE,
            "source_uri": f"{_PUBLICATION_URN}:{record.sequence:010d}-{record.release_id}",
            "retrieved_at": record.published_at,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
