"""Public OpenAlex source adapter."""

from research_platform.sources.openalex.connector import OpenAlexConnector
from research_platform.sources.openalex.manifest import parse_openalex_works_manifest
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata, unique_assets

__all__ = [
    "OpenAlexAssetMetadata",
    "OpenAlexConnector",
    "parse_openalex_works_manifest",
    "unique_assets",
]
