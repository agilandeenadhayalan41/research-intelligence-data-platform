# Work lookup projection — #23 PROTOTYPE

> **Status: PROTOTYPE. The #23 gate is NOT met.** This is a local PostgreSQL 16
> prototype built at the operator's explicit request (2026-10-11), ahead of
> the evidence #23 requires. It is not an approved serving store. It changes no
> query-pattern placement (the lookups stay `BIGQUERY_PLUS_CACHE`, `ASSUMED`;
> `OPTIONAL_OPERATIONAL_STORE` still needs MEASURED evidence), it is never
> deployed, and nothing in analytics depends on it. #23 stays open.

## Requirement link

#23 says to link every projection and index to a concrete unmet requirement
and #19 evidence. **There is none yet.**

| Pattern | Projection object | Requirement | Evidence |
| --- | --- | --- | --- |
| `openalex-work-id-lookup` | `serving.work_lookup` primary key (`work_id`) | UNKNOWN: `desired_p95_latency_ms: null` | None. #103 measured BigQuery only, no cache: small-sample synthetic data |
| `doi-work-lookup` | `work_lookup_doi_idx (doi, work_id)` | UNKNOWN: `desired_p95_latency_ms: null` | Same |

The inputs needed to evaluate the gate are listed on #23
([comment](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/23#issuecomment-6114109876)):
a lookup SLA, the consumer, and a BigQuery + API/cache measurement at
representative scale (#108).

## What it is

| Part | Path |
| --- | --- |
| DDL (idempotent) | [`sql/serving/postgres/001_work_lookup.sql`](../../sql/serving/postgres/001_work_lookup.sql) |
| Projection writer + reader | [`src/research_platform/serving/postgres_work_lookup.py`](../../src/research_platform/serving/postgres_work_lookup.py) |
| Offline tests (`make test`) | `tests/unit/test_postgres_work_lookup_projection.py` |
| Local PostgreSQL tests | `tests/integration/test_postgres_work_lookup_integration.py` (`make test-postgres-serving`) |

`PostgresWorkLookupRepository` implements the existing storage-independent
`ConsumerDataRepository` protocol, but serves **only** `get_work_metadata`.
Every other capability raises `DATA_UNAVAILABLE` and points to the
BigQuery-backed repository.

## Projection contract

- **Grain:** one row per ACTIVE Work, holding exactly the `WorkMetadataRecord` fields.
  - No author/topic/institution/citation relationship tables are copied.
  - Multi-valued ids are carried as `text[]` for the lookup payload only.
- **Lineage:** the only input is a published `ConsumerRepositorySnapshot`, the storage-independent consumer view.
  - The projection never reads BigQuery or canonical tables.
  - `serving.work_lookup_state` records the rows fingerprint, row count and the snapshot's freshness (`as_of_run_id`, `generated_at`, `source_max_updated_at`).
- **Update semantics:** `publish(snapshot)` replaces the whole projection in one transaction.
  - Publishers are serialized by a lock on the state table.
  - Readers are not blocked: under MVCC they see the previous snapshot until commit.
  - A snapshot with the same rows fingerprint and freshness is a no-op.
  - A freshness-only change updates the state without rewriting rows.
- **Freshness:** whatever the source snapshot reports. `freshness()` returns it from the state table.
  - The projection lags its source by however often `publish` is run.
  - No refresh schedule exists.
- **Deletion behaviour:** Works in `deleted_work_ids` are left out of the projection, so a lookup of a DELETED Work returns `None` (NOT_FOUND).
  - A Work deleted after a publish stays visible until the next publish.
- **Unpublished:** before the first publish, every lookup and `freshness()` raises `QUALITY_NOT_PUBLISHED`.
- **Lookup patterns** (parameterized only, no raw SQL):
  - `SELECT … WHERE work_id = %s`
  - `SELECT … WHERE doi = %s ORDER BY work_id LIMIT 1`
  - DOI matching is exact: there is no canonicalization beyond the request contract, which trims whitespace.

## Known differences from the in-memory reference

- **Shared DOIs.** A DOI shared by several ACTIVE Works resolves to the lowest
  `work_id`; the in-memory reference returns the first in tuple order. If a
  DOI is shared by a DELETED and an ACTIVE Work, the projection returns the
  ACTIVE one, while the in-memory reference may return `None`. Full-scale DOI
  uniqueness is still UNKNOWN (registry unknowns).
- **Round trips.** Each lookup checks the state row in the same transaction,
  so it costs one extra round trip. That matters only if latency is ever
  measured.

## Operational limitations

- **Local PostgreSQL 16 only.** AlloyDB is PostgreSQL-compatible and the DDL
  uses only core features, but nothing was run on AlloyDB or AlloyDB Omni.
- **Write cost.** A full replace rewrites every row on each changed publish,
  which is fine at fixture and synthetic scale. Full OpenAlex scale would need
  incremental upserts or a table swap. That is not designed here.
- **Not production-ready.** There are no connection pooling, timeouts, metrics
  or HA settings. The reader expects a psycopg connection with
  `autocommit=False`, as `connect_postgres` returns.
- **No production access.** No credentials or DSNs are committed;
  `POSTGRES_DSN` comes from the process environment.

## Recovery

- **The projection is fully derived.** Recovery is to re-apply the DDL
  (`apply_work_lookup_schema`, idempotent) and `publish` the current published
  snapshot again.
- **A failed publish rolls back** and leaves the previous snapshot served.
- **Dropping the `serving` schema loses nothing canonical.**

## Running it locally

```bash
POSTGRES_PASSWORD=<local throwaway> POSTGRES_PORT=5434 docker compose -p rip-serving up -d --wait postgres
POSTGRES_DSN=postgresql://research:<local throwaway>@127.0.0.1:5434/research make test-postgres-serving
```

The tests drop and recreate the `serving` schema of that database, so point
them at a local throwaway database only.

## Recorded run (2026-10-11)

- **Environment:** `postgres:16` from `docker-compose.yml` (project
  `rip-serving`, port 5434, WSL), psycopg 3.2.13.
- **Result:** 20 passed. That is 11 offline unit tests and 9 integration
  tests: reference parity, unpublished refusal, deletion and shared DOI, no-op
  and deletion republish, freshness-only update, rollback on a failed publish,
  MVCC read isolation, parameterization and the backend error mapping.
- **Not measured:** latency, concurrency and scale.
