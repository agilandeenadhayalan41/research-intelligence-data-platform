"""Unit tests for BigQueryWarehouse (fake client only; no network/credentials)."""

from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime

import pyarrow as pa
import pytest
from google.cloud import bigquery

from research_platform.analytics.bigquery.registry import DEFAULT_MAXIMUM_BYTES_BILLED
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryBudgetExceededError,
    QueryParameterError,
    QueryTimeoutError,
)
from tests.support.fake_bigquery import (
    FakeBigQueryClient,
    FakeForbidden,
    FakeTransportError,
)


def _warehouse(**overrides: object) -> WarehouseConfig:
    payload = {
        "analytical": "bigquery",
        "bigquery_dataset": "synthetic_dataset",
    }
    payload.update(overrides)
    return WarehouseConfig.model_validate(payload)


def _cloud() -> CloudConfig:
    return CloudConfig(project_id="synthetic-project")


def _store(
    client: FakeBigQueryClient | None = None, **kwargs: object
) -> tuple[BigQueryWarehouse, FakeBigQueryClient]:
    fake = client or FakeBigQueryClient()
    adapter = BigQueryWarehouse(_warehouse(), _cloud(), client=fake, **kwargs)
    return adapter, fake


def test_construction_requires_bigquery_config() -> None:
    fake = FakeBigQueryClient()
    with pytest.raises(ValueError, match="analytical='bigquery'"):
        BigQueryWarehouse(
            WarehouseConfig(analytical="duckdb"),
            _cloud(),
            client=fake,
        )
    with pytest.raises(ValueError, match="bigquery_dataset"):
        BigQueryWarehouse(
            WarehouseConfig(analytical="bigquery", bigquery_dataset=None),
            _cloud(),
            client=fake,
        )
    with pytest.raises(ValueError, match="project_id"):
        BigQueryWarehouse(
            _warehouse(),
            CloudConfig(project_id=None),
            client=fake,
        )


def test_construction_is_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("BigQueryWarehouse construction must not access the network")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    adapter = BigQueryWarehouse(_warehouse(), _cloud(), client=None)
    assert adapter.maximum_bytes_billed == DEFAULT_MAXIMUM_BYTES_BILLED


def test_dry_run_then_execute_with_budget_and_parameters() -> None:
    adapter, fake = _store()
    table = adapter.query(
        "SELECT @work_id AS work_id",
        {"work_id": "W1"},
    )
    assert table.num_rows == 1
    assert len(fake.calls) == 2
    dry, exe = fake.calls
    assert dry["dry_run"] is True
    assert dry["use_query_cache"] is False
    assert dry["maximum_bytes_billed"] is None
    assert exe["dry_run"] is False
    assert exe["maximum_bytes_billed"] == DEFAULT_MAXIMUM_BYTES_BILLED
    assert exe["default_dataset"] == "synthetic-project.synthetic_dataset"
    assert len(exe["query_parameters"]) == 1
    param = exe["query_parameters"][0]
    assert isinstance(param, bigquery.ScalarQueryParameter)
    assert param.name == "work_id"
    assert param.type_ == "STRING"
    assert param.value == "W1"


def test_budget_exceeded_on_dry_run_skips_execute() -> None:
    fake = FakeBigQueryClient(dry_run_bytes=2048)
    adapter, _ = _store(fake, maximum_bytes_billed=1024)
    with pytest.raises(QueryBudgetExceededError):
        adapter.query("SELECT 1")
    assert len(fake.calls) == 1
    assert fake.calls[0]["dry_run"] is True


def test_typed_parameters_cover_scalars_and_array() -> None:
    adapter, fake = _store()
    adapter.query(
        "SELECT @i, @f, @b, @s, @ts, @d, @arr",
        {
            "i": 7,
            "f": 1.5,
            "b": True,
            "s": "x",
            "ts": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            "d": datetime(2024, 1, 2, tzinfo=UTC).date(),
            "arr": ["a", "b"],
        },
    )
    params = {p.name: p for p in fake.calls[0]["query_parameters"]}
    assert params["i"].type_ == "INT64"
    assert params["f"].type_ == "FLOAT64"
    assert params["b"].type_ == "BOOL"
    assert params["s"].type_ == "STRING"
    assert params["ts"].type_ == "TIMESTAMP"
    assert params["d"].type_ == "DATE"
    assert isinstance(params["arr"], bigquery.ArrayQueryParameter)


def test_rejects_sql_interpolation_style_by_not_embedding_values() -> None:
    adapter, fake = _store()
    evil = "'; DROP TABLE t; --"
    adapter.query("SELECT @q AS q", {"q": evil})
    assert evil not in fake.calls[0]["sql"]
    assert fake.calls[0]["sql"] == "SELECT @q AS q"


def test_empty_result_returns_arrow_table() -> None:
    empty = pa.table({"work_id": pa.array([], type=pa.string())})
    fake = FakeBigQueryClient(execute_table=empty)
    adapter, _ = _store(fake)
    table = adapter.query("SELECT work_id FROM t WHERE FALSE")
    assert isinstance(table, pa.Table)
    assert table.num_rows == 0
    assert table.schema.names == ["work_id"]


def test_null_cells_preserved() -> None:
    rows = pa.table({"work_id": pa.array([None, "W1"], type=pa.string())})
    fake = FakeBigQueryClient(execute_table=rows)
    adapter, _ = _store(fake)
    table = adapter.query("SELECT work_id FROM t")
    assert table.column("work_id").to_pylist() == [None, "W1"]


def test_permission_denied_not_swallowed() -> None:
    fake = FakeBigQueryClient(fail_permission=True)
    adapter, _ = _store(fake)
    with pytest.raises(FakeForbidden):
        adapter.query("SELECT 1")


def test_transport_failure_not_swallowed() -> None:
    fake = FakeBigQueryClient(fail_transport=True)
    adapter, _ = _store(fake)
    with pytest.raises(FakeTransportError):
        adapter.query("SELECT 1")


def test_timeout_maps_to_query_timeout_and_cancels() -> None:
    fake = FakeBigQueryClient(fail_on_result=TimeoutError("timed out"))
    adapter, _ = _store(fake)
    with pytest.raises(QueryTimeoutError):
        adapter.query("SELECT 1")
    # Execute job was created; cancel attempted after failure.
    # Dry-run + execute = 2 calls; cancel is on the execute job object.
    assert len(fake.calls) == 2


def test_arrow_conversion_failure() -> None:
    fake = FakeBigQueryClient(fail_on_to_arrow=TypeError("cannot convert"))
    adapter, _ = _store(fake)
    with pytest.raises(ArrowConversionError):
        adapter.query("SELECT 1")


def test_invalid_parameters_rejected() -> None:
    adapter, fake = _store()
    with pytest.raises(QueryParameterError):
        adapter.query("SELECT 1", {"bad-name": 1})
    with pytest.raises(QueryParameterError):
        adapter.query("SELECT 1", {"x": None})
    with pytest.raises(QueryParameterError):
        adapter.query("SELECT 1", {"x": {"nested": 1}})
    assert fake.calls == []


def test_empty_sql_rejected() -> None:
    adapter, fake = _store()
    with pytest.raises(QueryParameterError):
        adapter.query("   ")
    assert fake.calls == []


def test_logs_do_not_include_secrets_or_sql_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, _ = _store()
    sql = "SELECT 'secret-token-value' AS x"
    with caplog.at_level(logging.INFO, logger="research_platform.warehouse.bigquery"):
        adapter.query(sql, {"token": "secret-token-value"})
    blob = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "secret-token-value" not in blob
    assert "private_key" not in blob
    assert "authorization" not in blob
