# Local one-file OpenAlex Works ingestion (Step 12)

Step 12 implements one bounded local Works ingestion slice behind the Step 10
transaction boundary. It is not end-to-end serving publication, BigQuery load,
GCS landing, Airflow, or multi-file orchestration.

## Dependencies

Steps **05–11** are required, plus an **explicit local `ControlStore` +
canonical write implementation** behind the Step 10 write boundary.

- Issue **#7** (`Warehouse.query` / PostgreSQL query adapter) is **not** a
  mandatory ingestion dependency.
- `Warehouse.query` remains **read-only**.
- Local PostgreSQL may back durable control/canonical writes when explicitly
  selected (`--backend postgres`). That does **not** make PostgreSQL the
  production serving architecture; BigQuery remains the first analytical
  implementation.

## Callable entry point

Persistence mode is **required and explicit**. The CLI never silently uses
fresh in-memory stores while presenting durable ingestion.

```bash
# Ephemeral (tests/demo only; state lost on process exit)
python -m research_platform.ingestion.cli \
  --config config/local.yaml --backend memory

# Durable local Step 12 (requires POSTGRES_DSN + schema apply)
python -m research_platform.ingestion.cli \
  --config config/local.yaml --backend postgres
```

The CLI prints which persistence mode was used (`persistence`, `durable`).

Programmatic:

```python
from research_platform.ingestion import run_openalex_works_local_ingest

result = run_openalex_works_local_ingest(
    "config/local.yaml",
    backend="postgres",  # or "memory"
)
```

Requires `environment=local`, `storage.backend=local`,
`sample_selection.max_files=1`, and `max_file_size_bytes <= 25000000`.

Offline unit tests inject a fake connector and use:

- `InMemoryControlStore` / `InMemoryCanonicalStore` (or `backend="memory"`)
- `LocalObjectStore` under a temp landing path

Durable path: `PostgresControlStore` + `PostgresCanonicalStore` via
`research_platform.persistence.postgres` (optional `.[postgres]` / `psycopg`).
Writes do **not** go through `Warehouse.query()`.

## Pipeline sequence

```text
manifest discover (metadata only)
  → select ≤1 file / ≤25 MB compressed
  → create PROCESSING PipelineRun
  → register SourceFileControl DISCOVERED
       (validate identity + declared size + known checksums)
  → claim → PROCESSING (atomic DB claim / lease + claim_token)
  → stream fetch → hash → put_if_absent (immutable raw + IngestionProvenance)
  → open landed object → stream JSONL.GZ decode (bounded)
  → map all records → CanonicalWorkBundle (+ RecordProvenance rows)
  → BEGIN asset DB transaction:
       re-check claim token / lease
       upsert all canonical records for the asset
       write record provenance
       mark source file SUCCESS
    COMMIT
  → finish PipelineRun SUCCESS | FAILED
```

On mapping/publish failure after claim: mark source file FAILED (separate control
write), finish run FAILED. Raw ObjectStore bytes may remain.

## One-asset database transaction

After immutable raw landing, canonical publication runs in **one explicit
database transaction** (PostgreSQL) or an in-memory rollback emulation for
offline tests:

```text
BEGIN
  SELECT source_files … FOR UPDATE
  re-check claim_token + PROCESSING + non-expired lease
  upsert all canonical records for the asset
  write record provenance
  mark source file SUCCESS
COMMIT
```

If canonical mapping/persistence or provenance fails:

```text
ROLLBACK
```

The database must not retain partial canonical rows or partial provenance from
that failed attempt. Then the control path marks the source file `FAILED`.

### What is intentionally *not* one distributed ACID transaction

| Layer | Durability |
| --- | --- |
| Immutable raw `ObjectStore` landing | Outside the DB transaction; may remain after DB rollback |
| Control + canonical + provenance + SUCCESS | One DB transaction (PostgreSQL) |

Do **not** claim ObjectStore + PostgreSQL are one distributed ACID transaction.

Recovery:

```text
raw exists
+
DB transaction rolled back / source file FAILED
→ explicit retry (FAILED → claim → PROCESSING)
→ immutable raw replay (put_if_absent identical / conflict)
→ database transaction rerun
```

## Transactional claims

PostgreSQL claims use `SELECT … FOR UPDATE` plus lifecycle validation and a
conditional row update. They do **not** rely on `threading.RLock`.

Completion (`SUCCESS` / `FAILED`) requires:

- matching `claim_token`
- `PROCESSING` status
- non-expired lease

Stale recovery is explicit (`recover_stale_claim` → `FAILED` / `STALE_CLAIM`,
then retry claim). Concurrent connections must not both own the same claim
(covered by `make test-postgres-ingestion`).

## Decompression / decode bounds

Compressed source remains `max_file_size_bytes <= 25_000_000`. Independently,
`iter_jsonl_gz_records()` enforces streaming budgets via `JsonlDecodeLimits`:

| Limit | Default | Purpose |
| --- | --- | --- |
| `max_decompressed_bytes` | 100_000_000 | Cap gzip expansion |
| `max_records` | 50_000 | Cap work units per file |
| `max_record_bytes` | 16_000_000 | Cap one JSONL line |

Decode streams chunked accumulation (no full gzip materialization). Explicit
rejects: expansion beyond max decompressed bytes, too many records, oversized
record, truncated gzip, malformed UTF-8, malformed JSON.

## Replay / source identity

OpenAlex snapshot **object identity** is treated as immutable for Step 12.

On registration replay, validate all available identity metadata, including
**declared size** when present on both sides:

| Incoming vs existing | Outcome |
| --- | --- |
| Same identity + identical known metadata + SUCCESS | `ALREADY_SUCCESS` — skip fetch; byte-level revalidation skipped for trusted immutable replay |
| Same identity + changed declared size | `IdempotencyConflictError` (not silent SUCCESS) |
| Same identity + conflicting uri/dates/format | `IdempotencyConflictError` |
| Same identity + known conflicting checksum | `CHECKSUM_CONFLICT` / `ChecksumConflictError` |

Do not silently treat changed declared metadata as `ALREADY_SUCCESS`.

## Recovery protocol

| Situation | Behavior |
| --- | --- |
| Identical replay after SUCCESS | `register` → `ALREADY_SUCCESS`; no re-fetch; no duplicate rows |
| Crash after land, before SUCCESS | raw object remains; control may be FAILED or PROCESSING; retry uses explicit FAILED→PROCESSING claim |
| Expired PROCESSING lease | `recover_stale_claim` → FAILED/`STALE_CLAIM`, then retry claim |
| Concurrent second claim | `ClaimConflictError`; only one worker owns the lease |
| Checksum conflict for same asset id | `CHECKSUM_CONFLICT`; never silent overwrite |
| Mid-file decode failure | file marked FAILED; raw landing kept; run FAILED; no publish txn |
| Mid-publish persistence failure | DB ROLLBACK (no partial canonical/provenance); file FAILED; raw kept |
| Canonical Work conflict | `CanonicalConflictError`; no silent last-writer-wins |

Raw landing is immutable and may outlive a failed control completion. Retries
never overwrite raw bytes. `ObjectStore.put_if_absent` treats identical
content+provenance as a no-op and conflicting state as an error. When a prior
FAILED attempt already landed bytes, Step 12 accepts a content-checksum match
for the same key (retrieval `run_id` in sidecar provenance may differ) and
reruns only the database publication transaction.

## Local PostgreSQL integration tests

Default `make test` stays service-free (`-m "not postgres"`).

When local PostgreSQL is available (`POSTGRES_DSN`):

```bash
make test-postgres-ingestion
# or: docker compose up -d postgres  # when Docker is available
```

These cover durable reconnect, concurrent claim, mid-file rollback, retry,
idempotent replay, version/relationship replacement, provenance uniqueness, and
SUCCESS atomicity. They skip when PostgreSQL/`psycopg` is unavailable.

## Out of scope

Serving publication, DQ orchestration, final `make pipeline`, GCS, BigQuery
physical ingestion, Parquet Works decode in this slice, multi-file runs,
deletion ingestion (#21), Airflow, AACT, ML/AI/KG, and production deployment.
