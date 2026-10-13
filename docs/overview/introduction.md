---
slug: /
sidebar_position: 1
title: Introduction
description: What the Research Intelligence Data Platform is, the problem it addresses, who it is for, the rules it follows, and how to read these docs.
---

# Research Intelligence Data Platform

**What it is.** One traceable path from public research data to the
applications and analysts that use it. The first source is **OpenAlex**, a
free public catalog of scholarly works, authors, institutions, journals,
publishers and topics.

**Why it is needed.** The data is large, it changes (records are updated and
Works are deleted), and its many-to-many relationships make naive counts
wrong.

**What a consumer gets.** Typed questions instead of raw SQL, for example:
look up a Work by DOI, count the unique authors per journal, or follow
publisher × topic trends by year. Today these exist as tested contracts and
query patterns. Their SQL has run in BigQuery on synthetic data, and the Data
Service capabilities run locally. None is deployed as a service yet.

The platform is a Python codebase, and everything can be developed and tested
on a laptop, offline, with tiny synthetic data.

| Page | Read it for |
| --- | --- |
| [How it works](how-it-works.md) | The end-to-end data flow, one stage at a time |
| [Status](status.md) | What has been validated, where, and how strongly. What is not proven yet |
| [Getting started](getting-started.md) | The repository layout and how to run the tests locally |
| [Glossary](glossary.md) | The status labels and terms used across these docs |

## The problem it addresses

Public research data such as OpenAlex is large and changes over time:
records are updated, and Works are deleted through a published deletion
ledger. It is also full of many-to-many relationships. One Work has many
authors, topics, locations and references. A naive join across them
multiplies rows and double-counts.

The platform's answer is a governed path with these properties:

1. **Bounded retrieval.** Source files are discovered from a public manifest
   and fetched one small, bounded file at a time, anonymously.
2. **Preserved source.** The original bytes are stored unchanged, with a
   SHA-256 checksum and provenance, so any step can be replayed.
3. **Portable canonical model.** Records are normalized into entities and
   relationships that do not depend on any one database engine.
4. **BigQuery analytics.** Analytical models are built in BigQuery. Gold marts
   (consumer-facing outputs) are materialized only when measured usage
   justifies it.
5. **Quality before visibility.** Hard data-quality gates must pass before
   anything becomes visible to consumers.
6. **Capabilities, not raw SQL.** Consumers use a storage-independent Data
   Service of domain capabilities, such as "look up a Work by DOI".

A lineage chain links every canonical record back to the run that wrote it,
the checksum of the bytes it came from and the source file
([lineage](../architecture/pipeline-control.md#lineage)).

> **Target, not running infrastructure.** This path is the target
> architecture, shown in the
> [diagram on How it works](how-it-works.md#the-flow-at-a-glance). Most stages
> are implemented and tested locally. Only some have cloud evidence, and none
> is deployed to production. See [Status](status.md).

## Who it is for

- **Future consumers of governed public research data:** enrichment,
  knowledge graphs, Research Intelligence applications, dashboards, APIs and
  data science
  ([system boundary](../architecture/platform-architecture.md#system-boundary)).
- **Engineers** who build, validate and extend the platform, including adding
  new public sources.

**What it is not.** Wiley's own research-paper and content ingestion, content
registry, delivery, search/vectorization, enrichment and knowledge-graph
systems stay separate. They are not replaced or implemented here. The current
stages also exclude Wiley content ingestion, internal services,
LLM/ML/GenAI/NLP, embeddings and knowledge graphs.

## Guiding principles and guardrails

Three engines have fixed roles. **BigQuery** is the first analytical engine on
Google Cloud (GCP). **DuckDB** is used only for local validation and
prototypes. **PostgreSQL or AlloyDB** is an *optional* serving store, and it
has to be justified by measurements first. The canonical data model stays
portable across all three
([three engines, three roles](how-it-works.md#three-engines-three-roles)).

| Principle | What it means in practice | Defined in |
| --- | --- | --- |
| Public or synthetic data only | No Wiley content, internal URLs, private project or dataset names, credentials or enterprise-only requirements | [Public data sources](../architecture/public-data-sources.md#public-data-and-scope-boundaries) |
| Preserve the source | Raw bytes and provenance are never overwritten. Identical replay is a no-op, and a conflicting write fails | [Immutable landing](../architecture/immutable-local-landing.md) |
| Keep canonical data portable | Entities and relationships are modelled separately, with no giant flattened table and no coupling to a serving engine | [Canonical model](../architecture/openalex-canonical-model.md) |
| BigQuery first | BigQuery is the first analytical implementation on GCP. Partitioning, clustering and materialization follow query evidence | [BigQuery-first](../architecture/gcp-bigquery-first.md#design-principles) |
| Local checks prove logic, not BigQuery | DuckDB `SEMANTIC_ONLY` results do not prove BigQuery dialect, cost or cloud readiness | [Validation kinds](../architecture/bigquery-analytical-models.md#separation-of-validation-kinds) |
| Measure before adding a store | PostgreSQL/AlloyDB is considered only if BigQuery plus an API/cache fails a measured requirement | [Operational-store gate](../architecture/query-routing.md#operational-store-gate) |
| Capabilities, not raw SQL | The Data Service has no `execute_sql`-style method; requests are typed domain models | [Data Service](../architecture/data-service.md#no-raw-sql-product-contract) |
| Evidence-honest labels | A YAML file or adapter code is not readiness evidence. `PRODUCTION_READY`, `ENTERPRISE_READY` and `VERIFIED_GCP` are forbidden without evidence | [Evidence legend](../architecture/environment-readiness.md#evidence-legend) |
| Nothing starts without authorization | No production access, no cloud resources and no next roadmap step without explicit authorization. The roadmap does not close or reopen issues | [README boundaries](../../README.md#public-data-and-delivery-boundaries), [Roadmap guardrails](../architecture/roadmap.md#architecture-decision-guardrails) |
| No credentials in the repository | No key files, `.env` or hard-coded project IDs. Cloud access goes through ADC, workload identity or service-account impersonation | [IAM and secrets](../architecture/environment-readiness.md#iam--workload-identity--secrets), [README boundaries](../../README.md#public-data-and-delivery-boundaries) and [configuration](../../README.md#configuration-and-environments), [#108 Stage 1 record](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1) |
| One source, one contract | The OpenAlex schema is never forced onto other sources. Each source gets its own verified contract and normalization | [Reusable source pattern](../architecture/public-data-sources.md#reusable-source-pattern) |

## Where things stand

All **21 roadmap steps are complete**. That means the planned contracts, local
validation, orchestration contracts and readiness assessment are done. It does
**not** mean GCP deployment is done, and it does not mean anything is
production-ready ([roadmap](../architecture/roadmap.md)).

Beyond the roadmap, the GCS and BigQuery adapters have passed live smoke tests
in a sandbox project and in personal projects labelled DEV and QA. The
BigQuery models have been deployed and measured there **on synthetic data
only**. Every environment is still `NOT_READY` overall, and every mandatory
PROD domain is `BLOCKED`. The details, with evidence levels and the open gaps,
are on the [Status](status.md) page.

## How to read these docs

1. **Start with the overview pages** in order: this page,
   [How it works](how-it-works.md), [Status](status.md), then keep the
   [Glossary](glossary.md) open. [Getting started](getting-started.md) covers
   the repository and local setup.
2. **Then use the sidebar** for the detailed architecture pages. They are
   grouped as architecture principles, sources and ingestion (in roadmap-step
   order), analytics, serving, and operations and readiness.

Some reading tips:

- **Numbers have fixed meanings.** `#NN` is a GitHub issue or pull request,
  "Step NN" is a step of the [roadmap](../architecture/roadmap.md), and
  "#108 Stage N" is a stage of issue #108
  ([naming conventions](glossary.md#naming-conventions)).
- **Capitalized labels have exact meanings.** `VERIFIED`, `MEASURED`,
  `SEMANTIC_ONLY`, `READY_TO_VALIDATE` and the rest are defined in the
  [Glossary](glossary.md). Read them literally.
- **"Recorded run" sections are the evidence.** Each gives the date, what ran,
  what passed and what was *not* covered. Identifiers such as project, bucket
  and account names are redacted.
- **Some pages are older than others.** Pages written at different times do
  not always agree, for example on the DEV/QA status. The
  [Status](status.md#known-inconsistencies-in-the-docs) page lists the known
  inconsistencies and which source is newer.
- **The site renders the repository's Markdown in place.** These pages are the
  files in the repository's `docs/` folder. Links to source files open on
  GitHub, and every page has an edit link.
