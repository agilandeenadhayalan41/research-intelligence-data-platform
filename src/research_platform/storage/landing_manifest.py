"""Bronze landing manifest (ADR 0001 D1): create-only, bucket-free lineage.

One manifest per pipeline run and source asset records the raw object that run
landed or verified: its key, SHA-256, sizes and MD5, the source response metadata
when the stream reports it, the run that committed the object, the store's own
object version and digests, and which checks ran. It never holds a bucket name,
project id, URL of the landing store, account or credential.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStore
from research_platform.storage.openalex_layout import openalex_landing_manifest_key

LANDING_MANIFEST_SCHEMA = "openalex-landing-manifest"
LANDING_MANIFEST_SCHEMA_VERSION = 1
LANDING_MANIFEST_SOURCE = "research-platform"
_MANIFEST_URN = "urn:research-platform:openalex-landing-manifest"

LandingCheck = Literal[
    "declared_size",
    "source_etag_md5",
    "gzip_complete",
    "store_size",
    "store_md5",
    "store_crc32c",
    "store_provenance",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class LandingManifestEntry(_Frozen):
    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    raw_object_key: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    snapshot_date: date
    updated_date: date | None
    content_format: str = Field(min_length=1)
    declared_size_bytes: int | None = Field(ge=0)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    md5_hex: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_content_length: int | None = Field(ge=0)
    source_etag: str | None
    source_version_id: str | None
    retrieved_at: AwareDatetime
    landing_outcome: Literal["created", "existing_object_verified"]
    committed_run_id: UUID | None
    committed_retrieved_at: AwareDatetime | None
    object_generation: str | None = Field(pattern=r"^[0-9]+$")
    object_crc32c: str | None
    object_md5_base64: str | None
    checks: tuple[LandingCheck, ...]

    @model_validator(mode="after")
    def checks_sorted_and_unique(self) -> LandingManifestEntry:
        if list(self.checks) != sorted(set(self.checks)):
            raise ValueError("checks must be sorted and unique")
        return self


class LandingManifestRetention(_Frozen):
    """Declared retention context, not evidence of bucket controls.

    ``bronze_rule`` names the policy the object falls under (ADR 0001 D1: Bronze
    is retained in full until DE-10); the manifest grants no retention of its own.
    The booleans state what this code does not do: it neither reads nor sets
    bucket retention, sets no holds, and source deletions are canonical
    tombstones, not storage lifecycle rules.
    """

    bronze_rule: Literal["adr-0001-d1-bronze-retained-in-full"] = (
        "adr-0001-d1-bronze-retained-in-full"
    )
    bucket_retention_policy_checked: Literal[False] = False
    object_holds_set: Literal[False] = False
    storage_lifecycle_implements_source_deletions: Literal[False] = False


class LandingManifest(_Frozen):
    manifest_schema: Literal["openalex-landing-manifest"] = LANDING_MANIFEST_SCHEMA
    schema_version: Literal[1] = LANDING_MANIFEST_SCHEMA_VERSION
    source: Literal["openalex"] = "openalex"
    entity: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    pipeline_name: str = Field(min_length=1)
    run_id: UUID
    run_created_at: AwareDatetime
    storage_backend: Literal["local", "gcs"]
    storage_role: Literal["bronze"] = "bronze"
    entry: LandingManifestEntry
    retention: LandingManifestRetention = LandingManifestRetention()

    @model_validator(mode="after")
    def created_by_this_run(self) -> LandingManifest:
        entry = self.entry
        if (
            entry.landing_outcome == "created"
            and entry.committed_run_id is not None
            and entry.committed_run_id != self.run_id
        ):
            raise ValueError("a created object must be committed by this run")
        return self

    @property
    def key(self) -> str:
        return openalex_landing_manifest_key(self.entity, self.run_id, self.entry.asset_id)


def dumps_landing_manifest(manifest: LandingManifest) -> bytes:
    """Canonical bytes: sorted keys, compact separators, ASCII, trailing newline."""
    payload = manifest.model_dump(mode="json")
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{text}\n".encode("utf-8")


def landing_manifest_provenance(manifest: LandingManifest, payload: bytes) -> IngestionProvenance:
    """Provenance of the manifest object itself; ``source_uri`` is a URN, not a key."""
    return IngestionProvenance.model_validate(
        {
            "run_id": manifest.run_id,
            "source": LANDING_MANIFEST_SOURCE,
            "source_uri": (
                f"{_MANIFEST_URN}:{manifest.entity}:{manifest.run_id}:{manifest.entry.asset_id}"
            ),
            "retrieved_at": manifest.entry.retrieved_at,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )


def read_landing_manifest(objects: ObjectStore, key: str) -> LandingManifest:
    """Open a manifest (integrity-checked by the store) and validate it."""
    with objects.open(key) as handle:
        return LandingManifest.model_validate_json(handle.read())
