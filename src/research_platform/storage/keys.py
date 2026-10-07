"""Safe relative object-key validation under a configured landing root."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from research_platform.storage.errors import InvalidObjectKeyError

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+=-]*$")
_RESERVED_SEGMENTS = frozenset({".staging"})


def validate_object_key(key: str) -> PurePosixPath:
    """Return a relative POSIX key path or raise ``InvalidObjectKeyError``."""
    if not isinstance(key, str):
        raise InvalidObjectKeyError("object key must be a string")
    if not key or key.strip() != key:
        raise InvalidObjectKeyError("object key must be a non-empty relative path")
    if key.endswith("/") or "//" in key:
        raise InvalidObjectKeyError("object key must be a normalized relative path")
    if "\x00" in key or any(character.isspace() for character in key):
        raise InvalidObjectKeyError("object key contains unsafe characters")
    if key.startswith("/") or key.startswith("\\") or re.match(r"^[A-Za-z]:", key):
        raise InvalidObjectKeyError("object key must not be absolute")
    if "\\" in key:
        raise InvalidObjectKeyError("object key must use POSIX separators")

    # Validate raw segments before pathlib collapses "." / ".." away.
    raw_parts = key.split("/")
    if len(raw_parts) < 2:
        raise InvalidObjectKeyError("object key must include a parent directory")
    for part in raw_parts:
        if part in {"", ".", ".."} or part.startswith("."):
            raise InvalidObjectKeyError("object key contains a reserved or relative segment")
        if part in _RESERVED_SEGMENTS or not _SAFE_SEGMENT.fullmatch(part):
            raise InvalidObjectKeyError("object key contains an unsafe path segment")

    path = PurePosixPath(key)
    if path.is_absolute() or path.anchor or path.parts != tuple(raw_parts):
        raise InvalidObjectKeyError("object key must be a normalized relative path")
    return path


def resolve_under_root(root: Path, key: str) -> tuple[Path, Path, str]:
    """Resolve ``key`` under ``root``.

    Returns ``(object_dir, content_path, content_name)`` where ``object_dir`` is
    the committed directory that holds content and ``provenance.json``.
    """
    relative = validate_object_key(key)
    root_resolved = root.resolve(strict=False)
    content_path = (root_resolved.joinpath(*relative.parts)).resolve(strict=False)
    try:
        content_path.relative_to(root_resolved)
    except ValueError as error:
        raise InvalidObjectKeyError("object key escapes the landing root") from error
    if content_path == root_resolved:
        raise InvalidObjectKeyError("object key must identify an object basename")
    object_dir = content_path.parent
    try:
        object_dir.relative_to(root_resolved)
    except ValueError as error:
        raise InvalidObjectKeyError("object key escapes the landing root") from error
    if object_dir == root_resolved:
        raise InvalidObjectKeyError("object key must include a parent directory")
    return object_dir, content_path, content_path.name
