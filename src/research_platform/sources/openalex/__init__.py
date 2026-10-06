"""Public OpenAlex source adapter."""

from research_platform.sources.openalex.connector import (
    OpenAlexAccessDeniedError,
    OpenAlexConnector,
    OpenAlexConnectorError,
    OpenAlexEndpointUnavailableError,
    OpenAlexFormatError,
    OpenAlexManifestError,
    OpenAlexNetworkError,
    OpenAlexSizeLimitError,
    OpenAlexTimeoutError,
    OpenAlexTruncatedError,
    PublicConnectivityResult,
)
from research_platform.sources.openalex.manifest import parse_openalex_works_manifest
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata, unique_assets
from research_platform.sources.openalex.sample import (
    OpenAlexSampleSelection,
    SkippedOpenAlexAsset,
    select_openalex_works_sample,
)

__all__ = [
    "OpenAlexAssetMetadata",
    "OpenAlexAccessDeniedError",
    "OpenAlexConnectorError",
    "OpenAlexEndpointUnavailableError",
    "OpenAlexFormatError",
    "OpenAlexManifestError",
    "OpenAlexNetworkError",
    "OpenAlexSizeLimitError",
    "OpenAlexTimeoutError",
    "OpenAlexTruncatedError",
    "OpenAlexSampleSelection",
    "OpenAlexConnector",
    "SkippedOpenAlexAsset",
    "PublicConnectivityResult",
    "parse_openalex_works_manifest",
    "select_openalex_works_sample",
    "unique_assets",
]
