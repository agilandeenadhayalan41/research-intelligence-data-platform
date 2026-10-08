"""Truthful environment readiness registry (no cloud calls, no project IDs)."""

from __future__ import annotations

from research_platform.readiness.models import (
    DOMAIN_INVENTORY,
    MANDATORY_CLOUD_DOMAINS,
    OPTIONAL_DOMAINS,
    EvidenceLabel,
    EvidenceLevel,
    EnvironmentName,
    ReadinessDomain,
    ReadinessEntry,
)

# Symbolic config identifiers only — never hard-code enterprise project values.
SYMBOLIC_CONFIG_VARS: tuple[str, ...] = (
    "GOOGLE_CLOUD_PROJECT",
    "GCS_BUCKET",
    "BIGQUERY_DATASET",
    "POSTGRES_DSN",
)


def _entry(
    domain: ReadinessDomain,
    environment: EnvironmentName,
    level: EvidenceLevel,
    evidence: str,
    *,
    gaps: tuple[str, ...] = (),
    next_action: str,
    labels: tuple[EvidenceLabel, ...] = (),
    blocking: bool | None = None,
    owner_type: str = "platform",
) -> ReadinessEntry:
    if blocking is None:
        if domain in OPTIONAL_DOMAINS:
            blocking = False
        else:
            blocking = level in {
                EvidenceLevel.BLOCKED,
                EvidenceLevel.CLOUD_UNVERIFIED,
                EvidenceLevel.CONTRACT_DEFINED,
                EvidenceLevel.DEMONSTRATED_LOCAL,
            }
            # READY_TO_VALIDATE / VERIFIED are non-blocking for that domain.
            if level in {EvidenceLevel.READY_TO_VALIDATE, EvidenceLevel.VERIFIED}:
                blocking = False
    return ReadinessEntry(
        domain=domain,
        environment=environment,
        evidence_level=level,
        evidence_labels=labels,
        evidence=evidence,
        gaps=gaps,
        required_next_action=next_action,
        blocking=blocking,
        owner_type=owner_type,
    )


def _local_entries() -> tuple[ReadinessEntry, ...]:
    env = EnvironmentName.LOCAL
    rows: list[ReadinessEntry] = [
        _entry(
            ReadinessDomain.CONFIGURATION,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "config/local.yaml loads with explicit environment=local; offline tests use it",
            next_action="Keep LOCAL as the default for offline CI/tests only",
            labels=(EvidenceLabel.LOCAL_TESTED,),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.GCS_RAW_LANDING,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "LocalObjectStore + GCSObjectStore (fake-client contract tests) demonstrated; "
            "no live GCS bucket evidence (#9)",
            gaps=("No ADC/live-bucket sandbox execution evidence",),
            next_action="Run separately approved sandbox smoke against a real bucket before VERIFIED",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.CONTRACT_DEFINED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.BIGQUERY_ANALYTICAL,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Step 15–16 BigQuery SQL/contracts + DuckDB SEMANTIC_ONLY; "
            "BigQueryWarehouse adapter offline-tested (#10); no live job evidence",
            gaps=("No paid/MEASURED BigQuery execution evidence",),
            next_action="Run separately approved sandbox smoke before VERIFIED",
            labels=(EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.SEMANTIC_ONLY),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.PUBLICATION_CONCURRENCY,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Step 20 PublicationScope: TEMP_TABLES recommended; RUN_SCOPED/SINGLE_WRITER documented",
            gaps=("Not exercised in BigQuery",),
            next_action="Validate concurrency strategy in a real BigQuery script/session",
            labels=(EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.ORCHESTRATION,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Step 20 orchestration contracts + dry-run plan renderer (CONTRACT_ONLY)",
            gaps=("No Airflow/Composer runtime",),
            next_action="Map ExecutionUnits to a future scheduler after cloud adapters exist",
            labels=(EvidenceLabel.CONTRACT_DEFINED,),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.DATA_QUALITY,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "Step 17/19 DQ gates demonstrated locally (DuckDB SEMANTIC_ONLY)",
            gaps=("BigQuery-executed DQ unverified", "Production alerting absent"),
            next_action="Re-run DQ against BigQuery when runtime exists; add alerting later",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.SEMANTIC_ONLY),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.GOLD_MARTS,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "Step 16/19 Gold marts built locally via shared semantic builder",
            gaps=("No BigQuery Gold deployment",),
            next_action="Deploy/validate Gold SQL in BigQuery after #10",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.SEMANTIC_ONLY),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.DATA_SERVICE,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "Step 18 domain layer + in-memory SEMANTIC_ONLY repository demonstrated",
            gaps=("No HTTP deploy", "No production auth/cache/scaling"),
            next_action="Design HTTP/auth deployment separately; do not claim product API ready",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.CONTRACT_DEFINED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.CI_CD,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "GitHub Actions runs make check / make test / make build (local/CI validation only)",
            gaps=("No cloud deployment pipeline", "No environment promotion automation"),
            next_action="Design promotion pipeline after cloud runtimes exist",
            labels=(EvidenceLabel.LOCAL_TESTED,),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.RECOVERY,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Steps 12–20 document recovery for landing replay, conflicts, DQ, publication rollback",
            gaps=("No automated cloud recovery",),
            next_action="Exercise recovery runbooks against sandbox cloud runtimes",
            labels=(EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.LOCAL_TESTED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.ROLLBACK,
            env,
            EvidenceLevel.DEMONSTRATED_LOCAL,
            "Step 19 final-validation failure restores previous publication (local)",
            gaps=("BigQuery destructive/schema rollback not cloud-validated",),
            next_action="Prefer versioned forward-fix publication in cloud; document BQ rollback limits",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.CONTRACT_DEFINED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.LINEAGE,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Control/provenance models carry run_id, checksum, source identity, activity_state",
            gaps=("Environment/code-release lineage fields not cloud-published",),
            next_action="Ensure publication/lineage fields propagate in cloud runs",
            labels=(EvidenceLabel.CONTRACT_DEFINED,),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.OPERATIONAL_STORE_OPTIONAL,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "PostgreSQL/AlloyDB serving remains CONDITIONAL (#23); not a readiness prerequisite",
            gaps=(
                "No measured BigQuery+API/cache failure justifying operational store",
            ),
            next_action="Only consider if benchmarks prove BigQuery+API/cache insufficient",
            labels=(EvidenceLabel.OPTIONAL, EvidenceLabel.OPTIONAL_NOT_REQUIRED),
            blocking=False,
        ),
    ]
    # Remaining domains: contract-defined / not cloud-relevant for LOCAL.
    covered = {r.domain for r in rows}
    for domain in DOMAIN_INVENTORY:
        if domain in covered:
            continue
        rows.append(
            _entry(
                domain,
                env,
                EvidenceLevel.CONTRACT_DEFINED,
                f"{domain.value} requirements documented for future cloud environments",
                next_action=f"Validate {domain.value} when selecting a cloud environment",
                labels=(EvidenceLabel.CONTRACT_DEFINED,),
                blocking=False,
            )
        )
    return tuple(sorted(rows, key=lambda r: r.domain.value))


def _sandbox_entries() -> tuple[ReadinessEntry, ...]:
    env = EnvironmentName.GCP_SANDBOX
    return _cloud_placeholder_matrix(
        env,
        configuration_evidence=(
            "config/gcp-sandbox.yaml defines future GCS/BigQuery shape via "
            "symbolic ${GOOGLE_CLOUD_PROJECT}/${GCS_BUCKET}/${BIGQUERY_DATASET}"
        ),
        configuration_labels=(
            EvidenceLabel.CONTRACT_DEFINED,
            EvidenceLabel.CLOUD_UNVERIFIED,
        ),
        configuration_gaps=(
            "No actual GCP project/bucket/dataset provisioned by this repository",
            "GCS and BigQuery adapters not implemented",
        ),
    )


def _dev_entries() -> tuple[ReadinessEntry, ...]:
    return _cloud_placeholder_matrix(
        EnvironmentName.DEV,
        configuration_evidence=(
            "config/dev.yaml is a local-only placeholder (storage.backend=local); "
            "not a deployed development environment"
        ),
        configuration_labels=(
            EvidenceLabel.PLACEHOLDER_CONFIG,
            EvidenceLabel.CLOUD_UNVERIFIED,
        ),
        configuration_gaps=(
            "No deployed DEV cloud resources",
            "IAM/secrets/runtime unverified",
        ),
    )


def _qa_entries() -> tuple[ReadinessEntry, ...]:
    return _cloud_placeholder_matrix(
        EnvironmentName.QA,
        configuration_evidence=(
            "config/qa.yaml is a local-only placeholder; no deployment evidence"
        ),
        configuration_labels=(
            EvidenceLabel.PLACEHOLDER_CONFIG,
            EvidenceLabel.CLOUD_UNVERIFIED,
        ),
        configuration_gaps=("No QA cloud deployment", "No promotion evidence from DEV"),
    )


def _prod_entries() -> tuple[ReadinessEntry, ...]:
    return _cloud_placeholder_matrix(
        EnvironmentName.PROD,
        configuration_evidence=(
            "config/prod.yaml is a local-only placeholder; PROD has no deployment/access evidence"
        ),
        configuration_labels=(
            EvidenceLabel.PLACEHOLDER_CONFIG,
            EvidenceLabel.NOT_IMPLEMENTED,
        ),
        configuration_gaps=(
            "No PROD access",
            "No production credentials/resources in repository",
            "Explicitly NOT READY",
        ),
        force_blocked_domains=frozenset(MANDATORY_CLOUD_DOMAINS),
    )


def _cloud_placeholder_matrix(
    env: EnvironmentName,
    *,
    configuration_evidence: str,
    configuration_labels: tuple[EvidenceLabel, ...],
    configuration_gaps: tuple[str, ...],
    force_blocked_domains: frozenset[ReadinessDomain] | None = None,
) -> tuple[ReadinessEntry, ...]:
    force_blocked = force_blocked_domains or frozenset()
    rows: list[ReadinessEntry] = []

    def level_for(domain: ReadinessDomain, default: EvidenceLevel) -> EvidenceLevel:
        if domain in force_blocked and default is not EvidenceLevel.BLOCKED:
            # PROD mandatory domains stay BLOCKED / CLOUD_UNVERIFIED honestly.
            if domain is ReadinessDomain.CONFIGURATION:
                return EvidenceLevel.BLOCKED
            return EvidenceLevel.BLOCKED
        return default

    specs: dict[ReadinessDomain, tuple[EvidenceLevel, str, tuple[str, ...], str, tuple[EvidenceLabel, ...]]] = {
        ReadinessDomain.CONFIGURATION: (
            EvidenceLevel.CONTRACT_DEFINED,
            configuration_evidence,
            configuration_gaps,
            "Supply environment-specific identifiers via approved config/env management; never commit credentials",
            configuration_labels,
        ),
        ReadinessDomain.IDENTITY_IAM: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Least-privilege IAM capabilities documented; no bindings created",
            ("No runtime/deployment identities verified", "No environment isolation evidence"),
            "Design least-privilege roles and separate deploy vs runtime identities",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.WORKLOAD_IDENTITY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Prefer workload identity / short-lived credentials for future GCP runtime",
            ("No WIF pools/providers", "No Composer/runtime attached identity"),
            "Configure workload identity federation outside this repository when adapters exist",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.SECRETS: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Secrets must come from approved secret manager/runtime; Step-20 XCom forbids credentials",
            ("No Secret Manager secrets created", "No rotation evidence"),
            "Wire secret references in deployment config only; never store secrets in git/XCom/logs",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.GCS_RAW_LANDING: (
            EvidenceLevel.READY_TO_VALIDATE,
            "GCSObjectStore adapter implemented (#9) with create-only if_generation_match=0; "
            "offline fake-client contract tests only — not live-bucket VERIFIED",
            (
                "No approved sandbox ADC/bucket execution evidence",
                "Immutable GCS write/precondition behavior unverified in cloud",
            ),
            "Run separately approved sandbox smoke (ADC + real bucket) before any VERIFIED claim",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.BIGQUERY_ANALYTICAL: (
            EvidenceLevel.READY_TO_VALIDATE,
            "BigQueryWarehouse adapter implemented (#10) with dry-run + maximum_bytes_billed; "
            "offline fake-client tests only — not live-job VERIFIED",
            (
                "No approved sandbox ADC/dataset execution evidence",
                "No MERGE/partition/cluster MEASURED evidence",
                "No live maximum_bytes_billed enforcement evidence",
            ),
            "Run separately approved sandbox smoke (ADC + real dataset) before any VERIFIED claim",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.PUBLICATION_CONCURRENCY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Step 20 recommends TEMP_TABLES; RUN_SCOPED/SINGLE_WRITER documented — not cloud-verified",
            ("No BigQuery script/session concurrency proof",),
            "Exercise PublicationScope strategy in BigQuery after analytical runtime exists",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.ORCHESTRATION: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Step 20 ExecutionUnit/DAG/XCom/retry contracts defined; scheduler runtime absent",
            ("No Airflow/Composer deploy", "No DAG deployment verification"),
            "Map contracts to a future scheduler only after cloud data plane exists",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.DATA_QUALITY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "DQ logic demonstrated locally (SEMANTIC_ONLY); BigQuery-executed DQ unverified",
            ("No production alerting/on-call", "DuckDB success ≠ BigQuery validation"),
            "Execute DQ against BigQuery and define alerting after runtime exists",
            (EvidenceLabel.SEMANTIC_ONLY, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.GOLD_MARTS: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Gold mart contracts + local SEMANTIC_ONLY builder exist; cloud Gold unverified",
            ("No BigQuery Gold deployment",),
            "Deploy/validate Gold marts in BigQuery after #10",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.SEMANTIC_ONLY),
        ),
        ReadinessDomain.DATA_SERVICE: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Storage-independent Data Service domain contract + local in-memory demo",
            ("No HTTP/service deploy", "No production auth/scaling/cache"),
            "Treat as domain contract only until a separate serving deploy is scoped",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.OBSERVABILITY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Required signals documented (runs, DQ, publication, cost, freshness); no vendor SDK",
            ("No dashboards/alerts deployed", "No cloud audit trail"),
            "Implement logs/metrics/alerts against cloud runtimes when available",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.NOT_IMPLEMENTED),
        ),
        ReadinessDomain.COST_CONTROLS: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Controls documented (maximum_bytes_billed, budgets, bounded backfills); no amounts claimed",
            ("No budget alerts configured", "No cost attribution evidence"),
            "Apply cost controls in sandbox/DEV before any broader promotion",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.RECOVERY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Recovery scenarios documented from Steps 12–20; cloud automation absent",
            ("No sandbox recovery drill evidence",),
            "Run recovery drills in GCP_SANDBOX after adapters exist",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.ROLLBACK: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Local publication rollback demonstrated; BigQuery rollback strategy not cloud-validated",
            ("Prefer versioned forward-fix; destructive rollback unproven",),
            "Document and test cloud publication versioning/rollback limits before PROD",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.LINEAGE: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Required lineage fields documented (run_id, publication_version, checksum, …)",
            ("Cloud lineage publication unverified",),
            "Propagate lineage fields through cloud publication path",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.GOVERNANCE: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Governance requirements documented; public OpenAlex data still needs ownership/retention/audit",
            ("No access review / schema-change approval process evidenced",),
            "Define governance reviews before QA/PROD promotion",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.CI_CD: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Local/CI make check|test|build demonstrated; cloud deploy pipeline absent",
            ("No immutable promote/approve/rollback deploy pipeline",),
            "Design environment promotion CI/CD without automatic PROD deploy",
            (EvidenceLabel.LOCAL_TESTED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.NETWORKING: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Networking left CLOUD_UNVERIFIED (egress, private access, optional VPC-SC if required)",
            ("No VPC/DNS/proxy resources", "VPC-SC not prescribed as mandatory"),
            "Validate connectivity requirements with enterprise networking when cloud work starts",
            (EvidenceLabel.CLOUD_UNVERIFIED, EvidenceLabel.NOT_IMPLEMENTED),
        ),
        ReadinessDomain.SECURITY: (
            EvidenceLevel.CLOUD_UNVERIFIED,
            "Security boundaries documented (no keys in repo, least privilege, env isolation)",
            ("No cloud security review evidence",),
            "Complete security review as a PROD promotion gate",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.OPERATIONAL_STORE_OPTIONAL: (
            EvidenceLevel.CONTRACT_DEFINED,
            "PostgreSQL/AlloyDB OPTIONAL_NOT_REQUIRED; only if BigQuery+API/cache fails measured needs (#23)",
            ("No justifying benchmark failure",),
            "Do not treat operational store as a promotion prerequisite",
            (EvidenceLabel.OPTIONAL, EvidenceLabel.OPTIONAL_NOT_REQUIRED),
        ),
    }

    for domain in DOMAIN_INVENTORY:
        level, evidence, gaps, action, labels = specs[domain]
        level = level_for(domain, level)
        rows.append(
            _entry(
                domain,
                env,
                level,
                evidence,
                gaps=gaps,
                next_action=action,
                labels=labels,
            )
        )
    return tuple(sorted(rows, key=lambda r: r.domain.value))


def build_readiness_registry() -> tuple[ReadinessEntry, ...]:
    """Full deterministic registry across LOCAL + cloud progression environments."""
    entries = (
        *_local_entries(),
        *_sandbox_entries(),
        *_dev_entries(),
        *_qa_entries(),
        *_prod_entries(),
    )
    _assert_registry_invariants(entries)
    return entries


def entries_for_environment(
    environment: EnvironmentName,
    *,
    registry: tuple[ReadinessEntry, ...] | None = None,
) -> tuple[ReadinessEntry, ...]:
    rows = registry if registry is not None else build_readiness_registry()
    return tuple(e for e in rows if e.environment is environment)


def _assert_registry_invariants(entries: tuple[ReadinessEntry, ...]) -> None:
    # Completeness: every environment × domain exactly once.
    seen: set[tuple[EnvironmentName, ReadinessDomain]] = set()
    for entry in entries:
        key = (entry.environment, entry.domain)
        if key in seen:
            raise ValueError(f"duplicate readiness entry: {key}")
        seen.add(key)
        # No credentials / project IDs in evidence text.
        blob = " ".join(
            [
                entry.evidence,
                entry.required_next_action,
                *entry.gaps,
                entry.owner_type,
            ]
        ).lower()
        for forbidden in (
            "private_key",
            "begin rsa",
            "akia",
            "eyj",
            "password=",
            "postgres://",
            "mysql://",
        ):
            if forbidden in blob:
                raise ValueError(f"forbidden secret-like token in readiness registry: {forbidden}")
        # Disallow hard-coded GCP project id patterns (digits-heavy project forms).
        if "projects/" in blob and "google_cloud_project" not in blob:
            raise ValueError("hard-coded project path not allowed in readiness registry")

    expected_envs = {
        EnvironmentName.LOCAL,
        EnvironmentName.GCP_SANDBOX,
        EnvironmentName.DEV,
        EnvironmentName.QA,
        EnvironmentName.PROD,
    }
    for env in expected_envs:
        for domain in DOMAIN_INVENTORY:
            if (env, domain) not in seen:
                raise ValueError(f"missing readiness entry: {env}/{domain}")

    # Optional store must never block.
    for entry in entries:
        if entry.domain is ReadinessDomain.OPERATIONAL_STORE_OPTIONAL and entry.blocking:
            raise ValueError("optional operational store must not block")
