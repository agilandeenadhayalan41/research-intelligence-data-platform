# Enterprise environment inventory (DE-04 / #125)

> **Status:** documented blocker. No enterprise-managed GCP_SANDBOX, DEV or QA project exists, and no role holder is assigned.
> GCP_SANDBOX, DEV, QA, and PROD are all `NOT_READY` in the readiness registry, and this inventory changes no readiness level.
> An environment reaches the inventory verdict `READY_TO_VALIDATE` only when its project is `ENTERPRISE_MANAGED`, every fact below is `APPROVED` with an answer that does not keep it blocked, and every role holder is assigned.
> The inventory verdict (`READY_TO_VALIDATE` or `BLOCKED`) changes no readiness level and no `can_promote` decision.
> PROD is out of DE-04 scope: it is requested only when DE-27 ([#148]) can show the QA to PROD gate.

## Purpose

This page is the organizational approval gate of [#125] (DE-04). It is the non-secret record of what the enterprise-managed GCP_SANDBOX, DEV and QA environments need before any cloud validation, which owner role owes each fact, and where each approval is recorded. It answers the DE-04 inputs that [ADR 0001](../adr/0001-gcp-architecture-and-service-selection.md#ownership-and-sign-off) hands to this issue (status in the [ADR index](../adr/README.md)): the enterprise projects, the role holders, the US compute region, the org-policy facts, the shared-Composer question and the approval of budgets.

The issue's stop rule ("If approval, data sample or cloud permission is missing, report blocker and STOP") and ADR 0001 ("DE-04 records a blocker and stops") apply today: no enterprise project, budget, IAM or network approval exists, so this page records the blocker. Nothing was provisioned, deployed or simulated, and no cloud command was run.

## Scope and the non-secret rule

- **Covered:** GCP_SANDBOX, DEV and QA, each as one enterprise-managed project. PROD is requested only when DE-27 ([#148]) can show the QA to PROD gate ([ADR 0001 D3](../adr/0001-gcp-architecture-and-service-selection.md#d3-data-location-and-environment-isolation)), and LOCAL is not an enterprise environment.
- **Recorded here:** for each fact, its state, the answer for a decision, and the #125 comment that records the approval with its date; for each role, whether a holder is assigned. Resources are named only by role: the Bronze bucket, the Silver bucket, the analytical dataset `openalex`, and the identity classes of ADR 0001 D11.
- **Never recorded here:** project IDs or numbers; organization, folder or billing-account IDs; bucket names; identity, group or person names; account handles; emails; network names; IP ranges; run IDs; amounts or prices. The repository and its docs site are public.
- **Where real identifiers live:** once the Cloud platform owner provisions an enterprise project, its identifiers go to the per-environment configuration that DE-13 ([#134]) defines, reached through the symbolic names already in use (`GOOGLE_CLOUD_PROJECT`, `GCS_BUCKET`, `BIGQUERY_DATASET`) and supplied at deploy time through the DE-27 ([#148]) protected GitHub environments and DE-23 ([#144]) Secret Manager references.
- **Amounts:** budget and spend-cap amounts, quota values, per-query caps and instance limits are not published in this repository; they are held by the Cloud platform owner, and DE-26 ([#147]) sets the values ([ADR 0001 D13](../adr/0001-gcp-architecture-and-service-selection.md#d13-observability-and-cost-guardrails)). This inventory records only that a budget and a spend cap are approved, which is how the ADR's "budget amounts" input is answered in public.
- **Checks:** the validator rejects any identifier-shaped text in the YAML, and a test applies the same scanner to this page. A bare project ID without a numeric suffix, or a bare bucket name, cannot be told apart from an ordinary word; the YAML has no field that could hold one, and review of this page is the remaining control.

## Current gate result

Generated from [`config/enterprise-environments/inventory.yaml`](../../config/enterprise-environments/inventory.yaml) by `render_inventory_status` in [`enterprise_inventory.py`](../../src/research_platform/readiness/enterprise_inventory.py). A test compares this section with the YAML byte for byte, so edit the YAML and regenerate this section; never edit it by hand. Schema `enterprise-environment-inventory-v1`, as of 2026-10-16.

**Gate verdict: `BLOCKED`.** 0 of 3 enterprise environments have every fact approved.

| Environment | Enterprise project | Inventory verdict | Facts approved | Blockers |
|---|---|---|---|---|
| `GCP_SANDBOX` | `NOT_PROVISIONED` | `BLOCKED` | 0 of 31 | 37 |
| `DEV` | `NOT_PROVISIONED` | `BLOCKED` | 0 of 31 | 37 |
| `QA` | `NOT_PROVISIONED` | `BLOCKED` | 0 of 31 | 37 |
| `PROD` | out of DE-04 scope | not assessed | - | - |

Operator-owned and personal projects are not recorded here; they never count (ADR 0001 D3). An environment is blocked by its enterprise project until that is `ENTERPRISE_MANAGED`, by every fact that is not `APPROVED` or whose answer is listed under "Answers that keep an environment blocked", and by every role holder not yet assigned.

### Role holders

| Role | Holder |
|---|---|
| Lead architect | holder not yet assigned |
| Cloud platform owner | holder not yet assigned |
| Security and governance owner | holder not yet assigned |
| Data engineering owner | holder not yet assigned |
| API product owner | holder not yet assigned |

### Environment facts

One record per environment.

| Item | Group | Owner role | GCP_SANDBOX | DEV | QA |
|---|---|---|---|---|---|
| `enterprise-billing`: Project created in the enterprise organization and linked to enterprise billing, not a personal billing account | Organization and project | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `program-access`: Project accessible to the program for the approved validation work | Organization and project | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `core-apis`: APIs for the ADR 0001 selected services may be enabled, including the IAM API (not only IAM credentials) | Organization and project | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `resource-location-policy`: Org-policy resource-location constraint allows the US multi-region and the chosen US compute region | Org policy and mandates | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `key-creation-disabled`: Org policy disables service-account key creation | Org policy and mandates | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `workload-identity-federation`: Org policy on external workload identity providers (GitHub Actions OIDC federation for CI deploys) | Org policy and mandates | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `selected-services-allowed`: Org policy allows the ADR 0001 selected services (Cloud Run, Cloud Workflows, Cloud Scheduler, Cloud SQL for PostgreSQL, Secret Manager, Artifact Registry) | Org policy and mandates | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `separate-identities`: Separate deploy and runtime identities may be created (ADR 0001 D11 (a) and (b)) | Identity and access | Security and governance owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `human-access-role-groups`: Human access only through role groups (ADR 0001 D11 (c)) | Identity and access | Security and governance owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `no-cross-environment-grants`: No identity of another environment holds a grant in this project | Identity and access | Security and governance owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `bronze-bucket`: Bronze bucket in the US multi-region may be created | Cloud Storage | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `silver-bucket`: Silver bucket in the US multi-region may be created | Cloud Storage | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `analytical-dataset`: BigQuery dataset openalex in location US may be created | BigQuery | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `budget-and-alerts`: Cloud Billing budget with threshold alerts approved (the amount is held outside the repository) | Budget, quota and spend | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `spend-cap`: Spend cap for cloud actions recorded (the value is held outside the repository) | Budget, quota and spend | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `query-quotas`: Per-project and per-user BigQuery daily query quotas approved (values set in DE-26) | Budget, quota and spend | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `standing-instances`: Standing instances accepted (the Cloud SQL control instance and API minimum instances) | Budget, quota and spend | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |
| `network-model`: Network model approved (private connectivity for Cloud SQL and the egress path DE-24 approves) | Networking and egress | Cloud platform owner | `NOT_REQUESTED` | `NOT_REQUESTED` | `NOT_REQUESTED` |

### Program facts

One record covers GCP_SANDBOX, DEV and QA.

| Item | Group | Owner role | State |
|---|---|---|---|
| `separate-non-prod-projects`: Separate enterprise projects available for GCP_SANDBOX, DEV and QA | Organization and project | Cloud platform owner | `NOT_REQUESTED` |
| `us-compute-region`: US compute region for the regional services (a public region name such as us-central1) | Location | Cloud platform owner | `NOT_REQUESTED` |
| `residency-requirement`: Recorded residency or location requirement for the data | Location | Security and governance owner | `NOT_REQUESTED` |
| `in-organization-deploys`: Governance requirement that deploys originate inside the organization | Org policy and mandates | Security and governance owner | `NOT_REQUESTED` |
| `perimeter-and-cmek`: Enterprise mandate for a VPC Service Controls perimeter or customer-managed keys | Org policy and mandates | Security and governance owner | `NOT_REQUESTED` |
| `two-buckets-per-environment`: Two buckets (Bronze and Silver) per environment allowed by policy and quota | Cloud Storage | Cloud platform owner | `NOT_REQUESTED` |
| `dev-qa-control-instance`: Whether DEV and QA share one non-PROD Cloud SQL control instance | Runtime and orchestration | Cloud platform owner | `NOT_REQUESTED` |
| `shared-composer-environment`: Whether a shared enterprise Cloud Composer environment exists (ADR 0001 D6 sign-off condition) | Runtime and orchestration | Cloud platform owner | `NOT_REQUESTED` |
| `gke-mandate`: Enterprise standard that mandates GKE with an operating team | Runtime and orchestration | Lead architect | `NOT_REQUESTED` |
| `sql-transformation-mandate`: Enterprise mandate for a SQL transformation tool (Dataform or dbt) | Runtime and orchestration | Lead architect | `NOT_REQUESTED` |
| `egress-to-public-source`: Egress to the public OpenAlex source over anonymous HTTPS allowed in principle (DE-24 approves the path with allow and deny tests) | Networking and egress | Security and governance owner | `NOT_REQUESTED` |
| `source-access`: Use of the public OpenAlex source for metadata only approved (DE-05 records the licence and terms) | Source access and rights | Security and governance owner | `NOT_REQUESTED` |
| `managed-catalog`: Managed catalog (Dataplex Universal Catalog) approved, or another enterprise catalog standard named | Governance and catalog | Security and governance owner | `NOT_REQUESTED` |

### Answers that keep an environment blocked

| Item | Answer | Why it blocks |
|---|---|---|
| `workload-identity-federation` | `DISALLOWED` | engages the ADR 0001 Not selected row "GCP-hosted CI (Cloud Build or Cloud Deploy) as the primary deploy path" (owner DE-27, #148); it blocks until a superseding ADR is accepted |
| `standing-instances` | `NOT_ACCEPTED` | needs a lead architect ruling on ADR 0001 D4 and D8 (the named Firestore fallback for D4, which needs no new ADR, or a superseding ADR); it blocks until that ruling is recorded |
| `separate-non-prod-projects` | `SHARED_PROJECT_ONLY` | engages the ADR 0001 Not selected row "One shared project with per-environment datasets" (owner DE-04, #125); it blocks until a superseding ADR is accepted |
| `residency-requirement` | `EXCLUDES_US_MULTI_REGION` | engages the ADR 0001 Not selected row "Single-region or dual-region data location" (owner DE-24, #145); it blocks until a superseding ADR is accepted |
| `residency-requirement` | `REQUIRES_NON_US_LOCATION` | engages the ADR 0001 Not selected row "A non-US data location" (owner DE-24, #145); it blocks until a superseding ADR is accepted |
| `in-organization-deploys` | `REQUIRED` | engages the ADR 0001 Not selected row "GCP-hosted CI (Cloud Build or Cloud Deploy) as the primary deploy path" (owner DE-27, #148); it blocks until a superseding ADR is accepted |
| `perimeter-and-cmek` | `MANDATED` | engages the ADR 0001 Not selected row "VPC Service Controls perimeter and CMEK by default" (owner DE-24, #145); it blocks until a superseding ADR is accepted |
| `two-buckets-per-environment` | `ONE_BUCKET_ONLY` | engages the ADR 0001 Not selected row "One bucket per environment with prefix-conditioned IAM" (owner DE-04, #125); it blocks until a superseding ADR is accepted |
| `gke-mandate` | `MANDATED` | engages the ADR 0001 Not selected row "GKE (runtime or API)" (owner DE-35, #156); it blocks until a superseding ADR is accepted |
| `sql-transformation-mandate` | `DATAFORM` | engages the ADR 0001 Not selected row "Dataform with SQLX as the authoritative source" (owner DE-19, #140); it blocks until a superseding ADR is accepted |
| `sql-transformation-mandate` | `DBT` | engages the ADR 0001 Not selected row "dbt-core (dbt Cloud excluded)" (owner DE-19, #140); it blocks until a superseding ADR is accepted |

## What each owner role owes

Roles follow the [ADR 0001 ownership table](../adr/0001-gcp-architecture-and-service-selection.md#ownership-and-sign-off). Each line names every catalog item the role owes; a test checks these lists against the validator's catalog.

- **Cloud platform owner:** the enterprise project of each environment; for each environment `enterprise-billing`, `program-access`, `core-apis`, `resource-location-policy`, `key-creation-disabled`, `workload-identity-federation`, `selected-services-allowed`, `bronze-bucket`, `silver-bucket`, `analytical-dataset`, `budget-and-alerts`, `spend-cap`, `query-quotas`, `standing-instances` and `network-model`; for the program `separate-non-prod-projects`, `us-compute-region`, `two-buckets-per-environment`, `dev-qa-control-instance` and `shared-composer-environment`. ADR 0001 also names a "budget owner" and a "cost owner" without a row of their own; this inventory reads both as the Cloud platform owner, who is accountable for budgets, until the holder confirms or names another holder.
- **Security and governance owner:** for each environment `separate-identities`, `human-access-role-groups` and `no-cross-environment-grants`; for the program `residency-requirement`, `in-organization-deploys`, `perimeter-and-cmek`, `egress-to-public-source`, `source-access` and `managed-catalog`; and any time-boxed single-identity exception for a sandbox smoke run, recorded on its issue beforehand ([ADR 0001 D11](../adr/0001-gcp-architecture-and-service-selection.md#d11-identity-and-access-policy)).
- **Lead architect:** for the program `gke-mandate` and `sql-transformation-mandate`; the ruling when the standing-instances answer is `NOT_ACCEPTED`; the choice between Workflows and Composer when the shared-composer-environment answer is `AVAILABLE` ([ADR 0001 D6](../adr/0001-gcp-architecture-and-service-selection.md#d6-orchestration-cloud-workflows-triggered-by-cloud-scheduler)); and any superseding ADR that an answer under "Answers that keep an environment blocked" requires.
- **Data engineering owner:** no catalog item. The holder must still be assigned: ADR 0001 has DE-04 assign every sign-off role holder, and the validation runs that follow this gate are Data Engineering's.
- **API product owner:** no catalog item. The holder must still be assigned for the same reason; the role's own inputs belong to DE-28 ([#149]) and DE-30 ([#151]).

A `NOT_APPROVED` or `OTHER_STANDARD` answer for `managed-catalog` does not block this gate: ADR 0001 hands either outcome to DE-25 ([#146]) ([ADR 0001 D12](../adr/0001-gcp-architecture-and-service-selection.md#d12-data-and-rights-policy)). A `REJECTED` record for `selected-services-allowed` blocks this gate, and the selected service it names engages that decision's row in the ADR's [Not selected](../adr/0001-gcp-architecture-and-service-selection.md#not-selected) table.

## How an environment reaches READY_TO_VALIDATE

1. Role holders are assigned outside the repository. For each role, the repository owner posts a comment on [#125] from the template below (`Item: role-holder`), stating that the organization recorded the assignment outside the repository on a given date. The comment carries no name, account handle, email or internal ticket ID. A pull request then sets that role's `holder: ASSIGNED` with `evidence` (the comment URL and its date).
2. The Cloud platform owner requests the enterprise projects. A pull request may set facts to `REQUESTED`, with or without evidence.
3. Each approval is recorded outside the repository by the holder of the owner role, and the repository owner relays it as a [#125] comment from the template. In this single-account repository every comment is posted by the repository owner, so a comment states the role and the date, never who approved.
4. A pull request updates the YAML (`state`, `answer`, `evidence`, `as_of`), regenerates the "Current gate result" section with the snippet under [Validation](#validation), and updates `test_committed_inventory_records_the_2026_10_16_decisions`, which pins the committed state. Run `make test`, `make check`, `make build` and the docs build locally before pushing: anything pushed to a pull-request branch is public even if CI later fails, so an identifier that reaches a pushed commit counts as disclosed.
5. The user confirms each new record against the approval held outside the repository and approves the pull request explicitly. Pull-request review here is not independent, because one account owns the repository, and the validator checks the form of a record, never its truth. A change that sets a project to `ENTERPRISE_MANAGED` or a holder to `ASSIGNED` needs that explicit approval in the pull request.
6. A project is set to `ENTERPRISE_MANAGED` only when the Cloud platform owner confirms that it exists in the enterprise organization (`Item: enterprise-project` in the template). Its project ID and every other identifier go to the DE-13 ([#134]) environment configuration, never to this repository.
7. An answer listed under "Answers that keep an environment blocked" leaves the inventory verdict at `BLOCKED`. The lead architect then rules or follows the [ADR index](../adr/README.md) process for a superseding ADR, and DE-04 stops. After that ADR is accepted, the answer becomes an allowed one only through a reviewed source and test change to `GATE_RULES`; the inventory has no "resolved" state.
8. When the gate verdict is `READY_TO_VALIDATE`, that pull request closes [#125]; the cloud validation itself runs under its own approved issues.

Comment template:

```text
DE-04 inventory record
Environment: DEV            (or: all, for a program fact or a role holder)
Item: bronze-bucket         (or: enterprise-project, or: role-holder)
State: APPROVED             (or: REQUESTED, REJECTED; ASSIGNED for a role holder)
Answer: -                   (decisions only, for example TWO_BUCKETS_ALLOWED)
Owner role: Cloud platform owner
Recorded outside the repository by the holder of the owner role on 2026-11-02.
This comment holds no names, account handles, emails, identifiers, amounts or prices.
```

## What is not evidence

- The operator-owned project behind the registry's GCP_SANDBOX `VERIFIED` cells ([#91] and PR [#101], [#92] and PR [#102], [#103], [#108] Stages 1 and 2).
- The personal projects labelled DEV and QA ([#108] Stage 3, PRs [#116] and [#117]; [#118]).
- The [#108] operator-decision comment, which accepted proof-of-concept evidence for closing #108 only.
- [#23], whose closing decision rests on the #118 personal-DEV runs, and PR [#119], which recorded the operator-stated lookup requirement.
- ADR 0001 acceptance (PR [#162]): a decision is not evidence.
- A YAML entry by itself: a YAML file is not readiness evidence ([environment readiness](../architecture/environment-readiness.md)).
- The inventory verdict itself, which is not a readiness level.

The validator accepts as evidence only a comment on [#125] in this repository (`issues/125#issuecomment-N`) whose `recorded_on` date is on or after 2026-10-16 and not after the file's `as_of`. It checks that self-declared date, not the comment's timestamp. Review enforces the rest of this list.

## Prior work reused

| Prior issue / PR | What it established | Inventory items it informs | Not evidence of |
|---|---|---|---|
| [#28] / PR [#82] | Readiness model, symbolic identifiers, forbidden labels | the schema vocabulary (`EnvironmentName`, `SettingsModel`) | any cloud environment |
| [#91] / PR [#101] | Billing must be active; bucket settings precedent (uniform bucket-level access, public access prevention); a regional bucket that ADR 0001 D3 replaces with the US multi-region | `enterprise-billing`, `bronze-bucket`, `silver-bucket`, `resource-location-policy` | an enterprise project |
| [#92] / PR [#102] | API enablement, a byte cap before any job, dataset location US | `core-apis`, `analytical-dataset`, `query-quotas` | an enterprise project |
| [#103] (PRs [#104] to [#107]) | One project per environment, because the dataset name `openalex` is fixed until DE-13 | `separate-non-prod-projects`, `analytical-dataset` | an enterprise project |
| [#108] (PRs [#109] to [#111], [#113] to [#117]) | The "Operator decisions needed" table, which this catalog generalizes; keyless impersonation that used no service-account keys; IAM API enablement; the cross-environment negative probe; the personal billing project-link quota | `key-creation-disabled`, `core-apis`, `no-cross-environment-grants`, `enterprise-billing` | enterprise-managed environments, which its closing comment left open |
| [#124] / PR [#162] | ADR 0001 and its DE-04 inputs; the identifier and price patterns of `test_adr_records.py` | the whole catalog and the identifier scanner | any environment |

Code: `research_platform.readiness.models` is reused unchanged (`EnvironmentName`); the loader follows `research_platform.benchmarks.registry.load_query_pattern_registry`; `research_platform.readiness.enterprise_inventory` is new. Recorded runs: [GCS #91](../architecture/immutable-local-landing.md#recorded-run-91), [GCS #108 Stage 1](../architecture/immutable-local-landing.md#recorded-run-108-stage-1), [GCS #108 Stage 3 DEV](../architecture/immutable-local-landing.md#recorded-run-108-stage-3-dev) and [QA](../architecture/immutable-local-landing.md#recorded-run-108-stage-3-qa); [BigQuery #92](../architecture/bigquery-analytical-models.md#recorded-run-92), [#103](../architecture/bigquery-analytical-models.md#recorded-run-103), [#108 Stage 1](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-1) and [#108 Stage 2](../architecture/bigquery-analytical-models.md#recorded-run-108-stage-2).

ADR 0001 inputs and the #125 list, mapped to catalog items:

| Input | Catalog items |
|---|---|
| Enterprise projects (ADR 0001 ownership table, Cloud platform owner) | the enterprise project of each environment, `enterprise-billing`, `program-access`, `separate-non-prod-projects` |
| US compute region | `us-compute-region` |
| Org-policy facts: resource locations, key creation disabled, WIF allowed | `resource-location-policy`, `key-creation-disabled`, `workload-identity-federation` |
| Two buckets per environment | `two-buckets-per-environment`, `bronze-bucket`, `silver-bucket` |
| Whether DEV and QA share a control instance | `dev-qa-control-instance` |
| Whether a shared Composer environment exists | `shared-composer-environment` |
| Acceptance of standing instances | `standing-instances` |
| Budget and quota amounts (values set in DE-26) | `budget-and-alerts`, `spend-cap`, `query-quotas` |
| Role holders | the role holders table |
| Managed catalog, one-bucket policy, GKE mandate, separate non-PROD projects | `managed-catalog`, `two-buckets-per-environment`, `gke-mandate`, `separate-non-prod-projects` |
| #125: approved locations | `resource-location-policy`, `us-compute-region`, `residency-requirement` |
| #125: identities | `key-creation-disabled`, `workload-identity-federation`, `separate-identities`, `human-access-role-groups`, `no-cross-environment-grants` |
| #125: buckets and datasets | `two-buckets-per-environment`, `bronze-bucket`, `silver-bucket`, `analytical-dataset` |
| #125: budget alerts | `budget-and-alerts`, `spend-cap` |
| #125: VPC and egress | `network-model`, `perimeter-and-cmek`, `egress-to-public-source` |
| #125: source access and licensing | `source-access`, `egress-to-public-source` (DE-05 records the OpenAlex licence and terms) |

## Consumers

Each DE issue below needs the listed items approved before its own cloud work. A test checks these lists against the validator's catalog.

| DE issue | Needs from this inventory |
|---|---|
| DE-05 ([#126]) (direct GitHub dependent) | `egress-to-public-source`, `source-access` |
| DE-13 ([#134]) (direct GitHub dependent) | `enterprise-billing`, `program-access`, `core-apis`, `resource-location-policy`, `analytical-dataset`, `separate-non-prod-projects` |
| DE-20 ([#141]) (direct GitHub dependent) | `program-access`, `selected-services-allowed`, `us-compute-region`, `egress-to-public-source`, `source-access` |
| DE-23 ([#144]) (direct GitHub dependent) | `program-access`, `core-apis`, `key-creation-disabled`, `workload-identity-federation`, `selected-services-allowed`, `separate-identities`, `human-access-role-groups`, `no-cross-environment-grants` |
| DE-24 ([#145]) (direct GitHub dependent) | `resource-location-policy`, `bronze-bucket`, `silver-bucket`, `network-model`, `us-compute-region`, `residency-requirement`, `perimeter-and-cmek`, `two-buckets-per-environment`, `egress-to-public-source` |
| DE-34 ([#155]) (direct GitHub dependent) | `spend-cap` |
| DE-06 ([#127]) | `core-apis`, `resource-location-policy`, `bronze-bucket`, `two-buckets-per-environment`, `egress-to-public-source`, `source-access` |
| DE-07 ([#128]) | `resource-location-policy`, `silver-bucket`, `two-buckets-per-environment` |
| DE-12 ([#133]) | `selected-services-allowed`, `standing-instances`, `us-compute-region`, `dev-qa-control-instance` |
| DE-14 ([#135]) | `analytical-dataset` |
| DE-18 ([#139]) | `query-quotas` |
| DE-19 ([#140]) | `sql-transformation-mandate` |
| DE-21 ([#142]) | `selected-services-allowed`, `us-compute-region`, `shared-composer-environment` |
| DE-25 ([#146]) | `managed-catalog` |
| DE-26 ([#147]) | `enterprise-billing`, `budget-and-alerts`, `spend-cap`, `query-quotas` |
| DE-27 ([#148]) | `key-creation-disabled`, `workload-identity-federation`, `no-cross-environment-grants`, `in-organization-deploys` |
| DE-28 ([#149]) | `selected-services-allowed`, `standing-instances`, `us-compute-region` |
| DE-30 ([#151]) | `standing-instances` |
| DE-31 ([#152]) | `human-access-role-groups` |
| DE-35 ([#156]) | `gke-mandate` |

Every other cloud DE issue needs an approved environment and a recorded spend cap before any cloud action (ADR 0001 Status).

## Validation

```python
from research_platform.readiness.enterprise_inventory import (
    load_enterprise_inventory,
    render_inventory_status,
)

print(render_inventory_status(load_enterprise_inventory()), end="")
```

No cloud calls: the result derives from the YAML and the catalog in [`enterprise_inventory.py`](../../src/research_platform/readiness/enterprise_inventory.py) only. There is no command-line entry point; the tests in [`test_enterprise_environment_inventory.py`](../../tests/unit/test_enterprise_environment_inventory.py) enforce the gate and keep this page in step with the YAML, for example `test_inventory_doc_gate_section_matches_the_yaml`, `test_synthetic_all_approved_enterprise_inventory_is_ready_to_validate` and `test_operator_or_personal_ownership_is_rejected`.

## Relation to the readiness registry

The readiness registry does not read this inventory, and no readiness level or `can_promote` decision depends on it. An inventory verdict of `READY_TO_VALIDATE` for an environment is a necessary, not sufficient, precondition before any registry entry for that environment is raised on enterprise evidence: each domain still needs its own prerequisites, such as the DE-23 ([#144]) IAM matrix, and that entry's evidence cites this page and the recorded run. Wiring this rule into the registry is deferred to DE-27 ([#148]), which owns promotion gates. The existing domain-level `VERIFIED` cells on the operator-owned and personal projects keep their labels ([evidence legend](../architecture/environment-readiness.md#evidence-legend)).

## Rollback and maintenance

- Reverting the pull request that added this page removes the page, the YAML, the validator module, its tests, the sidebar entry and the pointers. No readiness level, registry entry, configuration value, cloud resource, identity or billing setting changed, so nothing else rolls back.
- An approval recorded in error is rolled back by a pull request that returns the fact to `REQUESTED` or `NOT_REQUESTED` (or the holder to `NOT_ASSIGNED`, or the project to `NOT_PROVISIONED`) and regenerates the gate section; the validator then returns `BLOCKED` (`test_withdrawn_approval_returns_the_environment_to_blocked`). A correcting comment on [#125] records the withdrawal; the earlier comments remain as history.
- The repository URL is fixed in the validator (`REPOSITORY_URL`), and the evidence pattern and the identifier scanner are built from it. If the repository moves, for example into an enterprise organization, one pull request changes that constant and rewrites the recorded evidence URLs.

## Related docs

| Topic | Doc |
|---|---|
| Environment readiness | [environment-readiness.md](../architecture/environment-readiness.md) |
| ADR 0001 (status in the [ADR index](../adr/README.md)) | [0001-gcp-architecture-and-service-selection.md](../adr/0001-gcp-architecture-and-service-selection.md) |
| Lead coverage register | [openalex-lead-coverage.md](../requirements/openalex-lead-coverage.md) |
| Placeholder environment configuration | [`config/dev.yaml`](../../config/dev.yaml) |
| Status | [status.md](../overview/status.md) |

[#23]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/23
[#28]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/28
[#82]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/82
[#91]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/91
[#92]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/92
[#101]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/101
[#102]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/102
[#103]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/103
[#104]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/104
[#107]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/107
[#108]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/108
[#109]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/109
[#111]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/111
[#113]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/113
[#116]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/116
[#117]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/117
[#118]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/118
[#119]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/119
[#124]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/124
[#125]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/125
[#126]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/126
[#127]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/127
[#128]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/128
[#133]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/133
[#134]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/134
[#135]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/135
[#139]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/139
[#140]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/140
[#141]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/141
[#142]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/142
[#144]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/144
[#145]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/145
[#146]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/146
[#147]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/147
[#148]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/148
[#149]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/149
[#151]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/151
[#152]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/152
[#155]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/155
[#156]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/156
[#162]: https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/pull/162
