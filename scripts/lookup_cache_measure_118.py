"""Manual #118 measurement: BigQuery + an in-process cache for Work point lookups.

Not part of ``make test`` or CI. It runs against a deployed ``openalex``
dataset (for example after a #103 run) as the operator's chosen identity::

    GOOGLE_CLOUD_PROJECT=<project> .venv/bin/python scripts/lookup_cache_measure_118.py \\
        --confirm-project <project> --out ~/i118 [--pattern doi|work-id] \\
        [--distinct-keys 1000] [--requests 2000] [--warmup 500] [--zipf-s 1.1]

**Plan first.** Every measurement parameter (access distribution, key count,
request counts, TTL, seed, target) is written to ``plan.json`` before the first
lookup, so no number is chosen after the fact (#118 criterion 1).

**Method.**
- **Keys:** ``--distinct-keys`` ACTIVE works, chosen deterministically by
  ``FARM_FINGERPRINT(work_id)``.
- **Requests:** a seeded Zipf(``--zipf-s``) sequence over those keys. The first
  ``--warmup`` requests fill the cache and are excluded from the results.
- **Each request** goes through ``TtlLruCache.get_or_load``. A miss runs the
  repo lookup SQL byte-for-byte through an unmodified ``BigQueryWarehouse``,
  which does a dry run and then executes. That is the full path an API would
  take on a miss.
- **Reported:** hit, miss and overall p50/p95 for the measured requests, the
  hit rate, and a **meets / misses** verdict against ``--target-p95-ms``.

**What it does not measure.** The cache is in-process, so a hit costs
microseconds. A shared cache such as Memorystore would add a network hop that
this script does not measure; the evidence says so.

Every BigQuery job is labelled ``run=<run id>``, capped, recorded in
``jobs.jsonl`` and budgeted against ``--budget-gib``. ``--out`` must be
outside the repository.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SQL = REPO / "sql" / "bigquery" / "openalex" / "queries"
PATTERNS = {
    "doi": ("doi_work_lookup", "doi"),
    "work-id": ("openalex_work_id_lookup", "work_id"),
}
SAMPLE_SQL = (
    "SELECT work_id, doi FROM `openalex.works` WHERE activity_state = 'ACTIVE' AND {column} IS NOT NULL "
    "ORDER BY FARM_FINGERPRINT(work_id) LIMIT @n"
)


def zipf_sequence(n_keys: int, n_requests: int, s: float, seed: int) -> list[int]:
    """``n_requests`` key indexes drawn from a Zipf(s) distribution over ``n_keys`` ranks, reproducibly."""
    if n_keys <= 0 or n_requests <= 0:
        raise ValueError("n_keys and n_requests must be positive")
    weights = [1.0 / (rank**s) for rank in range(1, n_keys + 1)]
    return random.Random(seed).choices(range(n_keys), weights=weights, k=n_requests)


def nearest_rank(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def measure(get: Any, keys: list[str], sequence: list[int], warmup: int, clock: Any = time.perf_counter) -> list[dict[str, Any]]:
    """Run ``sequence`` through ``get(key) -> (value, hit)``; records after ``warmup`` are kept."""
    records = []
    for i, index in enumerate(sequence):
        started = clock()
        _, hit = get(keys[index])
        elapsed_ms = (clock() - started) * 1000
        if i >= warmup:
            records.append({"i": i, "key_index": index, "hit": hit, "ms": round(elapsed_ms, 3)})
    return records


def summarize(records: list[dict[str, Any]], target_ms: float) -> dict[str, Any]:
    """Hit, miss and overall percentiles, plus the verdict against ``target_ms`` p95."""
    hits = [r["ms"] for r in records if r["hit"]]
    misses = [r["ms"] for r in records if not r["hit"]]
    overall = [r["ms"] for r in records]
    p95 = nearest_rank(overall, 0.95)
    miss_share = len(misses) / len(records) if records else None
    return {
        "measured_requests": len(records),
        "hit_rate": None if miss_share is None else round(1 - miss_share, 4),
        "hit_ms_p50": nearest_rank(hits, 0.5),
        "hit_ms_p95": nearest_rank(hits, 0.95),
        "miss_ms_p50": nearest_rank(misses, 0.5),
        "miss_ms_p95": nearest_rank(misses, 0.95),
        "overall_ms_p50": nearest_rank(overall, 0.5),
        "overall_ms_p95": p95,
        "target_p95_ms": target_ms,
        # With misses slower than the target, overall p95 can only meet it when misses are under 5% of requests.
        "verdict": None if p95 is None else ("MEETS" if p95 <= target_ms else "MISSES"),
    }


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pattern", choices=sorted(PATTERNS), default="doi")
    parser.add_argument("--distinct-keys", type=int, default=1000)
    parser.add_argument("--requests", type=int, default=2000, help="measured requests, after warm-up")
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--zipf-s", type=float, default=1.1)
    parser.add_argument("--seed", type=int, default=118)
    parser.add_argument("--ttl-s", type=float, default=24 * 60 * 60)
    parser.add_argument("--target-p95-ms", type=float, default=200.0)
    parser.add_argument("--budget-gib", type=float, default=20.0)
    parser.add_argument("--max-job-mib", type=int, default=64, help="maximum_bytes_billed per lookup job")
    parser.add_argument("--run-id", default="i118-" + datetime.now(UTC).strftime("%Y%m%dt%H%M%S"))
    args = parser.parse_args(argv)
    for name in ("distinct_keys", "requests", "max_job_mib"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.warmup < 0 or args.zipf_s <= 0 or args.ttl_s <= 0 or args.target_p95_ms <= 0 or args.budget_gib <= 0:
        parser.error("--warmup must be >= 0; --zipf-s, --ttl-s, --target-p95-ms and --budget-gib positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project or project != args.confirm_project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set and equal --confirm-project")

    from research_platform.analytics.bigquery.sandbox.live import (
        DATASET,
        MIB,
        AdapterClient,
        Run,
        adc_principal,
        evidence_dir,
    )

    out = evidence_dir(args.out, REPO)
    out.mkdir(parents=True, exist_ok=True)
    stem, param = PATTERNS[args.pattern]
    plan = {
        "issue": 118,
        "run_id": args.run_id,
        "pattern": stem,
        "distribution": f"Zipf(s={args.zipf_s}) over {args.distinct_keys} ACTIVE works chosen by FARM_FINGERPRINT(work_id)",
        "seed": args.seed,
        "warmup_requests": args.warmup,
        "measured_requests": args.requests,
        "cache": {"kind": "in-process TtlLruCache", "ttl_s": args.ttl_s, "max_entries": args.distinct_keys, "caches_not_found": True},
        "miss_path": "BigQueryWarehouse.query (dry run + execute) of the repo lookup SQL",
        "target_p95_ms": args.target_p95_ms,
        "budget_gib": args.budget_gib,
        "max_job_mib": args.max_job_mib,
        "planned_at": datetime.now(UTC).isoformat(),
    }
    plan_path = out / "plan.json"
    if plan_path.exists():
        raise SystemExit(f"{plan_path} exists; use a fresh --out so the plan predates the measurement")
    plan_path.write_text(json.dumps(plan, indent=1))
    print(f"plan written: {plan_path}", flush=True)

    from google.cloud import bigquery

    from research_platform.config.models import CloudConfig, WarehouseConfig
    from research_platform.serving.lookup_cache import TtlLruCache
    from research_platform.warehouse.bigquery import BigQueryWarehouse

    client = bigquery.Client(project=project)
    log = out / "jobs.jsonl"
    log.write_text("")
    run = Run(client, args.run_id, int(args.budget_gib * 1024 * MIB), log)
    cap = args.max_job_mib * MIB
    column = "doi" if param == "doi" else "work_id"
    rows = run.rows("sample-keys", SAMPLE_SQL.format(column=column), [bigquery.ScalarQueryParameter("n", "INT64", args.distinct_keys)])
    keys = [str(r[column]) for r in rows]
    if len(keys) < args.distinct_keys:
        raise SystemExit(f"only {len(keys)} ACTIVE works with a {column}; lower --distinct-keys")

    adapter = AdapterClient(run)
    warehouse = BigQueryWarehouse(WarehouseConfig(analytical="bigquery", bigquery_dataset=DATASET), CloudConfig(project_id=project), client=adapter, maximum_bytes_billed=cap)
    sql = (SQL / f"{stem}.sql").read_bytes().decode("utf-8")
    cache: TtlLruCache[Any] = TtlLruCache(ttl_seconds=args.ttl_s, max_entries=args.distinct_keys)

    def load(key: str) -> Any:
        run.ensure_fits("lookup-miss", cap)
        with adapter.recording("lookup-miss"):
            table = warehouse.query(sql, {param: key})
        return table.to_pylist()[0] if table.num_rows else None

    prefix = "work:doi:" if param == "doi" else "work:id:"
    sequence = zipf_sequence(args.distinct_keys, args.warmup + args.requests, args.zipf_s, args.seed)
    records = measure(lambda key: cache.get_or_load(prefix + key, lambda: load(key)), keys, sequence, args.warmup)
    with (out / "requests.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    summary = {
        "plan": plan,
        **summarize(records, args.target_p95_ms),
        "cache_stats": vars(cache.stats),
        "jobs": len(run.jobs),
        "billed_bytes": run.billed,
        "user_emails": sorted({j["user_email"] for j in run.jobs if j.get("user_email")}),
        "adc_principal": adc_principal(client._credentials),
        "not_measured": "a shared cache's network hop (in-process cache only)",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(
        f"{summary['verdict']}: overall p95 {summary['overall_ms_p95']} ms vs {args.target_p95_ms} ms; "
        f"hit rate {summary['hit_rate']}; hit p95 {summary['hit_ms_p95']} ms, miss p95 {summary['miss_ms_p95']} ms; "
        f"{run.billed} bytes billed",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
