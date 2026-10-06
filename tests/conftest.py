from pathlib import Path

import pytest

from research_platform.config import PlatformConfig, load_config
from research_platform.provenance.models import IngestionProvenance


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def local_config(repo_root: Path) -> PlatformConfig:
    return load_config(repo_root / "config/local.yaml", environ={})


@pytest.fixture
def provenance(repo_root: Path) -> IngestionProvenance:
    return IngestionProvenance.model_validate_json(
        (repo_root / "tests/fixtures/provenance.json").read_text(encoding="utf-8")
    )
