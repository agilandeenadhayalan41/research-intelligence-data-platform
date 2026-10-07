"""OpenAlex immutable raw key layout tests."""

from datetime import date

import pytest

from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.storage.openalex_layout import (
    openalex_raw_object_key,
    openalex_raw_provenance_key,
    openalex_source_extension,
)


def _asset(**overrides: object) -> OpenAlexAssetMetadata:
    payload = {
        "source": "openalex",
        "snapshot_date": date(2024, 1, 15),
        "entity": "works",
        "file_uri": (
            "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part_000.gz"
        ),
        "byte_size": 10,
        "record_count": 1,
        "updated_date": date(2024, 1, 10),
        "content_format": "jsonl",
    }
    payload.update(overrides)
    return OpenAlexAssetMetadata.model_validate(payload)


def test_jsonl_gz_key_layout_and_extension() -> None:
    asset = _asset()
    key = openalex_raw_object_key(asset)
    assert openalex_source_extension(asset) == ".gz"
    assert key == (
        f"openalex/works/snapshot_date=2024-01-15/updated_date=2024-01-10/"
        f"{asset.asset_id}/source.gz"
    )
    assert openalex_raw_provenance_key(asset).endswith("/provenance.json")
    assert openalex_raw_provenance_key(asset).rsplit("/", 1)[0] == key.rsplit("/", 1)[0]


def test_parquet_extension_preserved() -> None:
    asset = _asset(
        content_format="parquet",
        file_uri="s3://openalex/data/parquet/works/updated_date=2024-01-10/part_000.parquet",
    )
    assert openalex_source_extension(asset) == ".parquet"
    assert openalex_raw_object_key(asset).endswith("/source.parquet")


def test_missing_updated_date_uses_none_partition() -> None:
    asset = _asset(
        updated_date=None,
        file_uri="s3://openalex/data/jsonl/works/part_000.gz",
    )
    key = openalex_raw_object_key(asset)
    assert "/updated_date=none/" in key
    assert key.count("/") == openalex_raw_object_key(
        _asset()
    ).count("/")


def test_key_uses_asset_id_not_raw_uri() -> None:
    asset = _asset()
    key = openalex_raw_object_key(asset)
    assert "s3:" not in key
    assert "openalex/data" not in key
    assert asset.asset_id in key
