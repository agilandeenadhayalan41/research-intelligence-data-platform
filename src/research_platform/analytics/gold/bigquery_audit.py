"""BigQuery build for the staged Gold audit tables (#103).

``quality/active_gold_deleted_work_exclusion.sql`` (HARD_GATE
``gold.deleted_source_work_exclusion``) reads three tables that only the DuckDB
``semantic_build.build_staged_gold_audit_tables`` creates:

- ``staged_gold_mart_manifest(mart_id)``: one row per ``load_gold_registry()``
  mart, in registry order;
- ``staged_gold_work_contributions(mart_id, work_id)``: only the ACTIVE works
  that actually feed each mart, with the same per-mart filters as the DuckDB
  builder;
- ``staged_gold_citation_edges``: the ``gold/citation_edges.sql`` SELECT,
  embedded verbatim, projected to the four columns the DuckDB builder keeps.

This module renders those three ``CREATE OR REPLACE TABLE ... AS`` statements
into ``sql/bigquery/openalex/gold_audit/`` (byte-identical files, like the DDL
renderer), so the manifest can never drift from the registry and the citation
edges never drift from the Gold mart SQL.

Every contribution branch filters ACTIVE works, so the HARD_GATE over these
tables is close to tautological; independent exclusion checks against the Gold
outputs are the evidence. Nothing here executes SQL or touches GCP/BigQuery.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from research_platform.analytics.gold.contracts import GoldMartContract
from research_platform.analytics.gold.registry import gold_sql_path, load_gold_registry

__all__ = [
    "GOLD_AUDIT_DATASET",
    "GOLD_AUDIT_SQL_DIR",
    "GOLD_EXCLUSION_QUALITY_SQL",
    "STAGED_GOLD_AUDIT_TABLES",
    "STAGED_GOLD_CITATION_EDGE_COLUMNS",
    "GoldAuditError",
    "gold_audit_sql_path",
    "gold_select_statement",
    "mart_ids_in_unnest_sql",
    "render_gold_audit_sql",
    "render_staged_gold_citation_edges_sql",
    "render_staged_gold_mart_manifest_sql",
    "render_staged_gold_work_contributions_sql",
]

# The literal dataset every repo SQL file already qualifies tables with.
GOLD_AUDIT_DATASET: Final = "openalex"
GOLD_AUDIT_SQL_DIR: Final = "sql/bigquery/openalex/gold_audit"
GOLD_EXCLUSION_QUALITY_SQL: Final = (
    "sql/bigquery/openalex/quality/active_gold_deleted_work_exclusion.sql"
)

# Build order; the statements are independent, the quality gate needs all three.
STAGED_GOLD_AUDIT_TABLES: Final = (
    "staged_gold_mart_manifest",
    "staged_gold_work_contributions",
    "staged_gold_citation_edges",
)

# Columns semantic_build keeps from gold_citation_edges (reference_status dropped).
STAGED_GOLD_CITATION_EDGE_COLUMNS: Final = (
    "source_work_id",
    "reference_index",
    "referenced_work_id",
    "target_activity_state",
)

_SQL_LINE_COMMENT = re.compile(r"--.*?$", re.MULTILINE)
_UNNEST_MART_IDS = re.compile(
    r"UNNEST\s*\(\s*\[(?P<items>[^\]]*)\]\s*\)\s+AS\s+`?mart_id`?", re.IGNORECASE
)
_QUOTED_ID = re.compile(r"'([a-z][a-z0-9_]*)'")

_ACTIVE_WORKS = """\
SELECT
  '{mart_id}' AS `mart_id`,
  w.`work_id`
FROM `{dataset}.works` AS w
WHERE w.`activity_state` = 'ACTIVE'"""

_PUBLISHER_TOPIC_WORKS = """\
SELECT DISTINCT
  '{mart_id}' AS `mart_id`,
  w.`work_id`
FROM `{dataset}.works` AS w
INNER JOIN `{dataset}.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL"""

# mart_id -> (comment, contributing-works SELECT). Mirrors semantic_build.
_CONTRIBUTIONS: Final[dict[str, tuple[str, str]]] = {
    "research_discovery": ("every ACTIVE work", _ACTIVE_WORKS),
    "journal_author_stats": (
        "ACTIVE works with a non-NULL location source_id",
        """\
SELECT DISTINCT
  '{mart_id}' AS `mart_id`,
  wl.`work_id`
FROM `{dataset}.work_locations` AS wl
INNER JOIN `{dataset}.works` AS w
  ON w.`work_id` = wl.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND wl.`source_id` IS NOT NULL""",
    ),
    "publisher_author_stats": (
        "ACTIVE works with a primary publisher",
        """\
SELECT
  '{mart_id}' AS `mart_id`,
  w.`work_id`
FROM `{dataset}.works` AS w
WHERE w.`activity_state` = 'ACTIVE'
  AND w.`primary_publisher_id` IS NOT NULL""",
    ),
    "publisher_topic_year_stats": (
        "ACTIVE works with a primary publisher and a non-NULL topic",
        _PUBLISHER_TOPIC_WORKS,
    ),
    "publisher_topic_license_year_stats": (
        "ACTIVE works with a primary publisher and a non-NULL topic",
        _PUBLISHER_TOPIC_WORKS,
    ),
    "institution_topic_stats": (
        "ACTIVE works with a non-NULL institution and a non-NULL topic",
        """\
SELECT DISTINCT
  '{mart_id}' AS `mart_id`,
  w.`work_id`
FROM `{dataset}.works` AS w
INNER JOIN `{dataset}.work_author_institutions` AS wai
  ON wai.`work_id` = w.`work_id`
INNER JOIN `{dataset}.work_topics` AS wt
  ON wt.`work_id` = w.`work_id`
WHERE w.`activity_state` = 'ACTIVE'
  AND wai.`institution_id` IS NOT NULL
  AND wt.`topic_id` IS NOT NULL""",
    ),
    "publication_trends": ("every ACTIVE work", _ACTIVE_WORKS),
    "open_access_trends": ("every ACTIVE work", _ACTIVE_WORKS),
    "citation_edges": (
        "ACTIVE source works that have reference rows",
        """\
SELECT DISTINCT
  '{mart_id}' AS `mart_id`,
  wr.`work_id`
FROM `{dataset}.work_references` AS wr
INNER JOIN `{dataset}.works` AS w
  ON w.`work_id` = wr.`work_id`
WHERE w.`activity_state` = 'ACTIVE'""",
    ),
}

_GRAINS: Final[dict[str, str]] = {
    "staged_gold_mart_manifest": "one row per load_gold_registry() mart_id",
    "staged_gold_work_contributions": (
        "one row per (mart_id, contributing ACTIVE work_id); only works that feed each mart"
    ),
    "staged_gold_citation_edges": (
        "one row per (source_work_id, reference_index) from gold/citation_edges.sql"
    ),
}


class GoldAuditError(ValueError):
    """The Gold registry or a Gold SQL file cannot be rendered into the audit build."""


def gold_audit_sql_path(table_name: str) -> str:
    """Repo-relative path of the rendered build for one staged audit table."""
    if table_name not in STAGED_GOLD_AUDIT_TABLES:
        raise GoldAuditError(f"unknown staged Gold audit table {table_name!r}")
    return f"{GOLD_AUDIT_SQL_DIR}/{table_name}.sql"


def _strip_sql_comments(text: str) -> str:
    return _SQL_LINE_COMMENT.sub("", text)


def gold_select_statement(mart: GoldMartContract, *, repo_root: Path | None = None) -> str:
    """The single SELECT of a Gold mart file, without its header comments or ``;`` line.

    Gold files are bare ``SELECT``/``WITH`` statements that end with a line
    holding only ``;``. Anything else (several statements, DDL, a missing final
    ``;`` line) is rejected, because the text is embedded in other statements.
    """
    lines = gold_sql_path(mart, repo_root=repo_root).read_text(encoding="utf-8").splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[-1].strip() != ";":
        raise GoldAuditError(f"{mart.sql_path}: expected a final line holding only ';'")
    body = "\n".join(lines[:-1]).rstrip()
    code = _strip_sql_comments(body)
    if ";" in code:
        raise GoldAuditError(f"{mart.sql_path}: expected exactly one statement")
    words = code.split()
    if not words or words[0].upper() not in {"SELECT", "WITH"}:
        raise GoldAuditError(f"{mart.sql_path}: expected a SELECT or WITH statement")
    return body


def mart_ids_in_unnest_sql(text: str) -> tuple[str, ...]:
    """The mart_ids of the single ``UNNEST([...]) AS mart_id`` list in ``text``."""
    matches = list(_UNNEST_MART_IDS.finditer(_strip_sql_comments(text)))
    if len(matches) != 1:
        raise GoldAuditError(f"expected one UNNEST([...]) AS mart_id list, found {len(matches)}")
    return tuple(_QUOTED_ID.findall(matches[0].group("items")))


def _marts(marts: Sequence[GoldMartContract] | None) -> tuple[GoldMartContract, ...]:
    return tuple(marts) if marts is not None else load_gold_registry()


def _header(table_name: str, *extra: str) -> list[str]:
    return [
        f"-- BigQuery staged Gold audit build: `{GOLD_AUDIT_DATASET}.{table_name}`",
        "-- #103 port of analytics/gold/semantic_build.build_staged_gold_audit_tables.",
        "-- DEFINED, NOT YET DEPLOYED / NOT EXECUTED here.",
        "-- Rendered by research_platform.analytics.gold.bigquery_audit; edit the",
        "-- renderer, not this file.",
        "-- Read by quality/active_gold_deleted_work_exclusion.sql",
        "-- (HARD_GATE gold.deleted_source_work_exclusion).",
        f"-- Grain: {_GRAINS[table_name]}",
        *(f"-- {line}" for line in extra),
        "",
    ]


def render_staged_gold_mart_manifest_sql(
    marts: Sequence[GoldMartContract] | None = None,
) -> str:
    """CTAS for ``staged_gold_mart_manifest``: exactly the registry mart_ids."""
    mart_ids = [mart.mart_id for mart in _marts(marts)]
    if not mart_ids or len(mart_ids) != len(set(mart_ids)):
        raise GoldAuditError("Gold registry must list unique mart_ids")
    lines = _header(
        "staged_gold_mart_manifest",
        "The list must equal the expected_marts UNNEST in the quality file.",
    )
    lines.append(
        f"CREATE OR REPLACE TABLE `{GOLD_AUDIT_DATASET}.staged_gold_mart_manifest` AS"
    )
    lines.append("SELECT")
    lines.append("  mart_id")
    lines.append("FROM UNNEST([")
    lines.append(",\n".join(f"  '{mart_id}'" for mart_id in mart_ids))
    lines.append("]) AS mart_id")
    lines.append(";")
    lines.append("")
    return "\n".join(lines)


def render_staged_gold_work_contributions_sql(
    marts: Sequence[GoldMartContract] | None = None,
) -> str:
    """CTAS for ``staged_gold_work_contributions``: one UNION ALL branch per mart."""
    mart_ids = [mart.mart_id for mart in _marts(marts)]
    missing = [mart_id for mart_id in mart_ids if mart_id not in _CONTRIBUTIONS]
    if missing:
        raise GoldAuditError(f"no contribution SQL for Gold marts {missing}")
    lines = _header(
        "staged_gold_work_contributions",
        "Every branch filters ACTIVE works, so the HARD_GATE over this table is",
        "close to tautological; check the Gold outputs independently.",
    )
    lines.append(
        f"CREATE OR REPLACE TABLE `{GOLD_AUDIT_DATASET}.staged_gold_work_contributions` AS"
    )
    branches: list[str] = []
    for mart_id in mart_ids:
        comment, template = _CONTRIBUTIONS[mart_id]
        select = template.format(mart_id=mart_id, dataset=GOLD_AUDIT_DATASET)
        branches.append(f"-- {mart_id}: {comment}\n{select}")
    lines.append("\nUNION ALL\n".join(branches))
    lines.append(";")
    lines.append("")
    return "\n".join(lines)


def render_staged_gold_citation_edges_sql(
    marts: Sequence[GoldMartContract] | None = None,
    *,
    repo_root: Path | None = None,
) -> str:
    """CTAS for ``staged_gold_citation_edges`` around the citation_edges Gold SELECT."""
    mart = next((item for item in _marts(marts) if item.mart_id == "citation_edges"), None)
    if mart is None:
        raise GoldAuditError("Gold registry has no citation_edges mart")
    body = gold_select_statement(mart, repo_root=repo_root)
    lines = _header(
        "staged_gold_citation_edges",
        f"Inner SELECT copied verbatim from {mart.sql_path}.",
    )
    lines.append(
        f"CREATE OR REPLACE TABLE `{GOLD_AUDIT_DATASET}.staged_gold_citation_edges` AS"
    )
    lines.append("SELECT")
    lines.append(
        ",\n".join(f"  edges.`{column}`" for column in STAGED_GOLD_CITATION_EDGE_COLUMNS)
    )
    lines.append("FROM (")
    lines.append("\n".join(f"  {line}" if line.strip() else "" for line in body.splitlines()))
    lines.append(") AS edges")
    lines.append(";")
    lines.append("")
    return "\n".join(lines)


def render_gold_audit_sql(
    marts: Sequence[GoldMartContract] | None = None,
    *,
    repo_root: Path | None = None,
) -> dict[str, str]:
    """All three audit builds keyed by table name, in ``STAGED_GOLD_AUDIT_TABLES`` order."""
    items = _marts(marts)
    return {
        "staged_gold_mart_manifest": render_staged_gold_mart_manifest_sql(items),
        "staged_gold_work_contributions": render_staged_gold_work_contributions_sql(items),
        "staged_gold_citation_edges": render_staged_gold_citation_edges_sql(
            items, repo_root=repo_root
        ),
    }
