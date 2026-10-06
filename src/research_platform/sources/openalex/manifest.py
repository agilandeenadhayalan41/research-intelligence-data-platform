"""Pure parsing for caller-provided current-layout OpenAlex Works manifests."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from research_platform.sources.openalex.metadata import (
    OpenAlexAssetMetadata,
    unique_assets,
)

_MANIFEST_FIELDS = {
    "date",
    "format",
    "entity",
    "record_count",
    "content_length",
    "files",
}
_FILE_FIELDS = {"url", "meta"}
_FILE_METADATA_FIELDS = {"content_length", "record_count"}
_MISSING = object()


def parse_openalex_works_manifest(
    content: str | bytes | Mapping[str, Any],
    *,
    snapshot_date: date | str | None = None,
    entity: str | None = None,
    content_format: str | None = None,
) -> tuple[OpenAlexAssetMetadata, ...]:
    """Parse one current-layout Works manifest without accessing external resources.

    ``content`` is JSON text/UTF-8 bytes or an already decoded mapping, never a path
    or URL. The manifest's ``date``, ``entity``, and ``format`` fields supply asset
    context; matching keyword arguments may provide omitted values or assert that
    the manifest context is expected. Conflicts are rejected. The file URL supplies
    ``file_uri`` and, when present, its unambiguous ``updated_date`` partition.
    Per-file ``meta.content_length`` and ``meta.record_count`` supply size/count;
    absent values remain ``None``. Results use the contract's stable identity,
    duplicate collapsing, and identity-sorted order.
    """
    manifest = _decode_content(content)
    _check_mapping_fields(manifest, _MANIFEST_FIELDS)

    files = manifest.get("files", _MISSING)
    if not isinstance(files, list):
        raise ValueError("OpenAlex Works manifest files must be a list")

    _validate_optional_count(manifest, "record_count")
    _validate_optional_count(manifest, "content_length")

    resolved_date = _resolve_snapshot_date(manifest, snapshot_date)
    resolved_entity = _resolve_entity(manifest, entity)
    resolved_format = _resolve_content_format(manifest, content_format)

    assets: list[OpenAlexAssetMetadata] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ValueError("OpenAlex Works manifest file entries must be objects")
        _check_mapping_fields(entry, _FILE_FIELDS)

        file_uri = entry.get("url", _MISSING)
        if not isinstance(file_uri, str):
            raise ValueError("OpenAlex Works manifest file URL must be a string")

        raw_metadata = entry.get("meta", {})
        if raw_metadata is None:
            raw_metadata = {}
        if not isinstance(raw_metadata, Mapping):
            raise ValueError("OpenAlex Works manifest file metadata must be an object")
        _check_mapping_fields(raw_metadata, _FILE_METADATA_FIELDS)
        byte_size = _optional_count(raw_metadata, "content_length")
        record_count = _optional_count(raw_metadata, "record_count")

        metadata = {
            "source": "openalex",
            "snapshot_date": resolved_date,
            "entity": resolved_entity,
            "content_format": resolved_format,
            "file_uri": file_uri,
            "updated_date": _updated_date_from_uri(file_uri),
            "byte_size": byte_size,
            "record_count": record_count,
        }
        try:
            assets.append(OpenAlexAssetMetadata.model_validate(metadata))
        except ValidationError:
            raise ValueError("invalid OpenAlex Works manifest file metadata") from None

    return unique_assets(assets)


def _decode_content(content: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(content, Mapping):
        return content
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("OpenAlex Works manifest must be UTF-8 JSON") from None
    if not isinstance(content, str):
        raise ValueError("OpenAlex Works manifest must be JSON text or an object")

    try:
        decoded = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("OpenAlex Works manifest contains invalid JSON") from None
    if not isinstance(decoded, Mapping):
        raise ValueError("OpenAlex Works manifest must be a JSON object")
    return decoded


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise ValueError


def _check_mapping_fields(mapping: Mapping[str, Any], allowed: set[str]) -> None:
    if any(not isinstance(key, str) or key not in allowed for key in mapping):
        raise ValueError("OpenAlex Works manifest contains an unsupported field")


def _resolve_snapshot_date(
    manifest: Mapping[str, Any], context_date: date | str | None
) -> date:
    manifest_value = manifest.get("date", _MISSING)
    if manifest_value is _MISSING and context_date is None:
        raise ValueError("OpenAlex Works manifest requires a snapshot date")
    parsed_manifest = _calendar_date(manifest_value) if manifest_value is not _MISSING else None
    parsed_context = _calendar_date(context_date) if context_date is not None else None
    if parsed_manifest is not None and parsed_context is not None:
        if parsed_manifest != parsed_context:
            raise ValueError("OpenAlex Works manifest snapshot date conflicts with context")
    if parsed_manifest is not None:
        return parsed_manifest
    if parsed_context is None:
        raise ValueError("OpenAlex Works manifest requires a snapshot date")
    return parsed_context


def _calendar_date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("OpenAlex Works manifest dates must be calendar dates")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("OpenAlex Works manifest dates must use YYYY-MM-DD") from None
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("OpenAlex Works manifest dates must use YYYY-MM-DD")


def _resolve_entity(manifest: Mapping[str, Any], context_entity: str | None) -> str:
    manifest_entity = manifest.get("entity", _MISSING)
    for value in (manifest_entity, context_entity):
        if value is not _MISSING and value is not None:
            if not isinstance(value, str) or value != "works":
                raise ValueError("only the OpenAlex works entity is supported")
    if manifest_entity is _MISSING or manifest_entity is None:
        manifest_entity = context_entity
    elif context_entity is not None and manifest_entity != context_entity:
        raise ValueError("OpenAlex Works manifest entity conflicts with context")
    if manifest_entity != "works":
        raise ValueError("OpenAlex Works manifest requires the works entity")
    return "works"


def _resolve_content_format(
    manifest: Mapping[str, Any], context_format: str | None
) -> str:
    manifest_format = manifest.get("format", _MISSING)
    for value in (manifest_format, context_format):
        if value is not _MISSING and value is not None:
            if not isinstance(value, str) or value not in {"jsonl", "parquet"}:
                raise ValueError("unsupported OpenAlex Works manifest format")
    if manifest_format is _MISSING or manifest_format is None:
        manifest_format = context_format
    elif context_format is not None and manifest_format != context_format:
        raise ValueError("OpenAlex Works manifest format conflicts with context")
    if manifest_format not in {"jsonl", "parquet"}:
        raise ValueError("OpenAlex Works manifest requires a supported format")
    return manifest_format


def _validate_optional_count(mapping: Mapping[str, Any], field: str) -> None:
    _optional_count(mapping, field)


def _optional_count(mapping: Mapping[str, Any], field: str) -> int | None:
    value = mapping.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("OpenAlex Works manifest counts and sizes must be non-negative integers")
    return value


def _updated_date_from_uri(file_uri: str) -> date | None:
    if not isinstance(file_uri, str):
        return None
    try:
        parts = urlsplit(file_uri).path.split("/")
    except ValueError:
        raise ValueError("OpenAlex Works manifest file URI is invalid") from None
    partitions = [part.removeprefix("updated_date=") for part in parts if part.startswith("updated_date=")]
    if not partitions:
        return None
    if len(partitions) != 1:
        raise ValueError("OpenAlex Works manifest URI has ambiguous updated_date partitions")
    return _calendar_date(partitions[0])
