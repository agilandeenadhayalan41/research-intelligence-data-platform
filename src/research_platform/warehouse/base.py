"""Minimal read interface shared by transactional and analytical adapters."""

from abc import ABC, abstractmethod
from collections.abc import Mapping

import pyarrow as pa


class Warehouse(ABC):
    @abstractmethod
    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        """Run a trusted query with bound parameters, returning an Arrow table.

        SQL dialects and parameter naming remain adapter-specific. Never format
        untrusted values into SQL. Writes and migrations need separate contracts.
        """
        raise NotImplementedError
