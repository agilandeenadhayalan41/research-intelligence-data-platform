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
| `max_decoded_sample_bytes` | 25,000,000 | Strict positive integer; caps decompressed bytes consumed while sampling |

Zero, negative, missing (`None`), boolean, string, and fractional values are
rejected. They are never interpreted as unlimited.

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
4. **No relabeling.** The first bytes must match the declared representation.
   Parquet needs `PAR1`, and OpenAlex `.gz` JSONL needs the gzip header. Parquet
   bytes declared as JSONL, or gzip bytes declared as Parquet, fail with
   `OpenAlexUnsupportedFormatError`. Bytes are never renamed or converted.
5. **Cleanup.** The payload stream, any gzip wrapper, and the Parquet temporary
   directory are closed or deleted on success and failure.

### JSONL / JSONL.GZ

- gzip is decompressed as a stream (`gzip.GzipFile` over the bounded reader).
  Plain JSONL streams are also supported by `sample_jsonl()`.
- Reads at most `max_profile_records` lines. Each `readline` is limited to the
  remaining decoded-byte budget, so long lines and decompression expansion fail
  explicitly (`OpenAlexProfileLimitError`) rather than being truncated or loaded.
- After the record bound, a one-byte probe only checks whether EOF was reached.
  The profiler does not read to EOF just to count records.
- Each line must be one UTF-8 JSON object. Duplicate keys, `NaN`/`Infinity`,
  blank lines, and non-object values raise `OpenAlexMalformedJSONLError`.
  Truncated or corrupt gzip raises `OpenAlexDecompressionError`. A file with no
  records raises `OpenAlexEmptySourceError`.
- Observations are field paths (`a.b` for object members, `a[]` for array
  elements), observed JSON types, present, missing, and null counts, nested
  fields, and fields with observed nulls or missing values. Fields where no
  null/missing value was observed have `nullable = null` (unknown), not `false`.

### Parquet

- Bytes are spooled through the counting reader into a private temporary file,
  because the footer is at the end of the file. The temporary directory is
  deleted before the function returns. The file is never stored in the
  repository.
- Leading/trailing `PAR1` magic is checked, and then the footer is read with
  `pyarrow.parquet.ParquetFile`. The profiler reports the Arrow schema with
  struct/list/map paths (`a.b`, `a[]`, `a{key}`, `a{value}`), declared
  nullability, row count, row-group count, top-level and physical column counts,
  column-chunk codecs, statistics availability (`all`/`partial`/`none`), and
  `created_by`.
- Value inspection uses `iter_batches(batch_size=max_profile_records)` and only
  the first batch. It never uses `read()`, a whole-table conversion, or pandas.
  The sampled present and null counts cover top-level columns only.
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
| Bounded profiling cost | Streams leading records only, with constant memory per bounded line | Requires the whole file (≤ byte bound) on local disk before the footer is readable |
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
