"""Strict EndToEndBounds validation helpers."""

from __future__ import annotations

from research_platform.e2e.models import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_FILES,
    EndToEndBounds,
)


def parse_bounds(
    *,
    max_files: object = DEFAULT_MAX_FILES,
    max_file_size_bytes: object = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> EndToEndBounds:
    """Parse and validate bounds; raise ValueError on invalid input."""
    return EndToEndBounds.model_validate(
        {
            "max_files": max_files,
            "max_file_size_bytes": max_file_size_bytes,
        }
    )
