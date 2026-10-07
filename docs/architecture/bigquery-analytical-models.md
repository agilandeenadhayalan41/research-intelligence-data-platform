# BigQuery analytical models (Step 15 / #22)

BigQuery-first analytical **contracts** for OpenAlex. This step defines schemas,
Standard SQL, grains, partition/cluster choices, incremental MERGE strategy,
cost-safety rules, and local DuckDB semantic checks.

## Scope boundary

| Status | What |
| --- | --- |
| **DEFINED IN STEP 15** | Table DDL contracts, query SQL files, pattern mappings, grains, partition/cluster decisions, MERGE/refresh design, cost rules, DuckDB `SEMANTIC_ONLY` fixtures, static offline checks |
| **NOT YET DEPLOYED / MEASURED** | GCP datasets/tables, paid BigQuery jobs, partition pruning proof, clustering effectiveness, real cost/latency, Gold marts (Step 16 / #55) |

No cloud deployment, service accounts, Terraform, or paid queries are included.

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
SQL must predicate on `publication_year`. This repository defines that contract;
actual pruning effectiveness is **NOT YET MEASURED** on deployed BigQuery.

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

### Works MERGE precedence

`sql/bigquery/openalex/models/merge_works.sql` does **not** blindly overwrite
matched rows. It mirrors Steps 11–13:

| Case | Behavior |
| --- | --- |
| Same `source_checksum_sha256` | Idempotent replay (touch `run_id` / `processed_at` only) |
| Target `DELETED` + staging `ACTIVE` | **No silent resurrection** (skip); surface via preconditions |
| Staging `lineage_source_updated_date` older | STALE — skip |
| Equal lineage dates, different checksum | CONFLICT — skip; detect via preconditions |
| Staging newer lineage date (or dated vs undated target) | APPLY update, including ACTIVE→DELETED tombstones |

Precondition query: `sql/bigquery/openalex/models/merge_works_preconditions.sql`
(`RESTORE_REQUIRED`, `CONFLICT`, `STALE_SKIPPED`). Ordinary analytical MERGE
must not resurrect DELETED Works; newer ACTIVE after DELETE remains
`RESTORE_REQUIRED` / explicit reconciliation.

Offline decision helper: `decide_analytical_works_merge` (tests only; not a
BigQuery runtime).

### Relationship tables — transactional REPLACE_BY_WORK_ID

Template (`merge_work_topics.sql`):

```sql
BEGIN TRANSACTION;
DELETE ... changed work_id set ...
INSERT ... current projection ...
COMMIT TRANSACTION;
```

Atomic commit/rollback prevents a failed mid-replace from leaving those Works
with an empty relationship projection. BigQuery multi-statement DML
transactions can span tables, but they differ operationally from the local
PostgreSQL ingestion transaction — do not claim identical ACID behavior to
Step 12.

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
