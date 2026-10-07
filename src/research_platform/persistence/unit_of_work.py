"""Shared asset-publication request/result types (backend-independent)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from research_platform.canonical.openalex.models import CanonicalWorkBundle
from research_platform.canonical.store import CanonicalUpsertOutcome
from research_platform.control.models import RecordProvenance, SourceFileControl
from research_platform.provenance.models import IngestionProvenance


@dataclass(frozen=True)
class AssetPublishRequest:
    asset_id: str
    claim_token: UUID
    now: datetime
    processed_at: datetime
    raw_object_key: str
    source_checksum_sha256: str
    retrieval_provenance: IngestionProvenance
    bundles: tuple[CanonicalWorkBundle, ...]
    provenance_rows: tuple[RecordProvenance, ...]


@dataclass(frozen=True)
class AssetPublishResult:
    source_file: SourceFileControl
    outcomes: tuple[CanonicalUpsertOutcome, ...]
