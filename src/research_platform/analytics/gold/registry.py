"""Gold mart registry — Step 16 contracts mapped to Step 14 patterns."""

from __future__ import annotations

from pathlib import Path

from research_platform.analytics.gold.contracts import (
    EvidenceStatus,
    FanoutStrategy,
    GoldColumn,
    GoldMartContract,
    MaterializationMode,
    RefreshStrategy,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_GOLD_SQL = "sql/bigquery/openalex/gold"


def _col(
    name: str,
    bq_type: str,
    *,
    nullable: bool = True,
    description: str = "",
) -> GoldColumn:
    return GoldColumn(name=name, bq_type=bq_type, nullable=nullable, description=description)  # type: ignore[arg-type]


def gold_mart_contracts() -> tuple[GoldMartContract, ...]:
    """Authoritative Gold inventory for OpenAlex consumer marts."""
    return (
        GoldMartContract(
            mart_id="research_discovery",
            name="Research discovery projection",
            sql_path=f"{_GOLD_SQL}/research_discovery.sql",
            source_pattern_ids=(
                "doi-work-lookup",
                "openalex-work-id-lookup",
            ),
            consumer_purpose=(
                "Consumer-friendly ACTIVE Work discovery projection with "
                "independently aggregated author/topic/institution arrays."
            ),
            result_grain="one row per ACTIVE work_id",
            grain_keys=("work_id",),
            input_tables=(
                "works",
                "work_authors",
                "work_topics",
                "work_author_institutions",
            ),
            output_columns=(
                _col("work_id", "STRING", nullable=False),
                _col("doi", "STRING"),
                _col("title", "STRING"),
                _col("publication_year", "INT64"),
                _col("publication_date", "DATE"),
                _col("work_type", "STRING"),
                _col("language", "STRING"),
                _col("is_oa", "BOOL"),
                _col("oa_status", "STRING"),
                _col("primary_source_id", "STRING"),
                _col("primary_publisher_id", "STRING"),
                _col("author_ids", "ARRAY<STRING>", nullable=False),
                _col("topic_ids", "ARRAY<STRING>", nullable=False),
                _col("institution_ids", "ARRAY<STRING>", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.INDEPENDENT_AGG_THEN_JOIN,
            null_handling=(
                "Missing DOI/title/year/OA/source/publisher remain NULL. "
                "Empty relationship arrays when no ACTIVE-scoped observations "
                "(COALESCE to ARRAY<STRING>[]; never NULL arrays). "
                "NULL identifiers are omitted from arrays. "
                "Do not fabricate identifiers or year 0."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_CHANGED_WORK_ROWS,
            refresh_keys=("work_id",),
            materialization_mode=MaterializationMode.COMPUTE_ON_READ,
            evidence_status=EvidenceStatus.ARCHITECTURE,
            materialization_rationale=(
                "Step 14 lookup patterns prefer BIGQUERY_PLUS_CACHE point access; "
                "no MEASURED reuse evidence for a physical discovery table."
            ),
            partition_candidate="publication_year",
            clustering_candidates=("work_id", "primary_publisher_id"),
            freshness_notes=(
                "Derive from current ACTIVE works + relationship observations; "
                "optional generated_at / source_max_lineage_source_updated_date "
                "as contract metadata only."
            ),
            cost_safety_notes=(
                "Prefer point lookup by work_id/doi. Full-scan discovery is not "
                "the intended access path; inherit works INTEGER_RANGE year partition."
            ),
        ),
        GoldMartContract(
            mart_id="journal_author_stats",
            name="Unique authors per journal/source",
            sql_path=f"{_GOLD_SQL}/journal_author_stats.sql",
            source_pattern_ids=("unique-authors-per-journal",),
            consumer_purpose="Source-level unique author and ACTIVE work counts.",
            result_grain="one row per source_id",
            grain_keys=("source_id",),
            input_tables=("works", "work_locations", "work_authors"),
            output_columns=(
                _col("source_id", "STRING", nullable=False),
                _col("unique_author_count", "INT64", nullable=False),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.DISTINCT_WORKS_THEN_JOIN,
            null_handling=(
                "Locations without source_id are excluded from the source grain. "
                "Author NULLs are ignored in unique_author_count."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_SOURCE_GROUP,
            refresh_keys=("source_id",),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Step 14 placement MATERIALIZED_AGGREGATE / candidate "
                "journal-author-counts. No MEASURED BigQuery workload yet."
            ),
            future_measurement_required=(
                "MEASURED scan bytes, latency, frequency, concurrency, reuse vs "
                "refresh cost for journal-author-counts workloads."
            ),
            partition_candidate=None,
            clustering_candidates=("source_id",),
            freshness_notes="Recompute affected source_id groups from current ACTIVE state.",
            cost_safety_notes=(
                "Output cardinality is source-level; do not partition only to have "
                "a partition. Pre-aggregate DISTINCT source/work before authors."
            ),
        ),
        GoldMartContract(
            mart_id="publisher_author_stats",
            name="Unique authors per publisher",
            sql_path=f"{_GOLD_SQL}/publisher_author_stats.sql",
            source_pattern_ids=("unique-authors-per-publisher",),
            consumer_purpose="Publisher-level unique author and ACTIVE work counts.",
            result_grain="one row per publisher_id",
            grain_keys=("publisher_id",),
            input_tables=("works", "work_authors"),
            output_columns=(
                _col("publisher_id", "STRING", nullable=False),
                _col("unique_author_count", "INT64", nullable=False),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.DISTINCT_WORKS_THEN_JOIN,
            null_handling=(
                "Works with NULL publisher_id are excluded from publisher grain. "
                "NULL author_id ignored in unique_author_count."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_PUBLISHER_GROUP,
            refresh_keys=("publisher_id",),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Step 14 placement MATERIALIZED_AGGREGATE / candidate "
                "publisher-author-counts. No MEASURED evidence yet."
            ),
            future_measurement_required=(
                "MEASURED BigQuery scan/latency/frequency/concurrency and refresh "
                "cost for publisher-author-counts."
            ),
            partition_candidate=None,
            clustering_candidates=("publisher_id",),
            freshness_notes=(
                "Publisher moves require repair of BOTH old and new publisher groups."
            ),
            cost_safety_notes="Small aggregate output; independent author dedupe before count.",
        ),
        GoldMartContract(
            mart_id="publisher_topic_year_stats",
            name="Publisher topic year active work counts",
            sql_path=f"{_GOLD_SQL}/publisher_topic_year_stats.sql",
            source_pattern_ids=("publisher-topic-counts",),
            consumer_purpose=(
                "ACTIVE work counts by publisher + topic + publication_year, "
                "sourced from the Step 14 publisher-topic-counts workload with "
                "an intentional year grain that may roll up to publisher+topic."
            ),
            result_grain="one row per publisher_id + topic_id + publication_year",
            grain_keys=("publisher_id", "topic_id", "publication_year"),
            input_tables=("works", "work_topics"),
            output_columns=(
                _col("publisher_id", "STRING", nullable=False),
                _col("topic_id", "STRING", nullable=False),
                _col("publication_year", "INT64"),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.WORKS_JOIN_TOPICS_ONLY,
            null_handling=(
                "NULL publication_year preserved as an explicit bucket. "
                "NULL publisher_id / topic_id rows excluded from this grain."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_PUBLISHER_YEAR,
            refresh_keys=("publisher_id", "publication_year"),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Sourced from Step 14 publisher-topic-counts "
                "(candidate_materialization: publisher-topic-counts). "
                "Step 14 explicitly notes a year-grained aggregate may "
                "intentionally roll up to publisher+topic; year grain also "
                "aligns with works INTEGER_RANGE partition. "
                "No MEASURED BigQuery evidence yet."
            ),
            future_measurement_required=(
                "MEASURED reuse vs refresh for year-grained publisher-topic "
                "workloads; confirm year-partition pruning benefit and "
                "intentional rollup to publisher+topic."
            ),
            partition_candidate="publication_year",
            clustering_candidates=("publisher_id", "topic_id"),
            freshness_notes=(
                "Dimension moves must rebuild old and new publisher/year buckets."
            ),
            cost_safety_notes=(
                "Do not join authors or locations. Predicate on publication_year "
                "to align with Step 15 partition pruning."
            ),
        ),
        GoldMartContract(
            mart_id="publisher_topic_license_year_stats",
            name="Publisher topic primary-license year stats",
            sql_path=f"{_GOLD_SQL}/publisher_topic_license_year_stats.sql",
            source_pattern_ids=("publisher-topic-license-year",),
            consumer_purpose=(
                "ACTIVE work counts by publisher + topic + primary location "
                "license + publication_year."
            ),
            result_grain=(
                "one row per publisher_id + topic_id + primary_location_license "
                "+ publication_year"
            ),
            grain_keys=(
                "publisher_id",
                "topic_id",
                "primary_location_license",
                "publication_year",
            ),
            input_tables=("works", "work_topics", "work_locations"),
            output_columns=(
                _col("publisher_id", "STRING", nullable=False),
                _col("topic_id", "STRING", nullable=False),
                _col("primary_location_license", "STRING"),
                _col("publication_year", "INT64"),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.PRIMARY_LOCATION_THEN_JOIN,
            null_handling=(
                "NULL primary license is an explicit UNKNOWN bucket — never "
                "MIN/MAX/ANY_VALUE(license). NULL year preserved."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_PUBLISHER_YEAR,
            refresh_keys=("publisher_id", "publication_year"),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Step 14 placement MATERIALIZED_AGGREGATE / candidate "
                "publisher-topic-license-year-counts. Pending MEASURED reuse."
            ),
            future_measurement_required=(
                "MEASURED scan/latency/frequency and refresh cost for "
                "publisher-topic-license-year workloads."
            ),
            partition_candidate="publication_year",
            clustering_candidates=("publisher_id", "topic_id"),
            freshness_notes="Primary-license changes rebuild impacted publisher/year grains.",
            cost_safety_notes=(
                "Filter work_locations.is_primary = TRUE before joining topics. "
                "Never select license via MIN/MAX/ANY_VALUE."
            ),
        ),
        GoldMartContract(
            mart_id="institution_topic_stats",
            name="Institution topic relationships",
            sql_path=f"{_GOLD_SQL}/institution_topic_stats.sql",
            source_pattern_ids=("institution-topic-relationships",),
            consumer_purpose=(
                "ACTIVE work counts by institution + topic after DISTINCT "
                "institution/work dedupe (Step 14 grain; no year)."
            ),
            result_grain="one row per institution_id + topic_id",
            grain_keys=("institution_id", "topic_id"),
            input_tables=("works", "work_author_institutions", "work_topics"),
            output_columns=(
                _col("institution_id", "STRING", nullable=False),
                _col("topic_id", "STRING", nullable=False),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.DISTINCT_WORKS_THEN_JOIN,
            null_handling=(
                "NULL institution_id / topic_id excluded. Multiple authorships "
                "from the same institution on one Work count once."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_INSTITUTION_GROUP,
            refresh_keys=("institution_id",),
            materialization_mode=MaterializationMode.COMPUTE_ON_READ,
            evidence_status=EvidenceStatus.ARCHITECTURE,
            materialization_rationale=(
                "Step 14 candidate_materialization is null; BIGQUERY_ANALYTICAL "
                "compute-on-read until MEASURED reuse appears."
            ),
            partition_candidate=None,
            clustering_candidates=("institution_id", "topic_id"),
            freshness_notes="Recompute affected institution_id groups from ACTIVE state.",
            cost_safety_notes=(
                "DISTINCT institution_id, work_id before joining work_topics. "
                "COUNT(DISTINCT) after Cartesian join is not a substitute."
            ),
        ),
        GoldMartContract(
            mart_id="publication_trends",
            name="Publication year trends",
            sql_path=f"{_GOLD_SQL}/publication_trends.sql",
            source_pattern_ids=("publication-trends",),
            consumer_purpose="ACTIVE work counts by publication_year.",
            result_grain="one row per publication_year",
            grain_keys=("publication_year",),
            input_tables=("works",),
            output_columns=(
                _col("publication_year", "INT64"),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.ACTIVE_WORKS_ONLY,
            null_handling=(
                "NULL publication_year is an explicit bucket. Do not assign year 0."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_YEAR_BUCKETS,
            refresh_keys=("publication_year",),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Step 14 candidate_materialization publication-trends; year grain "
                "aligns with INTEGER_RANGE partition. No MEASURED evidence yet."
            ),
            future_measurement_required=(
                "MEASURED scan bytes/latency/frequency for publication-trends "
                "and refresh cost of year buckets."
            ),
            partition_candidate="publication_year",
            clustering_candidates=(),
            freshness_notes="Year moves rebuild BOTH old and new year buckets.",
            cost_safety_notes="Predicate publication_year for partition pruning.",
        ),
        GoldMartContract(
            mart_id="open_access_trends",
            name="Open access trends by year and status",
            sql_path=f"{_GOLD_SQL}/open_access_trends.sql",
            source_pattern_ids=("open-access-trends",),
            consumer_purpose="ACTIVE work counts by publication_year + oa_status.",
            result_grain="one row per publication_year + oa_status",
            grain_keys=("publication_year", "oa_status"),
            input_tables=("works",),
            output_columns=(
                _col("publication_year", "INT64"),
                _col("oa_status", "STRING"),
                _col("active_work_count", "INT64", nullable=False),
            ),
            fanout_strategy=FanoutStrategy.ACTIVE_WORKS_ONLY,
            null_handling=(
                "NULL oa_status and NULL publication_year preserved. "
                "Do not fabricate 'closed' when OA status is unknown. "
                "is_oa is not used to invent oa_status."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_IMPACTED_YEAR_BUCKETS,
            refresh_keys=("publication_year",),
            materialization_mode=MaterializationMode.MATERIALIZATION_CANDIDATE,
            evidence_status=EvidenceStatus.ASSUMED,
            materialization_rationale=(
                "Step 14 candidate_materialization oa-trends. Pending MEASURED."
            ),
            future_measurement_required=(
                "MEASURED BigQuery workload for oa-trends; justify oa_status "
                "clustering only if measured pruning benefit exists."
            ),
            partition_candidate="publication_year",
            clustering_candidates=("oa_status",),
            freshness_notes="OA/year changes rebuild impacted year buckets.",
            cost_safety_notes=(
                "oa_status clustering is a candidate only; do not deploy without "
                "measurement. Partition candidate remains publication_year."
            ),
        ),
        GoldMartContract(
            mart_id="citation_edges",
            name="Citation relationship edges",
            sql_path=f"{_GOLD_SQL}/citation_edges.sql",
            source_pattern_ids=("citation-relationships",),
            consumer_purpose=(
                "Citation edges from ACTIVE source Works; targets may be "
                "ACTIVE, DELETED, or unresolved."
            ),
            result_grain="one row per (source_work_id, reference_index)",
            grain_keys=("source_work_id", "reference_index"),
            input_tables=("works", "work_references"),
            output_columns=(
                _col("source_work_id", "STRING", nullable=False),
                _col("reference_index", "INT64", nullable=False),
                _col("referenced_work_id", "STRING"),
                _col("reference_status", "STRING"),
                _col("target_activity_state", "STRING"),
            ),
            fanout_strategy=FanoutStrategy.ACTIVE_SOURCE_LEFT_JOIN_TARGET,
            null_handling=(
                "Unresolved targets remain (referenced_work_id NULL or absent "
                "target row). Never INNER JOIN away unresolved references. "
                "target_activity_state NULL when target Work is absent."
            ),
            refresh_strategy=RefreshStrategy.RECOMPUTE_SOURCE_WORK_EDGES,
            refresh_keys=("source_work_id",),
            materialization_mode=MaterializationMode.COMPUTE_ON_READ,
            evidence_status=EvidenceStatus.ARCHITECTURE,
            materialization_rationale=(
                "Step 14 candidate_materialization null; do not pre-aggregate "
                "the full citation network without MEASURED need."
            ),
            partition_candidate=None,
            clustering_candidates=("source_work_id",),
            freshness_notes=(
                "Recompute edges for changed ACTIVE source work_ids; tombstoned "
                "sources drop from Gold while canonical refs remain stored."
            ),
            cost_safety_notes=(
                "Default COMPUTE_ON_READ. Bound by source work_id. Do not build "
                "a huge pre-aggregated citation Gold without measured need."
            ),
        ),
    )


def load_gold_registry() -> tuple[GoldMartContract, ...]:
    marts = gold_mart_contracts()
    ids = [m.mart_id for m in marts]
    if len(ids) != len(set(ids)):
        raise ValueError("mart_id values must be unique")
    return marts


def gold_sql_path(mart: GoldMartContract, *, repo_root: Path | None = None) -> Path:
    root = repo_root or _REPO_ROOT
    return root / mart.sql_path


def serialize_gold_registry(marts: tuple[GoldMartContract, ...] | None = None) -> list[dict]:
    """Deterministic JSON-serializable registry snapshot."""
    items = marts if marts is not None else load_gold_registry()
    return [
        m.model_dump(mode="json")
        for m in sorted(items, key=lambda x: x.mart_id)
    ]
