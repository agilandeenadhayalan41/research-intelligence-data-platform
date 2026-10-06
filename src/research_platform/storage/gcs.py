"""GCS storage skeleton; no SDK, credentials, or cloud resources are loaded."""

from typing import BinaryIO

from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStore


class GCSObjectStore(ObjectStore):
    def __init__(self, storage: StorageConfig, cloud: CloudConfig) -> None:
        self.storage = storage
        self.cloud = cloud

    def put_if_absent(
        self, key: str, content: BinaryIO, provenance: IngestionProvenance
    ) -> None:
        raise NotImplementedError("Immutable GCS landing is deferred to a later phase")

    def open(self, key: str) -> BinaryIO:
        raise NotImplementedError("GCS object reads are deferred to a later phase")
