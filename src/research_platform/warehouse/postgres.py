"""PostgreSQL serving skeleton; no connections are opened."""

from collections.abc import Mapping

import pyarrow as pa

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.base import Warehouse


class PostgreSQLWarehouse(Warehouse):
    def __init__(self, config: WarehouseConfig) -> None:
        self.config = config

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        raise NotImplementedError("PostgreSQL queries are deferred to a later phase")
