"""Load and validate the OpenAlex query-pattern registry."""

from __future__ import annotations

from pathlib import Path

import yaml

from research_platform.benchmarks.models import (
    QueryCategory,
    QueryPattern,
    QueryPatternRegistry,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY_PATH = _REPO_ROOT / "config" / "query-patterns" / "openalex.yaml"

REQUIRED_CATEGORIES: frozenset[QueryCategory] = frozenset(QueryCategory)


def load_query_pattern_registry(
    path: Path | str | None = None,
) -> QueryPatternRegistry:
    """Load a YAML registry file into a validated ``QueryPatternRegistry``."""
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("query pattern registry must be a YAML mapping")
    registry = QueryPatternRegistry.model_validate(raw)
    assert_required_categories(registry)
    return registry


def assert_required_categories(registry: QueryPatternRegistry) -> None:
    """Ensure every Step 14 category appears at least once."""
    present = {pattern.category for pattern in registry.patterns}
    missing = REQUIRED_CATEGORIES - present
    if missing:
        names = ", ".join(sorted(item.value for item in missing))
        raise ValueError(f"registry missing required categories: {names}")


def patterns_by_category(
    registry: QueryPatternRegistry, category: QueryCategory
) -> tuple[QueryPattern, ...]:
    return tuple(p for p in registry.patterns if p.category is category)


def registry_to_sorted_dict(registry: QueryPatternRegistry) -> dict[str, object]:
    """Deterministic JSON-friendly dump (sorted keys via Pydantic)."""
    return registry.model_dump(mode="json")
