"""Deterministic in-memory BigQuery client/job fakes for Warehouse tests.

No network, credentials, project, or dataset provisioning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa


class FakeForbidden(Exception):
    code = 403


class FakeTransportError(Exception):
    """Generic transport interruption."""


class FakeQueryFailed(Exception):
    """Provider-reported query failure."""


@dataclass
class FakeQueryJob:
    sql: str
    job_config: Any
    rows: pa.Table
    total_bytes_processed: int = 0
    errors: list[object] | None = None
    cancelled: bool = False
    fail_on_result: Exception | None = None
    fail_on_to_arrow: Exception | None = None

    def result(self, *, timeout: float | None = None) -> FakeQueryJob:
        del timeout
        if self.fail_on_result is not None:
            raise self.fail_on_result
        return self

    def to_arrow(self, create_bqstorage_client: bool = False) -> pa.Table:
        del create_bqstorage_client
        if self.fail_on_to_arrow is not None:
            raise self.fail_on_to_arrow
        return self.rows

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class FakeBigQueryClient:
    """Minimal client.query boundary for BigQueryWarehouse injection."""

    default_bytes: int = 1024
    default_table: pa.Table = field(
        default_factory=lambda: pa.table({"value": pa.array([1], type=pa.int64())})
    )
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_permission: bool = False
    fail_transport: bool = False
    fail_query: bool = False
    fail_on_result: Exception | None = None
    fail_on_to_arrow: Exception | None = None
    # When set, dry-run reports this estimate (else default_bytes).
    dry_run_bytes: int | None = None
    execute_table: pa.Table | None = None

    def query(
        self,
        query: str,
        *,
        job_config: Any = None,
        timeout: float | None = None,
    ) -> FakeQueryJob:
        del timeout
        dry_run = bool(getattr(job_config, "dry_run", False)) if job_config else False
        maximum_bytes_billed = getattr(job_config, "maximum_bytes_billed", None)
        query_parameters = list(getattr(job_config, "query_parameters", []) or [])
        default_dataset = getattr(job_config, "default_dataset", None)
        use_query_cache = getattr(job_config, "use_query_cache", None)
        self.calls.append(
            {
                "sql": query,
                "dry_run": dry_run,
                "maximum_bytes_billed": maximum_bytes_billed,
                "query_parameters": query_parameters,
                "default_dataset": str(default_dataset) if default_dataset else None,
                "use_query_cache": use_query_cache,
            }
        )
        if self.fail_permission:
            raise FakeForbidden("permission denied")
        if self.fail_transport:
            raise FakeTransportError("transport interrupted")
        if self.fail_query:
            raise FakeQueryFailed("query failed")

        if dry_run:
            estimated = (
                self.default_bytes if self.dry_run_bytes is None else self.dry_run_bytes
            )
            return FakeQueryJob(
                sql=query,
                job_config=job_config,
                rows=pa.table({}),
                total_bytes_processed=estimated,
            )

        table = self.default_table if self.execute_table is None else self.execute_table
        return FakeQueryJob(
            sql=query,
            job_config=job_config,
            rows=table,
            total_bytes_processed=self.default_bytes,
            fail_on_result=self.fail_on_result,
            fail_on_to_arrow=self.fail_on_to_arrow,
        )
