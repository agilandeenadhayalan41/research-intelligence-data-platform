"""Offline checks for the shared live-sandbox helpers (fake jobs and clients; no BigQuery calls)."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pyarrow as pa
import pytest

from research_platform.analytics.bigquery.sandbox import live
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.errors import (
    QueryBudgetExceededError,
    QueryTimeoutError,
)

MIB = live.MIB
USER = "runner@example.org"


class SlowJob:
    """A running query job: status, error and billing change only on reload(), like the client's local copy.

    result() raises ``exc`` until the job is DONE; after cancel() it reaches DONE
    (reason ``stopped``, 30 MiB billed) on the ``done_after``-th reload, or never.
    """

    job_id = "job-slow"
    statement_type = "SELECT"
    total_bytes_processed = 40 * MIB
    cache_hit = False
    ddl_operation_performed = None
    dml_stats = None
    started = ended = None
    user_email = USER

    def __init__(self, done_after: int | None = 1, exc: Exception | None = None) -> None:
        self.done_after = done_after
        self.exc = exc or concurrent.futures.TimeoutError()
        self.state = "RUNNING"
        self.error_result = None
        self.total_bytes_billed = None
        self.slot_millis = None
        self.timeouts: list[float | None] = []
        self.cancels = self.reloads = self.reloads_after_cancel = 0

    def result(self, timeout: float | None = None) -> None:
        self.timeouts.append(timeout)
        if self.state != "DONE":
            raise self.exc

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def reload(self) -> None:
        self.reloads += 1
        if not self.cancels:
            return
        self.reloads_after_cancel += 1
        if self.done_after is not None and self.reloads_after_cancel >= self.done_after:
            self.state = "DONE"
            self.error_result = {"reason": "stopped", "message": "Job execution was cancelled"}
            self.total_bytes_billed = 30 * MIB
            self.slot_millis = 1234

    def to_arrow(self, create_bqstorage_client: bool = False) -> pa.Table:
        return pa.table({"x": [1]})


class FailedJob(SlowJob):
    """A job that already failed: the reload after result() raises shows DONE with its error."""

    def reload(self) -> None:
        self.reloads += 1
        self.state = "DONE"
        self.error_result = {"reason": "invalidQuery"}
        self.total_bytes_billed = 10 * MIB


class DryJob:
    job_id = "job-dry"
    statement_type = "SELECT"
    ddl_operation_performed = None
    dml_stats = None
    state = "DONE"
    error_result = None
    user_email = USER

    def __init__(self, processed: int) -> None:
        self.total_bytes_processed = processed


class FakeClient:
    """Dry-run configs get a DryJob; executed queries get the next job given."""

    def __init__(self, *jobs: SlowJob, processed: int = 1 * MIB) -> None:
        self.jobs = list(jobs)
        self.processed = processed
        self.configs: list[object] = []

    def query(self, sql: str, job_config: object) -> object:
        self.configs.append(job_config)
        return DryJob(self.processed) if job_config.dry_run else self.jobs.pop(0)


def warehouse(adapter: live.AdapterClient, budget: int = 64 * MIB) -> BigQueryWarehouse:
    return BigQueryWarehouse(
        WarehouseConfig(analytical="bigquery", bigquery_dataset=live.DATASET),
        CloudConfig(project_id="synthetic-project"),
        client=adapter,
        maximum_bytes_billed=budget,
        job_timeout_seconds=1.0,
    )


def test_timed_out_job_is_cancelled_waited_on_and_billed(tmp_path: Path) -> None:
    job = SlowJob(done_after=3)
    sleeps: list[float] = []
    log = tmp_path / "jobs.jsonl"
    run = live.Run(FakeClient(job), "t", 1024 * MIB, log, job_timeout_s=5.0, poll_s=2.0, sleep=sleeps.append)
    with pytest.raises(TimeoutError):
        run.query("slow", "SELECT 1")
    assert job.timeouts == [5.0]
    assert job.cancels == 1
    assert sleeps == [2.0, 2.0]  # DONE on the third reload after cancel()
    record = json.loads(log.read_text())
    assert (record["state"], record["cancelled"], record["error"]) == ("DONE", True, "stopped")
    assert (record["total_bytes_billed"], record["slot_ms"]) == (30 * MIB, 1234)
    assert run.billed == 30 * MIB


def test_expected_timeout_returns_the_job_after_a_bounded_wait() -> None:
    job = SlowJob(done_after=None)
    sleeps: list[float] = []
    run = live.Run(FakeClient(job), "t", 1024 * MIB, cancel_wait_s=6.0, poll_s=2.0, sleep=sleeps.append)
    assert run.query("slow", "SELECT 1", expect_error=True) is job
    assert sleeps == [2.0, 2.0, 2.0] and job.reloads_after_cancel == 4  # gives up after cancel_wait_s
    record = run.jobs[0]
    assert (record["state"], record["cancelled"], record["error"], record["total_bytes_billed"]) == ("RUNNING", True, "TimeoutError", 0)


def test_cancelled_job_cost_counts_against_the_budget() -> None:
    run = live.Run(FakeClient(SlowJob()), "t", 10 * MIB, sleep=lambda _: None)
    run.query("slow", "SELECT 1", expect_error=True)
    assert run.billed == 30 * MIB
    with pytest.raises(SystemExit, match="budget exceeded before next"):
        run.query("next", "SELECT 1")


def test_any_failure_while_running_cancels_but_a_failed_job_does_not() -> None:
    running = SlowJob(exc=ConnectionError("connection reset"))
    failed = FailedJob(exc=RuntimeError("400 Required field topic_id cannot be null"))
    run = live.Run(FakeClient(running, failed), "t", 1024 * MIB, sleep=lambda _: None)
    run.query("reset", "SELECT 1", expect_error=True)
    run.query("invalid", "SELECT 1", expect_error=True)
    assert (running.cancels, failed.cancels) == (1, 0)
    assert [(r["error"], r["cancelled"], r["state"]) for r in run.jobs] == [("stopped", True, "DONE"), ("invalidQuery", False, "DONE")]
    assert run.billed == 40 * MIB


def test_failed_cancel_still_waits_and_records(capsys: pytest.CaptureFixture[str]) -> None:
    job = SlowJob(done_after=None)

    def refuse() -> bool:
        raise ConnectionError("cancel refused")

    job.cancel = refuse
    run = live.Run(FakeClient(job), "t", 1024 * MIB, sleep=lambda _: None)
    with pytest.raises(TimeoutError):
        run.query("slow", "SELECT 1")
    assert (run.jobs[0]["cancelled"], run.jobs[0]["state"]) == (False, "RUNNING")
    assert "WARN cancel slow failed: ConnectionError" in capsys.readouterr().out


def test_user_email_is_recorded_for_executed_and_dry_jobs() -> None:
    done = SlowJob()
    done.state = "DONE"
    run = live.Run(FakeClient(done), "t", 1024 * MIB)
    run.query("dry", "SELECT 1", dry_run=True)
    run.query("done", "SELECT 1")
    assert [(r["user_email"], r["cancelled"], r["state"]) for r in run.jobs] == [(USER, False, "DONE")] * 2
    assert done.cancels == 0


def test_adapter_recording_reloads_and_records_jobs_when_the_warehouse_raises(tmp_path: Path) -> None:
    job = SlowJob(done_after=2)
    sleeps: list[float] = []
    inner = FakeClient(job)
    run = live.Run(inner, "t", 1024 * MIB, tmp_path / "jobs.jsonl", sleep=sleeps.append)
    adapter = live.AdapterClient(run)
    with pytest.raises(QueryTimeoutError), adapter.recording("adapter-publisher_lookup"):
        warehouse(adapter).query("SELECT 1")
    assert job.timeouts == [1.0] and job.cancels == 1  # BigQueryWarehouse timed out and cancelled it
    assert sleeps == [2.0]  # flush() waited for the cancelled job to reach DONE
    assert [c.labels for c in inner.configs] == [{"run": "t", "step": "adapter-publisher_lookup"}] * 2
    dry, executed = run.jobs
    assert (dry["step"], dry["error"]) == ("adapter-publisher_lookup-dry", None)
    assert (executed["step"], executed["state"], executed["error"]) == ("adapter-publisher_lookup", "DONE", "stopped")
    assert executed["total_bytes_billed"] == run.billed == 30 * MIB
    assert len((tmp_path / "jobs.jsonl").read_text().splitlines()) == 2
    assert not adapter.pending


def test_adapter_recording_reloads_a_successful_job() -> None:
    job = SlowJob()
    job.state, job.total_bytes_billed = "DONE", 10 * MIB
    run = live.Run(FakeClient(job), "t", 1024 * MIB)
    adapter = live.AdapterClient(run)
    with adapter.recording("adapter-ok"):
        assert warehouse(adapter).query("SELECT 1").num_rows == 1
    assert job.reloads == 1 and job.cancels == 0
    assert [(r["step"], r["error"], r["user_email"]) for r in run.jobs] == [("adapter-ok-dry", None, USER), ("adapter-ok", None, USER)]
    assert run.billed == 10 * MIB


def test_adapter_recording_keeps_a_dry_run_budget_rejection() -> None:
    run = live.Run(FakeClient(processed=2 * MIB), "t", 1024 * MIB)
    adapter = live.AdapterClient(run)
    with pytest.raises(QueryBudgetExceededError), adapter.recording("adapter-big"):
        warehouse(adapter, budget=1 * MIB).query("SELECT 1")
    assert [(r["step"], r["error"]) for r in run.jobs] == [("adapter-big-dry", "QueryBudgetExceededError")]
    assert run.billed == 0


class FakeServiceAccountCredentials:
    service_account_email = USER
    token = "secret-token"


class FakeImpersonatedCredentials:
    signer_email = "signer@example.org"
    token = "secret-token"


class FakeUserCredentials:
    token = "secret-token"
    refresh_token = "secret-refresh"


def test_adc_principal_reports_type_and_service_account_email_only() -> None:
    module = FakeUserCredentials.__module__
    service = live.adc_principal(FakeServiceAccountCredentials())
    user = live.adc_principal(FakeUserCredentials())
    assert service == {"type": f"{module}.FakeServiceAccountCredentials", "service_account_email": USER}
    assert user == {"type": f"{module}.FakeUserCredentials", "service_account_email": None}
    assert live.adc_principal(FakeImpersonatedCredentials())["service_account_email"] == "signer@example.org"
    assert "secret" not in json.dumps([service, user])


def test_timeout_defaults_to_job_timeout_and_is_sized_from_an_estimate() -> None:
    first, second = SlowJob(), SlowJob()
    first.state = second.state = "DONE"
    run = live.Run(FakeClient(first, second), "t", 10 * live.GIB, job_timeout_s=600.0, timeout_s_per_gib=60.0)
    assert run.sized_timeout(None) == run.sized_timeout(0) == 600.0
    assert run.sized_timeout(3 * live.GIB) == 600.0 + 3 * 60.0
    run.query("default", "SELECT 1")
    run.query("sized", "SELECT 1", timeout=run.sized_timeout(3 * live.GIB))
    assert (first.timeouts, second.timeouts) == ([600.0], [780.0])


class UndryableClient(FakeClient):
    """A multi-statement script BigQuery cannot dry-run as a whole."""

    def query(self, sql: str, job_config: object) -> object:
        if job_config.dry_run:
            raise RuntimeError("400 dry run not supported for this script")
        return super().query(sql, job_config)


def test_estimate_is_recorded_and_a_failed_dry_run_returns_none(capsys: pytest.CaptureFixture[str]) -> None:
    run = live.Run(FakeClient(processed=3 * MIB), "t", 1024 * MIB)
    assert run.estimate("b1-merge_works", "SELECT 1") == 3 * MIB
    assert [j["step"] for j in run.jobs] == ["b1-merge_works-est"] and run.billed == 0
    assert live.Run(UndryableClient(), "t", 1024 * MIB).estimate("b1-merge_work_topics", "BEGIN; SELECT 1; END;") is None
    assert "WARN dry-run estimate for b1-merge_work_topics failed" in capsys.readouterr().out


def test_ensure_fits_refuses_a_cap_above_the_remaining_budget_or_an_overrun() -> None:
    run = live.Run(FakeClient(), "t", 100 * MIB)
    run.billed = 40 * MIB
    run.ensure_fits("ok", 60 * MIB)  # exactly the remaining budget
    with pytest.raises(SystemExit, match=f"next: cap {61 * MIB} exceeds the remaining budget {60 * MIB}"):
        run.ensure_fits("next", 61 * MIB)
    run.billed = 101 * MIB
    with pytest.raises(SystemExit, match="budget exceeded before next"):
        run.ensure_fits("next", 0)


class FakeLoadJob:
    """A load job, which has no query statistics: result() times out until DONE; after cancel() DONE on the ``done_after``-th reload."""

    job_id = "job-load"
    user_email = USER
    started = ended = None
    output_rows = 3

    def __init__(self, done_after: int = 1) -> None:
        self.done_after = done_after
        self.state = "RUNNING"
        self.error_result = None
        self.timeouts: list[float | None] = []
        self.cancels = self.reloads_after_cancel = 0

    def result(self, timeout: float | None = None) -> None:
        self.timeouts.append(timeout)
        if self.state != "DONE":
            raise concurrent.futures.TimeoutError()

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def reload(self) -> None:
        if self.cancels:
            self.reloads_after_cancel += 1
            if self.reloads_after_cancel >= self.done_after:
                self.state, self.error_result = "DONE", {"reason": "stopped"}


class LoadClient:
    project = "synthetic-project"

    def __init__(self, job: FakeLoadJob) -> None:
        self.job = job
        self.targets: list[str] = []

    def load_table_from_file(self, handle: object, target: str, job_config: object) -> FakeLoadJob:
        self.targets.append(target)
        return self.job


def parquet(tmp_path: Path) -> Path:
    import pyarrow.parquet as pq

    path = tmp_path / "works.parquet"
    pq.write_table(pa.table({"work_id": ["a", "b", "c"]}), path)
    return path


def test_load_that_times_out_is_cancelled_waited_on_and_recorded(tmp_path: Path) -> None:
    job = FakeLoadJob(done_after=2)
    sleeps: list[float] = []
    log = tmp_path / "jobs.jsonl"
    run = live.Run(LoadClient(job), "t", 1024 * MIB, log, job_timeout_s=5.0, sleep=sleeps.append)
    with pytest.raises(TimeoutError):
        run.load("works_staging", parquet(tmp_path))
    assert job.timeouts == [5.0] and job.cancels == 1 and sleeps == [2.0]  # DONE on the second reload after cancel()
    record = json.loads(log.read_text())
    assert (record["step"], record["state"], record["cancelled"], record["error"]) == ("load-works_staging", "DONE", True, "stopped")
    assert (record["cap"], record["total_bytes_billed"], record["statement_type"]) == (None, 0, None)
    assert run.checks == []  # no row-count check for a load that did not finish


def test_load_records_the_job_and_checks_its_rows(tmp_path: Path) -> None:
    job = FakeLoadJob()
    job.state = "DONE"
    client = LoadClient(job)
    run = live.Run(client, "t", 1024 * MIB)
    assert run.load("works_staging", parquet(tmp_path)) == 3
    assert client.targets == ["synthetic-project.openalex.works_staging"]
    assert [(r["step"], r["error"], r["cancelled"], r["user_email"]) for r in run.jobs] == [("load-works_staging", None, False, USER)]
    assert run.checks == [{"check": "load works_staging", "ok": True, "detail": "3/3 rows"}] and job.cancels == 0


def test_evidence_dir_refuses_the_repo_by_path_and_by_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "build").mkdir(parents=True)
    for inside in (repo, repo / "build" / "ev", repo / "build" / ".." / "new"):
        with pytest.raises(SystemExit, match="--out must be outside the repository"):
            live.evidence_dir(inside, repo)
    assert live.evidence_dir(tmp_path / "evidence" / "run1", repo) == (tmp_path / "evidence" / "run1").resolve()
    assert not (tmp_path / "evidence").exists()  # the guard creates nothing


def test_evidence_dir_refuses_a_case_variant_of_the_repo_on_a_case_insensitive_filesystem() -> None:
    repo = Path(__file__).resolve().parents[2]
    variant = repo.parent / repo.name.swapcase()
    if not variant.exists():
        pytest.skip("case-sensitive filesystem: a case variant is a different path")
    assert str(variant) != str(repo)
    with pytest.raises(SystemExit, match="outside the repository"):
        live.evidence_dir(variant / "evidence", repo)
