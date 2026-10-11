"""Offline checks for the manual BigQueryWarehouse smoke script (fake BigQuery and session; no network)."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from google.api_core.exceptions import BadRequest, Forbidden, NotFound
from google.cloud import bigquery

from research_platform.analytics.bigquery.sandbox import live
from research_platform.warehouse.errors import QueryBudgetExceededError

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "bigquery_warehouse_smoke.py"
_spec = importlib.util.spec_from_file_location("bigquery_warehouse_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

MIB = live.MIB
PROJECT = "synthetic-project"
SA = "smoke-runner@example.org"
CHECKS = [
    "0. synthetic table created, labelled, expiring in a day",
    "1. over-budget query rejected by dry run before execution",
    "1b. provider rejects the job over maximum_bytes_billed, 0 bytes billed",
    "2. bounded execute returns rows within a 20 MiB cap",
    "3. typed parameters bound (BOOL/INT64/FLOAT64/STRING/TIMESTAMP/DATE/ARRAY)",
    "4a. empty result keeps schema",
    "4b. null result values are null with typed columns",
    "5. executed jobs recorded DONE with bytes billed within their caps",
]


class FakeJob:
    """A query job as the client's local copy shows it: a rejected job's state and reason appear on reload()."""

    cache_hit = False
    slot_millis = 1
    dml_stats = None
    started = ended = None
    user_email = SA

    def __init__(self, job_id: str, *, processed: int | None = 0, billed: int | None = 0, table: pa.Table | None = None,
                 error: Exception | None = None, statement: str = "SELECT", ddl: str | None = None) -> None:
        self.job_id = job_id
        self.total_bytes_processed = processed
        self.total_bytes_billed = billed
        self.table = table
        self.error = error
        self.statement_type = statement
        self.ddl_operation_performed = ddl
        self.state = "RUNNING" if error else "DONE"
        self.error_result = None
        self.cancels = 0

    def result(self, timeout: float | None = None) -> FakeJob:
        if self.error is not None:
            raise self.error
        return self

    def reload(self) -> None:
        if self.error is not None:
            self.state, self.error_result = "DONE", {"reason": "bytesBilledLimitExceeded"}

    def cancel(self) -> bool:
        self.cancels += 1
        return True

    def to_arrow(self, create_bqstorage_client: bool = False) -> pa.Table | None:
        return self.table


def answer(sql: str, params: list[object]) -> pa.Table:
    values = {p.name: p.values if isinstance(p, bigquery.ArrayQueryParameter) else p.value for p in params}
    if "@corpus" in sql:
        n = smoke.ROWS // len(smoke.CORPORA)
        return pa.table({"word": [f"w{i}" for i in range(n)], "word_count": list(range(n))})
    if "@b AS b" in sql:
        row = {k: values[k] for k in ("b", "i", "f", "s", "ts", "d", "sa")}
        return pa.Table.from_pylist([{**row, "ia_len": len(values["ia"])}])
    if "WHERE FALSE" in sql:
        return pa.table({"x": pa.array([], pa.int64()), "y": pa.array([], pa.string())})
    if "CAST(NULL" in sql:
        return pa.table({"n": pa.array([None], pa.int64()), "s": pa.array([None], pa.string())})
    raise AssertionError(f"unexpected SQL: {sql}")


class FakeBigQuery:
    """Answers the smoke's SQL as BigQuery would at this size.

    Dry runs estimate ~100 KB for the probe table and 0 for literals. Executed reads
    bill the 10 MiB per-table floor and, with ``enforce_cap``, are rejected over
    maximum_bytes_billed with nothing billed. SQL naming ``denied`` raises ``denial``.
    """

    project = PROJECT

    def __init__(self, *, enforce_cap: bool = True, denied: str | None = None, denial: Exception | None = None,
                 tables: tuple[str, ...] = (), dataset_error: Exception | None = None) -> None:
        self.enforce_cap = enforce_cap
        self.denied, self.denial = denied, denial
        self.tables, self.dataset_error = tables, dataset_error  # the dataset's existing tables; a get_dataset refusal
        self.configs: list[bigquery.QueryJobConfig] = []
        self.sql: list[str] = []
        self.created: dict[str, str] | None = None
        self.datasets: list[str] = []

    def get_dataset(self, ref: str) -> SimpleNamespace:
        assert ref.startswith(f"{PROJECT}.")
        self.datasets.append(ref)
        if self.dataset_error is not None:
            raise self.dataset_error
        return SimpleNamespace(location="US", ref=ref)

    def list_tables(self, dataset: SimpleNamespace, max_results: int | None = None) -> list[SimpleNamespace]:
        return [SimpleNamespace(table_id=t) for t in self.tables][:max_results]

    def get_table(self, ref: str) -> SimpleNamespace:
        assert self.created is not None and ref == self.created["ref"]
        return SimpleNamespace(num_rows=smoke.ROWS, expires=datetime.now(UTC) + timedelta(days=1, seconds=-5), labels={"run": self.created["run"]})

    def query(self, sql: str, job_config: bigquery.QueryJobConfig) -> FakeJob:
        self.configs.append(job_config)
        self.sql.append(sql)
        if self.denied is not None and f".{self.denied}." in sql:
            raise self.denial
        job_id = f"job-{len(self.sql)}"
        create = sql.startswith("CREATE TABLE")
        reads = "smoke_" in sql and not create
        if job_config.dry_run:
            return FakeJob(job_id, processed=100_000 if reads else 0)
        if create:
            ref = re.match(r"CREATE TABLE `([^`]+)`", sql).group(1)
            self.created = {"ref": ref, "run": re.search(r"labels = \[\('run', '([^']+)'\)\]", sql).group(1)}
            return FakeJob(job_id, statement="CREATE_TABLE_AS_SELECT", ddl="CREATE")
        billed = live.BILLING_FLOOR if reads else 0
        cap = job_config.maximum_bytes_billed
        if self.enforce_cap and cap is not None and billed > cap:
            error = BadRequest(f"Query exceeded limit for bytes billed: {cap}. {billed} or higher required.")
            return FakeJob(job_id, processed=None, billed=None, error=error)
        return FakeJob(job_id, processed=100_000 if reads else 0, billed=billed, table=answer(sql, job_config.query_parameters))


class FakeServiceAccount:
    service_account_email = SA
    token = "secret-token"


class FakeSession:
    def __init__(self, status: int = 200, body: dict[str, object] | None = None, error: Exception | None = None) -> None:
        self.status, self.body, self.error = status, body or {}, error
        self.calls: list[tuple[str, object]] = []

    def post(self, url: str, json: object = None, timeout: float | None = None) -> SimpleNamespace:
        self.calls.append((url, json))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeBigQuery:
    client = FakeBigQuery(denied="other", denial=Forbidden("Access Denied: Table synthetic-project:other.INFORMATION_SCHEMA.TABLES"))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setattr(smoke, "connect", lambda project: (client, FakeServiceAccount()))
    return client


def argv(out: Path, *extra: str) -> list[str]:
    return ["--confirm-project", PROJECT, "--dataset", "scratch", "--out", str(out), "--run-id", "T1-Run", *extra]


def evidence(out: Path) -> tuple[dict, list[dict]]:
    jobs = [json.loads(line) for line in (out / "jobs.jsonl").read_text().splitlines()]
    return json.loads((out / "summary.json").read_text()), jobs


@pytest.mark.parametrize(
    ("overrides", "env", "message"),
    [
        ({"--confirm-project": "other-project"}, PROJECT, "must be set and equal --confirm-project"),
        ({}, None, "must be set and equal --confirm-project"),
        ({"--dataset": "scratch`; SELECT 1 --"}, PROJECT, "--dataset must be a dataset name"),
        ({"--dataset": "other_project.scratch"}, PROJECT, "--dataset must be a dataset name"),
        ({"--out": str(REPO / "smoke-evidence")}, PROJECT, "--out must be outside the repository"),
        ({"--out": str(REPO)}, PROJECT, "--out must be outside the repository"),
        ({"--least-privilege": None}, PROJECT, "--least-privilege needs --other-dataset"),
        ({"--least-privilege": None, "--other-dataset": "scratch"}, PROJECT, "--other-dataset must differ"),
        ({"--budget-mib": "0"}, PROJECT, "--budget-mib must be positive"),
        ({"--run-id": "!!"}, PROJECT, "--run-id needs a letter or digit"),
    ],
)
def test_guards_refuse_before_any_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str | None], env: str | None, message: str) -> None:
    def no_client(project: str) -> None:
        raise AssertionError("no client before the guards pass")

    if env is None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", env)
    monkeypatch.setattr(smoke, "connect", no_client)
    options = {"--confirm-project": PROJECT, "--dataset": "scratch", "--out": str(tmp_path / "out"), "--run-id": "t1", **overrides}
    args = [part for flag, value in options.items() for part in ((flag,) if value is None else (flag, value))]
    with pytest.raises(SystemExit, match=re.escape(message)):
        smoke.main(args)
    assert not (REPO / "smoke-evidence").exists()
    assert not (tmp_path / "out").exists()


def test_table_ddl_self_expires_and_never_drops_or_replaces() -> None:
    ddl = smoke.table_ddl("synthetic-project.scratch.smoke_t1", "t1")
    assert ddl.startswith("CREATE TABLE `synthetic-project.scratch.smoke_t1`\n")
    assert "expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)" in ddl
    assert "labels = [('run', 't1')]" in ddl
    assert f"FROM UNNEST(GENERATE_ARRAY(1, {smoke.ROWS})) AS n" in ddl
    assert "AS corpus" in ddl and "AS word_count" in ddl
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|MERGE|UPDATE|INSERT)\b|OR\s+REPLACE|IF\s+NOT\s+EXISTS", ddl, re.IGNORECASE)
    source = SCRIPT.read_text()
    assert "bigquery-public-data" not in source
    assert not re.search(r"\.delete_(table|dataset|model|routine)\(", source)


def test_happy_path_runs_every_check_and_writes_evidence(tmp_path: Path, fake: FakeBigQuery, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert smoke.main(argv(out)) == 0
    summary, jobs = evidence(out)
    assert [c["check"] for c in summary["checks"]] == CHECKS
    assert all(c["ok"] for c in summary["checks"]) and summary["failed"] == 0 and summary["aborted"] is None
    assert (summary["run_id"], summary["table"], summary["location"]) == ("t1-run", "smoke_t1_run", "US")
    assert fake.created == {"ref": f"{PROJECT}.scratch.smoke_t1_run", "run": "t1-run"}
    # Only the bounded execute reads the table and bills (the 10 MiB floor); 1b was rejected unbilled.
    assert summary["billed_bytes"] == live.BILLING_FLOOR and summary["estimate_bytes"] == 100_000
    assert summary["user_emails"] == [SA]
    assert summary["adc_principal"] == {"type": f"{FakeServiceAccount.__module__}.FakeServiceAccount", "service_account_email": SA}
    assert summary["dataset_is_openalex"] is False and summary["dataset_access"] == smoke.DATASET_ACCESS
    assert fake.datasets == [f"{PROJECT}.scratch"]
    assert "secret" not in (out / "summary.json").read_text() + (out / "jobs.jsonl").read_text() + capsys.readouterr().out
    assert [c.labels["run"] for c in fake.configs] == ["t1-run"] * len(fake.configs)
    assert len(jobs) == len(fake.configs) == summary["jobs"]
    assert not any(re.search(r"\b(DROP|DELETE|TRUNCATE)\b", sql) for sql in fake.sql)
    assert all(f"`{PROJECT}.scratch.smoke_t1_run`" in sql for sql in fake.sql if "smoke_" in sql)
    by_step = {j["step"]: j for j in jobs}
    assert [j["step"] for j in jobs if j["step"].startswith(("dry-run-budget", "provider-cap"))] == ["dry-run-budget-dry", "provider-cap-dry", "provider-cap"]
    assert by_step["dry-run-budget-dry"]["error"] == "QueryBudgetExceededError"
    rejected = by_step["provider-cap"]
    assert (rejected["error"], rejected["state"], rejected["total_bytes_billed"], rejected["cap"]) == ("bytesBilledLimitExceeded", "DONE", 0, 8 * MIB)
    assert by_step["bounded-execute"]["cap"] == 20 * MIB
    assert by_step["create-table"]["ddl_operation_performed"] == "CREATE"


def test_a_failing_check_exits_1(tmp_path: Path, fake: FakeBigQuery) -> None:
    fake.enforce_cap = False  # the provider lets the 8 MiB-capped job run and bill the floor
    out = tmp_path / "out"
    assert smoke.main(argv(out)) == 1
    summary, _ = evidence(out)
    failed = [c["check"] for c in summary["checks"] if not c["ok"]]
    assert failed == [CHECKS[2], CHECKS[7]] and summary["failed"] == 2
    assert summary["billed_bytes"] == 2 * live.BILLING_FLOOR


def test_budget_abort_still_writes_the_summary(tmp_path: Path, fake: FakeBigQuery) -> None:
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="budget exceeded before typed-params"):
        smoke.main(argv(out, "--budget-mib", "1"))
    summary, jobs = evidence(out)
    assert summary["aborted"] == "SystemExit"
    assert summary["user_emails"] == [SA] and summary["billed_bytes"] == live.BILLING_FLOOR
    assert jobs[-1]["step"] == "bounded-execute"


@pytest.mark.parametrize("name", ["openalex", "OpenAlex"])
def test_empty_openalex_is_refused_with_evidence_and_no_jobs(tmp_path: Path, fake: FakeBigQuery, name: str) -> None:
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="--dataset openalex is empty"):
        smoke.main([a if a != "scratch" else name for a in argv(out)])
    summary, jobs = evidence(out)
    assert (summary["aborted"], summary["dataset_is_openalex"], summary["checks"], jobs) == ("SystemExit", True, [], [])
    assert fake.configs == []


def test_openalex_after_the_103_run_is_used_in_place(tmp_path: Path, fake: FakeBigQuery) -> None:
    fake.tables = ("works", "work_topics")
    out = tmp_path / "out"
    assert smoke.main([a if a != "scratch" else "openalex" for a in argv(out)]) == 0
    summary, _ = evidence(out)
    assert summary["dataset_is_openalex"] is True and summary["failed"] == 0
    assert fake.created["ref"] == f"{PROJECT}.openalex.smoke_t1_run"  # self-expiring, labelled, never replacing


def test_a_refused_get_dataset_still_writes_the_evidence(tmp_path: Path, fake: FakeBigQuery) -> None:
    fake.dataset_error = Forbidden("Access Denied: Dataset synthetic-project:scratch: Permission bigquery.datasets.get denied")
    out = tmp_path / "out"
    with pytest.raises(Forbidden):
        smoke.main(argv(out))
    summary, jobs = evidence(out)
    assert (summary["aborted"], summary["checks"], jobs) == ("Forbidden", [], [])
    assert "location" not in summary and summary["adc_principal"]["service_account_email"] == SA


def test_least_privilege_in_main_and_default_run_id(tmp_path: Path, fake: FakeBigQuery, monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(body={})
    monkeypatch.setattr(smoke, "AuthorizedSession", lambda credentials: session)
    out = tmp_path / "out"
    args = [a for a in argv(out, "--least-privilege", "--other-dataset", "other") if a not in ("--run-id", "T1-Run")]
    assert smoke.main(args) == 0
    summary, _ = evidence(out)
    assert re.fullmatch(r"\d{8}t\d{6}", summary["run_id"]) and summary["table"] == f"smoke_{summary['run_id']}"
    assert [c["check"][:4] for c in summary["checks"][-2:]] == ["LP-a", "LP-b"] and summary["least_privilege"] is True
    assert session.calls == [(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions", {"permissions": ["bigquery.datasets.create"]})]
    assert fake.configs[-1].dry_run is True and "INFORMATION_SCHEMA.TABLES" in fake.sql[-1]


def run_for(client: object) -> live.Run:
    return live.Run(client, "t1", 200 * MIB, sleep=lambda _: None)


@pytest.mark.parametrize(
    ("session", "ok"),
    [
        (FakeSession(body={}), True),
        (FakeSession(body={"permissions": ["bigquery.datasets.create"]}), False),
        (FakeSession(status=403), False),
        (FakeSession(error=ConnectionError("reset")), False),
    ],
)
def test_permission_absent_passes_only_on_a_clean_answer(session: FakeSession, ok: bool) -> None:
    run = run_for(FakeBigQuery())
    smoke.permission_absent(run, session, PROJECT)
    assert [c["ok"] for c in run.checks] == [ok]
    assert run.jobs == []


@pytest.mark.parametrize(
    ("denial", "ok"),
    [
        (Forbidden("Access Denied: Table p:other.INFORMATION_SCHEMA.TABLES: User does not have permission"), True),
        (NotFound("Not found: Dataset p:other was not found in location US"), False),  # a typo or another location
        (Forbidden("Quota exceeded: Your project exceeded quota for concurrent queries"), False),
        (BadRequest("Syntax error"), False),
        (None, False),  # the dry run succeeds: the identity can read the dataset
    ],
)
def test_read_refused_passes_only_on_access_denied(denial: Exception | None, ok: bool) -> None:
    client = FakeBigQuery(denied="other" if denial else None, denial=denial)
    run = run_for(client)
    smoke.read_refused(run, PROJECT, "other")
    assert [c["ok"] for c in run.checks] == [ok]
    if isinstance(denial, NotFound):
        assert "not a refusal" in run.checks[0]["detail"]
    assert client.configs[-1].dry_run is True
    assert [j["step"] for j in run.jobs] == ([] if denial else ["lp-other-dataset"])


def test_adapter_jobs_are_recorded_when_the_warehouse_raises(tmp_path: Path) -> None:
    client = FakeBigQuery()
    log = tmp_path / "jobs.jsonl"
    run = live.Run(client, "t1", 200 * MIB, log, sleep=lambda _: None)
    adapter = live.AdapterClient(run)
    probe = f"SELECT word, word_count FROM `{PROJECT}.scratch.smoke_t1` WHERE corpus = @corpus"
    table, error = smoke.attempt(run, adapter, smoke.warehouse(adapter, PROJECT, "scratch", smoke.PROVIDER_CAP), "provider-cap", probe, {"corpus": "hamlet"})
    assert table is None and isinstance(error, QueryBudgetExceededError)
    table, error = smoke.attempt(run, adapter, smoke.warehouse(adapter, PROJECT, "scratch", 1024), "dry-run-budget", probe, {"corpus": "hamlet"})
    assert table is None and isinstance(error, QueryBudgetExceededError) and "dry-run" in str(error)
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["step"], r["error"], r["total_bytes_billed"]) for r in records] == [
        ("provider-cap-dry", None, 0),
        ("provider-cap", "bytesBilledLimitExceeded", 0),
        ("dry-run-budget-dry", "QueryBudgetExceededError", 0),
    ]
    assert [c.labels for c in client.configs] == [{"run": "t1", "step": "provider-cap"}] * 2 + [{"run": "t1", "step": "dry-run-budget"}]
    assert not adapter.pending and run.billed == 0
