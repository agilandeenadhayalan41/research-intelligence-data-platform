# OpenAlex Works deletions (Step 13)

Step 13 applies bounded, idempotent OpenAlex Work deletions while preserving
immutable raw bytes, lineage, and transactional correctness. It does not
implement BigQuery models, GCS, Airflow, or a generic schema-migration system.

## Source representation

Primary deletion source: **`deleted_ids.csv.gz`**.

Repository evidence does not pin a single public OpenAlex URI template for this
file. Step 13 therefore defines an explicit local contract:

| Field | Value |
| --- | --- |
| `source` | `openalex` |
| `entity` | `works-deletions` |
| `content_format` | `csv` (bytes are gzip-compressed) |
| filename | `deleted_ids.csv.gz` |
| URI shape | `s3://openalex/data/csv/works-deletions/.../deleted_ids.csv.gz` |

CSV contract:

- required header column: `id`
- values: OpenAlex Work URL or short `W…` form
- blank rows skipped
- extra columns tolerated after `id`
- IDs normalized with the Step 11 identifier contract (`expected_prefix="W"`)

Asset identity (`oad-…`) includes source, snapshot date, entity, content format,
and file URI — distinct from Works-data (`oa-…`) identities.

## Raw layout

```text
openalex/works-deletions/snapshot_date=YYYY-MM-DD/updated_date=…/
  <asset-id>/source.csv.gz
  <asset-id>/provenance.json
```

Immutable `LocalObjectStore` semantics apply: identical bytes replay;
conflicting bytes for the same key fail. Retries with a new run id may reuse
content-checksum-matching raw objects (same recovery rule as Step 12).

## Callable entry points

```bash
python -m research_platform.ingestion.deletion_cli \
  --config config/local.yaml \
  --backend postgres \
  --local-file /path/to/deleted_ids.csv.gz \
  --file-uri s3://openalex/data/csv/works-deletions/updated_date=2024-01-10/deleted_ids.csv.gz \
  --snapshot-date 2024-01-15 \
  --updated-date 2024-01-10
```

Programmatic: `run_openalex_deletions_local_ingest(..., deletion_asset=..., connector=...)`.

`--backend memory|postgres` is required and printed.

## Deletion event grain

Table `deletion_events` (and in-memory `_deletion_events`) stores:

- `work_id`, `asset_id`, `source_checksum_sha256` (unique grain)
- `run_id`, `source_uri`, `source_updated_date`, `processed_at`
- `outcome`: `DELETED` | `ALREADY_DELETED` | `UNKNOWN_WORK` | `STALE` | `CONFLICT` | `DUPLICATE`

`RecordProvenance` rows with `entity_type=work-deletion` are also written.
Prior Works ingestion provenance is never erased.

## Tombstone semantics

Do **not** physically delete the Work row.

On apply:

- `activity_state = DELETED`
- `deleted_at = processed_at` (deletion processing time)
- lineage fields updated to the deletion asset/checksum/run

Payload fields (title, etc.) remain for audit/explanation.

## Relationship semantics (policy A)

Physical relationship rows owned by the deleted Work (`work_authors`, topics,
locations, …) are **preserved**. Consumer-facing active models must filter
through parent Work `activity_state = ACTIVE` (`is_active_work()`).

Shared entities (authors, institutions, topics, …) are never removed because
one Work is deleted.

## References to deleted Works

If Work A references Work B and B is tombstoned, A's `work_references` row
remains as a source-observed relationship. That is not an orphan error.
Unresolved/external references are unchanged.

## Unknown IDs

Deletion files may list IDs absent from the bounded local sample. Those yield
`UNKNOWN_WORK` without failing the file. Replay remains idempotent.

## Duplicate IDs

Within one file, the first normalized ID performs the deletion action; later
duplicates increment the `duplicates` counter only (no second mutation).

## Version precedence / restore

| Sequence | Result |
| --- | --- |
| Active T1 then deletion T2 | Work tombstoned (`DELETED`) |
| Stale active T1 after deletion T2 | Upsert returns `STALE`; Work stays `DELETED` |
| Newer active after deletion | `RestoreRequiredError` — no silent `DELETED → ACTIVE` |

Step 13 does **not** auto-restore. Explicit restore reconciliation is a later
slice unless OpenAlex semantics later prove an automatic rule.

## Transaction boundary

Same pattern as Step 12:

```text
immutable raw land (outside DB)
BEGIN
  FOR UPDATE claim re-check
  stream deletion IDs
  for each unique ID: tombstone + deletion event + provenance
  mark source SUCCESS
COMMIT
```

Failure at row N → `ROLLBACK` (no partial deletion mutations/events);
raw bytes may remain; source → `FAILED`; retry reuses raw.

ObjectStore + PostgreSQL are not one distributed ACID transaction.

## Counters

`rows_seen`, `unique_ids`, `duplicates`, `deleted`, `already_deleted`,
`unknown`, `stale`, `conflicts` — aggregate only; the whole file's events are
not retained for statistics.

## Active filtering

Use `research_platform.canonical.openalex.activity.is_active_work`.
Consumer-facing active models must exclude `DELETED` Works. BigQuery models are
out of scope here.

## Schema evolution / reprocessing boundary

Step 13 establishes:

- immutable raw enables replay
- version precedence blocks stale overwrite / resurrection
- claim/retry recovers failed assets
- source/schema incompatibility fails explicitly at decode

It does **not** ship a generic schema-migration framework. Broader OpenAlex
schema-evolution support remains a later dedicated slice.

## Out of scope

Step 14 benchmarks (#19), BigQuery models (#22), gold marts (#55), DQ, Data
Service, E2E, Airflow, readiness, GCS, AACT, serving projection (#23).
