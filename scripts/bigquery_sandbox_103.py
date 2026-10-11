"""Manual #103 sandbox run: deploy, load, publish and measure the Step 15-16 BigQuery SQL.

Not part of ``make test`` or CI. Run once by an operator against an empty,
operator-created dataset literally named ``openalex`` (the repo SQL hard-codes
that name), ideally created with a default table expiration::

    bq mk --dataset --location=US --default_table_expiration=1209600 <project>:openalex

Then (WSL, user ADC)::

    GOOGLE_CLOUD_PROJECT=<project> .venv/bin/python scripts/bigquery_sandbox_103.py \\
        --confirm-project <project> --out /tmp/i103 [--scale tiny|default]

Every job is labelled ``run=<run id>`` and capped with ``maximum_bytes_billed``;
the run aborts once cumulative billed bytes pass ``--budget-gib``. Repo SQL is
submitted byte-for-byte. Evidence (``jobs.jsonl``, ``summary.json``) goes to
``--out``, which must be outside the repository. The script never creates or
deletes datasets; the operator removes the dataset afterwards (or lets the
table expiration do it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from google.cloud import bigquery

from research_platform.analytics.bigquery.contracts import build_table_contracts
from research_platform.analytics.bigquery.sandbox.synthetic import write_synthetic_cycle
from research_platform.config.models import CloudConfig, WarehouseConfig
from research_platform.warehouse.bigquery import BigQueryWarehouse

REPO = Path(__file__).resolve().parents[1]
SQL = REPO / "sql" / "bigquery" / "openalex"
DATASET = "openalex"
MIB = 1024 * 1024
BILLING_FLOOR = 10 * MIB
TABLE_REF = re.compile(r"`openalex\.([a-z_]+)`")
LEGACY_TYPES = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL"}
YEAR_BOUNDED = ("publication_trends", "open_access_trends", "publisher_topic_license_year")


class Run:
    def __init__(self, client: bigquery.Client, run_id: str, budget_bytes: int) -> None:
        self.client = client
        self.run_id = run_id
        self.budget_bytes = budget_bytes
        self.billed = 0
        self.jobs: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

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
        job = self.client.query(sql, job_config=config)
        error = None
        if not dry_run:
            try:
                job.result(timeout=600)
            except Exception as exc:  # noqa: BLE001 - recorded, re-raised unless expected
                error = (job.error_result or {}).get("reason") or type(exc).__name__
                if not expect_error:
                    self._record(step, job, cap, dry_run, error)
                    raise
        self._record(step, job, cap, dry_run, error)
        return job

    def _record(self, step: str, job: bigquery.QueryJob, cap: int, dry_run: bool, error: str | None) -> None:
        billed = 0 if dry_run else int(job.total_bytes_billed or 0)
        self.billed += billed
        dml = job.dml_stats
        self.jobs.append(
            {
                "step": step,
                "job_id": job.job_id,
                "dry_run": dry_run,
                "statement_type": job.statement_type,
                "cap": cap,
                "total_bytes_processed": job.total_bytes_processed,
                "total_bytes_billed": billed,
                "floor_artifact": billed > 0 and billed % BILLING_FLOOR == 0,
                "slot_ms": None if dry_run else job.slot_millis,
                "cache_hit": None if dry_run else job.cache_hit,
                "ddl_operation_performed": job.ddl_operation_performed,
                "dml_stats": None if dml is None else {
                    "inserted": dml.inserted_row_count,
                    "updated": dml.updated_row_count,
                    "deleted": dml.deleted_row_count,
                },
                "error": error,
                "started": None if dry_run or job.started is None else job.started.isoformat(),
                "ended": None if dry_run or job.ended is None else job.ended.isoformat(),
            }
        )

    def rows(self, step: str, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self.query(step, sql, params=params).result()]

    def script_dml(self, job: bigquery.QueryJob) -> dict[str, int]:
        totals = Counter()
        for child in self.client.list_jobs(parent_job=job.job_id):
            stats = getattr(child, "dml_stats", None)
            if stats is not None:
                totals["inserted"] += stats.inserted_row_count
                totals["updated"] += stats.updated_row_count
                totals["deleted"] += stats.deleted_row_count
        return dict(totals)

    def load(self, table: str, path: Path) -> int:
        config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            labels={"run": self.run_id, "step": label(f"load-{table}")},
        )
        with path.open("rb") as handle:
            job = self.client.load_table_from_file(handle, f"{self.client.project}.{DATASET}.{table}", job_config=config)
        job.result(timeout=600)
        expected = pq.ParquetFile(path).metadata.num_rows
        self.check(f"load {table}", job.output_rows == expected, f"{job.output_rows}/{expected} rows")
        return int(job.output_rows or 0)


def label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", value.lower())[:63]


def cap_for(sql: str) -> int:
    tables = len(set(TABLE_REF.findall(sql))) or 1
    return min(512 * MIB, max(64 * MIB, 2 * BILLING_FLOOR * tables))


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
    parser.add_argument("--scale", default="default", choices=("tiny", "default"))
    parser.add_argument("--budget-gib", type=float, default=20.0)
    args = parser.parse_args(argv)

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project or project != args.confirm_project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set and equal --confirm-project")
    out = args.out.resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit("--out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)

    client = bigquery.Client(project=project)
    dataset = client.get_dataset(f"{project}.{DATASET}")
    if list(client.list_tables(dataset)):
        raise SystemExit("dataset openalex must be empty; refusing to deploy")
    if dataset.default_partition_expiration_ms:
        raise SystemExit("dataset has a default partition expiration; old partitions would vanish")
    run = Run(client, "i103-" + datetime.now(UTC).strftime("%Y%m%dt%H%M%S"), int(args.budget_gib * 1024 * MIB))
    print(f"run {run.run_id}: location {dataset.location}, table expiration ms {dataset.default_table_expiration_ms}")

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

    # Data: synthetic cycle through the real Step 12/13 ingest path.
    cycle = out / "cycle"
    write_synthetic_cycle(cycle, scale=args.scale)
    expected = json.loads((cycle / "expected_decisions.json").read_text())
    pub1, pub2 = expected["publication"]["batch1"], expected["publication"]["batch2"]
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
    run.check("NULL topic_id merge fails", failed.error_result is not None, (failed.error_result or {}).get("reason"))
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
            run.query(f"{folder}-{path.stem}-dry", sql, dry_run=True)
            result = run.rows(f"{folder}-{path.stem}", sql)
            if folder == "quality":
                run.checks.append({"check": f"quality {path.stem}", "ok": True, "detail": result[:3]})
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
    warehouse = BigQueryWarehouse(WarehouseConfig(analytical="bigquery", bigquery_dataset=DATASET), CloudConfig(project_id=project), client=client, maximum_bytes_billed=64 * MIB)
    for path in sorted((SQL / "queries").glob("*.sql")):
        sql = path.read_bytes().decode("utf-8")
        names = sorted(set(re.findall(r"@([a-z_]+)", sql)))
        params = [bigquery.ScalarQueryParameter(n, "INT64" if n.startswith("year_") else "STRING", values[n]) for n in names]
        run.query(f"query-{path.stem}-dry", sql, params=params, dry_run=True)
        result = run.rows(f"query-{path.stem}", sql, params)
        adapter = warehouse.query(sql, {n: values[n] for n in names})
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

    partitions = run.rows("partitions", "SELECT table_name, COUNT(*) AS partitions, SUM(total_rows) AS rows FROM `openalex.INFORMATION_SCHEMA.PARTITIONS` GROUP BY table_name ORDER BY table_name")

    failed_checks = [c for c in run.checks if not c["ok"]]
    summary = {
        "run_id": run.run_id,
        "scale": expected["dataset_scale"],
        "location": dataset.location,
        "jobs": len(run.jobs),
        "billed_bytes": run.billed,
        "checks": run.checks,
        "failed": len(failed_checks),
        "params": {k: (v if k.startswith("year_") else "<chosen from data>") for k, v in values.items()},
        "partitions": partitions,
    }
    (out / "jobs.jsonl").write_text("".join(json.dumps(j, default=str) + "\n" for j in run.jobs))
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"\n{len(run.checks) - len(failed_checks)}/{len(run.checks)} checks passed; {len(run.jobs)} jobs; {run.billed} bytes billed")
    return 1 if failed_checks else 0


if __name__ == "__main__":
    sys.exit(main())
