# OpenAlex Works deletions (Step 13)

Step 13 applies bounded, idempotent OpenAlex Work deletions while preserving
immutable raw bytes, lineage, and transactional correctness. It does not
implement BigQuery models, GCS, Airflow, or a generic schema-migration system.

## Authoritative public source

OpenAlex documents the Works deletion ledger next to the Works manifest
([Sync / deletions](https://help.openalex.org/access/sync/)):

| Field | Value |
| --- | --- |
| Canonical physical URI | `s3://openalex/data/jsonl/works/deleted_ids.csv.gz` |
| Also published under | `s3://openalex/data/parquet/works/deleted_ids.csv.gz` (same file; **not** used as an alternate identity here) |
| Logical control entity | `works-deletions` (distinct from Works-data `works`) |
| `content_format` | `csv` (bytes are gzip-compressed) |
| Asset id prefix | `oad-…` |

Logical entity ≠ physical URI. Control identity uses `works-deletions`; the
public path remains under `data/jsonl/works/`.

### CSV contract

```csv
work_id,deleted_date
https://openalex.org/W4245566371,2026-08-14
```

- Header must be exactly `work_id,deleted_date` (strict; unexpected schema fails)
- `work_id`: OpenAlex Work URL or short `W…` form (Step 11 normalization)
- `deleted_date`: exact `YYYY-MM-DD` (per-row source evidence for precedence)
- Blank rows skipped
- Extra/missing columns fail explicitly

### Cumulative ledger

`deleted_ids.csv.gz` is a **cumulative** Works deletion ledger, not a daily
delta. The latest snapshot file is the whole truth. Public size is roughly
**160 MB compressed / tens of millions of rows**.

This local Step 13 runner **does not** ingest the full public ledger. Default
local safety remains `max_file_size_bytes <= 25_000_000`. Full-scale ledger
ingestion requires a later explicitly approved scalable/cloud path.

Automatic restoration when a Work leaves the ledger is **out of scope**. Newer
active observations after a tombstone still raise `RestoreRequiredError`.

## Raw layout

```text
openalex/works-deletions/snapshot_date=YYYY-MM-DD/updated_date=…/
  <asset-id>/source.csv.gz
  <asset-id>/provenance.json
```

Immutable `LocalObjectStore` semantics apply. Retries with matching content
checksum may reuse raw objects (same recovery rule as Step 12).

## Callable entry points

```bash
python -m research_platform.ingestion.deletion_cli \
  --config config/local.yaml \
  --backend postgres \
  --local-file /path/to/deleted_ids.csv.gz \
  --snapshot-date 2024-01-15
```

Default `--file-uri` is the public JSONL Works path. The CLI opens the local
file in `rb` mode (caller-owned stream); it does **not** `read_bytes()` the
entire payload into memory.

Programmatic: `run_openalex_deletions_local_ingest(..., deletion_asset=..., connector=...)`.

## Deletion event grain

Table `deletion_events` stores:

- `work_id`, `asset_id`, `source_checksum_sha256` (unique grain)
- `deleted_date` (**required** per-row OpenAlex source deletion date)
- optional file-level `source_updated_date` when known for the asset
- `run_id`, `source_uri`, `processed_at`
- `outcome`: `DELETED` | `ALREADY_DELETED` | `UNKNOWN_WORK` | `STALE` | `CONFLICT` | `DUPLICATE`

`RecordProvenance` rows with `entity_type=work-deletion` set
`source_updated_date` to the row’s `deleted_date`. Prior Works ingestion
provenance is never erased.

## Tombstone semantics

Do **not** physically delete the Work row.

On apply:

- `activity_state = DELETED`
- `CanonicalLineage.source_updated_date = deleted_date` (source calendar date for
  precedence; no fabricated timestamp)
- `deleted_at = processed_at` (processing time)
- lineage asset/checksum/run updated to the deletion observation

## Version precedence

| Sequence | Result |
| --- | --- |
| Active T1, deletion T2 (`T2 > T1`) | Tombstone (`DELETED`) |
| Active T3, deletion T2 (`T2 < T3`) | `STALE` — Work stays `ACTIVE` |
| Active T2, deletion T2 | Deletion wins (`DELETED`) |
| DELETED T2, same deletion T2 | `ALREADY_DELETED` |
| DELETED T3, older deletion T2 | `STALE` |
| Stale active T1 after deletion T2 | Upsert `STALE`; Work stays `DELETED` |
| Newer active after deletion | `RestoreRequiredError` — no silent restore |

Missing Work version date while ACTIVE → conservative `STALE` (do not destroy
state when ordering cannot be proven).

## Relationship / reference semantics

Policy A: physical relationship rows owned by the deleted Work are preserved;
filter via parent Work `activity_state = ACTIVE` (`is_active_work()`).

Shared entities are never removed because one Work is deleted.

Active Work → deleted target references remain as source observations.

## Unknown / duplicate IDs

Unknown IDs → `UNKNOWN_WORK` without failing the file (bounded local sample).

Same `work_id` + same `deleted_date` → one mutation; later rows count as
`duplicates`.

Same `work_id` + conflicting `deleted_date` → explicit decode failure (source
inconsistency); transaction rolls back.

## Transaction boundary

```text
immutable raw land (outside DB; streamed, not fully buffered by the CLI)
BEGIN
  FOR UPDATE claim re-check
  stream deletion rows
  for each unique work_id: precedence + tombstone + event + provenance
  mark source SUCCESS
COMMIT
```

Failure at row N → `ROLLBACK`; raw bytes may remain; source → `FAILED`; retry.

ObjectStore + PostgreSQL are not one distributed ACID transaction.

## Counters

`rows_seen`, `unique_ids`, `duplicates`, `deleted`, `already_deleted`,
`unknown`, `stale`, `conflicts`.

## Active filtering

Use `research_platform.canonical.openalex.activity.is_active_work`.
Consumer-facing active models must exclude `DELETED` Works.

## Schema evolution / reprocessing boundary

Step 13 establishes immutable replay, per-row deletion precedence, claim/retry,
and explicit decode failures. It does **not** ship a generic schema-migration
framework or full-ledger cloud execution.

Local PostgreSQL `apply_ingestion_schema` may add `deletion_events.deleted_date`
to empty legacy tables. It **never** fabricates `deleted_date` from file-level
`source_updated_date` or a sentinel date. Legacy rows lacking a trustworthy
`deleted_date` fail migration explicitly; recover by truncating/replaying
immutable raw `deleted_ids.csv.gz` under the `work_id,deleted_date` contract.

## Out of scope

Step 14 benchmarks (#19), BigQuery models (#22), gold marts (#55), DQ, Data
Service, E2E, Airflow, readiness, GCS, AACT, serving projection (#23).
