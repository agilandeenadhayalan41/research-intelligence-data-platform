"""Pure-Python environment readiness evaluation and promotion gates.

No cloud SDK calls. No manual ready=True override.
"""

from __future__ import annotations

from research_platform.readiness.models import (
    BLOCKING_LEVELS,
    CLOUD_PROMOTION_ORDER,
    ENVIRONMENT_PROGRESSION,
    MANDATORY_CLOUD_DOMAINS,
    OPTIONAL_DOMAINS,
    READINESS_CONTRACT_VERSION,
    EvidenceLevel,
    EnvironmentName,
    EnvironmentOverallStatus,
    EnvironmentReadinessReport,
    PromotionDecision,
    ReadinessDomain,
    ReadinessEntry,
)
from research_platform.readiness.registry import (
    build_readiness_registry,
    entries_for_environment,
)


def parse_environment(value: object) -> EnvironmentName:
    """Reject unknown environments; no implicit PROD fallback."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("environment must be a non-empty string")
    key = value.strip().upper().replace("-", "_")
    # Accept common YAML forms: local, gcp-sandbox, gcp_sandbox, GCP_SANDBOX
    aliases = {
        "LOCAL": EnvironmentName.LOCAL,
        "GCP_SANDBOX": EnvironmentName.GCP_SANDBOX,
        "GCPSANDBOX": EnvironmentName.GCP_SANDBOX,
        "DEV": EnvironmentName.DEV,
        "QA": EnvironmentName.QA,
        "PROD": EnvironmentName.PROD,
        "PRODUCTION": EnvironmentName.PROD,
    }
    if key not in aliases:
        raise ValueError(f"unknown environment: {value!r}")
    return aliases[key]


def mandatory_blocking_domains(
    entries: tuple[ReadinessEntry, ...],
) -> tuple[ReadinessDomain, ...]:
    if not entries:
        return ()
    if entries[0].environment is EnvironmentName.LOCAL:
        # LOCAL is reference evidence only — not a cloud readiness target.
        return ()
    blocking: list[ReadinessDomain] = []
    for entry in entries:
        if entry.domain in OPTIONAL_DOMAINS:
            continue
        if entry.domain not in MANDATORY_CLOUD_DOMAINS:
            continue
        if entry.evidence_level in {
            EvidenceLevel.READY_TO_VALIDATE,
            EvidenceLevel.VERIFIED,
        }:
            continue
        if entry.blocking or entry.evidence_level in BLOCKING_LEVELS:
            blocking.append(entry.domain)
    return tuple(sorted(blocking, key=lambda d: d.value))


def overall_status_for(
    environment: EnvironmentName,
    entries: tuple[ReadinessEntry, ...],
) -> EnvironmentOverallStatus:
    if environment is EnvironmentName.LOCAL:
        # LOCAL is demonstrated reference evidence, never "cloud READY".
        return EnvironmentOverallStatus.NOT_READY

    mandatory = [e for e in entries if e.domain in MANDATORY_CLOUD_DOMAINS]
    if not mandatory:
        return EnvironmentOverallStatus.NOT_READY

    if any(
        e.evidence_level is EvidenceLevel.BLOCKED
        or e.evidence_level is EvidenceLevel.CLOUD_UNVERIFIED
        or e.evidence_level is EvidenceLevel.CONTRACT_DEFINED
        or e.evidence_level is EvidenceLevel.DEMONSTRATED_LOCAL
        for e in mandatory
    ):
        return EnvironmentOverallStatus.NOT_READY

    if all(e.evidence_level is EvidenceLevel.VERIFIED for e in mandatory):
        return EnvironmentOverallStatus.READY

    if all(
        e.evidence_level
        in {EvidenceLevel.READY_TO_VALIDATE, EvidenceLevel.VERIFIED}
        for e in mandatory
    ):
        return EnvironmentOverallStatus.READY_TO_VALIDATE

    return EnvironmentOverallStatus.NOT_READY


def evaluate_environment_readiness(
    environment: EnvironmentName | str,
    *,
    registry: tuple[ReadinessEntry, ...] | None = None,
) -> EnvironmentReadinessReport:
    """Evaluate readiness for one environment from the static registry."""
    env = (
        environment
        if isinstance(environment, EnvironmentName)
        else parse_environment(environment)
    )
    rows = entries_for_environment(env, registry=registry)
    if not rows:
        raise ValueError(f"no readiness entries for {env}")
    status = overall_status_for(env, rows)
    blocking = mandatory_blocking_domains(rows)
    notes = (
        "CONTRACT_ONLY readiness evaluation — no cloud calls",
        "YAML/config presence is not READY evidence",
        "OPERATIONAL_STORE_OPTIONAL never blocks promotion",
        f"overall_status={status.value}",
    )
    if env is EnvironmentName.PROD:
        notes = notes + ("PROD is explicitly NOT READY without VERIFIED cloud evidence",)
    if env is EnvironmentName.LOCAL:
        notes = notes + (
            "LOCAL is DEMONSTRATED_LOCAL / SEMANTIC_ONLY reference evidence only",
        )
    return EnvironmentReadinessReport(
        contract_version=READINESS_CONTRACT_VERSION,
        environment=env,
        overall_status=status,
        entries=rows,
        mandatory_blocking_domains=blocking,
        optional_domains=tuple(sorted(OPTIONAL_DOMAINS, key=lambda d: d.value)),
        notes=notes,
    )


def readiness_report(
    environment: EnvironmentName | str,
    *,
    registry: tuple[ReadinessEntry, ...] | None = None,
) -> EnvironmentReadinessReport:
    """Alias for evaluate_environment_readiness."""
    return evaluate_environment_readiness(environment, registry=registry)


def _adjacent_cloud_promotion(
    from_env: EnvironmentName, to_env: EnvironmentName
) -> bool:
    if from_env not in CLOUD_PROMOTION_ORDER or to_env not in CLOUD_PROMOTION_ORDER:
        return False
    i = CLOUD_PROMOTION_ORDER.index(from_env)
    j = CLOUD_PROMOTION_ORDER.index(to_env)
    return j == i + 1


def can_promote(
    from_environment: EnvironmentName | str,
    to_environment: EnvironmentName | str,
    *,
    registry: tuple[ReadinessEntry, ...] | None = None,
) -> PromotionDecision:
    """Promotion requires adjacent cloud order + non-blocking mandatory domains on target.

    LOCAL cannot promote into the cloud ladder. Skipping GCP_SANDBOX→DEV→QA→PROD
    is rejected. Missing mandatory evidence on the **target** blocks promotion.
    There is no ready=True override.
    """
    src = (
        from_environment
        if isinstance(from_environment, EnvironmentName)
        else parse_environment(from_environment)
    )
    dst = (
        to_environment
        if isinstance(to_environment, EnvironmentName)
        else parse_environment(to_environment)
    )
    reg = registry if registry is not None else build_readiness_registry()
    reasons: list[str] = []
    blocking: tuple[ReadinessDomain, ...] = ()

    if src is EnvironmentName.LOCAL or dst is EnvironmentName.LOCAL:
        reasons.append("LOCAL is evidence/reference only and is not a cloud promotion rung")
        return PromotionDecision(
            allowed=False,
            from_environment=src,
            to_environment=dst,
            blocking_domains=(),
            reasons=tuple(reasons),
        )

    if not _adjacent_cloud_promotion(src, dst):
        reasons.append(
            "promotion order must be adjacent: "
            + " -> ".join(e.value for e in CLOUD_PROMOTION_ORDER)
        )
        return PromotionDecision(
            allowed=False,
            from_environment=src,
            to_environment=dst,
            blocking_domains=(),
            reasons=tuple(reasons),
        )

    target = evaluate_environment_readiness(dst, registry=reg)
    blocking = target.mandatory_blocking_domains
    if blocking:
        reasons.append(
            "target environment has mandatory domain gaps: "
            + ", ".join(d.value for d in blocking)
        )
    if target.overall_status is EnvironmentOverallStatus.NOT_READY:
        reasons.append(f"target overall_status={target.overall_status.value}")

    # Source must also not be NOT_READY for a healthy promotion story — but today
    # every cloud env is NOT_READY, so adjacency+target gaps already fail closed.
    source = evaluate_environment_readiness(src, registry=reg)
    if source.overall_status is EnvironmentOverallStatus.NOT_READY:
        reasons.append(f"source overall_status={source.overall_status.value}")

    allowed = (
        not blocking
        and target.overall_status
        in {
            EnvironmentOverallStatus.READY_TO_VALIDATE,
            EnvironmentOverallStatus.READY,
        }
        and source.overall_status
        in {
            EnvironmentOverallStatus.READY_TO_VALIDATE,
            EnvironmentOverallStatus.READY,
        }
    )
    if allowed:
        reasons = ("promotion gates satisfied by readiness registry evidence",)

    return PromotionDecision(
        allowed=allowed,
        from_environment=src,
        to_environment=dst,
        blocking_domains=blocking,
        reasons=tuple(reasons),
    )


def environment_progression() -> tuple[EnvironmentName, ...]:
    return ENVIRONMENT_PROGRESSION
