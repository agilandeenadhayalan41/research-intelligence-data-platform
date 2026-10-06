import builtins
import copy
import json
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from research_platform.sources.openalex import (
    OpenAlexAssetMetadata,
    parse_openalex_works_manifest,
)


def works_manifest() -> dict[str, Any]:
    return {
        "date": "2025-01-15",
        "format": "jsonl",
        "entity": "works",
        "record_count": 5,
        "content_length": 900,
        "files": [
            {
                "url": "s3://openalex/data/jsonl/works/updated_date=2025-01-14/part_0001.gz",
                "meta": {"content_length": 400, "record_count": 2},
            },
            {
                "url": "s3://openalex/data/jsonl/works/updated_date=2025-01-13/part_0000.gz",
                "meta": {"content_length": 500, "record_count": 3},
            },
        ],
    }


def test_parse_manifest_content_and_multiple_update_partitions() -> None:
    assets = parse_openalex_works_manifest(json.dumps(works_manifest()).encode())

    assert len(assets) == 2
    assert all(isinstance(asset, OpenAlexAssetMetadata) for asset in assets)
    assert {asset.snapshot_date for asset in assets} == {date(2025, 1, 15)}
    assert {asset.updated_date for asset in assets} == {
        date(2025, 1, 13),
        date(2025, 1, 14),
    }
    assert {asset.byte_size for asset in assets} == {400, 500}
    assert {asset.record_count for asset in assets} == {2, 3}


def test_valid_parquet_manifest_returns_exact_metadata() -> None:
    manifest = {
        "date": "2025-02-01",
        "format": "parquet",
        "entity": "works",
        "files": [
            {
                "url": "s3://openalex/data/parquet/works/updated_date=2025-01-31/part_0002.parquet",
                "meta": {"content_length": 2048, "record_count": 9},
            }
        ],
    }

    assert parse_openalex_works_manifest(manifest) == (
        OpenAlexAssetMetadata(
            source="openalex",
            snapshot_date=date(2025, 2, 1),
            entity="works",
            content_format="parquet",
            file_uri="s3://openalex/data/parquet/works/updated_date=2025-01-31/part_0002.parquet",
            updated_date=date(2025, 1, 31),
            byte_size=2048,
            record_count=9,
        ),
    )


def test_mapping_json_text_and_utf8_bytes_are_equivalent() -> None:
    manifest = works_manifest()
    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

    assert parse_openalex_works_manifest(manifest) == parse_openalex_works_manifest(
        encoded.decode("utf-8")
    )
    assert parse_openalex_works_manifest(manifest) == parse_openalex_works_manifest(encoded)


@pytest.mark.parametrize("content", ["{", b"\xff"])
def test_invalid_json_and_invalid_utf8_are_rejected(content: str | bytes) -> None:
    with pytest.raises(ValueError):
        parse_openalex_works_manifest(content)


def test_empty_manifest_validates_and_uses_caller_context() -> None:
    manifest = {"files": []}

    assert parse_openalex_works_manifest(
        manifest,
        snapshot_date="2025-01-15",
        entity="works",
        content_format="parquet",
    ) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_date", 1736899200),
        ("entity", "authors"),
        ("content_format", "csv"),
    ],
)
def test_empty_manifest_does_not_bypass_invalid_context(field: str, value: Any) -> None:
    context = {
        "snapshot_date": "2025-01-15",
        "entity": "works",
        "content_format": "jsonl",
    }
    context[field] = value

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(
            {"files": []},
            **context,  # type: ignore[arg-type]
        )


def test_missing_optional_file_metadata_remains_unknown() -> None:
    manifest = works_manifest()
    manifest["files"] = [
        {"url": "s3://openalex/data/jsonl/works/part_0000.gz"}
    ]

    (asset,) = parse_openalex_works_manifest(manifest)

    assert asset.byte_size is None
    assert asset.record_count is None
    assert asset.updated_date is None


def test_explicit_none_zero_and_partial_optional_metadata_are_distinct() -> None:
    manifest = works_manifest()
    manifest["files"] = [
        {
            "url": "s3://openalex/data/jsonl/works/part_0000.gz",
            "meta": {"content_length": None, "record_count": 0},
        },
        {
            "url": "s3://openalex/data/jsonl/works/part_0001.gz",
            "meta": {"content_length": 0},
        },
        {
            "url": "s3://openalex/data/jsonl/works/part_0002.gz",
            "meta": {"record_count": None},
        },
    ]

    assets = parse_openalex_works_manifest(manifest)
    by_uri = {asset.file_uri: asset for asset in assets}

    first = by_uri["s3://openalex/data/jsonl/works/part_0000.gz"]
    second = by_uri["s3://openalex/data/jsonl/works/part_0001.gz"]
    third = by_uri["s3://openalex/data/jsonl/works/part_0002.gz"]

    assert (first.byte_size, first.record_count) == (None, 0)
    assert (second.byte_size, second.record_count) == (0, None)
    assert (third.byte_size, third.record_count) == (None, None)


@pytest.mark.parametrize(
    ("field", "context_field", "context_value"),
    [
        ("entity", "entity", "works"),
        ("format", "content_format", "jsonl"),
    ],
)
@pytest.mark.parametrize("has_file", [False, True])
def test_present_null_entity_and_format_headers_are_rejected(
    field: str, context_field: str, context_value: str, has_file: bool
) -> None:
    manifest = works_manifest()
    manifest[field] = None
    if not has_file:
        manifest["files"] = []

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest, **{context_field: context_value})


def test_manifest_header_and_context_must_agree() -> None:
    with pytest.raises(ValueError, match="conflicts with context"):
        parse_openalex_works_manifest(works_manifest(), snapshot_date="2025-01-16")

    with pytest.raises(ValueError, match="conflicts with context"):
        parse_openalex_works_manifest(works_manifest(), content_format="parquet")


def test_missing_header_values_can_be_supplied_by_context() -> None:
    manifest = works_manifest()
    del manifest["date"]
    del manifest["entity"]
    del manifest["format"]

    assets = parse_openalex_works_manifest(
        manifest,
        snapshot_date="2025-01-15",
        entity="works",
        content_format="jsonl",
    )

    assert len(assets) == 2


def test_exact_duplicates_collapse_and_output_is_deterministic() -> None:
    manifest = works_manifest()
    manifest["files"] = [manifest["files"][0], manifest["files"][0], manifest["files"][1]]
    forward = parse_openalex_works_manifest(manifest)
    reversed_entries = {**manifest, "files": list(reversed(manifest["files"]))}
    reverse = parse_openalex_works_manifest(reversed_entries)

    assert forward == reverse
    assert len(forward) == 2
    assert [asset.asset_id for asset in forward] == sorted(
        asset.asset_id for asset in forward
    )


def test_conflicting_duplicate_entries_are_rejected() -> None:
    manifest = works_manifest()
    duplicate = dict(manifest["files"][0])
    duplicate["meta"] = {"content_length": 401, "record_count": 2}
    manifest["files"] = [manifest["files"][0], duplicate]

    with pytest.raises(ValueError, match="conflicting metadata"):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"files": {}},
        {"date": "2025-01-15", "format": "jsonl", "entity": "works"},
        {**works_manifest(), "files": [None]},
        {**works_manifest(), "files": [{"meta": {}}]},
        {**works_manifest(), "files": [{"url": 42}]},
        {**works_manifest(), "files": [{"url": "s3://openalex/data/jsonl/works/part.gz", "meta": []}]},
    ],
)
def test_malformed_manifest_structures_are_rejected(manifest: Any) -> None:
    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    "value",
    [
        0,
        1736899200,
        "2025-02-30",
        "2025-01-15T00:00:00Z",
    ],
)
def test_invalid_manifest_snapshot_dates_are_rejected(value: Any) -> None:
    manifest = works_manifest()
    manifest["date"] = value

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_length", -1),
        ("content_length", 1.5),
        ("content_length", "10"),
        ("content_length", True),
        ("record_count", -1),
        ("record_count", 1.5),
        ("record_count", "10"),
        ("record_count", False),
    ],
)
def test_invalid_aggregate_counts_and_sizes_are_rejected(
    field: str, value: Any
) -> None:
    manifest = works_manifest()
    manifest[field] = value

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_length", -1),
        ("content_length", 1.5),
        ("content_length", "10"),
        ("content_length", True),
        ("record_count", -1),
        ("record_count", 1.5),
        ("record_count", "10"),
        ("record_count", False),
    ],
)
def test_invalid_file_counts_and_sizes_are_rejected(field: str, value: Any) -> None:
    manifest = works_manifest()
    manifest["files"][0]["meta"][field] = value

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entity", "authors"),
        ("entity", "Works"),
        ("format", "csv"),
    ],
)
def test_unsupported_manifest_context_is_rejected(field: str, value: str) -> None:
    manifest = works_manifest()
    manifest[field] = value

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    "file_uri",
    [
        "https://openalex.org/data/jsonl/works/part_0000.gz",
        "s3://other/data/jsonl/works/part_0000.gz",
        "s3://openalex/data/jsonl/authors/part_0000.gz",
        "s3://openalex/data/parquet/works/part_0000.gz",
        "s3://openalex/data/jsonl/works/../part_0000.gz",
        "s3://openalex/data/jsonl/works/part_0000.gz?unsafe",
        "s3://openalex/data/jsonl/works/updated_date=2025-02-30/part_0000.gz",
        "s3://openalex/data/jsonl/works/updated_date=2025-01-13/updated_date=2025-01-14/part_0000.gz",
        "s3://[invalid/data/jsonl/works/part_0000.gz",
        "\x00s3://openalex/data/jsonl/works/part_0000.gz",
        "s3://openalex/data/jsonl/works/part_\x01_0000.gz",
    ],
)
def test_unsafe_or_inconsistent_uris_are_rejected(file_uri: str) -> None:
    manifest = works_manifest()
    manifest["files"] = [{"url": file_uri}]

    with pytest.raises(ValueError):
        parse_openalex_works_manifest(manifest)


@pytest.mark.parametrize(
    "manifest",
    [
        {**works_manifest(), "extra_manifest_field": "sensitive-value"},
        {**works_manifest(), "files": [{**works_manifest()["files"][0], "extra": "sensitive-value"}]},
        {
            **works_manifest(),
            "files": [
                {
                    "url": works_manifest()["files"][0]["url"],
                    "meta": {"record_count": 2, "extra": "sensitive-value"},
                }
            ],
        },
    ],
)
def test_unknown_fields_are_rejected_without_echoing_input(manifest: Any) -> None:
    with pytest.raises(ValueError) as error:
        parse_openalex_works_manifest(manifest)

    assert "sensitive-value" not in str(error.value)


def test_duplicate_json_object_keys_are_rejected() -> None:
    content = (
        '{"date":"2025-01-15","date":"2025-01-16","format":"jsonl",'
        '"entity":"works","files":[]}'
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        parse_openalex_works_manifest(content)


def test_parser_has_no_network_or_file_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_side_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("manifest parsing must not access services or write files")

    monkeypatch.setattr(socket, "socket", reject_side_effect)
    monkeypatch.setattr(builtins, "open", reject_side_effect)
    monkeypatch.setattr(Path, "write_bytes", reject_side_effect)
    monkeypatch.setattr(Path, "write_text", reject_side_effect)

    assert parse_openalex_works_manifest(works_manifest())
    assert list(tmp_path.iterdir()) == []


def test_parser_does_not_mutate_mapping_input() -> None:
    manifest = works_manifest()
    original = copy.deepcopy(manifest)

    parse_openalex_works_manifest(manifest)

    assert manifest == original
