"""Local DuckDB warehouse query adapter.

Construction stores configuration only. It does not import DuckDB, open a
connection, or create a database file. The first ``query`` opens one connection
owned by the adapter. Later queries reuse that connection, so an in-memory
database keeps tables created by earlier queries. ``close()`` releases the
connection; queries after close fail instead of reopening or returning an empty
table.

Parameter convention (DuckDB-specific):
- Write placeholders as ``$name`` (native) or ``:name``.
- ``:name`` is rewritten to ``$name`` only for names present in the parameter
  mapping, and only outside quotes, dollar-quotes, and comments.
- ``::type`` casts are preserved. Values are passed to ``cursor.execute`` and
  are never interpolated into SQL.
- Supported bound values are ``None``, ``bool``, ``int``, ``float``, ``str``,
  ``datetime.date``, ``datetime.datetime``, and ``datetime.time``.

The connection is opened with external access disabled, so this adapter does
not install extensions or read remote paths. Local success is not BigQuery
dialect, cost, cloud, or production evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import NoReturn

import pyarrow as pa

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.base import Warehouse
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryExecutionError,
    QueryParameterError,
    WarehouseError,
)

_LOGGER = logging.getLogger("research_platform.warehouse.duckdb")

_REMOTE_PREFIXES = (
    "http:",
    "https:",
    "s3:",
    "gs:",
    "gcs:",
    "az:",
    "azure:",
    "abfs:",
    "abfss:",
    "hf:",
    "md:",
    "motherduck:",
)


class DuckDBWarehouse(Warehouse):
    """Local DuckDB query adapter returning PyArrow tables.

    The adapter owns at most one connection. Cursors used for a single query
    are closed before ``query`` returns. Call ``close()`` or use the adapter
    as a context manager when finished.
    """

    def __init__(self, config: WarehouseConfig) -> None:
        self.config = config
        self._database = _validate_database(config)
        self._connection: object | None = None
        self._closed = False

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        if self._closed:
            raise WarehouseError("DuckDBWarehouse is closed")
        if not isinstance(sql, str) or not sql.strip():
            raise QueryParameterError("sql must be a non-empty string")
        bound = _validate_parameters(parameters)
        statement = _rewrite_named_placeholders(sql, frozenset(bound))
        connection = self._open_connection()
        cursor = connection.cursor()
        try:
            try:
                cursor.execute(statement, bound)
            except Exception as error:
                _raise_driver_error(error, stage="execute")
            try:
                table = cursor.to_arrow_table()
            except Exception as error:
                _raise_driver_error(error, stage="convert")
        finally:
            cursor.close()
        if not isinstance(table, pa.Table):
            raise ArrowConversionError("DuckDB query did not return a PyArrow table")
        _LOGGER.info(
            "duckdb query completed",
            extra={
                "operation": "query",
                "row_count": table.num_rows,
                "database": "memory" if self._database == ":memory:" else "file",
            },
        )
        return table

    def close(self) -> None:
        """Release the owned connection. Further queries fail explicitly."""
        self._closed = True
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def __enter__(self) -> DuckDBWarehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _open_connection(self) -> object:
        if self._closed:
            raise WarehouseError("DuckDBWarehouse is closed")
        if self._connection is not None:
            return self._connection
        try:
            import duckdb
        except ImportError as error:
            raise WarehouseError("DuckDB is not available") from error
        try:
            connection = duckdb.connect(
                database=self._database,
                config={"enable_external_access": False},
            )
        except Exception as error:
            _raise_driver_error(error, stage="open")
        self._connection = connection
        return connection


def _validate_database(config: WarehouseConfig) -> str:
    if config.analytical != "duckdb":
        raise ValueError("DuckDBWarehouse requires warehouse.analytical='duckdb'")
    path = config.duckdb_path
    if not isinstance(path, str) or path.strip() == "":
        raise ValueError("duckdb_path must be a non-empty string")
    if path != path.strip() or "\x00" in path:
        raise ValueError("duckdb_path must be ':memory:' or a local database file path")
    if path == ":memory:":
        return path
    lowered = path.lower()
    if "://" in path or lowered.startswith(_REMOTE_PREFIXES):
        raise ValueError(
            "DuckDBWarehouse only supports ':memory:' or a local database file path"
        )
    return path


def _validate_parameters(parameters: Mapping[str, object] | None) -> dict[str, object]:
    if parameters is None:
        return {}
    if isinstance(parameters, (str, bytes)) or not isinstance(parameters, Mapping):
        raise QueryParameterError("parameters must be a mapping of name to value")
    bound: dict[str, object] = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not name.isascii() or not name.isidentifier():
            raise QueryParameterError("parameter names must be ASCII identifiers")
        if not _is_supported_value(value):
            raise QueryParameterError(
                f"unsupported parameter type for {name!r}: {type(value).__name__}"
            )
        bound[name] = value
    return bound


def _is_supported_value(value: object) -> bool:
    return value is None or isinstance(value, (str, bool, int, float, date, time))


def _rewrite_named_placeholders(sql: str, names: frozenset[str]) -> str:
    """Rewrite supplied ``:name`` placeholders to DuckDB ``$name`` form.

    The rewrite never inserts parameter values. ``$name`` placeholders, casts,
    quotes, dollar-quotes, and comments are left unchanged.
    """
    if not names:
        return sql
    out: list[str] = []
    index = 0
    length = len(sql)
    state = "code"
    dollar_close = ""
    while index < length:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < length else ""
        if state == "code":
            if char == "-" and nxt == "-":
                out.append("--")
                index += 2
                state = "line"
                continue
            if char == "/" and nxt == "*":
                out.append("/*")
                index += 2
                state = "block"
                continue
            if char == "'":
                out.append("'")
                index += 1
                state = "single"
                continue
            if char == '"':
                out.append('"')
                index += 1
                state = "double"
                continue
            if char == "$":
                token = _dollar_quote_delimiter(sql, index)
                if token is not None:
                    out.append(token)
                    index += len(token)
                    state = "dollar"
                    dollar_close = token
                    continue
            if char == ":" and nxt == ":":
                out.append("::")
                index += 2
                continue
            if char == ":":
                ident = _identifier_at(sql, index + 1)
                if ident is not None and ident in names:
                    out.append("$")
                    out.append(ident)
                    index += 1 + len(ident)
                    continue
            out.append(char)
            index += 1
            continue
        if state == "line":
            out.append(char)
            index += 1
            if char == "\n":
                state = "code"
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                out.append("*/")
                index += 2
                state = "code"
                continue
            out.append(char)
            index += 1
            continue
        if state == "single":
            out.append(char)
            index += 1
            if char == "'" and nxt == "'":
                out.append(nxt)
                index += 1
                continue
            if char == "'":
                state = "code"
            continue
        if state == "double":
            out.append(char)
            index += 1
            if char == '"' and nxt == '"':
                out.append(nxt)
                index += 1
                continue
            if char == '"':
                state = "code"
            continue
        if dollar_close and sql.startswith(dollar_close, index):
            out.append(dollar_close)
            index += len(dollar_close)
            state = "code"
            dollar_close = ""
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _dollar_quote_delimiter(sql: str, start: int) -> str | None:
    if start >= len(sql) or sql[start] != "$":
        return None
    end = start + 1
    while end < len(sql) and sql[end].isascii() and (sql[end].isalnum() or sql[end] == "_"):
        end += 1
    if end < len(sql) and sql[end] == "$":
        return sql[start : end + 1]
    return None


def _identifier_at(sql: str, start: int) -> str | None:
    if start >= len(sql):
        return None
    first = sql[start]
    if not first.isascii() or not (first.isalpha() or first == "_"):
        return None
    end = start + 1
    while end < len(sql):
        char = sql[end]
        if not char.isascii() or not (char.isalnum() or char == "_"):
            break
        end += 1
    return sql[start:end]


def _raise_driver_error(error: BaseException, *, stage: str) -> NoReturn:
    """Map driver failures without copying bound parameter values into messages."""
    import duckdb

    if isinstance(error, WarehouseError):
        raise error
    if isinstance(error, duckdb.ConversionException):
        if stage == "convert":
            raise ArrowConversionError(
                "DuckDB result could not be converted to a PyArrow table"
            ) from None
        raise QueryExecutionError("DuckDB query failed during value conversion") from None
    if isinstance(error, duckdb.InvalidInputException):
        if "parameter" in str(error).lower():
            raise QueryParameterError(
                "DuckDB query parameters do not match the SQL placeholders"
            ) from None
        raise QueryExecutionError("DuckDB query failed: invalid input") from None
    if isinstance(error, duckdb.ConnectionException):
        raise WarehouseError("DuckDB connection is closed") from None
    if isinstance(error, duckdb.PermissionException):
        raise QueryExecutionError(
            "DuckDB query failed: external or network access is disabled"
        ) from None
    if isinstance(error, duckdb.Error):
        if stage == "open":
            raise WarehouseError(f"DuckDB database could not be opened: {error}") from error
        if stage == "convert":
            raise ArrowConversionError(
                "DuckDB result could not be converted to a PyArrow table"
            ) from error
        raise QueryExecutionError(f"DuckDB query failed: {error}") from error
    raise error
