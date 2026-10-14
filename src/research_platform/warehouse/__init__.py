"""Read-query ``Warehouse`` adapters: BigQuery, DuckDB and PostgreSQL.

No code selects an adapter from ``WarehouseConfig`` or the query-pattern
registry; callers construct the one they need. ``PostgreSQLWarehouse`` is not
the Step 12/13 control/canonical stores and not the #23 serving prototype.
"""

from research_platform.warehouse.base import Warehouse
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.duckdb import DuckDBWarehouse
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryBudgetExceededError,
    QueryExecutionError,
    QueryParameterError,
    QueryTimeoutError,
    WarehouseError,
)
from research_platform.warehouse.postgres import PostgreSQLWarehouse

__all__ = [
    "ArrowConversionError",
    "BigQueryWarehouse",
    "DuckDBWarehouse",
    "PostgreSQLWarehouse",
    "QueryBudgetExceededError",
    "QueryExecutionError",
    "QueryParameterError",
    "QueryTimeoutError",
    "Warehouse",
    "WarehouseError",
]
