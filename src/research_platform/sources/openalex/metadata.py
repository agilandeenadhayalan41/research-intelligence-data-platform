"""Validated metadata contracts for OpenAlex snapshot assets."""

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_platform.sources.base import SourceAsset


class OpenAlexAssetMetadata(BaseModel):
    """Metadata for one file in a dated OpenAlex snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source: Literal["openalex"]
    snapshot_date: date
    entity: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    file_uri: str = Field(min_length=1)
    byte_size: int | None = Field(default=None, strict=True, ge=0)
    record_count: int | None = Field(default=None, strict=True, ge=0)
    updated_date: date | None = None
    content_format: Literal["jsonl", "parquet"]

    @field_validator("snapshot_date", "updated_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        if isinstance(value, datetime):
            raise ValueError("date fields must be calendar dates, not timestamps")
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
    def validate_file_uri(self) -> "OpenAlexAssetMetadata":
        parsed = urlsplit(self.file_uri)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != "openalex"
            or "?" in self.file_uri
            or "#" in self.file_uri
            or any(character.isspace() for character in self.file_uri)
        ):
            raise ValueError("file_uri must be a canonical OpenAlex S3 object URI")

        parts = parsed.path.split("/")
        expected_suffix = ".gz" if self.content_format == "jsonl" else ".parquet"
        if (
            len(parts) < 5
            or parts[0] != ""
            or parts[1] != "data"
            or parts[2] != self.content_format
            or parts[3] != self.entity
            or not parts[-1].endswith(expected_suffix)
            or any(part in {".", ".."} for part in parts)
        ):
            raise ValueError("file_uri namespace, entity, or suffix does not match metadata")

        for part in parts:
            if part.startswith("updated_date="):
                partition_date = part.removeprefix("updated_date=")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", partition_date):
                    raise ValueError("updated_date URI partition must use YYYY-MM-DD")
                try:
                    date.fromisoformat(partition_date)
                except ValueError as error:
                    raise ValueError("updated_date URI partition must be a real date") from error
                if self.updated_date is not None and self.updated_date.isoformat() != partition_date:
                    raise ValueError("updated_date does not match the URI partition")
        return self

    @property
    def asset_id(self) -> str:
        identity = json.dumps(
            [self.source, self.snapshot_date.isoformat(), self.entity, self.file_uri],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return f"oa-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"

    def to_source_asset(self) -> SourceAsset:
        """Adapt metadata to the existing discovery contract."""
        return SourceAsset(source=self.source, identifier=self.asset_id, uri=self.file_uri)


def unique_assets(
    assets: Iterable[OpenAlexAssetMetadata],
) -> tuple[OpenAlexAssetMetadata, ...]:
    """Collapse exact repeated entries and reject conflicting metadata for an identity."""
    by_identity: dict[str, OpenAlexAssetMetadata] = {}
    for asset in assets:
        previous = by_identity.get(asset.asset_id)
        if previous is not None and previous != asset:
            raise ValueError("conflicting metadata for an OpenAlex asset identity")
        by_identity[asset.asset_id] = asset
    return tuple(by_identity[asset_id] for asset_id in sorted(by_identity))
