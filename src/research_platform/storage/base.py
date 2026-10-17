"""Immutable landing contract, independent of filesystem or cloud SDKs."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO

from research_platform.provenance.models import IngestionProvenance


@dataclass(frozen=True)
class ObjectStat:
    """Store-reported facts about one committed object.

    Never carries a bucket name, project, URL or credential. ``provenance`` is the
    committed provenance when the store keeps it. ``version`` is the store's object
    version as a decimal string (GCS: the object generation); landing manifests
    accept only that form. ``crc32c`` and ``md5_base64`` are the store's own base64
    digests, as GCS reports them. Stores that have none of these leave them ``None``.
    """

    size_bytes: int
    provenance: IngestionProvenance | None = None
    version: str | None = None
    crc32c: str | None = None
    md5_base64: str | None = None


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

    def stat(self, key: str) -> ObjectStat | None:
        """Return store-side facts for a committed object without reading its bytes.

        The default reports nothing (``None``). Stores that override it raise
        ``ObjectNotFoundError`` for a missing object and ``IncompleteObjectError``
        for a partial one, like ``open``.
        """
        del key
        return None
