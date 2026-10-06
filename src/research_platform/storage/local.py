"""Local storage skeleton; no filesystem operations yet."""

from typing import BinaryIO

from research_platform.config.models import StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStore


class LocalObjectStore(ObjectStore):
    def __init__(self, config: StorageConfig) -> None:
        self.config = config

    def put_if_absent(
        self, key: str, content: BinaryIO, provenance: IngestionProvenance
    ) -> None:
        raise NotImplementedError("Immutable local landing is deferred to a later phase")

    def open(self, key: str) -> BinaryIO:
        raise NotImplementedError("Local object reads are deferred to a later phase")
