"""Immutable retrieval provenance attached to raw landed objects.

Mutable pipeline-run / source-file control state is defined separately under
``research_platform.control`` and must not redefine these fields' semantics.
"""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class IngestionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    run_id: UUID
    source: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    retrieved_at: AwareDatetime
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
