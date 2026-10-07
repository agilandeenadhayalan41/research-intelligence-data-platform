# Query-pattern and benchmark registry (Step 14)

Step 14 defines the OpenAlex Research Intelligence **query-pattern and benchmark
registry** that Step 15 will use as evidence for BigQuery analytical design.

It does **not** deploy BigQuery, create gold marts, implement a Data Service, or
recommend PostgreSQL/AlloyDB without measured evidence.

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
`relevant_entities`, `relevant_relationships`, `parameters`, `filters`,
`active_work_filter`, `freshness_requirement`, `expected_frequency`,
`expected_concurrency`, `expected_result_size`, `known_scale`, `assumed_scale`,
`aggregation_risk`, `fanout_risk`, `candidate_materialization`, `placement`,
`placement_rationale`, `evidence_status`, `logical_query_shape`.

Optional service-level fields stay **UNKNOWN** when no production SLA exists.
No Wiley SLAs are invented.

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

## Measurement methodology

`BenchmarkMeasurementSpec` / `BenchmarkResult` capture:

cold/warm latency, p50/p95/p99, concurrency, bytes scanned, estimated cost,
result rows/bytes, freshness lag, frequency, cache hit rate, materialization
refresh cost.

Step 15+ should record real BigQuery runs into `BenchmarkResult` without
changing the registry contract.

## Materialization candidates

Documented candidates (not built; Step 16 / #55):

- journal author counts  
- publisher author counts  
- publisher-topic-year counts  
- publication trends  
- OA trends  

Each lists input/output grain, refresh dependency, reuse, and freshness.

## Partitioning / clustering

Patterns may imply candidate partition/cluster keys (e.g. `publication_year`,
`publisher_id`). Step 14 records relationship cardinality implications only.
**Step 15** makes physical BigQuery decisions.

## Current unknowns

- Production concurrency and p95 latency targets  
- Actual BigQuery bytes scanned / cost per pattern  
- Cache hit rates  
- Whether any lookup fails BigQuery + API/cache (none measured → no operational store)  
- True DOI uniqueness collisions at full OpenAlex scale  

## Out of scope

BigQuery tables (#22), gold marts (#55), DQ, Data Service, E2E, Airflow, GCS,
cloud API calls, AACT, Kubernetes, assigning #23.
