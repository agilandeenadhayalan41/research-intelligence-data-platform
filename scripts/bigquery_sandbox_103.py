"""Manual #103 sandbox run: deploy, load, publish and measure the Step 15-16 BigQuery SQL.

Not part of ``make test`` or CI. Run once by an operator against an empty,
operator-created dataset literally named ``openalex`` (the repo SQL hard-codes
that name), ideally created with a default table expiration::

    bq mk --dataset --location=US --default_table_expiration=1209600 <project>:openalex

Then (WSL, user ADC)::

    GOOGLE_CLOUD_PROJECT=<project> .venv/bin/python scripts/bigquery_sandbox_103.py \\
        --confirm-project <project> --out /tmp/i103 [--scale tiny|default|large]

``--scale`` takes a ``SYNTHETIC_SCALES`` preset. ``large`` (8 x 12,500 dated
works, ten times the default delta mix) is slow to generate locally: the
in-memory canonical store makes batch-1 ingest grow with the square of the
corpus (~13 min at default scale, roughly a day at ``large``). It is still
labelled synthetic and does not stand in for a representative slice. The
cycle is generated before anything is deployed.

Every job is labelled ``run=<run id>`` and capped with ``maximum_bytes_billed``;
the run aborts once cumulative billed bytes pass ``--budget-gib``. An executed
job without an explicit cap is dry-run first (free; an existing dry run is
reused where the script already makes one) and capped at
``min(--max-job-mib, max(cap_for(sql), 2 x estimate + 10 MiB per table))``,
so ``cap_for`` still decides at tiny/default scale. Each BigQueryWarehouse call
is capped at twice what the raw run of the same pattern billed (at least
64 MiB and the dry-run estimate the warehouse checks against its cap, at most
``--max-job-mib``). Any job, raw or BigQueryWarehouse, whose cap exceeds the
remaining budget is refused before it is submitted. Result timeouts are
``--job-timeout-s`` plus ``--timeout-s-per-gib`` per GiB of the job's dry-run
estimate (a warehouse call uses its pattern's); loads and jobs without an
estimate get ``--job-timeout-s``.

After the latency repeats, one query pattern goes through a BigQueryWarehouse
with a 1 s job timeout (``--skip-timeout-probe`` skips it): it must time out
(and cancel) or complete, and its job is waited on until DONE and billed. The
timeout is not smaller because ``QueryJob.result`` also passes it to each
``jobs.get`` as the HTTP timeout, and a request timing out there is retried
for ~40-50 min (google-cloud-bigquery 3.46 DEFAULT_RETRY and DEFAULT_JOB_RETRY).
``result()`` checks its own deadline only between polls, and each
``getQueryResults`` poll waits up to ~10 s server-side for the job, so a job
that finishes within that wait is DONE first: at tiny/default scale expect
``completed`` (with or without QueryTimeoutError); ``cancelled`` needs a longer
job. The probe's wall time is recorded.

Clustering is reported per query pattern as executed vs dry-run bytes, OBSERVED
only for a pattern one of whose tables reaches 64 MiB logical. Processed bytes
come from the pattern's last latency repeat (the raw run when there are none),
after ``--cluster-settle-s`` of waiting; ``processed_from`` names the run.

Repo SQL is submitted byte-for-byte. Evidence (``jobs.jsonl``, ``summary.json``)
goes to ``--out``, which must be outside the repository (by path and by file
identity). The script never creates or deletes datasets; the operator removes
the dataset afterwards (or lets the table expiration do it).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.cloud import bigquery

from research_platform.analytics.bigquery.contracts import build_table_contracts
from research_platform.analytics.bigquery.sandbox.live import (
    BILLING_FLOOR,
    DATASET,
    MIB,
    TABLE_REF,
    AdapterClient,
    Run,
    adc_principal,
    cap_for,
    evidence_dir,
    nearest_rank,
)
from research_platform.analytics.bigquery.sandbox.synthetic import (
    SYNTHETIC_SCALES,
    write_synthetic_cycle,
)
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.quality.registry import load_quality_registry
from research_platform.warehouse.bigquery import BigQueryWarehouse
from research_platform.warehouse.errors import QueryTimeoutError

REPO = Path(__file__).resolve().parents[1]
SQL = REPO / "sql" / "bigquery" / "openalex"
LEGACY_TYPES = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL"}
YEAR_BOUNDED = ("publication_trends", "open_access_trends", "publisher_topic_license_year")
ADAPTER_MIN_CAP = 64 * MIB
# Below this logical size for every table a pattern reads, pruning differences are noise.
CLUSTER_MIN_TABLE_BYTES = 64 * MIB
CLUSTER_OBSERVED = "OBSERVED; dry-run is the unpruned upper bound; cause not isolated"
# The widest join pattern. The timeout is also each jobs.get HTTP timeout, so it
# stays well above one round trip (see the module docstring).
TIMEOUT_PROBE_PATTERN = "publisher_topic_license_year"
TIMEOUT_PROBE_S = 1.0
# Text matches for cluster_filters, applied with backticks and -- comments removed.
TABLE_NAME = re.compile(r"\bopenalex\.([a-z_]+)\b")
TABLE_ALIAS = re.compile(r"\bopenalex\.([a-z_]+)\s+(?i:AS)\s+(\w+)")
PARAM_FILTER = re.compile(r"(?:\b(\w+)\.)?\b(\w+)\s*=\s*@")


def job_cap(sql: str, estimate: int | None, ceiling: int) -> int:
    """Twice the dry-run estimate (whole MiB) plus the billing floor per table; at least cap_for(sql), at most ``ceiling``."""
    tables = len(set(TABLE_REF.findall(sql))) or 1
    sized = math.ceil(2 * (estimate or 0) / MIB) * MIB + BILLING_FLOOR * tables
    return min(ceiling, max(cap_for(sql), sized))


def adapter_cap(billed: int, estimate: int, ceiling: int) -> int:
    """Twice what the raw run billed, at least 64 MiB; never below the dry-run estimate the warehouse refuses above its cap."""
    return min(ceiling, max(ADAPTER_MIN_CAP, 2 * billed, estimate))


def adapter_warehouse(project: str, client: AdapterClient, cap: int, timeout_s: float) -> BigQueryWarehouse:
    return BigQueryWarehouse(
        WarehouseConfig(analytical="bigquery", bigquery_dataset=DATASET),
        CloudConfig(project_id=project),
        client=client,
        maximum_bytes_billed=cap,
        job_timeout_seconds=timeout_s,
    )


class SizedRun(Run):
    """A ``Run`` whose executed jobs are sized from a dry-run estimate and refused above the remaining budget.

    Without an explicit cap the cap is ``job_cap`` of ``estimate`` (dry-run first
    when the caller has none); the result timeout is ``sized_timeout`` of it.
    """

    def __init__(self, *args: Any, max_job_bytes: int = 512 * MIB, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_job_bytes = max_job_bytes

    def cap(self, sql: str, estimate: int | None) -> int:
        return job_cap(sql, estimate, self.max_job_bytes)

    def query(
        self,
        step: str,
        sql: str,
        *,
        params: list[Any] | None = None,
        cap: int | None = None,
        estimate: int | None = None,
        dry_run: bool = False,
        expect_error: bool = False,
        timeout: float | None = None,
    ) -> bigquery.QueryJob:
        if dry_run:
            # Free; the ceiling keeps an estimate above cap_for(sql) from being refused.
            cap = self.max_job_bytes if cap is None else cap
        else:
            if cap is None:
                if estimate is None:
                    estimate = self.estimate(step, sql, params)  # recorded as <step>-est
                cap = self.cap(sql, estimate)
            timeout = self.sized_timeout(estimate) if timeout is None else timeout
            self.ensure_fits(step, cap)
        return super().query(step, sql, params=params, cap=cap, dry_run=dry_run, expect_error=expect_error, timeout=timeout)

    def rows(self, step: str, sql: str, params: list[Any] | None = None, *, estimate: int | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self.query(step, sql, params=params, estimate=estimate).result()]


def cluster_filters(sql: str, clustering: Mapping[str, tuple[str, ...]]) -> list[str]:
    """``table.column`` for each cluster column of a referenced table that ``sql`` compares with a parameter.

    A text match, not a parser: with backticks and ``--`` comments removed, an
    ``<alias>.<column> = @`` whose alias is declared ``openalex.<table> AS <alias>``
    (or is the table name), or an unqualified ``<column> = @`` credited to every
    referenced table clustered on that column. Only ``=`` counts; ranges,
    ``IN UNNEST(@...)`` and ``@param = column`` are not detected.
    """
    plain = re.sub(r"--[^\n]*", "", sql.replace("`", ""))
    tables = set(TABLE_NAME.findall(plain))
    aliases = {table: table for table in tables} | {alias: table for table, alias in TABLE_ALIAS.findall(plain)}
    found = set()
    for alias, column in PARAM_FILTER.findall(plain):
        owners = {aliases[alias]} if alias in aliases else (set() if alias else tables)  # unknown alias: a CTE
        found |= {f"{table}.{column}" for table in owners if column in clustering.get(table, ())}
    return sorted(found)


def clustering_patterns(
    cases: dict[str, tuple[str, list[Any]]],
    measured: dict[str, tuple[int, int, str]],
    partitions: list[dict[str, Any]],
    min_table_bytes: int = CLUSTER_MIN_TABLE_BYTES,
) -> dict[str, dict[str, Any]]:
    """Per query pattern: its status, dry-run estimate vs executed processed bytes (and which run), the cluster columns it filters, its tables' partitions.

    A pattern is OBSERVED only when one of its own tables reaches ``min_table_bytes`` logical.
    """
    clustering = {contract.table_name: contract.clustering.fields for contract in build_table_contracts()}
    by_table = {row["table_name"]: row for row in partitions}
    result = {}
    for stem, (sql, _) in cases.items():
        estimate, processed, source = measured[stem]
        tables = sorted(set(TABLE_NAME.findall(sql.replace("`", ""))))
        largest = max((int(by_table.get(t, {}).get("total_logical_bytes") or 0) for t in tables), default=0)
        result[stem] = {
            "status": CLUSTER_OBSERVED if largest >= min_table_bytes else f"NOT MEASURED: its tables are under {min_table_bytes // MIB} MiB logical",
            "dry_run_bytes": estimate,
            "processed_bytes": processed,
            "processed_from": source,
            "processed_over_estimate": round(processed / estimate, 4) if estimate else None,
            "cluster_filters": cluster_filters(sql, clustering),
            "tables": {t: {k: by_table.get(t, {}).get(k) for k in ("partition_count", "total_logical_bytes")} for t in tables},
        }
    return result


def clustering_summary(patterns: dict[str, dict[str, Any]], partitions: list[dict[str, Any]], min_table_bytes: int = CLUSTER_MIN_TABLE_BYTES) -> dict[str, Any]:
    """The per-pattern statuses rolled up; with none OBSERVED the #103 note stands."""
    largest = max((int(row.get("total_logical_bytes") or 0) for row in partitions), default=0)
    observed = sorted(stem for stem, pattern in patterns.items() if pattern["status"] == CLUSTER_OBSERVED)
    if not observed:
        status = (
            f"NOT MEASURED (tables of KB-MB): no query pattern reads a table of {min_table_bytes // MIB} MiB logical, "
            "so clustering effectiveness is not meaningful at this scale"
        )
    elif len(observed) == len(patterns):
        status = CLUSTER_OBSERVED
    else:
        status = f"OBSERVED for {len(observed)} of {len(patterns)} patterns (observed_patterns); NOT MEASURED for the rest"
    return {"status": status, "observed_patterns": observed, "largest_table_logical_bytes": largest, "min_table_bytes": min_table_bytes, "patterns": patterns}


def remeasure(run: Run, measured: dict[str, tuple[int, int, str]], runs: int) -> dict[str, tuple[int, int, str]]:
    """Processed bytes from each pattern's last latency repeat, which runs well after the MERGEs, where there is one."""
    by_step = {record["step"]: record for record in run.jobs}
    result = {}
    for stem, (estimate, processed, source) in measured.items():
        last = by_step.get(f"latency-{stem}-{runs - 1:02d}") if runs else None
        result[stem] = (estimate, int(last["total_bytes_processed"] or 0), last["step"]) if last else (estimate, processed, source)
    return result


def warehouse_query(run: Run, adapter: AdapterClient, warehouse: Any, step: str, sql: str, params: dict[str, Any]) -> Any:
    """One BigQueryWarehouse call recorded as ``step``, refused like a raw job when its cap exceeds the remaining budget."""
    run.ensure_fits(step, warehouse.maximum_bytes_billed)
    with adapter.recording(step):
        return warehouse.query(sql, params)


def timeout_probe(run: Run, adapter: AdapterClient, warehouse: Any, stem: str, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    """One query pattern through a BigQueryWarehouse with a short job timeout: QueryTimeoutError or completion.

    ``recording()`` reloads the job until DONE (bounded) before recording it, so
    what it billed is in the evidence and the run's billed total either way.
    ``wall_ms`` covers the whole call: dry run, execution and that wait.
    """
    step = f"timeout-probe-{stem}"
    raised = None
    started = time.perf_counter()
    try:
        warehouse_query(run, adapter, warehouse, step, sql, params)
    except Exception as exc:  # noqa: BLE001 - recorded by recording(); judged below
        raised = exc
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    executed = [r for r in run.jobs if r["step"] == step]
    record = executed[-1] if executed else {}
    # BigQuery reports a job stopped by cancel() as reason "stopped"; recording()
    # puts the warehouse's exception name on a job that reports no error.
    if not record:
        outcome = "not submitted"
    elif record["state"] != "DONE":
        outcome = "not DONE after the wait"
    elif record["error"] == "stopped":
        outcome = "cancelled"
    elif record["error"] in (None, "QueryTimeoutError"):
        outcome = "completed"
    else:
        outcome = f"failed: {record['error']}"
    detail = {
        "pattern": stem,
        "timeout_s": getattr(warehouse, "job_timeout_seconds", None),
        "raised": type(raised).__name__ if raised else None,
        "outcome": outcome,
        "wall_ms": wall_ms,
        "state": record.get("state"),
        "error": record.get("error"),
        "billed": record.get("total_bytes_billed"),
        "slot_ms": record.get("slot_ms"),
    }
    expected = raised is None or isinstance(raised, QueryTimeoutError)
    run.check(f"timeout probe {stem}: QueryTimeoutError or completion, job DONE", expected and outcome in ("cancelled", "completed"), detail)
    return detail


def text(relative: str) -> str:
    return (SQL / relative).read_bytes().decode("utf-8")


def select_body(sql: str) -> str:
    """A repo SELECT file without its trailing comment lines and final ';'."""
    lines = sql.rstrip().splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("--")):
        lines.pop()
    return "\n".join(lines).rstrip().removesuffix(";")


def fingerprint(run: Run, step: str, table: str, ids: list[str] | None = None) -> tuple[int, int]:
    where = "WHERE t.work_id IN UNNEST(@ids)" if ids is not None else ""
    params = [bigquery.ArrayQueryParameter("ids", "STRING", ids)] if ids is not None else None
    row = run.rows(step, f"SELECT COUNT(*) AS n, BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) AS f FROM `openalex.{table}` AS t {where}", params)[0]
    return int(row["n"]), int(row["f"] or 0)


def decision_counts(run: Run, step: str) -> dict[str, int]:
    rows = run.rows(step, "SELECT publication_decision AS d, COUNT(*) AS n FROM `openalex.work_publication_decisions` GROUP BY d")
    return {row["d"]: int(row["n"]) for row in rows}


def measure_latency(run: SizedRun, cases: dict[str, tuple[str, list[Any]]], runs: int, estimates: dict[str, int] | None = None) -> dict[str, dict[str, Any]]:
    """Cache-off repeats of each query pattern: small-sample nearest-rank p50/p95.

    ``estimates`` (pattern -> its earlier dry-run bytes, which size each
    repeat's cap and timeout) saves a dry run per repeat.
    """
    latency = {}
    for stem, (sql, params) in cases.items():
        for i in range(runs):
            run.query(f"latency-{stem}-{i:02d}", sql, params=params, estimate=(estimates or {}).get(stem))
        # Sizing without a cap adds a "-est" dry-run record before each run; keep only the executed runs.
        steps = {f"latency-{stem}-{i:02d}" for i in range(runs)}
        records = [r for r in run.jobs if r["step"] in steps]
        elapsed = [r["elapsed_ms"] for r in records]
        wall = [r["wall_ms"] for r in records]
        latency[stem] = {
            "runs": runs,
            "any_cache_hit": any(r["cache_hit"] for r in records),
            "elapsed_ms_p50": nearest_rank(elapsed, 0.5),
            "elapsed_ms_p95": nearest_rank(elapsed, 0.95),
            "wall_ms_p50": nearest_rank(wall, 0.5),
            "wall_ms_p95": nearest_rank(wall, 0.95),
            "slot_ms_p50": nearest_rank([r["slot_ms"] or 0 for r in records], 0.5),
            "processed_bytes": records[0]["total_bytes_processed"],
            "billed_bytes_per_run": records[0]["total_bytes_billed"],
        }
    return latency


def publish(run: Run, batch: str, expected: dict[str, Any]) -> None:
    run.query(f"{batch}-decisions", text("models/work_publication_decisions.sql"))
    got = decision_counts(run, f"{batch}-decision-counts")
    want = {k: v for k, v in expected["decision_counts"].items() if v}
    run.check(f"{batch} decision counts", got == want, f"got {got} want {want}")
    run.query(f"{batch}-accepted", text("models/accepted_work_ids.sql"))
    run.query(f"{batch}-relationship-ids", text("models/relationship_publish_work_ids.sql"))
    for name in ("merge_works_preconditions", "classify_works_staging"):
        run.query(f"{batch}-{name}", text(f"models/{name}.sql"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scale", default="default", choices=sorted(SYNTHETIC_SCALES), help="synthetic preset ('large' takes roughly a day to generate)")
    parser.add_argument("--budget-gib", type=float, default=20.0)
    parser.add_argument("--max-job-mib", type=int, default=512, help="ceiling on any one job's maximum_bytes_billed")
    parser.add_argument("--job-timeout-s", type=float, default=600.0, help="base result timeout per query, adapter and load job")
    parser.add_argument("--timeout-s-per-gib", type=float, default=60.0, help="added to --job-timeout-s per GiB of a job's dry-run estimate")
    parser.add_argument("--latency-runs", type=int, default=21, help="cache-off repeats per query pattern (0 skips)")
    parser.add_argument("--cluster-settle-s", type=float, default=0.0, help="wait after the raw query runs, before the latency repeats that re-measure clustering")
    parser.add_argument("--skip-timeout-probe", action="store_true", help=f"skip the BigQueryWarehouse {TIMEOUT_PROBE_S}s timeout/cancellation probe")
    parser.add_argument("--pricing-model", default="unknown", choices=("on-demand", "editions", "unknown"), help="recorded as evidence only")
    args = parser.parse_args(argv)
    if args.max_job_mib <= 0 or args.job_timeout_s <= 0:
        parser.error("--max-job-mib and --job-timeout-s must be positive")
    if args.timeout_s_per_gib < 0 or args.cluster_settle_s < 0:
        parser.error("--timeout-s-per-gib and --cluster-settle-s must not be negative")
    ceiling = args.max_job_mib * MIB

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project or project != args.confirm_project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set and equal --confirm-project")
    out = evidence_dir(args.out, REPO)
    out.mkdir(parents=True, exist_ok=True)

    client = bigquery.Client(project=project)
    dataset = client.get_dataset(f"{project}.{DATASET}")
    if list(client.list_tables(dataset)):
        raise SystemExit("dataset openalex must be empty; refusing to deploy")
    if dataset.default_partition_expiration_ms:
        raise SystemExit("dataset has a default partition expiration; old partitions would vanish")
    log = out / "jobs.jsonl"
    log.write_text("")
    run = SizedRun(
        client,
        "i103-" + datetime.now(UTC).strftime("%Y%m%dt%H%M%S"),
        int(args.budget_gib * 1024 * MIB),
        log,
        job_timeout_s=args.job_timeout_s,
        timeout_s_per_gib=args.timeout_s_per_gib,
        max_job_bytes=ceiling,
    )
    principal = adc_principal(client._credentials)
    print(f"run {run.run_id}: location {dataset.location}, table expiration ms {dataset.default_table_expiration_ms}, ADC {principal['type']}")

    # Generate the synthetic cycle offline before deploying, so a slow or failed
    # generation (hours at 'large') leaves the dataset empty.
    print(f"generating the {args.scale} synthetic cycle", flush=True)
    cycle = out / "cycle"
    write_synthetic_cycle(cycle, scale=args.scale)
    expected = json.loads((cycle / "expected_decisions.json").read_text())
    pub1, pub2 = expected["publication"]["batch1"], expected["publication"]["batch2"]

    # Deploy: 15 contract tables, the active_works view, 2 staging tables.
    for path in sorted((SQL / "ddl").glob("*.sql")):
        job = run.query(f"ddl-{path.stem}", path.read_bytes().decode("utf-8"))
        run.check(f"ddl {path.stem} CREATE", job.ddl_operation_performed == "CREATE", job.ddl_operation_performed)
    run.query("view-active_works", text("models/active_works.sql"))
    for path in sorted((SQL / "staging").glob("*.sql")):
        job = run.query(f"staging-{path.stem}", path.read_bytes().decode("utf-8"))
        run.check(f"staging {path.stem} CREATE", job.ddl_operation_performed == "CREATE", job.ddl_operation_performed)
    for contract in build_table_contracts():
        table = client.get_table(f"{project}.{DATASET}.{contract.table_name}")
        columns = [(f.name, LEGACY_TYPES.get(f.field_type, f.field_type), f.mode != "REQUIRED") for f in table.schema]
        want_cols = [(c.name, str(c.bq_type), c.nullable) for c in contract.columns]
        part = table.range_partitioning.field if table.range_partitioning else (table.time_partitioning.field if table.time_partitioning else None)
        ok = columns == want_cols and part == contract.partitioning.field and tuple(table.clustering_fields or ()) == contract.clustering.fields
        run.check(f"schema/partition/cluster {contract.table_name}", ok, "" if ok else {"columns": columns != want_cols, "partition": part, "cluster": table.clustering_fields})

    # Data: the synthetic cycle, generated through the real Step 12/13 ingest path.
    for path in sorted((cycle / "batch1").glob("*.parquet")):
        target = {"works": "works_staging", "work_topics": "work_topics_staging"}.get(path.stem, path.stem)
        run.load(target, path)

    # Batch 1: everything is INSERT; deleted works arrive as tombstones.
    publish(run, "b1", pub1)
    merge = run.query("b1-merge_works", text("models/merge_works.sql"))
    run.check("b1 merge_works dml", merge.dml_stats.inserted_row_count == pub1["merge_works"]["inserted"], merge.dml_stats._asdict() if merge.dml_stats else None)
    topics = run.query("b1-merge_work_topics", text("models/merge_work_topics.sql"))
    run.check("b1 merge_work_topics dml", run.script_dml(topics).get("inserted") == pub1["merge_work_topics"]["inserted"], run.script_dml(topics))
    totals = run.rows("b1-works-totals", "SELECT COUNT(*) AS n, COUNTIF(activity_state = 'DELETED') AS deleted FROM `openalex.works`")[0]
    run.check("b1 works rows/deleted", (totals["n"], totals["deleted"]) == (pub1["works_rows_after"], pub1["works_deleted_after"]), totals)
    for name in ("canonical_work_id_not_null", "canonical_work_id_unique"):
        value = list(run.rows(f"b1-quality-{name}", text(f"quality/{name}.sql"))[0].values())[0]
        run.check(f"b1 quality {name} == 0", value == 0, value)

    # Batch 2: composed delta covering every decision outcome.
    run.query("b2-truncate", "TRUNCATE TABLE `openalex.works_staging`; TRUNCATE TABLE `openalex.work_topics_staging`;")
    run.load("works_staging", cycle / "batch2" / "works_staging.parquet")
    run.load("work_topics_staging", cycle / "batch2" / "work_topics_staging.parquet")
    by_decision: dict[str, list[str]] = {}
    for item in expected["batch2_decisions"]:
        by_decision.setdefault(item["publication_decision"], []).append(item["work_id"])
    frozen = sorted(by_decision.get("STALE", []) + by_decision.get("CONFLICT", []) + by_decision.get("RESTORE_REQUIRED", []))
    frozen_before = fingerprint(run, "b2-frozen-before", "works", frozen)
    publish(run, "b2", pub2)

    # Injection 2: a cap below the per-table billing floor must reject the job unchanged.
    decisions_before = fingerprint(run, "cap-floor-before", "work_publication_decisions")
    rejected = run.query("cap-floor", text("models/work_publication_decisions.sql"), cap=1 * MIB, expect_error=True)
    run.check("cap below floor rejected", (rejected.error_result or {}).get("reason") == "bytesBilledLimitExceeded" and int(rejected.total_bytes_billed or 0) == 0, (rejected.error_result or {}).get("reason"))
    run.check("cap-floor left decisions unchanged", fingerprint(run, "cap-floor-after", "work_publication_decisions") == decisions_before)

    merge = run.query("b2-merge_works", text("models/merge_works.sql"))
    stats = merge.dml_stats
    run.check("b2 merge_works dml", (stats.inserted_row_count, stats.updated_row_count) == (pub2["merge_works"]["inserted"], pub2["merge_works"]["updated"]), stats._asdict())
    run.check("b2 STALE/CONFLICT/RESTORE rows unchanged", fingerprint(run, "b2-frozen-after", "works", frozen) == frozen_before, f"{len(frozen)} rows")

    # Injection 1: a NULL topic_id must roll back the whole REPLACE transaction.
    topics_before = fingerprint(run, "inject-before", "work_topics")
    run.query("inject-truncate", "TRUNCATE TABLE `openalex.work_topics_staging`")
    run.load("work_topics_staging", cycle / expected["injection"]["path"])
    failed = run.query("inject-merge_work_topics", text("models/merge_work_topics.sql"), expect_error=True)
    steps = [(child.statement_type, (child.error_result or {}).get("reason")) for child in run.children(failed)]
    run.check(
        "NULL topic_id merge fails and rolls back",
        failed.error_result is not None and "ROLLBACK_TRANSACTION" in [s for s, _ in steps],
        {"reason": (failed.error_result or {}).get("reason"), "children": steps, "rolled_back_dml": run.script_dml(failed)},
    )
    run.check("failed merge left work_topics unchanged", fingerprint(run, "inject-after", "work_topics") == topics_before)
    run.query("b2-restage-topics", "TRUNCATE TABLE `openalex.work_topics_staging`")
    run.load("work_topics_staging", cycle / "batch2" / "work_topics_staging.parquet")
    topics = run.query("b2-merge_work_topics", text("models/merge_work_topics.sql"))
    dml = run.script_dml(topics)
    run.check("b2 merge_work_topics dml", (dml.get("inserted"), dml.get("deleted")) == (pub2["merge_work_topics"]["inserted"], pub2["merge_work_topics"]["deleted"]), dml)
    totals = run.rows("b2-totals", "SELECT (SELECT COUNT(*) FROM `openalex.works`) AS n, (SELECT COUNTIF(activity_state = 'DELETED') FROM `openalex.works`) AS deleted, (SELECT COUNT(*) FROM `openalex.work_topics`) AS topics")[0]
    want = (pub2["works_rows_after"], pub2["works_deleted_after"], pub2["work_topics_rows_after"])
    run.check("b2 works/deleted/work_topics rows", (totals["n"], totals["deleted"], totals["topics"]) == want, totals)

    # Gold audit tables, then every Gold, quality and query file once (dry run first).
    for path in sorted((SQL / "gold_audit").glob("*.sql")):
        run.query(f"gold_audit-{path.stem}", path.read_bytes().decode("utf-8"))
    for folder in ("gold", "quality"):
        for path in sorted((SQL / folder).glob("*.sql")):
            sql = path.read_bytes().decode("utf-8")
            estimate = int(run.query(f"{folder}-{path.stem}-dry", sql, dry_run=True).total_bytes_processed or 0)
            result = run.rows(f"{folder}-{path.stem}", sql, estimate=estimate)
            if folder == "quality":
                run.checks.append({"check": f"quality {path.stem}", "ok": True, "detail": result[:3]})
    not_run = [c.check_id for c in load_quality_registry() if not c.sql_path]
    print(f"NOT_RUN quality checks without BigQuery SQL: {not_run}")
    for mart, column in (("research_discovery", "work_id"), ("citation_edges", "source_work_id")):
        body = select_body(text(f"gold/{mart}.sql"))
        leaked = run.rows(f"exclusion-{mart}", f"SELECT COUNT(*) AS n FROM ({body}) AS g JOIN `openalex.works` AS w ON g.{column} = w.work_id WHERE w.activity_state = 'DELETED'")[0]["n"]
        run.check(f"gold {mart} excludes DELETED works", leaked == 0, leaked)

    pick = run.rows("pick-params", """
        SELECT
          (SELECT w.work_id FROM `openalex.works` w JOIN `openalex.work_references` r USING (work_id)
             WHERE w.activity_state = 'ACTIVE' ORDER BY w.work_id LIMIT 1) AS work_id,
          (SELECT doi FROM `openalex.works` WHERE activity_state = 'ACTIVE' AND doi IS NOT NULL ORDER BY work_id LIMIT 1) AS doi,
          (SELECT primary_publisher_id FROM `openalex.works` WHERE primary_publisher_id IS NOT NULL
             GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1) AS publisher_id,
          (SELECT primary_source_id FROM `openalex.works` WHERE primary_source_id IS NOT NULL
             GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1) AS source_id,
          (SELECT institution_id FROM `openalex.work_author_institutions` WHERE institution_id IS NOT NULL
             GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1) AS institution_id,
          (SELECT MIN(publication_year) FROM `openalex.works`) AS year_min,
          (SELECT MAX(publication_year) FROM `openalex.works`) AS year_max
    """)[0]
    year_from = int(pick["year_max"]) - 1
    values = {**{k: pick[k] for k in ("work_id", "doi", "publisher_id", "source_id", "institution_id")}, "year_from": year_from, "year_to": int(pick["year_max"])}
    adapter_client = AdapterClient(run)
    cases: dict[str, tuple[str, list[Any]]] = {}
    estimates: dict[str, int] = {}
    adapter_caps: dict[str, int] = {}
    measured: dict[str, tuple[int, int, str]] = {}  # pattern -> (dry-run estimate, executed processed bytes, measuring step)
    for path in sorted((SQL / "queries").glob("*.sql")):
        sql = path.read_bytes().decode("utf-8")
        names = sorted(set(re.findall(r"@([a-z_]+)", sql)))
        params = [bigquery.ScalarQueryParameter(n, "INT64" if n.startswith("year_") else "STRING", values[n]) for n in names]
        cases[path.stem] = (sql, params)
        estimate = int(run.query(f"query-{path.stem}-dry", sql, params=params, dry_run=True).total_bytes_processed or 0)
        estimates[path.stem] = estimate
        raw =run.query(f"query-{path.stem}", sql, params=params, estimate=estimate)
        result = [dict(row) for row in raw.result()]
        measured[path.stem] = (estimate, int(raw.total_bytes_processed or 0), f"query-{path.stem}")
        adapter_caps[path.stem] = adapter_cap(int(raw.total_bytes_billed or 0), estimate, ceiling)
        warehouse = adapter_warehouse(project, adapter_client, adapter_caps[path.stem], run.sized_timeout(estimate))
        adapter = warehouse_query(run, adapter_client, warehouse, f"adapter-{path.stem}", sql, {n: values[n] for n in names})
        run.check(f"query {path.stem} runs (raw + adapter)", adapter.num_rows == len(result), f"{len(result)} rows")
        if path.stem in YEAR_BOUNDED:
            variants = {
                "bounded": params,
                "typed_null": [bigquery.ScalarQueryParameter(p.name, p.type_, None) if p.name.startswith("year_") else p for p in params],
            }
            literal = sql.replace("@year_from", str(values["year_from"])).replace("@year_to", str(values["year_to"]))
            pruning = {name: run.query(f"prune-{path.stem}-{name}", sql, params=p, dry_run=True).total_bytes_processed for name, p in variants.items()}
            pruning["literal"] = run.query(f"prune-{path.stem}-literal", literal, params=[p for p in params if not p.name.startswith("year_")], dry_run=True).total_bytes_processed
            run.checks.append({"check": f"pruning {path.stem}", "ok": True, "detail": pruning})

    if args.cluster_settle_s:
        print(f"waiting {args.cluster_settle_s}s for re-clustering before the latency repeats", flush=True)
        run.sleep(args.cluster_settle_s)
    latency = measure_latency(run, cases, args.latency_runs, estimates) if args.latency_runs else {}
    measured = remeasure(run, measured, args.latency_runs)
    probe = None
    if not args.skip_timeout_probe:
        sql, params = cases[TIMEOUT_PROBE_PATTERN]
        warehouse = adapter_warehouse(project, adapter_client, adapter_caps[TIMEOUT_PROBE_PATTERN], TIMEOUT_PROBE_S)
        probe = timeout_probe(run, adapter_client, warehouse, TIMEOUT_PROBE_PATTERN, sql, {p.name: p.value for p in params})
    # ROWS is a reserved keyword in BigQuery, so it cannot be a bare alias.
    partitions = run.rows("partitions", """
        SELECT table_name, COUNT(*) AS partition_count, SUM(total_rows) AS row_count,
               SUM(total_logical_bytes) AS total_logical_bytes,
               MIN(total_logical_bytes) AS min_partition_bytes,
               APPROX_QUANTILES(total_logical_bytes, 2)[OFFSET(1)] AS median_partition_bytes,
               MAX(total_logical_bytes) AS max_partition_bytes
        FROM `openalex.INFORMATION_SCHEMA.PARTITIONS` GROUP BY table_name ORDER BY table_name
    """)
    clustering = clustering_summary(clustering_patterns(cases, measured, partitions), partitions)

    failed_checks = [c for c in run.checks if not c["ok"]]
    summary = {
        "run_id": run.run_id,
        "scale": expected["dataset_scale"],
        "scale_preset": args.scale,
        "adc_principal": principal,
        "user_emails": sorted({j["user_email"] for j in run.jobs if j.get("user_email")}),
        "pricing_model": args.pricing_model,
        "caps": {
            "job": "min(max_job_bytes, max(cap_for(sql), 2 x dry-run estimate rounded up to MiB + 10 MiB per table))",
            "adapter": "min(max_job_bytes, max(64 MiB, 2 x raw-run billed bytes, dry-run estimate))",
            "refused": "any raw or BigQueryWarehouse job whose cap exceeds the remaining budget, before submission",
            "max_job_bytes": ceiling,
            "timeout": "job_timeout_s + timeout_s_per_gib x dry-run estimate in GiB (adapter calls: their pattern's); job_timeout_s for loads and jobs without an estimate",
            "job_timeout_s": args.job_timeout_s,
            "timeout_s_per_gib": args.timeout_s_per_gib,
        },
        "location": dataset.location,
        "jobs": len(run.jobs),
        "billed_bytes": run.billed,
        "checks": run.checks,
        "failed": len(failed_checks),
        "not_run": not_run,
        "params": {k: (v if k.startswith("year_") else "<chosen from data>") for k, v in values.items()},
        "latency": latency,
        "clustering": {
            "method": (
                "per pattern: executed total_bytes_processed of its last latency repeat (the raw run without repeats; processed_from) "
                "vs its dry-run estimate (dry runs apply no block pruning); status from the pattern's own tables; cluster_filters is a text match"
            ),
            "settle_s": args.cluster_settle_s,
            **clustering,
        },
        "timeout_probe": probe,
        "partitions": partitions,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"\n{len(run.checks) - len(failed_checks)}/{len(run.checks)} checks passed; {len(run.jobs)} jobs; {run.billed} bytes billed")
    return 1 if failed_checks else 0


if __name__ == "__main__":
    sys.exit(main())
