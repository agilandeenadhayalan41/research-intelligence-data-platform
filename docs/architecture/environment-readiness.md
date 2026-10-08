# Environment readiness (Step 21 / #28)

**Status:** COMPLETE — readiness assessment/contracts only.  
Package: `research_platform.readiness`  
Issue: [#28](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/28) (CLOSED)  
Umbrella: [#2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2) (remains OPEN)

### Explicit non-claims

This step does **NOT**:

- provision GCP resources, IAM, secrets, VPC, Composer, Dataform, or Terraform
- deploy to Sandbox/DEV/QA/PROD
- access PROD
- create service accounts or workload-identity pools
- implement GCS (#9) or BigQuery (#10) runtime adapters
- claim the platform is production-ready

A YAML file is **not** readiness evidence.

---

## Progression

```text
LOCAL  (evidence / reference only)
  ->
GCP_SANDBOX
  ->
DEV
  ->
QA
  ->
PROD
```

`LOCAL` demonstrates offline/SEMANTIC_ONLY capability. It is **not** a cloud
promotion rung and never equals cloud readiness.

---

## Evidence legend

| Level | Meaning |
|---|---|
| `DEMONSTRATED_LOCAL` | Proven offline / DuckDB / in-memory |
| `CONTRACT_DEFINED` | Specs/SQL/contracts exist; not cloud-proven |
| `CLOUD_UNVERIFIED` | Cloud path required; no execution evidence |
| `READY_TO_VALIDATE` | Prerequisites met to begin cloud validation |
| `VERIFIED` | Actual cloud evidence exists (**unused today**) |
| `BLOCKED` / `NOT_IMPLEMENTED` | Missing runtime capability blocks readiness |

Additional labels: `LOCAL_TESTED`, `SEMANTIC_ONLY`, `PLACEHOLDER_CONFIG`,
`OPTIONAL`, `OPTIONAL_NOT_REQUIRED`.

**Forbidden labels without evidence:** `PRODUCTION_READY`, `ENTERPRISE_READY`,
`VERIFIED_GCP`.

---

## Current truthful matrix (summary)

| Domain | LOCAL | GCP_SANDBOX | DEV | QA | PROD |
|---|---|---|---|---|---|
| CONFIGURATION | DEMONSTRATED_LOCAL | CONTRACT_DEFINED (yaml shape) | PLACEHOLDER | PLACEHOLDER | BLOCKED / NOT READY |
| GCS_RAW_LANDING | LocalObjectStore + GCSObjectStore (fake tests); no live bucket | READY_TO_VALIDATE (#9 adapter) | READY_TO_VALIDATE | READY_TO_VALIDATE | BLOCKED (PROD) |
| BIGQUERY_ANALYTICAL | CONTRACT + SEMANTIC_ONLY | CLOUD_UNVERIFIED (#10) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| PUBLICATION_CONCURRENCY | CONTRACT (TEMP_TABLES) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| ORCHESTRATION | CONTRACT_ONLY (Step 20) | CLOUD_UNVERIFIED (no Composer) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| DATA_QUALITY | DEMONSTRATED_LOCAL / SEMANTIC_ONLY | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| GOLD_MARTS | DEMONSTRATED_LOCAL | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| DATA_SERVICE | Domain + in-memory demo | CLOUD_UNVERIFIED (no HTTP) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| IDENTITY_IAM / WIF / SECRETS | Documented | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| OBSERVABILITY / COST / NETWORK / GOVERNANCE | Documented | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| CI_CD | make check/test/build | Local/CI only | No deploy pipeline | No deploy pipeline | No deploy pipeline |
| OPERATIONAL_STORE | OPTIONAL_NOT_REQUIRED | OPTIONAL_NOT_REQUIRED | OPTIONAL_NOT_REQUIRED | OPTIONAL_NOT_REQUIRED | OPTIONAL_NOT_REQUIRED |

**Overall today:** GCP_SANDBOX, DEV, QA, and PROD are all `NOT_READY`.

---

## Configuration

- Environment must be selected explicitly (`parse_environment` rejects unknowns).
- No implicit PROD fallback.
- Symbolic identifiers only: `GOOGLE_CLOUD_PROJECT`, `GCS_BUCKET`,
  `BIGQUERY_DATASET` (and `POSTGRES_DSN` env name for optional local Postgres).
- Never commit credentials or hard-code project/bucket/dataset values.
- `config/local.yaml` — safe offline default for tests.
- `config/gcp-sandbox.yaml` — future cloud **shape** only.
- `config/dev.yaml` / `qa.yaml` / `prod.yaml` — local-only placeholders.

---

## IAM / workload identity / secrets

Documented capabilities (least privilege, not Owner/Editor):

- source discovery/read
- raw object write/read
- BigQuery job execution / dataset read-write
- quality validation / Gold publication / consumer read
- monitoring/logging

Boundaries:

- separate deployment identity vs runtime identity
- no human long-lived production credentials
- no service-account JSON keys in the repository
- prefer workload identity / short-lived credentials for future GCP runtime
- GitHub Actions must not store cloud deploy credentials in this step
- secrets only via approved secret manager/runtime; never in config git,
  TaskMessage/XCom, or logs (Step 20 bounds preserved)

No IAM bindings, WIF pools, or Secret Manager resources are created here.

---

## GCS readiness

| Fact | Status |
|---|---|
| `ObjectStore` contract | exists |
| `LocalObjectStore` | demonstrated |
| `GCSObjectStore` adapter | implemented (#9) — create-only `if_generation_match=0`, fake-client contract tests |
| Live ADC / real bucket | **not** executed in default CI |

Therefore `GCS_RAW_LANDING` is `READY_TO_VALIDATE` for Sandbox/DEV/QA (adapter
ready for separately approved sandbox smoke), **not** `VERIFIED`, and PROD
remains BLOCKED. Do not mark any environment READY solely because the adapter
exists. Future live validation must cover immutable writes, checksum/provenance,
preconditions, retries, encryption, retention, environment separation, and
access logging — under a separately approved sandbox issue.

---

## BigQuery readiness

| Fact | Status |
|---|---|
| Step 15–16 SQL/contracts | CONTRACT_DEFINED |
| DuckDB SEMANTIC_ONLY | local evidence only |
| Warehouse adapter / deploy | deferred **#10** |

Status remains `CONTRACT_DEFINED` / `CLOUD_UNVERIFIED` until real execution
evidence exists (datasets, schema deploy, MERGE, partition/cluster,
`maximum_bytes_billed`, concurrency strategy, DQ/Gold, latency/cost).

**Do not run paid queries from this readiness step.**

Publication concurrency (Step 20): preferred `TEMP_TABLES`, fallback
`RUN_SCOPED`, alternative `SINGLE_WRITER` — still `CLOUD_UNVERIFIED`.

---

## Orchestration readiness

Step 20 proved CONTRACT_ONLY graph/ExecutionUnits/XCom/retries/backfills.
Airflow/Composer is **not** deployed. No environment may claim Composer
readiness.

---

## Data quality / Gold / Data Service

- DQ & Gold logic: `DEMONSTRATED_LOCAL` / `SEMANTIC_ONLY`
- BigQuery-executed DQ/Gold: `CLOUD_UNVERIFIED`
- Production alerting/on-call: not implemented
- Data Service: domain contract + local in-memory demo; no HTTP/auth/scale/cache

Local DuckDB success must not be relabeled as production BigQuery validation.

---

## Observability / cost / recovery / rollback / lineage / governance

Requirements are documented in the readiness registry (signals, budgets,
recovery scenarios from Steps 12–20, publication rollback semantics, lineage
fields, governance themes). None are deployed cloud components.

Prefer versioned forward-fix publication over unvalidated destructive
BigQuery rollback.

Public OpenAlex data does **not** remove governance obligations.

---

## Networking

`CLOUD_UNVERIFIED`. Future questions include public-source egress, private
service access, Composer egress, DNS/proxy, and optional VPC Service Controls
if enterprise policy requires them. VPC-SC is **not** mandated here.

---

## CI/CD and promotion gates

Existing Actions prove `make check` / `make test` / `make build` only.

Promotion order (adjacent only):

```text
GCP_SANDBOX -> DEV -> QA -> PROD
```

`can_promote(from, to)` requires:

- adjacent order: `GCP_SANDBOX → DEV → QA → PROD`
- **source** `overall_status == READY` (all mandatory domains `VERIFIED`)
- **target** `overall_status` in `{READY_TO_VALIDATE, READY}`

`can_promote(from, to)` fails closed when:

- order is skipped or reversed
- LOCAL is used as a cloud rung
- source is only `READY_TO_VALIDATE` (insufficient promotion evidence)
- target mandatory domains are `BLOCKED` / `CLOUD_UNVERIFIED` / `CONTRACT_DEFINED` / `DEMONSTRATED_LOCAL`
- overall target status is `NOT_READY`

There is **no** manual `ready=True` override.
`OPERATIONAL_STORE_OPTIONAL` never blocks promotion.

PROD remains NOT READY while mandatory cloud evidence is missing.

---

## Optional operational store

PostgreSQL/AlloyDB serving is `OPTIONAL_NOT_REQUIRED` (#23).

It must **not** block promotion. Consider only if measured BigQuery + API/cache
evidence fails a concrete latency/concurrency requirement.

---

## Machine validation

```python
from research_platform.readiness import (
    can_promote,
    evaluate_environment_readiness,
    readiness_report,
)

report = evaluate_environment_readiness("GCP_SANDBOX")
decision = can_promote("GCP_SANDBOX", "DEV")
```

No cloud calls. Results derive from the static readiness registry.

---

## Known gaps (blocking cloud readiness today)

1. GCS live sandbox validation (adapter implemented in #9; no ADC/bucket VERIFIED evidence)  
2. BigQuery runtime adapter (#10) not implemented  
3. No cloud IAM / WIF / secrets wiring  
4. No Composer/Airflow deployment  
5. No HTTP Data Service deploy  
6. No cloud observability/cost/network evidence  
7. No environment promotion deploy pipeline  

---

## Related docs

| Topic | Doc |
|---|---|
| Roadmap | [roadmap.md](roadmap.md) |
| Orchestration | [orchestration.md](orchestration.md) |
| End-to-end (local) | [end-to-end-pipeline.md](end-to-end-pipeline.md) |
| BigQuery contracts | [bigquery-analytical-models.md](bigquery-analytical-models.md) |
| Data Service | [data-service.md](data-service.md) |
