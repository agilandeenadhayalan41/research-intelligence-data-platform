"""Select the immutable landing ObjectStore from validated configuration.

Construction never performs I/O: ``GCSObjectStore`` resolves Application Default
Credentials lazily on its first operation unless a client is injected. Callers
decide whether a cloud backend is allowed (see the ingestion opt-in guards).
"""

from __future__ import annotations

from research_platform.config.models import PlatformConfig
from research_platform.storage.base import ObjectStore
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.local import LocalObjectStore


def object_store_from_config(
    config: PlatformConfig, *, gcs_client: object | None = None
) -> ObjectStore:
    """Return ``LocalObjectStore`` for ``local`` or ``GCSObjectStore`` for ``gcs``."""
    backend = config.storage.backend
    if backend == "local":
        if gcs_client is not None:
            raise ValueError("gcs_client is only valid with storage.backend=gcs")
        return LocalObjectStore(config.storage)
    if backend == "gcs":
        return GCSObjectStore(config.storage, config.cloud, client=gcs_client)  # type: ignore[arg-type]
    raise ValueError("unsupported storage.backend")
