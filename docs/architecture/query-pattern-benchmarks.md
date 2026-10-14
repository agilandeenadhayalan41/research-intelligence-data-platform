# Query-pattern and benchmark registry (Step 14)

Step 14 defines the OpenAlex Research Intelligence **query-pattern and benchmark
registry** that Step 15 used as evidence for BigQuery analytical design.

Step 14 itself does **not** deploy BigQuery, create gold marts, implement a Data
Service, or recommend PostgreSQL/AlloyDB without measured evidence. Later issues
ran the 11 implemented patterns in BigQuery on synthetic data only; see
[Evidence classification](#evidence-classification).

## Artifacts

| Path | Role |
| --- | --- |
| [`config/query-patterns/openalex.yaml`](../../config/query-patterns/openalex.yaml) | Human-readable registry |
| [`src/research_platform/benchmarks/`](../../src/research_platform/benchmarks/) | Typed models, loader, fan-out fixtures, DuckDB harness |
| This document | Decision rules, grains, risks, unknowns |

Load offline:

```python
from research_platform.benchmarks import load_query_pattern_registry

registry = load_query_pattern_registry()
```

## Registry fields

Each pattern includes at least:

`pattern_id`, `name`, `capability`, `category`, `expected_result_grain`,
`relevant_entities`, `relevant_relationships`, `required_fields`, `parameters`,
`filters`, `active_work_filter`, `freshness_requirement`, `expected_frequency`,
`expected_concurrency`, `expected_result_size`, `known_scale`, `assumed_scale`,
`aggregation_risk`, `fanout_risk`, `candidate_materialization`, `placement`,
`placement_rationale`, `evidence_status`, `logical_query_shape`.

`required_fields` must reference the Step 11 canonical field contract (see
`benchmarks/canonical_contract.py`). Loader validation rejects unknown entities,
relationships, fields, dangling materialization candidates, invented
`sources.issn` / `sources.eissn` query shapes, and `MIN`/`MAX(license)` collapse.

Optional service-level fields stay **UNKNOWN** when no production SLA exists.
No Wiley SLAs are invented. The DOI and Work-ID lookups carry an
operator-stated requirement (200 ms p95, 5 rps, 24 h freshness; #23, recorded by
PR #119). It is not a measured or product-validated Wiley SLA.

## Categories covered

1. DOI lookup  
2. OpenAlex ID lookup  
3. eISSN / ISSN lookup  
4. Publisher lookup  
5. Unique authors per journal  
6. Unique authors per publisher  
7. Publisher + topic counts  
8. Publisher + topic + license + year  
9. Institution/topic relationships  
10. Citation relationships  
11. Publication trends  
12. Open-access trends  

## Result grains (examples)

| Pattern | Grain |
| --- | --- |
| DOI / OpenAlex ID lookup | 0–1 Work |
| ISSN lookup | 0–N Source (multiplicity documented) |
| Unique authors per journal | 1 row per `source_id` |
| Publisher × topic × license × year | 1 row per publisher+topic+license+year |
| Publication trends | 1 row per `publication_year` |
| Citation relationships | 1 row per `(source_work_id, reference_index)` |

## Active / deleted Work semantics

Consumer-facing patterns that return or count Works require
`activity_state = ACTIVE` (enum `REQUIRED_ACTIVE`).

Citation patterns use `REFERENCE_TARGET_MAY_BE_DELETED`: the **source** Work must
be ACTIVE for consumer lists, but a referenced target may be ACTIVE, DELETED, or
unresolved. Source-observed `work_references` rows are retained (Step 13).

## Fan-out / double-counting

Unsafe:

```text
works ⋈ work_authors ⋈ work_topics
```

For a Work with 2 authors and 3 topics this yields **6** join rows. Counting
without care inflates author or topic metrics.

Safe:

1. Aggregate `work_authors` independently  
2. Aggregate `work_topics` independently  
3. Join aggregates only at the required grain  

Offline fixtures in `benchmarks/fanout.py` and `benchmarks/harness.py`
demonstrate the inflation and the safe alternative.

Do **not** recommend a giant flattened join as an analytical model.

## Placement values (only these)

| Value | Meaning |
| --- | --- |
| `BIGQUERY_ANALYTICAL` | Analytical warehouse query |
| `MATERIALIZED_AGGREGATE` | Candidate reused aggregate |
| `BIGQUERY_PLUS_CACHE` | Warehouse + API/cache for lookups |
| `OPTIONAL_OPERATIONAL_STORE` | Only with **MEASURED** evidence that BQ+cache fails |
| `UNRESOLVED` | Insufficient evidence |

Decision order:

1. Can BigQuery satisfy it?  
2. Expensive + reused → materialization candidate  
3. Lookup latency → evaluate BigQuery + cache  
4. Measured failure of that path → optional operational store  
5. Else → UNRESOLVED  

The typed model **rejects** `OPTIONAL_OPERATIONAL_STORE` unless
`evidence_status = MEASURED`.

## Evidence classification

| Status | Use |
| --- | --- |
| `MEASURED` | Real production/BigQuery observations |
| `ESTIMATED` | Derived estimates with stated method |
| `ASSUMED` | Architecture/product assumptions |
| `UNKNOWN` | Not known |
| `ARCHITECTURE` | Placement from architecture intent, not cloud timing |
| `FIXTURE_ONLY` | Local DuckDB/Python timings only |

Local harness results must use `FIXTURE_ONLY`. They are **not** BigQuery
performance evidence. `BenchmarkResult` rejects `MEASURED` for local engines.

No pattern is `MEASURED`. The recorded runs below are small synthetic samples:
they show SQL correctness and small-sample latency, not placement at production
scale, so every `evidence_status` is unchanged (`MEASURED` would also unlock
`OPTIONAL_OPERATIONAL_STORE`).

- **BigQuery alone, all 11 implemented patterns** (`issn-source-lookup` has no
  SQL): #103 and #108 Stages 1–2 in GCP_SANDBOX (an operator-owned project,
  synthetic ~10k and ~100k Works), and #108 Stage 3 in personal projects
  labelled DEV and QA (synthetic ~10k Works). In GCP_SANDBOX, 21 cache-off runs
  per pattern gave server p50 152–266 ms and client p50 0.99–1.29 s.
  Year-partition pruning held for the year-bounded patterns, clustering was
  OBSERVED for 4 of 11 patterns at Stage 2 (a reduction only for
  `citation-relationships`), and billed cost was almost entirely 10 MiB
  per-table floor artifacts, not representative. See the
  [recorded runs](bigquery-analytical-models.md#recorded-run-103).
- **BigQuery + in-process cache, DOI and Work-ID lookups (#118):** four runs in
  the operator's personal DEV project on synthetic ~10k Works, through a
  `TtlLruCache` around `BigQueryWarehouse` (no Data Service or HTTP hop). With a
  cold cache (warm-up requests only) both MISSED the operator-stated 200 ms p95
  (DOI 2,426 ms, Work ID 2,554 ms); prewarmed, both MET it (0.006 ms and
  0.010 ms). The run evidence is in the #118 issue comments. #23 then closed
  not_planned: the operator chose the BigQuery + API/cache path.

No run used real OpenAlex data, an enterprise-managed project, a shared cache or
concurrent load. Requirement-level status is in the
[lead-requirement register](../requirements/openalex-lead-coverage.md).

## Measurement methodology

`BenchmarkMeasurementSpec` / `BenchmarkResult` capture:

cold/warm latency, p50/p95/p99, concurrency, bytes scanned, estimated cost,
result rows/bytes, freshness lag, frequency, cache hit rate, materialization
refresh cost.

Step 15+ should record real BigQuery runs into `BenchmarkResult` without
changing the registry contract. The #103, #108 and #118 scripts did not: they
wrote `jobs.jsonl` and `summary.json` outside the repository, and no
`BenchmarkResult` has been recorded for a real run.

## Materialization candidates

Documented candidates (none materialized):

- journal author counts  
- publisher author counts  
- publisher-topic counts  
- publisher-topic-year counts  
- publisher-topic-license-year counts  
- publication trends  
- OA trends  

Each lists input/output grain, refresh dependency, reuse, and freshness.
Step 16 (#55) defined Gold SQL for them as `MATERIALIZATION_CANDIDATE` marts
([inventory](gold-analytical-marts.md#inventory)); `publisher_topic_year_stats`
has the year grain and serves the `publisher-topic-counts` pattern. The Gold
SELECTs ran on synthetic data in the #103 and #108 runs, but no mart is
`MATERIALIZED` or refreshed.

## Partitioning / clustering

Patterns may imply candidate partition/cluster keys (e.g. `publication_year`,
`publisher_id`). Step 14 records relationship cardinality implications only.
**Step 15** makes physical BigQuery decisions.

## Canonical capability gaps

### ISSN / eISSN lookup (`issn-source-lookup`)

**Required consumer capability:** resolve normalized ISSN or eISSN to Source
metadata.

**Current canonical contract:** `Source` exposes only `issn_l`. There are no
`sources.issn` or `sources.eissn` fields in the Step 11 model, PyArrow schema,
or mapper.

**Registry status:** `placement = UNRESOLVED`, `evidence_status = UNKNOWN`.

ISSN-L is **not** a substitute for every ISSN/eISSN value. Step 15 must not
invent those columns. Faithful lookup needs a future approved canonical
extension or source-identifier relationship.

### Primary-location license metric (`publisher-topic-license-year`)

License dimension uses **PRIMARY LOCATION LICENSE** only:

- take `work_locations.license` where `is_primary = true`
- if no primary location/license exists, the Work contributes to a **NULL /
  UNKNOWN** license bucket
- do **not** use `MIN(license)` / `MAX(license)` across locations
- this is not “all observed licenses” (that would allow one Work in multiple
  license buckets)

Grain: `publisher_id + topic_id + primary_location_license + publication_year`.

## Current unknowns

- Production concurrency and p95 latency targets (the DOI/Work-ID 200 ms p95
  and 5 rps are operator-stated, not production SLAs)  
- Representative BigQuery bytes scanned / cost per pattern (the synthetic runs
  billed almost only 10 MiB per-table floor multiples; tracked in DE-18, #139)  
- Cache hit rates under real traffic (#118 saw 85.7% cold and 100% prewarmed
  under a synthetic Zipf(1.1) over 1,000 keys; tracked in DE-30, #151)  
- Whether the real DOI/Work-ID working set keeps cache misses under 5%, the
  condition under which #118 met the 200 ms p95; #23 closed not_planned, so no
  operational store  
- True DOI uniqueness collisions at full OpenAlex scale  
- Canonical modeling for ISSN/eISSN → Source identity mapping  

## Out of scope

BigQuery tables (#22), gold marts (#55), DQ, Data Service, E2E, Airflow, GCS,
cloud API calls, AACT, Kubernetes, assigning #23.
