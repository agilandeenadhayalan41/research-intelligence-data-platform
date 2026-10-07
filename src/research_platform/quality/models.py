"""Typed data-quality contracts (Step 17 / #24).

Observes and reports only — never silently repairs data.
A check that cannot run must NEVER be reported as PASS.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QUALITY_CONTRACT_VERSION = "quality-contract-v1"


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class QualityCheckKind(StrEnum):
    HARD_GATE = "HARD_GATE"
    INFORMATIONAL_METRIC = "INFORMATIONAL_METRIC"


class QualityScope(StrEnum):
    CANONICAL = "CANONICAL"
    ANALYTICAL = "ANALYTICAL"
    STAGED_GOLD = "STAGED_GOLD"
    PUBLISHED_GOLD = "PUBLISHED_GOLD"
    RECONCILIATION = "RECONCILIATION"


class QualityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExecutionStage(StrEnum):
    PRE_SERVING_BUILD = "PRE_SERVING_BUILD"
    PRE_VISIBLE_PUBLICATION = "PRE_VISIBLE_PUBLICATION"


class BackendSupport(StrEnum):
    DUCKDB_SEMANTIC = "DUCKDB_SEMANTIC"
    BIGQUERY_SQL_CONTRACT = "BIGQUERY_SQL_CONTRACT"
    BOTH = "BOTH"


class ErrorClassification(StrEnum):
    QUERY_ERROR = "QUERY_ERROR"
    MISSING_COLUMN = "MISSING_COLUMN"
    MISSING_TABLE = "MISSING_TABLE"
    EXECUTOR_ERROR = "EXECUTOR_ERROR"
    MISSING_INPUT = "MISSING_INPUT"
    UNKNOWN = "UNKNOWN"


class ValidationLabel(StrEnum):
    SEMANTIC_ONLY = "SEMANTIC_ONLY"
    BIGQUERY_SQL_CONTRACT = "BIGQUERY_SQL_CONTRACT"


class QualityMetricPoint(SettingsModel):
    """Typed distribution / category point (deterministic serialization)."""

    dimension_value: str | None = None
    count: int = Field(ge=0)
    denominator: int | None = Field(default=None, ge=0)
    rate: float | None = None

    @model_validator(mode="after")
    def rate_null_when_zero_denominator(self) -> Self:
        if self.denominator == 0 and self.rate is not None:
            raise ValueError("rate must be NULL when denominator is 0")
        if self.rate is not None and (self.rate != self.rate or self.rate in (float("inf"), float("-inf"))):
            raise ValueError("rate must not be NaN or Infinity")
        return self


class ReconciliationExpectation(SettingsModel):
    """Pipeline-declared reconciliation counters (not invented by quality).

    Validate only mathematically declared relationships.
    Absent required inputs → ERROR/FAIL for required gates, never PASS.
    """

    source_records_seen: int | None = Field(default=None, ge=0)
    source_records_decoded: int | None = Field(default=None, ge=0)
    records_mapped_successfully: int | None = Field(default=None, ge=0)
    records_rejected: int | None = Field(default=None, ge=0)
    unique_work_ids_evaluated: int | None = Field(default=None, ge=0)
    inserted: int | None = Field(default=None, ge=0)
    updated: int | None = Field(default=None, ge=0)
    identical: int | None = Field(default=None, ge=0)
    stale: int | None = Field(default=None, ge=0)
    conflict: int | None = Field(default=None, ge=0)
    restore_required: int | None = Field(default=None, ge=0)


class QualityCheckContract(SettingsModel):
    """One registered quality check."""

    check_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]{1,127}$")
    kind: QualityCheckKind
    scope: QualityScope
    model_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    required: bool
    expected_rule: str = Field(min_length=1, max_length=512)
    input_tables: tuple[str, ...] = ()
    execution_stage: ExecutionStage
    backend_support: BackendSupport = BackendSupport.BOTH
    sql_path: str | None = None
    diagnostic_fields: tuple[str, ...] = ()
    threshold: int | None = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=1024)

    @model_validator(mode="after")
    def validate_gate_threshold(self) -> Self:
        if self.kind is QualityCheckKind.HARD_GATE and self.required:
            if self.threshold is None:
                raise ValueError(f"{self.check_id}: required HARD_GATE needs explicit threshold")
        if self.kind is QualityCheckKind.INFORMATIONAL_METRIC and self.required:
            raise ValueError(f"{self.check_id}: INFORMATIONAL_METRIC cannot be required")
        return self


class QualityResult(SettingsModel):
    """Stable quality result — aggregate diagnostics only (no raw payloads)."""

    check_id: str = Field(min_length=1, max_length=128)
    check_kind: QualityCheckKind
    scope: QualityScope
    model_name: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    contract_version: str = Field(default=QUALITY_CONTRACT_VERSION, min_length=1, max_length=64)
    status: QualityStatus
    observed_value: int | float | str | None = None
    expected_value: int | float | str | None = None
    denominator: int | None = Field(default=None, ge=0)
    unit: str = Field(default="count", max_length=64)
    message: str = Field(default="", max_length=1024)
    diagnostic_counts: dict[str, int] = Field(default_factory=dict)
    metric_points: tuple[QualityMetricPoint, ...] = ()
    error_classification: ErrorClassification | None = None
    executed_at: str | None = None

    @field_validator("diagnostic_counts")
    @classmethod
    def non_negative_diagnostics(cls, value: dict[str, int]) -> dict[str, int]:
        for key, count in value.items():
            if not isinstance(count, int) or count < 0:
                raise ValueError(f"diagnostic {key} must be non-negative int")
            lowered = key.lower()
            forbidden = ("doi", "title", "author", "payload", "sql", "password", "token", "secret")
            if any(part in lowered for part in forbidden) and "count" not in lowered and "missing" not in lowered:
                # Allow missing_doi_count etc.; block raw-looking keys.
                if lowered in {"doi", "title", "author_name", "payload", "sql", "password"}:
                    raise ValueError(f"unsafe diagnostic key: {key}")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def error_requires_classification(self) -> Self:
        if self.status is QualityStatus.ERROR and self.error_classification is None:
            raise ValueError(f"{self.check_id}: ERROR requires error_classification")
        return self


class QualityReport(SettingsModel):
    """Aggregated quality report with stage-local publication blocking rules.

    ``publication_allowed`` meaning depends on ``execution_stage``:
    - PRE_SERVING_BUILD: allowed to proceed to serving/Gold build
    - PRE_VISIBLE_PUBLICATION: allowed to make staged consumer outputs visible

    A PRE_SERVING_BUILD PASS must never be mistaken for final visible publication.
    """

    run_id: str = Field(min_length=1, max_length=128)
    contract_version: str = Field(default=QUALITY_CONTRACT_VERSION, min_length=1, max_length=64)
    execution_stage: ExecutionStage
    results: tuple[QualityResult, ...] = ()
    hard_gate_passed: bool
    publication_allowed: bool
    validation_label: ValidationLabel = ValidationLabel.SEMANTIC_ONLY
    notes: str = Field(default="", max_length=1024)

    @model_validator(mode="after")
    def consistent_flags(self) -> Self:
        if self.publication_allowed and not self.hard_gate_passed:
            raise ValueError("publication_allowed cannot be true when hard_gate_passed is false")
        return self


def compute_publication_allowed(
    *,
    required_check_ids: tuple[str, ...],
    results: tuple[QualityResult, ...],
) -> tuple[bool, bool]:
    """Return (hard_gate_passed, publication_allowed).

    For every required hard-gate ID:
    - result must exist
    - result.check_kind must be HARD_GATE (wrong kind blocks)
    - result.status must be PASS

    Duplicate result check_ids are rejected (no silent overwrite).
    Missing required results, FAIL, ERROR, or wrong kind → FALSE.
    """
    seen: dict[str, QualityResult] = {}
    for result in results:
        if result.check_id in seen:
            return False, False
        seen[result.check_id] = result

    for check_id in required_check_ids:
        result = seen.get(check_id)
        if result is None:
            return False, False
        if result.check_kind is not QualityCheckKind.HARD_GATE:
            return False, False
        if result.status is not QualityStatus.PASS:
            return False, False
    return True, True


def safe_error_message(exc: BaseException, *, classification: ErrorClassification) -> str:
    """Sanitize exception text — no connection strings / payloads."""
    text = f"{type(exc).__name__}: {classification.value}"
    raw = str(exc)
    lowered = raw.lower()
    if any(s in lowered for s in ("password", "token", "secret", "dsn=", "postgres://", "bigquery")):
        return text
    # Keep short, non-payload context only.
    snippet = raw.replace("\n", " ").strip()[:160]
    if snippet:
        return f"{text}: {snippet}"
    return text


def serialize_report(report: QualityReport) -> dict[str, Any]:
    """Deterministic JSON-serializable report snapshot."""
    return report.model_dump(mode="json")
