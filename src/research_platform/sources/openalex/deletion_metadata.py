"""Validated metadata for one OpenAlex Works deletion asset (Step 13 / #21).

Authoritative public OpenAlex snapshot contract (Help Center “Sync”, 2026):

- Physical URI (JSONL Works tree, chosen as canonical for this project)::

    s3://openalex/data/jsonl/works/deleted_ids.csv.gz

- Equivalent file also exists under ``data/parquet/works/``; we do **not** accept
  that path as an alternate identity in this slice (one canonical URI).
- CSV columns: ``work_id,deleted_date``
- Cumulative Works deletion ledger (~160 MB compressed / tens of millions of
  rows in production). Local development remains bounded at
  ``max_file_size_bytes <= 25_000_000`` and does **not** ingest the full public
  ledger.

Logical control entity remains ``works-deletions`` so deletion assets stay
distinct from Works-data (``works`` / ``oa-…``) identities. Physical URI and
logical entity are intentionally different concepts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_platform.sources.base import SourceAsset

DELETION_ENTITY = "works-deletions"
DELETION_CONTENT_FORMAT = "csv"
DELETION_FILENAME = "deleted_ids.csv.gz"
# Canonical physical path under the public OpenAlex bucket (JSONL Works tree).
DELETION_PUBLIC_URI = f"s3://openalex/data/jsonl/works/{DELETION_FILENAME}"
DELETION_PUBLIC_PATH = f"/data/jsonl/works/{DELETION_FILENAME}"


class OpenAlexDeletionAssetMetadata(BaseModel):
    """Identity metadata for one OpenAlex deletion CSV.GZ object."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source: Literal["openalex"] = "openalex"
    snapshot_date: date
    entity: Literal["works-deletions"] = DELETION_ENTITY
    file_uri: str = Field(min_length=1)
    byte_size: int | None = Field(default=None, strict=True, ge=0)
    updated_date: date | None = None
    content_format: Literal["csv"] = DELETION_CONTENT_FORMAT

    @field_validator("snapshot_date", "updated_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
        if value is not None and not isinstance(value, (date, str)):
            raise ValueError("date fields must be calendar dates or YYYY-MM-DD strings")
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("date fields must use YYYY-MM-DD") from error
            if parsed.isoformat() != value:
                raise ValueError("date fields must use YYYY-MM-DD")
            return parsed
        return value

    @model_validator(mode="after")
    def validate_file_uri(self) -> OpenAlexDeletionAssetMetadata:
        parsed = urlsplit(self.file_uri)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != "openalex"
            or "?" in self.file_uri
            or "#" in self.file_uri
            or any(character.isspace() for character in self.file_uri)
        ):
            raise ValueError("file_uri must be a canonical OpenAlex S3 object URI")

        if parsed.path != DELETION_PUBLIC_PATH or any(
            part in {".", ".."} for part in parsed.path.split("/")
        ):
            raise ValueError(
                "deletion file_uri must be the public Works deletion ledger "
                f"{DELETION_PUBLIC_URI} (logical entity remains {DELETION_ENTITY})"
            )
        return self

    @property
    def asset_id(self) -> str:
        """Stable identity distinct from Works-data asset ids."""
        identity = json.dumps(
            [
                self.source,
                self.snapshot_date.isoformat(),
                self.entity,
                self.content_format,
                self.file_uri,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return f"oad-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"

    def to_source_asset(self) -> SourceAsset:
        return SourceAsset(source=self.source, identifier=self.asset_id, uri=self.file_uri)
