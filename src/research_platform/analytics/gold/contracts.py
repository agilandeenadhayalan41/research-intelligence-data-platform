"""Typed Gold analytical mart contracts (Step 16 / #55).

Evidence-honest materialization: MATERIALIZED requires MEASURED evidence.
Current repository has FIXTURE_ONLY / SEMANTIC_ONLY / architecture evidence only,
so marts are COMPUTE_ON_READ or MATERIALIZATION_CANDIDATE — never falsely MATERIALIZED.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class MaterializationMode(StrEnum):
    COMPUTE_ON_READ = "COMPUTE_ON_READ"
    MATERIALIZATION_CANDIDATE = "MATERIALIZATION_CANDIDATE"
    MATERIALIZED = "MATERIALIZED"


class EvidenceStatus(StrEnum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"
    ARCHITECTURE = "ARCHITECTURE"
    FIXTURE_ONLY = "FIXTURE_ONLY"


class FanoutStrategy(StrEnum):
    INDEPENDENT_AGG_THEN_JOIN = "INDEPENDENT_AGG_THEN_JOIN"
    DISTINCT_WORKS_THEN_JOIN = "DISTINCT_WORKS_THEN_JOIN"
    WORKS_JOIN_TOPICS_ONLY = "WORKS_JOIN_TOPICS_ONLY"
    PRIMARY_LOCATION_THEN_JOIN = "PRIMARY_LOCATION_THEN_JOIN"
    ACTIVE_WORKS_ONLY = "ACTIVE_WORKS_ONLY"
    ACTIVE_SOURCE_LEFT_JOIN_TARGET = "ACTIVE_SOURCE_LEFT_JOIN_TARGET"


class RefreshStrategy(StrEnum):
    RECOMPUTE_CHANGED_WORK_ROWS = "RECOMPUTE_CHANGED_WORK_ROWS"
    RECOMPUTE_IMPACTED_YEAR_BUCKETS = "RECOMPUTE_IMPACTED_YEAR_BUCKETS"
    RECOMPUTE_IMPACTED_PUBLISHER_YEAR = "RECOMPUTE_IMPACTED_PUBLISHER_YEAR"
    RECOMPUTE_IMPACTED_SOURCE_GROUP = "RECOMPUTE_IMPACTED_SOURCE_GROUP"
    RECOMPUTE_IMPACTED_PUBLISHER_GROUP = "RECOMPUTE_IMPACTED_PUBLISHER_GROUP"
    RECOMPUTE_IMPACTED_INSTITUTION_GROUP = "RECOMPUTE_IMPACTED_INSTITUTION_GROUP"
    RECOMPUTE_SOURCE_WORK_EDGES = "RECOMPUTE_SOURCE_WORK_EDGES"


class GoldColumn(SettingsModel):
    name: str = Field(min_length=1, max_length=128)
    bq_type: Literal["STRING", "INT64", "FLOAT64", "BOOL", "DATE", "TIMESTAMP", "ARRAY<STRING>"]
    nullable: bool = True
    description: str = Field(default="", max_length=512)


class GoldRefreshImpact(SettingsModel):
    """Logical impact contract for Gold recompute (not orchestration)."""

    work_id: str | None = None
    old_publication_year: int | None = None
    new_publication_year: int | None = None
    old_publisher_id: str | None = None
    new_publisher_id: str | None = None
    old_source_ids: tuple[str, ...] = ()
    new_source_ids: tuple[str, ...] = ()
    old_topic_ids: tuple[str, ...] = ()
    new_topic_ids: tuple[str, ...] = ()
    old_institution_ids: tuple[str, ...] = ()
    new_institution_ids: tuple[str, ...] = ()
    deletion_transition: bool = False
    notes: str = Field(
        default=(
            "When old-state capture is unavailable, safe implementation is "
            "bounded partition/group recomputation — not incorrect delta counters."
        ),
        max_length=1024,
    )


class GoldMartContract(SettingsModel):
    """One consumer-oriented Gold mart contract."""

    mart_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    sql_path: str = Field(min_length=1, max_length=256)
    source_pattern_ids: tuple[str, ...] = Field(min_length=1)
    consumer_purpose: str = Field(min_length=1, max_length=512)
    result_grain: str = Field(min_length=1, max_length=256)
    grain_keys: tuple[str, ...] = Field(min_length=1)
    input_tables: tuple[str, ...] = Field(min_length=1)
    output_columns: tuple[GoldColumn, ...] = Field(min_length=1)
    active_work_required: bool = True
    fanout_strategy: FanoutStrategy
    null_handling: str = Field(min_length=1, max_length=1024)
    refresh_strategy: RefreshStrategy
    refresh_keys: tuple[str, ...] = ()
    materialization_mode: MaterializationMode
    evidence_status: EvidenceStatus
    materialization_rationale: str = Field(min_length=1, max_length=1024)
    future_measurement_required: str = Field(default="", max_length=1024)
    partition_candidate: str | None = None
    clustering_candidates: tuple[str, ...] = ()
    freshness_notes: str = Field(min_length=1, max_length=512)
    cost_safety_notes: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_materialization_honesty(self) -> Self:
        names = [c.name for c in self.output_columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.mart_id}: duplicate output columns")
        for key in self.grain_keys:
            if key not in names:
                raise ValueError(f"{self.mart_id}: grain key {key!r} missing from outputs")
        if self.materialization_mode is MaterializationMode.MATERIALIZED:
            if self.evidence_status is not EvidenceStatus.MEASURED:
                raise ValueError(
                    f"{self.mart_id}: MATERIALIZED requires evidence_status=MEASURED; "
                    "no deployed BigQuery measurements exist yet"
                )
        if self.materialization_mode is MaterializationMode.MATERIALIZATION_CANDIDATE:
            if not self.future_measurement_required.strip():
                raise ValueError(
                    f"{self.mart_id}: MATERIALIZATION_CANDIDATE requires "
                    "future_measurement_required"
                )
            if self.evidence_status is EvidenceStatus.MEASURED:
                raise ValueError(
                    f"{self.mart_id}: MEASURED evidence should promote to MATERIALIZED "
                    "via the promotion rule, not remain a candidate"
                )
        if self.active_work_required is False:
            raise ValueError(
                f"{self.mart_id}: consumer Gold marts must require ACTIVE Work filtering"
            )
        forbidden = {"issn", "eissn"}
        for col in self.output_columns:
            if col.name in forbidden:
                raise ValueError(f"{self.mart_id}: invented field {col.name}")
        return self


# Promotion rule (contract metadata; not a runtime scheduler).
MATERIALIZATION_PROMOTION_RULE = (
    "MATERIALIZATION_CANDIDATE → MATERIALIZED only when MEASURED BigQuery "
    "evidence shows repeated executions where scan bytes, latency, frequency, "
    "concurrency, result reuse, and/or cost are significant, AND materialization "
    "refresh cost/freshness remains acceptable. FIXTURE_ONLY / SEMANTIC_ONLY / "
    "architecture preference alone cannot promote."
)
