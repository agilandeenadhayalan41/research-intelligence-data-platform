# Local one-file OpenAlex Works ingestion (Step 12)

Step 12 implements one bounded local Works ingestion slice behind the Step 10
transaction boundary. It is not end-to-end serving publication, BigQuery load,
GCS landing, Airflow, or multi-file orchestration.

## Dependencies

Steps 05–11 plus an explicit local `ControlStore` / canonical write
implementation. PostgreSQL may back that path later if intentionally selected.
`Warehouse.query` / issue #7 is **not** required for ingestion writes.

## Callable entry point

```bash
python -m research_platform.ingestion.cli --config config/local.yaml
```

Programmatic:

```python
from research_platform.ingestion import run_openalex_works_local_ingest

result = run_openalex_works_local_ingest("config/local.yaml")
```

Requires `environment=local`, `storage.backend=local`,
`sample_selection.max_files=1`, and `max_file_size_bytes <= 25000000`.

Default offline tests inject a fake connector and use:

- `InMemoryControlStore`
- `InMemoryCanonicalStore`
- `LocalObjectStore` under a temp landing path

## Pipeline sequence

```text
manifest discover (metadata only)
  → select ≤1 file / ≤25 MB
  → create PROCESSING PipelineRun
  → register SourceFileControl DISCOVERED
  → claim → PROCESSING (lease + claim_token)
  → stream fetch → hash → put_if_absent (immutable raw + IngestionProvenance)
  → open landed object → stream JSONL.GZ decode
  → map_openalex_work → CanonicalStore.upsert_work_bundle
  → ControlStore.record_provenance (per Work)
  → mark_source_file_success | mark_source_file_failed
  → finish PipelineRun SUCCESS | FAILED
```

## Recovery protocol

| Situation | Behavior |
| --- | --- |
| Identical replay after SUCCESS | `register` → `ALREADY_SUCCESS`; no re-fetch; no duplicate rows |
| Crash after land, before SUCCESS | raw object remains; control may be FAILED or PROCESSING; retry uses explicit FAILED→PROCESSING claim |
| Expired PROCESSING lease | `recover_stale_claim` → FAILED/`STALE_CLAIM`, then retry claim |
| Concurrent second claim | `ClaimConflictError`; only one worker owns the lease |
| Checksum conflict for same asset id | `CHECKSUM_CONFLICT`; never silent overwrite |
| Mid-file decode/canonical failure | file marked FAILED; raw landing kept; run FAILED |
| Canonical Work conflict | `CanonicalConflictError`; no silent last-writer-wins |

Raw landing is immutable and may outlive a failed control completion. Retries
never overwrite raw bytes; `put_if_absent` replays identical content or raises
on conflict.

## Out of scope

Serving publication, DQ orchestration, final `make pipeline`, GCS, BigQuery
physical ingestion, Parquet Works decode in this slice, multi-file runs, and
production deployment.
