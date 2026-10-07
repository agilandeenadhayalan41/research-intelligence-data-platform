"""Finite decode budgets for OpenAlex Works JSONL.GZ streaming."""

from __future__ import annotations

from pydantic import Field

from research_platform.config.models import SettingsModel


class JsonlDecodeLimits(SettingsModel):
    """Bounds applied while streaming decompressed JSONL (not compressed size).

    Defaults are sized for the ≤25 MB compressed development sample: a gzip can
    expand substantially, so decompressed work is capped independently.
    """

    max_decompressed_bytes: int = Field(default=100_000_000, strict=True, gt=0)
    max_records: int = Field(default=50_000, strict=True, gt=0)
    max_record_bytes: int = Field(default=16_000_000, strict=True, gt=0)
