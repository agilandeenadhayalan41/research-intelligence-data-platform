"""Guard the DE-04 (#125) enterprise environment inventory and its page.

- The committed YAML is valid and records the 2026-10-16 decisions: no
  enterprise project is provisioned and no role holder is assigned, so the
  gate is ``BLOCKED`` with every blocker listed and owed by a role.
- ``READY_TO_VALIDATE`` needs an ``ENTERPRISE_MANAGED`` project, every catalog
  fact approved with an answer that does not keep it blocked, and every role
  holder assigned. Operator-owned and personal projects are rejected outright.
- ``GATE_RULES`` is the only catalog; its revisit answers name real ADR 0001
  "Not selected" rows.
- Evidence is only a comment on #125. Identifiers, comments outside the fixed
  header, duplicate keys and unsafe tags are rejected, and no error echoes a
  rejected value.
- The inventory changes no readiness level: the readiness package and the
  cloud scripts do not reference it.
- The page's generated section equals the renderer's output byte for byte, and
  its hand-written role, consumer and status statements match the catalog and
  the assessment.

Negative cases feed broken inventories to the same validators.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from pydantic import ValidationError

from research_platform.readiness.enterprise_inventory import (
    ADR_0001_ACCEPTED_ON,
    DEFAULT_INVENTORY_PATH,
    ENVIRONMENT_ITEMS,
    GATE_RULES,
    GATE_SECTION_HEADING,
    INVENTORY_ENVIRONMENTS,
    INVENTORY_HEADER,
    INVENTORY_MODELS,
    PROGRAM_ITEMS,
    REPOSITORY_URL,
    ROLE_LABELS,
    ROLE_ORDER,
    AnswerFormat,
    BlockerKind,
    EnterpriseEnvironmentInventory,
    EnterpriseInventoryError,
    FactState,
    HolderState,
    InventoryVerdict,
    ItemKind,
    ItemScope,
    OwnerRole,
    Ownership,
    assess_enterprise_inventory,
    inventory_errors,
    load_enterprise_inventory,
    parse_enterprise_inventory,
    private_identifier_errors,
    render_inventory_status,
)
from research_platform.readiness.models import EnvironmentName

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs/operations/enterprise-environment-inventory.md"
READINESS_DOC = REPO_ROOT / "docs/architecture/environment-readiness.md"
ADR_0001 = REPO_ROOT / "docs/adr/0001-gcp-architecture-and-service-selection.md"
SIDEBARS = REPO_ROOT / "docs/site/sidebars.ts"
READY_AS_OF = date(2026, 11, 2)

# Hand-written sentences that must match the assessment (see the last tests).
BLOCKER_STATUS = "**Status:** documented blocker."
NO_ENTERPRISE_PROJECT = "No enterprise-managed GCP_SANDBOX, DEV or QA project exists"
NO_ROLE_HOLDER = "no role holder is assigned"


def _evidence(recorded_on: date = READY_AS_OF) -> dict[str, Any]:
    # Synthetic comment id; short, so it is never mistaken for a project number.
    return {"ref": f"{REPOSITORY_URL}/issues/125#issuecomment-101", "recorded_on": recorded_on}


def _committed_text() -> str:
    return DEFAULT_INVENTORY_PATH.read_text(encoding="utf-8")


def _committed_dict() -> dict[str, Any]:
    raw = yaml.safe_load(_committed_text())
    assert isinstance(raw, dict)
    return raw


def _first_allowed(item: str) -> str:
    rule = next(r for r in GATE_RULES if r.item == item)
    if rule.answer_format is AnswerFormat.US_COMPUTE_REGION:
        return "us-central1"
    blocking = {r.answer for r in rule.revisit}
    return next(a for a in rule.answers if a not in blocking)


def _approve(fact: dict[str, Any]) -> None:
    rule = next(r for r in GATE_RULES if r.item == fact["item"])
    fact["state"] = "APPROVED"
    fact["evidence"] = _evidence()
    if rule.kind is ItemKind.DECISION:
        fact["answer"] = _first_allowed(rule.item)


def _ready_dict() -> dict[str, Any]:
    data = _committed_dict()
    data["as_of"] = READY_AS_OF
    for holder in data["role_holders"]:
        holder["holder"] = "ASSIGNED"
        holder["evidence"] = _evidence()
    for fact in data["program_facts"]:
        _approve(fact)
    for env in data["environments"]:
        env["ownership"] = "ENTERPRISE_MANAGED"
        env["ownership_evidence"] = _evidence()
        for fact in env["facts"]:
            _approve(fact)
    return data


class _NoAliasDumper(yaml.SafeDumper):
    """Writes repeated values in full: the validator refuses anchors and aliases."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _dump(data: dict[str, Any]) -> str:
    return INVENTORY_HEADER + yaml.dump(data, Dumper=_NoAliasDumper, sort_keys=False)


def _parse(data: dict[str, Any]) -> EnterpriseEnvironmentInventory:
    return parse_enterprise_inventory(_dump(data))


def _env(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(e for e in data["environments"] if e["environment"] == name)


def _env_fact(data: dict[str, Any], name: str, item: str) -> dict[str, Any]:
    return next(f for f in _env(data, name)["facts"] if f["item"] == item)


def _program_fact(data: dict[str, Any], item: str) -> dict[str, Any]:
    return next(f for f in data["program_facts"] if f["item"] == item)


def _facts(data: dict[str, Any], item: str) -> list[dict[str, Any]]:
    if item in PROGRAM_ITEMS:
        return [_program_fact(data, item)]
    return [_env_fact(data, env.value, item) for env in INVENTORY_ENVIRONMENTS]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    return text[start : text.index("\n## ", start + 1) + 1]


# --- the committed file -------------------------------------------------------


def test_committed_inventory_is_valid() -> None:
    text = _committed_text()
    assert text.startswith(INVENTORY_HEADER)
    assert inventory_errors(text) == ()
    assert load_enterprise_inventory() == parse_enterprise_inventory(text)


def test_committed_inventory_records_the_2026_10_16_decisions() -> None:
    # Pins user decisions 1 and 2. The first pull request that records an
    # approval updates this test on purpose.
    inventory = load_enterprise_inventory()
    assert inventory.as_of == date(2026, 10, 16)
    assert [e.environment for e in inventory.environments] == list(INVENTORY_ENVIRONMENTS)
    for env in inventory.environments:
        assert env.ownership is Ownership.NOT_PROVISIONED and env.ownership_evidence is None
    for fact in (*inventory.program_facts, *(f for e in inventory.environments for f in e.facts)):
        assert (fact.state, fact.answer, fact.evidence) == (FactState.NOT_REQUESTED, None, None)
    assert [(h.role, h.holder, h.evidence) for h in inventory.role_holders] == [
        (role, HolderState.NOT_ASSIGNED, None) for role in ROLE_ORDER
    ]
    assessment = assess_enterprise_inventory(inventory)
    assert assessment.gate is InventoryVerdict.BLOCKED
    for result in assessment.environments:
        assert result.verdict is InventoryVerdict.BLOCKED
        assert (result.facts_approved, result.facts_total) == (0, len(GATE_RULES)) == (0, 31)
        assert len(result.blockers) == 1 + len(GATE_RULES) + len(ROLE_ORDER) == 37
        assert [b.subject for b in result.blockers] == [
            "ownership", *ENVIRONMENT_ITEMS, *PROGRAM_ITEMS, *["role-holder"] * len(ROLE_ORDER)
        ]
        assert [b.owner_role for b in result.blockers] == [
            OwnerRole.CLOUD_PLATFORM_OWNER,
            *(r.owner_role for r in GATE_RULES if r.scope is ItemScope.ENVIRONMENT),
            *(r.owner_role for r in GATE_RULES if r.scope is ItemScope.PROGRAM),
            *ROLE_ORDER,
        ]
    first = assessment.environments[0].blockers[0]
    assert first.describe() == (
        "GCP_SANDBOX: ownership (Cloud platform owner): no enterprise-managed project is provisioned"
    )


def test_gate_rules_are_the_reviewed_catalog() -> None:
    # Changing the gate needs this literal to change in review.
    assert [
        (r.item, r.scope.value, r.kind.value, r.owner_role.value,
         None if r.answer_format is None else r.answer_format.value, r.answers,
         tuple(v.answer for v in r.revisit))
        for r in GATE_RULES
    ] == [
        ("enterprise-billing", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("program-access", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("core-apis", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("resource-location-policy", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("key-creation-disabled", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("workload-identity-federation", "ENVIRONMENT", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("ALLOWED", "DISALLOWED"), ("DISALLOWED",)),
        ("selected-services-allowed", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("separate-identities", "ENVIRONMENT", "APPROVAL", "SECURITY_GOVERNANCE_OWNER", None, (), ()),
        ("human-access-role-groups", "ENVIRONMENT", "APPROVAL", "SECURITY_GOVERNANCE_OWNER", None, (), ()),
        ("no-cross-environment-grants", "ENVIRONMENT", "APPROVAL", "SECURITY_GOVERNANCE_OWNER", None, (), ()),
        ("bronze-bucket", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("silver-bucket", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("analytical-dataset", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("budget-and-alerts", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("spend-cap", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("query-quotas", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("standing-instances", "ENVIRONMENT", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("ACCEPTED", "NOT_ACCEPTED"), ("NOT_ACCEPTED",)),
        ("network-model", "ENVIRONMENT", "APPROVAL", "CLOUD_PLATFORM_OWNER", None, (), ()),
        ("separate-non-prod-projects", "PROGRAM", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("SEPARATE_PROJECTS", "SHARED_PROJECT_ONLY"), ("SHARED_PROJECT_ONLY",)),
        ("us-compute-region", "PROGRAM", "DECISION", "CLOUD_PLATFORM_OWNER", "US_COMPUTE_REGION", (), ()),
        ("residency-requirement", "PROGRAM", "DECISION", "SECURITY_GOVERNANCE_OWNER", "CHOICE",
         ("NONE_RECORDED", "EXCLUDES_US_MULTI_REGION", "REQUIRES_NON_US_LOCATION"),
         ("EXCLUDES_US_MULTI_REGION", "REQUIRES_NON_US_LOCATION")),
        ("in-organization-deploys", "PROGRAM", "DECISION", "SECURITY_GOVERNANCE_OWNER", "CHOICE",
         ("NOT_REQUIRED", "REQUIRED"), ("REQUIRED",)),
        ("perimeter-and-cmek", "PROGRAM", "DECISION", "SECURITY_GOVERNANCE_OWNER", "CHOICE",
         ("NOT_MANDATED", "MANDATED"), ("MANDATED",)),
        ("two-buckets-per-environment", "PROGRAM", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("TWO_BUCKETS_ALLOWED", "ONE_BUCKET_ONLY"), ("ONE_BUCKET_ONLY",)),
        ("dev-qa-control-instance", "PROGRAM", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("SHARED_NON_PROD_INSTANCE", "SEPARATE_INSTANCES"), ()),
        ("shared-composer-environment", "PROGRAM", "DECISION", "CLOUD_PLATFORM_OWNER", "CHOICE",
         ("AVAILABLE", "NOT_AVAILABLE"), ()),
        ("gke-mandate", "PROGRAM", "DECISION", "LEAD_ARCHITECT", "CHOICE",
         ("NONE", "MANDATED"), ("MANDATED",)),
        ("sql-transformation-mandate", "PROGRAM", "DECISION", "LEAD_ARCHITECT", "CHOICE",
         ("NONE", "DATAFORM", "DBT"), ("DATAFORM", "DBT")),
        ("egress-to-public-source", "PROGRAM", "APPROVAL", "SECURITY_GOVERNANCE_OWNER", None, (), ()),
        ("source-access", "PROGRAM", "APPROVAL", "SECURITY_GOVERNANCE_OWNER", None, (), ()),
        ("managed-catalog", "PROGRAM", "DECISION", "SECURITY_GOVERNANCE_OWNER", "CHOICE",
         ("MANAGED_CATALOG_APPROVED", "NOT_APPROVED", "OTHER_STANDARD"), ()),
    ]
    assert (len(ENVIRONMENT_ITEMS), len(PROGRAM_ITEMS)) == (18, 13)
    adr_refs = {"Status", *(f"D{n}" for n in range(1, 14))}
    for rule in GATE_RULES:
        assert re.fullmatch(r"[^|\n]{1,160}", rule.title) and rule.title.isascii(), rule.item
        assert private_identifier_errors(rule.title) == (), rule.item
        assert set(rule.adr_refs) <= adr_refs and rule.adr_refs, rule.item
        assert rule.consumers and all(re.fullmatch(r"DE-\d{2}", c) and c != "DE-04" for c in rule.consumers)
        assert all(v.answer in rule.answers for v in rule.revisit), rule.item


def _not_selected_rows() -> dict[str, str]:
    text = ADR_0001.read_text(encoding="utf-8")
    table = _section(text, "## Not selected")
    rows: dict[str, str] = {}
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = cells[3]
    return rows


def test_revisit_answers_name_adr_0001_not_selected_rows() -> None:
    rows = _not_selected_rows()
    rulings = []
    for rule in GATE_RULES:
        for revisit in rule.revisit:
            if revisit.adr_row is None:
                rulings.append((rule.item, revisit.answer))
                continue
            assert revisit.owner is not None
            de, number = revisit.owner
            assert rows.get(revisit.adr_row) == f"{de} [#{number}]", revisit.adr_row
            assert f'"{revisit.adr_row}"' in revisit.why and f"{de}, #{number}" in revisit.why
    assert rulings == [("standing-instances", "NOT_ACCEPTED")]


def test_prod_and_local_are_never_assessed() -> None:
    for data in (_committed_dict(), _ready_dict()):
        assessment = assess_enterprise_inventory(_parse(data))
        assert tuple(r.environment for r in assessment.environments) == INVENTORY_ENVIRONMENTS


# --- happy path and partial readiness -----------------------------------------


def test_validator_accepts_the_ready_fixture() -> None:
    assert inventory_errors(_dump(_ready_dict())) == ()


def test_synthetic_all_approved_enterprise_inventory_is_ready_to_validate() -> None:
    assessment = assess_enterprise_inventory(_parse(_ready_dict()))
    assert assessment.gate is InventoryVerdict.READY_TO_VALIDATE
    for result in assessment.environments:
        assert result.verdict is InventoryVerdict.READY_TO_VALIDATE
        assert result.blockers == ()
        assert result.facts_approved == result.facts_total == 31


@pytest.mark.parametrize(
    ("state", "reason"), [("REQUESTED", "requested, not approved"), ("REJECTED", "rejected")]
)
def test_one_missing_approval_blocks_only_that_environment(state: str, reason: str) -> None:
    data = _ready_dict()
    _env_fact(data, "DEV", "bronze-bucket")["state"] = state
    assessment = assess_enterprise_inventory(_parse(data))
    verdicts = {r.environment: r for r in assessment.environments}
    assert verdicts[EnvironmentName.GCP_SANDBOX].verdict is InventoryVerdict.READY_TO_VALIDATE
    assert verdicts[EnvironmentName.QA].verdict is InventoryVerdict.READY_TO_VALIDATE
    dev = verdicts[EnvironmentName.DEV]
    assert dev.verdict is InventoryVerdict.BLOCKED and dev.facts_approved == 30
    assert [(b.kind, b.subject, b.owner_role, b.reason) for b in dev.blockers] == [
        (BlockerKind.FACT, "bronze-bucket", OwnerRole.CLOUD_PLATFORM_OWNER, reason)
    ]
    assert assessment.gate is InventoryVerdict.BLOCKED


def test_program_fact_blocks_every_environment() -> None:
    data = _ready_dict()
    fact = _program_fact(data, "us-compute-region")
    fact["state"] = "REQUESTED"
    del fact["answer"]
    assessment = assess_enterprise_inventory(_parse(data))
    for result in assessment.environments:
        assert [b.subject for b in result.blockers] == ["us-compute-region"]


@pytest.mark.parametrize("role", ROLE_ORDER, ids=lambda r: r.value)
def test_unassigned_role_holder_blocks_every_environment(role: OwnerRole) -> None:
    data = _ready_dict()
    holder = next(h for h in data["role_holders"] if h["role"] == role.value)
    holder["holder"] = "NOT_ASSIGNED"
    del holder["evidence"]
    assessment = assess_enterprise_inventory(_parse(data))
    for result in assessment.environments:
        assert [(b.kind, b.owner_role, b.reason) for b in result.blockers] == [
            (BlockerKind.ROLE_HOLDER, role, "holder not yet assigned")
        ]


REVISIT_CASES = [(r.item, v) for r in GATE_RULES for v in r.revisit]


@pytest.mark.parametrize(
    ("item", "revisit"), REVISIT_CASES, ids=[f"{i}-{v.answer}" for i, v in REVISIT_CASES]
)
def test_revisit_answers_block_with_the_adr_reason(item: str, revisit: Any) -> None:
    data = _ready_dict()
    for fact in _facts(data, item):
        fact["answer"] = revisit.answer
    inventory = _parse(data)
    assessment = assess_enterprise_inventory(inventory)
    for result in assessment.environments:
        assert [(b.kind, b.subject, b.reason) for b in result.blockers] == [
            (BlockerKind.REVISIT, item, f"answer {revisit.answer} {revisit.why}")
        ]
    assert f"`APPROVED`: `{revisit.answer}`" in render_inventory_status(inventory)
    assert "(blocks)" in render_inventory_status(inventory)


ALLOWED_CASES = [
    (r.item, a)
    for r in GATE_RULES
    if r.answer_format is AnswerFormat.CHOICE
    for a in r.answers
    if r.revisit_for(a) is None
] + [("us-compute-region", region) for region in ("us-central1", "us-east4", "us-west2", "us-south1")]


@pytest.mark.parametrize(("item", "answer"), ALLOWED_CASES, ids=[f"{i}-{a}" for i, a in ALLOWED_CASES])
def test_allowed_answers_keep_the_inventory_ready(item: str, answer: str) -> None:
    data = _ready_dict()
    for fact in _facts(data, item):
        fact["answer"] = answer
    assert assess_enterprise_inventory(_parse(data)).gate is InventoryVerdict.READY_TO_VALIDATE


def test_withdrawn_approval_returns_the_environment_to_blocked() -> None:
    # The rollback path: a record set back by a later pull request.
    data = _ready_dict()
    fact = _env_fact(data, "QA", "spend-cap")
    fact["state"] = "NOT_REQUESTED"
    del fact["evidence"]
    assessment = assess_enterprise_inventory(_parse(data))
    qa = assessment.environments[2]
    assert qa.verdict is InventoryVerdict.BLOCKED and [b.subject for b in qa.blockers] == ["spend-cap"]
    holder = data["role_holders"][0]
    holder["holder"] = "NOT_ASSIGNED"
    del holder["evidence"]
    assessment = assess_enterprise_inventory(_parse(data))
    assert all(r.verdict is InventoryVerdict.BLOCKED for r in assessment.environments)


def test_not_provisioned_environment_is_blocked_even_when_every_fact_is_approved() -> None:
    data = _ready_dict()
    env = _env(data, "GCP_SANDBOX")
    env["ownership"] = "NOT_PROVISIONED"
    del env["ownership_evidence"]
    sandbox = assess_enterprise_inventory(_parse(data)).environments[0]
    assert sandbox.facts_approved == 31
    assert [(b.kind, b.reason) for b in sandbox.blockers] == [
        (BlockerKind.OWNERSHIP, "no enterprise-managed project is provisioned")
    ]


# --- ownership ------------------------------------------------------------------


@pytest.mark.parametrize("with_evidence", [True, False], ids=["with-evidence", "no-evidence"])
@pytest.mark.parametrize("env", [e.value for e in INVENTORY_ENVIRONMENTS])
@pytest.mark.parametrize("ownership", ["OPERATOR_OWNED", "PERSONAL"])
def test_operator_or_personal_ownership_is_rejected(
    ownership: str, env: str, with_evidence: bool
) -> None:
    data = _ready_dict()
    record = _env(data, env)
    record["ownership"] = ownership
    if not with_evidence:
        del record["ownership_evidence"]
    expected = (
        f"environments.{env}: {ownership} never counts as an enterprise environment "
        "(ADR 0001 D3); record NOT_PROVISIONED",
    )
    assert inventory_errors(_dump(data)) == expected
    with pytest.raises(EnterpriseInventoryError):
        parse_enterprise_inventory(_dump(data))
    # A model built around the parser still yields no assessment and no render.
    model = EnterpriseEnvironmentInventory.model_validate(data)
    with pytest.raises(EnterpriseInventoryError):
        assess_enterprise_inventory(model)
    with pytest.raises(EnterpriseInventoryError):
        render_inventory_status(model)


# --- invalid inputs ---------------------------------------------------------------

Mutation = Callable[[dict[str, Any]], None]


def _append_copy_of_env(index: int, name: str) -> Mutation:
    def mutate(d: dict[str, Any]) -> None:
        extra = copy.deepcopy(d["environments"][index])
        extra["environment"] = name
        d["environments"].append(extra)
    return mutate


def _set(path: Callable[[dict[str, Any]], dict[str, Any]], key: str, value: Any) -> Mutation:
    return lambda d: path(d).__setitem__(key, value)


def _drop(path: Callable[[dict[str, Any]], dict[str, Any]], key: str) -> Mutation:
    return lambda d: path(d).pop(key)


def _dev_bronze(d: dict[str, Any]) -> dict[str, Any]:
    return _env_fact(d, "DEV", "bronze-bucket")


def _gke(d: dict[str, Any]) -> dict[str, Any]:
    return _program_fact(d, "gke-mandate")


def _region(d: dict[str, Any]) -> dict[str, Any]:
    return _program_fact(d, "us-compute-region")


def _gke_evidence(d: dict[str, Any]) -> dict[str, Any]:
    return _program_fact(d, "gke-mandate")["evidence"]


def _qa(d: dict[str, Any]) -> dict[str, Any]:
    return _env(d, "QA")


def _cpo_holder(d: dict[str, Any]) -> dict[str, Any]:
    return d["role_holders"][1]


def _swap(key: str, i: int, j: int, env: str | None = None) -> Mutation:
    def mutate(d: dict[str, Any]) -> None:
        items = _env(d, env)[key] if env else d[key]
        items[i], items[j] = items[j], items[i]
    return mutate


def _without(key: str, item: str, env: str | None = None) -> Mutation:
    def mutate(d: dict[str, Any]) -> None:
        holder = _env(d, env) if env else d
        holder[key] = [f for f in holder[key] if f["item"] != item]
    return mutate


def _reject_gke(d: dict[str, Any]) -> None:
    _gke(d)["state"] = "REJECTED"
    del _gke(d)["answer"]


def _reject_bronze_without_evidence(d: dict[str, Any]) -> None:
    _dev_bronze(d)["state"] = "REJECTED"
    del _dev_bronze(d)["evidence"]


def _qa_not_provisioned_with_evidence(d: dict[str, Any]) -> None:
    _qa(d)["ownership"] = "NOT_PROVISIONED"


ROLES_MESSAGE = (
    "role_holders: must list LEAD_ARCHITECT, CLOUD_PLATFORM_OWNER, SECURITY_GOVERNANCE_OWNER, "
    "DATA_ENGINEERING_OWNER, API_PRODUCT_OWNER once each, in that order"
)
SEMANTIC_CASES: list[tuple[str, bool, Mutation, tuple[str, ...]]] = [
    # (case id, start from the committed file instead of the ready fixture, mutation, errors)
    ("local-environment", False, _append_copy_of_env(0, "LOCAL"),
     ("environments: LOCAL is not an enterprise environment",)),
    ("prod-present-as-ready", False, _append_copy_of_env(2, "PROD"),
     ("environments: PROD is out of DE-04 scope (ADR 0001 D3: requested only when DE-27 [#148] "
      "can show the QA to PROD gate)",)),
    ("duplicate-environment", False, lambda d: d["environments"].append(copy.deepcopy(d["environments"][0])),
     ("environments: duplicate GCP_SANDBOX",)),
    ("missing-environment", False, lambda d: d["environments"].pop(), ("environments: missing QA",)),
    ("environment-order", False, _swap("environments", 0, 1),
     ("environments: must be listed in the order GCP_SANDBOX, DEV, QA",)),
    ("unknown-fact-item", False, _set(_dev_bronze, "item", "my-proj-a1b2c3"),
     ("environments.DEV.facts[10]: not a gate-rule item", "environments.DEV.facts: missing bronze-bucket")),
    ("duplicate-fact-item", False, lambda d: _env(d, "DEV")["facts"].append(copy.deepcopy(_dev_bronze(d))),
     ("environments.DEV.facts: duplicate bronze-bucket",)),
    ("missing-fact-item", False, _without("facts", "silver-bucket", "DEV"),
     ("environments.DEV.facts: missing silver-bucket",)),
    ("fact-order", False, _swap("facts", 0, 1, "DEV"), ("environments.DEV.facts: must follow the gate-rule order",)),
    ("program-order", False, _swap("program_facts", 0, 1), ("program_facts: must follow the gate-rule order",)),
    ("missing-program-item", False, _without("program_facts", "managed-catalog"),
     ("program_facts: missing managed-catalog",)),
    ("program-item-in-environment", False, lambda d: _env(d, "DEV")["facts"].append(copy.deepcopy(_gke(d))),
     ("environments.DEV.facts[18]: gke-mandate is a PROGRAM item",)),
    ("environment-item-in-program", False, lambda d: d["program_facts"].append(copy.deepcopy(_dev_bronze(d))),
     ("program_facts[13]: bronze-bucket is an ENVIRONMENT item",)),
    ("approved-without-evidence", False, _drop(_dev_bronze, "evidence"),
     ("environments.DEV.facts.bronze-bucket: APPROVED needs evidence",)),
    ("rejected-without-evidence", False, _reject_bronze_without_evidence,
     ("environments.DEV.facts.bronze-bucket: REJECTED needs evidence",)),
    ("not-requested-with-evidence", False, _set(_dev_bronze, "state", "NOT_REQUESTED"),
     ("environments.DEV.facts.bronze-bucket: NOT_REQUESTED must not carry evidence",)),
    ("rejected-decision", False, _reject_gke,
     ("program_facts.gke-mandate: REJECTED applies to APPROVAL items only",)),
    ("decision-without-answer", False, _drop(_gke, "answer"),
     ("program_facts.gke-mandate: an APPROVED decision needs an answer",)),
    ("answer-on-unapproved-decision", False, _set(_gke, "state", "REQUESTED"),
     ("program_facts.gke-mandate: only an APPROVED decision carries an answer",)),
    ("answer-not-allowed", False, _set(_gke, "answer", "MAYBE"),
     ("program_facts.gke-mandate: answer is not an allowed answer",)),
    ("approval-with-answer", False, _set(_dev_bronze, "answer", "YES"),
     ("environments.DEV.facts.bronze-bucket: an APPROVAL item takes no answer",)),
    ("non-us-region", False, _set(_region, "answer", "europe-west1"),
     ("program_facts.us-compute-region: answer is not an allowed answer",)),
    ("region-without-number", False, _set(_region, "answer", "us-central"),
     ("program_facts.us-compute-region: answer is not an allowed answer",)),
    ("choice-answer-for-region", False, _set(_region, "answer", "ALLOWED"),
     ("program_facts.us-compute-region: answer is not an allowed answer",)),
    ("evidence-before-adr", False, _set(_gke_evidence, "recorded_on", date(2026, 10, 12)),
     ("program_facts.gke-mandate.evidence: recorded before ADR 0001 acceptance (2026-10-16)",)),
    ("evidence-after-as-of", False, _set(_gke_evidence, "recorded_on", date(2026, 11, 3)),
     ("program_facts.gke-mandate.evidence: recorded after as_of",)),
    ("ownership-evidence-after-as-of", False,
     lambda d: _qa(d)["ownership_evidence"].__setitem__("recorded_on", date(2026, 11, 3)),
     ("environments.QA.ownership_evidence: recorded after as_of",)),
    ("holder-evidence-before-adr", False,
     lambda d: _cpo_holder(d)["evidence"].__setitem__("recorded_on", date(2026, 10, 12)),
     ("role_holders.CLOUD_PLATFORM_OWNER.evidence: recorded before ADR 0001 acceptance (2026-10-16)",)),
    ("as-of-before-adr", True, lambda d: d.__setitem__("as_of", date(2026, 10, 15)),
     ("as_of: before ADR 0001 acceptance (2026-10-16)",)),
    ("enterprise-without-ownership-evidence", False, _drop(_qa, "ownership_evidence"),
     ("environments.QA: ENTERPRISE_MANAGED needs ownership_evidence",)),
    ("not-provisioned-with-ownership-evidence", False, _qa_not_provisioned_with_evidence,
     ("environments.QA: NOT_PROVISIONED must not carry ownership_evidence",)),
    ("assigned-without-evidence", False, _drop(_cpo_holder, "evidence"),
     ("role_holders.CLOUD_PLATFORM_OWNER: ASSIGNED needs evidence",)),
    ("not-assigned-with-evidence", False, _set(_cpo_holder, "holder", "NOT_ASSIGNED"),
     ("role_holders.CLOUD_PLATFORM_OWNER: NOT_ASSIGNED must not carry evidence",)),
    ("role-order", False, _swap("role_holders", 0, 1), (ROLES_MESSAGE,)),
    ("role-missing", False, lambda d: d["role_holders"].pop(), (ROLES_MESSAGE,)),
    ("role-duplicate", False, lambda d: d["role_holders"].append(copy.deepcopy(d["role_holders"][0])),
     (ROLES_MESSAGE,)),
]


@pytest.mark.parametrize(
    ("committed", "mutate", "expected"),
    [c[1:] for c in SEMANTIC_CASES],
    ids=[c[0] for c in SEMANTIC_CASES],
)
def test_validator_rejects_invalid_inventories(
    committed: bool, mutate: Mutation, expected: tuple[str, ...]
) -> None:
    data = _committed_dict() if committed else _ready_dict()
    mutate(data)
    assert inventory_errors(_dump(data)) == expected


PATTERN = "String should match pattern"
SCHEMA_CASES: list[tuple[str, Mutation, str]] = [
    ("unknown-state", _set(_dev_bronze, "state", "PENDING"), "environments.1.facts.10.state: Input should be"),
    ("unknown-ownership", lambda d: d["environments"][1].__setitem__("ownership", "ENTERPRISE"),
     "environments.1.ownership: Input should be"),
    ("unknown-environment", lambda d: d["environments"][0].__setitem__("environment", "STAGING"),
     "environments.0.environment: Input should be"),
    ("environment-alias", lambda d: d["environments"][0].__setitem__("environment", "gcp-sandbox"),
     "environments.0.environment: Input should be"),
    ("unknown-role", lambda d: d["role_holders"][0].__setitem__("role", "OWNER"), "role_holders.0.role: Input should be"),
    ("unknown-holder", lambda d: d["role_holders"][0].__setitem__("holder", "MAYBE"),
     "role_holders.0.holder: Input should be"),
    ("schema-version", lambda d: d.__setitem__("schema_version", "enterprise-environment-inventory-v2"),
     "schema_version: Input should be 'enterprise-environment-inventory-v1'"),
    ("bad-as-of", lambda d: d.__setitem__("as_of", "not-a-date"), "as_of: Input should be a valid date"),
    ("missing-facts", lambda d: d["environments"][0].pop("facts"), "environments.0.facts: Field required"),
    ("item-id-shape", _set(_dev_bronze, "item", "Bronze Bucket"), f"environments.1.facts.10.item: {PATTERN}"),
    ("extra-project-id-key", lambda d: d["environments"][0].__setitem__("project_id", "x"),
     "environments.0.<unknown key>: Extra inputs are not permitted"),
    ("old-base-commit-key", lambda d: d.__setitem__("base_commit", "15b77d4"),
     "<unknown key>: Extra inputs are not permitted"),
    ("old-prod-scope-key", lambda d: d.__setitem__("prod_scope", "OUT_OF_SCOPE"),
     "<unknown key>: Extra inputs are not permitted"),
    ("evidence-other-repo", _set(_gke_evidence, "ref", "https://github.com/example/other/issues/125#issuecomment-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-bare-issue-url", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/issues/125"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-108-comment", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/issues/108#issuecomment-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-116-review", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/pull/116#pullrequestreview-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-adr-pr-162", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/pull/162#issuecomment-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-119", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/pull/119#issuecomment-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-query-string", _set(_gke_evidence, "ref", f"{REPOSITORY_URL}/issues/125#issuecomment-101?x=1"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
    ("evidence-http", _set(_gke_evidence, "ref", "http" + REPOSITORY_URL[5:] + "/issues/125#issuecomment-101"),
     f"program_facts.8.evidence.ref: {PATTERN}"),
]


@pytest.mark.parametrize(("mutate", "prefix"), [c[1:] for c in SCHEMA_CASES], ids=[c[0] for c in SCHEMA_CASES])
def test_schema_errors_are_location_anchored(mutate: Mutation, prefix: str) -> None:
    data = _ready_dict()
    mutate(data)
    errors = inventory_errors(_dump(data))
    assert len(errors) == 1 and errors[0].startswith(prefix), errors


@pytest.mark.parametrize("body", ["", "- a\n", "null\n", "42\n"])
def test_inventory_must_be_a_yaml_mapping(body: str) -> None:
    assert inventory_errors(INVENTORY_HEADER + body) == ("inventory: must be a YAML mapping",)


def test_unsafe_yaml_tags_are_rejected() -> None:
    errors = inventory_errors(INVENTORY_HEADER + "!!python/object:os.system {}\n")
    assert errors == ("inventory: not valid YAML (line 13)",)


def test_duplicate_yaml_keys_are_rejected() -> None:
    text = INVENTORY_HEADER + "schema_version: a\nschema_version: b\n"
    assert inventory_errors(text) == ("inventory: duplicate key (line 14)",)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda t: t[len(INVENTORY_HEADER):],
         "inventory: must start with the fixed header comment (INVENTORY_HEADER)"),
        (lambda t: t.replace("program_facts:\n", "program_facts:\n  # note\n"),
         "line 22: comments are allowed only in the fixed header"),
        (lambda t: re.sub(r"^(as_of: \S+)$", r"\1  # today", t, count=1, flags=re.MULTILINE),
         "line 14: comments are allowed only in the fixed header"),
        (lambda t: re.sub(r"^as_of: (\S+)$", r'as_of: "\1"#x', t, count=1, flags=re.MULTILINE),
         "line 14: comments are allowed only in the fixed header"),
        (lambda t: t.replace("NOT_ASSIGNED}\n", "NOT_ASSIGNED}#x\n", 1),
         "line 16: comments are allowed only in the fixed header"),
    ],
    ids=["no-header", "comment-line", "inline-comment", "after-quote", "after-flow"],
)
def test_comments_outside_the_fixed_header_are_rejected(edit: Callable[[str], str], expected: str) -> None:
    assert inventory_errors(edit(_committed_text())) == (expected,)


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: re.sub(r"^as_of: (\S+)$", r"as_of: &d \1", t, count=1, flags=re.MULTILINE),
        lambda t: re.sub(r"^as_of: (\S+)$", r"as_of: !!timestamp \1", t, count=1, flags=re.MULTILINE),
        lambda t: t.replace(INVENTORY_HEADER, INVENTORY_HEADER + "%TAG !x! tag:example.org,2026:\n---\n", 1),
    ],
    ids=["anchor", "tag", "directive"],
)
def test_yaml_anchors_tags_and_directives_are_rejected(edit: Callable[[str], str]) -> None:
    errors = inventory_errors(edit(_committed_text()))
    assert any(
        e.endswith(": YAML anchors, aliases, tags and directives are not allowed") for e in errors
    ), errors


@pytest.mark.parametrize("value", ["!!int SECRET-X", "!!bool SECRET-X", "2026-02-30"])
def test_unconstructible_scalars_are_rejected_without_echo(value: str) -> None:
    text = re.sub(r"^as_of: \S+$", f"as_of: {value}", _committed_text(), count=1, flags=re.MULTILINE)
    assert inventory_errors(text) == ("inventory: not valid YAML",)


def test_missing_inventory_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_enterprise_inventory(tmp_path / "missing.yaml")


IDENTIFIER_SAMPLES = [
    ("owner@example.com", "an email address"),
    ("gs://example-bucket", "a gs:// bucket URL"),
    ("ci@example.iam.gserviceaccount.com", "a service-account identity"),
    ("projects/example-project-1", "a projects/<id> path"),
    ("serviceAccount:publisher@example-proj", "an IAM member"),
    ("group:data-eng@example", "an IAM member"),
    ("i118-20261013t101500", "a run ID"),
    ("example-project-123456", "a project-ID-like token"),
    ("organizations/123456789012", "an organization, folder or billing-account path"),
    ("0A1B2C-3D4E5F-6A7B8C", "a billing-account ID"),
    ("principal://iam.googleapis.com/x", "a workload identity principal"),
    ("${GOOGLE_CLOUD_PROJECT}", "an environment-variable reference"),
    ("USD 500", "a currency sign or word"),
    ("5 per TB", "a unit price"),
    ("123456789012", "a project-number-like token"),
    ("10.128.0.0/20", "an IP address"),
    # Identifiers glued to a link of this repository are not masked.
    (f"see {REPOSITORY_URL}/issues/125?u=owner@example.com", "an email address"),
    (f"{REPOSITORY_URL}/blob/main/projects/my-proj-a1b2c3", "a projects/<id> path"),
    (f"{REPOSITORY_URL}/x,ci@my-proj.iam.gserviceaccount.com", "a service-account identity"),
    (f"{REPOSITORY_URL}/issues/1251234567890", "a project-number-like token"),
    (f"{REPOSITORY_URL}/issues/125@example.com", "an email address"),
]


@pytest.mark.parametrize(("sample", "kind"), IDENTIFIER_SAMPLES, ids=[k for k, _ in IDENTIFIER_SAMPLES])
def test_validator_rejects_private_identifiers(sample: str, kind: str) -> None:
    assert f"line 1: contains {kind}" in private_identifier_errors(f"note: {sample}")
    data = _ready_dict()
    data["environments"][0]["note"] = sample
    errors = inventory_errors(_dump(data))
    assert any(re.fullmatch(rf"line \d+: contains {re.escape(kind)}", e) for e in errors), errors
    assert not any(sample in e for e in errors)


@pytest.mark.parametrize(
    "text",
    [
        f"[#125]: {REPOSITORY_URL}/issues/125",
        f"[#116]: {REPOSITORY_URL}/pull/116",
        f"`APPROVED` ([2026-11-02]({REPOSITORY_URL}/issues/125#issuecomment-6114407084))",
        "The #108 Stage 2 runs used synthetic-100100-works and synthetic-10k-works.",
        "Recorded outside the repository on 2026-11-02.",
        "Python 3.13 and pydantic 2.13.5",
        f"- {{item: separate-non-prod-projects, state: REQUESTED, evidence: {{ref: {REPOSITORY_URL}"
        "/issues/125#issuecomment-6114407084, recorded_on: 2026-11-02}}",
    ],
    ids=["issue-link", "pull-link", "comment-link", "synthetic-scales", "date", "versions",
         "flow-comment-link"],
)
def test_identifier_scanner_allows_own_references_and_known_tokens(text: str) -> None:
    assert private_identifier_errors(text) == ()


def _set_extra_int_key(d: dict[str, Any]) -> None:
    d["environments"][0][123456789012] = "x"


ECHO_CASES: list[tuple[str, Mutation]] = [
    ("SECRET-TOKEN-XYZ", _set(_dev_bronze, "state", "SECRET-TOKEN-XYZ")),
    ("https://example.invalid/SECRET", _set(_gke_evidence, "ref", "https://example.invalid/SECRET")),
    ("owner@example.com", lambda d: d["environments"][0].__setitem__("owner@example.com", "x")),
    ("SECRET-KEY-XYZ", lambda d: d.__setitem__("SECRET-KEY-XYZ", "x")),
    ("123456789012", _set_extra_int_key),
    ("my-proj-a1b2c3", _set(_dev_bronze, "item", "my-proj-a1b2c3")),
    ("my-proj-a1b2c3", _set(_gke, "answer", "my-proj-a1b2c3")),
]


@pytest.mark.parametrize(
    ("secret", "mutate"), ECHO_CASES,
    ids=["state", "ref", "email-key", "root-key", "int-key", "item-id", "answer"],
)
def test_validation_errors_never_echo_input(secret: str, mutate: Mutation) -> None:
    data = _ready_dict()
    mutate(data)
    errors = inventory_errors(_dump(data))
    assert errors
    assert not any(secret in e for e in errors), errors
    with pytest.raises(EnterpriseInventoryError) as raised:
        parse_enterprise_inventory(_dump(data))
    assert secret not in str(raised.value)


def test_schema_has_no_identifier_fields() -> None:
    forbidden = {"project", "bucket", "dataset", "email", "account", "member", "principal",
                 "billing", "network", "subnet", "ip", "amount", "price", "name", "id"}
    for model in INVENTORY_MODELS:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert model.model_config.get("frozen") is True, model.__name__
        assert model.model_config.get("hide_input_in_errors") is True, model.__name__
        for field in model.model_fields:
            assert not set(field.split("_")) & forbidden, (model.__name__, field)


# --- determinism ----------------------------------------------------------------


def test_assessment_is_deterministic_and_idempotent() -> None:
    for data in (_committed_dict(), _ready_dict()):
        inventory = _parse(data)
        first, second = assess_enterprise_inventory(inventory), assess_enterprise_inventory(inventory)
        assert first == second
        assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
            second.model_dump(mode="json"), sort_keys=True
        )
        replayed = parse_enterprise_inventory(
            INVENTORY_HEADER + yaml.safe_dump(inventory.model_dump(mode="json"), sort_keys=False)
        )
        assert replayed == inventory
        assert assess_enterprise_inventory(replayed) == first
        with pytest.raises(ValidationError):
            inventory.as_of = ADR_0001_ACCEPTED_ON  # type: ignore[misc]


def test_render_is_deterministic_and_ascii() -> None:
    for data in (_committed_dict(), _ready_dict()):
        inventory = _parse(data)
        rendered = render_inventory_status(inventory)
        assert rendered == render_inventory_status(inventory)
        assert rendered.isascii()
        assert rendered.startswith(GATE_SECTION_HEADING + "\n\n")
        assert rendered.endswith("|\n\n") and not rendered.endswith("\n\n\n")
    assert "**Gate verdict: `READY_TO_VALIDATE`.**" in render_inventory_status(_parse(_ready_dict()))
    assert "**Gate verdict: `BLOCKED`.**" in render_inventory_status(load_enterprise_inventory())
    link = f" ([{READY_AS_OF.isoformat()}]({REPOSITORY_URL}/issues/125#issuecomment-101))"
    assert render_inventory_status(_parse(_ready_dict())).count(link) == (
        len(INVENTORY_ENVIRONMENTS)
        + len(ROLE_ORDER)
        + len(INVENTORY_ENVIRONMENTS) * len(ENVIRONMENT_ITEMS)
        + len(PROGRAM_ITEMS)
    )


def test_render_counts_environments_without_a_blocker() -> None:
    assert "3 of 3 enterprise environments have no blocker." in render_inventory_status(
        _parse(_ready_dict())
    )
    data = _ready_dict()
    holder = data["role_holders"][0]
    holder["holder"] = "NOT_ASSIGNED"
    del holder["evidence"]
    assert "0 of 3 enterprise environments have no blocker." in render_inventory_status(_parse(data))


ENV_TOKEN = re.compile(r"(?<![\w-])(?:GCP_SANDBOX|GCP sandbox|Sandbox|sandbox|LOCAL|DEV|QA|PROD)(?![\w-])")
STATUS_TOKEN = re.compile(
    r"\b(?:READY_TO_VALIDATE|BLOCKED|VERIFIED|NOT_READY|NOT READY|CLOUD_UNVERIFIED|"
    r"CONTRACT_DEFINED|DEMONSTRATED_LOCAL|NOT_IMPLEMENTED|READY)\b"
)


def _mixed_dict() -> dict[str, Any]:
    data = _ready_dict()
    _env_fact(data, "DEV", "bronze-bucket")["state"] = "REQUESTED"
    _program_fact(data, "two-buckets-per-environment")["answer"] = "ONE_BUCKET_ONLY"
    return data


def test_rendered_tables_never_put_an_environment_and_a_status_in_one_cell() -> None:
    # The page is claim-checked by test_readiness_docs_truth.py; this keeps every
    # possible render safe for that checker, not only the committed one.
    for data in (_committed_dict(), _ready_dict(), _mixed_dict()):
        for line in render_inventory_status(_parse(data)).splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for cell in cells:
                assert not (ENV_TOKEN.search(cell) and STATUS_TOKEN.search(cell)), cell
            assert not ({"GCP_SANDBOX", "PROD"} <= {c.strip("`* ").upper() for c in cells}), line


# --- the readiness registry is untouched ------------------------------------------


def test_readiness_package_does_not_reference_the_inventory() -> None:
    package = REPO_ROOT / "src/research_platform/readiness"
    for name in ("__init__.py", "models.py", "registry.py", "validation.py"):
        text = (package / name).read_text(encoding="utf-8")
        assert "enterprise_inventory" not in text and "enterprise-environments" not in text, name


def test_cloud_scripts_do_not_read_the_inventory() -> None:
    scripts = sorted((REPO_ROOT / "scripts").glob("*.py"))
    assert scripts
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "enterprise_inventory" not in text and "enterprise-environments" not in text, script.name


# --- the page ---------------------------------------------------------------------


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_inventory_doc_gate_section_matches_the_yaml() -> None:
    text = _doc()
    start = text.index(GATE_SECTION_HEADING)
    section = text[start : text.index("\n## ", start + 1) + 1]
    assert section == render_inventory_status(load_enterprise_inventory())


def test_inventory_doc_has_no_private_identifiers() -> None:
    assert private_identifier_errors(_doc()) == ()


def test_inventory_page_is_in_the_sidebar_once() -> None:
    sidebars = SIDEBARS.read_text(encoding="utf-8")
    assert sidebars.count("'operations/enterprise-environment-inventory'") == 1


def _backticked_ids(line: str) -> set[str]:
    return set(re.findall(r"`([a-z][a-z0-9-]*)`", line))


def test_owner_role_section_matches_the_gate_rules() -> None:
    section = _section(_doc(), "## What each owner role owes")
    for role in ROLE_ORDER:
        lines = [line for line in section.splitlines() if line.startswith(f"- **{ROLE_LABELS[role]}:**")]
        assert len(lines) == 1, role
        assert _backticked_ids(lines[0]) == {r.item for r in GATE_RULES if r.owner_role is role}, role


def test_consumer_table_matches_the_gate_rules() -> None:
    section = _section(_doc(), "## Consumers")
    consumers = sorted({c for r in GATE_RULES for c in r.consumers})
    rows = [line for line in section.splitlines() if re.match(r"\| DE-\d{2} ", line)]
    assert len(rows) == len(consumers)
    for consumer in consumers:
        number = 121 + int(consumer[3:])
        matching = [row for row in rows if row.startswith(f"| {consumer} ([#{number}])")]
        assert len(matching) == 1, consumer
        assert _backticked_ids(matching[0]) == {r.item for r in GATE_RULES if consumer in r.consumers}


def test_hand_written_gate_statements_match_the_assessment() -> None:
    inventory = load_enterprise_inventory()
    blocked = assess_enterprise_inventory(inventory).gate is InventoryVerdict.BLOCKED
    none_provisioned = all(e.ownership is Ownership.NOT_PROVISIONED for e in inventory.environments)
    none_assigned = all(h.holder is HolderState.NOT_ASSIGNED for h in inventory.role_holders)
    doc = " ".join(_doc().split())
    readiness = " ".join(READINESS_DOC.read_text(encoding="utf-8").split())
    assert (BLOCKER_STATUS in doc) is blocked
    assert (NO_ENTERPRISE_PROJECT in doc) is none_provisioned
    assert (NO_ENTERPRISE_PROJECT in readiness) is none_provisioned
    assert (NO_ROLE_HOLDER in doc) is none_assigned


def test_inventory_doc_and_readiness_doc_link_each_other() -> None:
    doc = _doc()
    assert "(../operations/enterprise-environment-inventory.md)" in READINESS_DOC.read_text(encoding="utf-8")
    assert "(../../config/enterprise-environments/inventory.yaml)" in doc
    assert (
        "The inventory verdict (`READY_TO_VALIDATE` or `BLOCKED`) changes no readiness level "
        "and no `can_promote` decision." in " ".join(doc.split())
    )
    assert "docs/operations/enterprise-environment-inventory.md" in INVENTORY_HEADER
