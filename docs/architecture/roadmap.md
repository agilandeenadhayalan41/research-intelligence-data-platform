# Research Intelligence Public Data Platform roadmap

This roadmap preserves completed work and sequences implementation from bounded
source profiling through consumer delivery. It does not authorize work beyond an
explicitly scoped task.

**Steps 01–21 are complete.** Completion of the roadmap means the planned
contracts, local validation, orchestration contracts, and readiness assessment
are complete — **not** that GCP deployment is complete. Cloud runtime and
deployment gaps remain explicitly documented; this does not mean
production-ready.

Step 19 / #27 bounded E2E pipeline is complete (local `SEMANTIC_ONLY`).
Step 20 / #25 orchestration contracts are complete (logical DAG + physical
ExecutionUnits; no Airflow/Composer/Dataform/GCP deploy). Step 21 / #28
environment readiness assessment/contracts are complete (no cloud provisioning).

GCS (#9 / PR #84) and BigQuery (#10 / PR #85) **adapters** are complete offline
(`READY_TO_VALIDATE`). Live cloud validation is **not yet done**.

Explicit remaining gaps (separately scoped; do not start automatically):

- Live GCS sandbox smoke (ADC + approved bucket) — does not reopen #9
- Live BigQuery sandbox smoke (ADC + approved dataset) — does not reopen #10
- Composer/Airflow deployment
- HTTP Data Service deployment
- cloud IAM/WIF/secrets
- monitoring/cost/network validation

## Completed foundation

| Step | Work | Status |
| --- | --- | --- |
| 01 | Repository foundation | Complete |
| 02 | Configuration framework | Complete |
| 03 | Storage abstraction/contracts | Complete contract; GCSObjectStore adapter in #9 (live sandbox validation separate) |
| 04 | Warehouse abstraction/contracts | Complete contract; BigQueryWarehouse via #10 (offline); DuckDB/PostgreSQL adapters separate |
| 05 | Bounded public OpenAlex connector | Complete |
| 06 | OpenAlex Works manifest parser | Complete |
| 07 | Bounded deterministic development sample selector | Complete |
| 08 | Bounded OpenAlex source profiling ([details](openalex-source-profiling.md)) | Implemented; real payload profile pending public network access |
| 09 | Immutable local raw landing ([details](immutable-local-landing.md)) | `LocalObjectStore` delivered; `GCSObjectStore` via #9 (offline tests; live bucket separate) |
| 10 | Pipeline control and provenance contracts ([details](pipeline-control.md)) | Models, lifecycle, `ControlStore` protocol, DDL specs; local Postgres adapter in Step 12 |
| 11 | Canonical OpenAlex logical model ([details](openalex-canonical-model.md)) | Normalized entities/relationships, PyArrow schemas, mapping |
| 12 | One-file local Works ingestion ([details](local-works-ingestion.md)) | Claim → land → stream decode/map/upsert/provenance in one txn → SUCCESS; memory + Postgres backends |
| 13 | OpenAlex Works deletions ([details](openalex-deletions.md)) | `deleted_ids.csv.gz` tombstones, precedence/no resurrection, deletion lineage; memory + Postgres |
| 14 | Query-pattern + benchmark registry ([details](query-pattern-benchmarks.md)) | Typed workloads, grains, fan-out risks, evidence-honest placements; FIXTURE_ONLY local harness; no BigQuery deploy ([#19](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/19)) |
| 15 | BigQuery-first analytical contracts ([details](bigquery-analytical-models.md)) | BQ Standard SQL DDL/queries, grains, partition/cluster, MERGE/refresh, cost rules, DuckDB `SEMANTIC_ONLY`; no GCP deploy ([#22](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/22)) |
| 16 | Gold analytical marts ([details](gold-analytical-marts.md)) | Consumer Gold marts, grains, fan-out-safe SQL, ACTIVE/citation/license semantics, evidence-honest materialization candidates; DuckDB `SEMANTIC_ONLY`; no GCP deploy ([#55](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/55)) |
| 17 | Data quality gates and metrics ([details](data-quality.md)) | HARD_GATE vs INFORMATIONAL_METRIC, canonical/relationship/reconciliation/Gold exclusion, DuckDB `SEMANTIC_ONLY` runner; no silent repair; no GCP deploy ([#24](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/24)) |
| 18 | Data Service domain layer ([details](data-service.md)) | Storage-independent capabilities, registry, typed contracts, cursor pagination, freshness/`PUBLISHED_SNAPSHOT`, in-memory `SEMANTIC_ONLY` repo; no HTTP/raw SQL/Postgres serving ([#26](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/26)) |
| 19 | Bounded end-to-end pipeline ([details](end-to-end-pipeline.md)) | Complete — one outer `PipelineRun`, reusable Step-12/13 stages, DuckDB `SEMANTIC_ONLY` analytical/Gold, Step-17 gates, atomic local publication, Data Service final validation; no BigQuery/Airflow ([#27](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/27)) |
| 20 | Orchestration contracts ([details](orchestration.md)) | Complete — thin DAG + physical ExecutionUnits (`WORKS_INGEST_UNIT` = one `ingest_works_asset`), bounded TaskMessage/XCom, recursive safe metadata, retries, publication concurrency scope; no Airflow/Composer/Dataform/GCP deploy ([#25](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/25)) |
| 21 | Environment readiness ([details](environment-readiness.md)) | Complete — readiness assessment/contracts only; Sandbox→DEV→QA→PROD matrix, evidence levels, promotion gates; no cloud provisioning ([#28](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/28)) |

## Deferred / runtime gaps (not automatic next work)

| Gap | Scope | Notes |
| --- | --- | --- |
| #9 | GCS ObjectStore runtime | Adapter implemented; keep OPEN until review/live-validation policy; not VERIFIED |
| #10 | BigQuery Warehouse adapter | Adapter implemented offline; keep OPEN until review; not VERIFIED live |
| — | Composer/Airflow deployment | Not authorized by Step 20/21 |
| — | HTTP Data Service deployment | Domain contracts only today |
| — | Cloud IAM / WIF / secrets | Documented; not provisioned |
| — | Monitoring / cost / network validation | Documented; not verified |
| #7 / #8 / #23 | Optional adapters / operational store | Separately scoped; do not auto-start |

## Source extension

OpenAlex is first. AACT / ClinicalTrials is the next planned source in
[issue #2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2);
it is not implemented here. Discover AACT's existing cleaning and semantics
before deciding normalization. Future global clinical-trial sources may include
US, Europe, China, WHO/global, and other regional data. OpenFDA, grants, patents,
news/releases, and other public sources require separately scoped work and
source-specific normalization.

## Architecture decision guardrails

- BigQuery is the first analytical implementation on GCP; PostgreSQL/AlloyDB is
  optional and follows query-pattern evidence, not precedes it.
- Canonical models remain portable, with Parquet where applicable. High-volume
  relationships are primarily analytical.
- Materialize repeated expensive queries only when measured usage warrants it.
- The Data Service/API is a domain-specific, storage-independent consumer
  contract; unrestricted raw SQL is not the product API.
- Spark/Dataproc, Iceberg/BigLake, orchestration, infrastructure, and deployed
  services are not mandatory first-iteration technologies.
- Preserve completed steps, tests, contracts, sample limits, security rules, and
  CI. This roadmap does not close or reopen issues or automatically start later
  steps.
