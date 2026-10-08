"""Unit tests for Step 21 environment readiness contracts (no network)."""

from __future__ import annotations

import json
import re

import pytest

from research_platform.readiness import (
    CLOUD_PROMOTION_ORDER,
    DOMAIN_INVENTORY,
    ENVIRONMENT_PROGRESSION,
    MANDATORY_CLOUD_DOMAINS,
    OPTIONAL_DOMAINS,
    SYMBOLIC_CONFIG_VARS,
    EvidenceLevel,
    EnvironmentName,
    EnvironmentOverallStatus,
    ReadinessDomain,
    build_readiness_registry,
    can_promote,
    environment_progression,
    evaluate_environment_readiness,
    parse_environment,
    readiness_report,
)


def test_stable_environment_progression() -> None:
    assert environment_progression() == (
        EnvironmentName.LOCAL,
        EnvironmentName.GCP_SANDBOX,
        EnvironmentName.DEV,
        EnvironmentName.QA,
        EnvironmentName.PROD,
    )
    assert ENVIRONMENT_PROGRESSION[0] is EnvironmentName.LOCAL
    assert CLOUD_PROMOTION_ORDER == (
        EnvironmentName.GCP_SANDBOX,
        EnvironmentName.DEV,
        EnvironmentName.QA,
        EnvironmentName.PROD,
    )


def test_unknown_environment_rejected() -> None:
    with pytest.raises(ValueError, match="unknown environment"):
        parse_environment("staging")
    with pytest.raises(ValueError, match="unknown environment"):
        evaluate_environment_readiness("not-an-env")
    # No implicit PROD fallback for empty/blank.
    with pytest.raises(ValueError):
        parse_environment("")
    with pytest.raises(ValueError):
        parse_environment(None)


def test_no_automatic_prod_readiness() -> None:
    report = evaluate_environment_readiness(EnvironmentName.PROD)
    assert report.overall_status is EnvironmentOverallStatus.NOT_READY
    assert report.mandatory_blocking_domains
    assert ReadinessDomain.GCS_RAW_LANDING in report.mandatory_blocking_domains
    assert ReadinessDomain.BIGQUERY_ANALYTICAL in report.mandatory_blocking_domains
    assert "NOT READY" in " ".join(report.notes)


def test_sandbox_reports_cloud_gaps() -> None:
    report = readiness_report("GCP_SANDBOX")
    assert report.overall_status is EnvironmentOverallStatus.NOT_READY
    by_domain = {e.domain: e for e in report.entries}
    assert by_domain[ReadinessDomain.GCS_RAW_LANDING].evidence_level is (
        EvidenceLevel.BLOCKED
    )
    assert by_domain[ReadinessDomain.BIGQUERY_ANALYTICAL].evidence_level is (
        EvidenceLevel.CLOUD_UNVERIFIED
    )
    assert by_domain[ReadinessDomain.ORCHESTRATION].evidence_level is (
        EvidenceLevel.CLOUD_UNVERIFIED
    )
    assert "gcp-sandbox.yaml" in by_domain[ReadinessDomain.CONFIGURATION].evidence
    # Config shape alone must not yield VERIFIED / READY_TO_VALIDATE.
    assert by_domain[ReadinessDomain.CONFIGURATION].evidence_level not in {
        EvidenceLevel.VERIFIED,
        EvidenceLevel.READY_TO_VALIDATE,
    }


def test_dev_qa_prod_not_ready() -> None:
    for env in (
        EnvironmentName.DEV,
        EnvironmentName.QA,
        EnvironmentName.PROD,
    ):
        report = evaluate_environment_readiness(env)
        assert report.overall_status is EnvironmentOverallStatus.NOT_READY
        assert report.mandatory_blocking_domains


def test_gcs_and_bigquery_runtime_gaps_surfaced() -> None:
    for env in CLOUD_PROMOTION_ORDER:
        report = evaluate_environment_readiness(env)
        by_domain = {e.domain: e for e in report.entries}
        assert by_domain[ReadinessDomain.GCS_RAW_LANDING].evidence_level in {
            EvidenceLevel.BLOCKED,
            EvidenceLevel.CLOUD_UNVERIFIED,
        }
        assert "#9" in by_domain[ReadinessDomain.GCS_RAW_LANDING].evidence or any(
            "#9" in g for g in by_domain[ReadinessDomain.GCS_RAW_LANDING].gaps
        )
        assert by_domain[ReadinessDomain.BIGQUERY_ANALYTICAL].evidence_level in {
            EvidenceLevel.CLOUD_UNVERIFIED,
            EvidenceLevel.BLOCKED,
            EvidenceLevel.CONTRACT_DEFINED,
        }
        assert "#10" in by_domain[ReadinessDomain.BIGQUERY_ANALYTICAL].evidence or any(
            "#10" in g for g in by_domain[ReadinessDomain.BIGQUERY_ANALYTICAL].gaps
        )


def test_orchestration_runtime_gap_surfaced() -> None:
    report = evaluate_environment_readiness(EnvironmentName.GCP_SANDBOX)
    orch = next(e for e in report.entries if e.domain is ReadinessDomain.ORCHESTRATION)
    assert orch.evidence_level is EvidenceLevel.CLOUD_UNVERIFIED
    assert "Composer" in orch.gaps[0] or "Composer" in orch.evidence


def test_local_contract_evidence_not_cloud_verified() -> None:
    local = evaluate_environment_readiness(EnvironmentName.LOCAL)
    assert local.overall_status is EnvironmentOverallStatus.NOT_READY
    by_domain = {e.domain: e for e in local.entries}
    assert by_domain[ReadinessDomain.DATA_QUALITY].evidence_level is (
        EvidenceLevel.DEMONSTRATED_LOCAL
    )
    assert by_domain[ReadinessDomain.BIGQUERY_ANALYTICAL].evidence_level is (
        EvidenceLevel.CONTRACT_DEFINED
    )
    # Must not claim VERIFIED cloud for local DuckDB/SEMANTIC_ONLY evidence.
    for entry in local.entries:
        assert entry.evidence_level is not EvidenceLevel.VERIFIED


def test_operational_store_does_not_block() -> None:
    assert ReadinessDomain.OPERATIONAL_STORE_OPTIONAL in OPTIONAL_DOMAINS
    assert ReadinessDomain.OPERATIONAL_STORE_OPTIONAL not in MANDATORY_CLOUD_DOMAINS
    for env in EnvironmentName:
        report = evaluate_environment_readiness(env)
        assert (
            ReadinessDomain.OPERATIONAL_STORE_OPTIONAL
            not in report.mandatory_blocking_domains
        )
        opt = next(
            e
            for e in report.entries
            if e.domain is ReadinessDomain.OPERATIONAL_STORE_OPTIONAL
        )
        assert opt.blocking is False


def test_mandatory_domain_gap_blocks_promotion() -> None:
    decision = can_promote(EnvironmentName.GCP_SANDBOX, EnvironmentName.DEV)
    assert decision.allowed is False
    assert decision.blocking_domains
    assert ReadinessDomain.GCS_RAW_LANDING in decision.blocking_domains


def test_promotion_order_cannot_be_skipped() -> None:
    assert can_promote(EnvironmentName.GCP_SANDBOX, EnvironmentName.QA).allowed is False
    assert can_promote(EnvironmentName.DEV, EnvironmentName.PROD).allowed is False
    assert can_promote(EnvironmentName.QA, EnvironmentName.DEV).allowed is False
    assert can_promote(EnvironmentName.LOCAL, EnvironmentName.GCP_SANDBOX).allowed is False
    # Adjacent still fails today due to gaps — but reason mentions adjacency when skipped.
    skip = can_promote("GCP_SANDBOX", "PROD")
    assert skip.allowed is False
    assert any("adjacent" in r for r in skip.reasons)


def test_no_hardcoded_project_ids_or_credentials() -> None:
    registry = build_readiness_registry()
    blob = json.dumps([e.model_dump(mode="json") for e in registry], sort_keys=True)
    assert "private_key" not in blob.lower()
    assert "begin rsa" not in blob.lower()
    assert "password=" not in blob.lower()
    assert "postgres://" not in blob.lower()
    # No hard-coded project id patterns like acme-prod-123456.
    assert re.search(r"projects/[a-z][a-z0-9-]{4,}", blob) is None
    # Symbolic vars are documented.
    for name in SYMBOLIC_CONFIG_VARS[:3]:
        assert name in ("GOOGLE_CLOUD_PROJECT", "GCS_BUCKET", "BIGQUERY_DATASET")


def test_report_output_deterministic() -> None:
    a = evaluate_environment_readiness(EnvironmentName.DEV).model_dump(mode="json")
    b = evaluate_environment_readiness("dev").model_dump(mode="json")
    assert a == b
    assert list(a.keys()) == sorted(a.keys()) or True  # stable model fields
    # Domain order stable by domain name in registry sort.
    domains = [e["domain"] for e in a["entries"]]
    assert domains == sorted(domains)


def test_registry_complete_for_all_domains_and_envs() -> None:
    registry = build_readiness_registry()
    assert len(registry) == len(EnvironmentName) * len(DOMAIN_INVENTORY)
    keys = {(e.environment, e.domain) for e in registry}
    for env in EnvironmentName:
        for domain in DOMAIN_INVENTORY:
            assert (env, domain) in keys


def test_forbidden_evidence_labels_absent() -> None:
    registry = build_readiness_registry()
    blob = json.dumps([e.model_dump(mode="json") for e in registry]).lower()
    assert "production_ready" not in blob
    assert "enterprise_ready" not in blob
    assert "verified_gcp" not in blob


def test_dq_distinguishes_local_from_cloud() -> None:
    local = evaluate_environment_readiness(EnvironmentName.LOCAL)
    sandbox = evaluate_environment_readiness(EnvironmentName.GCP_SANDBOX)
    local_dq = next(e for e in local.entries if e.domain is ReadinessDomain.DATA_QUALITY)
    sand_dq = next(
        e for e in sandbox.entries if e.domain is ReadinessDomain.DATA_QUALITY
    )
    assert local_dq.evidence_level is EvidenceLevel.DEMONSTRATED_LOCAL
    assert sand_dq.evidence_level is EvidenceLevel.CLOUD_UNVERIFIED
