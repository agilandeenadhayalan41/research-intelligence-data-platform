"""Idempotency and reconciliation rules for source-file registration."""

from __future__ import annotations

from enum import StrEnum

from research_platform.control.errors import ChecksumConflictError, IdempotencyConflictError
from research_platform.control.models import ControlStatus, SourceFileControl


class RegistrationOutcome(StrEnum):
    """Result of registering a discovered source file against existing control state."""

    CREATED = "CREATED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    ALREADY_SUCCESS = "ALREADY_SUCCESS"
    ALREADY_IN_PROGRESS = "ALREADY_IN_PROGRESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    CHECKSUM_CONFLICT = "CHECKSUM_CONFLICT"


def identity_fields_match(left: SourceFileControl, right: SourceFileControl) -> bool:
    """Stable identity excludes mutable control/claim fields."""
    return (
        left.asset_id == right.asset_id
        and left.source == right.source
        and left.entity == right.entity
        and left.source_uri == right.source_uri
        and left.snapshot_date == right.snapshot_date
        and left.updated_date == right.updated_date
        and left.content_format == right.content_format
    )


def checksums_conflict(existing: SourceFileControl, incoming: SourceFileControl) -> bool:
    """True when both sides declare checksums and they differ."""
    if existing.source_checksum_sha256 is None or incoming.source_checksum_sha256 is None:
        return False
    return existing.source_checksum_sha256 != incoming.source_checksum_sha256


def declared_sizes_conflict(
    existing: SourceFileControl, incoming: SourceFileControl
) -> bool:
    """True when both sides declare byte sizes and they differ."""
    if existing.declared_size_bytes is None or incoming.declared_size_bytes is None:
        return False
    return existing.declared_size_bytes != incoming.declared_size_bytes


def reconcile_registration(
    existing: SourceFileControl | None,
    incoming: SourceFileControl,
    *,
    raise_on_conflict: bool = False,
) -> RegistrationOutcome:
    """Decide how a registration request interacts with existing control state.

    Rules:
    - No existing row → ``CREATED`` (caller persists DISCOVERED).
    - Conflicting identity metadata (uri/dates/format) → ``IdempotencyConflictError``.
    - Conflicting declared sizes when both known → ``IdempotencyConflictError``.
    - Same identity + different known checksum → ``CHECKSUM_CONFLICT``.
    - Same identity + SUCCESS + matching known metadata → ``ALREADY_SUCCESS``
      (trusted immutable replay; byte-level re-fetch is skipped).
    - Same identity + PROCESSING → ``ALREADY_IN_PROGRESS``.
    - Same identity + FAILED + same/unknown checksum → ``RETRYABLE_FAILURE``.
    - Same identity + DISCOVERED → ``IDEMPOTENT_REPLAY``.

    OpenAlex snapshot object identity is treated as immutable for Step 12:
    ``ALREADY_SUCCESS`` does not re-fetch bytes. Conflicting declared metadata
    for the same asset id must fail rather than silently skip.
    """
    if incoming.status is not ControlStatus.DISCOVERED:
        raise IdempotencyConflictError("registration requires DISCOVERED incoming status")
    if existing is None:
        return RegistrationOutcome.CREATED
    if existing.asset_id != incoming.asset_id:
        raise IdempotencyConflictError("existing control asset_id does not match incoming")
    if not identity_fields_match(existing, incoming):
        raise IdempotencyConflictError(
            "asset identity metadata conflicts with an existing control row"
        )
    if declared_sizes_conflict(existing, incoming):
        raise IdempotencyConflictError(
            "declared_size_bytes conflicts with an existing control row"
        )
    if checksums_conflict(existing, incoming):
        if raise_on_conflict:
            raise ChecksumConflictError(
                "source checksum conflicts for an existing asset identity"
            )
        return RegistrationOutcome.CHECKSUM_CONFLICT

    if existing.status is ControlStatus.SUCCESS:
        return RegistrationOutcome.ALREADY_SUCCESS
    if existing.status is ControlStatus.PROCESSING:
        return RegistrationOutcome.ALREADY_IN_PROGRESS
    if existing.status is ControlStatus.FAILED:
        return RegistrationOutcome.RETRYABLE_FAILURE
    return RegistrationOutcome.IDEMPOTENT_REPLAY
