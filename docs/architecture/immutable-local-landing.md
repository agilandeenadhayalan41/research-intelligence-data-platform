# Immutable local raw landing

Step 09 implements `LocalObjectStore` and the OpenAlex raw key layout.
`GCSObjectStore` (#9) implements the same `ObjectStore` contract against GCS
with create-only generation preconditions and offline fake-client tests. Live
bucket validation is separately approved and is not part of default CI.
Later steps build on this contract: the Step 12 Works and Step 13 deletion
runners ([local Works ingestion](local-works-ingestion.md),
[deletions](openalex-deletions.md)) land raw bytes through `put_if_absent`
before decoding them (Works records then go through the Step 11 canonical
mapping), and the Step 19 bounded runner and the Step 20 `WORKS_INGEST_UNIT`
contract reuse that path. The Step 12/13 runners land into GCS only when a
caller opts in with an injected source connector
([GCS landing through ingestion](#gcs-landing-through-ingestion-de-06-127)); the
CLIs and the Step 19 runner refuse non-local configuration. All of them have run
only on synthetic payloads, and no live GCS landing through ingestion is
recorded yet (#127 stays open). No Airflow or Composer is deployed.

## Preserve source bytes

Immutable raw landing stores the exact bytes received from the source connector:

| Declared OpenAlex format | Raw object basename | Bytes |
| --- | --- | --- |
| `jsonl` | `source.gz` | Original gzip JSONL payload |
| `parquet` | `source.parquet` | Original Parquet payload |

JSONL.GZ is never rewritten as Parquet in this layer. Analytical Parquet is
written separately, from mapped canonical rows, by the canonical Parquet
exporter (`research_platform.canonical.openalex.export`, PRs #104/#105) for the
synthetic #103/#108 BigQuery loads, and by the Silver release builder
(`research_platform.canonical.openalex.publisher`, DE-07 #128) under
`openalex/canonical/`. Neither writes under raw-landing keys.

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
6. `fsync` the final object's parent directory.

On Linux, directory rename to a non-existent destination is atomic: readers never
observe content without provenance or provenance without content. If the
destination already exists, rename fails and the store evaluates replay/conflict
against the committed object.

**Visibility vs durability:** `rename` provides atomic visibility of the paired
content/provenance directory. Parent-directory `fsync` persists that directory
entry on supported POSIX filesystems so a crash is less likely to lose the
publication. These guarantees are local-filesystem / POSIX specific; they are
not claimed for network mounts or non-POSIX platforms.

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
raises `ChecksumMismatchError` and leaves no committed object. Before
`put_if_absent`, the Step 12/13 runners also check the source: the byte count
against the declared size (for streams that report it, and on the GCS backend
for any asset with a known size), a single-part S3 ETag against the MD5 of the
bytes, and that a gzip payload does not end inside a member (DE-06). Those are
completeness checks on the source, not the content checksum.

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
close it. Before returning the stream, the adapter loads and validates
`provenance.json`, hashes the committed content file, and compares the digest to
`provenance.sha256`.

| Condition | Error |
| --- | --- |
| No object directory / content / provenance | `ObjectNotFoundError` |
| Partial presence (only content or only provenance) | `IncompleteObjectError` |
| Malformed / unreadable provenance | `IncompleteObjectError` |
| Content bytes ≠ provenance `sha256` | `IncompleteObjectError` |

Corrupt committed objects are never silently returned, repaired, overwritten, or
deleted. The handle is never writable.

## Typed errors

| Error | Meaning |
| --- | --- |
| `InvalidObjectKeyError` | Unsafe/absolute/traversing key |
| `ObjectNotFoundError` | No committed object |
| `ObjectConflictError` | Immutable conflict (`FileExistsError`) |
| `ChecksumMismatchError` | Streamed bytes ≠ provenance SHA-256 |
| `IncompleteObjectError` | Partial or inconsistent committed state |

## GCS semantic equivalence (#9)

`GCSObjectStore` matches this contract:

| Concern | GCS approach |
| --- | --- |
| Immutable create | `upload_from_file(..., if_generation_match=0)` |
| Content + provenance coherence | Raw bytes as object body; `IngestionProvenance` in custom metadata `rp_provenance` |
| Checksum | SHA-256 over streamed source bytes before successful create |
| Identical replay | Precondition loss → reload metadata + content digest; equal provenance ⇒ no-op |
| Conflict | Precondition loss with differing sha/provenance ⇒ `ObjectConflictError` |
| Reads | Caller-owned read-only stream after integrity checks |

Shared behavioral coverage lives in `tests/contract/test_object_store.py`
(parametrized for `LocalObjectStore` and `GCSObjectStore` + fake client).
Provider precondition argument assertions live in
`tests/unit/test_gcs_object_store.py`.

### Optional live sandbox smoke (manual only)

Not run by `make test`, GitHub Actions, or default development. Requires a
separately approved project/bucket and ADC (Workload Identity / user ADC).
Does **not** authorize this repository to create buckets, projects, or IAM.

Suggested manual checks (operator-owned):

1. Construct `GCSObjectStore` with `backend=gcs` config and no injected client.
2. `put_if_absent` new key → succeeds (generation precondition create).
3. Identical replay → no-op success.
4. Conflicting bytes/provenance → `ObjectConflictError`.
5. `open` returns original bytes; caller closes the stream.

#### Recorded run (#91)

On 2026-10-10 (`main` @ `b6da206`, unmodified `GCSObjectStore`, no injected
client) all five checks passed against real GCS, plus extras: a checksum
mismatch raised `ChecksumMismatchError` and created nothing, a missing key
raised `ObjectNotFoundError`, and a `gs://` key raised `InvalidObjectKeyError`.
The object generation stayed unchanged across replay and both conflicts. One
41-byte synthetic object was written; its provenance is the object's
`rp_provenance` metadata. Evidence:
[#91](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/91#issuecomment-6102155343)
(identifiers redacted).

Environment: an operator-owned sandbox project and bucket, created for the run
with operator approval (the original sandbox project's billing was closed);
uniform bucket-level access and public access prevention enforced; user ADC;
CPython 3.14.4 and `google-cloud-storage` 3.16.0.

#### Recorded run (#108 Stage 1)

**Run.** On 2026-10-11, `scripts/gcs_object_store_smoke.py --least-privilege`
re-ran the #91 checks in the same operator-owned POC project. It ran as a
least-privilege service account, reached by impersonation, with no key files.

**Grants.** A custom role with only `storage.objects.create` and
`storage.objects.get`, limited to the test prefix by an IAM condition, on a
uniform-access bucket.

**Result:** 17/17 PASS.
- **Every #91 check passed.** Without `storage.objects.delete`, a create-if-absent
  on an existing object still gets 412, and the adapter classified replay and
  conflict correctly.
- **Bucket `testIamPermissions`** holds neither `objects.delete` nor `objects.list`.
- **A raw overwrite of a throwaway object** got a 403 naming `storage.objects.delete`.
- **A raw create outside the prefix** got a 403 naming `storage.objects.create`.

Evidence: [#108](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/108#issuecomment-6114407084) (identifiers redacted).

#### Recorded run (#108 Stage 3, DEV)

**Run.** On 2026-10-12, `gcs_object_store_smoke.py --least-privilege` passed
**17/17** in DEV, a personal project labelled DEV with the Stage 1 grants. It ran
as a least-privilege service account reached by impersonation.
- **The #91 checks passed,** with replay and conflict classified as 412 without
  `objects.delete`.
- **The overwrite got a 403** naming `storage.objects.delete`.
- **The out-of-prefix create got a 403** naming `storage.objects.create`.

**Readiness.** `GCS_RAW_LANDING` is `VERIFIED` for DEV too. No promotion is
claimed. Evidence: [#108](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/108#issuecomment-6131598666) (identifiers redacted).

#### Recorded run (#108 Stage 3, QA)

**Run.** On 2026-10-12, after DEV, `gcs_object_store_smoke.py --least-privilege`
passed **17/17** in QA, a separate personal project labelled QA. It ran as a
separate least-privilege service account.
- **The #91 checks passed.**
- **The overwrite got a 403** naming `storage.objects.delete`.
- **The out-of-prefix create got a 403** naming `storage.objects.create`.

**Readiness.** `GCS_RAW_LANDING` is `VERIFIED` for QA too. No promotion is
claimed. Evidence: [#108](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/108#issuecomment-6132004917) (identifiers redacted).

`GCS_RAW_LANDING` is therefore `VERIFIED` for **GCP_SANDBOX**, now also as
a least-privilege runtime identity, and for the personal projects labelled DEV
and QA (#108 Stage 3). This is domain-level evidence only: every environment
stays `NOT_READY` overall, no promotion is claimed, and PROD stays `BLOCKED`.
It is not evidence for an enterprise-managed project, separate deploy/runtime
identities, concurrent writers, retries, large/resumable uploads, CMEK,
retention, or access logging.

## GCS landing through ingestion (DE-06, #127)

The Step 12 Works and Step 13 deletion runners can land raw bytes into GCS
through the same `put_if_absent` path. The path is opt-in and tested offline
only: no live landing through ingestion is recorded. *(PC)* marks a Google Cloud
product characteristic that is not repository evidence, as in
[ADR 0001](../adr/0001-gcp-architecture-and-service-selection.md).

**Selection and opt-in.** `object_store_from_config`
(`src/research_platform/storage/factory.py`) builds `LocalObjectStore` for
`storage.backend: local` and `GCSObjectStore` for `gcs` without any I/O; the GCS
client is created with Application Default Credentials on the first operation
unless one is injected. `run_openalex_works_local_ingest` and
`run_openalex_deletions_local_ingest` accept `gcs` only when the caller passes
`allow_gcs_landing=True` and an injected source connector, and the config says
`environment: gcp-sandbox`. Injected (synthetic) sources only: real OpenAlex
payloads in a cloud bucket need the DE-24 egress approval and the DE-05 terms
first. Every other combination, including `dev`, `qa` and `prod`, is refused
before discovery or any client call, and the bounds stay `max_files=1` and
`max_file_size_bytes <= 25000000`. The CLIs and the Step 19 runner have no
opt-in. Both wrappers and both stage functions (`ingest_works_asset`,
`ingest_deletion_asset`) refuse an injected `GCSObjectStore` or
`LocalObjectStore` that does not match `storage.backend`.

**Keys.** Raw keys are unchanged: `openalex/...` as `openalex_layout.py` builds
them (ADR 0001 D1), not a `raw/` prefix. Each run writes one create-only landing
manifest per source asset under a separate prefix in the same store:

```text
manifests/openalex/<entity>/run_id=<run-uuid>/asset_id=<asset-id>/landing.json
```

A run directory holds only asset directories, and each asset directory holds
only `landing.json`, so two assets landed under one run (Step 19, or one task
per asset) each get their own manifest.

**Before landing.** The runner streams the source to a temporary file while
computing SHA-256, MD5 and CRC32C, and checks the bytes before any
`put_if_absent`:

- a `Content-Length` unlike the asset's manifest size is refused before the body
  is read (`SourceListingMismatchError`, `VALIDATION`);
- a byte count unlike the declared size (`SourceSizeMismatchError`,
  `RETRIEVAL`);
- a single-part S3 ETag unlike the MD5 of the bytes
  (`SourceChecksumMismatchError`, `CHECKSUM`);
- a gzip payload that ends inside a member (`SourcePayloadIncompleteError`,
  `RETRIEVAL`).

Nothing is landed when a check fails. On the GCS backend the declared size is
enforced for every stream with a known size. On the local backend, streams
without response metadata (local files, test doubles) are checked for gzip
completeness and against the byte cap only, because existing fixtures declare
sizes that do not match their payloads. A gzip payload cut exactly at a member
boundary is a valid, shorter gzip; only the size checks catch it. The gzip
check gives no verdict on a payload that is not gzip, has bytes after its last
member, has a corrupt deflate stream or trailer, or decompresses past the decode
limit; such a payload can land and then fail in the decoder, as before DE-06.

**After landing.** `ObjectStore.stat` reads the store's own facts without
downloading the object. For GCS, one metadata GET returns the size, generation,
CRC32C, MD5 and the committed provenance; `LocalObjectStore` reports the size
and the committed provenance. A size, digest or provenance SHA-256 unlike the
retrieved bytes fails the asset as `LANDING` and writes no manifest, and a GCS
store that reports no facts fails closed. `created` means the committed
provenance is this landing's own; `existing_object_verified` means an earlier
landing (another run, or an earlier attempt of this run) committed the object
and its SHA-256 equals the retrieved bytes.
`committed_run_id` names the run that committed it.

| Manifest field | Meaning |
| --- | --- |
| `entity`, `pipeline_name`, `run_id`, `run_created_at` | The run that wrote the manifest |
| `storage_backend`, `storage_role` | `local` or `gcs`; always `bronze`. No bucket, project or URL |
| `entry.asset_id`, `raw_object_key`, `source_uri`, `snapshot_date`, `updated_date`, `content_format` | The landed object and its source identity |
| `entry.sha256`, `md5_hex`, `size_bytes`, `declared_size_bytes` | Measured bytes and the manifest size |
| `entry.source_content_length`, `source_etag`, `source_version_id` | Source response metadata, when the stream reports it (the OpenAlex connector does) |
| `entry.retrieved_at`, `landing_outcome` | When this run fetched the bytes; `created` or `existing_object_verified` |
| `entry.committed_run_id`, `committed_retrieved_at` | The run that committed the object, from its committed provenance |
| `entry.object_generation`, `object_crc32c`, `object_md5_base64` | The store's own object version and digests (null for local) |
| `entry.checks` | The checks that ran and passed: `declared_size`, `source_etag_md5`, `gzip_complete`, `store_size`, `store_md5`, `store_crc32c`, `store_provenance` |
| `retention` | Declared context only (below) |

A retry of the same run and asset keeps the first manifest when it names the
same object and SHA-256; a manifest with different content under the key is
`LANDING`.

**Failures.** A `Content-Length` unlike the listing is `VALIDATION`, an ETag
unlike the MD5 is `CHECKSUM`, and short or incomplete reads are `RETRIEVAL`.
Connector errors are `RETRIEVAL` too, including `OpenAlexTruncatedError` (which
was `UNKNOWN` before DE-06), and the deletion runner now has the same branch;
`OpenAlexFormatError` is `VALIDATION`. Provider and transport errors while
landing, verifying or writing the manifest (permission denied, interrupted
uploads, exhausted retries) become `RawLandingError` (`LANDING`) with a safe
message that names no key, bucket or caller. Different bytes under an existing
key stay `ObjectConflictError` (`CHECKSUM`) and keep the original. Programming
errors stay `UNKNOWN`.

**Uploads.** With the pinned google-cloud-storage 3.16.0, every
`GCSObjectStore` write is a resumable upload: a POST with `ifGenerationMatch=0`,
then one PUT for any object under the library's 100 MiB chunk size, carrying a
CRC32C. *(PC)* GCS checks it before creating the object, and an interrupted
session creates nothing. The library re-sends a dropped chunk; a rejected chunk
or exhausted retries fail the asset as `LANDING`, and a later run lands it.
*(PC)* A retryable error after the final chunk committed ends as a successful
upload (`created`); a non-retryable one fails the asset, and the next run gets
412, re-reads the object and accepts it when its SHA-256 matches
(`existing_object_verified`, naming the failed run). Interrupted and multi-chunk
resumable sessions are not verified against real GCS; the opt-in live test
checks the interrupted case. Source range resume is not implemented: under the
per-object cap a restart re-reads at most 25,000,000 bytes, and resume stays
deferred until an issue lifts that cap.

**Retention context.** Each manifest's `retention` object is a declaration, not
evidence of a bucket control. `bronze_rule` names ADR 0001 D1: Bronze is
retained in full until DE-10; every Silver release manifest lists its Bronze
inputs (DE-07, #128). Keeping the assets that a retained release lists is a
retention rule for bucket retention (DE-24, #145) and Silver garbage
collection, which has no owner yet. A landing manifest grants no retention of
its own. `bucket_retention_policy_checked`, `object_holds_set` and
`storage_lifecycle_implements_source_deletions` are always false: this code
neither reads nor sets bucket retention, holds or lifecycle rules (DE-24, #145),
and OpenAlex deletions are canonical tombstones (D12 (d)). The canonical
deletion barrier for unknown Works is a different mechanism.

**Evidence.** LOCAL: offline tests in default CI (register Evidence `DEFINED`;
the readiness LOCAL cell stays `DEMONSTRATED_LOCAL`); no `CLOUD_*` evidence until
a live run is recorded. `tests/unit/test_gcs_bronze_landing.py` runs the cases
on `LocalObjectStore` and on `GCSObjectStore` with the fake client,
`tests/unit/test_raw_landing_gzip_check.py` drives the gzip completeness check
directly, `tests/unit/test_gcs_resumable_transport.py` drives the real
google-cloud-storage client over an in-memory transport, and
`tests/unit/test_gcs_live_harness.py` tests the live harness and its gating.

**Approvals.** The owner's run is in an operator-owned or personal project,
which the #125 inventory does not record (ADR D3: such projects never count as
environments). Landing in an enterprise `GCP_SANDBOX` still needs `core-apis`,
`resource-location-policy`, `bronze-bucket`, `two-buckets-per-environment`,
`egress-to-public-source` and `source-access` approved
([inventory](../operations/enterprise-environment-inventory.md)). The
`gcp-sandbox` allow-list is temporary until DE-13/DE-27 own environment
configuration.

**Opt-in live test.** `tests/integration/test_approved_gcs_raw_landing.py`
(marker `gcs_live`) is deselected by `make test` and never runs in CI. The
repository owner runs it from the repository root, in a project they control and
after their own approval:

```bash
gcloud auth application-default login            # only if ADC is not configured yet
export GOOGLE_CLOUD_PROJECT=<sandbox-project-id>
export GCS_LIVE_CONFIRM_PROJECT=<sandbox-project-id>
export GCS_LIVE_BUCKET=<existing-bucket-name>
export GCS_LIVE_CONFIRM_BUCKET=<existing-bucket-name>
export GCS_LIVE_PROJECT_CLASS=operator-sandbox   # or: personal
export GCS_LIVE_EVIDENCE_DIR="$HOME/de06-gcs-live-evidence"
make test-gcs-live
```

It reads nothing from OpenAlex, makes at most 16 writes of at most 256 KiB each
and at most 200 HTTP requests under `de06-live/<run-token>/`. The test never
calls list or delete, and the client library's own delete after a reported
checksum mismatch is refused before it is sent. It records PASS or FAIL per
check, without project or bucket names (checked); no account name is recorded.
Post `summary.json` only; never paste console output or `junit.xml`, which can
hold the bucket, project, caller and host names. It proves the store and
landing behaviour, not the production key layout or the store/config guard. The
objects stay until the owner removes that prefix with their own tooling or a
lifecycle rule. A recorded run would be cloud evidence on synthetic data,
labelled by the recording PR; never enterprise evidence.
