"""Offline checks for the manual #103 sandbox script (no BigQuery calls)."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pyarrow as pa
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
from google.cloud.bigquery.job import query as query_job_module

from research_platform.analytics.bigquery.contracts import build_table_contracts
from research_platform.analytics.bigquery.sandbox import live
from research_platform.analytics.bigquery.sandbox.synthetic import SYNTHETIC_SCALES
from research_platform.warehouse.errors import (
    QueryBudgetExceededError,
    QueryTimeoutError,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bigquery_sandbox_103.py"
_spec = importlib.util.spec_from_file_location("bigquery_sandbox_103", SCRIPT)
s103 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s103)
MIB = s103.MIB


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
        self.state = "RUNNING"  # stale until reload(), like the client's local copy

    def result(self, timeout: float | None = None) -> None:
        raise RuntimeError("400 Required field topic_id cannot be null")

    def reload(self) -> None:
        self.error_result = {"reason": "invalidQuery"}
        self.state = "DONE"


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
        self.state = "DONE"
        self.timeouts: list[float | None] = []

    def result(self, timeout: float | None = None) -> None:
        self.timeouts.append(timeout)

    def reload(self) -> None:
        pass


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

    assert live.cap_for(tables(1)) == 64 * live.MIB  # minimum cap
    assert live.cap_for(tables(5)) == 2 * live.BILLING_FLOOR * 5
    assert live.cap_for(tables(26)) == 512 * live.MIB  # ceiling
    assert live.label("Query-Foo.Bar") == "query-foo-bar"


def test_scale_choices_are_the_synthetic_presets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    base = ["--confirm-project", "synthetic-project", "--out", str(tmp_path)]
    assert "large" in SYNTHETIC_SCALES
    for preset in SYNTHETIC_SCALES:  # parsed, then stopped before any client exists
        with pytest.raises(SystemExit, match="GOOGLE_CLOUD_PROJECT must be set"):
            s103.main([*base, "--scale", preset])
    for bad in (["--scale", "8x50000"], ["--max-job-mib", "0"], ["--timeout-s-per-gib", "-1"], ["--cluster-settle-s", "-1"]):
        with pytest.raises(SystemExit) as exc:
            s103.main([*base, *bad])
        assert exc.value.code == 2  # argparse usage error


def test_out_inside_the_repository_is_refused_before_any_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "synthetic-project")
    monkeypatch.setattr(s103.bigquery, "Client", lambda **_: pytest.fail("no client before the --out guard"))
    inside = s103.REPO / "build" / "i103-evidence"
    with pytest.raises(SystemExit, match="outside the repository"):
        s103.main(["--confirm-project", "synthetic-project", "--out", str(inside)])
    assert not inside.exists()


ONE_TABLE = "SELECT * FROM `openalex.works`"
TWO_TABLES = "SELECT * FROM `openalex.works` JOIN `openalex.work_topics` USING (work_id)"


def test_job_cap_is_cap_for_at_small_scale_then_twice_the_estimate_plus_floor() -> None:
    ceiling = 512 * MIB
    assert s103.job_cap(ONE_TABLE, None, ceiling) == s103.job_cap(ONE_TABLE, 0, ceiling) == live.cap_for(ONE_TABLE) == 64 * MIB
    assert s103.job_cap(ONE_TABLE, 20 * MIB, ceiling) == 64 * MIB  # 2 x 20 + 10 < 64: cap_for decides
    assert s103.job_cap(ONE_TABLE, 100 * MIB, ceiling) == 210 * MIB
    assert s103.job_cap(ONE_TABLE, 100 * MIB + 1, ceiling) == 211 * MIB  # rounded up to a whole MiB
    assert s103.job_cap(TWO_TABLES, 100 * MIB, ceiling) == 220 * MIB  # billing floor per table
    assert s103.job_cap(ONE_TABLE, 300 * MIB, ceiling) == ceiling
    assert s103.job_cap(ONE_TABLE, 1, 32 * MIB) == 32 * MIB  # a ceiling below cap_for still wins


def test_adapter_cap_is_twice_the_raw_billed_bytes_within_bounds() -> None:
    ceiling = 512 * MIB
    assert s103.adapter_cap(10 * MIB, 1 * MIB, ceiling) == 64 * MIB  # floor-billed lookup: the old fixed cap
    assert s103.adapter_cap(100 * MIB, 90 * MIB, ceiling) == 200 * MIB
    assert s103.adapter_cap(10 * MIB, 150 * MIB, ceiling) == 150 * MIB  # pruned raw run: the warehouse dry-run check still passes
    assert s103.adapter_cap(400 * MIB, 0, ceiling) == ceiling


class TimedJob(DoneJob):
    """An executed job with server timings, processed bytes and a cache flag."""

    def __init__(self, processed: int) -> None:
        super().__init__(10 * MIB)
        self.total_bytes_processed = processed
        self.cache_hit = False
        self.slot_millis = 5
        self.started = datetime(2026, 1, 1, tzinfo=UTC)
        self.ended = self.started + timedelta(milliseconds=200)


class SizingClient:
    """Dry runs report ``estimate`` bytes; executed jobs report ``processed``."""

    def __init__(self, estimate: int, processed: int) -> None:
        self.estimate, self.processed = estimate, processed
        self.configs: list[bigquery.QueryJobConfig] = []

    def query(self, sql: str, job_config: bigquery.QueryJobConfig) -> DoneJob:
        self.configs.append(job_config)
        if job_config.dry_run:
            job = DoneJob(0)
            job.total_bytes_processed = self.estimate
            return job
        return TimedJob(self.processed)


def test_sized_run_dry_runs_uncapped_jobs_first_and_keeps_explicit_caps() -> None:
    client = SizingClient(100 * MIB, 100 * MIB)
    run = s103.SizedRun(client, "t", 10 * 1024 * MIB, max_job_bytes=512 * MIB)
    run.query("big", ONE_TABLE)
    run.query("capped", ONE_TABLE, cap=1 * MIB)
    run.query("dry", ONE_TABLE, dry_run=True)
    assert [r["step"] for r in run.jobs] == ["big-est", "big", "capped", "dry"]
    assert [(c.dry_run, c.maximum_bytes_billed) for c in client.configs] == [
        (True, 512 * MIB),  # free estimate, capped at the ceiling
        (False, 210 * MIB),
        (False, 1 * MIB),
        (True, 512 * MIB),
    ]


def test_sized_run_refuses_a_cap_above_the_remaining_budget() -> None:
    run = s103.SizedRun(SizingClient(100 * MIB, 0), "t", 200 * MIB)
    with pytest.raises(SystemExit, match="exceeds the remaining budget"):
        run.query("big", ONE_TABLE)
    assert [r["step"] for r in run.jobs] == ["big-est"]  # only the free dry run went out


def test_sized_run_sizes_the_result_timeout_from_the_estimate() -> None:
    gib = 1024 * MIB
    client = SizingClient(3 * gib, 0)
    run = s103.SizedRun(client, "t", 100 * gib, job_timeout_s=600.0, timeout_s_per_gib=60.0, max_job_bytes=8 * gib)
    assert run.query("dry-run-first", ONE_TABLE).timeouts == [600.0 + 3 * 60.0]
    given = run.query("given", ONE_TABLE, estimate=1 * gib)  # no dry run: the caller has one
    assert given.timeouts == [660.0] and client.configs[-1].maximum_bytes_billed == 2 * gib + live.BILLING_FLOOR
    assert run.query("capped", ONE_TABLE, cap=1 * MIB).timeouts == [600.0]  # an explicit cap without an estimate
    assert [r["step"] for r in run.jobs] == ["dry-run-first-est", "dry-run-first", "given", "capped"]


def test_latency_reuses_estimates_and_ignores_estimate_records() -> None:
    cases = {"doi_work_lookup": ("SELECT 1", [])}
    run = s103.SizedRun(SizingClient(4 * MIB, 4 * MIB), "t", 1024 * MIB)
    latency = s103.measure_latency(run, cases, 3)
    assert len(run.jobs) == 6  # 3 estimates + 3 runs
    assert latency["doi_work_lookup"]["runs"] == 3
    assert latency["doi_work_lookup"]["elapsed_ms_p50"] == 200.0
    assert latency["doi_work_lookup"]["any_cache_hit"] is False
    reused = s103.SizedRun(SizingClient(4 * MIB, 4 * MIB), "t", 1024 * MIB)
    s103.measure_latency(reused, cases, 3, {"doi_work_lookup": 30 * MIB})
    assert [(r["dry_run"], r["cap"]) for r in reused.jobs] == [(False, 70 * MIB)] * 3  # 2 x 30 MiB + the floor


def test_remeasure_takes_processed_bytes_from_the_last_latency_repeat() -> None:
    cases = {"doi_work_lookup": ("SELECT 1", []), "publisher_lookup": ("SELECT 2", [])}
    run = s103.SizedRun(SizingClient(4 * MIB, 3 * MIB), "t", 1024 * MIB)
    measured = {"doi_work_lookup": (4 * MIB, 4 * MIB, "query-doi_work_lookup"), "publisher_lookup": (1, 1, "query-publisher_lookup")}
    assert s103.remeasure(run, measured, 0) == measured  # no repeats: the raw runs stand
    s103.measure_latency(run, {"doi_work_lookup": cases["doi_work_lookup"]}, 2, {"doi_work_lookup": 4 * MIB})
    assert s103.remeasure(run, measured, 2) == {
        "doi_work_lookup": (4 * MIB, 3 * MIB, "latency-doi_work_lookup-01"),
        "publisher_lookup": (1, 1, "query-publisher_lookup"),  # not repeated
    }


SAMPLE_CLUSTERING = {"works": ("work_id", "doi", "primary_publisher_id"), "work_topics": ("work_id", "topic_id")}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM `openalex.works` AS w WHERE w.`doi` = @doi", ["works.doi"]),
        ("SELECT * FROM openalex.works as w WHERE w.doi=@doi", ["works.doi"]),
        ("SELECT * FROM `openalex.works` WHERE works.work_id = @id", ["works.work_id"]),  # table name as qualifier
        ("SELECT * FROM `openalex.works` JOIN `openalex.work_topics` USING (work_id) WHERE work_id = @id", ["work_topics.work_id", "works.work_id"]),
        ("SELECT * FROM `openalex.works` AS w JOIN `openalex.work_topics` AS t ON t.work_id = w.work_id WHERE t.topic_id = @t", ["work_topics.topic_id"]),
        ("SELECT * FROM `openalex.works` AS w WHERE w.title = @t AND w.publication_year >= @y", []),  # not clustered; a range
        ("WITH x AS (SELECT work_id FROM `openalex.works`) SELECT * FROM x AS c WHERE c.work_id = @id", []),  # CTE alias
        ("SELECT * FROM `openalex.works` AS w WHERE @doi = w.doi -- w.doi = @doi", []),  # reversed; comment
    ],
)
def test_cluster_filters_text_match(sql: str, expected: list[str]) -> None:
    assert s103.cluster_filters(sql, SAMPLE_CLUSTERING) == expected


def test_cluster_filters_on_the_repo_query_patterns() -> None:
    clustering = {c.table_name: c.clustering.fields for c in build_table_contracts()}
    found = {path.stem: s103.cluster_filters(path.read_text(encoding="utf-8"), clustering) for path in sorted((s103.SQL / "queries").glob("*.sql"))}
    assert found["doi_work_lookup"] == ["works.doi"]
    assert found["openalex_work_id_lookup"] == ["works.work_id"]
    assert found["citation_relationships"] == ["work_references.work_id"]
    assert found["publisher_lookup"] == ["publishers.publisher_id"]
    assert "works.primary_publisher_id" in found["publisher_topic_counts"]
    assert found["publication_trends"] == found["open_access_trends"] == []  # partition-key ranges only
    assert s103.TIMEOUT_PROBE_PATTERN in found


def test_clustering_is_observed_per_pattern_once_its_own_table_reaches_the_threshold() -> None:
    cases = {
        "doi_work_lookup": ("SELECT * FROM `openalex.works` AS w WHERE w.doi = @doi", []),
        "empty": (ONE_TABLE, []),
        "topics": ("SELECT * FROM `openalex.work_topics` AS t WHERE t.topic_id = @t", []),
    }
    measured = {"doi_work_lookup": (1000, 250, "latency-doi_work_lookup-20"), "empty": (0, 0, "query-empty"), "topics": (10, 5, "query-topics")}
    small = [
        {"table_name": "publishers", "partition_count": 1, "total_logical_bytes": 1 * MIB},
        {"table_name": "works", "partition_count": 3, "total_logical_bytes": 5 * MIB},
        {"table_name": "work_topics", "partition_count": 4, "total_logical_bytes": 2 * MIB},
    ]
    patterns = s103.clustering_patterns(cases, measured, small)
    assert patterns["doi_work_lookup"] == {
        "status": "NOT MEASURED: its tables are under 64 MiB logical",
        "dry_run_bytes": 1000,
        "processed_bytes": 250,
        "processed_from": "latency-doi_work_lookup-20",
        "processed_over_estimate": 0.25,
        "cluster_filters": ["works.doi"],
        "tables": {"works": {"partition_count": 3, "total_logical_bytes": 5 * MIB}},
    }
    assert patterns["empty"]["processed_over_estimate"] is None
    summary = s103.clustering_summary(patterns, small)
    assert summary["status"].startswith("NOT MEASURED (tables of KB-MB)")
    assert "not meaningful at this scale" in summary["status"]
    assert summary["observed_patterns"] == [] and summary["largest_table_logical_bytes"] == 5 * MIB and summary["patterns"] is patterns
    assert s103.clustering_summary({}, [])["status"].startswith("NOT MEASURED")

    # One large table makes only the patterns that read it OBSERVED.
    big = [row | ({"total_logical_bytes": 64 * MIB} if row["table_name"] == "work_topics" else {}) for row in small]
    patterns = s103.clustering_patterns(cases, measured, big)
    assert {stem: p["status"] == s103.CLUSTER_OBSERVED for stem, p in patterns.items()} == {"doi_work_lookup": False, "empty": False, "topics": True}
    summary = s103.clustering_summary(patterns, big)
    assert summary["status"] == "OBSERVED for 1 of 3 patterns (observed_patterns); NOT MEASURED for the rest"
    assert summary["observed_patterns"] == ["topics"]
    assert s103.clustering_summary({"topics": patterns["topics"]}, big)["status"] == s103.CLUSTER_OBSERVED
    assert s103.CLUSTER_OBSERVED == "OBSERVED; dry-run is the unpruned upper bound; cause not isolated"


class CancelledAfterReloads(DoneJob):
    """A job given up on after a timeout: DONE (stopped, billed) only from the ``done_after``-th reload."""

    def __init__(self, done_after: int) -> None:
        super().__init__(0)
        self.state, self.total_bytes_billed = "RUNNING", None
        self.done_after, self.reloads, self.cancels = done_after, 0, 0

    def result(self, timeout: float | None = None) -> None:
        raise concurrent.futures.TimeoutError()

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def reload(self) -> None:
        self.reloads += 1
        if self.reloads >= self.done_after:
            self.state, self.error_result = "DONE", {"reason": "stopped"}
            self.total_bytes_billed, self.slot_millis = 10 * MIB, 321


class ProbeClient:
    def __init__(self, job: DoneJob) -> None:
        self.job = job

    def query(self, sql: str, job_config: object) -> DoneJob:
        return DoneJob(0) if job_config.dry_run else self.job


class TimingOutWarehouse:
    """Submits through the adapter as BigQueryWarehouse does, then raises QueryTimeoutError."""

    maximum_bytes_billed = 64 * MIB

    def __init__(self, adapter: live.AdapterClient) -> None:
        self.adapter = adapter
        self.parameters: object = None

    def query(self, sql: str, parameters: object = None) -> None:
        self.parameters = parameters
        self.adapter.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
        self.adapter.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=64 * MIB))
        raise QueryTimeoutError("BigQuery execute timed out")


def test_timeout_probe_waits_for_the_cancelled_job_and_bills_it() -> None:
    job = CancelledAfterReloads(done_after=3)
    sleeps: list[float] = []
    run = s103.SizedRun(ProbeClient(job), "t", 1024 * MIB, sleep=sleeps.append)
    adapter = s103.AdapterClient(run)
    warehouse = TimingOutWarehouse(adapter)
    detail = s103.timeout_probe(run, adapter, warehouse, "publication_trends", "SELECT 1", {"year_from": 2025})
    assert run.checks[-1]["ok"], detail
    assert warehouse.parameters == {"year_from": 2025}
    assert (detail["raised"], detail["outcome"], detail["state"], detail["error"]) == ("QueryTimeoutError", "cancelled", "DONE", "stopped")
    assert (detail["billed"], detail["slot_ms"]) == (10 * MIB, 321)
    assert isinstance(detail["wall_ms"], float) and detail["wall_ms"] >= 0
    assert run.billed == 10 * MIB  # counted in the run's billed total
    assert job.reloads == 3 and sleeps == [2.0, 2.0]  # reloaded until DONE
    assert [r["step"] for r in run.jobs] == ["timeout-probe-publication_trends-dry", "timeout-probe-publication_trends"]


def test_timeout_probe_through_the_warehouse_cancels_on_its_tiny_timeout() -> None:
    job = CancelledAfterReloads(done_after=1)
    run = s103.SizedRun(ProbeClient(job), "t", 1024 * MIB, sleep=lambda _: None)
    adapter = s103.AdapterClient(run)
    warehouse = s103.adapter_warehouse("synthetic-project", adapter, 64 * MIB, s103.TIMEOUT_PROBE_S)
    detail = s103.timeout_probe(run, adapter, warehouse, "p", "SELECT 1", {})
    assert run.checks[-1]["ok"], detail
    assert job.cancels == 1  # BigQueryWarehouse cancelled it
    assert (detail["timeout_s"], detail["raised"], detail["outcome"]) == (s103.TIMEOUT_PROBE_S, "QueryTimeoutError", "cancelled")


def test_timeout_probe_accepts_a_query_that_completes() -> None:
    finished = TimedJob(0)
    finished.to_arrow = lambda **_: pa.table({"n": [1]})
    run = s103.SizedRun(ProbeClient(finished), "t", 1024 * MIB)
    adapter = s103.AdapterClient(run)
    warehouse = s103.adapter_warehouse("synthetic-project", adapter, 64 * MIB, s103.TIMEOUT_PROBE_S)
    detail = s103.timeout_probe(run, adapter, warehouse, "p", "SELECT 1", {})
    assert run.checks[-1]["ok"], detail
    assert (detail["raised"], detail["outcome"]) == (None, "completed")
    assert run.billed == 10 * MIB


def test_warehouse_calls_are_refused_above_the_remaining_budget() -> None:
    run = s103.SizedRun(ProbeClient(TimedJob(0)), "t", 100 * MIB)
    run.billed = 50 * MIB
    adapter = s103.AdapterClient(run)
    warehouse = s103.adapter_warehouse("synthetic-project", adapter, 64 * MIB, 60.0)
    with pytest.raises(SystemExit, match=f"adapter-p: cap {64 * MIB} exceeds the remaining budget {50 * MIB}"):
        s103.warehouse_query(run, adapter, warehouse, "adapter-p", "SELECT 1", {})
    with pytest.raises(SystemExit, match="timeout-probe-p: cap"):
        s103.timeout_probe(run, adapter, s103.adapter_warehouse("synthetic-project", adapter, 64 * MIB, s103.TIMEOUT_PROBE_S), "p", "SELECT 1", {})
    assert run.jobs == [] and run.checks == []  # nothing submitted, not even the dry runs
    run.billed = 0
    finished = TimedJob(0)
    finished.to_arrow = lambda **_: pa.table({"n": [1]})
    run.client = ProbeClient(finished)
    assert s103.warehouse_query(run, adapter, warehouse, "adapter-p", "SELECT 1", {}).num_rows == 1
    assert [r["step"] for r in run.jobs] == ["adapter-p-dry", "adapter-p"]


def test_timeout_probe_fails_on_any_other_error_without_raising() -> None:
    class Refusing:
        maximum_bytes_billed = 64 * MIB

        def query(self, sql: str, parameters: object = None) -> None:
            raise QueryBudgetExceededError("dry-run total_bytes_processed exceeds maximum_bytes_billed")

    run = s103.SizedRun(ProbeClient(TimedJob(0)), "t", 1024 * MIB)
    detail = s103.timeout_probe(run, s103.AdapterClient(run), Refusing(), "p", "SELECT 1", {})
    assert not run.checks[-1]["ok"]
    assert (detail["raised"], detail["outcome"]) == ("QueryBudgetExceededError", "not submitted")


class FakeBigQueryHttp:
    """The HTTP transport under a real bigquery.Client (no network): query jobs stay RUNNING until cancelled.

    After a cancel request, the next jobs.get shows the job DONE (``stopped``,
    10 MiB billed). A request whose HTTP timeout is below ``RTT_S`` fails at
    once: the real transport would raise requests.Timeout, which the client's
    DEFAULT_RETRY retries for up to ten minutes per call.
    """

    RTT_S = 0.25
    is_mtls = False  # read by the client's connection to pick its base URL

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.cancelled: set[str] = set()
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, data: object = None, headers: object = None, timeout: object = None, **_: object) -> SimpleNamespace:
        path = urlsplit(url).path.removeprefix("/bigquery/v2/")
        self.calls.append((method, path, timeout))
        if isinstance(timeout, (int, float)) and timeout < self.RTT_S:
            raise AssertionError(f"{method} {path}: HTTP timeout {timeout}s is below one round trip")
        parts = path.split("/")  # projects/<p>/jobs[/<id>[/cancel]] or projects/<p>/queries/<id>
        if method == "POST" and parts[2:] == ["jobs"]:
            body = json.loads(data)
            dry = bool(body["configuration"].get("dryRun"))
            job = {
                "jobReference": {**body["jobReference"], "location": "US"},
                "configuration": body["configuration"],
                "user_email": "runner@example.org",
                "status": {"state": "DONE" if dry else "RUNNING"},
                "statistics": {"query": {"totalBytesProcessed": str(MIB), "statementType": "SELECT"}},
            }
            self.jobs[body["jobReference"]["jobId"]] = job
            return self.respond(job)
        job = self.jobs[parts[3]]
        if parts[2] == "queries":
            return self.respond({"jobReference": job["jobReference"], "jobComplete": False})
        if parts[4:] == ["cancel"]:
            self.cancelled.add(parts[3])
            return self.respond({"job": job})
        if parts[3] in self.cancelled:
            job["status"] = {"state": "DONE", "errorResult": {"reason": "stopped", "message": "Job execution was cancelled"}}
            job["statistics"]["query"].update(totalBytesBilled=str(10 * MIB), totalSlotMs="12")
        return self.respond(job)

    @staticmethod
    def respond(body: dict) -> SimpleNamespace:
        content = json.dumps(body).encode()
        return SimpleNamespace(status_code=200, content=content, headers={"content-type": "application/json"}, json=lambda: json.loads(content))


@pytest.mark.parametrize(("timeout_s", "ok"), [(s103.TIMEOUT_PROBE_S, True), (0.001, False)])
def test_timeout_probe_through_a_real_client_times_out_on_its_own_deadline(monkeypatch: pytest.MonkeyPatch, timeout_s: float, ok: bool) -> None:
    clock = itertools.count(0.0, 0.4)  # result()'s deadline clock: 0.4 s per poll
    monkeypatch.setattr(query_job_module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    http = FakeBigQueryHttp()
    client = bigquery.Client(project="synthetic-project", credentials=AnonymousCredentials(), _http=http)
    run = s103.SizedRun(client, "t", 1024 * MIB, sleep=lambda _: None)
    adapter = s103.AdapterClient(run)
    warehouse = s103.adapter_warehouse("synthetic-project", adapter, 64 * MIB, timeout_s)
    detail = s103.timeout_probe(run, adapter, warehouse, "p", "SELECT 1", {})
    assert run.checks[-1]["ok"] is ok, detail
    if ok:
        # result() raised its own TimeoutError after a few polls; the warehouse cancelled; the wait saw DONE.
        assert (detail["raised"], detail["outcome"], detail["billed"], detail["slot_ms"]) == ("QueryTimeoutError", "cancelled", 10 * MIB, 12)
        assert [c[0] for c in http.calls].count("POST") == 3  # dry run, the job, its cancel
        assert all(t is None or t >= FakeBigQueryHttp.RTT_S for _, _, t in http.calls)
    else:
        # A sub-round-trip HTTP timeout fails the first jobs.get; live it is retried for ~40 min.
        assert detail["raised"] == "AssertionError"
