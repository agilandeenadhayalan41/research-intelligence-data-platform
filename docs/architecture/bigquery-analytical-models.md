# BigQuery analytical models (Step 15 / #22)

BigQuery-first analytical **contracts** for OpenAlex. This step defines schemas,
Standard SQL, grains, partition/cluster choices, incremental MERGE strategy,
cost-safety rules, and local DuckDB semantic checks.

## Scope boundary

| Status | What |
| --- | --- |
| **DEFINED IN STEP 15** | Table DDL contracts, query SQL files, pattern mappings, grains, partition/cluster decisions, MERGE/refresh design, cost rules, DuckDB `SEMANTIC_ONLY` fixtures, static offline checks |
| **MEASURED IN GCP_SANDBOX (#103)** | Step 15–16 DDL, publication/MERGE/REPLACE models, Gold/quality/query SQL and `publication_year` pruning on synthetic-10k-works; small-sample latency ([recorded run](#recorded-run-103)) |
| **NOT YET MEASURED** | Production-scale cost/latency/concurrency, clustering effectiveness, Gold refresh/materialization, DEV/QA/PROD |
| **RUNTIME ADAPTER (#10)** | `BigQueryWarehouse` — dry-run + `maximum_bytes_billed`, typed `@params`, Arrow results; offline fake-client tests plus a live sandbox smoke (#92) |

No cloud deployment, service accounts, Terraform, or paid queries are included
in default CI. DuckDB `SEMANTIC_ONLY` is **not** a BigQuery dialect or cost
validator.

### Optional live sandbox smoke (manual only)

Not run by `make test`, GitHub Actions, or default development. Requires a
separately approved project/dataset and ADC. Suggested checks: dry-run budget
rejection, bounded execute, parameter binding, empty/null Arrow results.

#### Recorded run (#92)

On 2026-10-10 (`main` @ `91edd61`, unmodified `BigQueryWarehouse`, no injected
client) all checks passed against real BigQuery. A 1 KiB cap was rejected by
the dry-run check before execution. With an 8 MiB cap the dry-run estimate fit
but BigQuery's 10 MiB billing minimum did not, so the job failed
`bytesBilledLimitExceeded` (mapped to `QueryBudgetExceededError`, 0 bytes
billed). A bounded query on `bigquery-public-data.samples.shakespeare` under a
20 MiB cap returned 5,318 rows (5,114,816 bytes processed, 10,485,760 billed).
BOOL/INT64/FLOAT64/STRING/TIMESTAMP/DATE/ARRAY parameters bound correctly, and
empty and typed-NULL results kept their Arrow schema. Evidence:
[#92](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/92#issuecomment-6102493009)
(identifiers redacted).

Environment: the operator-owned sandbox project used for #91 and an empty
dataset created for the run with operator approval; user ADC; CPython 3.14.4
and `google-cloud-bigquery` 3.46.1. No tables were created.

#92 on its own covered the adapter contract only; the Step 15–16 models were
deployed and MEASURED later, in #103 (below).

#### Recorded run (#103)

On 2026-10-11 (`main` @ `0979223`, `scripts/bigquery_sandbox_103.py`, repo SQL
submitted byte-for-byte, unmodified `BigQueryWarehouse`) the Step 15–16 models
were deployed to an empty `openalex` dataset in the operator-owned sandbox
project. They were exercised with a synthetic cycle of about 10k works generated
through the Step 12/13 ingest path. Status: **MEASURED (GCP_SANDBOX,
synthetic-10k-works)**.

- Deploy: all 15 DDL statements reported `CREATE`. Columns, types, modes,
  partition column and clustering equal `build_table_contracts()`. The integer
  range spec is covered by the offline DDL checks, and the DDL was submitted
  byte-for-byte.
- Publication: batch-2 decisions (`INSERT` 200, `IDENTICAL` 200, `APPLY_UPDATE`
  300, `STALE` 110, `CONFLICT` 110, `RESTORE_REQUIRED` 100) equal
  `decide_analytical_works_merge`. `merge_works` and `merge_work_topics` DML
  counts reconcile, the 320 STALE/CONFLICT/RESTORE_REQUIRED rows are unchanged,
  and `canonical_work_id_not_null` / `_unique` return 0.
- Failure injection: a NULL `topic_id` made `merge_work_topics.sql` fail
  (`Required field topic_id cannot be null`). BigQuery rolled back the 395-row
  DELETE already made inside the transaction, and `work_topics` was unchanged.
  A 1 MiB cap was rejected (`bytesBilledLimitExceeded`, 0 bytes billed).
- Gold, quality, queries: all 9 Gold SELECTs, the 12 quality files and the 11
  IMPLEMENTED query patterns executed (raw client and `BigQueryWarehouse`, equal
  row counts). `issn-source-lookup` stays UNRESOLVED.
  `reconciliation.decode_balance` and
  `reconciliation.publication_decision_balance` have no BigQuery SQL
  (NOT_RUN).
- Pruning (dry-run `total_bytes_processed`; bounded / typed-NULL /
  literal-year control): `publication-trends` 42,584 / 162,086 / 42,584;
  `open-access-trends` 62,271 / 237,968 / 62,271;
  `publisher-topic-license-year` 965,757 / 1,307,643 / 965,757.
- Latency (21 cache-off runs per pattern, 0 cache hits): server-side elapsed
  p50 166–256 ms and p95 207–696 ms across the 11 patterns; client wall-clock
  p50 1.03–1.25 s and p95 1.25–2.26 s. Small sample on KB–MB tables, not a
  production latency.
- Cost: 350 jobs (331 query, 19 free Parquet loads) billed 6,018,826,240 bytes
  (5.61 GiB). Query caps were 64–200 MiB, apart from the deliberate 1 MiB
  injection, and no job was billed above its cap. Every billed query job was
  billed at a multiple of the 10 MiB per-table floor, so these are floor
  artifacts, not workload cost. Clustering is NOT MEASURED (tables of KB–MB).

The run hit two script defects, fixed in #107. A failed script job was read
before its status was refreshed, and a reserved `rows` alias crashed the final
query before the evidence files were written. The job record was rebuilt from
BigQuery job history, including the 11 unlabelled `BigQueryWarehouse` jobs,
found by time window. Then, against the unchanged dataset and with #107's code:

- the lost dry-run estimates were re-measured;
- the partitions summary was run;
- the 21-run latency measurement, new in #107, was added.

Evidence: [#103](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/103) (identifiers redacted).

This is not evidence of production, DEV or QA cost, latency or concurrency. It
also does not cover clustering effectiveness, Gold refresh or materialization,
REPLACE/MERGE for the other relationship tables and dimensions, concurrent
publication, job timeout/cancellation under load, or an enterprise-managed
project or least-privilege runtime identity. `BIGQUERY_ANALYTICAL` stays
`VERIFIED` for **GCP_SANDBOX only**. DEV/QA stay `READY_TO_VALIDATE`, PROD stays
`BLOCKED`, and no Gold mart is `MATERIALIZED`.

## Separation of validation kinds

1. **BigQuery SQL correctness (contract)** — Standard SQL files use backtick
   identifiers and `@named_parameter` syntax; static tests assert file presence,
   parameter form, ACTIVE predicates, partition-key alignment, MERGE guards, and
   forbidden patterns.
2. **Local semantic validation (`SEMANTIC_ONLY`)** — DuckDB + tiny synthetic
   fixtures prove activity/deletion, citation LEFT JOIN, primary-license, and
   fan-out avoidance semantics.
3. **Future deployed BigQuery validation** — required before production use;
   must measure bytes scanned, pruning, clustering, cost, and latency on real
   (or representative) data. DuckDB does **not** prove those properties.

## Work record date vs lineage date

Two distinct calendar-date concepts exist on Works:

| Concept | Nested model | Flattened physical column |
| --- | --- | --- |
| Work record OpenAlex `updated_date` | `Work.source_updated_date` | `source_updated_date` |
| Canonical lineage / deletion date | `CanonicalLineage.source_updated_date` | `lineage_source_updated_date` |

Step 13 stores tombstone `deleted_date` in `CanonicalLineage.source_updated_date`.
Flattened PyArrow schemas, PostgreSQL DDL, and BigQuery DDL all use
`lineage_source_updated_date` for that lineage value so it never collides with
the Work record field. Nested Pydantic access is unchanged.

Every analytical table preserves lineage:

`source_asset_id`, `source_checksum_sha256`, `lineage_source_updated_date`,
`run_id`, `processed_at`, `activity_state`, `deleted_at`.

## Analytical table inventory

Canonical input tables only (Step 11). No invented fields.

| Table | Grain | Partition | Clustering | Incremental |
| --- | --- | --- | --- | --- |
| `works` | one row per `work_id` | `INTEGER_RANGE` on `publication_year` | `work_id`, `doi`, `primary_publisher_id`, `primary_source_id` | `MERGE_BY_PRIMARY_KEY` + precedence |
| `authors` | one row per `author_id` | none | `author_id` | `MERGE_BY_PRIMARY_KEY` |
| `institutions` | one row per `institution_id` | none | `institution_id` | `MERGE_BY_PRIMARY_KEY` |
| `sources` | one row per `source_id` | none | `source_id` | `MERGE_BY_PRIMARY_KEY` |
| `publishers` | one row per `publisher_id` | none | `publisher_id` | `MERGE_BY_PRIMARY_KEY` |
| `topics` | one row per `topic_id` | none | `topic_id` | `MERGE_BY_PRIMARY_KEY` |
| `funders` | one row per `funder_id` | none | `funder_id` | `MERGE_BY_PRIMARY_KEY` |
| `work_authors` | `(work_id, authorship_index)` | `lineage_source_updated_date` | `work_id`, `author_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_author_institutions` | `(work_id, authorship_index, institution_index)` | `lineage_source_updated_date` | `work_id`, `institution_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_topics` | `(work_id, topic_id)` | `lineage_source_updated_date` | `work_id`, `topic_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_keywords` | `(work_id, keyword_id)` | `lineage_source_updated_date` | `work_id`, `keyword_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_references` | `(work_id, reference_index)` | `lineage_source_updated_date` | `work_id`, `referenced_work_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_mesh` | `(work_id, mesh_index)` | `lineage_source_updated_date` | `work_id`, `descriptor_ui` | `REPLACE_BY_WORK_ID` (txn) |
| `work_locations` | `(work_id, location_index)` | `lineage_source_updated_date` | `work_id`, `source_id` | `REPLACE_BY_WORK_ID` (txn) |
| `work_grants` | `(work_id, grant_index)` | `lineage_source_updated_date` | `work_id`, `funder_id` | `REPLACE_BY_WORK_ID` (txn) |

Ordinal relationship keys are retained for canonical portability.

## Canonical → BigQuery type mapping

| PyArrow (Step 11) | BigQuery |
| --- | --- |
| `string` | `STRING` |
| `int32` / `int64` | `INT64` |
| `float64` | `FLOAT64` |
| `bool` | `BOOL` |
| `date32` | `DATE` |
| UTC `timestamp` | `TIMESTAMP` |

## Works partition strategy

`works` uses BigQuery **integer-range** partitioning on `publication_year`:

```sql
PARTITION BY RANGE_BUCKET(publication_year, GENERATE_ARRAY(1000, 3001, 1))
```

Why this key:

- Step 14 `publication-trends`, `open-access-trends`, and
  `publisher-topic-license-year` filter `@year_from` / `@year_to` on
  `publication_year`.
- Partition pruning requires filtering the **actual partition column**. Year
  predicates on `publication_year` align with that column; filtering only
  `publication_date` would not establish the documented pruning story.
- Canonical `publication_year` domain is 1000..3000. NULL years use the
  BigQuery NULL partition.

**Partition-pruning requirement (contract):** year-bounded analytical query
SQL must predicate on `publication_year`. On deployed BigQuery (#103,
synthetic-10k-works) the three year-bounded patterns with `@year_from` /
`@year_to` set processed the same bytes as a literal-year control, and fewer
than with typed-NULL bounds ([recorded run](#recorded-run-103)). Pruning at
production scale is **NOT YET MEASURED**.

Relationship tables partition on `lineage_source_updated_date` (source/asset
freshness, including deletion dates) — **not** publication time.

## Clustering strategy

Cluster keys are chosen from Step 14 lookup/aggregate filters and stay within
BigQuery’s four-column limit. Rationale is recorded on each
`BigQueryTableContract.clustering`.

## Active / deleted behavior

- Canonical tables **retain** ACTIVE and DELETED Works (no physical delete of
  Work identity).
- Reusable predicate/view: `sql/bigquery/openalex/models/active_works.sql`
  (`WHERE activity_state = 'ACTIVE'`).
- Consumer analytical models must filter ACTIVE explicitly.
- Relationships owned by a deleted source Work remain canonical observations;
  consumer models exclude them by joining ACTIVE works.

## Citation behavior

- Source Work must be `ACTIVE`.
- Referenced target may be ACTIVE, DELETED, or absent/unresolved.
- Query contract uses `LEFT JOIN` to target `works` — never inner-join away
  unresolved targets.

## Primary-location license behavior

For `publisher-topic-license-year`:

- License means **primary location license** (`work_locations.is_primary = TRUE`).
- Do **not** use `MIN(license)` / `MAX(license)`.
- Missing primary license → NULL license bucket (document as UNKNOWN to consumers).

## Incremental / MERGE design

### Publication decision contract (frozen pre-MERGE)

**Work acceptance ≠ relationship replacement eligibility.**

`work_publication_decisions.sql` owns the **single** precedence `CASE`. It is
materialized against the **pre-MERGE** `openalex.works` target. Derived sets
select from that frozen relation — they must not copy the CASE.

| Decision | Work publication | Relationship REPLACE |
| --- | --- | --- |
| `INSERT` + ACTIVE | yes (`accepted_work_ids`) | yes (`relationship_publish_work_ids`) |
| `APPLY_UPDATE` + ACTIVE | yes | yes |
| `APPLY_UPDATE` + DELETED | yes (tombstone Work) | **no** — preserve owned relationships (Step 13 Policy A) |
| `IDENTICAL` | touch clocks only | **no** ordinary REPLACE |
| `STALE` / `CONFLICT` / `RESTORE_REQUIRED` | no | no |

Publication unit order (`publish_works_unit.sql`):

1. Freeze `work_publication_decisions` (pre-MERGE)
2. Derive `accepted_work_ids` and `relationship_publish_work_ids`
3. `merge_works` applies frozen `publication_decision` (no reclassification)
4. Relationship REPLACE uses `relationship_publish_work_ids` only

Recalculating decisions after `merge_works` would turn `APPLY_UPDATE` into
`IDENTICAL` and is forbidden.

Offline helpers: `decide_analytical_works_merge`,
`is_accepted_publication_decision`, `is_relationship_refresh_eligible`.

**Concurrent publication ownership (Step 20 / #25):** the SQL files keep shared
contract names for addressability. Production concurrency is defined by
`PublicationScope` in `research_platform.orchestration` — recommended
`TEMP_TABLES` (script-scoped), with `RUN_SCOPED` or `SINGLE_WRITER` when a
single BigQuery script session is not available. See
[orchestration.md](orchestration.md). Not deployed by Step 15 or Step 20.

### Works MERGE precedence

| Case | Decision |
| --- | --- |
| Same `source_checksum_sha256` | `IDENTICAL` |
| Target `DELETED` + staging `ACTIVE` | `RESTORE_REQUIRED` |
| Staging lineage older | `STALE` |
| ACTIVE T2 + DELETED T2 (equal date) | **`APPLY_UPDATE`** (tombstone wins) |
| ACTIVE T2 + ACTIVE T2, different checksum | `CONFLICT` |
| DELETED T2 + DELETED T2, different checksum | `CONFLICT` |
| Staging newer lineage (or dated vs undated) | `APPLY_UPDATE` |

### Relationship tables — transactional REPLACE_BY_WORK_ID

Template (`merge_work_topics.sql`):

```sql
BEGIN TRANSACTION;
DELETE ... WHERE work_id IN (SELECT work_id FROM relationship_publish_work_ids);
INSERT ... WHERE source.work_id IN (SELECT work_id FROM relationship_publish_work_ids);
COMMIT TRANSACTION;
```

Deletion tombstones publish the Work row but **must not** DELETE owned
relationship observations. Consumers exclude them via
`works.activity_state = 'ACTIVE'`.

Rejected / stale staging must not replace relationships. `IDENTICAL` does not
ordinary-refresh relationships. This unit does **not** claim one global BigQuery
ACID commit across every canonical table.

## Fan-out avoidance

Do not flatten `work_authors × work_topics × work_locations × …` at raw grain.

Safe patterns (implemented in query SQL):

- Journal/publisher authors: scope works first, then `COUNT(DISTINCT author_id)`.
- Publisher×topic: `works ⋈ work_topics` only.
- Institution×topic: `DISTINCT` institution/work, then join topics.
- License metrics: primary location CTE, then join topics (not all locations).

## Cost / scan safety

Logical deployment-time rules (enforced when BigQuery execution exists):

- Prefer bounded `@year_from` / `@year_to` on `publication_year` for trends.
- No `SELECT *` in broad analytical aggregate query contracts.
- Select only needed columns; filter `ACTIVE` early.
- Avoid accidental cross joins / unrestricted full-corpus exploratory scans.
- Suggested default `maximum_bytes_billed`: **10 GiB** per application query.
  Not executed in Step 15.

## Step 14 pattern → BigQuery query mapping

| pattern_id | SQL | Status |
| --- | --- | --- |
| `doi-work-lookup` | `queries/doi_work_lookup.sql` | IMPLEMENTED |
| `openalex-work-id-lookup` | `queries/openalex_work_id_lookup.sql` | IMPLEMENTED |
| `issn-source-lookup` | — | **UNRESOLVED** |
| `publisher-lookup` | `queries/publisher_lookup.sql` | IMPLEMENTED |
| `unique-authors-per-journal` | `queries/unique_authors_per_journal.sql` | IMPLEMENTED |
| `unique-authors-per-publisher` | `queries/unique_authors_per_publisher.sql` | IMPLEMENTED |
| `publisher-topic-counts` | `queries/publisher_topic_counts.sql` | IMPLEMENTED |
| `publisher-topic-license-year` | `queries/publisher_topic_license_year.sql` | IMPLEMENTED |
| `institution-topic-relationships` | `queries/institution_topic_relationships.sql` | IMPLEMENTED |
| `citation-relationships` | `queries/citation_relationships.sql` | IMPLEMENTED |
| `publication-trends` | `queries/publication_trends.sql` | IMPLEMENTED |
| `open-access-trends` | `queries/open_access_trends.sql` | IMPLEMENTED |

## Unresolved capability: ISSN / eISSN

Canonical `sources` exposes `issn_l` only. Step 14 marks ISSN/eISSN lookup
`UNRESOLVED`. Step 15 **preserves** that gap.

## Gold marts (out of scope)

Step 16 / #55 owns gold marts and materialized aggregates. Step 15 does not
claim Gold models are deployed.

## Code and SQL layout

```text
sql/bigquery/openalex/
  ddl/           CREATE TABLE contracts
  models/        active_works, MERGE + preconditions, transactional REPLACE
  queries/       Step 14 pattern SQL (BigQuery Standard SQL)

src/research_platform/analytics/bigquery/
  contracts.py   BigQueryTableContract / type mapping / merge decision helper
  registry.py    pattern → query mapping
  validation.py  static checks + DuckDB SEMANTIC_ONLY
```

## Required future real BigQuery validation

Before treating these contracts as production-ready:

1. Deploy DDL to a non-production dataset.
2. Load a bounded representative slice.
3. Measure bytes scanned, slot time, cost, and latency for each pattern.
4. Verify integer-range partition pruning on `publication_year` filters.
5. Verify MERGE precedence and transactional REPLACE under failure injection.
6. Confirm `maximum_bytes_billed` policy behavior.
7. Only then consider Step 16 materializations from measured reuse.

Items 1, 4, 5 and 6 were run once in GCP_SANDBOX on synthetic-10k-works (#103,
[recorded run](#recorded-run-103)), and so was item 3 for bytes, slot time and
latency. Two parts remain open: item 2, a representative slice rather than
synthetic data, and the cost part of item 3 (billed bytes were floor
artifacts). Repeat the run at representative scale in an enterprise-managed
project with a least-privilege identity before item 7.
