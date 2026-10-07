"""Immutable retrieval provenance contracts.

Mutable pipeline-control models live in ``research_platform.control``.
Record-level lineage is ``research_platform.control.models.RecordProvenance``.
"""

from research_platform.provenance.models import IngestionProvenance

__all__ = ["IngestionProvenance"]
