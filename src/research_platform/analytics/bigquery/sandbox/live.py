"""Shared helpers for the manual live BigQuery sandbox scripts (``scripts/``).

Nothing here runs at import or creates a client: every BigQuery call goes
through the client a script passes to ``Run``. ``Run`` labels, caps, records
and budgets each query and load job; ``AdapterClient`` does the same for the
jobs of an unmodified ``BigQueryWarehouse``. A query or load job that times out
or otherwise stops being waited on is cancelled and polled until DONE (bounded)
before it is recorded, so whatever it billed still counts against the run
budget. ``ensure_fits`` refuses a job whose cap exceeds the remaining budget and
``sized_timeout`` sizes a result timeout from a dry-run estimate; a script
applies them or not (``scripts/bigquery_sandbox_103.py`` does).
Recorded evidence carries job ids, bytes, slot ms, states and the submitting
``user_email``; never tokens or credentials.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from google.cloud import bigquery

DATASET = "openalex"
MIB = 1024 * 1024
GIB = 1024 * MIB
BILLING_FLOOR = 10 * MIB
TABLE_REF = re.compile(r"`openalex\.([a-z_]+)`")


class Run:
    def __init__(
        self,
        client: bigquery.Client,
        run_id: str,
        budget_bytes: int,
        log: Path | None = None,
        *,
        job_timeout_s: float = 600.0,
        timeout_s_per_gib: float = 60.0,
        cancel_wait_s: float = 60.0,
        poll_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.run_id = run_id
        self.budget_bytes = budget_bytes
        self.billed = 0
        self.jobs: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.log = log  # jobs.jsonl, appended per job so a crash keeps the record
        self.job_timeout_s = job_timeout_s
        self.timeout_s_per_gib = timeout_s_per_gib  # added to job_timeout_s per GiB of a dry-run estimate
        self.cancel_wait_s = cancel_wait_s  # bound on waiting for a cancelled job to reach DONE
        self.poll_s = poll_s
        self.sleep = sleep

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail != '' else ''}", flush=True)

    def query(
        self,
        step: str,
        sql: str,
        *,
        params: list[Any] | None = None,
        cap: int | None = None,
        dry_run: bool = False,
        expect_error: bool = False,
        timeout: float | None = None,
    ) -> bigquery.QueryJob:
        if self.billed > self.budget_bytes:
            raise SystemExit(f"budget exceeded before {step}: {self.billed} billed bytes")
        cap = cap if cap is not None else cap_for(sql)
        config = bigquery.QueryJobConfig(
            dry_run=dry_run,
            use_query_cache=False,
            maximum_bytes_billed=cap,
            query_parameters=params or [],
            labels={"run": self.run_id, "step": label(step)},
        )
        started = time.perf_counter()
        job = self.client.query(sql, job_config=config)
        error = None
        cancelled = False
        if not dry_run:
            failure, cancelled = self.wait(step, job, self.job_timeout_s if timeout is None else timeout)
            if failure is not None:  # recorded; re-raised unless expected
                error = (job.error_result or {}).get("reason") or type(failure).__name__
                if not expect_error:
                    self._record(step, job, cap, dry_run, error, time.perf_counter() - started, cancelled)
                    raise failure
        self._record(step, job, cap, dry_run, error, time.perf_counter() - started, cancelled)
        return job

    def wait(self, step: str, job: Any, timeout: float) -> tuple[Exception | None, bool]:
        """``job.result(timeout)``: (None, False), or (what it raised, whether cancel() was accepted)."""
        try:
            job.result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - returned to the caller
            # result() raises from getQueryResults without refreshing the job's
            # status, so error_result stays None for scripts until a reload.
            job.reload()
            # A timeout (concurrent.futures.TimeoutError is TimeoutError) or any
            # failure while the job still runs: cancel it and wait for DONE, so
            # what it billed before stopping is recorded and budgeted.
            cancelled = False
            if isinstance(exc, TimeoutError) or job.state != "DONE":
                try:
                    job.cancel()
                    cancelled = True
                except Exception as cancel_exc:  # noqa: BLE001 - still wait and record; it may run on
                    print(f"WARN cancel {step} failed: {type(cancel_exc).__name__}", flush=True)
                self.settle(job)
            return exc, cancelled
        return None, False

    def ensure_fits(self, step: str, cap: int) -> None:
        """Refuse (SystemExit) a job capped at ``cap`` unless it fits the remaining budget, so billed bytes cannot pass it."""
        if self.billed > self.budget_bytes:
            raise SystemExit(f"budget exceeded before {step}: {self.billed} billed bytes")
        remaining = self.budget_bytes - self.billed
        if cap > remaining:
            raise SystemExit(f"{step}: cap {cap} exceeds the remaining budget {remaining}; refusing")

    def estimate(self, step: str, sql: str, params: list[Any] | None = None) -> int | None:
        """Dry-run bytes for ``sql`` (recorded as ``<step>-est``); None when the dry run itself fails.

        Some multi-statement scripts cannot be dry-run as a whole; they fall back
        to the floor-based cap.
        """
        try:
            job = self.query(f"{step}-est", sql, params=params, dry_run=True)
        except Exception as exc:  # noqa: BLE001 - recorded, falls back
            print(f"WARN dry-run estimate for {step} failed: {type(exc).__name__}", flush=True)
            return None
        return int(job.total_bytes_processed or 0)

    def sized_timeout(self, estimate: int | None) -> float:
        """Result timeout for a job whose dry run estimated ``estimate`` bytes; ``job_timeout_s`` without one."""
        return self.job_timeout_s + self.timeout_s_per_gib * (estimate or 0) / GIB

    def settle(self, job: bigquery.QueryJob) -> None:
        """Reload until the job is DONE, waiting at most ``cancel_wait_s``."""
        job.reload()
        for _ in range(math.ceil(self.cancel_wait_s / self.poll_s)):
            if job.state == "DONE":
                return
            self.sleep(self.poll_s)
            job.reload()

    def _record(
        self,
        step: str,
        job: bigquery.QueryJob,
        cap: int | None,
        dry_run: bool,
        error: str | None,
        wall_s: float | None,
        cancelled: bool | None = False,
    ) -> None:
        def get(name: str) -> Any:  # load jobs have no query statistics
            return getattr(job, name, None)

        billed = 0 if dry_run else int(get("total_bytes_billed") or 0)
        self.billed += billed
        dml = get("dml_stats")
        record = {
            "step": step,
            "job_id": job.job_id,
            "user_email": get("user_email"),
            "dry_run": dry_run,
            "statement_type": get("statement_type"),
            "cap": cap,
            "total_bytes_processed": get("total_bytes_processed"),
            "total_bytes_billed": billed,
            "floor_artifact": billed > 0 and billed % BILLING_FLOOR == 0,
            "slot_ms": None if dry_run else get("slot_millis"),
            "cache_hit": None if dry_run else get("cache_hit"),
            "ddl_operation_performed": get("ddl_operation_performed"),
            "dml_stats": None if dml is None else {
                "inserted": dml.inserted_row_count,
                "updated": dml.updated_row_count,
                "deleted": dml.deleted_row_count,
            },
            "error": error,
            "state": job.state,
            "cancelled": cancelled,  # None: not tracked here (BigQueryWarehouse cancels on its own)
            "started": None if dry_run or job.started is None else job.started.isoformat(),
            "ended": None if dry_run or job.ended is None else job.ended.isoformat(),
            "elapsed_ms": None if dry_run or job.started is None or job.ended is None else (job.ended - job.started) / timedelta(milliseconds=1),
            "wall_ms": None if wall_s is None else round(wall_s * 1000, 1),
        }
        self.jobs.append(record)
        if self.log is not None:
            with self.log.open("a") as handle:
                handle.write(json.dumps(record, default=str) + "\n")

    def rows(self, step: str, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self.query(step, sql, params=params).result()]

    def children(self, job: bigquery.QueryJob) -> list[Any]:
        return sorted(self.client.list_jobs(parent_job=job.job_id), key=lambda child: child.created)

    def script_dml(self, job: bigquery.QueryJob) -> dict[str, int]:
        totals = Counter()
        for child in self.children(job):
            stats = getattr(child, "dml_stats", None)
            if stats is not None:
                totals["inserted"] += stats.inserted_row_count
                totals["updated"] += stats.updated_row_count
                totals["deleted"] += stats.deleted_row_count
        return dict(totals)

    def load(self, table: str, path: Path) -> int:
        """A recorded Parquet append; one that times out is cancelled and waited on, so it cannot append rows later."""
        step = f"load-{table}"
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            labels={"run": self.run_id, "step": label(step)},
        )
        started = time.perf_counter()
        with path.open("rb") as handle:
            job = self.client.load_table_from_file(handle, f"{self.client.project}.{DATASET}.{table}", job_config=config)
        failure, cancelled = self.wait(step, job, self.job_timeout_s)
        error = None if failure is None else (job.error_result or {}).get("reason") or type(failure).__name__
        self._record(step, job, None, False, error, time.perf_counter() - started, cancelled)
        if failure is not None:
            raise failure
        expected = pq.ParquetFile(path).metadata.num_rows
        self.check(f"load {table}", job.output_rows == expected, f"{job.output_rows}/{expected} rows")
        return int(job.output_rows or 0)


class AdapterClient:
    """The real client for an unmodified BigQueryWarehouse, adding the run label so its jobs are logged and billed."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self.step = "adapter"
        self.pending: list[tuple[bigquery.QueryJob, bigquery.QueryJobConfig]] = []

    def query(self, sql: str, job_config: bigquery.QueryJobConfig) -> bigquery.QueryJob:
        job_config.labels = {"run": self.run.run_id, "step": label(self.step)}
        job = self.run.client.query(sql, job_config=job_config)
        self.pending.append((job, job_config))
        return job

    @contextmanager
    def recording(self, step: str) -> Iterator[None]:
        """Label the warehouse calls inside as ``step`` and record their jobs, even when one raises."""
        self.step = step
        error = None
        try:
            yield
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.flush(error)

    def flush(self, error: str | None = None) -> None:
        """Record the pending jobs at their final status.

        BigQueryWarehouse cancels a job whose result() raised, and a cancelled job
        may still bill, so each executed job is reloaded (and waited on until DONE)
        first. ``error``, what the warehouse call raised, stands in on the last job
        when BigQuery reports none (e.g. a dry-run budget rejection).
        """
        pending, self.pending = self.pending, []
        for i, (job, config) in enumerate(pending):
            dry = bool(config.dry_run)
            if not dry:
                self.run.settle(job)
            reason = (job.error_result or {}).get("reason") or (error if i == len(pending) - 1 else None)
            self.run._record(self.step + ("-dry" if dry else ""), job, config.maximum_bytes_billed or 0, dry, reason, None, None)


def adc_principal(credentials: object) -> dict[str, str | None]:
    """The ADC principal as evidence: credential type and service-account email (None for user ADC).

    Reads attributes only; never a token. A metadata-server credential reports
    ``"default"`` as its email until it has been refreshed.
    """
    kind = type(credentials)
    return {
        "type": f"{kind.__module__}.{kind.__qualname__}",
        "service_account_email": getattr(credentials, "service_account_email", None) or getattr(credentials, "signer_email", None),
    }


def evidence_dir(out: Path, repo: Path) -> Path:
    """``out`` resolved; SystemExit when it is ``repo`` or inside it, by path or by file identity.

    The identity check catches a case variant of the repo path on a
    case-insensitive filesystem (WSL ``/mnt/c``, Windows), which ``resolve()``
    does not normalise.
    """
    out = out.resolve()

    def same(path: Path) -> bool:
        try:
            return path.exists() and path.samefile(repo)
        except OSError:
            return False

    if out == repo or repo in out.parents or any(same(p) for p in (out, *out.parents)):
        raise SystemExit("--out must be outside the repository")
    return out


def label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", value.lower())[:63]


def cap_for(sql: str) -> int:
    """A cap that covers the 10 MiB per-table billing floor twice over (64 MiB to 512 MiB)."""
    tables = len(set(TABLE_REF.findall(sql))) or 1
    return min(512 * MIB, max(64 * MIB, 2 * BILLING_FLOOR * tables))


def nearest_rank(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]
