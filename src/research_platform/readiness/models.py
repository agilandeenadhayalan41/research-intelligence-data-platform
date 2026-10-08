"""Environment readiness contracts (Step 21 / #28).

Documentation/contracts only — no cloud SDK, no provisioning, no VERIFIED claims
without actual cloud evidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from research_platform.service.models import SettingsModel


READINESS_CONTRACT_VERSION = "environment-readiness-contract-v1"


class EnvironmentName(StrEnum):
    LOCAL = "LOCAL"
    GCP_SANDBOX = "GCP_SANDBOX"
    DEV = "DEV"
    QA = "QA"
    PROD = "PROD"


# Explicit progression. LOCAL is evidence/reference only — not a cloud rung.
ENVIRONMENT_PROGRESSION: tuple[EnvironmentName, ...] = (
    EnvironmentName.LOCAL,
    EnvironmentName.GCP_SANDBOX,
    EnvironmentName.DEV,
    EnvironmentName.QA,
    EnvironmentName.PROD,
)

CLOUD_PROMOTION_ORDER: tuple[EnvironmentName, ...] = (
    EnvironmentName.GCP_SANDBOX,
    EnvironmentName.DEV,
    EnvironmentName.QA,
    EnvironmentName.PROD,
)


class EvidenceLevel(StrEnum):
    """Honest readiness evidence levels."""

    DEMONSTRATED_LOCAL = "DEMONSTRATED_LOCAL"
    CONTRACT_DEFINED = "CONTRACT_DEFINED"
    CLOUD_UNVERIFIED = "CLOUD_UNVERIFIED"
    READY_TO_VALIDATE = "READY_TO_VALIDATE"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"


class EvidenceLabel(StrEnum):
    """Additional evidence-honest labels (never PRODUCTION_READY / VERIFIED_GCP)."""

    LOCAL_TESTED = "LOCAL_TESTED"
    CONTRACT_DEFINED = "CONTRACT_DEFINED"
    SEMANTIC_ONLY = "SEMANTIC_ONLY"
    CLOUD_UNVERIFIED = "CLOUD_UNVERIFIED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    OPTIONAL = "OPTIONAL"
    OPTIONAL_NOT_REQUIRED = "OPTIONAL_NOT_REQUIRED"
    PLACEHOLDER_CONFIG = "PLACEHOLDER_CONFIG"


class ReadinessDomain(StrEnum):
    CONFIGURATION = "CONFIGURATION"
    IDENTITY_IAM = "IDENTITY_IAM"
    WORKLOAD_IDENTITY = "WORKLOAD_IDENTITY"
    SECRETS = "SECRETS"
    GCS_RAW_LANDING = "GCS_RAW_LANDING"
    BIGQUERY_ANALYTICAL = "BIGQUERY_ANALYTICAL"
    PUBLICATION_CONCURRENCY = "PUBLICATION_CONCURRENCY"
    ORCHESTRATION = "ORCHESTRATION"
    DATA_QUALITY = "DATA_QUALITY"
    GOLD_MARTS = "GOLD_MARTS"
    DATA_SERVICE = "DATA_SERVICE"
    OBSERVABILITY = "OBSERVABILITY"
    COST_CONTROLS = "COST_CONTROLS"
    RECOVERY = "RECOVERY"
    ROLLBACK = "ROLLBACK"
    LINEAGE = "LINEAGE"
    GOVERNANCE = "GOVERNANCE"
    CI_CD = "CI_CD"
    NETWORKING = "NETWORKING"
    SECURITY = "SECURITY"
    OPERATIONAL_STORE_OPTIONAL = "OPERATIONAL_STORE_OPTIONAL"


DOMAIN_INVENTORY: tuple[ReadinessDomain, ...] = tuple(ReadinessDomain)


# Mandatory for cloud promotion (Sandbox→DEV→QA→PROD). Operational store excluded.
MANDATORY_CLOUD_DOMAINS: frozenset[ReadinessDomain] = frozenset(
    {
        ReadinessDomain.CONFIGURATION,
        ReadinessDomain.IDENTITY_IAM,
        ReadinessDomain.WORKLOAD_IDENTITY,
        ReadinessDomain.SECRETS,
        ReadinessDomain.GCS_RAW_LANDING,
        ReadinessDomain.BIGQUERY_ANALYTICAL,
        ReadinessDomain.PUBLICATION_CONCURRENCY,
        ReadinessDomain.ORCHESTRATION,
        ReadinessDomain.DATA_QUALITY,
        ReadinessDomain.GOLD_MARTS,
        ReadinessDomain.DATA_SERVICE,
        ReadinessDomain.OBSERVABILITY,
        ReadinessDomain.COST_CONTROLS,
        ReadinessDomain.RECOVERY,
        ReadinessDomain.ROLLBACK,
        ReadinessDomain.LINEAGE,
        ReadinessDomain.GOVERNANCE,
        ReadinessDomain.CI_CD,
        ReadinessDomain.NETWORKING,
        ReadinessDomain.SECURITY,
    }
)

OPTIONAL_DOMAINS: frozenset[ReadinessDomain] = frozenset(
    {ReadinessDomain.OPERATIONAL_STORE_OPTIONAL}
)


# Evidence levels that block promotion into / readiness of a target environment.
BLOCKING_LEVELS: frozenset[EvidenceLevel] = frozenset(
    {
        EvidenceLevel.BLOCKED,
        EvidenceLevel.CLOUD_UNVERIFIED,
        EvidenceLevel.CONTRACT_DEFINED,
        EvidenceLevel.DEMONSTRATED_LOCAL,
    }
)


class EnvironmentOverallStatus(StrEnum):
    """Aggregate environment status — never READY without VERIFIED mandatory domains."""

    NOT_READY = "NOT_READY"
    READY_TO_VALIDATE = "READY_TO_VALIDATE"
    READY = "READY"


class ReadinessEntry(SettingsModel):
    """One domain × environment readiness row."""

    domain: ReadinessDomain
    environment: EnvironmentName
    evidence_level: EvidenceLevel
    evidence_labels: tuple[EvidenceLabel, ...] = ()
    evidence: str = Field(min_length=1, max_length=1024)
    gaps: tuple[str, ...] = ()
    required_next_action: str = Field(min_length=1, max_length=1024)
    blocking: bool = True
    owner_type: str = Field(default="platform", max_length=64)

    @field_validator("evidence", "required_next_action", "owner_type", mode="before")
    @classmethod
    def strip_required(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must be non-empty")
            return stripped
        return value

    @model_validator(mode="after")
    def optional_store_never_blocks(self) -> ReadinessEntry:
        if self.domain is ReadinessDomain.OPERATIONAL_STORE_OPTIONAL and self.blocking:
            raise ValueError("OPERATIONAL_STORE_OPTIONAL must not be blocking")
        return self


class PromotionDecision(SettingsModel):
    allowed: bool
    from_environment: EnvironmentName
    to_environment: EnvironmentName
    blocking_domains: tuple[ReadinessDomain, ...] = ()
    reasons: tuple[str, ...] = ()


class EnvironmentReadinessReport(SettingsModel):
    """Deterministic readiness report for one environment (no cloud calls)."""

    contract_version: str = READINESS_CONTRACT_VERSION
    environment: EnvironmentName
    overall_status: EnvironmentOverallStatus
    entries: tuple[ReadinessEntry, ...]
    mandatory_blocking_domains: tuple[ReadinessDomain, ...]
    optional_domains: tuple[ReadinessDomain, ...] = (
        ReadinessDomain.OPERATIONAL_STORE_OPTIONAL,
    )
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
