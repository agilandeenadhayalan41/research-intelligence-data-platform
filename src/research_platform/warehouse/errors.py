"""Typed Warehouse adapter errors."""


class WarehouseError(Exception):
    """Safe base exception for warehouse adapter failures."""


class QueryParameterError(WarehouseError, ValueError):
    """Query parameters are missing, mistyped, or otherwise unusable."""


class QueryBudgetExceededError(WarehouseError, ValueError):
    """Dry-run estimate exceeds the configured maximum_bytes_billed budget."""


class QueryTimeoutError(WarehouseError, TimeoutError):
    """Query job exceeded the configured timeout."""


class ArrowConversionError(WarehouseError, ValueError):
    """Provider result could not be converted to a PyArrow table."""


class QueryExecutionError(WarehouseError):
    """Provider reported a query/job failure that is not a typed budget/timeout case."""
