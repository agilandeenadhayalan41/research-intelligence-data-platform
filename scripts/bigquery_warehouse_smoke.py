"""Manual BigQueryWarehouse smoke (the #92 checks, #108 Stage 0): budgets, provider cap, binding, empty/null results.

Not part of ``make test`` or CI. Run by an operator against an existing dataset:
``openalex`` once it holds the #103 tables, or a scratch dataset. An empty
``openalex`` is refused, since ``scripts/bigquery_sandbox_103.py`` needs it empty.
The identity needs ``bigquery.jobs.create`` on the project and, on ``--dataset``,
``bigquery.datasets.get`` and ``bigquery.tables.create``/``get``/``getData``/
``updateData`` (plus ``tables.list`` on ``openalex``), all in dataEditor. On a
scratch dataset that is a grant beyond dataEditor on ``openalex`` only, so to
re-run the checks as the least-privilege identity, use ``openalex`` after the
#103 run. ``summary.json`` records which. WSL, ADC already configured::

    GOOGLE_CLOUD_PROJECT=<project> .venv/bin/python scripts/bigquery_warehouse_smoke.py \\
        --confirm-project <project> --dataset <dataset> --out /tmp/wh-smoke \\
        [--run-id <id>] [--budget-mib 200] [--least-privilege --other-dataset <dataset>]

Reads no public datasets (VPC Service Controls can block them). The one table it
reads, ``smoke_<run id>``, is created in ``--dataset`` from GENERATE_ARRAY with a
``run`` label and a one-day expiration, so it removes itself; the script never
drops or deletes anything. Every job is labelled ``run=<run id>``, capped with
``maximum_bytes_billed`` and appended to ``jobs.jsonl``; the run aborts once billed
bytes pass ``--budget-mib``. ``--out`` must be outside the repository. Its
``summary.json`` holds the checks, byte totals, the jobs' ``user_email`` values and
the ADC principal (credential type and service-account email, never a token).

``--least-privilege`` also checks, mutating nothing, that the identity lacks
``bigquery.datasets.create`` on the project (Resource Manager testIamPermissions)
and that a dry-run read of ``--other-dataset``, an operator-created non-public
dataset in the same project, is refused with 403 Access Denied. A 404 Not found
(a mistyped name, or a dataset in another location) fails the check: it is not
evidence of a refusal. Exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import google.auth
import pyarrow as pa
from google.api_core.exceptions import Forbidden, NotFound
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

from research_platform.analytics.bigquery.sandbox.live import (
    DATASET,
    MIB,
    AdapterClient,
    Run,
    adc_principal,
    evidence_dir,
    label,
)
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.errors import QueryBudgetExceededError

REPO = Path(__file__).resolve().parents[1]
DATASET_NAME = re.compile(r"[A-Za-z0-9_]{1,1024}")  # interpolated into SQL, so nothing else
CORPORA = ("hamlet", "macbeth", "othello", "tempest")
ROWS = 4000  # ~100 KB: the estimate fits PROVIDER_CAP, the billed floor does not
PROVIDER_CAP = 8 * MIB  # under live.BILLING_FLOOR, the 10 MiB billed per table read
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
TEST_IAM = "https://cloudresourcemanager.googleapis.com/v1/projects/{}:testIamPermissions"
CREATE_DATASETS = "bigquery.datasets.create"
DATASET_ACCESS = (
    "bigquery.jobs.create on the project; bigquery.datasets.get and bigquery.tables.create/get/getData/updateData "
    "(plus tables.list on openalex) on --dataset"
)


def table_ddl(table_ref: str, run_id: str) -> str:
    """The probe table: ROWS synthetic rows over CORPORA, labelled with the run and expiring after a day."""
    corpora = ", ".join(f"'{c}'" for c in CORPORA)
    return f"""CREATE TABLE `{table_ref}`
OPTIONS (
  expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 DAY),
  labels = [('run', '{run_id}')]
)
AS SELECT
  [{corpora}][OFFSET(MOD(n, {len(CORPORA)}))] AS corpus,
  CONCAT('w', CAST(n AS STRING)) AS word,
  MOD(n * 37, 1000) AS word_count
FROM UNNEST(GENERATE_ARRAY(1, {ROWS})) AS n"""


def connect(project: str) -> tuple[bigquery.Client, Any]:
    """ADC credentials and a BigQuery client built on them."""
    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM])
    return bigquery.Client(project=project, credentials=credentials), credentials


def warehouse(adapter: AdapterClient, project: str, dataset: str, cap: int) -> BigQueryWarehouse:
    return BigQueryWarehouse(WarehouseConfig(analytical="bigquery", bigquery_dataset=dataset), CloudConfig(project_id=project), client=adapter, maximum_bytes_billed=cap)


def attempt(run: Run, adapter: AdapterClient, wh: BigQueryWarehouse, step: str, sql: str, params: dict[str, Any] | None = None) -> tuple[pa.Table | None, Exception | None]:
    """One BigQueryWarehouse call recorded as ``step``: (table, None), or (None, what it raised)."""
    if run.billed > run.budget_bytes:
        raise SystemExit(f"budget exceeded before {step}: {run.billed} billed bytes")
    try:
        with adapter.recording(step):
            return wh.query(sql, params), None
    except Exception as exc:  # noqa: BLE001 - the caller's check judges it; recording() has logged the jobs
        return None, exc


def executed(run: Run, step: str | None = None) -> list[dict[str, Any]]:
    return [j for j in run.jobs if not j["dry_run"] and step in (None, j["step"])]


def describe(error: Exception | None) -> str:
    # Type names only: provider messages carry the request URL, which names the project.
    return "no error raised" if error is None else type(error).__name__


def columns(table: pa.Table) -> str:
    return ", ".join(f"{f.name} {f.type}" for f in table.schema)


def smoke(run: Run, project: str, dataset: str, facts: dict[str, Any]) -> None:
    table = "smoke_" + run.run_id.replace("-", "_")
    ref = f"{project}.{dataset}.{table}"
    facts["table"] = table
    probe = f"SELECT word, word_count FROM `{ref}` WHERE corpus = @corpus"
    corpus = {"corpus": CORPORA[0]}

    # 0. The synthetic probe table (plain CREATE: fails rather than replace an existing table).
    created = run.query("create-table", table_ddl(ref, run.run_id))
    info = run.client.get_table(ref)
    left = None if info.expires is None else info.expires - datetime.now(UTC)
    run.check(
        "0. synthetic table created, labelled, expiring in a day",
        created.ddl_operation_performed == "CREATE"
        and info.num_rows == ROWS
        and (info.labels or {}).get("run") == run.run_id
        and left is not None
        and timedelta(hours=23) < left <= timedelta(hours=25),
        {"rows": info.num_rows, "expires_in_h": None if left is None else round(left / timedelta(hours=1), 2)},
    )
    estimate = int(run.query("estimate", probe, params=[bigquery.ScalarQueryParameter("corpus", "STRING", CORPORA[0])], dry_run=True).total_bytes_processed or 0)
    facts["estimate_bytes"] = estimate
    print(f"dry-run estimate for the probe: {estimate} bytes")
    adapter = AdapterClient(run)

    # 1. Dry-run budget rejection before execution.
    _, error = attempt(run, adapter, warehouse(adapter, project, dataset, 1024), "dry-run-budget", probe, corpus)
    run.check(
        "1. over-budget query rejected by dry run before execution",
        isinstance(error, QueryBudgetExceededError) and "dry-run" in str(error) and not executed(run, "dry-run-budget"),
        describe(error),
    )

    # 1b. Provider-enforced cap: the estimate fits under 8 MiB, BigQuery's 10 MiB per-table billing floor does not.
    name = "1b. provider rejects the job over maximum_bytes_billed, 0 bytes billed"
    if estimate < PROVIDER_CAP:
        _, error = attempt(run, adapter, warehouse(adapter, project, dataset, PROVIDER_CAP), "provider-cap", probe, corpus)
        jobs = executed(run, "provider-cap")  # an executed job: the dry run let it through
        run.check(
            name,
            isinstance(error, QueryBudgetExceededError) and len(jobs) == 1 and jobs[0]["total_bytes_billed"] == 0,
            {"error": describe(error), "reason": [j["error"] for j in jobs], "billed": [j["total_bytes_billed"] for j in jobs]},
        )
    else:
        run.check(name, False, f"estimate {estimate} is not under the {PROVIDER_CAP}-byte cap")

    # 2. Bounded execute within maximum_bytes_billed.
    bounded, error = attempt(run, adapter, warehouse(adapter, project, dataset, 20 * MIB), "bounded-execute", probe, corpus)
    billed = [j["total_bytes_billed"] for j in executed(run, "bounded-execute")]
    run.check(
        "2. bounded execute returns rows within a 20 MiB cap",
        bounded is not None
        and bounded.num_rows == ROWS // len(CORPORA)
        and bounded.column_names == ["word", "word_count"]
        and bool(billed)
        and all(b <= 20 * MIB for b in billed),
        {"rows": bounded.num_rows, "billed": billed} if bounded is not None else describe(error),
    )

    # 3. Named @parameter binding for representative types (literal-only, 0 bytes).
    wh = warehouse(adapter, project, dataset, 10 * MIB)
    params = {
        "b": True,
        "i": 2**40,
        "f": 1.5,
        "s": "o'brien; DROP TABLE x --",
        "ts": datetime(2026, 10, 10, 1, 2, 3, tzinfo=UTC),
        "d": date(2026, 10, 10),
        "ia": [1, 2, 3],
        "sa": ["a", "b"],
    }
    bound, error = attempt(
        run, adapter, wh, "typed-params",
        "SELECT @b AS b, @i AS i, @f AS f, @s AS s, @ts AS ts, @d AS d, ARRAY_LENGTH(@ia) AS ia_len, @sa AS sa",
        params,
    )
    row = bound.to_pylist()[0] if bound is not None and bound.num_rows else {}
    want = {"i": 2**40, "f": 1.5, "s": params["s"], "ts": params["ts"], "d": params["d"], "ia_len": 3, "sa": ["a", "b"]}
    run.check(
        "3. typed parameters bound (BOOL/INT64/FLOAT64/STRING/TIMESTAMP/DATE/ARRAY)",
        row.get("b") is True and {k: row.get(k) for k in want} == want,
        str({k: type(v).__name__ for k, v in row.items()}) if bound is not None else describe(error),
    )

    # 4. Empty and null results.
    empty, error = attempt(run, adapter, wh, "empty-result", "SELECT 1 AS x, 'a' AS y FROM UNNEST([1]) WHERE FALSE")
    run.check(
        "4a. empty result keeps schema",
        empty is not None and empty.num_rows == 0 and empty.column_names == ["x", "y"],
        columns(empty) if empty is not None else describe(error),
    )
    nulls, error = attempt(run, adapter, wh, "null-result", "SELECT CAST(NULL AS INT64) AS n, CAST(NULL AS STRING) AS s")
    run.check(
        "4b. null result values are null with typed columns",
        nulls is not None
        and nulls.to_pylist() == [{"n": None, "s": None}]
        and nulls.schema.field("n").type == pa.int64()
        and nulls.schema.field("s").type == pa.string(),
        columns(nulls) if nulls is not None else describe(error),
    )

    # 5. Bytes processed/billed of this run's executed jobs, from their reloaded records.
    jobs = executed(run)
    run.check(
        "5. executed jobs recorded DONE with bytes billed within their caps",
        bool(jobs) and all(j["job_id"] and j["state"] == "DONE" and j["total_bytes_billed"] <= j["cap"] for j in jobs) and run.billed <= run.budget_bytes,
        {"executed_jobs": len(jobs), "processed": sum(int(j["total_bytes_processed"] or 0) for j in jobs), "billed": run.billed},
    )


def permission_absent(run: Run, session: Any, project: str, permission: str = CREATE_DATASETS) -> None:
    """LP-a: testIamPermissions (non-mutating; needs no permission itself) must not return ``permission``."""
    name = f"LP-a. {permission} not granted on the project"
    try:
        response = session.post(TEST_IAM.format(project), json={"permissions": [permission]}, timeout=30)
    except Exception as exc:  # noqa: BLE001 - no answer is not evidence of absence
        run.check(name, False, f"request failed: {type(exc).__name__}")
        return
    if response.status_code != 200:
        run.check(name, False, f"HTTP {response.status_code}")
        return
    granted = response.json().get("permissions", [])
    run.check(name, permission not in granted, {"granted": granted})


def read_refused(run: Run, project: str, other: str) -> None:
    """LP-b: a dry-run read of ``other`` (free; creates nothing) must be refused.

    It reads the dataset's INFORMATION_SCHEMA.TABLES, which exists in every
    dataset: a guessed table name would come back Not found even with access.
    """
    name = "LP-b. dry-run read of --other-dataset refused (403 Access Denied)"
    try:
        run.query("lp-other-dataset", f"SELECT table_name FROM `{project}.{other}.INFORMATION_SCHEMA.TABLES`", dry_run=True)
    except Forbidden as exc:
        # Forbidden alone is not enough: quota and rate limits are 403s too.
        run.check(name, "Access Denied" in str(exc), f"HTTP {exc.code} {type(exc).__name__}")
    except NotFound as exc:
        run.check(name, False, f"HTTP {exc.code} NotFound: dataset not found (check --other-dataset name and location); not a refusal")
    except Exception as exc:  # noqa: BLE001 - any other outcome is a FAIL, not a crash
        run.check(name, False, f"unexpected {type(exc).__name__}")
    else:
        run.check(name, False, "dry run succeeded: the identity can read it")


def write_summary(out: Path, run: Run, credentials: object, facts: dict[str, Any], aborted: str | None) -> int:
    """summary.json, also after an abort; returns the number of failed checks."""
    jobs = executed(run)
    processed = sum(int(j["total_bytes_processed"] or 0) for j in jobs)
    emails = sorted({j["user_email"] for j in run.jobs if j["user_email"]})
    principal = adc_principal(credentials)  # after the jobs, so a metadata credential has its email by now
    failed = [c for c in run.checks if not c["ok"]]
    summary = {
        "run_id": run.run_id,
        **facts,
        "jobs": len(run.jobs),
        "executed_jobs": len(jobs),
        "processed_bytes": processed,
        "billed_bytes": run.billed,
        "budget_bytes": run.budget_bytes,
        "user_emails": emails,
        "adc_principal": principal,
        "checks": run.checks,
        "failed": len(failed),
        "aborted": aborted,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"identity: ADC {principal['type']}; {len(emails)} job user_email value(s), in summary.json")
    print(f"\n{len(run.checks) - len(failed)}/{len(run.checks)} checks passed; {len(run.jobs)} jobs; {processed} bytes processed, {run.billed} bytes billed")
    return len(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project", required=True)
    parser.add_argument("--dataset", required=True, help="existing dataset: openalex after the #103 run, or a scratch dataset")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dt%H%M%S"))
    parser.add_argument("--budget-mib", type=float, default=200.0)
    parser.add_argument("--least-privilege", action="store_true", help="also check the identity cannot create datasets or read --other-dataset")
    parser.add_argument("--other-dataset", help="operator-created non-public dataset the identity must not read")
    args = parser.parse_args(argv)

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project or project != args.confirm_project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set and equal --confirm-project")
    for flag, value in (("--dataset", args.dataset), ("--other-dataset", args.other_dataset)):
        if value is not None and not DATASET_NAME.fullmatch(value):
            raise SystemExit(f"{flag} must be a dataset name (letters, digits, underscores)")
    if args.least_privilege and not args.other_dataset:
        raise SystemExit("--least-privilege needs --other-dataset")
    if args.other_dataset is not None and args.other_dataset == args.dataset:
        raise SystemExit("--other-dataset must differ from --dataset")
    if args.budget_mib <= 0:
        raise SystemExit("--budget-mib must be positive")
    run_id = label(args.run_id)
    if not re.search(r"[a-z0-9]", run_id):
        raise SystemExit("--run-id needs a letter or digit")
    out = evidence_dir(args.out, REPO)
    out.mkdir(parents=True, exist_ok=True)

    client, credentials = connect(project)
    log = out / "jobs.jsonl"
    log.write_text("")
    run = Run(client, run_id, int(args.budget_mib * MIB), log)
    openalex = args.dataset == DATASET  # dataset names are case-sensitive
    facts: dict[str, Any] = {"least_privilege": args.least_privilege, "dataset_is_openalex": openalex, "dataset_access": DATASET_ACCESS}
    try:
        dataset = client.get_dataset(f"{project}.{args.dataset}")  # must exist already; never created here
        facts["location"] = dataset.location
        if openalex and next(iter(client.list_tables(dataset, max_results=1)), None) is None:
            raise SystemExit(f"--dataset {DATASET} is empty: scripts/bigquery_sandbox_103.py needs it so; use it after the #103 run")
        print(f"run {run.run_id}: location {dataset.location}")
        smoke(run, project, args.dataset, facts)
        if args.least_privilege:
            permission_absent(run, AuthorizedSession(credentials), project)
            read_refused(run, project, args.other_dataset)
    except BaseException as exc:  # the budget's SystemExit included: keep the evidence, then stop
        write_summary(out, run, credentials, facts, type(exc).__name__)
        raise
    return 1 if write_summary(out, run, credentials, facts, None) else 0


if __name__ == "__main__":
    sys.exit(main())
