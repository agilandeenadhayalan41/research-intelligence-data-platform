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
        key raises ``ObjectConflictError`` (a ``FileExistsError``). Verify the
        content checksum before publishing. Keys must stay within the configured
        landing namespace.
        """
        raise NotImplementedError

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Return a caller-owned read-only binary stream.

        The caller must close the stream. Missing or incomplete committed objects
        raise typed storage errors rather than returning a writable handle.
        """
        raise NotImplementedError
