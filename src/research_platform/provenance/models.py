"""Metadata required by future immutable landing implementations."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class IngestionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    run_id: UUID
    source: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    retrieved_at: AwareDatetime
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
