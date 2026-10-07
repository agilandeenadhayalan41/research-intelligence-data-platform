"""Storage-independent query-pattern and benchmark contracts (Step 14 / #19)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

_PATTERN_ID = r"^[a-z][a-z0-9-]{1,63}$"


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Placement(StrEnum):
    """Issue-approved placement values only."""

    BIGQUERY_ANALYTICAL = "BIGQUERY_ANALYTICAL"
    MATERIALIZED_AGGREGATE = "MATERIALIZED_AGGREGATE"
    BIGQUERY_PLUS_CACHE = "BIGQUERY_PLUS_CACHE"
    OPTIONAL_OPERATIONAL_STORE = "OPTIONAL_OPERATIONAL_STORE"
    UNRESOLVED = "UNRESOLVED"


class EvidenceStatus(StrEnum):
    """How strongly placement / scale claims are supported."""

    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"
    ARCHITECTURE = "ARCHITECTURE"


class BenchmarkEvidenceType(StrEnum):
    """Classification of one recorded benchmark observation."""

    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"
    FIXTURE_ONLY = "FIXTURE_ONLY"


class QueryCategory(StrEnum):
    """Required Step 14 workload categories."""

    DOI_LOOKUP = "DOI_LOOKUP"
    OPENALEX_ID_LOOKUP = "OPENALEX_ID_LOOKUP"
    ISSN_LOOKUP = "ISSN_LOOKUP"
    PUBLISHER_LOOKUP = "PUBLISHER_LOOKUP"
    AUTHORS_PER_JOURNAL = "AUTHORS_PER_JOURNAL"
    AUTHORS_PER_PUBLISHER = "AUTHORS_PER_PUBLISHER"
    PUBLISHER_TOPIC_COUNTS = "PUBLISHER_TOPIC_COUNTS"
    PUBLISHER_TOPIC_LICENSE_YEAR = "PUBLISHER_TOPIC_LICENSE_YEAR"
    INSTITUTION_TOPIC = "INSTITUTION_TOPIC"
    CITATION_RELATIONSHIPS = "CITATION_RELATIONSHIPS"
    PUBLICATION_TRENDS = "PUBLICATION_TRENDS"
    OPEN_ACCESS_TRENDS = "OPEN_ACCESS_TRENDS"


class Cardinality(StrEnum):
    ONE_TO_ONE = "1:1"
    ONE_TO_N = "1:N"
    N_TO_M = "N:M"


class RiskLevel(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActiveWorkFilter(StrEnum):
    """How consumer-facing patterns treat Work activity_state."""

    REQUIRED_ACTIVE = "REQUIRED_ACTIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REFERENCE_TARGET_MAY_BE_DELETED = "REFERENCE_TARGET_MAY_BE_DELETED"


class FrequencyHint(StrEnum):
    UNKNOWN = "UNKNOWN"
    RARE = "RARE"
    OCCASIONAL = "OCCASIONAL"
    FREQUENT = "FREQUENT"
    CONTINUOUS = "CONTINUOUS"


class ParameterSpec(SettingsModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=512)
    value_type: Literal["string", "integer", "date", "boolean"] = "string"
    required: bool = True


class ServiceLevelRequirement(SettingsModel):
    """Optional SLOs; unknown production SLAs stay UNKNOWN."""

    desired_p95_latency_ms: int | None = Field(default=None, strict=True, gt=0)
    max_freshness_lag: str | None = Field(default=None, max_length=64)
    expected_concurrency: int | None = Field(default=None, strict=True, gt=0)
    expected_frequency: FrequencyHint = FrequencyHint.UNKNOWN
    expected_result_size: str | None = Field(default=None, max_length=128)
    notes: str = Field(default="UNKNOWN — no Wiley production SLA invented", max_length=512)


class MaterializationCandidate(SettingsModel):
    candidate_id: str = Field(min_length=1, max_length=64, pattern=_PATTERN_ID)
    name: str = Field(min_length=1, max_length=128)
    input_grain: str = Field(min_length=1, max_length=256)
    output_grain: str = Field(min_length=1, max_length=256)
    refresh_dependency: str = Field(min_length=1, max_length=256)
    expected_reuse: str = Field(min_length=1, max_length=256)
    freshness_requirement: str = Field(min_length=1, max_length=128)
    notes: str = Field(default="", max_length=1024)


class RelationshipUse(SettingsModel):
    relationship: str = Field(min_length=1, max_length=64)
    cardinality: Cardinality
    join_expansion_risk: RiskLevel = RiskLevel.NONE
    notes: str = Field(default="", max_length=512)


class QueryPattern(SettingsModel):
    """One registered consumer workload definition."""

    pattern_id: str = Field(min_length=1, max_length=64, pattern=_PATTERN_ID)
    name: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=256)
    category: QueryCategory
    expected_result_grain: str = Field(min_length=1, max_length=512)
    relevant_entities: tuple[str, ...] = Field(min_length=1)
    relevant_relationships: tuple[RelationshipUse, ...] = ()
    parameters: tuple[ParameterSpec, ...] = ()
    filters: tuple[str, ...] = ()
    active_work_filter: ActiveWorkFilter
    freshness_requirement: str = Field(min_length=1, max_length=128)
    expected_frequency: FrequencyHint = FrequencyHint.UNKNOWN
    expected_concurrency: int | None = Field(default=None, strict=True, gt=0)
    expected_result_size: str = Field(min_length=1, max_length=128)
    known_scale: str = Field(default="UNKNOWN", max_length=256)
    assumed_scale: str = Field(default="UNKNOWN", max_length=256)
    aggregation_risk: RiskLevel = RiskLevel.NONE
    fanout_risk: RiskLevel = RiskLevel.NONE
    candidate_materialization: str | None = Field(default=None, max_length=128)
    placement: Placement
    placement_rationale: str = Field(min_length=1, max_length=1024)
    evidence_status: EvidenceStatus
    service_level: ServiceLevelRequirement = Field(
        default_factory=ServiceLevelRequirement
    )
    logical_query_shape: str = Field(min_length=1, max_length=4096)
    cache_key_hint: str | None = Field(default=None, max_length=256)
    notes: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_placement_honesty(self) -> Self:
        if (
            self.placement is Placement.OPTIONAL_OPERATIONAL_STORE
            and self.evidence_status is not EvidenceStatus.MEASURED
        ):
            raise ValueError(
                "OPTIONAL_OPERATIONAL_STORE requires MEASURED evidence; "
                "use UNRESOLVED or BIGQUERY_PLUS_CACHE until BigQuery+cache "
                "benchmarks exist"
            )
        if self.category in {
            QueryCategory.DOI_LOOKUP,
            QueryCategory.OPENALEX_ID_LOOKUP,
            QueryCategory.PUBLICATION_TRENDS,
            QueryCategory.OPEN_ACCESS_TRENDS,
            QueryCategory.CITATION_RELATIONSHIPS,
            QueryCategory.AUTHORS_PER_JOURNAL,
            QueryCategory.AUTHORS_PER_PUBLISHER,
            QueryCategory.PUBLISHER_TOPIC_COUNTS,
            QueryCategory.PUBLISHER_TOPIC_LICENSE_YEAR,
            QueryCategory.INSTITUTION_TOPIC,
        } and self.active_work_filter is ActiveWorkFilter.NOT_APPLICABLE:
            raise ValueError(
                f"{self.category} involves Works and must declare active filtering"
            )
        return self


class BenchmarkMeasurementSpec(SettingsModel):
    """What to measure for a pattern (not a recorded observation)."""

    pattern_id: str = Field(min_length=1, max_length=64, pattern=_PATTERN_ID)
    cold_latency_ms: bool = True
    warm_latency_ms: bool = True
    p50_latency_ms: bool = True
    p95_latency_ms: bool = True
    p99_latency_ms: bool = True
    concurrency: bool = True
    bytes_scanned: bool = True
    estimated_query_cost: bool = True
    result_row_count: bool = True
    result_bytes: bool = True
    freshness_lag: bool = True
    execution_frequency: bool = True
    cache_hit_rate: bool = False
    materialization_refresh_cost: bool = False
    notes: str = Field(default="", max_length=1024)


class BenchmarkResult(SettingsModel):
    """One recorded observation for a future BigQuery benchmark run."""

    result_id: UUID
    pattern_id: str = Field(min_length=1, max_length=64, pattern=_PATTERN_ID)
    engine: str = Field(min_length=1, max_length=64)
    dataset_scale: str = Field(min_length=1, max_length=128)
    started_at: AwareDatetime
    finished_at: AwareDatetime
    cold_or_warm: Literal["cold", "warm", "n/a"] = "n/a"
    latency_ms: float | None = Field(default=None, ge=0)
    bytes_scanned: int | None = Field(default=None, strict=True, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    result_rows: int | None = Field(default=None, strict=True, ge=0)
    result_bytes: int | None = Field(default=None, strict=True, ge=0)
    concurrency: int | None = Field(default=None, strict=True, gt=0)
    freshness_lag: str | None = Field(default=None, max_length=64)
    evidence_type: BenchmarkEvidenceType
    notes: str = Field(default="", max_length=2048)

    @field_validator("finished_at")
    @classmethod
    def finished_after_started(cls, value: datetime, info: ValidationInfo) -> datetime:
        started = info.data.get("started_at")
        if isinstance(started, datetime) and value < started:
            raise ValueError("finished_at must be >= started_at")
        return value

    @model_validator(mode="after")
    def local_engines_cannot_claim_measured_bigquery(self) -> Self:
        engine = self.engine.lower()
        local = engine in {"duckdb", "python", "fixture", "local"} or engine.startswith(
            "local-"
        )
        if local and self.evidence_type is BenchmarkEvidenceType.MEASURED:
            raise ValueError(
                "local/fixture engines cannot claim MEASURED BigQuery evidence; "
                "use FIXTURE_ONLY"
            )
        if "bigquery" not in engine and self.evidence_type is BenchmarkEvidenceType.MEASURED:
            raise ValueError(
                "MEASURED evidence requires a BigQuery (or declared production) engine"
            )
        return self


class QueryPatternRegistry(SettingsModel):
    """Validated collection of OpenAlex Research Intelligence query patterns."""

    version: str = Field(min_length=1, max_length=32)
    source: Literal["openalex"] = "openalex"
    decision_order: tuple[str, ...] = ()
    patterns: tuple[QueryPattern, ...] = Field(min_length=1)
    materialization_candidates: tuple[MaterializationCandidate, ...] = ()
    measurement_defaults: BenchmarkMeasurementSpec | None = None

    @model_validator(mode="after")
    def unique_pattern_ids(self) -> Self:
        ids = [pattern.pattern_id for pattern in self.patterns]
        if len(ids) != len(set(ids)):
            raise ValueError("pattern_id values must be unique")
        return self
