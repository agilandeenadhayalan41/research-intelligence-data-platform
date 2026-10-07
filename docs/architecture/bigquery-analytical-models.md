# BigQuery analytical models (Step 15 / #22)

BigQuery-first analytical **contracts** for OpenAlex. This step defines schemas,
Standard SQL, grains, partition/cluster choices, incremental MERGE strategy,
cost-safety rules, and local DuckDB semantic checks.

## Scope boundary

| Status | What |
| --- | --- |
| **DEFINED IN STEP 15** | Table DDL contracts, query SQL files, pattern mappings, grains, partition/cluster decisions, MERGE/relationship refresh design, cost rules, DuckDB `SEMANTIC_ONLY` fixtures, static offline checks |
| **NOT YET DEPLOYED / MEASURED** | GCP datasets/tables, paid BigQuery jobs, partition pruning proof, clustering effectiveness, real cost/latency, Gold marts (Step 16 / #55) |

No cloud deployment, service accounts, Terraform, or paid queries are included.

## Separation of validation kinds

1. **BigQuery SQL correctness (contract)** — Standard SQL files use backtick
   identifiers and `@named_parameter` syntax; static tests assert file presence,
   parameter form, ACTIVE predicates, and forbidden patterns.
2. **Local semantic validation (`SEMANTIC_ONLY`)** — DuckDB + tiny synthetic
   fixtures prove activity/deletion, citation LEFT JOIN, primary-license, and
   fan-out avoidance semantics.
3. **Future deployed BigQuery validation** — required before production use;
   must measure bytes scanned, pruning, clustering, cost, and latency on real
   (or representative) data. DuckDB does **not** prove those properties.

## Analytical table inventory

Canonical input tables only (Step 11). No invented fields.

| Table | Grain | Partition | Clustering | Incremental |
| --- | --- | --- | --- | --- |
| `works` | one row per `work_id` | `publication_date` (DATE) | `work_id`, `doi`, `primary_publisher_id`, `primary_source_id` | `MERGE_BY_PRIMARY_KEY` |
| `authors` | one row per `author_id` | none | `author_id` | `MERGE_BY_PRIMARY_KEY` |
| `institutions` | one row per `institution_id` | none | `institution_id` | `MERGE_BY_PRIMARY_KEY` |
| `sources` | one row per `source_id` | none | `source_id` | `MERGE_BY_PRIMARY_KEY` |
| `publishers` | one row per `publisher_id` | none | `publisher_id` | `MERGE_BY_PRIMARY_KEY` |
| `topics` | one row per `topic_id` | none | `topic_id` | `MERGE_BY_PRIMARY_KEY` |
| `funders` | one row per `funder_id` | none | `funder_id` | `MERGE_BY_PRIMARY_KEY` |
| `work_authors` | `(work_id, authorship_index)` | `source_updated_date` | `work_id`, `author_id` | `REPLACE_BY_WORK_ID` |
| `work_author_institutions` | `(work_id, authorship_index, institution_index)` | `source_updated_date` | `work_id`, `institution_id` | `REPLACE_BY_WORK_ID` |
| `work_topics` | `(work_id, topic_id)` | `source_updated_date` | `work_id`, `topic_id` | `REPLACE_BY_WORK_ID` |
| `work_keywords` | `(work_id, keyword_id)` | `source_updated_date` | `work_id`, `keyword_id` | `REPLACE_BY_WORK_ID` |
| `work_references` | `(work_id, reference_index)` | `source_updated_date` | `work_id`, `referenced_work_id` | `REPLACE_BY_WORK_ID` |
| `work_mesh` | `(work_id, mesh_index)` | `source_updated_date` | `work_id`, `descriptor_ui` | `REPLACE_BY_WORK_ID` |
| `work_locations` | `(work_id, location_index)` | `source_updated_date` | `work_id`, `source_id` | `REPLACE_BY_WORK_ID` |
| `work_grants` | `(work_id, grant_index)` | `source_updated_date` | `work_id`, `funder_id` | `REPLACE_BY_WORK_ID` |

Ordinal relationship keys are retained for canonical portability (no array-only
collapse).

## Canonical → BigQuery type mapping

| PyArrow (Step 11) | BigQuery |
| --- | --- |
| `string` | `STRING` |
| `int32` / `int64` | `INT64` |
| `float64` | `FLOAT64` |
| `bool` | `BOOL` |
| `date32` | `DATE` |
| UTC `timestamp` | `TIMESTAMP` |

Nullability and lineage fields are preserved:

`source_asset_id`, `source_checksum_sha256`, `source_updated_date`, `run_id`,
`processed_at`, `activity_state`, `deleted_at`.

`works` lists `source_updated_date` once in BigQuery DDL (PyArrow currently
repeats the name in entity + lineage field lists).

## Partition strategy

- **`works.publication_date`**: supports publication/OA trend pruning and
  year-bounded analytical scans. NULL dates use the NULL partition. Incremental
  identity still keys on `work_id` + control-plane changed sets /
  `source_updated_date`, not partition replacement alone.
- **Relationship `source_updated_date`**: lineage freshness of the producing
  asset — **not** Work publication time. Enables prune of recently refreshed
  relationship batches; primary refresh path remains `REPLACE_BY_WORK_ID`.
- **Dimensions**: unpartitioned (small; MERGE by primary key).

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

### Entity tables (`works`, dimensions)

`MERGE` by canonical primary key from a staging table. Deterministic update of
attributes + lineage; insert when absent. Idempotent replay of the same staging
rows converges to the same current projection. Tombstones arrive as rows with
`activity_state = 'DELETED'`.

Representative contract: `sql/bigquery/openalex/models/merge_works.sql`.

### Relationship tables

**Chosen strategy: `REPLACE_BY_WORK_ID`**

1. Identify changed `work_id` set (control/provenance from Steps 10–13).
2. `DELETE` existing relationships for those Works.
3. `INSERT` current projection for those Works from staging.

This avoids stale relationship members that a key-only `MERGE` would leave
behind. Representative contract:
`sql/bigquery/openalex/models/merge_work_topics.sql`.

BigQuery does **not** provide multi-table ACID identical to local Postgres
ingestion. Orchestration must coordinate `works` MERGE + relationship replace
for the same changed `work_id` set and support retry/idempotency by replaying
the same staging projection.

## Fan-out avoidance

Do not flatten `work_authors × work_topics × work_locations × …` at raw grain.

Safe patterns (implemented in query SQL):

- Journal/publisher authors: scope works first, then `COUNT(DISTINCT author_id)`.
- Publisher×topic: `works ⋈ work_topics` only.
- Institution×topic: `DISTINCT` institution/work, then join topics.
- License metrics: primary location CTE, then join topics (not all locations).

Offline DuckDB fixtures demonstrate unsafe authors×topics row inflation versus
safe distinct counts (`SEMANTIC_ONLY`).

## Cost / scan safety

Logical deployment-time rules (enforced when BigQuery execution exists):

- Prefer bounded `@year_from` / `@year_to` (and partition predicates) for trends.
- No `SELECT *` in broad analytical aggregate query contracts.
- Select only needed columns; filter `ACTIVE` early.
- Avoid accidental cross joins / unrestricted full-corpus exploratory scans in
  application contracts.
- Suggested default `maximum_bytes_billed`: **10 GiB** per application query
  (`BigQueryAnalyticalRegistry.maximum_bytes_billed`). Not executed in Step 15.

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
`UNRESOLVED`. Step 15 **preserves** that gap: no `sources.issn` / `sources.eissn`
columns and no fake BigQuery SQL for that pattern.

## Gold marts (out of scope)

Step 16 / #55 owns gold marts, materialized aggregates, reusable publisher/topic
metrics, trend marts, and journal stats. Step 15 may define candidate SQL but
does not claim Gold models are deployed.

## Code and SQL layout

```text
sql/bigquery/openalex/
  ddl/           CREATE TABLE contracts
  models/        active_works view, MERGE / relationship refresh
  queries/       Step 14 pattern SQL (BigQuery Standard SQL)

src/research_platform/analytics/bigquery/
  contracts.py   BigQueryTableContract / type mapping
  registry.py    pattern → query mapping
  validation.py  static checks + DuckDB SEMANTIC_ONLY
```

## Required future real BigQuery validation

Before treating these contracts as production-ready:

1. Deploy DDL to a non-production dataset.
2. Load a bounded representative slice.
3. Measure bytes scanned, slot time, cost, and latency for each pattern.
4. Verify partition pruning and clustering effectiveness.
5. Confirm `maximum_bytes_billed` policy behavior.
6. Only then consider Step 16 materializations from measured reuse.
