import pytest
from pydantic import ValidationError

from research_platform.provenance.models import IngestionProvenance


def test_provenance_is_immutable(provenance: IngestionProvenance) -> None:
    assert provenance.retrieved_at.tzinfo is not None
    with pytest.raises(ValidationError, match="frozen"):
        provenance.source = "changed"


@pytest.mark.parametrize("field", ["run_id", "source", "source_uri", "retrieved_at", "sha256"])
def test_provenance_requires_all_fields(
    provenance: IngestionProvenance, field: str
) -> None:
    content = provenance.model_dump()
    del content[field]
    with pytest.raises(ValidationError):
        IngestionProvenance.model_validate(content)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "not-a-checksum"),
        ("retrieved_at", "2026-01-01T00:00:00"),
        ("run_id", "not-a-uuid"),
        ("source", ""),
        ("source_uri", ""),
    ],
)
def test_invalid_provenance_is_rejected(
    provenance: IngestionProvenance, field: str, value: str
) -> None:
    content = provenance.model_dump()
    content[field] = value
    with pytest.raises(ValidationError):
        IngestionProvenance.model_validate(content)
