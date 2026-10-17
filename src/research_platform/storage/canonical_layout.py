"""Silver canonical release keys, release ids and publication-record keys (ADR 0001 D1, DE-07).

Layout (one object per leaf directory, like ``openalex_layout``)::

    openalex/canonical/schema_major=<n>/release_id=<id>/<table>/part-<nnnnn>/part-<nnnnn>.parquet
    openalex/canonical/schema_major=<n>/release_id=<id>/manifest/manifest.json
    openalex/canonical/publications/<ssssssssss>-<id>/<ssssssssss>-<id>.json

ADR 0001 D1 writes ``<table>/part-<nnnnn>.parquet``, ``manifest.json`` at the
release root and ``publications/<seq>-<id>.json``. ``LocalObjectStore``
publishes one object per directory (the object's directory holds the content
plus ``provenance.json``), so two parts in one directory, or a manifest beside
part directories, cannot be created there. Each object therefore gets its own
leaf directory named after the ADR basename, and the manifest lives in
``manifest/``. Readers never glob: they resolve the manifest key from the
logical ``release_id`` and every part from the manifest's relative paths. Keys
hold no bucket name or URL, and ``canonical`` is not a Bronze entity, so Silver
keys never collide with raw-landing or landing-manifest keys.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from research_platform.storage.errors import InvalidObjectKeyError
from research_platform.storage.keys import validate_object_key

CANONICAL_ROOT: Final = "openalex/canonical"
MAX_RELEASE_ID_LEN: Final = 128  # = orchestration publication_version limit
MAX_PART_INDEX: Final = 99_999
MANIFEST_RELATIVE_PATH: Final = "manifest/manifest.json"
PUBLICATIONS_ROOT: Final = f"{CANONICAL_ROOT}/publications"
MAX_PUBLICATION_SEQUENCE: Final = 9_999_999_999
_RELEASE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$")
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RESERVED_TABLE_SEGMENTS: Final = frozenset({"manifest", "publications"})
_PART_PATH = re.compile(r"^([a-z][a-z0-9_]{0,62})/part-([0-9]{5})/part-\2\.parquet$")


def release_id_for_build(build_run_id: UUID, built_at: datetime) -> str:
    """``<UTC yyyymmddThhmmssZ>-<build run uuid hex>``: time-sortable, from the build run.

    ``built_at`` must be timezone-aware with whole seconds, so the id and
    ``built_at`` determine each other.
    """
    if not isinstance(build_run_id, UUID):
        raise InvalidObjectKeyError("build_run_id must be a UUID")
    if not isinstance(built_at, datetime) or built_at.tzinfo is None or built_at.utcoffset() is None:
        raise InvalidObjectKeyError("built_at must be a timezone-aware datetime")
    if built_at.microsecond != 0:
        raise InvalidObjectKeyError("built_at must have whole seconds")
    stamp = built_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return validate_release_id(f"{stamp}-{build_run_id.hex}")


def validate_release_id(release_id: str) -> str:
    if (
        not isinstance(release_id, str)
        or len(release_id) > MAX_RELEASE_ID_LEN
        or not _RELEASE_ID.fullmatch(release_id)
    ):
        raise InvalidObjectKeyError("release_id must be <yyyymmddThhmmssZ>-<32 hex>")
    return release_id


def _validate_schema_major(schema_major: int) -> int:
    if isinstance(schema_major, bool) or not isinstance(schema_major, int) or schema_major < 1:
        raise InvalidObjectKeyError("schema_major must be a positive integer")
    return schema_major


def canonical_manifest_path_from_root(schema_major: int, release_id: str) -> str:
    """The manifest path relative to ``CANONICAL_ROOT`` (used by publication records)."""
    return (
        f"schema_major={_validate_schema_major(schema_major)}/"
        f"release_id={validate_release_id(release_id)}/{MANIFEST_RELATIVE_PATH}"
    )


def canonical_release_root(schema_major: int, release_id: str) -> str:
    root = (
        f"{CANONICAL_ROOT}/schema_major={_validate_schema_major(schema_major)}/"
        f"release_id={validate_release_id(release_id)}"
    )
    validate_object_key(f"{root}/probe")
    return root


def canonical_part_relative_path(table: str, part_index: int) -> str:
    if not isinstance(table, str) or not _TABLE.fullmatch(table) or table in _RESERVED_TABLE_SEGMENTS:
        raise InvalidObjectKeyError("table must be a lower-case table name")
    if isinstance(part_index, bool) or not isinstance(part_index, int) or not 0 <= part_index <= MAX_PART_INDEX:
        raise InvalidObjectKeyError("part_index must be between 0 and 99999")
    name = f"part-{part_index:05d}"
    return f"{table}/{name}/{name}.parquet"


def canonical_part_key(schema_major: int, release_id: str, table: str, part_index: int) -> str:
    key = f"{canonical_release_root(schema_major, release_id)}/{canonical_part_relative_path(table, part_index)}"
    validate_object_key(key)
    return key


def canonical_manifest_key(schema_major: int, release_id: str) -> str:
    key = f"{CANONICAL_ROOT}/{canonical_manifest_path_from_root(schema_major, release_id)}"
    validate_object_key(key)
    return key


def canonical_publication_record_key(sequence: int, release_id: str) -> str:
    """``openalex/canonical/publications/<10-digit sequence>-<id>/<same>.json``."""
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or not 1 <= sequence <= MAX_PUBLICATION_SEQUENCE
    ):
        raise InvalidObjectKeyError("publication sequence must be between 1 and 9999999999")
    name = f"{sequence:010d}-{validate_release_id(release_id)}"
    key = f"{PUBLICATIONS_ROOT}/{name}/{name}.json"
    validate_object_key(key)
    return key


def parse_part_relative_path(path: str) -> tuple[str, int]:
    """Return ``(table, part_index)`` for a manifest path, refusing anything else."""
    match = _PART_PATH.fullmatch(path) if isinstance(path, str) else None
    if match is None or match[1] in _RESERVED_TABLE_SEGMENTS:
        raise InvalidObjectKeyError("not a canonical release part path")
    return match[1], int(match[2])
