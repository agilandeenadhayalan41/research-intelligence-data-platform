"""Bounded end-to-end OpenAlex Works pipeline (Step 19 / #27).

Composes Steps 12–18 under one outer PipelineRun. Evidence label: SEMANTIC_ONLY.
Does not deploy BigQuery, Airflow/Composer, or HTTP services.
"""

from research_platform.e2e.bounds import parse_bounds
from research_platform.e2e.models import (
    PIPELINE_NAME,
    STAGE_ORDER,
    EndToEndBounds,
    EndToEndResult,
    EndToEndRunContext,
    FinalValidationReport,
    StageName,
    StageResult,
    StageStatus,
)
from research_platform.e2e.publication import (
    InMemoryPublicationStore,
    PublicationConflictError,
    PublicationSnapshot,
    PublicationStore,
)
from research_platform.e2e.runner import run_bounded_e2e_pipeline
from research_platform.e2e.summaries import safe_result_summary

__all__ = [
    "PIPELINE_NAME",
    "STAGE_ORDER",
    "EndToEndBounds",
    "EndToEndResult",
    "EndToEndRunContext",
    "FinalValidationReport",
    "InMemoryPublicationStore",
    "PublicationConflictError",
    "PublicationSnapshot",
    "PublicationStore",
    "StageName",
    "StageResult",
    "StageStatus",
    "parse_bounds",
    "run_bounded_e2e_pipeline",
    "safe_result_summary",
]
