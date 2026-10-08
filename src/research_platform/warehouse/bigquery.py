"""BigQuery Warehouse query adapter (read-only) with cost bounds.

Construction and import are side-effect free. ADC / Workload Identity resolution
and network I/O occur only when ``query`` runs without an injected client.
No datasets, tables, or projects are created here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa

from research_platform.analytics.bigquery.registry import DEFAULT_MAXIMUM_BYTES_BILLED
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.base import Warehouse
from research_platform.warehouse.errors import (
    ArrowConversionError,
    QueryBudgetExceededError,
    QueryExecutionError,
    QueryParameterError,
    QueryTimeoutError,
)

_LOGGER = logging.getLogger("research_platform.warehouse.bigquery")


@runtime_checkable
class _QueryJob(Protocol):
    total_bytes_processed: int | None
    errors: Sequence[object] | None

    def result(self, *, timeout: float | None = None) -> object: ...

    def to_arrow(self, create_bqstorage_client: bool = False) -> pa.Table: ...

    def cancel(self) -> None: ...


@runtime_checkable
class _Client(Protocol):
    def query(
        self,
        query: str,
        *,
        job_config: object | None = None,
        timeout: float | None = None,
    ) -> _QueryJob: ...


class BigQueryWarehouse(Warehouse):
    """Read-only BigQuery query adapter returning PyArrow tables.

    Cost policy:
    1. Always dry-run first (no cache) to obtain ``total_bytes_processed``.
    2. Fail closed with ``QueryBudgetExceededError`` when the estimate exceeds
       ``maximum_bytes_billed`` (default 10 GiB from analytical registry).
    3. Execute with ``maximum_bytes_billed`` set so the provider enforces the
       same bound.

    Parameters are bound as typed BigQuery query parameters — never interpolated
    into SQL. DuckDB SEMANTIC_ONLY validation is not a BigQuery dialect proof.
    """

    def __init__(
        self,
        warehouse: WarehouseConfig,
        cloud: CloudConfig,
        *,
        client: _Client | None = None,
        maximum_bytes_billed: int | None = None,
        job_timeout_seconds: float = 60.0,
    ) -> None:
        if warehouse.analytical != "bigquery":
            raise ValueError("BigQueryWarehouse requires warehouse.analytical='bigquery'")
        if not warehouse.bigquery_dataset:
            raise ValueError("BigQueryWarehouse requires warehouse.bigquery_dataset")
        if not cloud.project_id:
            raise ValueError("BigQueryWarehouse requires cloud.project_id")
        budget = (
            DEFAULT_MAXIMUM_BYTES_BILLED
            if maximum_bytes_billed is None
            else maximum_bytes_billed
        )
        if budget <= 0:
            raise ValueError("maximum_bytes_billed must be positive")
        if job_timeout_seconds <= 0:
            raise ValueError("job_timeout_seconds must be positive")
        self.warehouse = warehouse
        self.cloud = cloud
        self._client = client
        self.maximum_bytes_billed = budget
        self.job_timeout_seconds = job_timeout_seconds
        self._project_id = cloud.project_id
        self._dataset = warehouse.bigquery_dataset

    def query(
        self, sql: str, parameters: Mapping[str, object] | None = None
    ) -> pa.Table:
        if not isinstance(sql, str) or not sql.strip():
            raise QueryParameterError("sql must be a non-empty string")
        query_parameters = _bind_parameters(parameters)
        client = self._client_or_create()

        dry_config = self._job_config(
            dry_run=True,
            query_parameters=query_parameters,
            maximum_bytes_billed=None,
        )
        try:
            dry_job = client.query(sql, job_config=dry_config)
        except Exception as error:
            mapped = _map_provider_error(error, stage="dry_run")
            if mapped is error:
                raise
            raise mapped from error
        estimated = int(getattr(dry_job, "total_bytes_processed", 0) or 0)
        if estimated > self.maximum_bytes_billed:
            raise QueryBudgetExceededError(
                "dry-run total_bytes_processed exceeds maximum_bytes_billed: "
                f"{estimated} > {self.maximum_bytes_billed}"
            )

        exec_config = self._job_config(
            dry_run=False,
            query_parameters=query_parameters,
            maximum_bytes_billed=self.maximum_bytes_billed,
        )
        job: _QueryJob | None = None
        try:
            job = client.query(sql, job_config=exec_config)
            job.result(timeout=self.job_timeout_seconds)
        except Exception as error:
            if job is not None:
                _safe_cancel(job)
            mapped = _map_provider_error(error, stage="execute")
            if mapped is error:
                raise
            raise mapped from error

        try:
            try:
                table = job.to_arrow(create_bqstorage_client=False)
            except TypeError:
                # Older stubs may omit create_bqstorage_client.
                table = job.to_arrow()
        except Exception as error:
            raise ArrowConversionError(
                f"BigQuery Arrow conversion failed: {error}"
            ) from error

        if not isinstance(table, pa.Table):
            raise ArrowConversionError("BigQuery job did not return a PyArrow table")
        _LOGGER.info(
            "bigquery query completed",
            extra={
                "operation": "query",
                "estimated_bytes": estimated,
                "row_count": table.num_rows,
            },
        )
        return table

    def _client_or_create(self) -> _Client:
        if self._client is not None:
            return self._client
        from google.cloud import bigquery  # type: ignore[import-untyped]

        self._client = bigquery.Client(project=self._project_id)
        return self._client

    def _job_config(
        self,
        *,
        dry_run: bool,
        query_parameters: list[object],
        maximum_bytes_billed: int | None,
    ) -> object:
        from google.cloud import bigquery  # type: ignore[import-untyped]

        config = bigquery.QueryJobConfig(
            dry_run=dry_run,
            use_query_cache=False,
            query_parameters=query_parameters,
            default_dataset=f"{self._project_id}.{self._dataset}",
        )
        if maximum_bytes_billed is not None:
            config.maximum_bytes_billed = maximum_bytes_billed
        return config


def _bind_parameters(parameters: Mapping[str, object] | None) -> list[object]:
    if parameters is None:
        return []
    if not isinstance(parameters, Mapping):
        raise QueryParameterError("parameters must be a mapping of name to value")
    from google.cloud import bigquery  # type: ignore[import-untyped]

    bound: list[object] = []
    for name, value in parameters.items():
        if not isinstance(name, str) or not name.strip():
            raise QueryParameterError("parameter names must be non-empty strings")
        if not name.isidentifier():
            raise QueryParameterError(f"parameter name is not a valid identifier: {name!r}")
        bound.append(_scalar_parameter(bigquery, name, value))
    return bound


def _scalar_parameter(bigquery: Any, name: str, value: object) -> object:
    if value is None:
        raise QueryParameterError(
            f"parameter {name!r} is None; omit the parameter or pass a typed value"
        )
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int) and not isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    if isinstance(value, float):
        return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
    if isinstance(value, str):
        return bigquery.ScalarQueryParameter(name, "STRING", value)
    if isinstance(value, datetime):
        return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
    if isinstance(value, date):
        return bigquery.ScalarQueryParameter(name, "DATE", value)
    if isinstance(value, (list, tuple)):
        if not value:
            raise QueryParameterError(f"array parameter {name!r} must be non-empty")
        first = value[0]
        if isinstance(first, bool):
            array_type = "BOOL"
        elif isinstance(first, int) and not isinstance(first, bool):
            array_type = "INT64"
        elif isinstance(first, float):
            array_type = "FLOAT64"
        elif isinstance(first, str):
            array_type = "STRING"
        else:
            raise QueryParameterError(
                f"unsupported array element type for parameter {name!r}: {type(first)!r}"
            )
        return bigquery.ArrayQueryParameter(name, array_type, list(value))
    raise QueryParameterError(
        f"unsupported parameter type for {name!r}: {type(value)!r}"
    )


def _map_provider_error(error: BaseException, *, stage: str) -> BaseException:
    """Map well-understood provider failures; leave others intact (chained by caller)."""
    name = type(error).__name__
    message = str(error).lower()
    if isinstance(
        error,
        (
            QueryBudgetExceededError,
            QueryParameterError,
            ArrowConversionError,
            QueryTimeoutError,
            QueryExecutionError,
        ),
    ):
        return error
    if name in {"TimeoutError", "FuturesTimeoutError"} or "timed out" in message:
        return QueryTimeoutError(f"BigQuery {stage} timed out: {error}")
    if "bytes billed" in message or "maximum_bytes_billed" in message:
        return QueryBudgetExceededError(str(error))
    if name in {"BadRequest"} or (
        name in {"GoogleAPICallError", "GoogleAPIError"}
        and ("parameter" in message or "syntax" in message)
    ):
        return QueryExecutionError(f"BigQuery {stage} failed: {error}")
    # Permission/transport/unknown provider failures remain the original type.
    return error


def _safe_cancel(job: _QueryJob) -> None:
    cancel = getattr(job, "cancel", None)
    if not callable(cancel):
        return
    try:
        cancel()
    except Exception:  # noqa: BLE001 - best-effort cleanup only
        _LOGGER.info(
            "bigquery job cancel failed during cleanup",
            extra={"operation": "cancel"},
        )
