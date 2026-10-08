# Orchestration contracts (Step 20 / #25)

**Status:** ACTIVE / in review — contracts and deterministic local validation only.  
Package: `research_platform.orchestration`  
Issue: [#25](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/25)  
Umbrella: [#2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2) (remains OPEN)

### Explicit non-claims

This step does **NOT**:

- deploy Apache Airflow or Cloud Composer
- provision Dataform, BigQuery, GCS, Terraform, or Kubernetes
- add `apache-airflow` as a runtime dependency
- create production schedules or service accounts
- replace Step 19 `run_bounded_e2e_pipeline()` (local `SEMANTIC_ONLY` proof)

---

## Step 19 vs Step 20

| | Step 19 / #27 | Step 20 / #25 |
|---|---|---|
| Role | Local bounded composition proof | Scheduler/orchestrator **contract** + deployment alignment |
| Evidence | `SEMANTIC_ONLY` executable path | `CONTRACT_ONLY` graph/messages/retries/scope |
| Runtime | DuckDB + in-memory publication | None (dry-run validator only) |

---

## Required DAG (thin)

Business logic stays in Steps 12–19 public APIs. Task specs hold identity,
dependencies, retry policy, and a `capability_ref` string only.

```text
DISCOVER
  -> REGISTER
  -> INGEST
  -> CANONICALIZE
  -> APPLY_DELETIONS
  -> ANALYTICAL_PUBLICATION
  -> PRE_SERVING_QUALITY          (PRE_SERVING_BUILD)
  -> STAGE_GOLD
  -> PRE_VISIBLE_QUALITY          (PRE_VISIBLE_PUBLICATION)
  -> PUBLISH_SUCCESS
  -> FINAL_VALIDATION             (Step 19 alignment)
```

Rules:

- No path reaches `PUBLISH_SUCCESS` without `PRE_VISIBLE_QUALITY` success.
- No Gold staging before `PRE_SERVING_QUALITY` success.
- No direct `INGEST -> PUBLISH_SUCCESS` shortcut.
- Graph is acyclic with deterministic topological order (`TASK_INVENTORY`).

Capability refs (examples):

| Task | Reuses |
|---|---|
| INGEST | `ingest_works_asset(...)` |
| APPLY_DELETIONS | `ingest_deletion_asset(...)` |
| PRE_*_QUALITY | `run_quality_checks(...)` |
| STAGE_GOLD | `build_all_gold_marts(...)` |
| PUBLISH_SUCCESS | `PublicationStore.activate(...)` |

---

## TaskMessage / future XCom contract

`TaskMessage` may carry bounded identifiers/metadata only:

- `run_id`, `publication_version`, `source`, `task_id`, `attempt`, `environment`
- optional: `asset_id`, `source_file_id`, `object_key_ref`, `quality_report_ref`,
  `publication_scope_ref`, `scheduling_class`

Forbidden (validated): raw payloads, Arrow/DataFrames, DuckDB/DB connections,
credentials/tokens/DSNs, SQL text, large record lists, canonical/Gold contents.

This is the intended future Airflow XCom shape — small and safe.

---

## Retry policy

Typed per-task policies with bounded `max_attempts` (≤ 5). Infinite retries are
forbidden.

| Class | Examples | Notes |
|---|---|---|
| Retryable transient | DISCOVER transport | Bounded attempts |
| Idempotent replay | REGISTER, INGEST | Preserve `ALREADY_SUCCESS` semantics |
| Non-retryable business | PRE_*_QUALITY hard gate | Retries cannot convert FAIL → SUCCESS |
| Non-retryable conflict | CANONICALIZE conflict / RESTORE_REQUIRED; PUBLISH content conflict | Operator decision required |

---

## Backfill contract

`BackfillRequest` requires `source`, date range, `max_files_per_run`,
`max_file_size_bytes`, requester, reason, and `dry_run`.

- No unbounded / missing bounds
- Date span capped (≤ 366 days)
- Preserves asset identity, run identity, publication version, provenance,
  deletion precedence, quality gates, and idempotency
- Local validation uses synthetic small ranges only

Development sample bounds (Step 19 `max_files=1`) remain separate from this
production-oriented ceiling.

---

## Two quality gates

Mapped to Step 17 `ExecutionStage`:

1. `PRE_SERVING_QUALITY` → `PRE_SERVING_BUILD`  
   Failure blocks: `STAGE_GOLD`, `PRE_VISIBLE_QUALITY`, `PUBLISH_SUCCESS`, `FINAL_VALIDATION`
2. `PRE_VISIBLE_QUALITY` → `PRE_VISIBLE_PUBLICATION`  
   Failure blocks: `PUBLISH_SUCCESS`, `FINAL_VALIDATION`

Orchestration retries must not override hard-gate failures.

---

## Analytical publication order (Step 15 ownership)

Orchestration owns concurrency of scratch decision sets; it does not reopen
Step 15 logic:

1. Freeze `work_publication_decisions`
2. Derive `accepted_work_ids`
3. Derive `relationship_publish_work_ids`
4. Merge Works
5. Replace eligible relationships

Deletion tombstones publish Work rows; owned relationships stay physically
preserved. `IDENTICAL` / `STALE` / `CONFLICT` / `RESTORE_REQUIRED` must not
incorrectly refresh relationships.

---

## Concurrent BigQuery publication scope

Step-15 SQL file names are **contract addresses only**. Concurrent runs must
not share one global persistent scratch table set.

| Strategy | Meaning | When |
|---|---|---|
| **TEMP_TABLES** (recommended default) | Script/session-scoped decision sets | All dependent statements run in one BigQuery script/session |
| **RUN_SCOPED** | Physical names `{contract}_r_<uuidhex>` | Cross-task orchestration cannot share one script session |
| **SINGLE_WRITER** | Serialize publication jobs | Operational lock / queue |

`publication_scope_suffix(run_id)` derives a deterministic, bounded,
identifier-safe suffix from UUID only. Arbitrary user suffixes / raw SQL
templating are rejected.

**Not deployed** by this repository step.

---

## Scheduling classes

Documented only — no cron deployment:

- `MANUAL`
- `SCHEDULED_INCREMENTAL` (configurable; not hard-coded into business logic)
- `BACKFILL`

---

## Airflow / Composer compatibility (future)

| Contract | Future mapping |
|---|---|
| `OrchestrationGraph` | Airflow DAG |
| `TaskSpec` | Task / operator wrapper calling existing Python APIs |
| `TaskMessage` | Small XCom payload |
| `RetryPolicy` | `retries` / `retry_delay` |
| `run_id` / `publication_version` | DAG run metadata |

Composer must use workload identity / secret-managed connections (Step 21).
No Airflow Variables with secrets and no Connections in source control.

---

## Dataform boundary

Do **not** implement Dataform here. Future optional ownership:

| Owner | Concerns |
|---|---|
| Python / source pipeline | discover, landing, canonical publication, deletions, provenance/control, quality orchestration, consumer publication control |
| Possible Dataform (later) | BigQuery SQL dependency management for selected analytical/Gold transforms **after** data is in BigQuery |

Dataform must not become the source ingestion/control plane. Adoption remains a
decision after a real BigQuery runtime exists.

---

## CI/CD

Existing gates remain authoritative:

```text
make check
make test
make build
```

Orchestration contract tests run in the default offline suite
(`tests/unit/test_orchestration_contracts.py`). Optional:

```text
make test-orchestration
```

No deployment credentials in GitHub Actions. No automatic GCP deploy.

---

## Observability contract

Safe events only: `TaskStarted`, `TaskSucceeded`, `TaskFailed`,
`PipelineSucceeded`, `PipelineFailed` with `run_id`, `task_id`, `attempt`,
timestamps, safe error category, `publication_version`, recovery action.

Never log payload, credentials, DSN, SQL, or secret-bearing tracebacks.
No monitoring vendor SDK in this step.

---

## Local validation

```python
from research_platform.orchestration import (
    render_execution_plan,
    validate_orchestration_plan,
)

validate_orchestration_plan()
plan = render_execution_plan(publication_version="pub-demo")
```

Shows task order, dependencies, retry classification, and publication-scope
bindings. Does not execute cloud resources or emulate Airflow.

---

## Secrets boundary

Orchestration contracts never accept DSNs, tokens, or credential material in
task messages or events. Future Composer secret wiring belongs to Step 21
readiness (#28).

---

## Limitations

- Contract/validation only — no scheduler process
- No MEASURED BigQuery publication concurrency proof
- No production SLA / freshness guarantees invented here
- PostgreSQL/AlloyDB consumer serving remains optional (#23), not a DAG task
