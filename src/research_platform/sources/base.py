"""Discovery and retrieval are separate from ingestion orchestration."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True)
class SourceAsset:
    """Manifest entry identifying one public source object."""

    source: str
    identifier: str
    uri: str


class SourceConnector(ABC):
    @abstractmethod
    def discover(self) -> Iterable[SourceAsset]:
        """Describe public source objects without landing or transforming data."""
        raise NotImplementedError

    @abstractmethod
    def fetch(self, asset: SourceAsset) -> BinaryIO:
        """Return a caller-owned stream; implementations must support large objects."""
        raise NotImplementedError
