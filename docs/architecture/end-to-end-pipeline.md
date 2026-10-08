# Bounded end-to-end pipeline (Step 19 / #27)

**Status:** IMPLEMENTED (local `SEMANTIC_ONLY` composition; correctness hardening in flight).  
Package: `research_platform.e2e` — bounded runner, publication store, CLI.  
**Not:** BigQuery runtime, GCP deploy, Airflow/Composer (Step 20 / #25), or
readiness (Step 21 / #28). Evidence label is always `SEMANTIC_ONLY`.

### Unknown-work deletion barrier

When a deletion arrives for a Work that is not yet in canonical storage, the
in-memory store retains an authoritative `(work_id, deleted_date)` barrier
(not a fabricated bibliographic Work). Later ACTIVE upserts with
`source_updated_date <= deleted_date` are `STALE`; newer dates raise
`RESTORE_REQUIRED`. Barriers participate in deletion-publish rollback.

Postgres consults `MAX(deleted_date)` from `deletion_events` for the same
insert-time check (parity with the in-memory barrier).

**Prerequisite:** Steps 01–18 are complete. Authoritative contracts already exist
for discovery/selection, immutable landing, canonicalization, deletions,
analytical/Gold models, data-quality gates, and the storage-independent
Data Service. Step 19 wires those stages into one bounded OpenAlex Works path.

Issue: [#27](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/27)  
Umbrella: [#2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2) (remains OPEN)

---

## Goal

Deliver a **bounded** end-to-end pipeline that exercises the full Research
Intelligence path for OpenAlex Works under an explicit sample limit
(default `MAX_FILES=1`), ending in quality-approved consumer publication and
final validation.

Step 19 composes existing step contracts. It does not redefine Gold grains,
quality gates, or Data Service capabilities.

---

## Pipeline flow

```text
discover
  ↓
select
  ↓
ingest
  ↓
immutable landing
  ↓
canonicalize
  ↓
changes/deletions
  ↓
analytical models
  ↓
data-quality gate
  ↓
Gold outputs
  ↓
consumer publication
  ↓
final validation
```

| Stage | Role | Primary prior steps |
|---|---|---|
| discover | Manifest / source discovery | 05, 06 |
| select | Deterministic bounded sample selection | 07 |
| ingest | Claimed retrieval into the pipeline control plane | 10, 12 |
| immutable landing | Atomic raw content + provenance publish | 03, 09, 12 |
| canonicalize | Map/upsert ACTIVE canonical entities & relationships | 11, 12 |
| changes/deletions | Apply Works tombstones / Policy A precedence | 13 |
| analytical models | Build analytical projections from canonical | 15 |
| data-quality gate | `PRE_SERVING_BUILD` then staged Gold checks → `PRE_VISIBLE_PUBLICATION` | 17 |
| Gold outputs | Fan-out-safe consumer marts (staged until approved) | 16 |
| consumer publication | Make quality-approved published view visible to Data Service | 17, 18 |
| final validation | Post-publication checks that consumer-visible state is coherent | 17, 18 |

---

## Quality / publication boundary

Consumer-visible data must pass the Step-17 publication boundary:

```text
canonical checks
      ↓
PRE_SERVING_BUILD
      ↓
analytical / Gold build (staged)
      ↓
PRE_VISIBLE_PUBLICATION
      ↓
consumer publication (Data Service reads PUBLISHED_SNAPSHOT only)
      ↓
final validation
```

The Data Service (Step 18) does **not** orchestrate these stages. It consumes
only the currently visible quality-approved published snapshot.

---

## Bounds and guardrails (planned)

- Default `MAX_FILES=1` (or equivalent strict sample bound)
- Offline/synthetic fixtures preferred for CI; no implied paid BigQuery runs
- No unrestricted raw SQL product endpoint
- No silent data repair inside quality gates
- No automatic start of Step 20 (Airflow/Composer) or Step 21 (readiness)
- PostgreSQL/AlloyDB operational projection remains conditional (#23), not required for Step 19
- GCS runtime (#9) and BigQuery runtime adapter (#10) remain separately scoped unless explicitly included by #27 acceptance

---

## Related architecture docs

| Topic | Doc |
|---|---|
| Roadmap | [roadmap.md](roadmap.md) |
| Pipeline control | [pipeline-control.md](pipeline-control.md) |
| Local Works ingestion | [local-works-ingestion.md](local-works-ingestion.md) |
| OpenAlex deletions | [openalex-deletions.md](openalex-deletions.md) |
| BigQuery analytical models | [bigquery-analytical-models.md](bigquery-analytical-models.md) |
| Gold marts | [gold-analytical-marts.md](gold-analytical-marts.md) |
| Data quality | [data-quality.md](data-quality.md) |
| Data Service | [data-service.md](data-service.md) |

---

## Out of scope for this document

This file records the Step 19 target flow only. It does **not**:

- implement the pipeline runner
- deploy GCP resources
- add Airflow/Composer DAGs
- expand the Data Service HTTP surface
- close umbrella #2
