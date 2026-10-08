"""Run-scoped analytical publication concurrency ownership (Step 20).

Step-15 contract file names remain addressable constants. Concurrent production
runs must own scratch decision sets via TEMP_TABLES, RUN_SCOPED, or SINGLE_WRITER.
Nothing here deploys BigQuery.
"""

from __future__ import annotations

import re
from uuid import UUID

from research_platform.orchestration.models import (
    RECOMMENDED_PUBLICATION_STRATEGY,
    STEP15_DECISION_CONTRACT_NAMES,
    PublicationConcurrencyStrategy,
    PublicationScope,
)

_SAFE_SUFFIX_RE = re.compile(r"^r_[0-9a-f]{32}$")
_MAX_SUFFIX_LEN = 34  # r_ + 32 hex


def publication_scope_suffix(run_id: UUID | str) -> str:
    """Deterministic, SQL-identifier-safe suffix derived from run identity.

    Never interpolates arbitrary user strings into SQL. Callers must pass a
    UUID (or UUID string). Unsafe suffixes are rejected by
    ``assert_safe_scope_suffix``.
    """
    if isinstance(run_id, UUID):
        hex_part = run_id.hex
    else:
        text = str(run_id).strip()
        try:
            hex_part = UUID(text).hex
        except ValueError as exc:
            raise ValueError("run_id must be a UUID") from exc
    suffix = f"r_{hex_part}"
    assert_safe_scope_suffix(suffix)
    return suffix


def assert_safe_scope_suffix(suffix: str) -> None:
    """Reject arbitrary / unsafe publication-scope suffixes."""
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("suffix must be a non-empty string")
    if len(suffix) > _MAX_SUFFIX_LEN:
        raise ValueError("suffix exceeds bounded length")
    if not _SAFE_SUFFIX_RE.fullmatch(suffix):
        raise ValueError("suffix must match r_<32 lowercase hex>")


def build_publication_scope(
    *,
    run_id: UUID,
    publication_version: str,
    strategy: PublicationConcurrencyStrategy | None = None,
) -> PublicationScope:
    return PublicationScope(
        run_id=run_id,
        publication_version=publication_version,
        strategy=strategy or RECOMMENDED_PUBLICATION_STRATEGY,
    )


def physical_decision_table_name(
    contract_name: str,
    scope: PublicationScope,
) -> str:
    """Resolve the physical scratch name for a Step-15 decision contract.

    TEMP_TABLES / SINGLE_WRITER keep the contract name (script-scoped or
    serialized). RUN_SCOPED appends a safe run suffix.
    """
    if contract_name not in STEP15_DECISION_CONTRACT_NAMES:
        raise ValueError(f"unknown decision contract name: {contract_name}")
    if scope.strategy is PublicationConcurrencyStrategy.RUN_SCOPED:
        suffix = publication_scope_suffix(scope.run_id)
        return f"{contract_name}_{suffix}"
    return contract_name


def scopes_collide(a: PublicationScope, b: PublicationScope) -> bool:
    """True when two scopes would share the same physical scratch namespace unsafely."""
    if a.strategy is PublicationConcurrencyStrategy.SINGLE_WRITER:
        # Single-writer serializes all runs — collision means concurrent use.
        return a.strategy == b.strategy
    if (
        a.strategy is PublicationConcurrencyStrategy.TEMP_TABLES
        and b.strategy is PublicationConcurrencyStrategy.TEMP_TABLES
    ):
        # Script-scoped TEMP tables do not collide across independent sessions.
        return False
    if (
        a.strategy is PublicationConcurrencyStrategy.RUN_SCOPED
        and b.strategy is PublicationConcurrencyStrategy.RUN_SCOPED
    ):
        return publication_scope_suffix(a.run_id) == publication_scope_suffix(b.run_id)
    # Mixed strategies are treated as potentially colliding unless both are
    # TEMP_TABLES (already handled) — force explicit alignment.
    return True
