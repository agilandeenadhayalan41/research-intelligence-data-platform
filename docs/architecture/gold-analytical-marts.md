# Gold analytical marts (Step 16 / #55)

Consumer-oriented Gold mart contracts for OpenAlex Research Intelligence.

**Status:** DEFINED (contracts + BigQuery Standard SQL + DuckDB `SEMANTIC_ONLY` fixtures).  
**Not:** DEPLOYED / MEASURED. No GCP resources. No production materialization.

Authoritative inputs: Step 11 canonical model, Step 13 ACTIVE/deletion Policy A, Step 14 query-pattern registry, Step 15 BigQuery analytical contracts.

---

## Inventory

| mart_id | Grain | Step 14 pattern(s) | Mode |
|---|---|---|---|
| `research_discovery` | `work_id` | doi-work-lookup, openalex-work-id-lookup | COMPUTE_ON_READ |
| `journal_author_stats` | `source_id` | unique-authors-per-journal | MATERIALIZATION_CANDIDATE |
| `publisher_author_stats` | `publisher_id` | unique-authors-per-publisher | MATERIALIZATION_CANDIDATE |
| `publisher_topic_year_stats` | `publisher_id + topic_id + publication_year` | publisher-topic-counts (year grain; Step 14 notes intentional rollup) | MATERIALIZATION_CANDIDATE |
| `publisher_topic_license_year_stats` | `publisher_id + topic_id + primary_location_license + publication_year` | publisher-topic-license-year | MATERIALIZATION_CANDIDATE |
| `institution_topic_stats` | `institution_id + topic_id` | institution-topic-relationships | COMPUTE_ON_READ |
| `publication_trends` | `publication_year` | publication-trends | MATERIALIZATION_CANDIDATE |
| `open_access_trends` | `publication_year + oa_status` | open-access-trends | MATERIALIZATION_CANDIDATE |
| `citation_edges` | `source_work_id + reference_index` | citation-relationships | COMPUTE_ON_READ |

SQL contracts live under `sql/bigquery/openalex/gold/`.  
Typed registry: `src/research_platform/analytics/gold/`.

---

## Materialization honesty

Modes:

- **COMPUTE_ON_READ** — query analytical tables at read time.
- **MATERIALIZATION_CANDIDATE** — Step 14 placement/candidate suggests reuse value; **not** physically materialized.
- **MATERIALIZED** — requires `evidence_status=MEASURED` (scan bytes, latency, frequency, concurrency, reuse vs refresh). **None** in Step 16.

Promotion rule:

> `MATERIALIZATION_CANDIDATE` → `MATERIALIZED` only when MEASURED BigQuery evidence shows significant repeated-query cost/latency/frequency/concurrency/reuse **and** refresh cost/freshness remains acceptable. FIXTURE_ONLY / SEMANTIC_ONLY / preference alone cannot promote.

Current evidence available: architecture expectations, FIXTURE_ONLY local timing, DuckDB SEMANTIC_ONLY.  
Current evidence **not** available: production BigQuery scan/latency/concurrency/frequency/refresh cost.

---

## ACTIVE / deleted semantics

Every consumer Gold mart filters `works.activity_state = 'ACTIVE'` (or joins through ACTIVE parents).

Step 13 Policy A remains authoritative: physical relationship rows for deleted Works stay in canonical/analytical storage. Gold excludes them by filtering the parent Work. Gold refresh must **not** physically delete those canonical relationships.

---

## Fan-out safety

Preferred pattern: aggregate multi-valued dimensions **independently**, then join at Work (or mart) grain.

- `research_discovery`: `authors_agg` / `topics_agg` / `institutions_agg` CTEs (deterministic `ARRAY_AGG(... ORDER BY ...)`), never authors × topics × institutions × locations. Absent relationship aggregates `COALESCE` to empty `ARRAY<STRING>[]` (never NULL arrays).
- `journal_author_stats` / `institution_topic_stats`: `DISTINCT` work grain before author/topic aggregation.
- `publisher_topic_year_stats`: works ⋈ topics only.
- `publisher_topic_license_year_stats`: primary location CTE, then topics — never `MIN/MAX/ANY_VALUE(license)`.

`COUNT(DISTINCT ...)` after a Cartesian relationship join is **not** a substitute for correct grain design: it may hide inflation in intermediate row explosion and still burn scan/shuffle cost.

---

## Citation semantics

`citation_edges`:

- Source Work must be ACTIVE.
- Target via `LEFT JOIN` — ACTIVE, DELETED, or unresolved/absent.
- Preserve `referenced_work_id`, `reference_status`, optional `target_activity_state`.
- Do not pre-aggregate the full citation network without MEASURED need (default COMPUTE_ON_READ).

---

## Primary-location license

`publisher_topic_license_year_stats` uses `work_locations.is_primary = TRUE` only.  
NULL primary license → explicit NULL / UNKNOWN bucket. Never fabricate a license string.

---

## Null / gap handling

| Gap | Gold behavior |
|---|---|
| missing DOI / title | NULL |
| missing publication_year | explicit NULL year bucket (not year 0) |
| missing publisher / source / topic / author / institution | excluded from that dimension grain; discovery arrays omit NULLs; discovery returns `[]` (not NULL) when a Work has no observations |
| missing OA status / is_oa | NULL preserved; do not invent `closed` |
| missing primary license | NULL license bucket |
| unresolved citation target | retained via LEFT JOIN |

Metric zeros are used only when mathematically correct (e.g. source with works but zero authors → `unique_author_count = 0`).

---

## Refresh strategy

Safe contract: **recompute impacted Gold grains/partitions** from current canonical analytical state. Avoid unsafe counter delta arithmetic unless old and new dimensions are both reliably known.

| Mart class | Refresh |
|---|---|
| discovery | recompute changed ACTIVE work rows |
| year / OA trends | rebuild impacted `publication_year` buckets |
| publisher-topic(-license)-year | rebuild impacted publisher/year grains |
| journal / publisher author | recompute affected source/publisher groups |
| institution-topic | recompute affected institution groups |
| citations | recompute edges for changed ACTIVE source works |

Dimension moves (publisher/year/topic/source) must repair **both** old and new buckets. Deletion transitions drop tombstoned Works from Gold outputs while leaving canonical relationships stored.

`GoldRefreshImpact` is a logical contract for old/new dimension capture. When old-state evidence is unavailable, bounded partition/group recomputation is the safe implementation. Step 20 owns orchestration/scheduling.

---

## Cost / scan guidance (documented only)

Inherit Step 15 rules. No tables deployed.

- Year-based marts: partition candidate `publication_year` (INTEGER_RANGE alignment).
- Publisher/topic/year: partition `publication_year`; cluster `publisher_id`, `topic_id`.
- OA trends: partition `publication_year`; `oa_status` clustering only if later MEASURED.
- Small source/publisher aggregates: do not partition merely to have a partition.

---

## Freshness / lineage

Gold aggregates need not copy every canonical lineage column. Document derivation:

Gold mart → exact input tables → SQL contract → refresh strategy.

Optional freshness metadata concept: `generated_at`, `source_max_lineage_source_updated_date` — contract metadata only; static SQL does not invent timestamps.

---

## DuckDB SEMANTIC_ONLY limits

Offline validation proves relationship grains, ACTIVE filtering, license/citation/null semantics, and fan-out safety on synthetic fixtures.

It does **not** prove: BigQuery syntax acceptance, partition pruning, clustering effectiveness, optimizer plans, scan bytes, latency, or concurrency.

Label results `SEMANTIC_ONLY`, never `MEASURED_BIGQUERY`.

---

## Step 20 concurrency note

Shared Step 15 publication decision table names remain contract addresses only. Concurrent jobs need run-scoped/TEMP tables or single-writer orchestration — Step 20 responsibility. Gold does not solve this.

---

## Out of scope

End-to-end pipeline (#27; [details](end-to-end-pipeline.md)), orchestration (#25), readiness (#28), PostgreSQL/AlloyDB serving (#23), GCS/Terraform/cloud deploy, AACT/OpenFDA, ML/vector/KG. Data quality (#24) and Data Service (#26) are complete.
