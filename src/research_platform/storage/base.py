"""Immutable landing contract, independent of filesystem or cloud SDKs."""

from abc import ABC, abstractmethod
from typing import BinaryIO

from research_platform.provenance.models import IngestionProvenance


class ObjectStore(ABC):
    @abstractmethod
    def put_if_absent(
        self, key: str, content: BinaryIO, provenance: IngestionProvenance
    ) -> None:
        """Atomically create content and provenance; never overwrite either.

        An identical content/provenance replay is a no-op. A conflicting existing
        key raises FileExistsError. Verify the content checksum before publishing.
        Keys must stay within the configured landing namespace.
        """
        raise NotImplementedError

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Return a caller-owned read-only stream."""
        raise NotImplementedError
