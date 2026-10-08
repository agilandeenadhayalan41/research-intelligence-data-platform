# Bounded end-to-end pipeline (Step 19 / #27)

**Status:** IMPLEMENTED (local `SEMANTIC_ONLY` composition).  
Package: `research_platform.e2e` — bounded runner, publication store, CLI,
safe failure summaries with recovery guidance.  
**Not:** BigQuery runtime, GCP deploy, Airflow/Composer (Step 20 / #25), or
readiness (Step 21 / #28). Evidence label is always `SEMANTIC_ONLY`.

### Implementation status

The Step-19 runner is implemented and exercised under `make test` as a local
DuckDB `SEMANTIC_ONLY` path. It composes reusable Steps 12–18 stages under one
outer `PipelineRun`. It does **not** claim MEASURED BigQuery evidence or deploy
cloud resources.

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

Issue: [#27](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/27) (CLOSED — complete)  
Umbrella: [#2](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/2) (remains OPEN; Step 20 / #25 next)

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

All stages share one outer Step-19 `PipelineRun` (`run_id`) and an explicit
`publication_version` chosen at run start (default `pub-{run_id}`).

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

Two quality gates are required before activation:

1. **PRE_SERVING_BUILD** — after analytical projection, before Gold staging  
2. **PRE_VISIBLE_PUBLICATION** — after staged Gold, before consumer activation  

Activation is atomic via `PublicationStore.activate()`. Final validation runs
after activation and is fail-closed:

- `ok=False` **or** unexpected exception → restore previous current publication  
  (or clear current when there was no previous publication)  
- PipelineRun finishes `FAILED`; the failed candidate is never left consumer-visible  

Same `publication_version` + same content fingerprint is pipeline-idempotent:
`stage()` returns the existing candidate; activation/validation use that
snapshot’s original provenance/freshness (not re-stamped). Same version +
different content raises `PublicationConflictError` and leaves old current
unchanged.

The Data Service (Step 18) does **not** orchestrate these stages. It consumes
only the currently visible quality-approved published snapshot.

---

## Bounds and guardrails

- Strict `EndToEndBounds`: `max_files` and `max_file_size_bytes` are strict
  integers (no string/float coercion); defaults `1` / `25_000_000`
- Config validated before discovery: `environment=local`, `storage.backend=local`,
  and sample selection within bounds
- Over-bound connector selection fails closed (no silent slice); fetch never runs
- Offline/synthetic fixtures preferred for CI; no implied paid BigQuery runs
- No unrestricted raw SQL product endpoint
- No silent data repair inside quality gates
- Safe structured failure summaries include typed `recovery_action` guidance
  (no DSN, SQL, credentials, payload content, private paths, or raw exception text)
- No automatic start of Step 20 (Airflow/Composer) or Step 21 (readiness)
- PostgreSQL/AlloyDB operational projection remains conditional (#23), not required for Step 19
- GCS runtime (#9) and BigQuery runtime adapter (#10) remain separately scoped

### SEMANTIC_ONLY limitation

Local evidence builds DuckDB analytical/Gold marts and validates Data Service
contracts against an in-memory published snapshot. It does **not** execute or
measure BigQuery/GCP workloads. Do not treat green Step-19 CI as MEASURED
warehouse readiness.

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

This file describes the implemented local Step-19 path. It does **not**:

- deploy GCP resources
- add Airflow/Composer DAGs
- expand the Data Service HTTP surface
- claim BigQuery MEASURED evidence
- close umbrella #2
