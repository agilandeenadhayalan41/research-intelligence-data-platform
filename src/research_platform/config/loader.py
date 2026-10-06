"""Load one YAML file without connecting to services or loading .env files."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from research_platform.config.models import PlatformConfig

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve(value: Any, environ: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if not environ.get(name):
                raise ValueError(f"Required environment variable is missing or empty: {name}")
            return environ[name]

        return _ENV_REFERENCE.sub(substitute, value)
    if isinstance(value, list):
        return [_resolve(item, environ) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, environ) for key, item in value.items()}
    return value


def load_config(
    path: str | Path, *, environ: Mapping[str, str] | None = None
) -> PlatformConfig:
    """Resolve ${NAME} values after safe YAML parsing, then validate strictly.

    No environment is selected implicitly. Relative data paths are relative to
    the process working directory; run local commands from the repository root.
    """
    with Path(path).open(encoding="utf-8") as handle:
        content = yaml.safe_load(handle)
    if not isinstance(content, dict):
        raise ValueError("Configuration must be a YAML mapping")
    return PlatformConfig.model_validate(
        _resolve(content, os.environ if environ is None else environ)
    )
