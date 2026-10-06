"""DuckDB analytical skeleton; no database is created or opened."""

from collections.abc import Mapping

import pyarrow as pa

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.base import Warehouse


class DuckDBWarehouse(Warehouse):
    def __init__(self, config: WarehouseConfig) -> None:
        self.config = config

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        raise NotImplementedError("DuckDB queries are deferred to a later phase")
