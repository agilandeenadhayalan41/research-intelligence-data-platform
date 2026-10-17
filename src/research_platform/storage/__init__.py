"""Object storage adapters and immutable landing helpers."""

from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.errors import (
    ChecksumMismatchError,
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectStoreError,
)
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.local import LocalObjectStore, dumps_provenance
from research_platform.storage.openalex_layout import (
    openalex_deletion_raw_object_key,
    openalex_deletion_raw_provenance_key,
    openalex_landing_manifest_key,
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
    "ObjectStat",
    "ObjectStore",
    "ObjectStoreError",
    "dumps_provenance",
    "object_store_from_config",
    "openalex_deletion_raw_object_key",
    "openalex_deletion_raw_provenance_key",
    "openalex_landing_manifest_key",
    "openalex_raw_object_key",
    "openalex_raw_provenance_key",
    "openalex_source_extension",
]
