"""BigQuery serving skeleton; no SDK or cloud resources are loaded."""

from collections.abc import Mapping

import pyarrow as pa

from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.base import Warehouse


class BigQueryWarehouse(Warehouse):
    def __init__(self, warehouse: WarehouseConfig, cloud: CloudConfig) -> None:
        self.warehouse = warehouse
        self.cloud = cloud

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        raise NotImplementedError("BigQuery queries are deferred to a later phase")
