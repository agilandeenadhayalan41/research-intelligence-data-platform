"""Flatten ``CanonicalLineage`` into the seven portable lineage columns, and back.

Moved from ``persistence/postgres/row_codec.py`` (ADR 0001 D9, DE-07): the
portable Parquet exporter and the release publisher must not import a
PostgreSQL adapter module. ``row_codec`` re-exports both names unchanged.
"""

from __future__ import annotations

from typing import Any, Mapping

from research_platform.canonical.openalex.models import CanonicalLineage

__all__ = ["lineage_columns", "lineage_from_row"]


def lineage_columns(lineage: CanonicalLineage) -> dict[str, Any]:
    return {
        "source_asset_id": lineage.source_asset_id,
        "source_checksum_sha256": lineage.source_checksum_sha256,
        "lineage_source_updated_date": lineage.source_updated_date,
        "run_id": lineage.run_id,
        "processed_at": lineage.processed_at,
        "activity_state": lineage.activity_state.value,
        "deleted_at": lineage.deleted_at,
    }


def lineage_from_row(row: Mapping[str, Any]) -> CanonicalLineage:
    return CanonicalLineage.model_validate(
        {
            "source_asset_id": row["source_asset_id"],
            "source_checksum_sha256": row["source_checksum_sha256"],
            "source_updated_date": row.get("lineage_source_updated_date"),
            "run_id": row["run_id"],
            "processed_at": row["processed_at"],
            "activity_state": row["activity_state"],
            "deleted_at": row.get("deleted_at"),
        }
    )
