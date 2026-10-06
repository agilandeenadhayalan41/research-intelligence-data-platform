import builtins
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex import OpenAlexAssetMetadata, unique_assets


@pytest.fixture
def asset(repo_root: Path) -> OpenAlexAssetMetadata:
    return OpenAlexAssetMetadata.model_validate_json(
        (repo_root / "tests/fixtures/openalex_asset.json").read_text(encoding="utf-8")
    )


def test_synthetic_asset_metadata_and_source_asset_compatibility(
    asset: OpenAlexAssetMetadata,
) -> None:
    assert asset.snapshot_date == date(2025, 1, 15)
    assert asset.updated_date == date(2025, 1, 14)
    assert asset.to_source_asset() == SourceAsset(
        source="openalex", identifier=asset.asset_id, uri=asset.file_uri
    )


def test_unknown_optional_metadata_stays_unknown(asset: OpenAlexAssetMetadata) -> None:
    metadata = asset.model_dump()
    metadata.update(byte_size=None, record_count=None, updated_date=None)
    unknown = OpenAlexAssetMetadata.model_validate(metadata)

    assert unknown.byte_size is None
    assert unknown.record_count is None
    assert unknown.updated_date is None
    assert unknown.byte_size != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_date", 0),
        ("snapshot_date", 1736899200),
        ("updated_date", 1736812800),
    ],
)
def test_numeric_timestamps_are_not_calendar_dates(
    asset: OpenAlexAssetMetadata, field: str, value: int
) -> None:
    metadata = asset.model_dump()
    metadata[field] = value

    with pytest.raises(ValidationError):
        OpenAlexAssetMetadata.model_validate(metadata)


def test_date_fields_accept_date_objects_and_canonical_strings(
    asset: OpenAlexAssetMetadata,
) -> None:
    metadata = asset.model_dump()
    metadata.update(snapshot_date=date(2025, 1, 15), updated_date=date(2025, 1, 14))

    assert OpenAlexAssetMetadata.model_validate(metadata) == asset
    assert OpenAlexAssetMetadata.model_validate(
        {**metadata, "snapshot_date": "2025-01-15", "updated_date": "2025-01-14"}
    ) == asset


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "another-source"),
        ("snapshot_date", "2025-02-30"),
        ("snapshot_date", "2025-01-15T00:00:00Z"),
        ("entity", "Works"),
        ("file_uri", "https://openalex.org/data/jsonl/works/part_0000.gz"),
        ("file_uri", "******openalex/data/jsonl/works/part_0000.gz"),
        ("file_uri", "s3://openalex/data/jsonl/works/part_0000.gz?"),
        ("file_uri", "s3://openalex/data/jsonl/works/part_0000.gz#"),
        ("file_uri", "s3://openalex/data/parquet/works/part_0000.parquet"),
        ("file_uri", "s3://openalex/data/jsonl/authors/part_0000.gz"),
        ("byte_size", -1),
        ("byte_size", 1.5),
        ("record_count", -1),
        ("record_count", "7"),
        ("updated_date", "2025-01-14T00:00:00Z"),
        (
            "file_uri",
            "s3://openalex/data/jsonl/works/updated_date=2025-01-13/part_0000.gz",
        ),
    ],
)
def test_invalid_asset_metadata_is_rejected(
    asset: OpenAlexAssetMetadata, field: str, value: Any
) -> None:
    metadata = asset.model_dump()
    metadata[field] = value

    with pytest.raises(ValidationError):
        OpenAlexAssetMetadata.model_validate(metadata)


def test_identity_is_stable_for_metadata_reordering_and_independent_of_run_context(
    asset: OpenAlexAssetMetadata,
) -> None:
    changed_optional_values = asset.model_dump()
    changed_optional_values.update(byte_size=9999, record_count=None)
    other = OpenAlexAssetMetadata.model_validate(changed_optional_values)
    reversed_metadata = dict(reversed(list(asset.model_dump().items())))
    equivalent = OpenAlexAssetMetadata.model_validate(reversed_metadata)

    assert asset.asset_id == other.asset_id == equivalent.asset_id

    second_values = asset.model_dump()
    second_values["file_uri"] = second_values["file_uri"].replace(
        "part_0000.gz", "part_0001.gz"
    )
    second = OpenAlexAssetMetadata.model_validate(second_values)
    assert unique_assets([second, asset]) == unique_assets([asset, second])


def test_identity_changes_with_snapshot_date(asset: OpenAlexAssetMetadata) -> None:
    metadata = asset.model_dump()
    metadata["snapshot_date"] = "2025-01-16"

    assert OpenAlexAssetMetadata.model_validate(metadata).asset_id != asset.asset_id


def test_exact_duplicates_are_collapsed_and_conflicts_rejected(
    asset: OpenAlexAssetMetadata,
) -> None:
    assert unique_assets([asset, asset]) == (asset,)

    conflict_values = asset.model_dump()
    conflict_values["byte_size"] += 1
    conflict = OpenAlexAssetMetadata.model_validate(conflict_values)
    with pytest.raises(ValueError, match="conflicting metadata"):
        unique_assets([asset, conflict])


def test_asset_metadata_contract_has_no_network_or_file_write_side_effects(
    asset: OpenAlexAssetMetadata, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_side_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("metadata validation must not access services or write files")

    monkeypatch.setattr(socket, "socket", reject_side_effect)
    monkeypatch.setattr(builtins, "open", reject_side_effect)
    monkeypatch.setattr(Path, "write_bytes", reject_side_effect)
    monkeypatch.setattr(Path, "write_text", reject_side_effect)

    OpenAlexAssetMetadata.model_validate(asset.model_dump()).to_source_asset()
    unique_assets([asset])

    assert list(tmp_path.iterdir()) == []
