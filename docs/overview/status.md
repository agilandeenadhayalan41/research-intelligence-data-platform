---
sidebar_position: 3
title: Status
description: What is complete, what has been validated and where, how ready each environment is, and what is not proven yet.
---

# Status

> **Last updated from `main` @ `206105e` (2026-10-13).** This page summarizes
> the repository at that commit. If a detailed page disagrees with this one,
> check the dates on that page's "Recorded run" sections and the readiness
> levels in [`src/research_platform/readiness/registry.py`](../../src/research_platform/readiness/registry.py).

## At a glance

- **Roadmap:** all 21 steps are complete. That means contracts, local
  validation, orchestration contracts and a readiness assessment. It does
  **not** mean GCP deployment or production readiness.
- **Cloud evidence:** the GCS landing and BigQuery adapters passed live smoke
  tests in a sandbox project and in personal projects labelled DEV and QA. The
  BigQuery models were deployed and `MEASURED` on **synthetic data only**: at
  about 10k Works in the sandbox, DEV and QA, and at about 100k Works in the
  sandbox only.
- **Readiness:** every environment is `NOT_READY` overall, and every mandatory
  PROD domain is `BLOCKED`. No promotion between environments is possible
  today.
- **Serving:** the PostgreSQL lookup projection (#23) is a local `PROTOTYPE`
  whose gate is **not** met. One BigQuery + cache measurement run (#118) is
  mentioned only in a commit message: it **missed** the 200 ms p95 target with
  a mostly cold cache. No warm-cache result is recorded.
- **Not deployed anywhere:** Airflow/Composer, Dataform, an HTTP Data Service,
  repository-provisioned IAM, workload identity or secrets (the #108 grants
  were made by hand), monitoring, and a promotion pipeline.
- **Not proven yet:** real payloads, production scale, cost, enterprise
  environments and serving latency. See
  [What is not proven yet](#what-is-not-proven-yet).

## What is not proven yet

These are open. None of them should be assumed from the evidence further down
this page.

- **Real OpenAlex payloads.** No real payload has been profiled or ingested.
  Every cloud run used synthetic data, apart from the #92 smoke query on a
  public sample table.
- **Production scale.** Nothing has been measured at full OpenAlex scale, or
  even on a representative slice. The largest run is about 100k synthetic
  Works, in the sandbox only.
- **Cost.** Billed bytes in every BigQuery run were dominated by BigQuery's
  10 MiB per-table billing minimum, so they are floor artifacts, not workload
  cost.
- **Clustering.** It was assessed (`OBSERVED`) for only 4 of 11 patterns, the
  ones that read a table of at least 64 MiB, in #108 Stage 2. Only one of the
  four, `citation-relationships` (a filter on the leading cluster column),
  showed a reduction: it processed 0.157 of its dry-run estimate. The three
  filters on non-leading cluster columns showed none, and the cause was not
  isolated.
- **Job cancellation under load.** The timeout probe completed before a cancel
  could trigger, so cancellation was never exercised.
- **Enterprise environments.** The sandbox, DEV and QA are operator-owned or
  personal projects. There is no enterprise-managed project, and deploy and
  runtime used the same identity.
- **GCS beyond the smoke checks.** Retries, concurrent writers against real
  GCS, large or resumable uploads, CMEK, retention and access logging have not
  been validated against real GCS.
- **Concurrent publication.** `TEMP_TABLES` / `RUN_SCOPED` / `SINGLE_WRITER`
  are `CLOUD_UNVERIFIED`. The REPLACE/MERGE paths for relationship tables
  other than `work_topics`, and for dimensions, were not exercised in BigQuery.
- **Gold refresh and materialization.** No Gold mart is `MATERIALIZED`, and
  Gold refresh has not been measured.
- **Data quality in the cloud.** The quality SQL ran in each #103-script run
  (the sandbox at about 10k and about 100k Works, DEV and QA), on synthetic
  data only. Quality gating and alerting in BigQuery remain unverified, and
  `DATA_QUALITY` stays `CLOUD_UNVERIFIED`.
- **Serving.** There is no HTTP Data Service and no auth. No lookup latency
  through a cache is recorded in the docs: the one #118 run used a mostly cold
  cache and missed the target. The #23 projection has not been run on AlloyDB,
  and its latency, concurrency and scale were not measured.
- **Lookup requirement.** The 200 ms p95 target is operator-stated, not a
  measured or product-validated Wiley SLA.
- **Orchestration and infrastructure.** There is no Airflow/Composer, Dataform
  or Terraform. The repository provisions no IAM bindings, workload-identity
  pools or Secret Manager. The #108 least-privilege grants were made manually
  by the operator in operator-owned or personal projects, and `IDENTITY_IAM`,
  `WORKLOAD_IDENTITY` and `SECRETS` stay `CLOUD_UNVERIFIED`. There is no
  monitoring, cost or network evidence, and no promotion deploy pipeline.
- **Other sources.** AACT/ClinicalTrials, OpenFDA, grants, patents and news
  are not implemented.
- **ISSN/eISSN lookup.** It stays `UNRESOLVED`, because the canonical model
  holds only `issn_l`.
- **Tooling gaps.** There is no linter, formatter or type-checker target.
  Required-check enforcement is a repository setting that the CI workflow file
  does not establish.

## The 21-step roadmap

The [roadmap](../architecture/roadmap.md) marks every step complete. In the
roadmap's own words, completion means "the planned contracts, local
validation, orchestration contracts, and readiness assessment are complete"
and **not** that GCP deployment is complete.

| Step | Capability | Status at `206105e` | Issue |
| --- | --- | --- | --- |
| 01 | Repository foundation: packaging, typed interfaces, logging, tests, CI | Complete | — |
| 02 | Configuration framework: validated YAML, explicit environment, strict sample limits | Complete | — |
| 03 | Storage contracts (`ObjectStore`), plus `GCSObjectStore` | Complete. GCS adapter live-verified (see below) | [#9][i9] |
| 04 | Warehouse contracts, plus BigQuery, DuckDB and PostgreSQL adapters | Complete. BigQuery live-verified, DuckDB local only, PostgreSQL `LOCAL_POSTGRES_VERIFIED` | [#10][i10], [#8][i8], [#7][i7] |
| 05 | Bounded anonymous OpenAlex connector | Complete | — |
| 06 | Works manifest parser | Complete | — |
| 07 | Deterministic bounded sample selector | Complete | — |
| 08 | Bounded source profiling | Implemented. **Real payload profile pending** public network access | — |
| 09 | Immutable local raw landing | Complete | [#17][i17] |
| 10 | Pipeline control and provenance contracts | Complete | [#18][i18] |
| 11 | Canonical OpenAlex model | Complete | [#15][i15] |
| 12 | One-file local Works ingestion (memory + Postgres) | Complete | [#20][i20] |
| 13 | Works deletions as tombstones (memory + Postgres) | Complete, bounded local only | [#21][i21] |
| 14 | Query-pattern and benchmark registry | Complete (`FIXTURE_ONLY` local harness) | [#19][i19] |
| 15 | BigQuery-first analytical contracts | Complete. Models later measured in the cloud on synthetic data | [#22][i22] |
| 16 | Gold analytical marts | Complete. None materialized | [#55][i55] |
| 17 | Data quality gates and metrics | Complete (`SEMANTIC_ONLY` runner) | [#24][i24] |
| 18 | Data Service domain layer | Complete. No HTTP deployment | [#26][i26] |
| 19 | Bounded end-to-end pipeline | Complete (local `SEMANTIC_ONLY`) | [#27][i27] |
| 20 | Orchestration contracts | Complete. No Airflow/Composer/Dataform | [#25][i25] |
| 21 | Environment readiness registry and promotion gates | Complete. No cloud provisioning | [#28][i28] |

The Step 09–11 issue numbers come from the PR titles in the git history. The
others come from the README's status table.

## Validation history

The table lists the validation runs recorded in the docs and merge history,
oldest first. "Evidence level" uses the labels in the
[Glossary](glossary.md). All cloud runs used **synthetic** data or public
sample data, never real OpenAlex payloads. Identifiers are redacted in the
linked evidence.

| Issue | What was validated | Where | Evidence level | PR |
| --- | --- | --- | --- | --- |
| — | OpenAlex Works manifests (JSONL and Parquet) reachable anonymously, declared formats, 2026-10-06 | Public OpenAlex bucket, HEAD and bounded-range reads | Metadata verified. Payload codecs remain documented expectations | — |
| — | Step 08 opt-in profiling command, 2026-10-07 | Copilot cloud environment | **Did not complete:** DNS blocked both manifest reads, so no payload was profiled | — |
| [#90][i90] | `PostgreSQLWarehouse` read queries, types, errors, reconnects (7 passed), 2026-10-08 | Local PostgreSQL 18.6, psycopg 3.2.13 | `LOCAL_POSTGRES_VERIFIED`. Not cloud or AlloyDB evidence | [#98][p98] |
| [#91][i91] | `GCSObjectStore`: create-only write, identical replay, conflicts, checksum, reads, 2026-10-10 | `GCP_SANDBOX`, operator-owned project, user ADC | `VERIFIED` (GCP_SANDBOX) | [#101][p101] |
| [#92][i92] | `BigQueryWarehouse`: dry-run budget rejection, billed-bytes cap, parameter binding, empty/NULL results, 2026-10-10 | `GCP_SANDBOX`, user ADC | `VERIFIED` (GCP_SANDBOX, adapter contract only) | [#102][p102] |
| [#103][i103] | Step 15–16 models deployed and measured at about 10k Works, 2026-10-11 (see the notes below the table) | `GCP_SANDBOX`, user ADC | `MEASURED` (synthetic-10k-works) | [#104][p104]–[#107][p107] |
| [#108][i108] Stage 0 | Offline smoke tooling and Stage 2 script extensions, plus a linear-time in-memory store for the large preset | Local (offline) | Tooling only | [#109][p109], [#110][p110], [#111][p111] |
| [#23][i23] | Local PostgreSQL work-lookup projection, 20 tests passed, 2026-10-11 | Local PostgreSQL 16 (Docker) | `PROTOTYPE`. Gate **not** met. Latency, concurrency and scale not measured | [#112][p112] |
| [#108][i108] Stage 1 | #91, #92 and #103 re-run as a **least-privilege service account** via impersonation, with no key files: GCS 17/17, BigQuery smoke 10/10, #103 95/95, 2026-10-11/12 | `GCP_SANDBOX` (same project) | `VERIFIED` with a least-privilege identity. Every executed job ran as the service account. Cancellation not exercised | [#113][p113] |
| [#108][i108] Stage 2 | #103 re-run at about 10× scale (100,100 batch-1 Works), 95/95, after the adapter cap-floor fix, 2026-10-12 | `GCP_SANDBOX`, least-privilege SA | `MEASURED` (synthetic-100100-works). Clustering `OBSERVED` for 4 of 11 patterns, with a reduction in only 1. Cost still not representative | [#114][p114], [#115][p115] |
| [#108][i108] Stage 3 DEV | GCS 17/17, BigQuery smoke 10/10, #103 95/95 at about 10k Works, 2026-10-12 | DEV: a personal project labelled DEV, not enterprise-managed | `VERIFIED` for `GCS_RAW_LANDING` and `BIGQUERY_ANALYTICAL` in DEV. No promotion | [#116][p116] |
| [#108][i108] Stage 3 QA | The same runs, at about 10k Works, with a separate service account. The DEV account was refused access to QA, 2026-10-12 | QA: a separate personal project labelled QA | `VERIFIED` for both domains in QA. No promotion | [#117][p117] |
| [#23][i23] | Lookup requirement recorded: interactive app/API consumer, ≤200 ms end-to-end p95, ≤5 rps peak, daily freshness | Configuration only ([`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml)) | Operator-stated. **Not** measured, **not** a Wiley SLA | [#119][p119] |
| [#118][i118] | In-process TTL/LRU lookup cache, a manual BigQuery + cache measurement script, and `--prewarm` | Not recorded for the one run | **One run, mentioned only in a commit message, missed** the 200 ms p95 target with a mostly cold cache. No warm result and no docs evidence record | [#120][p120], [#121][p121] |

Notes on the table:

- **#103** covered all 15 DDL files, the six publication decisions, MERGE
  precedence, REPLACE rollback under failure injection, the Gold, quality and
  query SQL, `publication_year` pruning and small-sample latency. Its cost
  figures are billing-floor artifacts, and clustering was not measured.
- **#108** repeats the sandbox validation with a least-privilege identity
  (Stage 1), at about 10× scale (Stage 2), and then in DEV and QA (Stage 3).
  The repository records no stage after Stage 3, and not the issue's
  open/closed state
  ([BigQuery recorded runs](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1)).
- **#118's one run** is described only in the body of commit `ff82034`
  (PR #121): the 500 warm-up requests touched far fewer than the 1,000 keys,
  so the cache was mostly cold and the run missed the target. `--prewarm`
  was added so that a re-run measures the warm steady state. No docs page has
  a "Recorded run" for #118. The code is
  [`lookup_cache.py`](../../src/research_platform/serving/lookup_cache.py) and
  [`lookup_cache_measure_118.py`](../../scripts/lookup_cache_measure_118.py).

Sources: the "Recorded run" sections of
[BigQuery analytical models](../architecture/bigquery-analytical-models.md#recorded-run-92),
[Immutable landing](../architecture/immutable-local-landing.md#recorded-run-91),
[PostgreSQL Warehouse](../architecture/postgres-warehouse.md#local-validation-evidence-90),
[Work lookup projection](../architecture/serving-work-lookup-projection.md) and
[Source profiling](../architecture/openalex-source-profiling.md#what-is-verified),
plus the merge-commit titles and commit messages on `main`.

## Environment readiness

The platform has a fixed promotion ladder. `LOCAL` is evidence only. It is
never a cloud rung.

```text
LOCAL (evidence only) -> GCP_SANDBOX -> DEV -> QA -> PROD
```

The table shows the levels the code registry assigns at `206105e`, from
`evaluate_environment_readiness()`, which makes no cloud calls. The
identity, observability and similar rows group several registry domains that
share the same level. The cells are per-domain levels, and the last row is
each environment's overall status.

| Domain | LOCAL | GCP_SANDBOX | DEV | QA | PROD |
| --- | --- | --- | --- | --- | --- |
| `GCS_RAW_LANDING` | DEMONSTRATED_LOCAL | **VERIFIED** | **VERIFIED** | **VERIFIED** | BLOCKED |
| `BIGQUERY_ANALYTICAL` | CONTRACT_DEFINED | **VERIFIED** | **VERIFIED** | **VERIFIED** | BLOCKED |
| `CONFIGURATION` | DEMONSTRATED_LOCAL | CONTRACT_DEFINED | CONTRACT_DEFINED | CONTRACT_DEFINED | BLOCKED |
| `DATA_QUALITY`, `GOLD_MARTS`, `DATA_SERVICE` | DEMONSTRATED_LOCAL | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `ORCHESTRATION`, `PUBLICATION_CONCURRENCY` | CONTRACT_DEFINED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `IDENTITY_IAM`, `WORKLOAD_IDENTITY`, `SECRETS` | CONTRACT_DEFINED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `OBSERVABILITY`, `COST_CONTROLS`, `NETWORKING`, `GOVERNANCE`, `SECURITY`, `LINEAGE`, `RECOVERY` | CONTRACT_DEFINED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `ROLLBACK` | DEMONSTRATED_LOCAL | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `CI_CD` | DEMONSTRATED_LOCAL | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | CLOUD_UNVERIFIED | BLOCKED |
| `OPERATIONAL_STORE_OPTIONAL` | CONTRACT_DEFINED | CONTRACT_DEFINED | CONTRACT_DEFINED | CONTRACT_DEFINED | CONTRACT_DEFINED |
| **Overall** | NOT_READY | **NOT_READY** | **NOT_READY** | **NOT_READY** | **NOT_READY** |

How to read it:

- **A `VERIFIED` domain does not make an environment ready.** An environment
  is `NOT_READY` while any mandatory domain is below `READY_TO_VALIDATE`, and
  `READY` only when every mandatory domain is `VERIFIED`. Most mandatory
  domains are still `CLOUD_UNVERIFIED`. `LOCAL` is always `NOT_READY`.
- **Promotion is closed.** `can_promote()` needs adjacent order, a `READY`
  source, and a target that is `READY_TO_VALIDATE` or `READY` with no
  mandatory-domain gaps. It has no manual override. Today it returns false
  for GCP_SANDBOX → DEV, DEV → QA and QA → PROD.
- **The optional operational store never blocks promotion**
  ([promotion gates](../architecture/environment-readiness.md#cicd-and-promotion-gates)).
- **DEV and QA are personal projects labelled DEV and QA,** not
  enterprise-managed environments. The DEV/QA `VERIFIED` levels rest on the
  #108 Stage 3 runs above.

Every source agrees on one thing: **every environment is `NOT_READY`
overall**, and every mandatory PROD domain is `BLOCKED`.

## Open and active issues

| Issue | State as the repo records it | What remains open |
| --- | --- | --- |
| [#2][i2] | **Open.** The umbrella for deferred and runtime work and live-validation tracking | Separately scoped work, including AACT/ClinicalTrials as the next source (not implemented) |
| [#23][i23] | **Open, gated.** Only a local `PROTOTYPE` exists, and its gate is **not** met. Lookups stay `BIGQUERY_PLUS_CACHE` / `ASSUMED` | A BigQuery + API/cache measurement at representative scale (see below) |
| [#108][i108] | Stages 0–3 recorded. The issue's open/closed state is not recorded in the repo | The gaps its records leave open (see below) |
| [#118][i118] | Tooling merged (#120, #121). One cold-cache run, mentioned only in a commit message, missed the target. The issue's state is not recorded in the repo | A recorded warm (`--prewarm`) measurement and a docs evidence record. The script reports a meets/misses verdict against the target p95 |

More on the open items:

- **#23 gate.** The gate inputs listed on #23 are a lookup requirement, the
  consumer, and a BigQuery + API/cache measurement at representative scale.
  The first two are now operator-stated (#119). The measurement is not
  recorded. A PostgreSQL/AlloyDB projection is justified only if that
  measurement fails the requirement.
- **#108 gaps.** Synthetic data instead of a representative slice,
  non-representative cost, no enterprise-managed project, one identity for
  deploy and runtime, and cancellation not exercised.

## Known inconsistencies in the docs

The readiness pages do not all agree, and several older pages still describe
work as "not implemented" or "later" that has since been done. The newer
sources are the #108 records (PRs #113–#117), the registry's levels and the
code itself. The table lists the outdated passages known at `206105e`. It may
not be complete, so check any "not implemented", "later" or "future work"
statement against the dates of the "Recorded run" sections.

| Statement | Where | Newer source |
| --- | --- | --- |
| GCS and BigQuery are `VERIFIED` in GCP_SANDBOX only and `READY_TO_VALIDATE` for DEV/QA | README "Roadmap completion status", the README step table (rows 03 and 04) and its GCS/BigQuery adapter paragraphs, the [roadmap](../architecture/roadmap.md) intro, "Completed foundation" rows 03 and 04 and deferred-gaps table, and the [environment readiness](../architecture/environment-readiness.md#current-truthful-matrix-summary) matrix, evidence legend, GCS and BigQuery readiness sections and known gaps | The registry and the #108 Stage 3 records say `VERIFIED` for DEV and QA |
| DEV/QA stay `READY_TO_VALIDATE` | The summary paragraph right after the Stage 3 records in [Immutable landing](../architecture/immutable-local-landing.md#recorded-run-108-stage-3-qa). The [#103 record](../architecture/bigquery-analytical-models.md#recorded-run-103) says the same, but it was written before Stage 3 | [#108 Stage 3 QA](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-3-qa) |
| The DQ and Gold SQL ran once, in GCP_SANDBOX only (#103) | [Environment readiness](../architecture/environment-readiness.md#data-quality--gold--data-service) "Data quality / Gold / Data Service", and the `DATA_QUALITY` rationale text in the registry (its level, `CLOUD_UNVERIFIED`, still stands) | The #103 script runs every Gold and quality file in each run: #108 Stage 1 and 2 in the sandbox, and Stage 3 in DEV and QA ([#108 Stage 2](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-2)) |
| Deploying and measuring the Step 15–16 BigQuery models is "still planned" | README "Still planned" | The same README, and #103, record them as deployed and `MEASURED` on synthetic data |
| GCP runtime adapters are not part of the current implementation, and GCS and BigQuery runtime implementations "are not included" | The intro and the closing paragraph of "Design principles" in [GCP: BigQuery-first](../architecture/gcp-bigquery-first.md#design-principles) | The #9 / #10 adapters, the #91 / #92 smokes, and the #103 / #108 deployments |
| There is no ingestion, GCS landing, BigQuery runtime or Data Service, and "GCS landing is later" | The "current foundation" paragraph of [Platform architecture](../architecture/platform-architecture.md#platform-flow) and its [layer responsibilities](../architecture/platform-architecture.md#layer-responsibilities) | Steps 12–21 and the #9 / #10 adapters |
| "Landing, storage, and warehouse operations remain future work" | The end of [OpenAlex discovery and manifest contract](../architecture/openalex-manifest-contract.md#future-flow) | Steps 09, 12 and 15, and the #9 / #10 adapters |
| "Canonical transforms and ingestion orchestration are later steps" | The intro of [Immutable landing](../architecture/immutable-local-landing.md) | Steps 11, 12, 19 and 20 |
| `ControlStore` is a fail-fast protocol only | README "Configuration and environments" | Step 12 added an in-memory store and `PostgresControlStore` ([Local Works ingestion](../architecture/local-works-ingestion.md)) |
| The optional cache is "not implemented" | The diagram in [Data Service](../architecture/data-service.md#architecture-boundary) "Architecture boundary" | [`lookup_cache.py`](../../src/research_platform/serving/lookup_cache.py) (#118, PRs #120 and #121): an in-process cache for measurement, not a deployed service |
| The materialized-aggregate candidates are `journal_stats` and `publisher_topic_stats` | [Query routing](../architecture/query-routing.md#materialized-aggregates) "Materialized aggregates" | The Gold marts are `journal_author_stats`, `publisher_topic_year_stats` and `publisher_topic_license_year_stats` ([inventory](../architecture/gold-analytical-marts.md#inventory)) |
| No lookup requirement exists (`desired_p95_latency_ms: null`) | [Work lookup projection](../architecture/serving-work-lookup-projection.md#requirement-link) | PR #119 set 200 ms p95, 5 rps and 24 h freshness in the query-pattern registry |
| `ingestion\` and `serving\` are "Reserved runtime areas", and `scripts\` is reserved | The README repository map | `ingestion/` holds the Step 12–13 pipelines, `serving/` holds the #23 and #118 code, and `scripts/` holds four manual cloud scripts. `dags/` is still empty |
| Docusaurus publication remains future work | README "Documentation and repository map" | This site (`docs/site/`), added after `206105e` |
| Runtime dependencies are Pydantic, PyYAML and PyArrow | README "Local development" | `pyproject.toml` also pins `google-cloud-storage` and `google-cloud-bigquery` |

[i2]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2
[i7]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/7
[i8]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/8
[i9]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/9
[i10]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/10
[i15]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/15
[i17]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/17
[i18]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/18
[i19]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/19
[i20]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/20
[i21]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/21
[i22]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/22
[i23]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/23
[i24]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/24
[i25]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/25
[i26]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/26
[i27]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/27
[i28]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/28
[i55]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/55
[i90]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/90
[i91]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/91
[i92]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/92
[i103]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/103
[i108]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/108
[i118]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/118
[p98]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/98
[p101]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/101
[p102]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/102
[p104]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/104
[p107]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/107
[p109]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/109
[p110]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/110
[p111]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/111
[p112]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/112
[p113]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/113
[p114]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/114
[p115]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/115
[p116]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/116
[p117]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/117
[p119]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/119
[p120]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/120
[p121]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/121
