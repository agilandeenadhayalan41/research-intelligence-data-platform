# OpenAlex source profiling and format decision (Step 08)

Step 08 adds a bounded, evidence-classified profiler for one selected OpenAlex
Works source file. It reuses the existing manifest parser, deterministic sample
selector, and bounded `OpenAlexConnector`. It does not land raw data, convert or
relabel source bytes, canonicalize records, write to GCS/BigQuery, or run
ingestion.

DE-05 ([#126]) adds the opt-in, bounded inspection of the real public snapshot:
real-data fixes in the profiler and connector, and one operator script with byte
caps enforced in code. Its evidence is a **LOCAL bounded inspection** (sandbox
container, anonymous HTTPS to `openalex.s3.amazonaws.com`, 2026-10-16), not cloud
or enterprise evidence. See [Real snapshot evidence](#real-snapshot-evidence-2026-10-14).

## Modules

| Module | Responsibility |
| --- | --- |
| `profile_models.py` | `ProfilingLimits`, `EvidenceType`, `FieldProfile`, `OpenAlexSourceProfile`, `RepresentationEvidence`, `OpenAlexProfileRun`, and profiling errors |
| `profile_jsonl.py` | Streaming JSONL / gzip JSONL sampler (sampled observations only) |
| `profile_parquet.py` | Footer-first PyArrow inspection with one bounded record batch |
| `profiling.py` | Metadata checks, bounded retrieval through the connector, format-signature checks, report assembly, and the opt-in command |
| `scripts/openalex_snapshot_inspection_126.py` | DE-05 opt-in inspection of the real snapshot: allow-listed transport, byte budget and session ledger, record/replay, listings, CRC64NVME, summarisers, fail-closed redaction, fidelity comparator, evidence models and the renderer of the generated section below. No new `src` module and no new `__all__` name |

The connector gains two small public additions:
`OpenAlexConnector.discover_metadata()` returns the existing
`OpenAlexSampleSelection` (`discover()` now delegates to it), and `fetch()`
returns `OpenAlexPayloadStream`, a `BufferedReader` that exposes the normalized
response `content_type` and parsed `content_length`. No transport, parsing, or
selection logic is duplicated. DE-05 adds the validated S3 `etag` and
`version_id` to that stream and extracts the fixed-host, no-redirect transport into
`_open_https(method, path, ...)` (GET and HEAD only, with HEAD parsed as HEAD).

## Bounds

| Limit | Default | Rule |
| --- | --- | --- |
| `max_files` | 1 | Strict integer, exactly 1 is allowed |
| `max_file_size_bytes` | 25,000,000 | Strict positive integer |
| `max_profile_records` | 100 | Strict positive integer, at most 10,000 |
| `max_decoded_sample_bytes` | 25,000,000 | Strict positive integer; JSONL consumed decoded bytes, or conservative Parquet expansion bound and actual batch bytes |
| `max_record_bytes` | 1,000,000 | Strict positive integer, at most 25,000,000; one JSONL line including its terminator |
| `max_nesting_depth` | 64 | Strict positive integer, at most 128 |
| `max_profile_nodes` | 100,000 | Strict positive integer, at most 1,000,000; cumulative structural work |
| `max_schema_fields` | 10,000 | Strict positive integer, at most 100,000 |
| `max_profile_seconds` | 10 | Strict positive integer, at most 60; cooperative per-file deadline |
| `max_parquet_footer_bytes` | 1,000,000 | Strict positive integer, at most 25,000,000; checked before native footer decoding |
| `drain_jsonl_to_eof` | false | Strict boolean. When true, the JSONL remainder after the sample is read to EOF: lines are counted (not parsed) and gzip CRC32/ISIZE are verified |
| `max_drain_decoded_bytes` | 250,000,000 | Strict positive integer, at most 1,000,000,000; decoded bytes the drain may read |
| `parquet_values` | `sample` | `sample` or `footer_only`. `footer_only` keeps every exact footer fact and reads no values |

For the integer limits, zero, negative, missing (`None`), boolean, string, and
fractional values are rejected. They are never interpreted as unlimited.

The deadline starts before the single-file fetch and is checked around reads,
JSON preflight/observation, metadata/schema traversal, and native calls. It does
not include the two independent manifest discoveries. It cannot interrupt an
in-flight connector, gzip, JSON decoder, or PyArrow native call; the connector's
own network deadlines remain active. Resource limits are not an exact Python or
Arrow heap/RSS cap. Hard native-memory/time isolation for arbitrary corrupt
inputs would require a separate process/sandbox, which this step does not add.

## Processing order

1. **Metadata before payload.** The asset must be validated
   `OpenAlexAssetMetadata` for `works` in `jsonl` or `parquet` format. Its declared
   size must be known, non-zero, and no larger than `max_file_size_bytes`;
   otherwise nothing is requested.
2. **Connector retrieval.** `fetch()` still enforces the canonical public URI,
   configured format, content-type allow-list, Content-Length limit, actual-byte
   overflow probe, and truncation detection.
3. **Declared-size checks.** A response Content-Length that differs from manifest
   `content_length` is rejected before any payload byte is read. The profiler
   counts actual bytes and rejects bytes beyond the declared size or the byte
   limit. When a file is read to the end, the observed size must equal the
   declared size. When the observed record or row count is exact, it must equal
   any manifest `record_count`.
   Every profiler read requests at most the remaining smaller byte/declared-size
   budget plus one overflow-probe byte. `bytes_read` counts bytes consumed from
   the connector stream, not additional bytes in the connector's internal
   buffer. The opt-in CLI aligns the connector and profiler limits; supplying a
   connector with a larger limit does not disable that connector's read-ahead.
4. **No relabeling.** The first bytes must match the declared representation.
   Parquet needs `PAR1`, and OpenAlex `.gz` JSONL needs the gzip header. Parquet
   bytes declared as JSONL, or gzip bytes declared as Parquet, fail with
   `OpenAlexUnsupportedFormatError`. Bytes are never renamed or converted.
5. **Cleanup.** The payload stream, any gzip wrapper, and the Parquet temporary
   directory are closed or deleted on success and failure.

### JSONL / JSONL.GZ

- gzip is decompressed as a stream (`gzip.GzipFile` over the bounded reader).
  Plain JSONL streams are supported by
  `research_platform.sources.openalex.profile_jsonl.sample_jsonl(stream,
  compression=None, max_records=100, max_decoded_bytes=25_000_000,
  limits=ProfilingLimits())`. The caller owns the supplied binary stream; the
  helper closes only its own gzip wrapper. This does not weaken the remote
  metadata/URI contract, which still requires `.gz` for OpenAlex JSONL assets.
- Reads at most `max_profile_records` lines. Each `readline` is limited to the
  smaller remaining decoded-byte and per-record budget, plus one overflow
  probe. Before `json.loads`, a byte-level structural preflight caps nesting and
  cumulative nodes (containers, member names, and scalar values). Wide
  containers are observed with a depth-sized iterator stack, not an auxiliary
  list of every element. Limit failures raise `OpenAlexProfileLimitError`;
  observations are never silently truncated.
- After the record bound, a one-byte probe only checks whether EOF was reached.
  It is included in `decoded_bytes_sampled`. Successful reports never exceed the
  decoded cap; at most one extra byte may be consumed to detect overflow. Gzip
  can internally decode/read ahead beyond the bytes consumed by this helper.
  Without `drain_jsonl_to_eof`, the profiler does not read to EOF just to count
  records.
- With `drain_jsonl_to_eof`, the remainder is read in bounded chunks after the
  sample (at most `max_drain_decoded_bytes` decoded bytes). Newline-terminated
  lines are counted, not parsed; an unterminated final line counts as one line and
  adds a limitation. Reaching the gzip member end verifies CRC32 and ISIZE, so a
  truncated or corrupted tail raises `OpenAlexDecompressionError`. `row_count`
  (the line count), `observed_size_bytes` and `sha256` then become exact, and the
  line count is compared with the manifest `record_count`.
- Each line must be one UTF-8 JSON object. Duplicate keys, `NaN`/`Infinity`,
  blank lines, and non-object values raise `OpenAlexMalformedJSONLError`. The one
  exception is a repeated word key directly inside the top-level
  `abstract_inverted_index`, which real OpenAlex records contain (one in 1,202
  records of a 2026-10-14 prefix); JSON decoding keeps the last value, and the
  record is counted in `sampled_duplicate_key_records`. Any other duplicate key
  raises `OpenAlexMalformedJSONLError("JSONL object has a duplicate key outside
  abstract_inverted_index")`. Truncated or corrupt gzip raises
  `OpenAlexDecompressionError`. A file with no records raises
  `OpenAlexEmptySourceError`.
- Observations are field paths (`a.b` for object members, `a[]` for array
  elements), observed JSON types, present, missing, and null counts, nested
  fields, and fields with observed nulls or missing values. Fields where no
  null/missing value was observed have `nullable = null` (unknown), not `false`.
- Keys outside `[A-Za-z0-9_$@:-]` are JSON-quoted (for example `a["x.y"]`), so
  paths stay unambiguous. Empty keys are quoted the same way.
- `abstract_inverted_index` uses abstract words as keys, so those keys are data,
  not schema. It is reported as a `map`, and its values are collapsed into
  `abstract_inverted_index{value}`. At most `max_schema_fields` distinct field
  paths are tracked; beyond that the profile fails with `OpenAlexProfileLimitError`.

### Parquet

- Bytes are spooled through the counting reader into a private temporary file,
  because the footer is at the end of the file. The temporary directory is
  deleted before the function returns. The file is never stored in the
  repository.
- Leading/trailing `PAR1` magic and the footer length are checked before the
  footer is read with `pyarrow.parquet.ParquetFile`. Thrift string allocations
  use `max_parquet_footer_bytes`; container allocations use `max_profile_nodes`.
  When Arrow rejects those thrift allocations (`Exceeded size limit`), the
  profiler raises `OpenAlexProfileLimitError`, not a malformed-file error.
  Row-group/column traversal, schema fields, and nesting are bounded before any
  batch is requested. The profiler reports the Arrow schema with
  struct/list/map paths (`a.b`, `a[]`, `a{key}`, `a{value}`), declared
  nullability, row count, row-group count, top-level and physical column counts,
  column-chunk codecs, statistics availability (`all`/`partial`/`none`), and
  `created_by`.
- Value inspection first requires the sum of
  `column.total_uncompressed_size * max(1, column.num_values)` over all column
  chunks to fit `max_decoded_sample_bytes`. The deliberately conservative
  multiplier accounts for dictionary values repeated many times, including in
  a single nested-list row. It can reject otherwise readable files; it is not a
  precise memory estimate. A rejected file produces `OpenAlexProfileLimitError`,
  not a silently reduced or success-shaped profile.
- Only then does `iter_batches(batch_size=max_profile_records,
  use_threads=False)` read its first batch, with pre-buffering disabled. The
  batch's actual `nbytes` is also checked before observations are returned.
  Neither row count alone nor untrusted footer sizes guarantee a native memory
  ceiling: Arrow can allocate before returning, especially for corrupt inputs.
  There is no whole-table `read()`, pandas conversion, or hidden unbounded retry.
  Sampled present/null counts cover top-level columns only.
- Zero-row files are valid and report their exact schema with a limitation.
  Invalid footers and truncated files raise `OpenAlexMalformedParquetError`.
- **The expansion guard rejects every real OpenAlex Parquet file.** For the
  489-row, 322,063-byte 2016-06-24 part the conservative bound is 514,937,153
  against the 25,000,000 default, while the actual Arrow size of the whole table is
  1,709,803 bytes. `parquet_values="footer_only"` therefore keeps the exact
  footer schema, nullability, counts, codecs and statistics, skips the guard and
  makes no `iter_batches` call (`sampled_record_count` is 0, with a limitation).
  The default `sample` mode and its guards are unchanged.
- `inspect_parquet_footer(tail, *, file_size, limits)` inspects only the footer
  from the trailing bytes of a large file (for example a bounded Range read). It
  requires `PAR1`, checks the footer length against `max_parquet_footer_bytes`
  before native decoding and reads no value.

## Evidence classification

Every report field in `OpenAlexSourceProfile.evidence` has exactly one class.
The model rejects an unavailable value unless it is classified `UNKNOWN`, and
rejects `UNKNOWN` for an available value.

| Class | Meaning | Examples |
| --- | --- | --- |
| `VERIFIED_METADATA` | Declared by the validated manifest or HTTP response metadata | `source_format`, `snapshot_date`, `updated_date`, `declared_size_bytes`, `declared_record_count`, `content_type`, `content_length_bytes` |
| `EXACT_FILE_METADATA` | Exact for the whole profiled file | Parquet footer schema/nullability/row counts/row groups/codecs/statistics; gzip header verified; `observed_size_bytes` and JSONL `row_count` only when the file was read to EOF |
| `SAMPLED_OBSERVATION` | Seen only in the bounded sample | JSONL schema, nested fields, and nullability; `sampled_record_count`; all `sampled_*` counts |
| `UNKNOWN` | Not determinable within the bounds | JSONL `row_count` / `observed_size_bytes` / `sha256` when EOF was not reached; Parquet-only facts for JSONL |

The sampled counts are never presented as full-file or full-dataset facts.
Every profile also describes one development file, not the full snapshot.

`FieldProfile.evidence` and the report's `schema_fields` classification apply to
path/type/kind/nullability only. Each field additionally contains
`sampled_evidence`, with one classification for each of `sampled_present_count`,
`sampled_missing_count`, and `sampled_null_count`. A present count must be
`SAMPLED_OBSERVATION`, and an unavailable count must be `UNKNOWN`; validation
rejects counts labeled exact. Parquet nested fields and zero-row files have no
sampled value counts. Exact Arrow-declared nullability is kept separate from
sample null counts. JSONL samples cannot claim `nullable=false`.

**Additive output change, 2026-10-16 (DE-05).** `EVIDENCE_FIELDS` grows from 19 to
21. `sha256` is the SHA-256 of every retrieved byte and is `EXACT_FILE_METADATA`
only when the whole file was read and its size verified (Parquet always; JSONL
only when EOF was reached), otherwise `UNKNOWN`; the model rejects any other
combination. `sampled_duplicate_key_records` is a `SAMPLED_OBSERVATION` for JSONL
and `UNKNOWN` for Parquet. Existing fields and defaults are unchanged.

## Offline fixture provenance

The profiler's unit tests use deterministic synthetic JSON values, gzip streams
with fixed timestamps, or genuine Parquet generated by PyArrow in temporary
directories. The exact 25,000,000 and 25,000,001-byte accounting tests generate
bounded chunks on demand instead of storing large files. Small compressible
Parquet fixtures and fake metadata verify guards run before value access; fake
clocks test cooperative deadlines without sleeps. No normal test calls OpenAlex or
cloud services; the DE-05 offline modules also fail any socket, connect or TLS
wrap.

DE-05 commits three generated JSON files under `tests/fixtures/openalex_real/`,
from the LOCAL bounded inspection:

- `snapshot_evidence_2026-10-14.json`: aggregates only (sizes, counts, checksums,
  schema paths, quoted lines of the snapshot's own text files, the request log).
- `works_manifest_digest_2026-10-14.json`: per-file sizes and record counts of
  the two Works manifests (1,898 rows), so T01 runs on real metadata offline.
- `works_redacted_sample_2026-10-14.json`: five real Works records, **masked
  (pseudonymised), not anonymised**: the kept DOI and OpenAlex ids re-link a
  record to its real authors. A fail-closed policy maps each of the 143 observed
  string paths to one action: person fields (author ids, names, ORCIDs, raw
  affiliation strings) are pseudonymised; titles, URLs, location ids, raw source
  names, award strings and abstract words are replaced; APC currencies and
  amounts are masked (no prices); identifiers, vocabularies and organisation names
  are kept. A record with any string path outside the policy is refused, and the
  script writes no fixture unless every record passes. Under user decision 2 only
  person fields had to be masked; this policy masks more. The snapshot labels the
  data CC0 (README.txt line 8); whether that covers third-party text is an open
  governance question (ADR 0001).

No source bytes and no binary fixture are committed. Tests that need Parquet
re-encode from these JSON files in temporary directories.

## Opt-in public command

```powershell
python -m research_platform.sources.openalex.profiling --format jsonl
python -m research_platform.sources.openalex.profiling --format parquet
```

The command reads both public Works manifests (metadata only, each capped at
1,000,000 bytes) to record which representations are available. It then uses the
existing selector to choose at most one eligible file of the requested
representation, retrieves only that file through the connector, and prints a
JSON summary. The summary contains schema paths, types, counts, and evidence
classes, not record values. The command uses anonymous HTTPS and one 10-second
attempt per request. It uses no Wiley access or AWS credentials, writes no GCS,
BigQuery, or canonical data, and is not part of `make test` or CI. Exit codes:
`0` when profiled, `1` for a discovery or profiling failure (`error_category` is
reported), and `2` for invalid limits.

On the real 2026-10-14 snapshot the defaults select the smallest Works file, a
1-record file of 1,078 bytes (JSONL), and the default `max_profile_nodes=100_000`
fails on 100 current rich records (about 104,000 nodes were needed). The DE-05
script below profiles a larger pair with explicit limits instead.

## Network opt-in and caps

`scripts/openalex_snapshot_inspection_126.py` is the only DE-05 code that opens a
socket. It never runs in `make test` or CI:

- It refuses (exit 2) without `--allow-network`, and without `--budget-ledger`,
  before any client is built. `--from-raw <raw dir>` replays a recorded run with no
  socket and settles 0 bytes.
- The session ledger is a JSON file outside the repository. A run reserves its
  `--max-total-bytes` before its first request and settles the bytes it actually
  read in a `finally` block, so a killed run stays counted at its reservation. A
  run that would push the session above 100,000,000 bytes is refused
  (`SessionBudgetExhausted`). `--init-ledger-only --ledger-seed-bytes N` creates
  the ledger.
- Live pytest checks are in `tests/integration/test_real_source_profile_live.py`
  with the `openalex_live` marker and a skip unless `OPENALEX_LIVE_TESTS=1`. `make
  test` and `make test-unit` deselect the marker. `make test-openalex-live`
  requires `OPENALEX_LIVE_SCRATCH` and `OPENALEX_LIVE_BUDGET_LEDGER` and keeps
  temporary files and pytest `tmp_path` in that scratch directory.
- Every body byte is charged to one budget, including manifests and payloads read
  through the unchanged `OpenAlexConnector` (via its existing `client=`
  parameter). A size known from a listing, HEAD or manifest is required before
  any non-listing GET, so the proxy is never asked for an unbounded object.
- Every response is recorded under `<out>/raw/` (allow-listed headers and body;
  never request ids). Analysis reads only local copies: each pair file is
  downloaded once, verified (size, S3 CRC64NVME, single-part ETag MD5, HEAD ETag
  equal to GET ETag), then profiled through the unchanged connector and profiler
  from the local copy. The profile's `content_type` is the recorded GET header,
  or UNKNOWN when no GET was made.
- The script deletes only what it created: its own `de05-tmp-*` directory under
  `--out`, and `raw/` (without `--keep-raw`) only when it carries the script's
  marker file. An existing unmarked `<out>/raw` is refused (`RawNotOwned`, exit 2)
  before any request.

| Inspection cap | Value |
| --- | --- |
| Per object (cumulative per key, across Range reads) | 25,000,000 bytes |
| Per run (`--max-total-bytes`) | default 12,000,000; at most 100,000,000 |
| Requests per run (`--max-requests`) | default 60; at most 200 |
| Session ledger ceiling | 100,000,000 bytes |
| Combined or Works manifest | 1,000,000 bytes (`_MANIFEST_LIMIT`) |
| Listing response | 1,000,000 bytes, 1,000 keys per page, 6 pages per prefix |
| Parquet footer Range | footer at most 1,000,000 bytes, plus the 8-byte trailer, ending at the last byte |
| Deletion-ledger prefix Range | bytes 0 to 65,535 of `data/jsonl/works/deleted_ids.csv.gz` only |

| Allow-listed request | Paths |
| --- | --- |
| GET text | `/LICENSE.txt`, `/README.txt`, `/RELEASE_NOTES.txt` |
| GET combined manifest | `/data/{jsonl,parquet}/manifest.json` |
| GET Works manifest or payload | only through `OpenAlexConnector` (`_public_path` unchanged) |
| ListObjectsV2 | prefixes `""`, `legacy-data/merged_ids/`, `data/jsonl/`, `data/parquet/` |
| ListObjectVersions | `LICENSE.txt`, `RELEASE_NOTES.txt`, both combined manifests, the JSONL Works manifest, the JSONL deletion ledger |
| HEAD (with `x-amz-checksum-mode: ENABLED`) | texts, combined and Works manifests, Works part keys, both deletion ledgers |
| Range with `If-Match` | Parquet Works part footers; the JSONL deletion-ledger prefix. A 206 with the exact `Content-Range`, the full length and an ETag equal to the HEAD ETag is required; 412 or an ETag change is `OpenAlexSnapshotChangedError` (exit 3) |

Whole GETs of the deletion ledger or of any object above 25,000,000 bytes are not
allow-listed.

## Verified evidence and format decision

### What is verified

| Evidence | Status |
| --- | --- |
| Works JSONL manifest (`data/jsonl/works/manifest.json`) | Verified by anonymous HEAD and bounded-prefix reads on 2026-10-06 (see the [OpenAlex contract](openalex-manifest-contract.md)). On 2026-10-16, a LOCAL bounded inspection read it whole (357,013 bytes) and the unchanged parser accepted it |
| Works Parquet manifest (`data/parquet/works/manifest.json`) | Verified the same way; 370,414 bytes on 2026-10-16 (LOCAL) |
| JSONL payload codec and framing | LOCAL bounded inspection, 2026-10-16: gzip, header `1f8b0800000000000003`, one member and valid CRC32/ISIZE for the downloaded part, one UTF-8 JSON object per line, line count equal to the manifest `record_count` |
| Parquet payload codec, schema, row groups | LOCAL bounded inspection, 2026-10-16: `PAR1` at both ends, `parquet-mr` writer, SNAPPY on every column chunk, statistics on all chunks, 51 top-level and 200 leaf columns; a 1,070,341,481-byte part's footer (Range read) has the same schema |
| Step 08 command in the Copilot cloud environment | 2026-10-07 12:37:43 UTC: `OpenAlexNetworkError` for both manifests (DNS blocked in that runtime); no payload retrieved there |

All numbers come from the generated section below. The profiler behaviour itself
is still tested offline with synthetic fixtures; the real-format tests run offline
on the committed real-derived files.

### Comparison (evidence from the real pair)

| Aspect | JSONL.GZ | Parquet |
| --- | --- | --- |
| Availability | Every Works part listed in both manifests and present in the listing | Same; 1.10 times the JSONL bytes for Works |
| Exact file facts without reading the whole file | Size and gzip header only; the line count needs a full read (drain) | The footer gives exact schema, nullability, row/row-group counts, codecs and statistics, also from a Range read of a large part |
| Fidelity to the OpenAlex record | Verbatim published values: JVM-formatted floats; rare repeated word keys inside `abstract_inverted_index` | Same values after normalisation, except float32 narrowing of topic, concept and keyword scores; `ids` is a map; `abstract_inverted_index` is a JSON string; `updated_date` keeps microseconds that JSONL shows only to milliseconds (one distinct value in the pair; truncation vs rounding not determined) |
| Bounded profiling cost | Streams leading records; the optional drain counts the rest | Whole file on temporary disk; the value guard rejects real files, so values are read only by the script's bounded comparator |

### Recommendation for the next stage

- **Immutable raw source:** land the bytes of whichever representation is
  selected exactly as retrieved. Do not convert or rename them.
- **First canonical analytical path:** keep **JSONL.GZ**, now on evidence rather
  than as a provisional choice: it carries the published values verbatim, while
  Parquet narrows score fields to float32 and stores the abstract index as a JSON
  string. Step 12 local ingestion accepts JSONL.GZ only, and canonical Parquet is
  produced from mapped canonical rows by the canonical exporter
  (`research_platform.canonical.openalex.export`, PRs #104/#105).
- **Use Parquet footers** for exact schema, row-count and size checks, including
  footer-only Range reads of large parts.
- **Before any real ingestion**, the canonical mapper must accept real records:
  today it rejected all 2,798 records of the profiled 2026-09-28 file (ISO
  timestamps in `created_date`/`updated_date`, institution ids in
  `host_organization`), which is handed to DE-10 ([#131]) and DE-08 ([#129]).

## Real snapshot evidence (2026-10-14)

Generated from [`snapshot_evidence_2026-10-14.json`](../../tests/fixtures/openalex_real/snapshot_evidence_2026-10-14.json) by `render_snapshot_evidence_section` in [`openalex_snapshot_inspection_126.py`](../../scripts/openalex_snapshot_inspection_126.py). A test compares this section with the JSON byte for byte, so regenerate it from the evidence; never edit it by hand.

**Evidence label: LOCAL bounded inspection.** local container; anonymous HTTPS; no cloud project or credentials; host `openalex.s3.amazonaws.com`; generated from recorded raw; run 2026-10-16T22:00:09Z to 2026-10-16T22:00:18Z. This is not cloud or enterprise evidence.

### Request budget used

| Requests | Body bytes | Whole objects | Range reads | Largest body | Caps (object / run / requests) |
|---:|---:|---:|---:|---:|---|
| 37 | 7,053,146 | 9 | 3 | 1,246,757 | 25,000,000 / 12,000,000 / 60 |

No object above the per-object cap was requested whole. Tools: Python 3.13.16, PyArrow 25.0.1.

### Layout and full listing

- Bucket root files: `LICENSE.txt`, `README.txt`, `RELEASE_NOTES.txt`, `browse.html`; prefixes: `data/`, `legacy-data/`.
- `data/jsonl/` and `data/parquet/` each hold a combined `manifest.json` and 21 entities (authors, awards, concepts, continents, countries, domains, fields, funders, institution-types, institutions, keywords, languages, licenses, publishers, sdgs, source-types, sources, subfields, topics, work-types, works); the Parquet entity list equals the JSONL one: yes.
- `legacy-data/merged_ids/` prefixes (frozen pre-2026 layout, listed only): `legacy-data/merged_ids/authors/`, `legacy-data/merged_ids/institutions/`, `legacy-data/merged_ids/publishers/`, `legacy-data/merged_ids/sources/`, `legacy-data/merged_ids/works/`.
- Full key listing: yes for `data/jsonl/` (3,131 keys, 4 pages), `data/parquet/` (3,131 keys, 4 pages).

| Format | Manifest files | Listed keys | Missing from listing | Size mismatches | Single-part / multipart ETags | Checksum algorithm | Keys not referenced by a manifest |
|---|---:|---:|---:|---:|---|---|---|
| jsonl | 3,108 | 3,131 | 0 | 0 | 1,247 / 1,884 | CRC64NVME 3,131 | `<entity>/manifest.json` x 21; `manifest.json` x 1; `works/deleted_ids.csv.gz` x 1 |
| parquet | 3,108 | 3,131 | 0 | 0 | 1,304 / 1,827 | CRC64NVME 3,131 | `<entity>/manifest.json` x 21; `manifest.json` x 1; `works/deleted_ids.csv.gz` x 1 |

### Snapshot totals (combined manifests)

| Format | Date | Records | Bytes | Files | Entities | Sums equal totals |
|---|---|---:|---:|---:|---:|---|
| jsonl | 2026-10-14 | 586,176,148 | 755,513,952,946 | 3,108 | 21 | yes |
| parquet | 2026-10-14 | 586,176,148 | 800,893,070,274 | 3,108 | 21 | yes |

| Entity | Records | JSONL bytes | Parquet bytes | Files per format | Partitions |
|---|---:|---:|---:|---:|---:|
| `authors` | 101,066,311 | 74,121,890,690 | 50,678,811,863 | 517 | 171 |
| `awards` | 17,927,707 | 5,104,043,189 | 5,455,158,266 | 230 | 183 |
| `concepts` | 65,026 | 6,774,336 | 9,954,138 | 5 | 5 |
| `continents` | 7 | 3,131 | 10,325 | 1 | 1 |
| `countries` | 247 | 29,357 | 104,623 | 6 | 6 |
| `domains` | 4 | 1,174 | 9,082 | 1 | 1 |
| `fields` | 26 | 7,197 | 19,253 | 1 | 1 |
| `funders` | 41,799 | 11,155,921 | 11,224,635 | 89 | 89 |
| `institution-types` | 8 | 446 | 3,571 | 1 | 1 |
| `institutions` | 143,455 | 200,441,980 | 94,074,125 | 83 | 83 |
| `keywords` | 1,881,723 | 397,315,416 | 436,220,394 | 9 | 9 |
| `languages` | 182 | 7,981 | 71,110 | 22 | 22 |
| `licenses` | 10 | 1,488 | 14,713 | 3 | 3 |
| `publishers` | 10,713 | 2,040,650 | 3,433,105 | 94 | 94 |
| `sdgs` | 17 | 2,136 | 9,606 | 1 | 1 |
| `source-types` | 6 | 381 | 3,461 | 1 | 1 |
| `sources` | 268,629 | 364,324,898 | 155,714,195 | 136 | 136 |
| `subfields` | 252 | 215,373 | 189,661 | 2 | 2 |
| `topics` | 4,516 | 7,130,725 | 5,570,163 | 6 | 6 |
| `work-types` | 25 | 2,839 | 11,374 | 2 | 2 |
| `works` | 464,765,485 | 675,298,563,638 | 744,042,462,611 | 1,898 | 396 |

### T01: bounded discovery on the real Works manifests

| Format | Manifest bytes | Within 1,000,000 | Listed | Eligible at 25,000,000 | OVERSIZED | UNKNOWN_SIZE | Smallest / median / largest file | Partitions | Default selection |
|---|---:|---|---:|---:|---:|---:|---|---:|---|
| jsonl | 357,013 | yes | 1,898 | 296 | 1,602 | 0 | 1,078 / 367,249,193 / 985,079,418 | 396 | 1,078 B, 1 record(s) |
| parquet | 370,414 | yes | 1,898 | 289 | 1,609 | 0 | 64,058 / 390,004,253 / 1,070,341,481 | 396 | 64,058 B, 1 record(s) |

JSONL/Parquet Works pairs: 1,898; record-count mismatches: 0; pairs with both files within the per-object cap: 283.

Combined-manifest versions (seen only in version listings; prior versions are never fetched):

| Key | Last modified | Bytes | Within the manifest cap | Latest |
|---|---|---:|---|---|
| `data/jsonl/manifest.json` | 2026-10-14T18:27:44.000Z | 671,964 | yes | yes |
| `data/jsonl/manifest.json` | 2026-09-23T15:58:51.000Z | 712,682 | yes | no |
| `data/jsonl/manifest.json` | 2026-06-27T02:57:12.000Z | 1,121,660 | no | no |
| `data/parquet/manifest.json` | 2026-10-14T18:35:48.000Z | 694,359 | yes | yes |
| `data/parquet/manifest.json` | 2026-09-23T16:05:47.000Z | 736,227 | yes | no |
| `data/parquet/manifest.json` | 2026-06-27T03:07:02.000Z | 1,158,613 | no | no |

### T02: formats verified on real bytes

Profiled pair `updated_date=2026-09-28/part_0000` (latest updated_date pair with both files <= 2,000,000 bytes and >= 1,000 records); each file was downloaded once and profiled from the verified local copy through the unchanged connector and profiler.

| Object | Bytes | Records | Framing | SHA-256 (first 16) | CRC64NVME S3 = local | MD5 = ETag | HEAD ETag = GET ETag |
|---|---:|---:|---|---|---|---|---|
| `s3://openalex/data/jsonl/works/updated_date=2026-09-28/part_0000.gz` | 1,246,757 | 2,798 | gzip header `1f8b0800000000000003`, 1 member(s), CRC32 ok yes, ISIZE ok yes | `352337ab1e23145b` | yes | yes | yes |
| `s3://openalex/data/parquet/works/updated_date=2026-09-28/part_0000.parquet` | 973,551 | 2,798 | `PAR1` at both ends: yes; codecs SNAPPY; 1 row group(s); statistics all | `9d64e379ebc0a39e` | yes | yes | yes |

- JSONL: line count 2,798 (one record per line by JSONL framing; all 2,798 lines parsed by the script) equals the manifest record count; 300 records sampled for the schema; 243 field paths, 66 nested; 0 sampled record(s) repeat an `abstract_inverted_index` word key.
- Parquet: `parquet-mr version 1.12.3-databricks-0002 (build 2484a95dbe16a0023e3eb29c201f99ff9ea771ee)`; footer-only profile with 2,798 rows, 51 top-level and 200 leaf columns and 273 schema paths; the profiler read no values.
- Large Parquet part `s3://openalex/data/parquet/works/updated_date=2026-09-30/part_0000.parquet` (1,070,341,481 B): two If-Match Range reads (410,045 B) gave a 410,029 B footer with 400,000 rows (manifest 400,000) in 12 row groups of 1,091 to 38,880 rows; codecs SNAPPY; schema equal to the profiled pair: yes.

### Fidelity: JSONL vs Parquet on the profiled pair

- Paths: 243 sampled JSONL paths and 273 Parquet schema paths; JSONL paths missing in Parquet: 0; type conflicts: 0; Parquet paths not seen in the JSONL sample: 34.
- Representation rules: `abstract_inverted_index` object -> JSON string; `ids` object -> map<string,string>.
- Values: 2,798 records joined by `id` over 51 top-level fields; same id set: yes; same row order: no; fields with value differences: 0; fields equal only after float32 rounding: `concepts` (795), `primary_topic` (178), `topics` (178); Parquet timestamps with sub-millisecond digits that JSONL shows only to milliseconds (truncation vs rounding not determined): `updated_date` (2,798).

### Canonical model compatibility

- Real Works records have 51 top-level keys. Not in `FIELD_CATALOG`: `authors_count`, `citation_normalized_percentile`, `cited_by_percentile_year`, `countries_distinct_count`, `funders`, `fwci`, `has_content`, `indexed_in`, `institutions`, `institutions_distinct_count`, `is_xpac`, `locations_count`, `primary_topic`, `study_designs`, `volume`. Catalogued but absent: `grants`.
- The unchanged canonical mapper accepted 0 of 2,798 records of the profiled JSONL file. First failures:

| First failure | Records |
|---|---:|
| `IdentifierError: openalex_id prefix must be P, got I` | 169 |
| `MappingError: created_date must be exact YYYY-MM-DD` | 2,629 |

- With the diagnostic normalisation (created_date and updated_date cut to their first 10 characters (YYYY-MM-DD); a location source host_organization that is not a publisher (P) id set to null), 2,798 records map. The mapper is unchanged here (handed to DE-10 and DE-08).
- Records with a repeated `abstract_inverted_index` word key: 0 of 2,798.

### Checksums

- Manifests carry no checksum field. Single-part S3 ETags equal the body MD5; multipart ETags (`-N`) are not content hashes. HEAD with `x-amz-checksum-mode: ENABLED` returns a full-object CRC64NVME, compared above with a CRC64NVME of the retrieved bytes. The platform checksum is the SHA-256 of the exact retrieved bytes.

### Deletions and merges

| Object | Bytes | ETag | Last modified | Versions (last modified, bytes) |
|---|---:|---|---|---|
| `s3://openalex/data/jsonl/works/deleted_ids.csv.gz` | 234,102,326 | `1b79a1c54947244103dbc0db921a6a8b-4` | Wed, 14 Oct 2026 18:23:32 GMT | 2026-10-14T18:23:32.000Z, 234,102,326; 2026-09-23T15:47:53.000Z, 168,365,444 |
| `s3://openalex/data/parquet/works/deleted_ids.csv.gz` | 234,102,326 | `1b79a1c54947244103dbc0db921a6a8b-4` | Wed, 14 Oct 2026 18:27:46 GMT | not listed |

- Identical content under both trees (equal size, ETag and CRC64NVME; two distinct S3 objects): yes. The ledger is above the per-object cap, so only an If-Match Range prefix was read, and its total row count is UNKNOWN. "Cumulative" is documented on help.openalex.org (Sync page, per the Step 13 contract; not read in DE-05) and consistent with growth between versions, not proven.
- Prefix of 65,536 B: header `work_id,deleted_date`; 15,683 complete rows; row shape `https://openalex.org/W<digits>,YYYY-MM-DD`: yes; sorted by work id: no; deleted_date counts: 2026-08-14: 15,683.
- Deletion or merge objects of other entities in `data/` (full listing): none.
- Legacy `legacy-data/merged_ids/` prefixes (frozen, listed only): `legacy-data/merged_ids/authors/`, `legacy-data/merged_ids/institutions/`, `legacy-data/merged_ids/publishers/`, `legacy-data/merged_ids/sources/`, `legacy-data/merged_ids/works/`.
- `RELEASE_NOTES.txt` line 109: "- delete 187,452 works: deleted Zenodo records. (merge into deleted id: W4285719527)"
- `RELEASE_NOTES.txt` line 153: "- merged 566k duplicated works associated with HAL repository"
- `RELEASE_NOTES.txt` line 154: "- removed old author IDs (ID less than 5000000000) from merged_ids/authors"

### Prior releases

- Anonymous S3 object versioning: yes; current-layout combined JSONL manifest versions: 3. Manifests carry no version ids, and no retention policy is published.

### Rights (as the snapshot publishes them)

| Item | What the snapshot shows | Status |
|---|---|---|
| Licence | `LICENSE.txt` line 3: "CC0 1.0 Universal"; SHA-256 `6d489af6292662d9`, 7,047 B, 3 version(s), same ETag: yes | OBSERVED |
| README licence line | `README.txt` line 8: "License:   LICENSE.txt (CC0)" | OBSERVED |
| Third-party rights | `LICENSE.txt` line 113: "c. Affirmer disclaims responsibility for clearing rights of other persons" (clause continues) | OBSERVED |
| Attribution | 0 matching line(s) | NOT PUBLISHED IN SNAPSHOT |
| Terms of use | none in the snapshot; help.openalex.org docs/sync links at `README.txt` lines 11, 12 and `RELEASE_NOTES.txt` line 4 (not read) | NOT VERIFIED |
| Full article text | full-text field present: no; 0 full-text statement(s); indicators only: `has_fulltext`, `has_content.pdf`, `has_content.grobid_xml`, `open_access.any_repository_has_fulltext`, `open_access.oa_url`, `locations[].pdf_url`, `locations[].landing_page_url` | NOT IN SNAPSHOT; full-text rights NOT PUBLISHED |
| Abstracts | `abstract_inverted_index`, `abstract_inverted_index{value}`, `abstract_inverted_index{value}[]`; data-quality notes at RELEASE_NOTES lines 16, 27, 76, 128, 177, 189, 302 | PRESENT; no specific statement |
| Per-work licence fields | `best_oa_location.license`, `best_oa_location.license_id`, `locations[].license`, `locations[].license_id`, `primary_location.license`, `primary_location.license_id`; they describe works and grant nothing | OBSERVED |
| Personal data | 9 person field paths, masked (pseudonymised) in the committed fixture | OBSERVED |

Not verified:

- Terms of use, attribution guidance and any full-text or abstract statement on help.openalex.org (linked from README.txt and RELEASE_NOTES.txt; that host was neither approved nor read).
- Whether CC0 as dedicated by OpenAlex covers third-party abstract text: LICENSE clause 4(c) disclaims clearing other persons' rights. This is an input to the governance decision in ADR 0001, not an approval.

### Cadence

Stated (quoted):

- `README.txt` line 3: "The complete OpenAlex database as downloadable files, released once a quarter."
- `RELEASE_NOTES.txt` line 3: "Public releases land on the second Wednesday of January, April, July and October (UTC)."
- `RELEASE_NOTES.txt` line 77: "- after this release, the snapshot will be updated quarterly"

Observed: 56 release headings, 9 in 2026 (2026-01-15, 2026-02-03, 2026-02-25, 2026-03-30, 2026-04-29, 2026-05-22, 2026-06-25, 2026-09-23, 2026-10-14); on a stated second Wednesday: 2026-10-14. Inference, not a published statement: the usable change trigger is the combined manifest's ETag, version id or date (README: manifests are written last), not a calendar.

### Masked fixture

- Policy version 1 maps every observed string path to one action: keep 114, mask_person 9, mask_price 2, replace_text 18. 2,798 candidate records; skipped: too_large 139, unknown_path 0, leak 53, email 0, type 801; unknown string paths: none.

### Limitations

- LOCAL bounded inspection from a sandbox container; not cloud or enterprise evidence.
- One JSONL/Parquet Works pair was downloaded whole; every other part is described by the manifests and the full listing only, and one large Parquet part was read footer-only.
- The JSONL schema is sampled from the first 300 records of the pair; the line count comes from the drain, and every line was parsed by the script for fidelity. The Parquet schema is exact for the profiled files only.
- Gzip member structure is known for the downloaded pair only; for large parts it is UNKNOWN.
- The deletion ledger is above the per-object cap: only a 65,536-byte prefix was read, so its row count is unknown and 'cumulative' is not proven.
- Rights and cadence are quoted from the snapshot's own LICENSE.txt, README.txt and RELEASE_NOTES.txt only; help.openalex.org was not read.
- The TLS peer is the sandbox's agent proxy, so integrity rests on matching sizes, record counts, S3 CRC64NVME and ETags, not on the TLS certificate of S3 itself.

## Still open

Under [#126], which stays open (the PR says "Refs #126"):

1. OpenAlex's own terms of use, attribution guidance and any full-text or
   abstract statement. `help.openalex.org` is linked from `README.txt` and
   `RELEASE_NOTES.txt` but was neither approved nor read here (ADR 0001 D12 (a)).
2. Cloud or enterprise-environment evidence. Cloud integration tests are
   "separately approved" and blocked by [#125] (DE-04).
3. The full deletion-ledger row count and a proof that the ledger is cumulative.
   The ledger (234,102,326 bytes) is above the 25,000,000-byte per-object cap, so
   this needs a separately approved bounded read or DE-11 ([#132]).

Constraint, not open scope: DE-05 stays in the Follow-on of register rows
L03.1-01 and L24-02 until an ADR revision updates the "Still open" cells of ADR
0001.

Hand-offs recorded here and not implemented by DE-05:

- Canonical mapper rejects real timestamps and institution hosts: DE-10
  ([#131]) and DE-08 ([#129]).
- `FIELD_CATALOG` drift (15 uncatalogued top-level keys, `grants` absent):
  DE-10 ([#131]).
- `ingestion/jsonl.py` keeps duplicate keys last-wins: DE-10 ([#131]).
- Range resume with `If-Match` or `versionId` pinning; `OpenAlexTruncatedError`
  classified as `UNKNOWN` by `_classify_failure` (`pipeline.py`); no `RETRIEVAL`
  branch in `deletion_pipeline.py`: DE-06 ([#127]).
- Change triggers (combined-manifest ETag, version id or date), and canonical
  versioning when a release rewrites the same `updated_date` partition (the
  partition date is equal and the checksum differs, which the current versioning
  treats as a conflict): DE-11 ([#132]).
- Abstract text approval: the Security and governance owner, via DE-25 ([#146]).

[#125]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/125
[#126]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/126
[#127]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/127
[#129]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/129
[#131]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/131
[#132]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/132
[#146]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/146
