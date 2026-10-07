# Data quality (Step 17 / #24)

Composable data-quality gates and informational metrics for OpenAlex Research Intelligence.

**Status:** DEFINED (typed contracts + BigQuery Standard SQL + DuckDB `SEMANTIC_ONLY` runner).  
**Not:** DEPLOYED. No GCP resources. No orchestration. No silent data repair.

Authoritative inputs: Step 10 control/provenance, Step 11 canonical model, Step 13 Policy A, Step 15 BigQuery contracts, Step 16 Gold marts.

---

## Architecture flow (orchestration later)

```text
canonical checks
      ↓
PRE_SERVING_BUILD gate
      ↓
analytical / Gold build
      ↓
staged Gold checks
      ↓
PRE_VISIBLE_PUBLICATION gate
      ↓
publish allowed / blocked
```

Step 17 provides the check registry, runner, and report. Steps 19/20 call these stages.

---

## HARD_GATE vs INFORMATIONAL_METRIC

| Kind | Blocks publication? | Typical rule |
|---|---|---|
| `HARD_GATE` | Yes, if required and not PASS | exact violation count `== 0` |
| `INFORMATIONAL_METRIC` | Never independently | numerator / denominator / rate |

`publication_allowed = TRUE` only when every required HARD_GATE result is `PASS`.  
`FAIL`, `ERROR`, or missing/not-executed required gates → `publication_allowed = FALSE`.

A check that cannot run is **`ERROR`**, never `PASS`.

---

## Scopes

| Scope | Meaning |
|---|---|
| `CANONICAL` | Normalized storage — may contain ACTIVE **and** DELETED Works |
| `ANALYTICAL` | BigQuery analytical layer (reserved) |
| `STAGED_GOLD` | Pre-visible Gold outputs |
| `PUBLISHED_GOLD` | Visible consumer outputs (same exclusion rules) |
| `RECONCILIATION` | Pipeline-declared counter balances |

### Deletion / citation distinctions

- **CANONICAL** may retain DELETED Works and Policy A relationships.
- **GOLD** consumer models must exclude DELETED **source** Works.
- **Citation TARGET** may be ACTIVE, DELETED, or absent — not a hard orphan.
- **Citation SOURCE** in `citation_edges` must be ACTIVE.

---

## Execution stages

- `PRE_SERVING_BUILD` — canonical integrity + reconciliation.
- `PRE_VISIBLE_PUBLICATION` — staged Gold deleted-source exclusion.

---

## Registry (stable check IDs)

### Hard gates

| check_id | Stage |
|---|---|
| `canonical.works.work_id_not_null` | PRE_SERVING_BUILD |
| `canonical.works.work_id_unique` | PRE_SERVING_BUILD |
| `canonical.relationships.source_work_integrity` | PRE_SERVING_BUILD |
| `canonical.relationships.dimension_integrity` | PRE_SERVING_BUILD |
| `reconciliation.decode_balance` | PRE_SERVING_BUILD |
| `reconciliation.publication_decision_balance` | PRE_SERVING_BUILD |
| `gold.deleted_source_work_exclusion` | PRE_VISIBLE_PUBLICATION |

### Informational metrics

| check_id | Denominator |
|---|---|
| `metric.active_works.missing_doi` | ACTIVE Works |
| `metric.active_works.missing_title` | ACTIVE Works |
| `metric.active_works.without_topics` | ACTIVE Works |
| `metric.active_works.without_authors` | ACTIVE Works |
| `metric.active_works.publication_year_distribution` | ACTIVE Works (NULL year bucket) |
| `metric.active_works.work_type_distribution` | ACTIVE Works (NULL type bucket) |
| `metric.active_works.reference_reconciliation` | ACTIVE-source reference rows |

Contract version: **`quality-contract-v1`** (stable; not derived from date/git).

---

## Result / report schema

`QualityResult`: check_id, kind, scope, model_name, run_id, contract_version, status, observed/expected, denominator, unit, message, diagnostic_counts, metric_points, error_classification.

`QualityReport`: run_id, contract_version, results, hard_gate_passed, publication_allowed.

Diagnostics are **aggregate counts only**. Never include raw source rows, titles, DOIs, author names, payload samples, credentials, or secret-bearing SQL.

---

## Reconciliation

`ReconciliationExpectation` is supplied by the pipeline/control layer.

Declared balances:

- `decoded == mapped_successfully + rejected`
- `unique_work_ids_evaluated == inserted + updated + identical + stale + conflict + restore_required`

Missing required inputs → `ERROR` (never PASS). Quality does not invent counters the pipeline does not provide.

---

## Empty-data semantics

| Situation | Behavior |
|---|---|
| Zero Works | non-null + unique gates PASS; metrics count=0, denominator=0, rate=NULL |
| Relationships present, Works empty | source-work integrity FAIL |
| Empty distributions | empty `metric_points` collection |

Empty data is not evidence that ingestion succeeded — reconciliation may still FAIL if declared source count was non-zero.

---

## Error semantics

Missing table/column, SQL error, executor exception → `status=ERROR` with safe classification:

`QUERY_ERROR` | `MISSING_COLUMN` | `MISSING_TABLE` | `EXECUTOR_ERROR` | `MISSING_INPUT`

Required gate ERROR → `publication_allowed=FALSE`. Never convert exceptions into `observed_value=0` / PASS.

---

## Thresholds

Hard integrity gates: exact expected violation count `0`.  
No invented production SLAs. Informational metrics have no threshold.

---

## Backends

- BigQuery Standard SQL contracts under `sql/bigquery/openalex/quality/` (not executed here).
- DuckDB local runner: `SEMANTIC_ONLY` — proves check logic, result semantics, report aggregation, fixtures.
- Does **not** prove BigQuery syntax acceptance, cost, latency, or production scale.

---

## No silent repair

Quality code observes and reports. It must not delete orphans, fill DOI/title, invent authors/topics, change `activity_state`, resurrect deleted Works, remove unresolved references, or rewrite raw data.

---

## Package layout

```text
src/research_platform/quality/
  models.py registry.py runner.py validation.py
sql/bigquery/openalex/quality/
docs/architecture/data-quality.md
tests/unit/test_data_quality.py
```
