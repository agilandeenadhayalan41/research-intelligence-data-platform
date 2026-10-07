"""Opaque cursor codec for deterministic Data Service pagination."""

from __future__ import annotations

import base64
import json
from typing import Any

from research_platform.service.models import (
    DATA_SERVICE_CONTRACT_VERSION,
    ServiceError,
    ServiceErrorCode,
    ServiceErrorException,
    SettingsModel,
)


class CursorPayload(SettingsModel):
    """Capability-scoped cursor state — no SQL or backend secrets."""

    contract_version: str
    capability_id: str
    keys: dict[str, Any]


def encode_cursor(
    *,
    capability_id: str,
    keys: dict[str, Any],
    contract_version: str = DATA_SERVICE_CONTRACT_VERSION,
) -> str:
    payload = CursorPayload(
        contract_version=contract_version,
        capability_id=capability_id,
        keys=keys,
    )
    raw = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(
    cursor: str,
    *,
    expected_capability_id: str,
    expected_contract_version: str = DATA_SERVICE_CONTRACT_VERSION,
) -> dict[str, Any]:
    if not cursor or not cursor.strip():
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="cursor must be non-empty",
                capability_id=expected_capability_id,
                field="cursor",
            )
        )
    try:
        padded = cursor.encode("ascii")
        pad = (-len(padded)) % 4
        if pad:
            padded = padded + b"=" * pad
        raw = base64.urlsafe_b64decode(padded)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("cursor payload must be an object")
        payload = CursorPayload.model_validate(data)
    except ServiceErrorException:
        raise
    except Exception:
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="malformed cursor",
                capability_id=expected_capability_id,
                field="cursor",
            )
        ) from None

    if payload.contract_version != expected_contract_version:
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="cursor contract_version mismatch",
                capability_id=expected_capability_id,
                field="cursor",
            )
        )
    if payload.capability_id != expected_capability_id:
        raise ServiceErrorException(
            ServiceError(
                code=ServiceErrorCode.INVALID_CURSOR,
                message="cursor capability_id mismatch",
                capability_id=expected_capability_id,
                field="cursor",
            )
        )
    return dict(payload.keys)


def page_slice[T](items: list[T], *, limit: int) -> tuple[list[T], bool]:
    """Return up to ``limit`` items and whether more remain."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    has_more = len(items) > limit
    return items[:limit], has_more
