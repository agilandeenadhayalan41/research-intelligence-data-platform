from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from research_platform.config import PlatformConfig, load_config


@pytest.mark.parametrize("environment", ["local", "dev", "qa", "prod"])
def test_environment_templates_are_local_only(repo_root: Path, environment: str) -> None:
    config = load_config(repo_root / f"config/{environment}.yaml", environ={})
    assert config.environment == environment
    assert config.storage.backend == "local"
    assert config.warehouse.transactional == "postgres"
    assert config.warehouse.analytical == "duckdb"
    assert config.cloud.project_id is None


def test_sandbox_uses_explicit_environment(repo_root: Path) -> None:
    config = load_config(
        repo_root / "config/gcp-sandbox.yaml",
        environ={
            "GOOGLE_CLOUD_PROJECT": "synthetic-project",
            "GCS_BUCKET": "synthetic-bucket",
            "BIGQUERY_DATASET": "synthetic_dataset",
        },
    )
    assert config.storage.backend == "gcs"
    assert config.storage.bucket == "synthetic-bucket"
    assert config.cloud.project_id == "synthetic-project"
    assert config.warehouse.analytical == "bigquery"
    assert config.warehouse.bigquery_dataset == "synthetic_dataset"


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_environment_reference(
    repo_root: Path, value: str | None
) -> None:
    environ = {} if value is None else {"GCS_BUCKET": value}
    with pytest.raises(ValueError, match="GCS_BUCKET"):
        load_config(repo_root / "config/gcp-sandbox.yaml", environ=environ)


def test_process_environment_is_used_only_when_not_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("environment: ${PLATFORM_TEST_ENV}\n", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_TEST_ENV", "qa")
    assert load_config(path).environment == "qa"
    with pytest.raises(ValueError, match="PLATFORM_TEST_ENV"):
        load_config(path, environ={})


def test_substitution_cannot_inject_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("storage:\n  bucket: ${BUCKET}\n", encoding="utf-8")
    config = load_config(path, environ={"BUCKET": "literal\nbackend: gcs"})
    assert config.storage.bucket == "literal\nbackend: gcs"
    assert config.storage.backend == "local"


@pytest.mark.parametrize("content", ["", "- local\n", "null\n", "42\n"])
def test_requires_yaml_mapping(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_config(path, environ={})


@pytest.mark.parametrize(
    "content",
    [
        "environment: unknown\n",
        "unexpected: value\n",
        "storage:\n  backend: unknown\n",
        "warehouse:\n  analytical: unknown\n",
        "cloud:\n  credentials: forbidden\n",
        "storage:\n  backend: gcs\n",
        "storage:\n  backend: gcs\n  bucket: synthetic-bucket\n",
        "warehouse:\n  analytical: bigquery\ncloud:\n  project_id: synthetic-project\n",
    ],
)
def test_rejects_invalid_configuration(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path, environ={})


def test_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("!!python/object:builtins.object {}\n", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        load_config(path, environ={})


def test_missing_file_is_not_silently_defaulted(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml", environ={})


def test_settings_are_frozen(local_config: PlatformConfig) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        local_config.environment = "prod"
