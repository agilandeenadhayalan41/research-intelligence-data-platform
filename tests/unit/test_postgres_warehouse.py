"""Offline PostgreSQLWarehouse tests. No Docker, DSN, or network."""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pyarrow as pa
import pytest

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryExecutionError,
    QueryParameterError,
    WarehouseError,
)
from research_platform.warehouse.postgres import PostgreSQLWarehouse


class _Column:
    def __init__(
        self,
        name: str,
        type_code: int,
        *,
        precision: int | None = None,
        scale: int | None = None,
    ) -> None:
        self.name = name
        self.type_code = type_code
        self.precision = precision
        self.scale = scale


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.description: Sequence[_Column] | None = None
        self.closed = False

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> None:
        self.connection.executed.append((query, params))
        if self.connection.execute_error is not None:
            raise self.connection.execute_error
        self.description = self.connection.description

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.connection.fetch_error is not None:
            raise self.connection.fetch_error
        return list(self.connection.rows)

    def close(self) -> None:
        self.closed = True
        self.connection.closed_cursors.append(self)


class _Connection:
    def __init__(self) -> None:
        self.description: Sequence[_Column] | None = None
        self.rows: list[tuple[object, ...]] = []
        self.execute_error: BaseException | None = None
        self.fetch_error: BaseException | None = None
        self.executed: list[tuple[str, Mapping[str, object] | None]] = []
        self.closed_cursors: list[_Cursor] = []
        self.cursor_calls = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        self.cursor_calls += 1
        return _Cursor(self)

    def close(self) -> None:
        self.closed = True


def _config(env_name: str = "POSTGRES_DSN_UNIT") -> WarehouseConfig:
    return WarehouseConfig(postgres_dsn_env=env_name)


def _warehouse(
    connection: _Connection | None = None,
    *,
    env_name: str = "POSTGRES_DSN_UNIT",
    opens: list[str] | None = None,
) -> tuple[PostgreSQLWarehouse, _Connection]:
    owned = connection or _Connection()
    recorded = opens if opens is not None else []

    def connect(dsn: str) -> _Connection:
        recorded.append(dsn)
        return owned

    return PostgreSQLWarehouse(_config(env_name), connect=connect), owned


def test_constructor_does_not_connect_or_read_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_dsn(_name: object) -> str:
        raise AssertionError("constructor must not resolve a DSN")

    def reject_connect(_dsn: str) -> _Connection:
        raise AssertionError("constructor must not open a connection")

    monkeypatch.setattr(
        "research_platform.warehouse.postgres._read_dsn", reject_dsn
    )
    PostgreSQLWarehouse(_config(), connect=reject_connect)


def test_constructor_does_not_open_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("constructor must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    PostgreSQLWarehouse(_config())


def test_import_succeeds_when_psycopg_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins
    import sys

    PostgreSQLWarehouse(_config())
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit-test-not-a-secret")
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    real_import = builtins.__import__

    def guarded(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    warehouse = PostgreSQLWarehouse(_config())
    with pytest.raises(WarehouseError, match="PostgreSQL support is not available"):
        warehouse.query("SELECT 1")


def test_query_uses_configured_dsn_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def read_dsn(name: object) -> str:
        seen.append(name)
        return "postgresql://configured-name"

    monkeypatch.setattr("research_platform.warehouse.postgres._read_dsn", read_dsn)
    warehouse, connection = _warehouse(env_name="WAREHOUSE_DSN_ENV")
    connection.description = (_Column("value", 23),)
    connection.rows = [(1,)]
    warehouse.query("SELECT 1 AS value")
    assert seen == ["WAREHOUSE_DSN_ENV"]


def test_missing_dsn_fails_without_embedding_env_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTGRES_DSN_SECRET_NAME", raising=False)
    warehouse = PostgreSQLWarehouse(_config("POSTGRES_DSN_SECRET_NAME"))
    with pytest.raises(WarehouseError, match="not configured") as raised:
        warehouse.query("SELECT 1")
    assert "POSTGRES_DSN_SECRET_NAME" not in str(raised.value)


def test_query_opens_connection_lazily_and_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opens: list[str] = []
    warehouse, connection = _warehouse(opens=opens)
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://lazy-open")
    connection.description = (_Column("value", 23),)
    connection.rows = [(4,)]
    assert opens == []
    first = warehouse.query("SELECT %(value)s AS value", {"value": 4})
    second = warehouse.query("SELECT %(value)s AS value", {"value": 4})
    assert opens == ["postgresql://lazy-open"]
    assert connection.cursor_calls == 2
    assert first.num_rows == 1
    assert second.column("value")[0].as_py() == 4


def test_query_returns_pyarrow_table_with_column_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (
        _Column("zeta", 25),
        _Column("alpha", 23),
    )
    connection.rows = [("tail", 2)]
    table = warehouse.query("SELECT zeta, alpha FROM probe")
    assert isinstance(table, pa.Table)
    assert table.column_names == ["zeta", "alpha"]
    assert table.column("zeta")[0].as_py() == "tail"
    assert table.column("alpha")[0].as_py() == 2


def test_null_stays_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (
        _Column("label", 25),
        _Column("amount", 23),
        _Column("flag", 16),
    )
    connection.rows = [(None, None, None)]
    table = warehouse.query("SELECT label, amount, flag FROM probe")
    assert table.column("label")[0].as_py() is None
    assert table.column("amount")[0].as_py() is None
    assert table.column("flag")[0].as_py() is None
    assert table.column("label").null_count == 1


def test_empty_result_keeps_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("value", 23), _Column("label", 25))
    connection.rows = []
    table = warehouse.query("SELECT value, label FROM probe WHERE false")
    assert table.num_rows == 0
    assert table.column_names == ["value", "label"]
    assert table.schema.field("value").type == pa.int64()
    assert table.schema.field("label").type == pa.string()


def test_named_parameters_are_bound_not_interpolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    payload = "'; DROP TABLE works; --"
    connection.description = (_Column("label", 25),)
    connection.rows = [(payload,)]
    sql = "SELECT %(label)s AS label"
    table = warehouse.query(sql, {"label": payload})
    assert connection.executed == [(sql, {"label": payload})]
    assert payload not in connection.executed[0][0]
    assert table.column("label")[0].as_py() == payload


@pytest.mark.parametrize(
    ("type_code", "value", "arrow_type"),
    [
        (23, 41, pa.int64()),
        (16, True, pa.bool_()),
        (1082, date(2024, 3, 4), pa.date32()),
        (1114, datetime(2024, 3, 4, 5, 6, 7), pa.timestamp("us")),
        (
            1184,
            datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC),
            pa.timestamp("us", tz="UTC"),
        ),
        (1083, time(5, 6, 7), pa.time64("us")),
        (1700, Decimal("12.3400"), pa.decimal128(12, 4)),
    ],
)
def test_representative_value_conversions(
    monkeypatch: pytest.MonkeyPatch,
    type_code: int,
    value: object,
    arrow_type: pa.DataType,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    precision = 12 if type_code == 1700 else None
    scale = 4 if type_code == 1700 else None
    connection.description = (
        _Column("value", type_code, precision=precision, scale=scale),
    )
    connection.rows = [(value,)]
    table = warehouse.query("SELECT value FROM probe")
    assert table.schema.field("value").type == arrow_type
    assert table.column("value")[0].as_py() == value


def test_empty_unbounded_numeric_uses_documented_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("amount", 1700),)
    connection.rows = []
    table = warehouse.query("SELECT amount FROM probe WHERE false")
    assert table.num_rows == 0
    assert table.schema.field("amount").type == pa.decimal128(38, 18)


def test_unbounded_numeric_preserves_decimal_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("amount", 1700),)
    connection.rows = [(Decimal("10.25"),), (Decimal("3"),)]
    table = warehouse.query("SELECT amount FROM probe")
    assert table.schema.field("amount").type == pa.decimal128(4, 2)
    assert table.column("amount")[0].as_py() == Decimal("10.25")
    assert table.column("amount")[1].as_py() == Decimal("3.00")


@pytest.mark.parametrize(
    ("values", "arrow_type"),
    [
        ([Decimal("12345678.0"), Decimal("0.12345")], pa.decimal128(13, 5)),
        ([Decimal("1.5"), Decimal("100")], pa.decimal128(4, 1)),
        ([Decimal("0.001"), Decimal("-7")], pa.decimal128(4, 3)),
        ([Decimal("1E+2"), Decimal("0.5")], pa.decimal128(4, 1)),
        ([Decimal("0.000")], pa.decimal128(3, 3)),
        ([10**30, Decimal("0.12345678901234")], pa.decimal256(45, 14)),
    ],
)
def test_unbounded_numeric_mixed_scales_fit_every_value(
    monkeypatch: pytest.MonkeyPatch,
    values: list[object],
    arrow_type: pa.DataType,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("amount", 1700),)
    connection.rows = [(value,) for value in values]
    table = warehouse.query("SELECT amount FROM probe")
    assert table.schema.field("amount").type == arrow_type
    assert table.column("amount").to_pylist() == [Decimal(value) for value in values]


def test_unbounded_numeric_beyond_decimal256_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("amount", 1700),)
    connection.rows = [(10**60,), (Decimal("0.1234567890123456789"),)]
    with pytest.raises(ArrowConversionError, match="could not be converted"):
        warehouse.query("SELECT amount FROM probe")


def test_numeric_that_cannot_fit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("amount", 1700, precision=4, scale=2),)
    connection.rows = [(Decimal("123.456"),)]
    with pytest.raises(ArrowConversionError, match="could not be converted"):
        warehouse.query("SELECT amount FROM probe")


def test_duplicate_column_names_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("value", 23), _Column("value", 25))
    connection.rows = [(1, "a")]
    with pytest.raises(ArrowConversionError, match="duplicate column"):
        warehouse.query("SELECT value, value FROM probe")


def test_query_error_maps_without_driver_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.execute_error = RuntimeError("syntax error near password=hunter2")
    with pytest.raises(QueryExecutionError, match="query execution failed") as raised:
        warehouse.query("SELECT broken")
    assert "hunter2" not in str(raised.value)
    assert connection.closed_cursors and connection.closed_cursors[0].closed


def test_parameter_errors_hide_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, _connection = _warehouse()

    class Secret:
        def __repr__(self) -> str:
            return "password=hunter2"

    with pytest.raises(QueryParameterError, match="parameters are invalid") as raised:
        warehouse.query("SELECT %(label)s", {"label": Secret()})
    assert "hunter2" not in str(raised.value)
    with pytest.raises(QueryParameterError, match="non-empty string"):
        warehouse.query("   ")
    with pytest.raises(QueryParameterError, match="mapping"):
        warehouse.query("SELECT 1", ["nope"])  # type: ignore[arg-type]


def test_driver_parameter_error_maps_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.execute_error = KeyError("missing parameter label")
    with pytest.raises(QueryParameterError, match="parameters are invalid") as raised:
        warehouse.query("SELECT %(label)s AS label", {"label": "kept-as-data"})
    assert "kept-as-data" not in str(raised.value)


def test_unknown_type_maps_to_arrow_conversion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("value", 99999),)
    connection.rows = [("raw",)]
    with pytest.raises(ArrowConversionError, match="could not be converted"):
        warehouse.query("SELECT value FROM probe")
    assert connection.closed_cursors and connection.closed_cursors[0].closed


def test_non_result_statement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = None
    with pytest.raises(QueryExecutionError, match="did not produce a result set"):
        warehouse.query("SET application_name = 'warehouse'")
    assert connection.closed_cursors and connection.closed_cursors[0].closed


def test_cursor_closes_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("value", 23),)
    connection.rows = [(1,)]
    warehouse.query("SELECT 1 AS value")
    assert len(connection.closed_cursors) == 1
    assert connection.closed_cursors[0].closed


def test_recovered_query_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.execute_error = RuntimeError("aborted")
    with pytest.raises(QueryExecutionError):
        warehouse.query("SELECT broken")
    connection.execute_error = None
    connection.description = (_Column("value", 23),)
    connection.rows = [(9,)]
    table = warehouse.query("SELECT 9 AS value")
    assert table.column("value")[0].as_py() == 9
    assert len(connection.closed_cursors) == 2


class _ClosedConnectionError(Exception):
    """Stands in for ``psycopg.OperationalError`` after a lost session."""


class _LosingCursor(_Cursor):
    connection: _LosingConnection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> None:
        if self.connection.fail_at == "execute":
            self.connection.lose()
        super().execute(query, params)

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.connection.fail_at == "fetchall":
            self.connection.lose()
        return super().fetchall()


class _LosingConnection(_Connection):
    """Marks itself closed when the chosen step fails, like a lost session."""

    def __init__(self, fail_at: str) -> None:
        super().__init__()
        self.fail_at = fail_at
        self.description = (_Column("value", 23),)
        self.rows = [(1,)]

    def lose(self) -> None:
        self.closed = True
        raise _ClosedConnectionError("server closed the connection unexpectedly")

    def cursor(self) -> _Cursor:
        if self.fail_at == "cursor":
            self.lose()
        self.cursor_calls += 1
        return _LosingCursor(self)


@pytest.mark.parametrize("fail_at", ["cursor", "execute", "fetchall"])
def test_lost_session_maps_safely_and_next_query_reconnects(
    monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    lost = _LosingConnection(fail_at)
    fresh = _Connection()
    fresh.description = (_Column("value", 23),)
    fresh.rows = [(2,)]
    connections = [lost, fresh]
    opens: list[str] = []

    def connect(dsn: str) -> _Connection:
        opens.append(dsn)
        return connections.pop(0)

    warehouse = PostgreSQLWarehouse(_config(), connect=connect)
    with pytest.raises(QueryExecutionError, match="connection failed") as raised:
        warehouse.query("SELECT 1 AS value")
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "unexpectedly" not in str(raised.value)

    table = warehouse.query("SELECT 2 AS value")
    assert table.column("value")[0].as_py() == 2
    assert opens == ["postgresql://unit", "postgresql://unit"]
    warehouse.close()
    assert fresh.closed


def test_query_error_on_open_connection_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    opens: list[str] = []
    warehouse, connection = _warehouse(opens=opens)
    connection.execute_error = RuntimeError("relation does not exist")
    with pytest.raises(QueryExecutionError, match="query execution failed"):
        warehouse.query("SELECT * FROM missing")
    connection.execute_error = None
    connection.description = (_Column("value", 23),)
    connection.rows = [(3,)]
    warehouse.query("SELECT 3 AS value")
    assert opens == ["postgresql://unit"]
    assert not connection.closed


def test_lost_session_after_close_still_does_not_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    opens: list[str] = []
    lost = _LosingConnection("execute")

    def connect(dsn: str) -> _Connection:
        opens.append(dsn)
        return lost

    warehouse = PostgreSQLWarehouse(_config(), connect=connect)
    with pytest.raises(QueryExecutionError, match="connection failed"):
        warehouse.query("SELECT 1 AS value")
    warehouse.close()
    with pytest.raises(WarehouseError, match="closed"):
        warehouse.query("SELECT 1 AS value")
    assert opens == ["postgresql://unit"]


class _Status:
    """Stands in for ``psycopg.pq.TransactionStatus`` (only ``name`` is read)."""

    def __init__(self, name: str | None) -> None:
        if name is not None:
            self.name = name


class _Info:
    def __init__(self) -> None:
        self.transaction_status: _Status = _Status("IDLE")


class _StatusCursor(_Cursor):
    connection: _StatusConnection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> None:
        self.connection.info.transaction_status = _Status(self.connection.status_after)
        super().execute(query, params)


class _StatusConnection(_Connection):
    """Reports a psycopg-style transaction status after each execute."""

    def __init__(self, status_after: str | None = "IDLE") -> None:
        super().__init__()
        self.info = _Info()
        self.status_after = status_after
        self.close_calls = 0
        self.close_error: BaseException | None = None
        self.description = (_Column("value", 23),)
        self.rows = [(1,)]

    def cursor(self) -> _Cursor:
        self.cursor_calls += 1
        return _StatusCursor(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()
        if self.close_error is not None:
            raise self.close_error


def _reconnecting_warehouse(
    first: _Connection,
) -> tuple[PostgreSQLWarehouse, list[str], _Connection]:
    fresh = _Connection()
    fresh.description = (_Column("value", 23),)
    fresh.rows = [(2,)]
    connections = [first, fresh]
    opens: list[str] = []

    def connect(dsn: str) -> _Connection:
        opens.append(dsn)
        return connections.pop(0)

    return PostgreSQLWarehouse(_config(), connect=connect), opens, fresh


@pytest.mark.parametrize("status", ["ACTIVE", "INERROR", "INTRANS", "UNKNOWN", None])
def test_failed_query_leaving_session_not_idle_reconnects(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    stuck = _StatusConnection(status_after=status)
    stuck.execute_error = RuntimeError("COPY cannot be used with this method")
    warehouse, opens, _fresh = _reconnecting_warehouse(stuck)
    with pytest.raises(QueryExecutionError, match="query execution failed") as raised:
        warehouse.query("COPY (SELECT 1) TO STDOUT")
    assert raised.value.__cause__ is None
    assert "COPY cannot" not in str(raised.value)
    assert stuck.close_calls == 1

    table = warehouse.query("SELECT 2 AS value")
    assert table.column("value")[0].as_py() == 2
    assert len(opens) == 2


def test_successful_query_leaving_transaction_open_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    in_transaction = _StatusConnection(status_after="INTRANS")
    warehouse, opens, _fresh = _reconnecting_warehouse(in_transaction)
    table = warehouse.query("SELECT 1 AS value; BEGIN")
    assert table.column("value")[0].as_py() == 1
    assert in_transaction.close_calls == 1
    assert warehouse.query("SELECT 2 AS value").column("value")[0].as_py() == 2
    assert len(opens) == 2


def test_non_result_statement_leaving_transaction_open_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    began = _StatusConnection(status_after="INTRANS")
    began.description = None
    warehouse, opens, _fresh = _reconnecting_warehouse(began)
    with pytest.raises(QueryExecutionError, match="did not produce a result set"):
        warehouse.query("BEGIN")
    assert began.close_calls == 1
    assert warehouse.query("SELECT 2 AS value").column("value")[0].as_py() == 2
    assert len(opens) == 2


def test_failed_query_leaving_session_idle_keeps_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    opens: list[str] = []
    idle = _StatusConnection(status_after="IDLE")
    warehouse, _ = _warehouse(idle, opens=opens)
    idle.execute_error = RuntimeError("canceling statement due to statement timeout")
    with pytest.raises(QueryExecutionError, match="query execution failed"):
        warehouse.query("SELECT pg_sleep(10)")
    idle.execute_error = None
    assert warehouse.query("SELECT 1 AS value").column("value")[0].as_py() == 1
    assert opens == ["postgresql://unit"]
    assert idle.close_calls == 0


class _FailingCloseCursor(_Cursor):
    def close(self) -> None:
        super().close()
        raise RuntimeError("cursor close failed for postgresql://unit")


class _FailingCursorCloseConnection(_StatusConnection):
    def cursor(self) -> _Cursor:
        self.cursor_calls += 1
        return _FailingCloseCursor(self)


def test_cursor_close_failure_is_logged_as_cursor_and_keeps_connection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    opens: list[str] = []
    connection = _FailingCursorCloseConnection(status_after="IDLE")
    warehouse, _ = _warehouse(connection, opens=opens)
    with caplog.at_level(logging.INFO, logger="research_platform.warehouse.postgres"):
        table = warehouse.query("SELECT 1 AS value")
    assert table.column("value")[0].as_py() == 1
    messages = [record.getMessage() for record in caplog.records]
    assert "postgres cursor close failed" in messages
    assert "postgres connection close failed" not in messages
    assert not any("postgresql://unit" in message for message in messages)
    assert connection.close_calls == 0
    assert opens == ["postgresql://unit"]


@pytest.mark.parametrize("status", ["ACTIVE", "INERROR"])
def test_interrupt_mid_query_still_discards_unusable_connection(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    stuck = _StatusConnection(status_after=status)
    stuck.execute_error = KeyboardInterrupt()
    warehouse, opens, _fresh = _reconnecting_warehouse(stuck)
    with pytest.raises(KeyboardInterrupt):
        warehouse.query("SELECT pg_sleep(60)")
    assert stuck.close_calls == 1
    assert warehouse.query("SELECT 2 AS value").column("value")[0].as_py() == 2
    assert len(opens) == 2


def test_discarded_connection_close_failure_is_logged_as_connection(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    stuck = _StatusConnection(status_after="INERROR")
    stuck.execute_error = RuntimeError("current transaction is aborted")
    stuck.close_error = RuntimeError("close failed for postgresql://unit")
    warehouse, _opens, _fresh = _reconnecting_warehouse(stuck)
    with caplog.at_level(logging.INFO, logger="research_platform.warehouse.postgres"):
        with pytest.raises(QueryExecutionError, match="query execution failed"):
            warehouse.query("SELECT 1/0")
    messages = [record.getMessage() for record in caplog.records]
    assert "postgres connection close failed" in messages
    assert not any("postgresql://unit" in message for message in messages)


def test_connection_failure_hides_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://user:password@db.example/app")

    def connect(_dsn: str) -> _Connection:
        raise RuntimeError("could not connect postgresql://user:password@db.example/app")

    warehouse = PostgreSQLWarehouse(_config(), connect=connect)
    with pytest.raises(QueryExecutionError, match="connection failed") as raised:
        warehouse.query("SELECT 1")
    assert "password" not in str(raised.value)
    assert "db.example" not in str(raised.value)


def test_close_and_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse, connection = _warehouse()
    connection.description = (_Column("value", 23),)
    connection.rows = [(1,)]
    with warehouse as active:
        active.query("SELECT 1 AS value")
    assert connection.closed
    with pytest.raises(WarehouseError, match="closed"):
        warehouse.query("SELECT 1 AS value")


def test_query_after_explicit_close_does_not_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    opens: list[str] = []
    warehouse, connection = _warehouse(opens=opens)
    connection.description = (_Column("value", 23),)
    connection.rows = [(1,)]
    warehouse.query("SELECT 1 AS value")
    warehouse.close()
    with pytest.raises(WarehouseError, match="closed"):
        warehouse.query("SELECT 1 AS value")
    assert opens == ["postgresql://unit"]
    assert connection.closed


def test_logs_omit_dsn_and_parameter_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://user:password@db.example/app")
    warehouse, connection = _warehouse()
    connection.description = (_Column("label", 25),)
    secret = "password=hunter2"
    connection.rows = [(secret,)]
    with caplog.at_level(logging.INFO, logger="research_platform.warehouse.postgres"):
        warehouse.query("SELECT %(label)s AS label", {"label": secret})
    assert "hunter2" not in caplog.text
    assert "db.example" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_real_connect_uses_read_only_autocommit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _FakePsycopg:
        @staticmethod
        def connect(dsn: str, **kwargs: object) -> _Connection:
            calls.append({"dsn": dsn, **kwargs})
            connection = _Connection()
            connection.description = (_Column("value", 23),)
            connection.rows = [(1,)]
            return connection

    monkeypatch.setattr(
        "research_platform.warehouse.postgres._import_psycopg",
        lambda: _FakePsycopg,
    )
    monkeypatch.setenv("POSTGRES_DSN_UNIT", "postgresql://unit")
    warehouse = PostgreSQLWarehouse(_config())
    table = warehouse.query("SELECT 1 AS value")
    assert table.column("value")[0].as_py() == 1
    assert calls[0]["autocommit"] is True
    options = str(calls[0]["options"])
    assert "default_transaction_read_only=on" in options
    assert "TimeZone=UTC" in options
    assert calls[0]["dsn"] == "postgresql://unit"
