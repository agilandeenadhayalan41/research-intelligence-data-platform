---
sidebar_position: 3
title: Status
description: What is complete, what has been validated and where, how ready each environment is, and what is not proven yet.
---

# Status

> **Last updated from `main` @ `d6a71a5` (2026-10-13), plus the #123 (DE-02)
> corrections.** This page summarizes the repository at that commit, and the
> GitHub issue states on that date. The readiness levels, the promotion
> outcomes and the code paths under `src/` and `scripts/` are unchanged since
> `206105e`. #123 changed wording only (docs, config and SQL comments,
> docstrings, and descriptive strings such as the registry's rationale text),
> added a docs summary of the four #118 runs, and added and updated offline
> tests that check the docs against the registry. If a detailed page disagrees
> with this one, check the dates on that page's "Recorded run" sections and
> the readiness levels in
> [`src/research_platform/readiness/registry.py`](../../src/research_platform/readiness/registry.py).
> For the status of each requirement of the lead architecture, see the
> [lead coverage register](../requirements/openalex-lead-coverage.md).

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
- **Serving:** four BigQuery + in-process cache lookup runs (#118), in the
  personal DEV project on synthetic data, are recorded in the issue's
  comments. For both the DOI and the Work-ID lookup, a cold cache **missed**
  the operator-stated 200 ms p95 target and a prewarmed cache **met** it. The
  operator then chose the BigQuery + API/cache path, and #23 (the PostgreSQL
  lookup projection) was closed as not planned. Its local `PROTOTYPE` stays
  unapproved and undeployed, and its gate was **not** met.
- **Not deployed anywhere:** Airflow/Composer, Dataform, an HTTP Data Service,
  a shared lookup cache, repository-provisioned IAM, workload identity or
  secrets (the #108 grants were made by hand), monitoring, and a promotion
  pipeline.
- **Not proven yet:** real payloads, production scale, cost, enterprise
  environments and serving latency. See
  [What is not proven yet](#what-is-not-proven-yet).
- **Follow-on work:** the data-engineering issues DE-01 to DE-35 are #122 to
  #156, under the umbrella #157. #122 is closed: PR #160 added the
  [lead coverage register](../requirements/openalex-lead-coverage.md), which
  maps each lead requirement to its evidence and follow-on issue. The others
  are open.

## What is not proven yet

These are open. None of them should be assumed from the evidence further down
this page.

- **Real OpenAlex payloads.** Profiled only in a LOCAL bounded inspection
  ([#126][i126]); none ingested (the canonical mapper rejects real records).
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
- **Serving.** There is no HTTP Data Service and no auth. The #118 runs put an
  in-process cache around `BigQueryWarehouse.query`, with no Data Service, API
  or shared cache in the path, over 1,000 synthetic Works with an assumed
  access pattern. They are an optimistic bound in personal DEV, not a
  production latency, and the real working set is unknown. Their record is
  the #118 issue comments. The #23 projection has not been run on AlloyDB,
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

| Step | Capability | Status at `d6a71a5` | Issue |
| --- | --- | --- | --- |
| 01 | Repository foundation: packaging, typed interfaces, logging, tests, CI | Complete | [#6][i6] |
| 02 | Configuration framework: validated YAML, explicit environment, strict sample limits | Complete | [#5][i5] |
| 03 | Storage contracts (`ObjectStore`), plus `GCSObjectStore` | Complete. GCS adapter live-verified (see below) | [#3][i3], [#9][i9] |
| 04 | Warehouse contracts, plus BigQuery, DuckDB and PostgreSQL adapters | Complete. BigQuery live-verified, DuckDB local only, PostgreSQL `LOCAL_POSTGRES_VERIFIED` | [#4][i4], [#10][i10], [#8][i8], [#7][i7] |
| 05 | Bounded anonymous OpenAlex connector | Complete | [#13][i13] |
| 06 | Works manifest parser | Complete | [#12][i12] |
| 07 | Deterministic bounded sample selector | Complete | [#14][i14] |
| 08 | Bounded source profiling | Implemented; real payload profiled locally ([#126][i126]) | [#16][i16] |
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

The issue numbers come from the `[NN]` step markers in the GitHub issue
titles. For Steps 03 and 04, the first issue is the contract and the others
are the adapters (`[03b]`, `[04a]`–`[04c]`).

## Validation history

The table lists the validation runs recorded in the docs and merge history,
and the #118 runs recorded in that issue's comments, oldest first. "Evidence
level" uses the labels in the [Glossary](glossary.md). All cloud runs used
**synthetic** data or public sample data, never real OpenAlex payloads.
Identifiers are redacted in the linked evidence.

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
| [#23][i23] | Lookup requirement recorded: interactive app/API consumer, ≤200 ms end-to-end p95, ≤5 rps peak, daily freshness | Configuration only ([`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml)) | Operator-stated. **Not** a measured or product-validated Wiley SLA | [#119][p119] |
| [#118][i118] tooling | In-process TTL/LRU lookup cache, a manual BigQuery + cache measurement script, and `--prewarm` | Local (offline) | Tooling only | [#120][p120], [#121][p121] |
| [#118][i118] runs 1–4 | BigQuery + in-process cache for the DOI and Work-ID lookups: Zipf(1.1) access over 1,000 ACTIVE Works, 500 warm-up and 2,000 measured requests per run, with a cold cache (runs 1 and 3) and with every key prewarmed (runs 2 and 4), 2026-10-12/13 | DEV: the personal project labelled DEV, on the #108 Stage 3 synthetic tables (about 10k Works), as the DEV least-privilege service account | Cold: **missed** the 200 ms p95 (DOI 2,426 ms, Work-ID 2,554 ms). Prewarmed: **met** it (0.006 ms and 0.010 ms). In-process cache only, not a production latency. Placement stays `BIGQUERY_PLUS_CACHE` / `ASSUMED`, and no readiness level changed | — (issue comments) |
| [#126][i126] | Real OpenAlex snapshot: layout, manifests, one JSONL/Parquet pair, checksums, rights and cadence, 2026-10-16 | Local container, anonymous HTTPS to the public bucket | LOCAL bounded inspection. Not cloud or enterprise evidence | — |

Notes on the table:

- **#103** covered all 15 DDL files, the six publication decisions, MERGE
  precedence, REPLACE rollback under failure injection, the Gold, quality and
  query SQL, `publication_year` pruning and small-sample latency. Its cost
  figures are billing-floor artifacts, and clustering was not measured.
- **#108** repeats the sandbox validation with a least-privilege identity
  (Stage 1), at about 10× scale (Stage 2), and then in DEV and QA (Stage 3).
  There is no stage after Stage 3, and the issue was closed as completed on
  2026-10-12
  ([BigQuery recorded runs](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1)).
- **#118's four runs** are recorded in the issue's comments, and the
  [Work lookup projection](../architecture/serving-work-lookup-projection.md#118-bigquery--cache-measurement)
  page and the
  [lead coverage register](../requirements/openalex-lead-coverage.md)
  summarize them. Only run 1 is described in a commit message, the body of
  commit `ff82034` (PR #121): its 500 warm-up requests touched far fewer than
  the 1,000 keys, so the cache was mostly cold and the run missed the target.
  `--prewarm` loads every key first, so runs 2 and 4 measure the warm steady
  state. Each
  cache miss cost about 2.4–3.1 s through BigQuery, so the overall p95 meets
  200 ms only when fewer than 5% of requests miss the cache. Each miss billed
  BigQuery's 10 MiB minimum, so cost is a floor artifact here too. The code is
  [`lookup_cache.py`](../../src/research_platform/serving/lookup_cache.py) and
  [`lookup_cache_measure_118.py`](../../scripts/lookup_cache_measure_118.py).

Sources: the "Recorded run" sections of
[BigQuery analytical models](../architecture/bigquery-analytical-models.md#recorded-run-92),
[Immutable landing](../architecture/immutable-local-landing.md#recorded-run-91),
[PostgreSQL Warehouse](../architecture/postgres-warehouse.md#local-validation-evidence-90),
[Work lookup projection](../architecture/serving-work-lookup-projection.md) and
[Source profiling](../architecture/openalex-source-profiling.md#what-is-verified),
plus the merge-commit titles and commit messages on `main`, and the #118 and
#23 issue comments for the #118 runs and the #23 decision.

## Environment readiness

The platform has a fixed promotion ladder. `LOCAL` is evidence only. It is
never a cloud rung.

```text
LOCAL (evidence only) -> GCP_SANDBOX -> DEV -> QA -> PROD
```

The table shows the levels the code registry assigns at `d6a71a5` (unchanged
since `206105e`), from `evaluate_environment_readiness()`, which makes no
cloud calls. The identity, observability and similar rows group several
registry domains that share the same level. The cells are per-domain levels,
and the last row is each environment's overall status.

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
  #108 Stage 3 runs above. The #118 lookup runs in DEV changed no level:
  `DATA_SERVICE` stays `CLOUD_UNVERIFIED`.

Every source agrees on one thing: **every environment is `NOT_READY`
overall**, and every mandatory PROD domain is `BLOCKED`.

## Open and active issues

The issues the evidence above centres on are now closed. The open work is the
data-engineering program (last row).

| Issue | State on GitHub (2026-10-13) | What remains open |
| --- | --- | --- |
| [#2][i2] | **Closed** (completed). The roadmap umbrella: all 21 steps complete. Its body no longer mentions AACT/ClinicalTrials | New work needs its own issue. No open issue tracks AACT/ClinicalTrials, which is not implemented |
| [#23][i23] | **Closed, not planned.** The operator chose BigQuery + API/cache for DOI and Work-ID lookups, assuming a small, hot working set. The gate was **not** met. The local `PROTOTYPE` (#112) stays unapproved and undeployed. Lookups stay `BIGQUERY_PLUS_CACHE` / `ASSUMED` | Reopen only under the criteria in the #23 closing comment (see below) |
| [#108][i108] | **Closed** (completed) after Stages 0–3 | The gaps its records leave open (see below) |
| [#118][i118] | **Closed** (completed). Tooling merged (#120, #121). Four runs in personal DEV: cold **missed**, prewarmed **met**, for both lookups | The real working set, and a cache behind a deployed API. A production-aware lookup cache is tracked in [#151][i151], and the Data Service API in [#149][i149] |
| [#122][i122]–[#156][i156], [#157][i157] | #122 (DE-01) is **closed** by PR #160, which added the [lead coverage register](../requirements/openalex-lead-coverage.md). DE-02 to DE-35 (#123–#156) and the umbrella #157 are **open** | Each requirement's follow-on issue is named in the register |

More on what remains open:

- **#23 decision.** The gate inputs listed on #23 were a lookup requirement,
  the consumer, and a BigQuery + API/cache measurement. The first two are
  operator-stated (#119), and the #118 runs are the measurement, on synthetic
  data in personal DEV rather than at representative scale. They show that
  BigQuery + an in-process cache meets the 200 ms p95 only when fewer than 5%
  of requests miss the cache. The operator judged the consumer's
  working set small and hot enough for a warm cache, so no PostgreSQL/AlloyDB
  projection was adopted. The real working set has not been measured. #23
  reopens only if production evidence shows that more than 5% of requests
  miss a warm cache, or that a shared cache breaks the 200 ms p95.
- **#108 gaps.** Synthetic data instead of a representative slice,
  non-representative cost, no enterprise-managed project, one identity for
  deploy and runtime, and cancellation not exercised.

## Known inconsistencies in the docs

At `main` @ `d6a71a5` plus the #123 (DE-02) corrections, every outdated passage
this section listed at `206105e` has been corrected at its source. That covers
the GCP_SANDBOX-only and DEV/QA `READY_TO_VALIDATE` readiness wording (README,
roadmap, environment readiness and immutable landing), the "ran once in
GCP_SANDBOX only" data-quality and Gold wording (including the registry's
`DATA_QUALITY` rationale), the README "Still planned", `ControlStore`,
repository-map, Docusaurus and runtime-dependency lines, the "not
implemented", "later" and "future work" passages in GCP: BigQuery-first,
Platform architecture, the manifest contract, immutable landing and the Data
Service diagram, the aggregate names in Query routing, and the missing lookup
requirement on the Work lookup projection page.

"Recorded run" sections keep the readiness wording of their date. Where a
later run changed it, a dated note follows. For example, the
[#103 record](../architecture/bigquery-analytical-models.md#recorded-run-103)
still says DEV and QA stay `READY_TO_VALIDATE`, and the note after it records
the #108 Stage 3 result.

No known inconsistency remains. The SQL header comments that said "NOT YET
DEPLOYED / NOT EXECUTED here" (the `sql/bigquery/openalex/models/` files and
the renderer-generated `ddl/`, `staging/` and `gold_audit/` headers) now say
they were executed only by manual scripts on synthetic data (GCP_SANDBOX,
personal DEV/QA) and are not deployed; `publish_works_unit.sql` says no
recorded run executed it.

The sources of truth are the
[lead coverage register](../requirements/openalex-lead-coverage.md) for each
lead requirement, and the readiness registry
([`registry.py`](../../src/research_platform/readiness/registry.py)) for
readiness levels and promotion. Check any "not implemented", "later" or
"future work" statement against them and against the dates of the "Recorded
run" sections.

[i2]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2
[i3]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/3
[i4]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/4
[i5]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/5
[i6]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/6
[i7]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/7
[i8]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/8
[i9]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/9
[i10]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/10
[i12]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/12
[i13]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/13
[i14]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/14
[i15]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/15
[i16]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/16
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
[i122]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/122
[i126]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/126
[i149]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/149
[i151]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/151
[i156]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/156
[i157]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/157
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
