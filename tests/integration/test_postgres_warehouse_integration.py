"""Separately invoked PostgreSQL Warehouse query tests.

Run via ``make test-postgres-warehouse`` when ``POSTGRES_DSN`` points at a local
PostgreSQL instance. Default ``make test`` excludes this module (``not postgres``).
These tests are the local runtime evidence. Fake-driver unit tests are not.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import uuid4

import pyarrow as pa
import pytest

pytest.importorskip("psycopg")

import psycopg

from research_platform.config.models import WarehouseConfig
from research_platform.warehouse.errors import QueryExecutionError, WarehouseError
from research_platform.warehouse.postgres import PostgreSQLWarehouse

DSN = os.environ.get("POSTGRES_DSN", "").strip()
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="POSTGRES_DSN unset; skipping PostgreSQL tests"),
]

_PAYLOAD = "'; DROP TABLE works; --"


def test_local_postgres_query_adapter() -> None:
    table_name = f"wh_probe_{uuid4().hex}"
    setup = psycopg.connect(DSN, autocommit=True)
    warehouse = PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN"))
    try:
        setup.execute(
            f"""
            CREATE TABLE {table_name} (
                id integer,
                label text,
                flag boolean,
                amount numeric(12, 4),
                day date,
                moment timestamp,
                moment_tz timestamptz,
                clock_time time
            )
            """
        )
        setup.execute(
            f"""
            INSERT INTO {table_name} (
                id, label, flag, amount, day, moment, moment_tz, clock_time
            ) VALUES (
                %(id)s, %(label)s, %(flag)s, %(amount)s, %(day)s,
                %(moment)s, %(moment_tz)s, %(clock_time)s
            )
            """,
            {
                "id": 7,
                "label": _PAYLOAD,
                "flag": True,
                "amount": Decimal("12.3400"),
                "day": date(2024, 3, 4),
                "moment": datetime(2024, 3, 4, 5, 6, 7),
                "moment_tz": datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC),
                "clock_time": time(5, 6, 7),
            },
        )
        setup.execute(
            f"INSERT INTO {table_name} (id, label) VALUES (%(id)s, %(label)s)",
            {"id": None, "label": None},
        )

        selected = warehouse.query(
            f"""
            SELECT id, label, flag, amount, day, moment, moment_tz, clock_time
            FROM {table_name}
            WHERE label = %(label)s
            """,
            {"label": _PAYLOAD},
        )
        assert selected.num_rows == 1
        row = selected.to_pylist()[0]
        assert row["id"] == 7
        assert row["label"] == _PAYLOAD
        assert row["flag"] is True
        assert row["amount"] == Decimal("12.3400")
        assert row["day"] == date(2024, 3, 4)
        assert row["moment"] == datetime(2024, 3, 4, 5, 6, 7)
        assert row["moment_tz"] == datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC)
        assert row["clock_time"] == time(5, 6, 7)

        nulls = warehouse.query(
            f"SELECT id, label FROM {table_name} WHERE label IS NULL"
        )
        assert nulls.num_rows == 1
        assert nulls.column("id")[0].as_py() is None
        assert nulls.column("label")[0].as_py() is None

        empty = warehouse.query(
            f"SELECT id, label FROM {table_name} WHERE false"
        )
        assert empty.num_rows == 0
        assert empty.column_names == ["id", "label"]

        with pytest.raises(QueryExecutionError, match="query execution failed") as raised:
            warehouse.query("SELECT * FROM wh_probe_missing_relation")
        assert DSN not in str(raised.value)
        assert _PAYLOAD not in str(raised.value)

        with pytest.raises(QueryExecutionError, match="query execution failed"):
            warehouse.query(
                f"INSERT INTO {table_name} (id) VALUES (%(id)s)",
                {"id": 1},
            )

        again = warehouse.query("SELECT 1 AS value")
        assert again.column("value")[0].as_py() == 1

        first_pid = warehouse.query("SELECT pg_backend_pid() AS pid")
        second_pid = warehouse.query("SELECT pg_backend_pid() AS pid")
        assert first_pid.column("pid")[0].as_py() == second_pid.column("pid")[0].as_py()

        warehouse.close()
        with pytest.raises(WarehouseError, match="closed"):
            warehouse.query("SELECT 1 AS value")
    finally:
        warehouse.close()
        setup.execute(f"DROP TABLE IF EXISTS {table_name}")
        setup.close()


def test_local_postgres_lost_session_fails_safely_then_reconnects() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        first_pid = warehouse.query("SELECT pg_backend_pid() AS pid").column("pid")[0].as_py()
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (first_pid,))

        with pytest.raises(QueryExecutionError, match="connection failed") as raised:
            warehouse.query("SELECT 1 AS value")
        assert raised.value.__cause__ is None
        assert DSN not in str(raised.value)

        second_pid = warehouse.query("SELECT pg_backend_pid() AS pid").column("pid")[0].as_py()
        assert second_pid != first_pid
        assert warehouse.query("SHOW transaction_read_only").column(0)[0].as_py() == "on"


def _pid(warehouse: PostgreSQLWarehouse) -> int:
    return warehouse.query("SELECT pg_backend_pid() AS pid").column("pid")[0].as_py()


def test_local_postgres_copy_to_stdout_does_not_wedge_the_adapter() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        before = _pid(warehouse)
        with pytest.raises(QueryExecutionError, match="query execution failed") as raised:
            warehouse.query("COPY (SELECT 1) TO STDOUT")
        assert raised.value.__cause__ is None
        assert warehouse.query("SELECT 1 AS value").column("value")[0].as_py() == 1
        assert _pid(warehouse) != before


def test_local_postgres_aborted_caller_transaction_does_not_wedge_the_adapter() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        with pytest.raises(QueryExecutionError, match="did not produce a result set"):
            warehouse.query("BEGIN")
        with pytest.raises(QueryExecutionError, match="query execution failed"):
            warehouse.query("SELECT 1/0 AS value")
        assert warehouse.query("SELECT 1 AS value").column("value")[0].as_py() == 1
        assert warehouse.query("SHOW transaction_read_only").column(0)[0].as_py() == "on"


def test_local_postgres_caller_transaction_is_not_carried_into_later_queries() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        opened = warehouse.query("SELECT pg_backend_pid() AS pid; BEGIN")
        caller_pid = opened.column("pid")[0].as_py()
        # Inside a transaction now() is frozen; in autocommit it advances.
        first = warehouse.query("SELECT now() AS t").column("t")[0].as_py()
        warehouse.query("SELECT 1 AS slept FROM pg_sleep(0.01)")
        second = warehouse.query("SELECT now() AS t").column("t")[0].as_py()
        assert second > first
        assert _pid(warehouse) != caller_pid


def test_local_postgres_statement_timeout_keeps_the_connection() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        warehouse.query("SELECT set_config('statement_timeout', '100', false) AS s")
        before = _pid(warehouse)
        with pytest.raises(QueryExecutionError, match="query execution failed"):
            warehouse.query("SELECT 1 AS slept FROM pg_sleep(2)")
        assert _pid(warehouse) == before
        timeout = warehouse.query("SHOW statement_timeout").column(0)[0].as_py()
        assert timeout == "100ms"


def test_local_postgres_unbounded_numeric_with_mixed_scales() -> None:
    with PostgreSQLWarehouse(WarehouseConfig(postgres_dsn_env="POSTGRES_DSN")) as warehouse:
        mixed = warehouse.query(
            "SELECT v::numeric AS v FROM (VALUES (12345678.0), (0.12345)) AS t(v)"
        )
        assert mixed.schema.field("v").type == pa.decimal128(13, 5)
        assert mixed.column("v").to_pylist() == [
            Decimal("12345678.0"),
            Decimal("0.12345"),
        ]

        union = warehouse.query(
            "SELECT 1.5::numeric AS v UNION ALL SELECT 100::numeric ORDER BY v"
        )
        assert union.schema.field("v").type == pa.decimal128(4, 1)
        assert union.column("v").to_pylist() == [Decimal("1.5"), Decimal("100")]

        averages = warehouse.query(
            "SELECT g, avg(v) AS a FROM (VALUES (1, 1), (1, 2), (2, 1000000))"
            " AS t(g, v) GROUP BY g ORDER BY g"
        )
        assert averages.column("a").to_pylist() == [
            Decimal("1.5"),
            Decimal("1000000"),
        ]
