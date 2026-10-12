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
            "no live GCS bucket in LOCAL (#9); live sandbox evidence is GCP_SANDBOX (#91)",
            gaps=("LOCAL never uses a live bucket; cloud evidence lives in GCP_SANDBOX",),
            next_action="Keep LOCAL offline; see GCP_SANDBOX for live-bucket evidence (#91)",
            labels=(EvidenceLabel.LOCAL_TESTED, EvidenceLabel.CONTRACT_DEFINED),
            blocking=False,
        ),
        _entry(
            ReadinessDomain.BIGQUERY_ANALYTICAL,
            env,
            EvidenceLevel.CONTRACT_DEFINED,
            "Step 15–16 BigQuery SQL/contracts + DuckDB SEMANTIC_ONLY; "
            "BigQueryWarehouse adapter offline-tested (#10); no live job in LOCAL; "
            "live sandbox adapter and model evidence is GCP_SANDBOX (#92, #103)",
            gaps=("No MEASURED BigQuery model execution evidence in LOCAL",),
            next_action="Keep LOCAL offline; see GCP_SANDBOX for live-job evidence (#92, #103)",
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
            "No enterprise-managed sandbox project; #91/#92/#103 and the #108 Stage 1 "
            "re-runs used an operator-owned project",
        ),
        domain_overrides={
            ReadinessDomain.BIGQUERY_ANALYTICAL: (
                EvidenceLevel.VERIFIED,
                "BigQueryWarehouse (#10) live sandbox smoke passed (#92): dry-run budget "
                "rejection, provider-enforced maximum_bytes_billed (bytesBilledLimitExceeded "
                "mapped to QueryBudgetExceededError), bounded execute, typed @parameter "
                "binding, empty/null Arrow results. Step 15-16 models deployed and MEASURED "
                "(#103, synthetic-10k-works): DDL matches contracts, all six publication "
                "decisions, works MERGE precedence, work_topics REPLACE rollback under "
                "failure injection, below-floor cap rejected, publication_year pruning "
                "observed; operator-owned "
                "sandbox project and dataset (identifiers redacted). #108 Stage 1 re-ran "
                "the #92 smoke (10/10) and the #103 run (95/95) as a least-privilege "
                "service account via impersonation; every executed job's user_email was "
                "that account. #108 Stage 2 MEASURED the models at synthetic-100100-works "
                "(95/95, same identity): clustering OBSERVED for 4 of 11 patterns",
                (
                    "MEASURED on synthetic data only (#103 synthetic-10k-works; #108 Stage 2 "
                    "synthetic-100100-works): production-scale cost/latency, concurrent "
                    "publication and Gold refresh/materialization unverified; billed bytes are "
                    "still mostly billing-floor artifacts",
                    "Clustering effectiveness OBSERVED for 4 of 11 patterns only (#108 Stage 2): "
                    "a leading-column filter cut processed bytes to 0.157 of the dry-run "
                    "estimate, non-leading filters showed none; works lookups NOT MEASURED "
                    "(table under 64 MiB)",
                    "MERGE/REPLACE exercised for works and work_topics only; the other "
                    "relationship tables and dimensions were direct-loaded",
                    "Operator-owned project, not an enterprise-managed project: #92/#103 "
                    "ran as user ADC; the #108 Stage 1 re-runs ran as a least-privilege "
                    "service account",
                    "One identity for deploy and runtime (#108 Stage 1); separate "
                    "deploy/runtime identities unverified",
                    "Job timeout/cancellation under load unverified (the #108 Stage 1 and "
                    "Stage 2 1 s probes completed before a cancel could trigger)",
                ),
                "Repeat the #103 deploy/MEASURE on a representative slice and in an "
                "enterprise-managed project with separate deploy/runtime identities before "
                "DEV promotion",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
            ReadinessDomain.GCS_RAW_LANDING: (
                EvidenceLevel.VERIFIED,
                "GCSObjectStore (#9) live sandbox smoke passed (#91): create via "
                "if_generation_match=0, identical replay no-op, bytes/provenance conflict "
                "raises ObjectConflictError with generation unchanged, open returns original "
                "bytes; operator-owned sandbox project and bucket (identifiers redacted). "
                "#108 Stage 1 re-ran it (17/17) as a least-privilege service account: "
                "replay/conflict still classified (412) without objects.delete, and "
                "out-of-prefix create and unconditioned overwrite refused with 403",
                (
                    "Operator-owned project, not an enterprise-managed project: #91 ran as "
                    "user ADC; the #108 Stage 1 re-run ran as a least-privilege service "
                    "account",
                    "Concurrent writers, retries, large/resumable uploads unverified",
                    "Encryption/CMEK, retention, access logging, environment separation unverified",
                ),
                "Repeat the #91 smoke in an enterprise-managed project with separate "
                "deploy/runtime identities before DEV promotion",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
        },
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
            "DEV cloud resources exist only as an operator-provisioned personal project "
            "(#108 Stage 3), not an enterprise-managed DEV environment; config/dev.yaml "
            "is still a local-only placeholder",
            "IAM/secrets/runtime unverified",
        ),
        domain_overrides={
            ReadinessDomain.GCS_RAW_LANDING: (
                EvidenceLevel.VERIFIED,
                "#108 Stage 3: the #91 GCSObjectStore smoke passed (17/17) in DEV as a "
                "least-privilege service account via impersonation: create via "
                "if_generation_match=0, identical replay no-op, bytes/provenance conflict "
                "raises ObjectConflictError with generation unchanged (412 classified without "
                "objects.delete), open returns original bytes; unconditioned overwrite and "
                "out-of-prefix create refused with 403 (identifiers redacted)",
                (
                    "Personal project labelled DEV, not an enterprise-managed DEV environment",
                    "One identity for deploy and runtime; separate deploy/runtime identities "
                    "unverified",
                    "Concurrent writers, retries, large/resumable uploads unverified",
                    "Encryption/CMEK, retention, access logging unverified",
                ),
                "Record QA evidence (#108 Stage 3) before any QA VERIFIED claim; no promotion",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
            ReadinessDomain.BIGQUERY_ANALYTICAL: (
                EvidenceLevel.VERIFIED,
                "#108 Stage 3: in DEV, as a least-privilege service account via impersonation, "
                "the #92 BigQueryWarehouse smoke passed (10/10: dry-run budget rejection, "
                "provider-enforced maximum_bytes_billed with 0 billed, bounded execute, typed "
                "@parameter binding, empty/null Arrow results; no datasets.create, other "
                "dataset refused) and the Step 15-16 models were deployed and MEASURED "
                "(95/95, synthetic-10k-works); every executed job's user_email was that "
                "account (identifiers redacted)",
                (
                    "Personal project labelled DEV, not an enterprise-managed DEV environment",
                    "MEASURED on synthetic-10k-works only in DEV: production-scale cost/latency "
                    "and clustering effectiveness unverified; billed bytes are billing-floor "
                    "artifacts",
                    "One identity for deploy and runtime; separate deploy/runtime identities "
                    "unverified",
                    "Job timeout/cancellation under load unverified (the 1 s probe completed "
                    "before a cancel could trigger)",
                ),
                "Record QA evidence (#108 Stage 3) before any QA VERIFIED claim; no promotion",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
        },
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
        configuration_gaps=(
            "QA cloud resources exist only as an operator-provisioned personal project "
            "(#108 Stage 3), not an enterprise-managed QA environment; config/qa.yaml is "
            "still a local-only placeholder",
            "No promotion evidence from DEV",
        ),
        domain_overrides={
            ReadinessDomain.GCS_RAW_LANDING: (
                EvidenceLevel.VERIFIED,
                "#108 Stage 3 (after DEV): the #91 GCSObjectStore smoke passed (17/17) in QA "
                "as a separate least-privilege service account via impersonation: create via "
                "if_generation_match=0, identical replay no-op, bytes/provenance conflict "
                "raises ObjectConflictError with generation unchanged (412 classified without "
                "objects.delete), open returns original bytes; unconditioned overwrite and "
                "out-of-prefix create refused with 403 (identifiers redacted)",
                (
                    "Personal project labelled QA, not an enterprise-managed QA environment",
                    "One identity for deploy and runtime; separate deploy/runtime identities "
                    "unverified",
                    "Concurrent writers, retries, large/resumable uploads unverified",
                    "Encryption/CMEK, retention, access logging unverified",
                ),
                "No promotion is claimed; PROD stays BLOCKED",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
            ReadinessDomain.BIGQUERY_ANALYTICAL: (
                EvidenceLevel.VERIFIED,
                "#108 Stage 3 (after DEV): in QA, as a separate least-privilege service account "
                "via impersonation, the #92 BigQueryWarehouse smoke passed (10/10: dry-run "
                "budget rejection, provider-enforced maximum_bytes_billed with 0 billed, bounded "
                "execute, typed @parameter binding, empty/null Arrow results; no "
                "datasets.create, other dataset refused) and the Step 15-16 models were "
                "deployed and MEASURED (95/95, synthetic-10k-works); every executed job's "
                "user_email was that account (identifiers redacted)",
                (
                    "Personal project labelled QA, not an enterprise-managed QA environment",
                    "MEASURED on synthetic-10k-works only in QA: production-scale cost/latency "
                    "and clustering effectiveness unverified; billed bytes are billing-floor "
                    "artifacts",
                    "One identity for deploy and runtime; separate deploy/runtime identities "
                    "unverified",
                    "Job timeout/cancellation under load unverified (the 1 s probe completed "
                    "before a cancel could trigger)",
                ),
                "No promotion is claimed; PROD stays BLOCKED",
                (EvidenceLabel.CONTRACT_DEFINED,),
            ),
        },
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
        domain_overrides={
            ReadinessDomain.GCS_RAW_LANDING: (
                EvidenceLevel.BLOCKED,
                "GCSObjectStore adapter implemented (#9); live smoke passed only in "
                "GCP_SANDBOX (#91); PROD has no access",
                ("No PROD access", "No DEV/QA live-bucket evidence to promote from"),
                "Do not run smoke in PROD; verify in DEV and QA first, then promote",
                (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
            ),
            ReadinessDomain.BIGQUERY_ANALYTICAL: (
                EvidenceLevel.BLOCKED,
                "BigQueryWarehouse adapter implemented (#10); live smoke passed only in "
                "GCP_SANDBOX (#92); PROD has no access",
                (
                    "No PROD access",
                    "No DEV/QA live-dataset evidence to promote from",
                    "No MERGE/partition/cluster MEASURED evidence",
                ),
                "Do not run smoke in PROD; verify in DEV and QA first, then promote",
                (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
            ),
        },
    )


def _cloud_placeholder_matrix(
    env: EnvironmentName,
    *,
    configuration_evidence: str,
    configuration_labels: tuple[EvidenceLabel, ...],
    configuration_gaps: tuple[str, ...],
    force_blocked_domains: frozenset[ReadinessDomain] | None = None,
    domain_overrides: dict[
        ReadinessDomain,
        tuple[EvidenceLevel, str, tuple[str, ...], str, tuple[EvidenceLabel, ...]],
    ]
    | None = None,
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
            "live smoke passed only in GCP_SANDBOX (#91) — not live-bucket VERIFIED here",
            (
                "No approved ADC/bucket execution evidence in this environment",
                "Immutable GCS write/precondition behavior unverified in this environment",
            ),
            "Run the approved #91-style smoke in this environment before any VERIFIED claim",
            (EvidenceLabel.CONTRACT_DEFINED, EvidenceLabel.CLOUD_UNVERIFIED),
        ),
        ReadinessDomain.BIGQUERY_ANALYTICAL: (
            EvidenceLevel.READY_TO_VALIDATE,
            "BigQueryWarehouse adapter implemented (#10) with dry-run + maximum_bytes_billed; "
            "live smoke passed only in GCP_SANDBOX (#92) — not live-job VERIFIED here",
            (
                "No approved ADC/dataset execution evidence in this environment",
                "No MERGE/partition/cluster MEASURED evidence",
                "No maximum_bytes_billed enforcement evidence in this environment",
            ),
            "Run the approved #92-style smoke in this environment before any VERIFIED claim",
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
            "DQ logic demonstrated locally (SEMANTIC_ONLY); the BigQuery DQ SQL ran once on "
            "synthetic data in GCP_SANDBOX only (#103); BigQuery DQ gating/alerting unverified",
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
    if domain_overrides:
        specs.update(domain_overrides)

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
