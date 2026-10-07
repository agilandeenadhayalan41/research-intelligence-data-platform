# Immutable local raw landing

Step 09 implements `LocalObjectStore` and the OpenAlex raw key layout. The GCS
adapter remains a fail-fast skeleton (`#9`). Canonical transforms and ingestion
orchestration are later steps.

## Preserve source bytes

Immutable raw landing stores the exact bytes received from the source connector:

| Declared OpenAlex format | Raw object basename | Bytes |
| --- | --- | --- |
| `jsonl` | `source.gz` | Original gzip JSONL payload |
| `parquet` | `source.parquet` | Original Parquet payload |

JSONL.GZ is never rewritten as Parquet in this layer. Analytical Parquet
conversion belongs to a later canonical/analytical step.

## OpenAlex raw key layout

```text
openalex/
  <entity>/
    snapshot_date=YYYY-MM-DD/
      updated_date=YYYY-MM-DD|none/
        <asset-id>/
          source.<actual-extension>
          provenance.json
```

- Keys are built from validated `OpenAlexAssetMetadata` via
  `openalex_raw_object_key()`.
- `asset-id` is the existing stable `oa-<sha256>` identity.
- When `updated_date` is absent, the partition is always `updated_date=none` so
  hierarchy depth never changes.
- Caller-provided URI text is never used as a filesystem path.

## Object directory commit

Each logical object is one directory containing:

1. `source.<ext>` — streamed source bytes
2. `provenance.json` — deterministic JSON for `IngestionProvenance`

`LocalObjectStore.put_if_absent(key, content, provenance)` takes the **content**
key (`.../source.gz`). Provenance is always the sibling `provenance.json`.

### Atomic publication

1. Stream into `landing/.staging/<op-uuid>/<basename>` while computing SHA-256.
2. Reject checksum mismatch before any committed path is touched.
3. Write deterministic `provenance.json` beside the staged content.
4. `fsync` staged files and the staging directory.
5. `os.rename` the staging directory onto the final object directory.

On Linux, directory rename to a non-existent destination is atomic: readers never
observe content without provenance or provenance without content. If the
destination already exists, rename fails and the store evaluates replay/conflict
against the committed object.

Staging directories live only under the configured landing root's `.staging/`
namespace. Failure cleanup deletes only that operation's staging directory.

### Filesystem limitations

- Staging and destination must be on the same filesystem for atomic rename.
- Guarantees assume a POSIX-compatible local filesystem (CI uses Linux).
- The adapter rejects keys that resolve outside the landing root, including
  absolute paths, `..`, reserved `.staging` segments, and symlink escapes where
  intermediate symlink targets can be detected.

## Provenance

Persisted fields are exactly the existing model:

- `run_id`
- `source`
- `source_uri`
- `retrieved_at` (timezone-aware)
- `sha256` (lowercase hex)

Serialization is sorted-key compact JSON plus a trailing newline. No credentials,
environment values, machine paths, or payload bytes are stored.

## Checksums

SHA-256 is computed from the streamed bytes during `put_if_absent`. Manifest
metadata sizes and URI strings are never treated as content checksums. A mismatch
raises `ChecksumMismatchError` and leaves no committed object.

## Replay and conflict

| Situation | Result |
| --- | --- |
| New key, matching checksum | Commit succeeds |
| Same key, same bytes checksum, same provenance | No-op success (identical replay) |
| Same key, different bytes or provenance | `ObjectConflictError` |
| Concurrent identical writers | One creates; the other replays successfully |
| Concurrent conflicting writers | One creates; the other raises `ObjectConflictError` |

Committed objects are never overwritten, deleted, or replaced by `put_if_absent`.

## Read contract

`open(key)` returns a caller-owned read-only binary file object. The caller must
close it. Missing objects raise `ObjectNotFoundError`. Incomplete or corrupt
committed state raises `IncompleteObjectError`. The handle is never writable.

## Typed errors

| Error | Meaning |
| --- | --- |
| `InvalidObjectKeyError` | Unsafe/absolute/traversing key |
| `ObjectNotFoundError` | No committed object |
| `ObjectConflictError` | Immutable conflict (`FileExistsError`) |
| `ChecksumMismatchError` | Streamed bytes ≠ provenance SHA-256 |
| `IncompleteObjectError` | Partial or inconsistent committed state |

## Future GCS semantic equivalence

`GCSObjectStore` (#9) must match this contract: immutable create, identical
replay, conflict, checksum verification, paired content/provenance publication,
and caller-owned read streams. Local tests under `tests/contract/` define the
reusable behavior; this PR does not implement GCS.
