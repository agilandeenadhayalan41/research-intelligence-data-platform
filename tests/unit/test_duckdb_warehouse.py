"""Offline DuckDBWarehouse query, parameter, Arrow, and lifecycle tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.duckdb import DuckDBWarehouse
from research_platform.warehouse.errors import (
    QueryExecutionError,
    QueryParameterError,
    WarehouseError,
)

_SQL_LOOKING = "'; DROP TABLE something; --"


@pytest.fixture
def memory_warehouse() -> Iterator[DuckDBWarehouse]:
    adapter = DuckDBWarehouse(WarehouseConfig())
    try:
        yield adapter
    finally:
        adapter.close()


def test_constructor_does_not_open_connection_or_create_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    real_connect = duckdb.connect

    def spy(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", spy)
    path = tmp_path / "warehouse.duckdb"
    file_adapter = DuckDBWarehouse(WarehouseConfig(duckdb_path=str(path)))
    memory_adapter = DuckDBWarehouse(WarehouseConfig(duckdb_path=":memory:"))
    assert calls == []
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
    file_adapter.close()
    memory_adapter.close()
    assert calls == []
    assert not path.exists()


def test_memory_query_returns_arrow_table(memory_warehouse: DuckDBWarehouse) -> None:
    table = memory_warehouse.query("SELECT 1 AS value")
    assert isinstance(table, pa.Table)
    assert table.to_pylist() == [{"value": 1}]


def test_explicit_file_database_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "warehouse.duckdb"
    config = WarehouseConfig(duckdb_path=str(path))
    first = DuckDBWarehouse(config)
    assert not path.exists()
    try:
        first.query("CREATE TABLE kept (id INTEGER)")
        first.query("INSERT INTO kept VALUES ($id)", {"id": 7})
        assert path.is_file()
        seen = first.query("SELECT id FROM kept")
        assert seen.to_pylist() == [{"id": 7}]
    finally:
        first.close()
    assert path.is_file()
    with pytest.raises(WarehouseError, match="closed"):
        first.query("SELECT id FROM kept")

    second = DuckDBWarehouse(config)
    try:
        assert second.query("SELECT id FROM kept").to_pylist() == [{"id": 7}]
    finally:
        second.close()


def test_repeated_memory_queries_keep_adapter_state(
    memory_warehouse: DuckDBWarehouse,
) -> None:
    memory_warehouse.query("CREATE TABLE kept (id INTEGER)")
    memory_warehouse.query("INSERT INTO kept VALUES (1)")
    memory_warehouse.query("INSERT INTO kept VALUES ($id)", {"id": 2})
    table = memory_warehouse.query("SELECT id FROM kept ORDER BY id")
    assert table.to_pylist() == [{"id": 1}, {"id": 2}]

    other = DuckDBWarehouse(WarehouseConfig())
    try:
        with pytest.raises(QueryExecutionError):
            other.query("SELECT id FROM kept")
    finally:
        other.close()


def test_named_parameter_binding(memory_warehouse: DuckDBWarehouse) -> None:
    table = memory_warehouse.query(
        "SELECT $label AS label, $count AS count",
        {"label": "works", "count": 3},
    )
    assert table.to_pylist() == [{"label": "works", "count": 3}]


def test_sql_looking_parameter_stays_a_value(memory_warehouse: DuckDBWarehouse) -> None:
    memory_warehouse.query("CREATE TABLE something (id INTEGER)")
    memory_warehouse.query("INSERT INTO something VALUES (1)")
    table = memory_warehouse.query(
        "SELECT :payload AS payload",
        {"payload": _SQL_LOOKING},
    )
    assert table.to_pylist() == [{"payload": _SQL_LOOKING}]
    remaining = memory_warehouse.query("SELECT COUNT(*)::BIGINT AS n FROM something")
    assert remaining.to_pylist() == [{"n": 1}]


def test_colon_placeholders_skip_literals_comments_and_casts(
    memory_warehouse: DuckDBWarehouse,
) -> None:
    table = memory_warehouse.query(
        """
        SELECT ':payload' AS literal,
               $$:payload$$ AS dollar_literal,
               1::INTEGER AS cast_value,
               :payload AS payload
        -- :payload must stay in this comment
        """,
        {"payload": "kept"},
    )
    assert table.to_pylist() == [
        {"literal": ":payload", "dollar_literal": ":payload", "cast_value": 1, "payload": "kept"}
    ]


def test_null_parameters_and_null_columns_are_preserved(
    memory_warehouse: DuckDBWarehouse,
) -> None:
    table = memory_warehouse.query(
        """
        SELECT CAST(NULL AS INTEGER) AS id,
               CAST(NULL AS VARCHAR) AS name,
               $label AS label
        """,
        {"label": None},
    )
    assert table.to_pylist() == [{"id": None, "name": None, "label": None}]
    assert table.column("id")[0].as_py() is None
    assert table.column("name")[0].as_py() is None
    assert table.column("label")[0].as_py() is None


def test_empty_result_keeps_schema(memory_warehouse: DuckDBWarehouse) -> None:
    table = memory_warehouse.query(
        "SELECT 1::INTEGER AS id, 'x'::VARCHAR AS name WHERE FALSE"
    )
    assert table.num_rows == 0
    assert isinstance(table, pa.Table)
    assert table.schema.field("id").type == pa.int32()
    assert table.schema.field("name").type == pa.string()


def test_arrow_scalar_type_fidelity(memory_warehouse: DuckDBWarehouse) -> None:
    table = memory_warehouse.query(
        """
        SELECT
          $i::INTEGER AS int_value,
          $bi::BIGINT AS bigint_value,
          $f::DOUBLE AS float_value,
          $s::VARCHAR AS string_value,
          $b::BOOLEAN AS bool_value,
          $d::DATE AS date_value,
          $t::TIME AS time_value,
          $ts::TIMESTAMP AS timestamp_value
        """,
        {
            "i": 7,
            "bi": 9,
            "f": 1.5,
            "s": "abc",
            "b": True,
            "d": date(2020, 1, 2),
            "t": time(1, 2, 3),
            "ts": datetime(2020, 1, 2, 3, 4, 5),
        },
    )
    assert table.schema.field("int_value").type == pa.int32()
    assert table.schema.field("bigint_value").type == pa.int64()
    assert table.schema.field("float_value").type == pa.float64()
    assert table.schema.field("string_value").type == pa.string()
    assert table.schema.field("bool_value").type == pa.bool_()
    assert table.schema.field("date_value").type == pa.date32()
    assert table.schema.field("time_value").type == pa.time64("us")
    assert table.schema.field("timestamp_value").type == pa.timestamp("us")
    assert table.to_pylist() == [
        {
            "int_value": 7,
            "bigint_value": 9,
            "float_value": 1.5,
            "string_value": "abc",
            "bool_value": True,
            "date_value": date(2020, 1, 2),
            "time_value": time(1, 2, 3),
            "timestamp_value": datetime(2020, 1, 2, 3, 4, 5),
        }
    ]


def test_invalid_sql_fails_instead_of_returning_empty(
    memory_warehouse: DuckDBWarehouse,
) -> None:
    with pytest.raises(QueryExecutionError, match="syntax error"):
        memory_warehouse.query("SELECT FROM")


def test_parameter_mismatch_and_unsupported_type_fail_without_payload(
    memory_warehouse: DuckDBWarehouse,
) -> None:
    with pytest.raises(QueryParameterError, match="do not match"):
        memory_warehouse.query("SELECT $missing AS missing")
    with pytest.raises(QueryParameterError, match="parameters must be a mapping"):
        memory_warehouse.query("SELECT 1 AS n", ["not-a-mapping"])  # type: ignore[arg-type]
    with pytest.raises(QueryParameterError, match="unsupported parameter type") as exc_info:
        memory_warehouse.query("SELECT $value AS value", {"value": {"secret": _SQL_LOOKING}})
    assert _SQL_LOOKING not in str(exc_info.value)
    with pytest.raises(QueryExecutionError, match="value conversion") as conversion:
        memory_warehouse.query(
            "SELECT $value::INTEGER AS value",
            {"value": _SQL_LOOKING},
        )
    assert _SQL_LOOKING not in str(conversion.value)
    assert conversion.value.__cause__ is None


def test_query_log_omits_parameter_payload(
    memory_warehouse: DuckDBWarehouse, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="research_platform.warehouse.duckdb"):
        memory_warehouse.query("SELECT $payload AS payload", {"payload": _SQL_LOOKING})
    assert _SQL_LOOKING not in caplog.text


def test_close_is_idempotent_and_context_manager_closes(tmp_path: Path) -> None:
    path = tmp_path / "closed.duckdb"
    with DuckDBWarehouse(WarehouseConfig(duckdb_path=str(path))) as adapter:
        adapter.query("SELECT 1 AS n")
        assert path.is_file()
    adapter.close()
    with pytest.raises(WarehouseError, match="closed"):
        adapter.query("SELECT 1 AS n")
    assert path.is_file()


def test_close_before_query_does_not_create_file(tmp_path: Path) -> None:
    path = tmp_path / "never-opened.duckdb"
    adapter = DuckDBWarehouse(WarehouseConfig(duckdb_path=str(path)))
    adapter.close()
    with pytest.raises(WarehouseError, match="closed"):
        adapter.query("SELECT 1 AS n")
    assert not path.exists()


def test_missing_parent_directory_is_a_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "warehouse.duckdb"
    adapter = DuckDBWarehouse(WarehouseConfig(duckdb_path=str(path)))
    try:
        with pytest.raises(WarehouseError, match="could not be opened"):
            adapter.query("SELECT 1 AS n")
    finally:
        adapter.close()
    assert not path.exists()


def test_remote_database_paths_are_rejected() -> None:
    for remote in ("s3://bucket/file.duckdb", "https://example.com/x.duckdb", "md:remote"):
        with pytest.raises(ValueError, match="local database file path"):
            DuckDBWarehouse(WarehouseConfig(duckdb_path=remote))


def test_external_read_is_rejected(memory_warehouse: DuckDBWarehouse) -> None:
    with pytest.raises(QueryExecutionError, match="external or network access is disabled"):
        memory_warehouse.query("SELECT * FROM 'https://example.com/x.csv'")


def test_non_duckdb_analytical_config_is_rejected() -> None:
    with pytest.raises(ValueError, match="analytical='duckdb'"):
        DuckDBWarehouse(WarehouseConfig(analytical="bigquery"))
