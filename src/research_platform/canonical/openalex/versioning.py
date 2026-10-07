"""Canonical version / update precedence rules for later ingestion (contracts)."""

from __future__ import annotations

from enum import StrEnum

from research_platform.canonical.openalex.models import CanonicalLineage, Work


class VersionComparison(StrEnum):
    """How an incoming source version relates to an existing canonical Work."""

    NEWER = "NEWER"
    IDENTICAL = "IDENTICAL"
    CONFLICT = "CONFLICT"
    STALE = "STALE"


def compare_work_versions(
    existing: Work,
    incoming: Work,
) -> VersionComparison:
    """Decide whether an incoming mapped Work should replace an existing one.

    Precedence (in order):

    1. If ``source_checksum_sha256`` matches → ``IDENTICAL`` (replay).
    2. Compare ``source_updated_date`` when both are present:
       - incoming later → ``NEWER``
       - incoming earlier → ``STALE``
       - equal dates with different checksum → ``CONFLICT``
    3. If only one side has ``source_updated_date``, prefer the dated side as
       ``NEWER`` when it is incoming; otherwise ``STALE``.
    4. If neither has a source updated date and checksums differ → ``CONFLICT``.

    This function does not mutate state. Deletion processing is Step 13.
    """
    existing_lineage = existing.lineage
    incoming_lineage = incoming.lineage
    if existing.work_id != incoming.work_id:
        raise ValueError("version comparison requires the same work_id")

    if (
        existing_lineage.source_checksum_sha256
        == incoming_lineage.source_checksum_sha256
    ):
        return VersionComparison.IDENTICAL

    existing_date = existing_lineage.source_updated_date or existing.source_updated_date
    incoming_date = incoming_lineage.source_updated_date or incoming.source_updated_date

    if existing_date is not None and incoming_date is not None:
        if incoming_date > existing_date:
            return VersionComparison.NEWER
        if incoming_date < existing_date:
            return VersionComparison.STALE
        return VersionComparison.CONFLICT

    if incoming_date is not None and existing_date is None:
        return VersionComparison.NEWER
    if incoming_date is None and existing_date is not None:
        return VersionComparison.STALE
    return VersionComparison.CONFLICT


def lineage_identity(lineage: CanonicalLineage) -> tuple[str, str]:
    """Stable identity for reconciliation: asset id + content checksum."""
    return lineage.source_asset_id, lineage.source_checksum_sha256
