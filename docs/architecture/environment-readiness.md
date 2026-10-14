# Environment readiness (Step 21 / #28)

**Status:** COMPLETE — readiness assessment/contracts only.  
Package: `research_platform.readiness`  
Issue: [#28](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/28) (CLOSED)  
Umbrella: [#2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2) (CLOSED, completed 2026-10-13; closing the roadmap is not a deployment or readiness claim)

### Explicit non-claims

This step does **NOT**:

- provision GCP resources, IAM, secrets, VPC, Composer, Dataform, or Terraform
- deploy to Sandbox/DEV/QA/PROD
- access PROD
- create service accounts or workload-identity pools
- implement the GCS (#9) or BigQuery (#10) runtime adapters (they were delivered
  separately; see [GCS readiness](#gcs-readiness) and
  [BigQuery readiness](#bigquery-readiness))
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
| `CLOUD_UNVERIFIED` | Cloud path required; not cloud-verified (no execution evidence, or ad hoc execution only, as for the DQ and Gold SQL) |
| `READY_TO_VALIDATE` | Prerequisites met to begin cloud validation (no registry entry is at this level today) |
| `VERIFIED` | Actual cloud evidence exists (today: `GCS_RAW_LANDING` and `BIGQUERY_ANALYTICAL` in `GCP_SANDBOX` (#91/#92/#103, #108 Stages 1–2) and in the operator's personal projects labelled `DEV` and `QA` (#108 Stage 3); domain level only, synthetic data only) |
| `BLOCKED` / `NOT_IMPLEMENTED` | Missing runtime capability or access blocks readiness (today: every mandatory PROD domain) |

Additional labels: `LOCAL_TESTED`, `SEMANTIC_ONLY`, `PLACEHOLDER_CONFIG`,
`OPTIONAL`, `OPTIONAL_NOT_REQUIRED`.

**Forbidden labels without evidence:** `PRODUCTION_READY`, `ENTERPRISE_READY`,
`VERIFIED_GCP`.

---

## Current truthful matrix (summary)

| Domain | LOCAL | GCP_SANDBOX | DEV | QA | PROD |
|---|---|---|---|---|---|
| CONFIGURATION | DEMONSTRATED_LOCAL | CONTRACT_DEFINED (yaml shape) | CONTRACT_DEFINED (PLACEHOLDER_CONFIG) | CONTRACT_DEFINED (PLACEHOLDER_CONFIG) | BLOCKED |
| GCS_RAW_LANDING | DEMONSTRATED_LOCAL (LocalObjectStore + GCSObjectStore fake-client tests; no live bucket) | VERIFIED (#91 live smoke; #108 Stage 1 least-privilege re-run) | VERIFIED (#108 Stage 3; personal project) | VERIFIED (#108 Stage 3; personal project) | BLOCKED |
| BIGQUERY_ANALYTICAL | CONTRACT_DEFINED (+ SEMANTIC_ONLY; offline adapter #10) | VERIFIED (#92 adapter smoke; models MEASURED on synthetic-10k-works in #103 and #108 Stage 1, synthetic-100100-works in #108 Stage 2) | VERIFIED (#108 Stage 3; personal project; synthetic-10k-works) | VERIFIED (#108 Stage 3; personal project; synthetic-10k-works) | BLOCKED |
| PUBLICATION_CONCURRENCY | CONTRACT_DEFINED (TEMP_TABLES) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| ORCHESTRATION | CONTRACT_DEFINED (Step 20 CONTRACT_ONLY) | CLOUD_UNVERIFIED (no Composer) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| DATA_QUALITY | DEMONSTRATED_LOCAL / SEMANTIC_ONLY | CLOUD_UNVERIFIED (SQL ran ad hoc, not gated) | CLOUD_UNVERIFIED (SQL ran ad hoc, not gated) | CLOUD_UNVERIFIED (SQL ran ad hoc, not gated) | BLOCKED |
| GOLD_MARTS | DEMONSTRATED_LOCAL | CLOUD_UNVERIFIED (SELECTs ran ad hoc, not materialized) | CLOUD_UNVERIFIED (SELECTs ran ad hoc, not materialized) | CLOUD_UNVERIFIED (SELECTs ran ad hoc, not materialized) | BLOCKED |
| DATA_SERVICE | DEMONSTRATED_LOCAL (domain + in-memory demo) | CLOUD_UNVERIFIED (no HTTP) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| IDENTITY_IAM / WORKLOAD_IDENTITY / SECRETS | CONTRACT_DEFINED (documented) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| OBSERVABILITY / COST_CONTROLS / NETWORKING / GOVERNANCE / SECURITY / LINEAGE / RECOVERY | CONTRACT_DEFINED (documented) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| ROLLBACK | DEMONSTRATED_LOCAL (Step 19 local publication rollback) | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| CI_CD | DEMONSTRATED_LOCAL (make check/test/build) | CLOUD_UNVERIFIED (local/CI only) | CLOUD_UNVERIFIED (no deploy pipeline) | CLOUD_UNVERIFIED (no deploy pipeline) | BLOCKED (no deploy pipeline) |
| OPERATIONAL_STORE_OPTIONAL | CONTRACT_DEFINED (OPTIONAL_NOT_REQUIRED) | CONTRACT_DEFINED (OPTIONAL_NOT_REQUIRED) | CONTRACT_DEFINED (OPTIONAL_NOT_REQUIRED) | CONTRACT_DEFINED (OPTIONAL_NOT_REQUIRED) | CONTRACT_DEFINED (OPTIONAL_NOT_REQUIRED) |
| **Overall** | NOT_READY (reference only) | NOT_READY | NOT_READY | NOT_READY | NOT_READY |

**Overall today:** GCP_SANDBOX, DEV, QA, and PROD are all `NOT_READY`; every
mandatory PROD domain is `BLOCKED`, and `can_promote` denies every step
(GCP_SANDBOX → DEV, DEV → QA, QA → PROD). `DEV` and `QA` here are
operator-controlled personal projects labelled DEV and QA (#108 Stage 3), not
enterprise-managed environments. Every `VERIFIED` cell is domain-level evidence
on synthetic data only; it does not make its environment ready.

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
- The dev/qa/prod files configured none of the cloud runs. The #103 and #108
  runs (GCP_SANDBOX and the personal DEV/QA projects) were driven by the
  operator-run `scripts/` with their own flags; the earlier #91 and #92
  GCP_SANDBOX smokes predate those scripts. The local pipelines refuse any
  `storage.backend` other than `local`, and no code selects a cloud backend
  from these files.

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
| Live sandbox smoke (#91) | **passed** in GCP_SANDBOX: create-only precondition create, identical replay no-op, bytes/provenance conflict → `ObjectConflictError` with generation unchanged, checksum/provenance, caller-owned reads ([details](immutable-local-landing.md#recorded-run-91)) |
| Least-privilege and DEV/QA re-runs (#108) | **passed** 17/17 each time as a least-privilege service account reached by impersonation: in GCP_SANDBOX (Stage 1), then in personal projects labelled DEV and QA (Stage 3). Replay and conflict were classified without `objects.delete`; an unconditioned overwrite and an out-of-prefix create were refused with 403 ([Stage 1](immutable-local-landing.md#recorded-run-108-stage-1), [DEV](immutable-local-landing.md#recorded-run-108-stage-3-dev), [QA](immutable-local-landing.md#recorded-run-108-stage-3-qa)) |

Therefore `GCS_RAW_LANDING` is `VERIFIED` for GCP_SANDBOX and for the personal
DEV and QA projects, at domain level only; PROD remains BLOCKED. All of this
evidence comes from operator-controlled projects (user ADC for #91; for #108,
one least-privilege service account per project, with the buckets and grants
set up by the operator outside this repository), so it does not cover an
enterprise-managed project or separate deploy/runtime identities. A
VERIFIED domain does not make the environment READY: GCP_SANDBOX, DEV and QA
stay `NOT_READY` while other mandatory domains are unverified. Not yet
validated against real GCS in any environment: retries, concurrent writers
(covered locally and with the fake client only), large/resumable uploads,
encryption/CMEK, retention, environment separation, and access logging.

---

## BigQuery readiness

| Fact | Status |
|---|---|
| Step 15–16 SQL/contracts | CONTRACT_DEFINED |
| DuckDB SEMANTIC_ONLY | local evidence only (not a BigQuery dialect/cost proof) |
| `BigQueryWarehouse` adapter | implemented (#10) — dry-run + `maximum_bytes_billed`, fake-client tests |
| Live ADC / real dataset jobs | **not** executed in default CI |
| Live sandbox smoke (#92) | **passed** in GCP_SANDBOX: dry-run budget rejection, provider-enforced `maximum_bytes_billed`, bounded execute, typed `@parameter` binding, empty/null Arrow results ([details](bigquery-analytical-models.md#recorded-run-92)) |
| Model deploy and MEASURE (#103) | **MEASURED** in GCP_SANDBOX on synthetic-10k-works: DDL matches contracts, all six publication decisions, `works` MERGE precedence, `work_topics` REPLACE rollback under failure injection (other relationship tables and dimensions were direct-loaded), below-floor cap rejected, `publication_year` pruning, small-sample latency ([details](bigquery-analytical-models.md#recorded-run-103)) |
| Least-privilege, scale and DEV/QA re-runs (#108) | **passed** as a least-privilege service account reached by impersonation; every executed job ran as that account. Stage 1 (GCP_SANDBOX): #92 smoke 10/10 and #103 run 95/95. Stage 2 (GCP_SANDBOX): #103 run 95/95, MEASURED at synthetic-100100-works, clustering OBSERVED for 4 of 11 patterns. Stage 3 (personal projects labelled DEV, then QA): smoke 10/10 and #103 run 95/95 at synthetic-10k-works ([Stage 1](bigquery-analytical-models.md#recorded-run-108-stage-1), [Stage 2](bigquery-analytical-models.md#recorded-run-108-stage-2), [DEV](bigquery-analytical-models.md#recorded-run-108-stage-3-dev), [QA](bigquery-analytical-models.md#recorded-run-108-stage-3-qa)) |

Therefore `BIGQUERY_ANALYTICAL` is `VERIFIED` for GCP_SANDBOX and for the
personal DEV and QA projects, at domain level only (the adapter contract, plus
the models MEASURED at synthetic scale); PROD remains BLOCKED.
The evidence used operator-controlled projects (user ADC for #92 and #103; for
#108, one least-privilege service account per project that both deployed and
ran) and synthetic data only: synthetic-10k-works, plus synthetic-100100-works
in GCP_SANDBOX. It does not cover production-scale cost/latency/concurrency,
clustering effectiveness (Stage 2 only OBSERVED clustering, for 4 of 11
patterns, without isolating the cause), job timeout/cancellation under load, an
enterprise-managed project, or separate deploy/runtime identities. GCP_SANDBOX,
DEV and QA stay `NOT_READY` while other mandatory domains are unverified.

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
- BigQuery-executed DQ/Gold: `CLOUD_UNVERIFIED`. `scripts/bigquery_sandbox_103.py`
  executes all 12 quality files and all 9 Gold SELECTs in each run, so the DQ
  and Gold SQL ran on synthetic data in every recorded run of it: #103 and
  #108 Stages 1–2 in GCP_SANDBOX (about 10k and about 100k Works), and #108
  Stage 3 in the personal DEV and QA projects (about 10k Works). These were ad
  hoc runs: no DQ result gated a publication or raised an alert, and no Gold
  mart was materialized or refreshed (DELETED-work exclusion was checked on two
  marts). DQ gating/alerting and Gold deployment remain unverified.
- Production alerting/on-call: not implemented
- Data Service: domain contract + local in-memory demo; no HTTP/auth/scale and
  no production cache. The in-process lookup cache measured in #118 (personal
  DEV, synthetic data, `DataService` not in the measured path) is measurement
  code, not a deployed cache.

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

PostgreSQL/AlloyDB serving is `OPTIONAL_NOT_REQUIRED` and is not being adopted:
#23 closed as not planned on 2026-10-13, after the operator chose BigQuery +
API/cache for DOI and Work-ID lookups. The #118 runs (personal DEV, synthetic
data, in-process cache) missed the operator-stated 200 ms p95 with a cold cache
and met it when prewarmed; that target is not a measured or product-validated
SLA. The local PostgreSQL Work lookup projection (#112) stays an unapproved,
undeployed PROTOTYPE.

It must **not** block promotion. Reopen #23 only under its closing criteria:
production evidence that more than 5% of requests miss a warm cache, or that a
shared cache breaks the 200 ms p95.

This domain covers the optional serving store only. The local
`PostgresControlStore` / `PostgresCanonicalStore` (Steps 12–13,
`--backend postgres`) and the `PostgreSQLWarehouse` read adapter (#7, #90) are
local adapters, neither a serving store nor a production control plane. The
production durable control state is undecided (DE-12, #133):
[ADR 0001 D4](../adr/0001-gcp-architecture-and-service-selection.md#d4-durable-control-plane-cloud-sql-for-postgresql-control-state-only)
proposes Cloud SQL for PostgreSQL for control state only, adopted only after
the DE-12 gates pass. No readiness level changes because of it.

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

1. GCS beyond the operator-controlled projects (#91 and #108 verified GCP_SANDBOX
   and the personal DEV/QA projects): enterprise-managed project (DE-04, #125),
   separate deploy/runtime identities (DE-23, #144), retries, concurrent
   writers, large/resumable uploads, CMEK, retention, access logging  
2. BigQuery beyond the operator-controlled projects (#92 adapter; #103 and #108
   models on synthetic data, at most synthetic-100100-works): representative-scale
   MEASURE, cost and clustering (DE-18, #139), enterprise-managed project
   (DE-04, #125), separate deploy/runtime identities (DE-23, #144), cancellation
   under load (DE-22, #143)  
3. No repository-provisioned IAM / WIF / secrets; the #108 service accounts were
   set up by an operator-run procedure outside this repository (DE-23, #144)  
4. No Composer/Airflow deployment (DE-21, #142)  
5. No HTTP Data Service deploy (DE-28, #149)  
6. No cloud observability/cost/network evidence (DE-26, #147; DE-24, #145)  
7. No environment promotion deploy pipeline (DE-27, #148)  
8. No BigQuery DQ gating or Gold materialization/refresh; the SQL ran ad hoc
   only (DE-16, #137; DE-17, #138)  

---

## Related docs

| Topic | Doc |
|---|---|
| Roadmap | [roadmap.md](roadmap.md) |
| Orchestration | [orchestration.md](orchestration.md) |
| End-to-end (local) | [end-to-end-pipeline.md](end-to-end-pipeline.md) |
| BigQuery contracts | [bigquery-analytical-models.md](bigquery-analytical-models.md) |
| Data Service | [data-service.md](data-service.md) |
