# Data Service (Step 18 / #26)

Storage-independent consumer domain layer for Research Intelligence capabilities.

**Status:** DEFINED (typed contracts + capability registry + domain service +
in-memory `SEMANTIC_ONLY` reference repository).  
**Not:** DEPLOYED. No FastAPI/Flask/HTTP service. No BigQuery SDK adapter.
No PostgreSQL/AlloyDB serving. No Redis cache. No orchestration.

Authoritative inputs: Step 14 query-pattern registry, Step 15 BigQuery analytical
contracts, Step 16 Gold mart registry, Step 17 quality/publication contracts,
existing Warehouse read interface (adapter later — not leaked here).

---

## Purpose

Expose stable, typed consumer capabilities so callers do **not** need to know
whether data ultimately comes from BigQuery, a materialized Gold table, a cache,
or an optional future operational store.

This is a **domain/API contract layer**, not a network deployment.

---

## Architecture boundary

```text
consumer
   ↓
Data Service capability
   ↓
storage-independent ConsumerDataRepository
   ↓
BigQuery / Gold first          ← intended first production backing (not implemented here)
   ↓
optional cache                 ← implementation option only (not implemented)
   ↓
optional future selective operational projection  ← only if measured (#23); not in Step 18
```

**Quality boundary:** the Data Service consumes only data considered visible after
the Step-17 `PRE_VISIBLE_PUBLICATION` gate. It does **not** run Step-17
orchestration and must not read staged/unapproved consumer outputs through the
normal repository path. Steps 19/20 wire publication flow.

**Consistency:** `PUBLISHED_SNAPSHOT` — responses reflect the currently visible
quality-approved published data state. No claim of cross-request serializable
transactions or multi-capability ACID snapshots.

---

## Package layout

```text
src/research_platform/service/
    __init__.py
    models.py          # envelope, errors, freshness, records, limits
    contracts.py       # typed requests (extra=forbid)
    registry.py        # capability inventory
    repository.py      # ConsumerDataRepository + InMemoryConsumerRepository
    service.py         # DataService domain methods
    pagination.py      # opaque cursor codec
    validation.py      # Step-14/16 alignment checks
```

Reserved `research_platform.serving` remains unused for this domain layer.

---

## Contract version

Stable literal: **`data-service-contract-v1`**

Not derived from date, git SHA, or deployment version. Locked by tests.

---

## Capability inventory

| capability_id | workload | grain | Gold mart | Step-14 patterns | pagination |
|---|---|---|---|---|---|
| `work_metadata_lookup` | `POINT_LOOKUP` | zero or one ACTIVE Work | `research_discovery` | `doi-work-lookup`, `openalex-work-id-lookup` | none |
| `research_discovery` | `PAGED_DISCOVERY` | one ACTIVE Work per item | `research_discovery` | same lookup patterns | cursor |
| `journal_metrics` | `AGGREGATE_LOOKUP` | one row per `source_id` | `journal_author_stats` | `unique-authors-per-journal` | none |
| `publisher_summary` | `AGGREGATE_LOOKUP` | one row per `publisher_id` | `publisher_author_stats` | `unique-authors-per-publisher` | none |
| `publisher_topic_analytics` | `PAGED_ANALYTICS` | `publisher_id` + `topic_id` + `publication_year` | `publisher_topic_year_stats` | `publisher-topic-counts` | cursor |

Every `source_gold_mart` is validated against `load_gold_registry()`.  
Every declared `source_pattern_id` is validated against Step 14.

**Not exposed automatically:** `institution_topic_stats`, `publication_trends`,
`open_access_trends`, `citation_edges`, `publisher_topic_license_year_stats`.

**No ISSN/eISSN capability.** ISSN/eISSN remain unresolved from Steps 14–15.

---

## Request models

Strict Pydantic models with `extra="forbid"`:

- `WorkMetadataRequest` — exactly one of `work_id` / `doi` (non-empty)
- `ResearchDiscoveryRequest` — optional filters + `PageRequest`
- `JournalMetricsRequest` — `source_id`
- `PublisherSummaryRequest` — `publisher_id`
- `PublisherTopicAnalyticsRequest` — `publisher_id` + optional year/topic + page

Unsupported parameters are rejected (not silently ignored).

### Discovery filters (allowlist)

`publication_year_from`, `publication_year_to`, `primary_publisher_id`,
`primary_source_id`, `work_type`, `language`, `oa_status`, `topic_id`,
`institution_id`, `author_id`

No ISSN/eISSN filtering.

### Year range

`1000 <= year <= 3000`, and `from <= to`. No current-year assumptions.

When a year bound is present, NULL-year rows are excluded.  
Without a year filter, NULL-year rows **may** appear (explicit Gold bucket).

---

## Response envelope

```text
ServiceResponse[T]:
  contract_version
  capability_id
  data
  freshness
  consistency   # PUBLISHED_SNAPSHOT
```

No physical storage fields (`bigquery_table`, `postgres_schema`, `duckdb_file`).

Point lookups return `ServiceResponse[Record]` (not paginated).  
List capabilities return `ServiceResponse[Page[T]]`.

### Page contract

```text
Page[T]:
  items          # never NULL; empty page is []
  page.limit
  page.next_cursor   # None when has_more is False
  page.has_more
```

`len(items) <= limit`.

---

## Pagination / cursors

- Technical bounds: `DEFAULT_PAGE_LIMIT = 50`, `MAX_PAGE_LIMIT = 200`
- Invalid limit (`< 1` or `> MAX`) → validation error (`INVALID_ARGUMENT` /
  `LIMIT_EXCEEDED`); no silent clamp
- Opaque cursor codec; payload may include only capability-level sort keys plus
  `capability_id` and `contract_version`
- Reject malformed / wrong-capability / wrong-contract-version cursors with
  `INVALID_CURSOR`
- No SQL text, credentials, or backend state in cursors
- OFFSET is not the primary product contract

### Sort orders

| capability | sort |
|---|---|
| `research_discovery` | `work_id ASC` |
| `publisher_topic_analytics` | `publication_year ASC NULLS LAST`, `topic_id ASC` |

Cursor ordering matches result ordering exactly.

---

## Freshness

```text
FreshnessMetadata:
  status: KNOWN | UNKNOWN
  generated_at?
  source_max_updated_at?
  as_of_run_id?
  notes
```

Repository-supplied evidence is preserved. Missing evidence → `UNKNOWN`.  
Do not invent timestamps. No real-time freshness claim.

---

## ACTIVE / deleted semantics

All normal consumer capabilities operate on **ACTIVE** Works only.

Canonical may retain DELETED rows internally. The Data Service does not expose
deleted Works as normal records and does not reveal tombstone internals through
metadata lookup (`NOT_FOUND` for missing/inactive).

---

## Null / empty semantics

| context | rule |
|---|---|
| Work / discovery scalars | missing → `NULL` |
| relationship arrays | absent → `[]` (never `NULL`) |
| metrics counts | mathematical zero may be `0` |
| missing aggregate entity | `NOT_FOUND` |

Do not fabricate year 0, `"unknown"` IDs, closed OA status, publisher/source IDs,
or ISSN/eISSN.

---

## Safe error model

Codes: `INVALID_ARGUMENT`, `INVALID_CURSOR`, `NOT_FOUND`, `LIMIT_EXCEEDED`,
`UNSUPPORTED_FILTER`, `DATA_UNAVAILABLE`, `BACKEND_ERROR`, `QUALITY_NOT_PUBLISHED`.

Shape: `code`, `message`, `capability_id`, `retryable`, optional `field`.

Consumer-safe messages only. Do not expose SQL text, connection strings, stack
traces, project IDs, sensitive table names, or credentials.

---

## Fan-out prevention

Discovery and analytics consume Step-16 Gold grains with independently
aggregated relationship arrays. The service layer must **not** reconstruct
discovery by joining `work_authors` × `work_topics` ×
`work_author_institutions` × `work_locations`, and must not compute
`COUNT(DISTINCT …)` over accidental fan-out joins.

License (primary-location only) remains a separate mart
(`publisher_topic_license_year_stats`) and is not silently added to
`publisher_topic_analytics`.

---

## BigQuery-first intended backing (logical)

| capability | Gold mart |
|---|---|
| `work_metadata_lookup` | `research_discovery` |
| `research_discovery` | `research_discovery` |
| `journal_metrics` | `journal_author_stats` |
| `publisher_summary` | `publisher_author_stats` |
| `publisher_topic_analytics` | `publisher_topic_year_stats` |

Documented only. Step 18 does not deploy or query BigQuery and makes no
measured performance claims.

First serving approach: **BigQuery + Data Service + optional cache**.  
PostgreSQL/AlloyDB is conditional on later measured evidence (#23) and must not
copy the full analytical model.

---

## No raw SQL product contract

Public `DataService` methods accept domain request models only.  
There is no consumer method equivalent to `execute_sql` / `query(sql)` /
`run_query(text)`. Static tests forbid parameters named `sql`, `query_text`,
`raw_sql`, or `query`.

---

## Reference implementation

`InMemoryConsumerRepository` + `build_reference_fixture()` provide a
deterministic local fixture (`SEMANTIC_ONLY`) covering ACTIVE works, a DELETED
work not exposed, DOI / no-DOI, empty arrays, NULL OA/year, journal/publisher
metrics, multi-row topic analytics, and pagination tie-breaks.

No public network calls. No BigQuery/Postgres requirement.

---

## Capability discovery

`serialize_capability_registry()` returns a deterministic sorted snapshot for
contract metadata (not necessarily a network endpoint).

---

## Future steps (out of scope)

- Step 19 / #27 end-to-end pipeline publication wiring
- Step 20 / #25 Airflow/Composer
- Step 21 / #28 readiness
- #23 PostgreSQL/AlloyDB operational projection
- #9 GCS runtime / #10 BigQuery runtime adapter
- HTTP/gRPC/GraphQL deployment frameworks
- AACT / OpenFDA

Do not start those from this issue.
