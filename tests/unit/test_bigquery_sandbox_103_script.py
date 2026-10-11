"""Offline checks for the manual #103 sandbox script (no BigQuery calls)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bigquery_sandbox_103.py"
_spec = importlib.util.spec_from_file_location("bigquery_sandbox_103", SCRIPT)
s103 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s103)


class FailingScriptJob:
    """A script job whose status only shows the failure after reload(), as in the live run."""

    job_id = "job-1"
    statement_type = "SCRIPT"
    total_bytes_processed = 0
    total_bytes_billed = 20 * s103.MIB
    slot_millis = 5
    cache_hit = False
    ddl_operation_performed = None
    dml_stats = None
    started = ended = None

    def __init__(self) -> None:
        self.error_result = None

    def result(self, timeout: float | None = None) -> None:
        raise RuntimeError("400 Required field topic_id cannot be null")

    def reload(self) -> None:
        self.error_result = {"reason": "invalidQuery"}


class FakeClient:
    def __init__(self, job: FailingScriptJob) -> None:
        self.job = job

    def query(self, sql: str, job_config: object) -> FailingScriptJob:
        return self.job


def test_expected_failure_is_read_after_reload_and_logged(tmp_path: Path) -> None:
    job = FailingScriptJob()
    log = tmp_path / "jobs.jsonl"
    run = s103.Run(FakeClient(job), "i103-test", 1024 * s103.MIB, log)
    returned = run.query("inject", "SELECT 1", expect_error=True)
    assert returned.error_result == {"reason": "invalidQuery"}
    record = json.loads(log.read_text().splitlines()[0])
    assert record["error"] == "invalidQuery"
    assert record["floor_artifact"] is True
    assert run.billed == 20 * s103.MIB


def test_unexpected_failure_is_logged_then_raised(tmp_path: Path) -> None:
    log = tmp_path / "jobs.jsonl"
    run = s103.Run(FakeClient(FailingScriptJob()), "i103-test", 1024 * s103.MIB, log)
    with pytest.raises(RuntimeError):
        run.query("partitions", "SELECT 1")
    assert json.loads(log.read_text())["step"] == "partitions"


class DoneJob(FailingScriptJob):
    def __init__(self, billed: int) -> None:
        super().__init__()
        self.total_bytes_billed = billed

    def result(self, timeout: float | None = None) -> None:
        return None


class RecordingClient:
    def __init__(self) -> None:
        self.configs: list[object] = []

    def query(self, sql: str, job_config: object) -> DoneJob:
        self.configs.append(job_config)
        return DoneJob(0 if job_config.dry_run else 10 * s103.MIB)


def test_adapter_jobs_are_labelled_logged_and_billed(tmp_path: Path) -> None:
    from google.cloud import bigquery

    inner = RecordingClient()
    run = s103.Run(inner, "i103-test", 1024 * s103.MIB, tmp_path / "jobs.jsonl")
    adapter = s103.AdapterClient(run)
    adapter.step = "adapter-publisher_lookup"
    adapter.query("SELECT 1", bigquery.QueryJobConfig(dry_run=True))
    adapter.query("SELECT 1", bigquery.QueryJobConfig(maximum_bytes_billed=64 * s103.MIB))
    adapter.flush()
    assert [c.labels for c in inner.configs] == [{"run": "i103-test", "step": "adapter-publisher_lookup"}] * 2
    assert [j["step"] for j in run.jobs] == ["adapter-publisher_lookup-dry", "adapter-publisher_lookup"]
    assert run.billed == 10 * s103.MIB
    assert not adapter.pending


def test_nearest_rank_small_sample() -> None:
    values = [float(v) for v in range(1, 22)]  # 21 runs
    assert s103.nearest_rank(values, 0.5) == 11.0
    assert s103.nearest_rank(values, 0.95) == 20.0


def test_cap_covers_the_per_table_billing_floor() -> None:
    def tables(n: int) -> str:
        return " ".join(f"`openalex.t{chr(97 + i)}`" for i in range(n))

    assert s103.cap_for(tables(1)) == 64 * s103.MIB  # minimum cap
    assert s103.cap_for(tables(5)) == 2 * s103.BILLING_FLOOR * 5
    assert s103.cap_for(tables(26)) == 512 * s103.MIB  # ceiling
    assert s103.label("Query-Foo.Bar") == "query-foo-bar"
