"""Deletion event / outcome lineage models (Step 13)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, Field

from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.control.models import SettingsModel

_SHA256 = r"^[a-f0-9]{64}$"


class DeletionEvent(SettingsModel):
    """One auditable deletion application for a Work ID against a source asset.

    ``deleted_date`` is the per-row OpenAlex source deletion date.
    ``source_updated_date`` on the asset (file-level) is not used for
    per-Work precedence; it may still be recorded when known for the asset.
    """

    work_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=128)
    source_checksum_sha256: str = Field(pattern=_SHA256)
    run_id: UUID
    source_uri: str = Field(min_length=1, max_length=2048)
    deleted_date: date
    source_updated_date: date | None = None
    processed_at: AwareDatetime
    outcome: DeletionOutcome
