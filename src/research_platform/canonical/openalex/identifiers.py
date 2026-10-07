"""Explicit OpenAlex identifier normalization rules.

Canonical OpenAlex entity IDs are the short form (``W123``, ``A123``, …), never
the ``https://openalex.org/`` URL form. Source/raw URL forms may be retained in
``*_url`` fields when useful.
"""

from __future__ import annotations

import re
from typing import Literal

from research_platform.canonical.openalex.errors import IdentifierError

OpenAlexEntityPrefix = Literal["W", "A", "I", "S", "P", "T", "F", "C"]

_OPENALEX_URL_PREFIX = "https://openalex.org/"
_OPENALEX_ID = re.compile(r"^([WAISPTFC])(\d+)$")
_DOI_URL_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)
_ORCID_URL_PREFIXES = (
    "https://orcid.org/",
    "http://orcid.org/",
)
_ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
_ROR_URL_PREFIX = "https://ror.org/"
_ROR = re.compile(r"^0[a-zhj-km-np-tv-z0-9]{6}\d{2}$")
_ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
_KEYWORD_ID = re.compile(r"^keywords/[A-Za-z0-9][A-Za-z0-9_-]*$")


def normalize_openalex_id(
    value: object,
    *,
    expected_prefix: OpenAlexEntityPrefix | None = None,
    field_name: str = "openalex_id",
) -> str:
    """Normalize an OpenAlex ID to the short canonical form.

    Accepts ``https://openalex.org/W123`` or ``W123``. Rejects empty, wrong
    prefix, or non-matching shapes. Does not fabricate IDs.
    """
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is missing or empty")
    text = value.strip()
    if text.lower().startswith(_OPENALEX_URL_PREFIX):
        text = text[len(_OPENALEX_URL_PREFIX) :]
    match = _OPENALEX_ID.fullmatch(text)
    if match is None:
        raise IdentifierError(f"{field_name} is not a valid OpenAlex identifier")
    prefix, digits = match.group(1), match.group(2)
    if expected_prefix is not None and prefix != expected_prefix:
        raise IdentifierError(
            f"{field_name} prefix must be {expected_prefix}, got {prefix}"
        )
    return f"{prefix}{digits}"


def normalize_keyword_id(value: object, *, field_name: str = "keyword_id") -> str:
    """Normalize an OpenAlex keyword id to ``keywords/<slug>``."""
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is missing or empty")
    text = value.strip()
    if text.lower().startswith(_OPENALEX_URL_PREFIX):
        text = text[len(_OPENALEX_URL_PREFIX) :]
    if not _KEYWORD_ID.fullmatch(text):
        raise IdentifierError(f"{field_name} is not a valid OpenAlex keyword id")
    return text


def openalex_url(short_id: str) -> str:
    """Return the public OpenAlex URL for a short canonical ID or keyword id."""
    if short_id.startswith("keywords/"):
        return f"{_OPENALEX_URL_PREFIX}{short_id}"
    return f"{_OPENALEX_URL_PREFIX}{short_id}"


def normalize_doi(value: object | None, *, field_name: str = "doi") -> str | None:
    """Normalize DOI to lowercase bare form without a resolver prefix."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is empty")
    text = value.strip()
    lowered = text.lower()
    for prefix in _DOI_URL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.strip().lower()
    if not text or " " in text or text.startswith("http://") or text.startswith("https://"):
        raise IdentifierError(f"{field_name} is malformed")
    return text


def normalize_orcid(value: object | None, *, field_name: str = "orcid") -> str | None:
    """Normalize ORCID to ``ACCT-000028`` (no URL)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is empty")
    text = value.strip()
    lowered = text.lower()
    for prefix in _ORCID_URL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.upper()
    if not _ORCID.fullmatch(text):
        raise IdentifierError(f"{field_name} is malformed")
    return text


def normalize_ror(value: object | None, *, field_name: str = "ror") -> str | None:
    """Normalize ROR to the bare organization id (no URL)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is empty")
    text = value.strip()
    if text.lower().startswith(_ROR_URL_PREFIX):
        text = text[len(_ROR_URL_PREFIX) :]
    text = text.lower()
    if not _ROR.fullmatch(text):
        raise IdentifierError(f"{field_name} is malformed")
    return text


def normalize_issn(value: object | None, *, field_name: str = "issn") -> str | None:
    """Normalize ISSN / ISSN-L to uppercase ``NNNN-NNNC``."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IdentifierError(f"{field_name} is empty")
    text = value.strip().upper().replace(" ", "")
    if not _ISSN.fullmatch(text):
        raise IdentifierError(f"{field_name} is malformed")
    return text


def try_normalize_openalex_id(
    value: object,
    *,
    expected_prefix: OpenAlexEntityPrefix | None = None,
) -> str | None:
    """Return a normalized ID or ``None`` when the value is absent."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return normalize_openalex_id(value, expected_prefix=expected_prefix)
