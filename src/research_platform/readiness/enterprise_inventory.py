"""Enterprise environment inventory for DE-04 (#125): gate rules and validator.

The inventory is a non-secret approval record, not environment configuration
and not readiness evidence. For the enterprise-managed GCP_SANDBOX, DEV and QA
environments it records which facts are approved, which owner role owes each
one, and the #125 comment where each approval is recorded. It never holds
project IDs, bucket names, identities, emails, amounts or prices.

``GATE_RULES`` is the only item catalog. The YAML records states, answers and
evidence references for these ids and nothing else, so weakening the gate needs
a reviewed source and test change. Every function here is pure, offline and
deterministic: no clock, network, environment variable or cloud SDK. The
readiness registry does not reference this module, and the inventory verdict is
not a readiness level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError

from research_platform.readiness.models import EnvironmentName
from research_platform.service.models import SettingsModel

INVENTORY_SCHEMA_VERSION = "enterprise-environment-inventory-v1"
INVENTORY_ENVIRONMENTS: tuple[EnvironmentName, ...] = (
    EnvironmentName.GCP_SANDBOX,
    EnvironmentName.DEV,
    EnvironmentName.QA,
)
ADR_0001_ACCEPTED_ON = date(2026, 10, 16)
REPOSITORY_URL = "https://github.com/agilandeenadhayalan41/research-intelligence-data-platform"
GATE_ISSUE = 125
_REPOSITORY_URL_RE = REPOSITORY_URL.replace(".", r"\.")
# Evidence is a comment on the gate issue in this repository, and nothing else.
EVIDENCE_REF_PATTERN = (
    rf"^{_REPOSITORY_URL_RE}/issues/{GATE_ISSUE}#issuecomment-[1-9][0-9]{{0,19}}$"
)
GATE_SECTION_HEADING = "## Current gate result"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INVENTORY_PATH = _REPO_ROOT / "config" / "enterprise-environments" / "inventory.yaml"

INVENTORY_HEADER = (
    "# Enterprise environment inventory for DE-04 (#125): a non-secret approval record.\n"
    "# For the enterprise-managed GCP_SANDBOX, DEV and QA environments it records which\n"
    "# facts are approved and where each approval is recorded (a comment on #125).\n"
    "# It is not environment configuration and not readiness evidence: nothing deploys\n"
    "# from it, and the readiness registry does not read it.\n"
    "# Never add project IDs or numbers, organization, folder or billing-account IDs,\n"
    "# bucket names, identity or group names, emails, network names, IP ranges, run IDs,\n"
    "# amounts or prices. Real identifiers live in the DE-13 (#134) environment\n"
    "# configuration and in Secret Manager (DE-23, #144), never in this repository.\n"
    "# Comments are allowed only in this header. The item catalog, owner roles and\n"
    "# allowed answers are defined in src/research_platform/readiness/enterprise_inventory.py.\n"
    "# Rendered page: docs/operations/enterprise-environment-inventory.md\n"
)


class InventoryVerdict(StrEnum):
    """The DE-04 gate result; deliberately separate from the readiness enums."""

    READY_TO_VALIDATE = "READY_TO_VALIDATE"
    BLOCKED = "BLOCKED"


class Ownership(StrEnum):
    ENTERPRISE_MANAGED = "ENTERPRISE_MANAGED"
    NOT_PROVISIONED = "NOT_PROVISIONED"
    # Named only so that the validator can reject them with a specific message.
    OPERATOR_OWNED = "OPERATOR_OWNED"
    PERSONAL = "PERSONAL"


class FactState(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OwnerRole(StrEnum):
    LEAD_ARCHITECT = "LEAD_ARCHITECT"
    CLOUD_PLATFORM_OWNER = "CLOUD_PLATFORM_OWNER"
    SECURITY_GOVERNANCE_OWNER = "SECURITY_GOVERNANCE_OWNER"
    DATA_ENGINEERING_OWNER = "DATA_ENGINEERING_OWNER"
    API_PRODUCT_OWNER = "API_PRODUCT_OWNER"


class HolderState(StrEnum):
    NOT_ASSIGNED = "NOT_ASSIGNED"
    ASSIGNED = "ASSIGNED"


class ItemScope(StrEnum):
    ENVIRONMENT = "ENVIRONMENT"
    PROGRAM = "PROGRAM"


class ItemKind(StrEnum):
    APPROVAL = "APPROVAL"
    DECISION = "DECISION"


class AnswerFormat(StrEnum):
    CHOICE = "CHOICE"
    US_COMPUTE_REGION = "US_COMPUTE_REGION"


class ItemGroup(StrEnum):
    ORGANIZATION_PROJECT = "ORGANIZATION_PROJECT"
    LOCATION = "LOCATION"
    ORG_POLICY = "ORG_POLICY"
    IDENTITY_ACCESS = "IDENTITY_ACCESS"
    STORAGE = "STORAGE"
    BIGQUERY = "BIGQUERY"
    BUDGET_QUOTA = "BUDGET_QUOTA"
    NETWORKING = "NETWORKING"
    RUNTIME_ORCHESTRATION = "RUNTIME_ORCHESTRATION"
    SOURCE_ACCESS = "SOURCE_ACCESS"
    GOVERNANCE_CATALOG = "GOVERNANCE_CATALOG"


class BlockerKind(StrEnum):
    OWNERSHIP = "OWNERSHIP"
    FACT = "FACT"
    REVISIT = "REVISIT"
    ROLE_HOLDER = "ROLE_HOLDER"


ROLE_LABELS: dict[OwnerRole, str] = {
    OwnerRole.LEAD_ARCHITECT: "Lead architect",
    OwnerRole.CLOUD_PLATFORM_OWNER: "Cloud platform owner",
    OwnerRole.SECURITY_GOVERNANCE_OWNER: "Security and governance owner",
    OwnerRole.DATA_ENGINEERING_OWNER: "Data engineering owner",
    OwnerRole.API_PRODUCT_OWNER: "API product owner",
}
GROUP_LABELS: dict[ItemGroup, str] = {
    ItemGroup.ORGANIZATION_PROJECT: "Organization and project",
    ItemGroup.LOCATION: "Location",
    ItemGroup.ORG_POLICY: "Org policy and mandates",
    ItemGroup.IDENTITY_ACCESS: "Identity and access",
    ItemGroup.STORAGE: "Cloud Storage",
    ItemGroup.BIGQUERY: "BigQuery",
    ItemGroup.BUDGET_QUOTA: "Budget, quota and spend",
    ItemGroup.NETWORKING: "Networking and egress",
    ItemGroup.RUNTIME_ORCHESTRATION: "Runtime and orchestration",
    ItemGroup.SOURCE_ACCESS: "Source access and rights",
    ItemGroup.GOVERNANCE_CATALOG: "Governance and catalog",
}
# Every ADR 0001 sign-off role is required: DE-04 assigns all of their holders.
ROLE_ORDER: tuple[OwnerRole, ...] = tuple(OwnerRole)
US_COMPUTE_REGION_RE = re.compile(r"^us-(?:central|east|south|west)[1-9]$")


@dataclass(frozen=True)
class RevisitAnswer:
    """An allowed answer that keeps every affected environment blocked."""

    answer: str
    # The verbatim "Technology or option" cell of an ADR 0001 "Not selected"
    # row, or None when the answer needs a lead-architect ruling instead.
    adr_row: str | None
    # That row's "Owner DE issue" cell as (DE id, issue number); None for a ruling.
    owner: tuple[str, int] | None = None
    ruling: str = ""

    @property
    def why(self) -> str:
        if self.adr_row is not None and self.owner is not None:
            de, number = self.owner
            return (
                f'engages the ADR 0001 Not selected row "{self.adr_row}" '
                f"(owner {de}, #{number}); it blocks until a superseding ADR is accepted"
            )
        return self.ruling


@dataclass(frozen=True)
class GateRule:
    """One catalog item: what must be approved, by whom, and which answers block."""

    item: str
    title: str
    group: ItemGroup
    scope: ItemScope
    kind: ItemKind
    owner_role: OwnerRole
    adr_refs: tuple[str, ...]
    consumers: tuple[str, ...]
    answer_format: AnswerFormat | None = None
    answers: tuple[str, ...] = ()
    revisit: tuple[RevisitAnswer, ...] = ()

    def allows(self, answer: str) -> bool:
        if self.answer_format is AnswerFormat.US_COMPUTE_REGION:
            return US_COMPUTE_REGION_RE.fullmatch(answer) is not None
        return answer in self.answers

    def revisit_for(self, answer: str | None) -> RevisitAnswer | None:
        return next((r for r in self.revisit if r.answer == answer), None)


_ENV = ItemScope.ENVIRONMENT
_PROGRAM = ItemScope.PROGRAM
_APPROVAL = ItemKind.APPROVAL
_DECISION = ItemKind.DECISION
_CHOICE = AnswerFormat.CHOICE
_LA = OwnerRole.LEAD_ARCHITECT
_CPO = OwnerRole.CLOUD_PLATFORM_OWNER
_SGO = OwnerRole.SECURITY_GOVERNANCE_OWNER
_GCP_HOSTED_CI = "GCP-hosted CI (Cloud Build or Cloud Deploy) as the primary deploy path"
_STANDING_INSTANCES_RULING = (
    "needs a lead architect ruling on ADR 0001 D4 and D8 (the named Firestore fallback "
    "for D4, which needs no new ADR, or a superseding ADR); it blocks until that ruling "
    "is recorded"
)

GATE_RULES: tuple[GateRule, ...] = (
    # --- ENVIRONMENT scope: one record each for GCP_SANDBOX, DEV and QA ---------
    GateRule(
        item="enterprise-billing",
        title="Project created in the enterprise organization and linked to enterprise "
        "billing, not a personal billing account",
        group=ItemGroup.ORGANIZATION_PROJECT, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D3",), consumers=("DE-13", "DE-26"),
    ),
    GateRule(
        item="program-access",
        title="Project accessible to the program for the approved validation work",
        group=ItemGroup.ORGANIZATION_PROJECT, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("Status",), consumers=("DE-13", "DE-20", "DE-23"),
    ),
    GateRule(
        item="core-apis",
        title="APIs for the ADR 0001 selected services may be enabled, including the IAM API "
        "(not only IAM credentials)",
        group=ItemGroup.ORGANIZATION_PROJECT, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D1", "D2", "D11"), consumers=("DE-06", "DE-13", "DE-23"),
    ),
    GateRule(
        item="resource-location-policy",
        title="Org-policy resource-location constraint allows the US multi-region and the "
        "chosen US compute region",
        group=ItemGroup.ORG_POLICY, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D3",), consumers=("DE-06", "DE-07", "DE-13", "DE-24"),
    ),
    GateRule(
        item="key-creation-disabled",
        title="Org policy disables service-account key creation",
        group=ItemGroup.ORG_POLICY, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D11",), consumers=("DE-23", "DE-27"),
    ),
    GateRule(
        item="workload-identity-federation",
        title="Org policy on external workload identity providers (GitHub Actions OIDC "
        "federation for CI deploys)",
        group=ItemGroup.ORG_POLICY, scope=_ENV, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D11",), consumers=("DE-23", "DE-27"),
        answer_format=_CHOICE, answers=("ALLOWED", "DISALLOWED"),
        revisit=(RevisitAnswer("DISALLOWED", _GCP_HOSTED_CI, ("DE-27", 148)),),
    ),
    GateRule(
        item="selected-services-allowed",
        title="Org policy allows the ADR 0001 selected services (Cloud Run, Cloud Workflows, "
        "Cloud Scheduler, Cloud SQL for PostgreSQL, Secret Manager, Artifact Registry)",
        group=ItemGroup.ORG_POLICY, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D4", "D5", "D6", "D8", "D11"),
        consumers=("DE-12", "DE-20", "DE-21", "DE-23", "DE-28"),
    ),
    GateRule(
        item="separate-identities",
        title="Separate deploy and runtime identities may be created (ADR 0001 D11 (a) and (b))",
        group=ItemGroup.IDENTITY_ACCESS, scope=_ENV, kind=_APPROVAL, owner_role=_SGO,
        adr_refs=("D11",), consumers=("DE-23",),
    ),
    GateRule(
        item="human-access-role-groups",
        title="Human access only through role groups (ADR 0001 D11 (c))",
        group=ItemGroup.IDENTITY_ACCESS, scope=_ENV, kind=_APPROVAL, owner_role=_SGO,
        adr_refs=("D11",), consumers=("DE-23", "DE-31"),
    ),
    GateRule(
        item="no-cross-environment-grants",
        title="No identity of another environment holds a grant in this project",
        group=ItemGroup.IDENTITY_ACCESS, scope=_ENV, kind=_APPROVAL, owner_role=_SGO,
        adr_refs=("D3", "D11"), consumers=("DE-23", "DE-27"),
    ),
    GateRule(
        item="bronze-bucket",
        title="Bronze bucket in the US multi-region may be created",
        group=ItemGroup.STORAGE, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D1",), consumers=("DE-06", "DE-24"),
    ),
    GateRule(
        item="silver-bucket",
        title="Silver bucket in the US multi-region may be created",
        group=ItemGroup.STORAGE, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D1",), consumers=("DE-07", "DE-24"),
    ),
    GateRule(
        item="analytical-dataset",
        title="BigQuery dataset openalex in location US may be created",
        group=ItemGroup.BIGQUERY, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D2",), consumers=("DE-13", "DE-14"),
    ),
    GateRule(
        item="budget-and-alerts",
        title="Cloud Billing budget with threshold alerts approved (the amount is held outside "
        "the repository)",
        group=ItemGroup.BUDGET_QUOTA, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D13",), consumers=("DE-26",),
    ),
    GateRule(
        item="spend-cap",
        title="Spend cap for cloud actions recorded (the value is held outside the repository)",
        group=ItemGroup.BUDGET_QUOTA, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("Status", "D13"), consumers=("DE-26", "DE-34"),
    ),
    GateRule(
        item="query-quotas",
        title="Per-project and per-user BigQuery daily query quotas approved (values set in DE-26)",
        group=ItemGroup.BUDGET_QUOTA, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D13",), consumers=("DE-18", "DE-26"),
    ),
    GateRule(
        item="standing-instances",
        title="Standing instances accepted (the Cloud SQL control instance and API minimum "
        "instances)",
        group=ItemGroup.BUDGET_QUOTA, scope=_ENV, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D4", "D8"), consumers=("DE-12", "DE-28", "DE-30"),
        answer_format=_CHOICE, answers=("ACCEPTED", "NOT_ACCEPTED"),
        revisit=(RevisitAnswer("NOT_ACCEPTED", None, None, _STANDING_INSTANCES_RULING),),
    ),
    GateRule(
        item="network-model",
        title="Network model approved (private connectivity for Cloud SQL and the egress path "
        "DE-24 approves)",
        group=ItemGroup.NETWORKING, scope=_ENV, kind=_APPROVAL, owner_role=_CPO,
        adr_refs=("D3", "D4"), consumers=("DE-24",),
    ),
    # --- PROGRAM scope: one record covers GCP_SANDBOX, DEV and QA ---------------
    GateRule(
        item="separate-non-prod-projects",
        title="Separate enterprise projects available for GCP_SANDBOX, DEV and QA",
        group=ItemGroup.ORGANIZATION_PROJECT, scope=_PROGRAM, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D3",), consumers=("DE-13",),
        answer_format=_CHOICE, answers=("SEPARATE_PROJECTS", "SHARED_PROJECT_ONLY"),
        revisit=(
            RevisitAnswer(
                "SHARED_PROJECT_ONLY", "One shared project with per-environment datasets",
                ("DE-04", 125),
            ),
        ),
    ),
    GateRule(
        item="us-compute-region",
        title="US compute region for the regional services (a public region name such as "
        "us-central1)",
        group=ItemGroup.LOCATION, scope=_PROGRAM, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D3",), consumers=("DE-12", "DE-20", "DE-21", "DE-24", "DE-28"),
        answer_format=AnswerFormat.US_COMPUTE_REGION,
    ),
    GateRule(
        item="residency-requirement",
        title="Recorded residency or location requirement for the data",
        group=ItemGroup.LOCATION, scope=_PROGRAM, kind=_DECISION, owner_role=_SGO,
        adr_refs=("D3",), consumers=("DE-24",),
        answer_format=_CHOICE,
        answers=("NONE_RECORDED", "EXCLUDES_US_MULTI_REGION", "REQUIRES_NON_US_LOCATION"),
        revisit=(
            RevisitAnswer(
                "EXCLUDES_US_MULTI_REGION", "Single-region or dual-region data location",
                ("DE-24", 145),
            ),
            RevisitAnswer("REQUIRES_NON_US_LOCATION", "A non-US data location", ("DE-24", 145)),
        ),
    ),
    GateRule(
        item="in-organization-deploys",
        title="Governance requirement that deploys originate inside the organization",
        group=ItemGroup.ORG_POLICY, scope=_PROGRAM, kind=_DECISION, owner_role=_SGO,
        adr_refs=("D11",), consumers=("DE-27",),
        answer_format=_CHOICE, answers=("NOT_REQUIRED", "REQUIRED"),
        revisit=(RevisitAnswer("REQUIRED", _GCP_HOSTED_CI, ("DE-27", 148)),),
    ),
    GateRule(
        item="perimeter-and-cmek",
        title="Enterprise mandate for a VPC Service Controls perimeter or customer-managed keys",
        group=ItemGroup.ORG_POLICY, scope=_PROGRAM, kind=_DECISION, owner_role=_SGO,
        adr_refs=("D12",), consumers=("DE-24",),
        answer_format=_CHOICE, answers=("NOT_MANDATED", "MANDATED"),
        revisit=(
            RevisitAnswer(
                "MANDATED", "VPC Service Controls perimeter and CMEK by default", ("DE-24", 145)
            ),
        ),
    ),
    GateRule(
        item="two-buckets-per-environment",
        title="Two buckets (Bronze and Silver) per environment allowed by policy and quota",
        group=ItemGroup.STORAGE, scope=_PROGRAM, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D1",), consumers=("DE-06", "DE-07", "DE-24"),
        answer_format=_CHOICE, answers=("TWO_BUCKETS_ALLOWED", "ONE_BUCKET_ONLY"),
        revisit=(
            RevisitAnswer(
                "ONE_BUCKET_ONLY", "One bucket per environment with prefix-conditioned IAM",
                ("DE-04", 125),
            ),
        ),
    ),
    GateRule(
        item="dev-qa-control-instance",
        title="Whether DEV and QA share one non-PROD Cloud SQL control instance",
        group=ItemGroup.RUNTIME_ORCHESTRATION, scope=_PROGRAM, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D4",), consumers=("DE-12",),
        answer_format=_CHOICE, answers=("SHARED_NON_PROD_INSTANCE", "SEPARATE_INSTANCES"),
    ),
    GateRule(
        item="shared-composer-environment",
        title="Whether a shared enterprise Cloud Composer environment exists (ADR 0001 D6 "
        "sign-off condition)",
        group=ItemGroup.RUNTIME_ORCHESTRATION, scope=_PROGRAM, kind=_DECISION, owner_role=_CPO,
        adr_refs=("D6",), consumers=("DE-21",),
        answer_format=_CHOICE, answers=("AVAILABLE", "NOT_AVAILABLE"),
    ),
    GateRule(
        item="gke-mandate",
        title="Enterprise standard that mandates GKE with an operating team",
        group=ItemGroup.RUNTIME_ORCHESTRATION, scope=_PROGRAM, kind=_DECISION, owner_role=_LA,
        adr_refs=("D5", "D8", "D10"), consumers=("DE-35",),
        answer_format=_CHOICE, answers=("NONE", "MANDATED"),
        revisit=(RevisitAnswer("MANDATED", "GKE (runtime or API)", ("DE-35", 156)),),
    ),
    GateRule(
        item="sql-transformation-mandate",
        title="Enterprise mandate for a SQL transformation tool (Dataform or dbt)",
        group=ItemGroup.RUNTIME_ORCHESTRATION, scope=_PROGRAM, kind=_DECISION, owner_role=_LA,
        adr_refs=("D7",), consumers=("DE-19",),
        answer_format=_CHOICE, answers=("NONE", "DATAFORM", "DBT"),
        revisit=(
            RevisitAnswer(
                "DATAFORM", "Dataform with SQLX as the authoritative source", ("DE-19", 140)
            ),
            RevisitAnswer("DBT", "dbt-core (dbt Cloud excluded)", ("DE-19", 140)),
        ),
    ),
    GateRule(
        item="egress-to-public-source",
        title="Egress to the public OpenAlex source over anonymous HTTPS allowed in principle "
        "(DE-24 approves the path with allow and deny tests)",
        group=ItemGroup.NETWORKING, scope=_PROGRAM, kind=_APPROVAL, owner_role=_SGO,
        adr_refs=("D3", "D5"), consumers=("DE-05", "DE-06", "DE-20", "DE-24"),
    ),
    GateRule(
        item="source-access",
        title="Use of the public OpenAlex source for metadata only approved (DE-05 records the "
        "licence and terms)",
        group=ItemGroup.SOURCE_ACCESS, scope=_PROGRAM, kind=_APPROVAL, owner_role=_SGO,
        adr_refs=("D3", "D12"), consumers=("DE-05", "DE-06", "DE-20"),
    ),
    GateRule(
        item="managed-catalog",
        title="Managed catalog (Dataplex Universal Catalog) approved, or another enterprise "
        "catalog standard named",
        group=ItemGroup.GOVERNANCE_CATALOG, scope=_PROGRAM, kind=_DECISION, owner_role=_SGO,
        adr_refs=("D12",), consumers=("DE-25",),
        answer_format=_CHOICE,
        answers=("MANAGED_CATALOG_APPROVED", "NOT_APPROVED", "OTHER_STANDARD"),
    ),
)
_RULES: dict[str, GateRule] = {rule.item: rule for rule in GATE_RULES}
ENVIRONMENT_ITEMS: tuple[str, ...] = tuple(r.item for r in GATE_RULES if r.scope is _ENV)
PROGRAM_ITEMS: tuple[str, ...] = tuple(r.item for r in GATE_RULES if r.scope is _PROGRAM)
FACTS_PER_ENVIRONMENT = len(GATE_RULES)

_ITEM_ID = r"^[a-z][a-z0-9-]{1,63}$"
_ANSWER = r"^[A-Za-z0-9_-]{1,40}$"


class EvidenceRef(SettingsModel):
    ref: str = Field(max_length=200, pattern=EVIDENCE_REF_PATTERN)
    recorded_on: date


class FactRecord(SettingsModel):
    item: str = Field(pattern=_ITEM_ID)
    state: FactState
    answer: str | None = Field(default=None, pattern=_ANSWER)
    evidence: EvidenceRef | None = None


class RoleHolderRecord(SettingsModel):
    role: OwnerRole
    holder: HolderState
    evidence: EvidenceRef | None = None


class EnvironmentRecord(SettingsModel):
    environment: EnvironmentName
    ownership: Ownership
    ownership_evidence: EvidenceRef | None = None
    facts: tuple[FactRecord, ...]


class EnterpriseEnvironmentInventory(SettingsModel):
    schema_version: Literal["enterprise-environment-inventory-v1"]
    as_of: date
    role_holders: tuple[RoleHolderRecord, ...]
    program_facts: tuple[FactRecord, ...]
    environments: tuple[EnvironmentRecord, ...]


INVENTORY_MODELS = (
    EvidenceRef,
    FactRecord,
    RoleHolderRecord,
    EnvironmentRecord,
    EnterpriseEnvironmentInventory,
)
_DECLARED_FIELDS: frozenset[str] = frozenset(
    name for model in INVENTORY_MODELS for name in model.model_fields
)
_TUPLE_FIELDS: frozenset[str] = frozenset({"role_holders", "program_facts", "environments", "facts"})


class InventoryBlocker(SettingsModel):
    kind: BlockerKind
    environment: EnvironmentName
    scope: ItemScope
    subject: str
    owner_role: OwnerRole
    reason: str

    def describe(self) -> str:
        role = ROLE_LABELS[self.owner_role]
        return f"{self.environment.value}: {self.subject} ({role}): {self.reason}"


class EnvironmentAssessment(SettingsModel):
    environment: EnvironmentName
    ownership: Ownership
    verdict: InventoryVerdict
    facts_total: int
    facts_approved: int
    blockers: tuple[InventoryBlocker, ...]


class InventoryAssessment(SettingsModel):
    schema_version: str
    as_of: date
    gate: InventoryVerdict
    environments: tuple[EnvironmentAssessment, ...]


class EnterpriseInventoryError(ValueError):
    """An invalid inventory; ``errors`` never echo a rejected value."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("enterprise environment inventory is invalid: " + "; ".join(errors))


# --- identifier scanning ------------------------------------------------------

# Links to an issue, a pull request or an issue comment of this repository, and
# nothing glued to them; masked so that comment ids are not read as numbers.
_OWN_REFERENCE_RE = re.compile(
    rf"{_REPOSITORY_URL_RE}/(?:issues|pull)/[1-9][0-9]{{0,5}}"
    rf"(?:#issuecomment-[1-9][0-9]{{0,19}})?(?![\w/?&=%@.:+,#-])"
)
PRIVATE_IDENTIFIER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
     "an email address"),
    (re.compile(r"\bgs://", re.IGNORECASE), "a gs:// bucket URL"),
    (re.compile(r"\.iam\.gserviceaccount\.com\b", re.IGNORECASE), "a service-account identity"),
    (re.compile(r"\bprojects/[a-z][a-z0-9-]{4,28}[a-z0-9]\b"), "a projects/<id> path"),
    (re.compile(r"\b(?:serviceAccount|user|group|domain|deleted):[A-Za-z0-9._%+-]+@"),
     "an IAM member"),
    (re.compile(r"\bprincipal(?:Set)?://"), "a workload identity principal"),
    (re.compile(r"\b(?:organizations|folders|billingAccounts)/[A-Za-z0-9-]+"),
     "an organization, folder or billing-account path"),
    (re.compile(r"\b[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b", re.IGNORECASE),
     "a billing-account ID"),
    (re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d{4,}(?![\w-])"), "a project-ID-like token"),
    (re.compile(r"\b\d{10,}\b"), "a project-number-like token"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "an IP address"),
    (re.compile(r"\bi\d{3}-20\d{6}(?:t\d{6})?\b", re.IGNORECASE), "a run ID"),
    (re.compile(r"\$\{"), "an environment-variable reference"),
    (re.compile(r"[$€£¥]|\b(?:USD|EUR|GBP|dollars?|cents?)\b", re.IGNORECASE),
     "a currency sign or word"),
    (re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:per|/)\s*(?:GiB|GB|TiB|TB|vCPU|hour|month)\b",
                re.IGNORECASE), "a unit price"),
)


def private_identifier_errors(text: str) -> tuple[str, ...]:
    """Line-numbered findings; the matched value is never echoed."""
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        masked = _OWN_REFERENCE_RE.sub("<ref>", line)
        for pattern, kind in PRIVATE_IDENTIFIER_PATTERNS:
            if pattern.search(masked):
                errors.append(f"line {number}: contains {kind}")
    return tuple(errors)


# --- parsing ------------------------------------------------------------------


class _DuplicateKeyError(yaml.constructor.ConstructorError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of keeping the last."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        seen: list[Any] = []
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise _DuplicateKeyError(None, None, "duplicate key", key_node.start_mark)
            seen.append(key)
        return super().construct_mapping(node, deep=deep)


_COMMENT_RE = re.compile(r"(?:^|\s)#")


def _yaml_line(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    return "" if mark is None else f" (line {mark.line + 1})"


def _loc(error: Any) -> str:
    """The error location, with every key that is not a declared field masked."""
    loc = list(error["loc"])
    parts: list[str] = []
    for index, part in enumerate(loc):
        previous = loc[index - 1] if index else None
        if error["type"] == "extra_forbidden" and index == len(loc) - 1:
            keep = False
        elif previous in _TUPLE_FIELDS:
            keep = isinstance(part, int) and not isinstance(part, bool)
        else:
            keep = isinstance(part, str) and part in _DECLARED_FIELDS
        parts.append(str(part) if keep else "<unknown key>")
    return ".".join(parts)


def _validate(text: str) -> tuple[EnterpriseEnvironmentInventory | None, tuple[str, ...]]:
    errors: list[str] = list(private_identifier_errors(text))
    header_lines = 0
    if text.startswith(INVENTORY_HEADER):
        header_lines = INVENTORY_HEADER.count("\n")
    else:
        errors.append("inventory: must start with the fixed header comment (INVENTORY_HEADER)")
    for number, line in enumerate(text.splitlines(), start=1):
        if number > header_lines and _COMMENT_RE.search(line):
            errors.append(f"line {number}: comments are allowed only in the fixed header")
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader subclass
    except _DuplicateKeyError as exc:
        return None, (*errors, f"inventory: duplicate key{_yaml_line(exc)}")
    except yaml.YAMLError as exc:
        return None, (*errors, f"inventory: not valid YAML{_yaml_line(exc)}")
    if not isinstance(raw, dict):
        return None, (*errors, "inventory: must be a YAML mapping")
    try:
        inventory = EnterpriseEnvironmentInventory.model_validate(raw)
    except ValidationError as exc:
        flat = [
            f"{_loc(e)}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        ]
        return None, (*errors, *flat)
    errors.extend(_semantic_errors(inventory))
    return (None if errors else inventory), tuple(errors)


def _evidence_errors(path: str, evidence: EvidenceRef | None, as_of: date) -> list[str]:
    if evidence is None:
        return []
    errors: list[str] = []
    if evidence.recorded_on < ADR_0001_ACCEPTED_ON:
        errors.append(f"{path}: recorded before ADR 0001 acceptance (2026-10-16)")
    if evidence.recorded_on > as_of:
        errors.append(f"{path}: recorded after as_of")
    return errors


def _fact_errors(path: str, fact: FactRecord, rule: GateRule, as_of: date) -> list[str]:
    errors: list[str] = []
    if fact.state is FactState.NOT_REQUESTED and fact.evidence is not None:
        errors.append(f"{path}: NOT_REQUESTED must not carry evidence")
    if fact.state in (FactState.APPROVED, FactState.REJECTED) and fact.evidence is None:
        errors.append(f"{path}: {fact.state.value} needs evidence")
    if fact.state is FactState.REJECTED and rule.kind is _DECISION:
        errors.append(f"{path}: REJECTED applies to APPROVAL items only")
    if rule.kind is _APPROVAL:
        if fact.answer is not None:
            errors.append(f"{path}: an APPROVAL item takes no answer")
    elif fact.state is FactState.APPROVED and fact.answer is None:
        errors.append(f"{path}: an APPROVED decision needs an answer")
    elif fact.state is not FactState.APPROVED and fact.answer is not None:
        errors.append(f"{path}: only an APPROVED decision carries an answer")
    elif fact.answer is not None and not rule.allows(fact.answer):
        errors.append(f"{path}: answer is not an allowed answer")
    errors.extend(_evidence_errors(f"{path}.evidence", fact.evidence, as_of))
    return errors


def _fact_list_errors(
    loc: str, facts: tuple[FactRecord, ...], scope: ItemScope, as_of: date
) -> list[str]:
    errors: list[str] = []
    expected = ENVIRONMENT_ITEMS if scope is _ENV else PROGRAM_ITEMS
    seen: list[str] = []
    structural = False
    for index, fact in enumerate(facts):
        rule = _RULES.get(fact.item)
        if rule is None:
            # Never echo an unknown id: a project ID also matches the id pattern.
            errors.append(f"{loc}[{index}]: not a gate-rule item")
            structural = True
        elif rule.scope is not scope:
            article = "a PROGRAM" if rule.scope is _PROGRAM else "an ENVIRONMENT"
            errors.append(f"{loc}[{index}]: {fact.item} is {article} item")
            structural = True
        elif fact.item in seen:
            errors.append(f"{loc}: duplicate {fact.item}")
            structural = True
        else:
            seen.append(fact.item)
            errors.extend(_fact_errors(f"{loc}.{fact.item}", fact, rule, as_of))
    for item in expected:
        if item not in seen:
            errors.append(f"{loc}: missing {item}")
            structural = True
    if not structural and tuple(seen) != expected:
        errors.append(f"{loc}: must follow the gate-rule order")
    return errors


def _semantic_errors(inventory: EnterpriseEnvironmentInventory) -> tuple[str, ...]:
    errors: list[str] = []
    as_of = inventory.as_of
    if as_of < ADR_0001_ACCEPTED_ON:
        errors.append("as_of: before ADR 0001 acceptance (2026-10-16)")
    if tuple(record.role for record in inventory.role_holders) != ROLE_ORDER:
        errors.append(
            "role_holders: must list "
            + ", ".join(role.value for role in ROLE_ORDER)
            + " once each, in that order"
        )
    for record in inventory.role_holders:
        path = f"role_holders.{record.role.value}"
        if record.holder is HolderState.ASSIGNED and record.evidence is None:
            errors.append(f"{path}: ASSIGNED needs evidence")
        if record.holder is HolderState.NOT_ASSIGNED and record.evidence is not None:
            errors.append(f"{path}: NOT_ASSIGNED must not carry evidence")
        errors.extend(_evidence_errors(f"{path}.evidence", record.evidence, as_of))
    errors.extend(_fact_list_errors("program_facts", inventory.program_facts, _PROGRAM, as_of))
    names = [record.environment for record in inventory.environments]
    if EnvironmentName.LOCAL in names:
        errors.append("environments: LOCAL is not an enterprise environment")
    if EnvironmentName.PROD in names:
        errors.append(
            "environments: PROD is out of DE-04 scope (ADR 0001 D3: requested only when "
            "DE-27 [#148] can show the QA to PROD gate)"
        )
    listed = [name for name in names if name in INVENTORY_ENVIRONMENTS]
    duplicates = [name for i, name in enumerate(listed) if name in listed[:i]]
    for name in dict.fromkeys(duplicates):
        errors.append(f"environments: duplicate {name.value}")
    missing = [name for name in INVENTORY_ENVIRONMENTS if name not in listed]
    for name in missing:
        errors.append(f"environments: missing {name.value}")
    if not duplicates and not missing and tuple(listed) != INVENTORY_ENVIRONMENTS:
        errors.append("environments: must be listed in the order GCP_SANDBOX, DEV, QA")
    checked: set[EnvironmentName] = set()
    for record in inventory.environments:
        env = record.environment
        if env not in INVENTORY_ENVIRONMENTS or env in checked:
            continue
        checked.add(env)
        path = f"environments.{env.value}"
        if record.ownership in (Ownership.OPERATOR_OWNED, Ownership.PERSONAL):
            errors.append(
                f"{path}: {record.ownership.value} never counts as an enterprise environment "
                "(ADR 0001 D3); record NOT_PROVISIONED"
            )
        elif record.ownership is Ownership.ENTERPRISE_MANAGED and record.ownership_evidence is None:
            errors.append(f"{path}: ENTERPRISE_MANAGED needs ownership_evidence")
        elif record.ownership is Ownership.NOT_PROVISIONED and record.ownership_evidence is not None:
            errors.append(f"{path}: NOT_PROVISIONED must not carry ownership_evidence")
        errors.extend(
            _evidence_errors(f"{path}.ownership_evidence", record.ownership_evidence, as_of)
        )
        errors.extend(_fact_list_errors(f"{path}.facts", record.facts, _ENV, as_of))
    return tuple(errors)


def inventory_errors(text: str) -> tuple[str, ...]:
    """Every error in an inventory text, in a fixed order; () means valid."""
    return _validate(text)[1]


def parse_enterprise_inventory(text: str) -> EnterpriseEnvironmentInventory:
    inventory, errors = _validate(text)
    if errors or inventory is None:
        raise EnterpriseInventoryError(errors)
    return inventory


def load_enterprise_inventory(path: Path | str | None = None) -> EnterpriseEnvironmentInventory:
    """Load and validate the committed inventory (or another file); no substitution."""
    inventory_path = Path(path) if path is not None else DEFAULT_INVENTORY_PATH
    return parse_enterprise_inventory(inventory_path.read_text(encoding="utf-8"))


# --- assessment ---------------------------------------------------------------

_STATE_REASONS: dict[FactState, str] = {
    FactState.NOT_REQUESTED: "not requested",
    FactState.REQUESTED: "requested, not approved",
    FactState.REJECTED: "rejected",
}


def _fact_blocker(env: EnvironmentName, fact: FactRecord) -> InventoryBlocker | None:
    rule = _RULES[fact.item]
    if fact.state is FactState.APPROVED:
        revisit = rule.revisit_for(fact.answer)
        if revisit is None:
            return None
        kind, reason = BlockerKind.REVISIT, f"answer {fact.answer} {revisit.why}"
    else:
        kind, reason = BlockerKind.FACT, _STATE_REASONS[fact.state]
    return InventoryBlocker(
        kind=kind,
        environment=env,
        scope=rule.scope,
        subject=fact.item,
        owner_role=rule.owner_role,
        reason=reason,
    )


def assess_enterprise_inventory(inventory: EnterpriseEnvironmentInventory) -> InventoryAssessment:
    """Pure gate assessment; refuses a model that breaks a semantic rule."""
    errors = _semantic_errors(inventory)
    if errors:
        raise EnterpriseInventoryError(errors)
    by_env = {record.environment: record for record in inventory.environments}
    results: list[EnvironmentAssessment] = []
    for env in INVENTORY_ENVIRONMENTS:
        record = by_env[env]
        blockers: list[InventoryBlocker] = []
        if record.ownership is not Ownership.ENTERPRISE_MANAGED:
            blockers.append(
                InventoryBlocker(
                    kind=BlockerKind.OWNERSHIP,
                    environment=env,
                    scope=_ENV,
                    subject="ownership",
                    owner_role=_CPO,
                    reason="no enterprise-managed project is provisioned",
                )
            )
        fact_blockers = [
            blocker
            for fact in (*record.facts, *inventory.program_facts)
            if (blocker := _fact_blocker(env, fact)) is not None
        ]
        blockers.extend(fact_blockers)
        for holder in inventory.role_holders:
            if holder.holder is HolderState.NOT_ASSIGNED:
                blockers.append(
                    InventoryBlocker(
                        kind=BlockerKind.ROLE_HOLDER,
                        environment=env,
                        scope=_PROGRAM,
                        subject="role-holder",
                        owner_role=holder.role,
                        reason="holder not yet assigned",
                    )
                )
        results.append(
            EnvironmentAssessment(
                environment=env,
                ownership=record.ownership,
                verdict=InventoryVerdict.BLOCKED if blockers else InventoryVerdict.READY_TO_VALIDATE,
                facts_total=FACTS_PER_ENVIRONMENT,
                facts_approved=FACTS_PER_ENVIRONMENT - len(fact_blockers),
                blockers=tuple(blockers),
            )
        )
    ready = all(r.verdict is InventoryVerdict.READY_TO_VALIDATE for r in results)
    return InventoryAssessment(
        schema_version=inventory.schema_version,
        as_of=inventory.as_of,
        gate=InventoryVerdict.READY_TO_VALIDATE if ready else InventoryVerdict.BLOCKED,
        environments=tuple(results),
    )


# --- rendering ----------------------------------------------------------------


def _dated(evidence: EvidenceRef | None) -> str:
    if evidence is None:
        return ""
    return f" ([{evidence.recorded_on.isoformat()}]({evidence.ref}))"


def _fact_cell(fact: FactRecord) -> str:
    cell = f"`{fact.state.value}`"
    if fact.answer is not None:
        cell += f": `{fact.answer}`"
    cell += _dated(fact.evidence)
    if fact.state is FactState.APPROVED and _RULES[fact.item].revisit_for(fact.answer):
        cell += " (blocks)"
    return cell


def _item_cell(rule: GateRule) -> str:
    return f"`{rule.item}`: {rule.title}"


def render_inventory_status(inventory: EnterpriseEnvironmentInventory) -> str:
    """The generated "Current gate result" section of the inventory page (ASCII)."""
    assessment = assess_enterprise_inventory(inventory)
    by_env = {record.environment: record for record in inventory.environments}
    ready = sum(
        1 for r in assessment.environments if r.verdict is InventoryVerdict.READY_TO_VALIDATE
    )
    lines = [
        GATE_SECTION_HEADING,
        "",
        "Generated from [`config/enterprise-environments/inventory.yaml`]"
        "(../../config/enterprise-environments/inventory.yaml) by `render_inventory_status` in "
        "[`enterprise_inventory.py`](../../src/research_platform/readiness/enterprise_inventory.py). "
        "A test compares this section with the YAML byte for byte, so edit the YAML and "
        "regenerate this section; never edit it by hand. "
        f"Schema `{assessment.schema_version}`, as of {assessment.as_of.isoformat()}.",
        "",
        f"**Gate verdict: `{assessment.gate.value}`.** {ready} of {len(INVENTORY_ENVIRONMENTS)} "
        "enterprise environments have every fact approved.",
        "",
        "| Environment | Enterprise project | Inventory verdict | Facts approved | Blockers |",
        "|---|---|---|---|---|",
    ]
    for result in assessment.environments:
        project = f"`{result.ownership.value}`{_dated(by_env[result.environment].ownership_evidence)}"
        lines.append(
            f"| `{result.environment.value}` | {project} | `{result.verdict.value}` "
            f"| {result.facts_approved} of {result.facts_total} | {len(result.blockers)} |"
        )
    lines += [
        "| `PROD` | out of DE-04 scope | not assessed | - | - |",
        "",
        "Operator-owned and personal projects are not recorded here; they never count "
        "(ADR 0001 D3). An environment is blocked by its enterprise project until that is "
        "`ENTERPRISE_MANAGED`, by every fact that is not `APPROVED` or whose answer is listed "
        'under "Answers that keep an environment blocked", and by every role holder not yet '
        "assigned.",
        "",
        "### Role holders",
        "",
        "| Role | Holder |",
        "|---|---|",
    ]
    for holder in inventory.role_holders:
        if holder.holder is HolderState.NOT_ASSIGNED:
            state = "holder not yet assigned"
        else:
            state = "assigned" + _dated(holder.evidence)
        lines.append(f"| {ROLE_LABELS[holder.role]} | {state} |")
    lines += [
        "",
        "### Environment facts",
        "",
        "One record per environment.",
        "",
        "| Item | Group | Owner role | GCP_SANDBOX | DEV | QA |",
        "|---|---|---|---|---|---|",
    ]
    env_facts = {env: {f.item: f for f in by_env[env].facts} for env in INVENTORY_ENVIRONMENTS}
    for item in ENVIRONMENT_ITEMS:
        rule = _RULES[item]
        cells = " | ".join(_fact_cell(env_facts[env][item]) for env in INVENTORY_ENVIRONMENTS)
        lines.append(
            f"| {_item_cell(rule)} | {GROUP_LABELS[rule.group]} "
            f"| {ROLE_LABELS[rule.owner_role]} | {cells} |"
        )
    lines += [
        "",
        "### Program facts",
        "",
        "One record covers GCP_SANDBOX, DEV and QA.",
        "",
        "| Item | Group | Owner role | State |",
        "|---|---|---|---|",
    ]
    program = {fact.item: fact for fact in inventory.program_facts}
    for item in PROGRAM_ITEMS:
        rule = _RULES[item]
        lines.append(
            f"| {_item_cell(rule)} | {GROUP_LABELS[rule.group]} "
            f"| {ROLE_LABELS[rule.owner_role]} | {_fact_cell(program[item])} |"
        )
    lines += [
        "",
        "### Answers that keep an environment blocked",
        "",
        "| Item | Answer | Why it blocks |",
        "|---|---|---|",
    ]
    for rule in GATE_RULES:
        for revisit in rule.revisit:
            lines.append(f"| `{rule.item}` | `{revisit.answer}` | {revisit.why} |")
    lines += ["", ""]
    return "\n".join(lines)
