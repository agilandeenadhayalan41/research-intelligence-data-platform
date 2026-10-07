"""Finite decode budgets for OpenAlex deleted_ids.csv.gz streaming."""

from __future__ import annotations

from pydantic import Field

from research_platform.config.models import SettingsModel


class CsvDeletionDecodeLimits(SettingsModel):
    """Bounds applied while streaming decompressed deletion CSV (not compressed size)."""

    max_decompressed_bytes: int = Field(default=50_000_000, strict=True, gt=0)
    max_ids: int = Field(default=100_000, strict=True, gt=0)
    max_row_bytes: int = Field(default=4_096, strict=True, gt=0)
