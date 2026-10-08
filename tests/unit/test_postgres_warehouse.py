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
