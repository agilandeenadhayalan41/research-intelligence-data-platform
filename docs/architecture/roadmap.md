# Research Intelligence Public Data Platform roadmap

This roadmap preserves completed work and sequences implementation from bounded
source profiling through consumer delivery. It does not authorize work beyond an
explicitly scoped task. Steps 01–18 are complete through the Data Service
domain layer (no GCP deploy / no HTTP serving). Step 19 / #27 is next. GCS
landing and later steps remain planned.

## Completed foundation

| Step | Work | Status |
| --- | --- | --- |
| 01 | Repository foundation | Complete |
| 02 | Configuration framework | Complete |
| 03 | Storage abstraction/contracts | Complete contract; GCS runtime separate (#9) |
| 04 | Warehouse abstraction/contracts | Complete contract; DuckDB/BigQuery/PostgreSQL runtime adapters separate |
| 05 | Bounded public OpenAlex connector | Complete |
| 06 | OpenAlex Works manifest parser | Complete |
| 07 | Bounded deterministic development sample selector | Complete |
| 08 | Bounded OpenAlex source profiling ([details](openalex-source-profiling.md)) | Implemented; real payload profile pending public network access |
| 09 | Immutable local raw landing ([details](immutable-local-landing.md)) | `LocalObjectStore` delivered; GCS deferred to #9 |
| 10 | Pipeline control and provenance contracts ([details](pipeline-control.md)) | Models, lifecycle, `ControlStore` protocol, DDL specs; local Postgres adapter in Step 12 |
| 11 | Canonical OpenAlex logical model ([details](openalex-canonical-model.md)) | Normalized entities/relationships, PyArrow schemas, mapping |
| 12 | One-file local Works ingestion ([details](local-works-ingestion.md)) | Claim → land → stream decode/map/upsert/provenance in one txn → SUCCESS; memory + Postgres backends |
| 13 | OpenAlex Works deletions ([details](openalex-deletions.md)) | `deleted_ids.csv.gz` tombstones, precedence/no resurrection, deletion lineage; memory + Postgres |
| 14 | Query-pattern + benchmark registry ([details](query-pattern-benchmarks.md)) | Typed workloads, grains, fan-out risks, evidence-honest placements; FIXTURE_ONLY local harness; no BigQuery deploy ([#19](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/19)) |
| 15 | BigQuery-first analytical contracts ([details](bigquery-analytical-models.md)) | BQ Standard SQL DDL/queries, grains, partition/cluster, MERGE/refresh, cost rules, DuckDB `SEMANTIC_ONLY`; no GCP deploy ([#22](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/22)) |
| 16 | Gold analytical marts ([details](gold-analytical-marts.md)) | Consumer Gold marts, grains, fan-out-safe SQL, ACTIVE/citation/license semantics, evidence-honest materialization candidates; DuckDB `SEMANTIC_ONLY`; no GCP deploy ([#55](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/55)) |
| 17 | Data quality gates and metrics ([details](data-quality.md)) | HARD_GATE vs INFORMATIONAL_METRIC, canonical/relationship/reconciliation/Gold exclusion, DuckDB `SEMANTIC_ONLY` runner; no silent repair; no GCP deploy ([#24](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/24)) |
| 18 | Data Service domain layer ([details](data-service.md)) | Storage-independent capabilities, registry, typed contracts, cursor pagination, freshness/`PUBLISHED_SNAPSHOT`, in-memory `SEMANTIC_ONLY` repo; no HTTP/raw SQL/Postgres serving ([#26](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/26)) |

## Planned steps

| Step | Work |
| --- | --- |
| 19 | Deliver a bounded end-to-end pipeline ([details](end-to-end-pipeline.md)): discover → select → ingest → immutable landing → canonicalize → changes/deletions → analytical models → data-quality gate → Gold outputs → consumer publication → final validation. Default `MAX_FILES=1` ([#27](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/27)). |
| 20 | After Step 19 works, evaluate local Airflow and future Composer compatibility, CI/CD, Dataform alignment, retries, scheduling, and monitoring. Keep business logic out of DAGs. Include run-scoped analytical publication decision tables (or TEMP tables / single-writer orchestration) so concurrent jobs do not clash on shared `work_publication_decisions` / `accepted_work_ids` / `relationship_publish_work_ids` (Step 15 contracts use shared names for file addressability only). |
| 21 | Prepare sandbox/dev/QA/prod readiness documentation for IAM, workload identity, secrets, monitoring, recovery, cost controls, deployment, rollback, lineage, and governance. Do not automatically deploy production. |

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
