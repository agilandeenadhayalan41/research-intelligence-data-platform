# Pipeline control and provenance contracts (Step 10)

Step 10 defines **modeling and write-boundary contracts** for later ingestion.
It does not open database connections, run migrations, claim work, or land data.

| Concern | Module / artifact |
| --- | --- |
| Mutable run + source-file control models | `research_platform.control.models` |
| Status transitions, claims, retries | `research_platform.control.lifecycle` |
| Registration idempotency / checksum conflicts | `research_platform.control.reconciliation` |
| Persistence protocol (unimplemented) | `research_platform.control.store.ControlStore` |
| Immutable retrieval provenance | existing `IngestionProvenance` |
| Record-level lineage model | `RecordProvenance` |
| Versioned DDL specification | [`sql/control/001_pipeline_control.sql`](../../sql/control/001_pipeline_control.sql) |

## Immutable retrieval provenance vs mutable control state

```mermaid
flowchart TB
    subgraph immutable [Immutable]
        Raw["ObjectStore raw bytes"]
        IP["IngestionProvenance / provenance.json"]
        Raw --- IP
    end
    subgraph mutable [Mutable control plane]
        Run["PipelineRun"]
        File["SourceFileControl"]
        RP["RecordProvenance"]
        Run --> File
        File --> RP
    end
    IP -. "run_id + sha256 align" .-> File
    File -. "raw_object_key" .-> Raw
```

- **`IngestionProvenance`** is written with raw bytes and never overwritten. It
  records `run_id`, `source`, `source_uri`, `retrieved_at`, and content `sha256`.
- **`SourceFileControl`** is mutable workflow state: discovery, claim/lease,
  attempts, success/failure diagnostics, and the landed `raw_object_key`.
- **`RecordProvenance`** links a canonical record id to the source asset,
  checksum, run, and processing time. It does not store source payloads.

## Source-file lifecycle

States: `DISCOVERED`, `PROCESSING`, `SUCCESS`, `FAILED`.

```mermaid
stateDiagram-v2
    [*] --> DISCOVERED: register_source_file
    DISCOVERED --> PROCESSING: claim
    PROCESSING --> SUCCESS: mark success + current claim
    PROCESSING --> FAILED: mark failed + current claim
    FAILED --> PROCESSING: explicit retry claim
    SUCCESS --> [*]
```

| Transition | Event | Attempt counter |
| --- | --- | --- |
| DISCOVERED → PROCESSING | `claim` | set to `1` |
| PROCESSING → SUCCESS | `success` | unchanged |
| PROCESSING → FAILED | `fail` | unchanged |
| FAILED → PROCESSING | `retry` | `attempt_count + 1` |

Pipeline-run attempts:

| Transition | Helper / event | `attempt` |
| --- | --- | --- |
| PENDING → PROCESSING | `apply_pipeline_run_start` / `start` | unchanged (normally `1`) |
| PROCESSING → SUCCESS\|FAILED | `apply_pipeline_run_finish` | unchanged |
| FAILED → PROCESSING | `apply_pipeline_run_retry` / `retry` | `attempt + 1` |

Ordinary start must not silently retry a FAILED run. `SUCCESS` remains terminal.

Illegal examples (rejected by contract helpers):

- DISCOVERED → SUCCESS
- SUCCESS → PROCESSING / FAILED
- FAILED → PROCESSING without the explicit `retry` event
- FAILED → `apply_pipeline_run_start` (must use `apply_pipeline_run_retry`)
- SUCCESS → retry
- PENDING → retry

`SUCCESS` is terminal. Status field edits alone must never imply successful
canonical publication; `ControlStore.mark_source_file_success` requires the
current claim token and is intended to run inside the ingestion transaction
boundary after durable side effects.

Lifecycle helpers rebuild results through `model_validate(...)` so every returned
model satisfies the same field/model validators as direct construction.
`model_copy(update=...)` is not used for unvalidated transitions.

## Claim / lease sequence

```mermaid
sequenceDiagram
    participant W1 as Worker A
    participant CS as ControlStore
    participant W2 as Worker B
    W1->>CS: claim_source_file(asset, tokenA, lease)
    CS-->>W1: PROCESSING owned by A
    W2->>CS: claim_source_file(asset, tokenB, lease)
    CS-->>W2: ClaimConflictError (valid lease)
    Note over CS: lease expires
    W2->>CS: recover_stale_claim(asset, now)
    CS-->>W2: FAILED / STALE_CLAIM
    W2->>CS: claim_source_file(... retry ...)
    CS-->>W2: PROCESSING owned by B
    W2->>CS: mark_source_file_success(tokenB, ...)
    CS-->>W2: SUCCESS
```

Rules:

- Only one worker owns an active `PROCESSING` claim.
- Acquisition must be atomic (conditional update / equivalent).
- `claim_token` + `claimed_by` identify ownership.
- Completion requires the current token and a non-expired lease.
- Stale leases are recovered explicitly to `FAILED` (`STALE_CLAIM`); another
  worker cannot silently steal a still-valid claim.
- Retries increment `attempt_count`.

## Registration idempotency

| Situation | Outcome |
| --- | --- |
| New asset | `CREATED` |
| Same identity + same/unknown checksum + DISCOVERED | `IDEMPOTENT_REPLAY` |
| Same identity + SUCCESS + same checksum | `ALREADY_SUCCESS` |
| Same identity + PROCESSING | `ALREADY_IN_PROGRESS` |
| Same identity + FAILED | `RETRYABLE_FAILURE` |
| Same identity + different known checksum | `CHECKSUM_CONFLICT` |

A changed checksum for the same immutable asset identity is a reconciliation
conflict, never a silent overwrite.

## Lineage

```text
OpenAlexAssetMetadata.asset_id
  -> SourceFileControl (mutable)
  -> ObjectStore key (immutable raw + IngestionProvenance)
  -> RecordProvenance (canonical record_id + asset_id + checksum + run_id)
```

## Write / transaction boundary

`Warehouse.query()` stays read-only. Control writes use `ControlStore`:

- `create_pipeline_run` / `finish_pipeline_run` / `retry_pipeline_run`
- `register_source_file`
- `claim_source_file`
- `mark_source_file_success` / `mark_source_file_failed`
- `recover_stale_claim`
- `record_provenance`

Temporal invariants (when timestamps are present):

- `PipelineRun`: `created_at <= started_at <= completed_at <= updated_at`
- `SourceFileControl`: `created_at <= claimed_at < lease_expires_at` and
  `created_at <= processed_at <= updated_at`

DDL CHECK constraints mirror the important orderings; they do not encode the
full transition table. Transactional `ControlStore` operations still own
lifecycle correctness.

Step 12 local ingestion boundary for one asset (durable PostgreSQL path):

```text
register / claim_source_file          # atomic DB claim (FOR UPDATE)
retrieve -> put_if_absent (ObjectStore)   # outside DB txn; immutable
map all records from landed raw
begin
  re-check claim token / lease
  canonical upsert*
  record_provenance*
  mark_source_file_success
commit
```

On failure after claim: DB ROLLBACK of the publish unit, then
`mark_source_file_failed`. Raw ObjectStore bytes may remain; ObjectStore +
PostgreSQL are **not** one distributed ACID transaction.

`InMemoryControlStore` remains for offline tests. Durable local writes use
`PostgresControlStore` (not `Warehouse.query`). `UnimplementedControlStore` is
still the fail-fast skeleton when no adapter is selected.

## DDL / backend limitations

[`sql/control/001_pipeline_control.sql`](../../sql/control/001_pipeline_control.sql)
targets **PostgreSQL 16+**. It specifies primary keys, foreign keys, unique
indexes, status/claim/checksum checks, and useful indexes. Default tests only
assert the file’s contractual contents; they do **not** execute migrations.
DuckDB or mock checks do not prove PostgreSQL or BigQuery dialect compatibility.

## Out of scope for Step 10

Canonical OpenAlex modeling (#15), ingestion (#20), deletions (#21), GCS (#9),
warehouse adapters, Airflow, cloud resources, and credentials.
