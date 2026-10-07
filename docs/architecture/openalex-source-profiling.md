# OpenAlex source profiling and format decision (Step 08)

Step 08 adds a bounded, evidence-classified profiler for one selected OpenAlex
Works source file. It reuses the existing manifest parser, deterministic sample
selector, and bounded `OpenAlexConnector`. It does not land raw data, convert or
relabel source bytes, canonicalize records, write to GCS/BigQuery, or run
ingestion.

## Modules

| Module | Responsibility |
| --- | --- |
| `profile_models.py` | `ProfilingLimits`, `EvidenceType`, `FieldProfile`, `OpenAlexSourceProfile`, `RepresentationEvidence`, `OpenAlexProfileRun`, and profiling errors |
| `profile_jsonl.py` | Streaming JSONL / gzip JSONL sampler (sampled observations only) |
| `profile_parquet.py` | Footer-first PyArrow inspection with one bounded record batch |
| `profiling.py` | Metadata checks, bounded retrieval through the connector, format-signature checks, report assembly, and the opt-in command |

The connector gains two small public additions:
`OpenAlexConnector.discover_metadata()` returns the existing
`OpenAlexSampleSelection` (`discover()` now delegates to it), and `fetch()`
returns `OpenAlexPayloadStream`, a `BufferedReader` that exposes the normalized
response `content_type` and parsed `content_length`. No transport, parsing, or
selection logic is duplicated.

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

Zero, negative, missing (`None`), boolean, string, and fractional values are
rejected. They are never interpreted as unlimited.

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
  The profiler does not read to EOF just to count records.
- Each line must be one UTF-8 JSON object. Duplicate keys, `NaN`/`Infinity`,
  blank lines, and non-object values raise `OpenAlexMalformedJSONLError`.
  Truncated or corrupt gzip raises `OpenAlexDecompressionError`. A file with no
  records raises `OpenAlexEmptySourceError`.
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

## Evidence classification

Every report field in `OpenAlexSourceProfile.evidence` has exactly one class.
The model rejects an unavailable value unless it is classified `UNKNOWN`, and
rejects `UNKNOWN` for an available value.

| Class | Meaning | Examples |
| --- | --- | --- |
| `VERIFIED_METADATA` | Declared by the validated manifest or HTTP response metadata | `source_format`, `snapshot_date`, `updated_date`, `declared_size_bytes`, `declared_record_count`, `content_type`, `content_length_bytes` |
| `EXACT_FILE_METADATA` | Exact for the whole profiled file | Parquet footer schema/nullability/row counts/row groups/codecs/statistics; gzip header verified; `observed_size_bytes` and JSONL `row_count` only when the file was read to EOF |
| `SAMPLED_OBSERVATION` | Seen only in the bounded sample | JSONL schema, nested fields, and nullability; `sampled_record_count`; all `sampled_*` counts |
| `UNKNOWN` | Not determinable within the bounds | JSONL `row_count` / `observed_size_bytes` when EOF was not reached; Parquet-only facts for JSONL |

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

## Offline fixture provenance

All tests use deterministic synthetic JSON values, gzip streams with fixed
timestamps, or genuine Parquet generated by PyArrow in temporary directories.
No source data or generated binary fixture is committed. The exact 25,000,000
and 25,000,001-byte accounting tests generate bounded chunks on demand instead
of storing large files. Small compressible Parquet fixtures and fake metadata
verify guards run before value access; fake clocks test cooperative deadlines
without sleeps. No normal test calls OpenAlex or cloud services.

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

## Verified evidence and format decision

### What is verified

| Evidence | Status |
| --- | --- |
| Works JSONL manifest (`data/jsonl/works/manifest.json`) | Verified by anonymous HEAD and bounded-prefix reads on 2026-10-06. It declares `format: jsonl`, `entity: works` (see the [OpenAlex contract](openalex-manifest-contract.md)) |
| Works Parquet manifest (`data/parquet/works/manifest.json`) | Verified the same way. It declares `format: parquet`, `entity: works` |
| JSONL payload codec (gzip) | OpenAlex documentation states it, and file suffixes are `.gz`. No payload bytes have been inspected yet |
| Parquet payload codec (Snappy), schema, row groups | OpenAlex documentation states the codec. No payload bytes have been inspected yet |

Both representations are therefore **available as declared metadata**. The
Step 08 command was run in the Copilot cloud environment on
**2026-10-07 12:37:43 UTC**. It reported `OpenAlexNetworkError` for both
manifests, with `status: UNAVAILABLE`, `evidence: UNKNOWN`, and no file selected
or retrieved, because DNS for `openalex.s3.amazonaws.com` is blocked in that
runtime. This does not contradict the earlier manifest observations. However,
**no payload file was profiled**. Payload schema, nesting, nullability, row
counts, and codecs remain unverified for real data. The profiler behavior is
verified only with offline synthetic fixtures.

### Comparison (from verified evidence and profiler capability only)

| Aspect | JSONL.GZ | Parquet |
| --- | --- | --- |
| Availability | Manifest verified | Manifest verified |
| Exact file facts without reading the whole file | None beyond size and gzip header. Row counts need a full read | Footer gives exact schema, nullability, row/row-group counts, codecs, and statistics availability |
| Bounded profiling cost | Streams leading records with line/structure/work limits; Python memory is not an exact byte cap | Requires the whole file (≤ byte bound) on temporary disk; conservative expansion guards may reject value inspection |
| Schema evidence | Sampled. Absent fields/nulls in the sample are unknown | Exact declared schema. Values are sampled |
| Fidelity to the OpenAlex record | One original JSON object per line. Keys and nesting are as published | Depends on OpenAlex's Parquet schema mapping (unverified for dynamic-key objects and deep nesting) |

### Recommendation for the next stage (provisional)

- **Immutable raw source:** land the bytes of whichever representation is
  selected exactly as retrieved. Do not convert or rename them.
- **First canonical analytical path:** use **JSONL.GZ** as the provisional input.
  Both manifests are verified, but neither payload has been inspected. JSONL is
  the connector default. It preserves each published record verbatim, and it can
  be profiled and processed incrementally as a bounded stream. Parquet's mapping
  of OpenAlex's nested and dynamic-key objects has not been verified. Canonical
  analytical data may later be written as Parquet. That conversion is not part
  of Step 08.
- **Revisit:** if a bounded profile of a real Parquet Works file shows a
  complete, stable schema that maps the needed nested relationships, prefer it
  for its exact footer metadata and columnar reads. This decision must be based
  on profile output, not on the target architecture's use of Parquet.

### Unresolved questions

- Real Works payload schema, nesting depth, and nullability in each
  representation. Run the opt-in command for both formats where public network
  access is allowed.
- Whether the Parquet representation preserves every JSONL field. Examples are
  dynamic-key objects such as abstract inverted indexes, and deeply nested lists.
- Whether both representations are produced for the same snapshot date and
  `updated_date` partitions with matching record counts.
- Whether any Works file is no larger than 25,000,000 bytes in each
  representation. If not, no file is selected, and the run reports
  `NoEligibleFile`.
