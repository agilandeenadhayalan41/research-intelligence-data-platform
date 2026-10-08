"""Guard the test tree against duplicate test module basenames."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"


def test_test_module_basenames_are_unique_outside_packages() -> None:
    # Without __init__.py, pytest imports test modules by basename, so two
    # same-named files fail collection with "import file mismatch".
    by_name: dict[str, list[str]] = defaultdict(list)
    for path in TESTS_ROOT.rglob("test_*.py"):
        if (path.parent / "__init__.py").exists():
            continue
        by_name[path.name].append(path.relative_to(REPO_ROOT).as_posix())
    duplicates = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    assert duplicates == {}
