"""Object storage adapters and immutable landing helpers."""

from research_platform.storage.base import ObjectStore
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectStoreError,
)
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.local import LocalObjectStore, dumps_provenance
from research_platform.storage.openalex_layout import (
    openalex_raw_object_key,
    openalex_raw_provenance_key,
    openalex_source_extension,
)

__all__ = [
    "ChecksumMismatchError",
    "GCSObjectStore",
    "IncompleteObjectError",
    "InvalidObjectKeyError",
    "LocalObjectStore",
    "ObjectConflictError",
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "dumps_provenance",
    "openalex_raw_object_key",
    "openalex_raw_provenance_key",
    "openalex_source_extension",
]
