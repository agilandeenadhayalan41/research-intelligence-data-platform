"""PostgreSQL Warehouse query adapter (read-only).

Construction and import do not read ``POSTGRES_DSN``, open a socket, or import
``psycopg``. The optional ``postgres`` extra is imported only when ``query``
must open a real connection.

This adapter implements ``Warehouse.query`` for PostgreSQL. It is not the
BigQuery analytical path, not ``PostgresControlStore`` / ``PostgresCanonicalStore``,
and not approval for issue #23 (PostgreSQL/AlloyDB serving projections).

Dialect contract (not portable to DuckDB or BigQuery):

- Named placeholders are psycopg pyformat: ``%(name)s``.
- Values are passed to the driver as a parameter mapping. They are never
  interpolated into SQL.
- The session is ``autocommit`` with ``default_transaction_read_only=on`` so a
  completed statement does not leave an idle or aborted transaction, and
  data-changing statements fail in the server rather than through a SQL parser.
- ``TimeZone=UTC`` is set so ``timestamptz`` instants are returned in UTC.
  ``timestamp`` without time zone stays a naive datetime.

Evidence: offline fake-driver tests are ``OFFLINE_TESTED``. A separately invoked
local PostgreSQL run is required before calling the adapter
``LOCAL_POSTGRES_VERIFIED``. This module does not claim cloud or AlloyDB
verification.

The adapter is synchronous and is not thread-safe. It does not pool connections.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol

import pyarrow as pa

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.base import Warehouse
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryExecutionError,
    QueryParameterError,
    WarehouseError,
)

_LOGGER = logging.getLogger("research_platform.warehouse.postgres")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# PostgreSQL type OIDs used for Arrow conversion. Unlisted OIDs fail closed.
_OID_BOOL = 16
_OID_INT8 = 20
_OID_INT2 = 21
_OID_INT4 = 23
_OID_TEXT = 25
_OID_FLOAT4 = 700
_OID_FLOAT8 = 701
_OID_NAME = 19
_OID_VARCHAR = 1043
_OID_BPCHAR = 1042
_OID_NUMERIC = 1700
_OID_DATE = 1082
_OID_TIME = 1083
_OID_TIMESTAMP = 1114
_OID_TIMESTAMPTZ = 1184

_INT_OIDS = frozenset({_OID_INT2, _OID_INT4, _OID_INT8})
_TEXT_OIDS = frozenset({_OID_TEXT, _OID_VARCHAR, _OID_BPCHAR, _OID_NAME})
_FLOAT_OIDS = frozenset({_OID_FLOAT4, _OID_FLOAT8})

_MAX_DECIMAL128 = 38
_MAX_DECIMAL256 = 76
_EMPTY_UNBOUNDED_NUMERIC = (38, 18)


class _Cursor(Protocol):
    description: Sequence[object] | None

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> object: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def close(self) -> None: ...


class PostgreSQLWarehouse(Warehouse):
    """Synchronous PostgreSQL read adapter returning PyArrow tables.

    ``connect`` is an optional test seam. When omitted, the first ``query``
    resolves ``config.postgres_dsn_env`` and opens one owned connection.
    ``close`` does not reconnect.
    """

    def __init__(
        self,
        config: WarehouseConfig,
        *,
        connect: Callable[[str], _Connection] | None = None,
    ) -> None:
        self._config = config
        self._connect = connect
        self._connection: _Connection | None = None
        self._closed = False

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        if self._closed:
            raise WarehouseError("PostgreSQL warehouse is closed")
        bound = _validate_query(sql, parameters)
        connection = self._connection_or_open()
        cursor = connection.cursor()
        try:
            try:
                cursor.execute(sql, bound)
            except (QueryParameterError, QueryExecutionError, WarehouseError):
                raise
            except Exception as error:
                raise _map_driver_error(error) from None
            description = cursor.description
            if not description:
                raise QueryExecutionError(
                    "PostgreSQL query did not produce a result set"
                )
            try:
                rows = cursor.fetchall()
            except Exception as error:
                raise _map_driver_error(error) from None
            table = _table_from_result(tuple(description), rows)
        finally:
            _close_quietly(cursor)
        _LOGGER.info(
            "postgres query completed",
            extra={
                "operation": "query",
                "backend": "postgres",
                "row_count": table.num_rows,
                "column_count": table.num_columns,
            },
        )
        return table

    def close(self) -> None:
        """Close the owned connection. Further queries fail closed."""
        self._closed = True
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                raise QueryExecutionError("PostgreSQL connection failed") from None

    def __enter__(self) -> PostgreSQLWarehouse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _connection_or_open(self) -> _Connection:
        if self._connection is not None:
            return self._connection
        dsn = _read_dsn(self._config.postgres_dsn_env)
        try:
            if self._connect is not None:
                connection = self._connect(dsn)
            else:
                connection = _open_postgres(dsn)
        except WarehouseError:
            raise
        except Exception:
            raise QueryExecutionError("PostgreSQL connection failed") from None
        self._connection = connection
        return connection


def _read_dsn(env_var: object) -> str:
    """Resolve a DSN from the configured environment-variable name.

    Mirrors ``postgres_dsn_from_env`` (strip, reject blank) without importing
    ``persistence.postgres.connection``, which imports ``psycopg`` and opens
    ingestion transactions (``autocommit=False``). The value is never logged.
    """
    if not isinstance(env_var, str) or not env_var.strip():
        raise WarehouseError("PostgreSQL DSN environment variable is not configured")
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise WarehouseError("PostgreSQL DSN environment variable is not configured")
    return value


def _import_psycopg() -> object:
    try:
        import psycopg
    except ImportError:
        raise WarehouseError("PostgreSQL support is not available") from None
    return psycopg


def _open_postgres(dsn: str) -> _Connection:
    """Open a read-only autocommit session.

    Ingestion uses ``connect_postgres`` (``autocommit=False``) so explicit
    transactions can span writes. A query adapter must not stay idle in
    transaction after SELECT, and must not commit caller writes. Read-only is
    enforced by the server, not by inspecting SQL text.
    """
    psycopg = _import_psycopg()
    return psycopg.connect(  # type: ignore[attr-defined, no-any-return]
        dsn,
        autocommit=True,
        options="-c default_transaction_read_only=on -c TimeZone=UTC",
    )


def _validate_query(
    sql: object, parameters: Mapping[str, object] | None
) -> dict[str, object] | None:
    if not isinstance(sql, str) or not sql.strip():
        raise QueryParameterError("sql must be a non-empty string")
    if parameters is None:
        return None
    if not isinstance(parameters, Mapping):
        raise QueryParameterError("parameters must be a mapping of name to value")
    bound: dict[str, object] = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or _IDENTIFIER.fullmatch(name) is None:
            raise QueryParameterError("PostgreSQL query parameters are invalid")
        _validate_parameter_value(value)
        bound[name] = value
    return bound


def _validate_parameter_value(value: object) -> None:
    if value is None or isinstance(value, (str, Decimal, datetime, date, time)):
        return
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return
    raise QueryParameterError("PostgreSQL query parameters are invalid")


def _map_driver_error(error: BaseException) -> WarehouseError:
    """Map driver failures to fixed messages. Driver text can echo SQL."""
    if isinstance(error, (QueryParameterError, QueryExecutionError, WarehouseError)):
        return error
    name = type(error).__name__
    if name in {"KeyError"} or "parameter" in str(error).lower():
        return QueryParameterError("PostgreSQL query parameters are invalid")
    return QueryExecutionError("PostgreSQL query execution failed")


def _close_quietly(resource: object) -> None:
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        _LOGGER.info(
            "postgres cursor close failed",
            extra={"operation": "close", "backend": "postgres"},
        )


def _table_from_result(
    description: Sequence[object], rows: Sequence[Sequence[object]]
) -> pa.Table:
    names = [_column_name(column) for column in description]
    if len(names) != len(set(names)):
        raise ArrowConversionError("PostgreSQL result contains duplicate column names")
    arrays: list[pa.Array] = []
    fields: list[pa.Field] = []
    try:
        for index, column in enumerate(description):
            values = [row[index] for row in rows]
            array = _column_array(column, values)
            arrays.append(array)
            fields.append(pa.field(names[index], array.type))
        return pa.table(arrays, schema=pa.schema(fields))
    except ArrowConversionError:
        raise
    except Exception:
        raise ArrowConversionError(
            "PostgreSQL result could not be converted to PyArrow"
        ) from None


def _column_name(column: object) -> str:
    name = getattr(column, "name", None)
    if not isinstance(name, str) or not name:
        raise ArrowConversionError(
            "PostgreSQL result could not be converted to PyArrow"
        )
    return name


def _column_array(column: object, values: Sequence[object]) -> pa.Array:
    type_code = getattr(column, "type_code", None)
    precision = getattr(column, "precision", None)
    scale = getattr(column, "scale", None)
    if type_code in _INT_OIDS:
        _expect_instances(values, int, reject_bool=True)
        return pa.array(list(values), type=pa.int64())
    if type_code == _OID_BOOL:
        _expect_instances(values, bool)
        return pa.array(list(values), type=pa.bool_())
    if type_code in _TEXT_OIDS:
        _expect_instances(values, str)
        return pa.array(list(values), type=pa.string())
    if type_code in _FLOAT_OIDS:
        _expect_instances(values, float, reject_bool=True)
        return pa.array(list(values), type=pa.float64())
    if type_code == _OID_DATE:
        _expect_instances(values, date, reject_datetime=True)
        return pa.array(list(values), type=pa.date32())
    if type_code == _OID_TIME:
        _expect_time(values)
        return pa.array(list(values), type=pa.time64("us"))
    if type_code == _OID_TIMESTAMP:
        _expect_timestamp(values, aware=False)
        return pa.array(list(values), type=pa.timestamp("us"))
    if type_code == _OID_TIMESTAMPTZ:
        _expect_timestamp(values, aware=True)
        return pa.array(list(values), type=pa.timestamp("us", tz="UTC"))
    if type_code == _OID_NUMERIC:
        arrow_type = _numeric_type(precision, scale, values)
        converted = _numeric_values(values)
        return pa.array(converted, type=arrow_type)
    raise ArrowConversionError("PostgreSQL result could not be converted to PyArrow")


def _expect_instances(
    values: Sequence[object],
    expected: type[object],
    *,
    reject_bool: bool = False,
    reject_datetime: bool = False,
) -> None:
    for value in values:
        if value is None:
            continue
        if reject_bool and isinstance(value, bool):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        if reject_datetime and isinstance(value, datetime):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        if not isinstance(value, expected):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )


def _expect_time(values: Sequence[object]) -> None:
    for value in values:
        if value is None:
            continue
        if not isinstance(value, time) or isinstance(value, datetime):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        if value.tzinfo is not None:
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )


def _expect_timestamp(values: Sequence[object], *, aware: bool) -> None:
    for value in values:
        if value is None:
            continue
        if not isinstance(value, datetime):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        has_tz = value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None
        if has_tz != aware:
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )


def _numeric_values(values: Sequence[object]) -> list[Decimal | None]:
    converted: list[Decimal | None] = []
    for value in values:
        if value is None:
            converted.append(None)
            continue
        if isinstance(value, bool) or isinstance(value, float):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ArrowConversionError(
                    "PostgreSQL result could not be converted to PyArrow"
                )
            converted.append(value)
            continue
        if isinstance(value, int):
            converted.append(Decimal(value))
            continue
        raise ArrowConversionError(
            "PostgreSQL result could not be converted to PyArrow"
        )
    return converted


def _numeric_type(
    precision: object, scale: object, values: Sequence[object]
) -> pa.DataType:
    if (
        isinstance(precision, int)
        and isinstance(scale, int)
        and precision > 0
        and scale >= 0
        and scale <= precision
    ):
        chosen = (precision, scale)
    else:
        chosen = _numeric_spec_from_values(values)
    prec, sc = chosen
    if prec <= _MAX_DECIMAL128:
        return pa.decimal128(prec, sc)
    if prec <= _MAX_DECIMAL256:
        return pa.decimal256(prec, sc)
    raise ArrowConversionError("PostgreSQL result could not be converted to PyArrow")


def _numeric_spec_from_values(values: Sequence[object]) -> tuple[int, int]:
    # Integer digits and scale are maximised independently: a column holding
    # 12345678.0 and 0.12345 needs 8 integer digits and scale 5, i.e. (13, 5).
    max_scale = 0
    max_integer_digits = 0
    saw_value = False
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, float):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        if isinstance(value, int):
            dec = Decimal(value)
        elif isinstance(value, Decimal) and value.is_finite():
            dec = value
        else:
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        saw_value = True
        _sign, digits, exponent = dec.as_tuple()
        if not isinstance(exponent, int):
            raise ArrowConversionError(
                "PostgreSQL result could not be converted to PyArrow"
            )
        scale = -exponent if exponent < 0 else 0
        digit_count = len(digits) + (exponent if exponent > 0 else 0)
        max_scale = max(max_scale, scale)
        max_integer_digits = max(max_integer_digits, digit_count - scale)
    if not saw_value:
        return _EMPTY_UNBOUNDED_NUMERIC
    precision = max(max_integer_digits + max_scale, 1)
    return precision, max_scale
