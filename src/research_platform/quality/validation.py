"""Static registry checks and DuckDB SEMANTIC_ONLY fixtures (Step 17 / #24)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from research_platform.quality.models import (
    QUALITY_CONTRACT_VERSION,
    QualityCheckKind,
    ValidationLabel,
)
from research_platform.quality.registry import (
    load_quality_registry,
    quality_sql_path,
    serialize_quality_registry,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SELECT_STAR = re.compile(r"\bSELECT\s+\*\b", re.IGNORECASE)
_SQL_LINE_COMMENT = re.compile(r"--.*?$", re.MULTILINE)


def _strip_sql_comments(text: str) -> str:
    return _SQL_LINE_COMMENT.sub("", text)


@dataclass(frozen=True)
class StaticValidationReport:
    label: ValidationLabel
    errors: tuple[str, ...]
    checks_passed: int

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class SemanticValidationReport:
    label: ValidationLabel
    results: dict[str, Any]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_static_quality_contracts(
    *,
    repo_root: Path | None = None,
) -> StaticValidationReport:
    root = repo_root or _REPO_ROOT
    checks = load_quality_registry()
    errors: list[str] = []
    passed = 0

    def ok(condition: bool, message: str) -> None:
        nonlocal passed
        if condition:
            passed += 1
        else:
            errors.append(message)

    ids = [c.check_id for c in checks]
    ok(len(ids) == len(set(ids)), "duplicate check_id values")
    ok(QUALITY_CONTRACT_VERSION == "quality-contract-v1", "contract version drift")

    hard = [c for c in checks if c.kind is QualityCheckKind.HARD_GATE]
    info = [c for c in checks if c.kind is QualityCheckKind.INFORMATIONAL_METRIC]
    ok(len(hard) >= 7, f"expected >=7 hard gates, got {len(hard)}")
    ok(len(info) >= 7, f"expected >=7 informational metrics, got {len(info)}")
    ok(all(c.required for c in hard), "all hard gates must be required")
    ok(all(not c.required for c in info), "informational metrics must not be required")

    a = json.dumps(serialize_quality_registry(checks), sort_keys=True)
    b = json.dumps(serialize_quality_registry(checks), sort_keys=True)
    ok(a == b, "registry serialization not deterministic")

    for contract in checks:
        if contract.sql_path:
            path = quality_sql_path(contract, repo_root=root)
            ok(path is not None and path.is_file(), f"missing SQL: {contract.sql_path}")
            if path and path.is_file():
                text = path.read_text(encoding="utf-8")
                stripped = _strip_sql_comments(text)
                ok("`" in text, f"{contract.check_id}: expected BigQuery backticks")
                ok(
                    not _SELECT_STAR.search(stripped),
                    f"{contract.check_id}: SELECT * forbidden",
                )

    return StaticValidationReport(
        label=ValidationLabel.BIGQUERY_SQL_CONTRACT,
        errors=tuple(errors),
        checks_passed=passed,
    )


def seed_quality_semantic_fixture(
    conn: duckdb.DuckDBPyConnection,
    *,
    include_null_work_id: bool = False,
    include_duplicate_work_id: bool = False,
    include_source_orphan: bool = False,
    include_dimension_orphan: bool = False,
    include_deleted_in_gold: bool = False,
) -> None:
    """Tiny deterministic fixture for SEMANTIC_ONLY quality checks.

    CANONICAL may contain ACTIVE and DELETED Works; relationships for DELETED
    Works may remain (Policy A). GOLD must exclude DELETED source Works.
    Citation TARGET may be DELETED/absent; citation SOURCE must be ACTIVE.
    """
    conn.execute("DROP TABLE IF EXISTS works")
    conn.execute(
        """
        CREATE TABLE works AS SELECT * FROM (
          SELECT * FROM (VALUES
            ('W1', '10.1/a', 'Alpha', 2020, 'article', 'ACTIVE'),
            ('W2', '10.1/b', 'Beta', 2021, 'article', 'DELETED'),
            ('W3', CAST(NULL AS VARCHAR), 'Gamma', CAST(NULL AS INTEGER),
             CAST(NULL AS VARCHAR), 'ACTIVE'),
            ('W4', '10.1/d', CAST(NULL AS VARCHAR), 2019, 'book', 'ACTIVE'),
            ('W5', '10.1/e', '', 2022, 'article', 'ACTIVE')
          ) AS t(work_id, doi, title, publication_year, work_type, activity_state)
        )
        """
    )
    if include_null_work_id:
        conn.execute(
            """
            INSERT INTO works VALUES
              (CAST(NULL AS VARCHAR), '10.1/x', 'NullId', 2020, 'article', 'ACTIVE')
            """
        )
    if include_duplicate_work_id:
        conn.execute(
            """
            INSERT INTO works VALUES
              ('W1', '10.1/dup', 'Dup', 2020, 'article', 'ACTIVE')
            """
        )

    conn.execute("DROP TABLE IF EXISTS authors")
    conn.execute(
        """
        CREATE TABLE authors AS SELECT * FROM (VALUES
          ('A1'), ('A2')
        ) AS t(author_id)
        """
    )
    conn.execute("DROP TABLE IF EXISTS institutions")
    conn.execute(
        "CREATE TABLE institutions AS SELECT * FROM (VALUES ('I1')) AS t(institution_id)"
    )
    conn.execute("DROP TABLE IF EXISTS topics")
    conn.execute(
        "CREATE TABLE topics AS SELECT * FROM (VALUES ('T1'), ('T2')) AS t(topic_id)"
    )
    conn.execute("DROP TABLE IF EXISTS sources")
    conn.execute(
        "CREATE TABLE sources AS SELECT * FROM (VALUES ('S1')) AS t(source_id)"
    )
    conn.execute("DROP TABLE IF EXISTS funders")
    conn.execute(
        "CREATE TABLE funders AS SELECT * FROM (VALUES ('F1')) AS t(funder_id)"
    )

    conn.execute("DROP TABLE IF EXISTS work_authors")
    conn.execute(
        """
        CREATE TABLE work_authors AS SELECT * FROM (VALUES
          ('W1', 0, 'A1'),
          ('W1', 1, 'A2'),
          ('W2', 0, 'A1'),
          ('W4', 0, 'A1')
        ) AS t(work_id, authorship_index, author_id)
        """
    )
    if include_dimension_orphan:
        conn.execute("INSERT INTO work_authors VALUES ('W5', 0, 'A999')")
    if include_source_orphan:
        conn.execute("INSERT INTO work_authors VALUES ('W999', 0, 'A1')")

    conn.execute("DROP TABLE IF EXISTS work_author_institutions")
    conn.execute(
        """
        CREATE TABLE work_author_institutions AS SELECT * FROM (VALUES
          ('W1', 0, 0, 'I1'),
          ('W2', 0, 0, 'I1')
        ) AS t(work_id, authorship_index, institution_index, institution_id)
        """
    )
    conn.execute("DROP TABLE IF EXISTS work_topics")
    conn.execute(
        """
        CREATE TABLE work_topics AS SELECT * FROM (VALUES
          ('W1', 'T1'),
          ('W2', 'T1'),
          ('W4', 'T2')
        ) AS t(work_id, topic_id)
        """
    )
    conn.execute("DROP TABLE IF EXISTS work_keywords")
    conn.execute(
        """
        CREATE TABLE work_keywords AS SELECT * FROM (VALUES
          ('W1', 'K1')
        ) AS t(work_id, keyword_id)
        """
    )
    conn.execute("DROP TABLE IF EXISTS work_mesh")
    conn.execute(
        """
        CREATE TABLE work_mesh AS SELECT * FROM (VALUES
          ('W1', 0, 'D000')
        ) AS t(work_id, mesh_index, descriptor_ui)
        """
    )
    conn.execute("DROP TABLE IF EXISTS work_locations")
    conn.execute(
        """
        CREATE TABLE work_locations AS SELECT * FROM (VALUES
          ('W1', 0, 'S1', TRUE),
          ('W4', 0, 'S1', TRUE)
        ) AS t(work_id, location_index, source_id, is_primary)
        """
    )
    conn.execute("DROP TABLE IF EXISTS work_grants")
    conn.execute(
        """
        CREATE TABLE work_grants AS SELECT * FROM (VALUES
          ('W1', 0, 'F1')
        ) AS t(work_id, grant_index, funder_id)
        """
    )
    # Citations: ACTIVE→DELETED, ACTIVE→ABSENT, MISSING, MALFORMED, DELETED source
    conn.execute("DROP TABLE IF EXISTS work_references")
    conn.execute(
        """
        CREATE TABLE work_references AS SELECT * FROM (VALUES
          ('W1', 0, 'W2', 'RESOLVED_ID'),
          ('W1', 1, 'W999', 'RESOLVED_ID'),
          ('W1', 2, CAST(NULL AS VARCHAR), 'MISSING'),
          ('W1', 3, 'not-an-id', 'MALFORMED'),
          ('W4', 0, 'W1', 'RESOLVED_ID'),
          ('W2', 0, 'W1', 'RESOLVED_ID')
        ) AS t(work_id, reference_index, referenced_work_id, reference_status)
        """
    )

    # Staged Gold contributions (work grain for exclusion checks)
    conn.execute("DROP TABLE IF EXISTS staged_gold_work_contributions")
    conn.execute(
        """
        CREATE TABLE staged_gold_work_contributions AS SELECT * FROM (VALUES
          ('research_discovery', 'W1'),
          ('research_discovery', 'W3'),
          ('research_discovery', 'W4'),
          ('research_discovery', 'W5'),
          ('journal_author_stats', 'W1'),
          ('publisher_author_stats', 'W1'),
          ('publisher_topic_year_stats', 'W1'),
          ('publisher_topic_license_year_stats', 'W1'),
          ('institution_topic_stats', 'W1'),
          ('publication_trends', 'W1'),
          ('open_access_trends', 'W1'),
          ('citation_edges', 'W1'),
          ('citation_edges', 'W4')
        ) AS t(mart_id, work_id)
        """
    )
    if include_deleted_in_gold:
        conn.execute(
            """
            INSERT INTO staged_gold_work_contributions VALUES
              ('research_discovery', 'W2')
            """
        )

    conn.execute("DROP TABLE IF EXISTS staged_gold_citation_edges")
    conn.execute(
        """
        CREATE TABLE staged_gold_citation_edges AS SELECT * FROM (VALUES
          ('W1', 0, 'W2', 'DELETED'),
          ('W1', 1, 'W999', CAST(NULL AS VARCHAR)),
          ('W4', 0, 'W1', 'ACTIVE')
        ) AS t(source_work_id, reference_index, referenced_work_id, target_activity_state)
        """
    )
    if include_deleted_in_gold:
        conn.execute(
            """
            INSERT INTO staged_gold_citation_edges VALUES
              ('W2', 0, 'W1', 'ACTIVE')
            """
        )
