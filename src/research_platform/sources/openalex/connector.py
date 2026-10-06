"""OpenAlex connector skeleton: no HTTP requests or ingestion in Phase 1."""

from collections.abc import Iterable
from typing import BinaryIO

from research_platform.sources.base import SourceAsset, SourceConnector


class OpenAlexConnector(SourceConnector):
    def discover(self) -> Iterable[SourceAsset]:
        raise NotImplementedError("OpenAlex discovery is deferred to a later phase")

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        raise NotImplementedError("OpenAlex retrieval is deferred to a later phase")
