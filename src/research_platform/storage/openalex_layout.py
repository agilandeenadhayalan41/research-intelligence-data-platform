"""Deterministic OpenAlex immutable raw landing keys."""

from __future__ import annotations

from research_platform.sources.openalex.deletion_metadata import (
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.storage.keys import validate_object_key


def openalex_source_extension(metadata: OpenAlexAssetMetadata) -> str:
    """Return the actual source-byte extension for an OpenAlex asset.

    JSONL snapshot objects are gzip-compressed (``.gz``). Genuine Parquet objects
    use ``.parquet``. The immutable raw layer never converts between formats.
    """
    if metadata.content_format == "jsonl":
        return ".gz"
    return ".parquet"


def openalex_raw_object_key(metadata: OpenAlexAssetMetadata) -> str:
    """Build the content key for one OpenAlex raw landing object.

    Layout::

        openalex/<entity>/snapshot_date=YYYY-MM-DD/updated_date=YYYY-MM-DD|<none>/
            <asset-id>/source.<actual-extension>

    ``updated_date=none`` is used when the asset has no updated_date partition so
    the hierarchy always has the same depth. The key is validated before return.
    """
    updated = (
        metadata.updated_date.isoformat()
        if metadata.updated_date is not None
        else "none"
    )
    key = (
        f"openalex/{metadata.entity}/"
        f"snapshot_date={metadata.snapshot_date.isoformat()}/"
        f"updated_date={updated}/"
        f"{metadata.asset_id}/"
        f"source{openalex_source_extension(metadata)}"
    )
    validate_object_key(key)
    return key


def openalex_raw_provenance_key(metadata: OpenAlexAssetMetadata) -> str:
    """Return the provenance sibling key for an OpenAlex raw object."""
    content_key = openalex_raw_object_key(metadata)
    parent, _, _ = content_key.rpartition("/")
    return f"{parent}/provenance.json"


def openalex_deletion_raw_object_key(metadata: OpenAlexDeletionAssetMetadata) -> str:
    """Build the content key for one OpenAlex deletion CSV.GZ object.

    Layout::

        openalex/works-deletions/snapshot_date=YYYY-MM-DD/updated_date=…/
            <asset-id>/source.csv.gz
    """
    updated = (
        metadata.updated_date.isoformat()
        if metadata.updated_date is not None
        else "none"
    )
    key = (
        f"openalex/{metadata.entity}/"
        f"snapshot_date={metadata.snapshot_date.isoformat()}/"
        f"updated_date={updated}/"
        f"{metadata.asset_id}/"
        f"source.csv.gz"
    )
    validate_object_key(key)
    return key


def openalex_deletion_raw_provenance_key(
    metadata: OpenAlexDeletionAssetMetadata,
) -> str:
    """Return the provenance sibling key for a deletion raw object."""
    content_key = openalex_deletion_raw_object_key(metadata)
    parent, _, _ = content_key.rpartition("/")
    return f"{parent}/provenance.json"
