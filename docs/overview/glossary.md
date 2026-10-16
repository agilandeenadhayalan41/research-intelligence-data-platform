---
sidebar_position: 4
title: Glossary
description: Alphabetical one-line meanings of the status labels and terms used across these docs, each linked to where it is defined.
---

# Glossary

Capitalized labels such as `VERIFIED`, `MEASURED` and `SEMANTIC_ONLY` have
exact meanings in this project. Read them literally. A label applies only to
the environment, scale and date it was recorded for. Each entry links to the
page that defines it.

## Naming conventions

| Term | Meaning | Defined in |
| --- | --- | --- |
| `#NN` | A GitHub issue or pull request in this repository, for example #23 (the lookup gate) or #121 (a pull request) | [Status](status.md#validation-history) |
| Step NN | One of the 21 steps of the roadmap, for example Step 15 (BigQuery models). It is not a section number | [Roadmap](../architecture/roadmap.md) |
| #108 Stage N | A stage of issue #108, which repeated the sandbox validation: Stage 0 (offline tooling), Stage 1 (least-privilege identity), Stage 2 (about 10× scale) and Stage 3 (DEV and QA) | [Validation history](status.md#validation-history) |

## A

| Term | Meaning | Defined in |
| --- | --- | --- |
| AACT / ClinicalTrials | The next public source in the stated source priority. Not implemented, and no open issue tracks it | [Public data sources](../architecture/public-data-sources.md#aact--clinicaltrials) |
| `ACTIVE` / `DELETED` (`activity_state`) | Whether a Work is live or tombstoned. Consumers only ever see `ACTIVE` Works | [Deletions](../architecture/openalex-deletions.md#tombstone-semantics) |
| ADC (Application Default Credentials) | Credentials the Google client libraries find in the environment. #91, #92 and #103 ran with the operator's user ADC. No key files are kept in the repository | [IAM and secrets](../architecture/environment-readiness.md#iam--workload-identity--secrets) |
| AlloyDB | Google Cloud's managed PostgreSQL-compatible database. With PostgreSQL, it is the candidate for the *optional* operational store, considered only if BigQuery plus a cache fails a measured requirement. Nothing has run on it | [Operational-store gate](../architecture/query-routing.md#operational-store-gate) |
| `APPLY_UPDATE` | Publication decision: the incoming Work has newer lineage, or is a tombstone with an equal date, so it is applied | [Works MERGE precedence](../architecture/bigquery-analytical-models.md#works-merge-precedence) |
| `ARCHITECTURE` | Query-pattern evidence status: the placement comes from architecture intent, not from cloud timing | [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |
| `asset_id` | A stable file identity: `oa-` (Works data) or `oad-` (deletion ledger) plus a SHA-256 of identity fields. It names the file. It is **not** a content checksum | [Identity and duplicates](../architecture/openalex-manifest-contract.md#identity-and-duplicates) |
| `ASSUMED` | Query-pattern evidence status for an architecture or product assumption. The DOI and OpenAlex-ID lookup placements are `ASSUMED` | [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |

## B

| Term | Meaning | Defined in |
| --- | --- | --- |
| Backfill (`BackfillRequest`) | A bounded re-run request: source, a date range of at most 366 days, file and size limits, requester, reason and `dry_run` | [Backfill contract](../architecture/orchestration.md#backfill-contract) |
| Billing floor | BigQuery's minimum billing of 10 MiB per table. At the small synthetic scales used so far, billed bytes are dominated by this floor, so the recorded cost figures are "floor artifacts", not workload cost | [Recorded run #103](../architecture/bigquery-analytical-models.md#recorded-run-103) |
| `BIGQUERY_ANALYTICAL` | Two meanings. As a placement, answer the pattern with an analytical BigQuery query. As a readiness domain, the BigQuery adapter and models | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these), [Readiness matrix](../architecture/environment-readiness.md#current-truthful-matrix-summary) |
| `BIGQUERY_PLUS_CACHE` | Placement: BigQuery behind an API/cache for point lookups. It is the current placement for DOI and OpenAlex-ID lookups | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these) |
| <Badge>`BLOCKED`</Badge> / <Badge>`NOT_IMPLEMENTED`</Badge> | Per-domain readiness level (not an overall status): a missing runtime capability blocks readiness. Every mandatory PROD domain is `BLOCKED` today, so PROD is `NOT_READY` overall | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| Bounded development sample | The deterministic selection of at most `max_files=1` file of at most 25,000,000 bytes, with a reason code for every skipped file | [Bounded sample](../architecture/openalex-manifest-contract.md#bounded-development-sample) |

## C

| Term | Meaning | Defined in |
| --- | --- | --- |
| `can_promote` | The promotion check. It needs adjacent order, a `READY` source, and a target that is `READY_TO_VALIDATE` or `READY` with no mandatory-domain gaps. It has no manual override, and it returns false for every pair today | [Promotion gates](../architecture/environment-readiness.md#cicd-and-promotion-gates) |
| Canonical model | Portable, normalized OpenAlex entities and relationships, with PyArrow schemas as the source of truth. Independent of any database's physical design | [Canonical model](../architecture/openalex-canonical-model.md) |
| Claim token / lease | How one worker owns a `PROCESSING` file. Completion needs the current token and an unexpired lease. Stale leases are recovered explicitly | [Claim / lease sequence](../architecture/pipeline-control.md#claim--lease-sequence) |
| <Badge>`CLOUD_UNVERIFIED`</Badge> | Readiness level: a cloud path is required, but it is not cloud-verified. Either there is no execution evidence, or there are only ad hoc runs, as for the data-quality and Gold SQL in the #103/#108 runs | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| Clustering (`OBSERVED`) | BigQuery cluster keys chosen from query filters. Clustering was assessed (`OBSERVED`) for 4 of 11 patterns in #108 Stage 2, those reading tables of at least 64 MiB. Only `citation-relationships` (a filter on the leading cluster column) showed a reduction, to 0.157 of the dry-run estimate. The three filters on non-leading cluster columns showed none, and the cause was not isolated. Elsewhere it is NOT MEASURED | [Recorded run #108 Stage 2](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-2) |
| CMEK (customer-managed encryption keys) | Encryption with keys the customer controls rather than Google's defaults. Not validated for GCS in any environment | [GCS readiness](../architecture/environment-readiness.md#gcs-readiness) |
| `COMPUTE_ON_READ` | Gold mart mode: computed from the analytical tables at read time | [Materialization honesty](../architecture/gold-analytical-marts.md#materialization-honesty) |
| Composer / Airflow | GCP's managed Airflow and the Airflow scheduler. Not deployed. The Step 20 contracts are shaped to map onto an Airflow DAG later | [Airflow / Composer](../architecture/orchestration.md#airflow--composer-compatibility-future) |
| `CONFLICT` | Publication decision: an equal lineage date (or no dates to compare) but a different checksum, so precedence cannot be decided. The Work is not applied | [Works MERGE precedence](../architecture/bigquery-analytical-models.md#works-merge-precedence) |
| <Badge>`CONTRACT_DEFINED`</Badge> | Readiness level: specs, SQL or contracts exist, but they are not proven in the cloud | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| `ControlStore` | The write protocol for pipeline-control state (in-memory for tests, PostgreSQL for durable local runs). It is separate from the read-only `Warehouse.query` | [Write boundary](../architecture/pipeline-control.md#write--transaction-boundary) |

## D

| Term | Meaning | Defined in |
| --- | --- | --- |
| Data Service | The storage-independent domain layer (Step 18): five typed capabilities, cursor pagination, safe errors, no raw SQL and no HTTP deployment | [Data Service](../architecture/data-service.md) |
| Dataform | A possible future owner of selected BigQuery SQL transforms. Not implemented, and never the ingestion or control plane | [Dataform boundary](../architecture/orchestration.md#dataform-boundary) |
| Deletion barrier | Kept when a deletion arrives for a Work not yet stored: an authoritative `(work_id, deleted_date)` pair, not a fabricated Work. A later, older `ACTIVE` record is `STALE`, and a newer one needs a restore | [Deletion barrier](../architecture/end-to-end-pipeline.md#unknown-work-deletion-barrier) |
| Deletion ledger | OpenAlex's cumulative `deleted_ids.csv.gz` (`work_id,deleted_date`), about 160 MB compressed. Its logical entity is `works-deletions` | [Authoritative source](../architecture/openalex-deletions.md#authoritative-public-source) |
| <Badge>`DEMONSTRATED_LOCAL`</Badge> | Readiness level: proven offline, with DuckDB or in memory | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| Dry run / `maximum_bytes_billed` | The `BigQueryWarehouse` cost guards. It estimates bytes before running, then caps billed bytes per job | [Cost / scan safety](../architecture/bigquery-analytical-models.md#cost--scan-safety) |
| DuckDB | The local engine for `SEMANTIC_ONLY` checks and for the local `DuckDBWarehouse` adapter. Its results are not BigQuery evidence | [BigQuery-first](../architecture/gcp-bigquery-first.md#design-principles) |

## E

| Term | Meaning | Defined in |
| --- | --- | --- |
| `ESTIMATED` | Query-pattern evidence status: a derived estimate with a stated method | [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |
| Evidence class (profiling) | The label on every profiled fact: `VERIFIED_METADATA`, `EXACT_FILE_METADATA`, `SAMPLED_OBSERVATION` or `UNKNOWN` | [Profiling evidence](../architecture/openalex-source-profiling.md#evidence-classification) |
| Evidence level (readiness) | The per-domain readiness scale: `DEMONSTRATED_LOCAL`, `CONTRACT_DEFINED`, `CLOUD_UNVERIFIED`, `READY_TO_VALIDATE`, `VERIFIED`, `BLOCKED` / `NOT_IMPLEMENTED` | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| Evidence status (query patterns) | How a pattern's placement is supported: `MEASURED`, `ESTIMATED`, `ASSUMED`, `UNKNOWN`, `ARCHITECTURE` or `FIXTURE_ONLY` | [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |
| `ExecutionUnit` | A physical unit of orchestration work, for example `WORKS_INGEST_UNIT`. Retries belong to the unit, not to each logical task | [Logical DAG vs ExecutionUnit](../architecture/orchestration.md#logical-dag-vs-physical-executionunit) |

## F

| Term | Meaning | Defined in |
| --- | --- | --- |
| Fan-out | Row multiplication from joining multi-valued relationships. For example, 2 authors × 3 topics gives 6 rows and double-counts. The fix is to aggregate each relationship independently | [Fan-out / double-counting](../architecture/query-pattern-benchmarks.md#fan-out--double-counting) |
| `FIXTURE_ONLY` | Query-pattern evidence status for local DuckDB/Python timings. It can never be `MEASURED` | [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |
| Forbidden labels | `PRODUCTION_READY`, `ENTERPRISE_READY` and `VERIFIED_GCP`. None may be used without evidence | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |

## G

| Term | Meaning | Defined in |
| --- | --- | --- |
| `GCP_SANDBOX` / `DEV` / `QA` / `PROD` | The cloud rungs, promoted in that order. Today DEV and QA are personal projects labelled DEV and QA, not enterprise-managed environments | [Progression](../architecture/environment-readiness.md#progression) |
| GCS (Google Cloud Storage) | Google Cloud's object storage. `GCSObjectStore` lands raw bytes there with the same create-only contract as the local folder | [GCS semantic equivalence](../architecture/immutable-local-landing.md#gcs-semantic-equivalence-9) |
| `GCS_RAW_LANDING` | The readiness domain for raw landing on GCS. `VERIFIED` in GCP_SANDBOX, DEV and QA from the #91 and #108 smokes, and `BLOCKED` in PROD | [GCS readiness](../architecture/environment-readiness.md#gcs-readiness), [Status](status.md#environment-readiness) |
| Gold mart | A consumer-facing analytical output (Step 16) with a declared grain. Nine are defined, and none is materialized | [Gold marts](../architecture/gold-analytical-marts.md#inventory) |
| Grain | What one result row represents, for example one row per `source_id` | [Result grains](../architecture/query-pattern-benchmarks.md#result-grains-examples) |

## H

| Term | Meaning | Defined in |
| --- | --- | --- |
| `HARD_GATE` | A data-quality check that blocks publication unless it is `PASS`, typically an exact violation count of zero. A check that cannot run is `ERROR`, never `PASS` | [HARD_GATE vs metric](../architecture/data-quality.md#hard_gate-vs-informational_metric) |

## I

| Term | Meaning | Defined in |
| --- | --- | --- |
| `IDENTICAL` | Publication decision: same checksum as the stored Work. Only clocks are touched, and relationships are not refreshed | [Publication decisions](../architecture/bigquery-analytical-models.md#publication-decision-contract-frozen-pre-merge) |
| Immutable raw landing | Storing the exact source bytes with provenance, create-only. Identical replay is a no-op and a conflict fails | [Immutable landing](../architecture/immutable-local-landing.md) |
| Impersonation (service account) | The operator acting as a least-privilege service account through impersonation, with no key files. Used for the #108 Stage 1–3 cloud runs and the #118 lookup runs | [Recorded run #108 Stage 1](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1) |
| `INFORMATIONAL_METRIC` | A data-quality measurement, such as the share of ACTIVE Works without a DOI. It is reported and never blocks publication | [HARD_GATE vs metric](../architecture/data-quality.md#hard_gate-vs-informational_metric) |
| `IngestionProvenance` | The immutable retrieval record stored with the raw bytes: `run_id`, `source`, `source_uri`, `retrieved_at`, `sha256` | [Provenance](../architecture/immutable-local-landing.md#provenance) |
| `INSERT` | Publication decision: a Work not yet stored | [Publication decisions](../architecture/bigquery-analytical-models.md#publication-decision-contract-frozen-pre-merge) |
| ISSN-L (`issn_l`) | The linking ISSN that groups a journal's print and electronic ISSNs. The canonical `Source` holds only `issn_l`, which is not a substitute for every ISSN/eISSN, so ISSN/eISSN lookup stays `UNRESOLVED` | [ISSN / eISSN lookup](../architecture/query-pattern-benchmarks.md#issn--eissn-lookup-issn-source-lookup) |

## L

| Term | Meaning | Defined in |
| --- | --- | --- |
| Lineage date (`lineage_source_updated_date`) | The date that decides version precedence: the source `updated_date` for an active record, or the `deleted_date` for a tombstone. Relationship tables partition on it. It is separate from the Work's own `source_updated_date` | [Record date vs lineage date](../architecture/bigquery-analytical-models.md#work-record-date-vs-lineage-date) |
| Least-privilege service account | An identity with only the permissions a run needs, for example object create/get on one prefix, or job create/list plus edit on one dataset | [Recorded run #108 Stage 1](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1) |
| `LOCAL` | The evidence-only rung for offline and `SEMANTIC_ONLY` results. It is never a cloud promotion rung | [Progression](../architecture/environment-readiness.md#progression) |
| `LOCAL_POSTGRES_VERIFIED` | `PostgreSQLWarehouse` validated against local PostgreSQL 18.6 only (#90). Not cloud or AlloyDB evidence, and not #23 | [Local validation #90](../architecture/postgres-warehouse.md#local-validation-evidence-90) |

## M

| Term | Meaning | Defined in |
| --- | --- | --- |
| `MATERIALIZATION_CANDIDATE` | Gold mart mode: reuse looks likely, but the mart is not physically built | [Materialization honesty](../architecture/gold-analytical-marts.md#materialization-honesty) |
| `MATERIALIZED` | Gold mart mode that needs `MEASURED` BigQuery evidence of repeated cost, latency or reuse. No mart has it | [Materialization honesty](../architecture/gold-analytical-marts.md#materialization-honesty) |
| `MATERIALIZED_AGGREGATE` | Placement: the pattern is a candidate for a reused, precomputed aggregate. It marks a candidate, not a built table | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these) |
| `MEASURED` | A real BigQuery observation, always qualified by scale, for example `MEASURED (GCP_SANDBOX, synthetic-10k-works)`. Local engines can never produce it | [Recorded run #103](../architecture/bigquery-analytical-models.md#recorded-run-103), [Evidence classification](../architecture/query-pattern-benchmarks.md#evidence-classification) |
| MERGE | BigQuery's insert-or-update statement. The `works` MERGE applies the frozen publication decisions and never reclassifies a Work | [Incremental / MERGE design](../architecture/bigquery-analytical-models.md#incremental--merge-design) |

## N

| Term | Meaning | Defined in |
| --- | --- | --- |
| <Badge>`NOT_READY`</Badge> | Overall status when any mandatory domain is below `READY_TO_VALIDATE` (`BLOCKED`, `CLOUD_UNVERIFIED`, `CONTRACT_DEFINED` or `DEMONSTRATED_LOCAL`). `LOCAL` is always `NOT_READY`. Every environment is `NOT_READY` today | [Readiness matrix](../architecture/environment-readiness.md#current-truthful-matrix-summary), [Promotion gates](../architecture/environment-readiness.md#cicd-and-promotion-gates) |

## O

| Term | Meaning | Defined in |
| --- | --- | --- |
| `ObjectStore` / `put_if_absent` | The create-only storage contract for raw objects, implemented by `LocalObjectStore` and `GCSObjectStore` | [Replay and conflict](../architecture/immutable-local-landing.md#replay-and-conflict) |
| OpenAlex | The public scholarly metadata dataset (Works, authors, sources and more). It is the first and only implemented source, read anonymously from its public snapshot | [Manifest contract](../architecture/openalex-manifest-contract.md) |
| `OPERATIONAL_STORE_OPTIONAL` | The readiness domain for an optional PostgreSQL/AlloyDB store. It never blocks promotion | [Optional operational store](../architecture/environment-readiness.md#optional-operational-store) |
| Operator-stated requirement | A target the operator recorded that has not been measured or product-validated. Example: the ≤200 ms p95 Work lookup target, with ≤5 rps and 24 h freshness, recorded on 2026-10-12 by PR #119. It is not a Wiley SLA | [`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml), [Validation history](status.md#validation-history) |
| `OPTIONAL_OPERATIONAL_STORE` | Placement allowed only with `MEASURED` evidence that BigQuery plus a cache fails. No pattern has it | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these) |

## P

| Term | Meaning | Defined in |
| --- | --- | --- |
| p95 / p50 | The 95th / 50th percentile of a latency sample: 95 % (or half) of requests were at least this fast. The lookup target is stated as a p95 | [Recorded run #103](../architecture/bigquery-analytical-models.md#recorded-run-103) |
| `PipelineRun` / `SourceFileControl` | Mutable control records for a run and for each file. A file moves `DISCOVERED` → `PROCESSING` → `SUCCESS` or `FAILED`, with explicit retry | [Source-file lifecycle](../architecture/pipeline-control.md#source-file-lifecycle) |
| Placement | Where a query pattern should be answered: `BIGQUERY_ANALYTICAL`, `MATERIALIZED_AGGREGATE`, `BIGQUERY_PLUS_CACHE`, `OPTIONAL_OPERATIONAL_STORE` or `UNRESOLVED` | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these) |
| Policy A | The relationship rows of a deleted Work are preserved and filtered out through the `ACTIVE` parent. They are never physically deleted | [Relationship semantics](../architecture/openalex-deletions.md#relationship--reference-semantics) |
| `PRE_SERVING_BUILD` / `PRE_VISIBLE_PUBLICATION` | The two quality stages: before the Gold build (canonical and reconciliation checks), and before consumer visibility (staged Gold checks) | [Execution stages](../architecture/data-quality.md#execution-stages) |
| Presence flags | `MISSING`, `NULL`, `EMPTY` or `PRESENT` on canonical array fields, so that absent, null and empty are never confused | [Missing vs null vs empty](../architecture/openalex-canonical-model.md#missing-vs-null-vs-empty) |
| `PROTOTYPE` (#23) | The local PostgreSQL 16 Work lookup projection, built at the operator's request ahead of the evidence. Not approved and not deployed | [Work lookup projection](../architecture/serving-work-lookup-projection.md) |
| Publication decisions | `INSERT`, `IDENTICAL`, `APPLY_UPDATE`, `STALE`, `CONFLICT`, `RESTORE_REQUIRED`. Frozen once before the `works` MERGE and never recalculated after it | [Publication decisions](../architecture/bigquery-analytical-models.md#publication-decision-contract-frozen-pre-merge) |
| Publication scope | How concurrent BigQuery publications avoid sharing scratch tables: `TEMP_TABLES` (recommended), `RUN_SCOPED` or `SINGLE_WRITER`. Still `CLOUD_UNVERIFIED` | [Publication scope](../architecture/orchestration.md#concurrent-bigquery-publication-scope) |
| `PUBLISHED_SNAPSHOT` | The Data Service consistency model: responses reflect the currently visible, quality-approved published data | [Architecture boundary](../architecture/data-service.md#architecture-boundary) |

## R

| Term | Meaning | Defined in |
| --- | --- | --- |
| <Badge>`READY`</Badge> | Overall status when every mandatory domain is `VERIFIED`. No environment has it | [Promotion gates](../architecture/environment-readiness.md#cicd-and-promotion-gates) |
| <Badge>`READY_TO_VALIDATE`</Badge> | Readiness level: the prerequisites to begin cloud validation are met. It is also an overall status, when every mandatory domain is `READY_TO_VALIDATE` or `VERIFIED`. That is enough for a promotion *target*, but not for a source | [Evidence legend](../architecture/environment-readiness.md#evidence-legend), [Promotion gates](../architecture/environment-readiness.md#cicd-and-promotion-gates) |
| `RecordProvenance` | Record-level lineage linking a canonical record to its asset, checksum and run | [Lineage](../architecture/pipeline-control.md#lineage) |
| `REPLACE_BY_WORK_ID` | The relationship refresh: delete and re-insert an eligible Work's rows inside one BigQuery transaction | [Relationship REPLACE](../architecture/bigquery-analytical-models.md#relationship-tables--transactional-replace_by_work_id) |
| `RESTORE_REQUIRED` / `RestoreRequiredError` | A newer `ACTIVE` record arrives for a tombstoned Work. It is never silently restored | [Version precedence](../architecture/openalex-deletions.md#version-precedence) |
| rps | Requests per second. The operator-stated lookup requirement is at most 5 rps at peak | [`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml) |

## S

| Term | Meaning | Defined in |
| --- | --- | --- |
| `SEMANTIC_ONLY` | Local DuckDB or in-memory validation on tiny synthetic fixtures. It proves logic, not BigQuery dialect, cost or cloud readiness | [Validation kinds](../architecture/bigquery-analytical-models.md#separation-of-validation-kinds) |
| SLA (service-level agreement) | A committed service target. No Wiley SLA exists for this platform. The 200 ms p95 lookup target is an operator-stated requirement, not an SLA | [`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml) |
| `snapshot_date` / `updated_date` / `retrieved_at` | The manifest's release date, the source-record partition date in the file path, and the time the bytes were actually fetched | [Validation, dates and URIs](../architecture/openalex-manifest-contract.md#validation-dates-and-uris) |
| `STALE` | The incoming record or deletion is older than what is stored, so it does not change the Work | [Version precedence](../architecture/openalex-deletions.md#version-precedence) |

## T

| Term | Meaning | Defined in |
| --- | --- | --- |
| `TaskMessage` / XCom | A small message passed between orchestration tasks: identifiers and references only, at most 8192 bytes serialized, with secrets rejected | [TaskMessage contract](../architecture/orchestration.md#taskmessage--future-xcom-contract) |
| Tombstone | A deleted Work's row, kept with `activity_state = DELETED` and the deletion date as its lineage date. Never physically removed | [Tombstone semantics](../architecture/openalex-deletions.md#tombstone-semantics) |
| TTL / LRU cache | The #118 in-process lookup cache: an entry expires after its time-to-live (TTL, 24 h by default, the stated daily freshness), and the least-recently-used (LRU) entries are evicted above a size bound. A measurement aid, not a deployed service | [`lookup_cache.py`](../../src/research_platform/serving/lookup_cache.py) |

## U

| Term | Meaning | Defined in |
| --- | --- | --- |
| `UNRESOLVED` | Placement when evidence is insufficient. ISSN/eISSN lookup is `UNRESOLVED`, because the canonical model holds only `issn_l` | [Placements](../architecture/query-pattern-benchmarks.md#placement-values-only-these) |

## V

| Term | Meaning | Defined in |
| --- | --- | --- |
| <Badge>`VERIFIED`</Badge> | Readiness level: actual cloud evidence exists for that domain in that environment. Today only `GCS_RAW_LANDING` and `BIGQUERY_ANALYTICAL` have it, in GCP_SANDBOX, DEV and QA | [Evidence legend](../architecture/environment-readiness.md#evidence-legend), [Status](status.md#environment-readiness) |

## W

| Term | Meaning | Defined in |
| --- | --- | --- |
| Work | One OpenAlex scholarly work, such as an article, book or dataset, identified by a `work_id` like `W123`. The central entity: authors, topics, locations and references all hang off a Work | [Entity grains and keys](../architecture/openalex-canonical-model.md#entity-grains-and-keys) |
| WIF (Workload Identity Federation) | Short-lived, keyless credentials for workloads. Preferred for any future GCP runtime, and not provisioned | [IAM and secrets](../architecture/environment-readiness.md#iam--workload-identity--secrets) |
| `WORKS_INGEST_UNIT` | The single `ExecutionUnit` for the REGISTER, INGEST and CANONICALIZE phases. It calls `ingest_works_asset` once | [Step 12 physical boundary](../architecture/orchestration.md#step-12-physical-boundary-critical) |
