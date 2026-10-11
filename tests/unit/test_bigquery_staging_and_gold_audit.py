"""Offline tests for the #103 staging DDL, staged Gold audit build and SQL coverage.

No network, cloud or BigQuery calls. Generated data lives under ``tmp_path``.
DuckDB runs of the BigQuery files are SEMANTIC_ONLY: they map quoting and drop
the ``openalex.`` qualifier, so they prove logic, never BigQuery dialect or cost.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research_platform.analytics.bigquery.contracts import (
    BigQueryColumn,
    BigQueryType,
    build_table_contracts,
)
from research_platform.analytics.bigquery.registry import repo_path, sql_root
from research_platform.analytics.bigquery.sandbox.synthetic import (
    SyntheticCycleResult,
    write_synthetic_cycle,
)
from research_platform.analytics.bigquery.staging import (
    STAGING_SQL_DIR,
    STAGING_TARGET_TABLES,
    StagingCreateMode,
    StagingTableContract,
    bigquery_columns_from_parquet_schema,
    build_staging_table_contracts,
    check_parquet_load_compatibility,
    render_staging_ddl,
)
from research_platform.analytics.bigquery.validation import (
    SQL_TABLE_CONSUMER_DIRS,
    sql_table_coverage,
    validate_static_contracts,
)
from research_platform.analytics.gold.bigquery_audit import (
    GOLD_AUDIT_SQL_DIR,
    GOLD_EXCLUSION_QUALITY_SQL,
    STAGED_GOLD_AUDIT_TABLES,
    STAGED_GOLD_CITATION_EDGE_COLUMNS,
    GoldAuditError,
    gold_audit_sql_path,
    gold_select_statement,
    mart_ids_in_unnest_sql,
    render_gold_audit_sql,
    render_staged_gold_citation_edges_sql,
    render_staged_gold_work_contributions_sql,
)
from research_platform.analytics.gold.registry import load_gold_registry
from research_platform.analytics.gold.semantic_build import (
    build_all_gold_marts,
    build_staged_gold_audit_tables,
)
from research_platform.analytics.gold.validation import (
    _seed_gold_semantic_fixture,
    validate_static_gold_contracts,
)
from research_platform.quality.registry import GOLD_MART_IDS, load_quality_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The 5 tables repo SQL read before #103 with no DDL or creating SQL.
_PREVIOUSLY_MISSING = {
    "works_staging": ("models/merge_works.sql", "models/work_publication_decisions.sql"),
    "work_topics_staging": ("models/merge_work_topics.sql",),
    "staged_gold_mart_manifest": ("quality/active_gold_deleted_work_exclusion.sql",),
    "staged_gold_work_contributions": ("quality/active_gold_deleted_work_exclusion.sql",),
    "staged_gold_citation_edges": ("quality/active_gold_deleted_work_exclusion.sql",),
}

_GOLD_FIXTURE_TABLES = (
    "works",
    "work_authors",
    "work_topics",
    "work_locations",
    "work_author_institutions",
    "work_references",
)


@pytest.fixture(scope="module")
def cycle(tmp_path_factory: pytest.TempPathFactory) -> SyntheticCycleResult:
    return write_synthetic_cycle(tmp_path_factory.mktemp("cycle") / "out", scale="tiny")


def _contract(name: str) -> StagingTableContract:
    return next(c for c in build_staging_table_contracts() if c.table_name == name)


def _table_contract(name: str) -> Any:
    return next(c for c in build_table_contracts() if c.table_name == name)


def _load_check(destination: str, parquet: Path) -> Any:
    if destination in STAGING_TARGET_TABLES:
        columns = _contract(destination).columns
    else:
        columns = _table_contract(destination).columns
    return check_parquet_load_compatibility(destination, columns, pq.ParquetFile(parquet).schema)


def _duckdb_sql(text: str) -> str:
    """Repo BigQuery SQL for DuckDB (SEMANTIC_ONLY): bare table names, double quotes."""
    text = text.replace("`openalex.", "`").replace("`", '"')
    # DuckDB binds `UNNEST([...]) AS mart_id` to a struct; name the column instead.
    if "]) AS mart_id\n" in text:
        assert text.count("]) AS mart_id\n") == 1
        text = text.replace("]) AS mart_id\n", "]) AS mart_id_values(mart_id)\n")
    return text


def _repo_sql(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _build_audit_with_repo_sql(conn: Any) -> None:
    for table_name in STAGED_GOLD_AUDIT_TABLES:
        conn.execute(_duckdb_sql(_repo_sql(gold_audit_sql_path(table_name))))


def _rows(conn: Any, table: str) -> Counter[tuple[object, ...]]:
    return Counter(conn.execute(f"SELECT * FROM {table}").fetchall())


def _columns(conn: Any, table: str) -> list[str]:
    return [item[0] for item in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]


def _quality_counts(conn: Any) -> dict[str, int]:
    cursor = conn.execute(_duckdb_sql(_repo_sql(GOLD_EXCLUSION_QUALITY_SQL)))
    names = [item[0] for item in cursor.description]
    (row,) = cursor.fetchall()
    return dict(zip(names, row, strict=True))


def _copy_sql_tree(destination: Path) -> Path:
    root = destination / "sql/bigquery/openalex"
    shutil.copytree(sql_root(), root)
    return root


# --- staging DDL ------------------------------------------------------------------


def test_staging_ddl_render_matches_on_disk() -> None:
    contracts = build_staging_table_contracts()
    assert tuple(c.table_name for c in contracts) == ("works_staging", "work_topics_staging")
    for contract in contracts:
        assert contract.ddl_path == f"{STAGING_SQL_DIR}/{contract.table_name}.sql"
        on_disk = repo_path(contract.ddl_path).read_text(encoding="utf-8")
        assert render_staging_ddl(contract) == on_disk
        assert on_disk.isascii(), contract.ddl_path
    on_disk_names = sorted(path.name for path in (_REPO_ROOT / STAGING_SQL_DIR).iterdir())
    assert on_disk_names == ["work_topics_staging.sql", "works_staging.sql"]


def test_works_staging_is_create_like_works() -> None:
    contract = _contract("works_staging")
    assert contract.create_mode is StagingCreateMode.LIKE_TARGET
    assert contract.columns == _table_contract("works").columns
    statement = re.sub(r"--.*?$", "", _repo_sql(contract.ddl_path), flags=re.M).split()
    assert " ".join(statement) == (
        "CREATE TABLE IF NOT EXISTS `openalex.works_staging` LIKE `openalex.works` ;"
    )


def test_work_topics_staging_has_the_ten_work_topics_columns_all_nullable() -> None:
    contract = _contract("work_topics_staging")
    target = _table_contract("work_topics")
    assert contract.create_mode is StagingCreateMode.EXPLICIT_ALL_NULLABLE
    assert len(contract.columns) == 10
    assert [(c.name, c.bq_type) for c in contract.columns] == [
        (c.name, c.bq_type) for c in target.columns
    ]
    assert all(column.nullable for column in contract.columns)
    assert any(not column.nullable for column in target.columns)
    ddl = _repo_sql(contract.ddl_path)
    assert "NOT NULL" not in ddl
    assert "PARTITION BY" not in ddl and "CLUSTER BY" not in ddl
    for column in contract.columns:
        assert f"  `{column.name}` {column.bq_type.value}" in ddl


def test_staging_render_honours_dataset_and_rejects_required_explicit_columns() -> None:
    rendered = render_staging_ddl(_contract("works_staging"), dataset="scratch")
    assert "`scratch.works_staging` LIKE `scratch.works`" in rendered
    assert "openalex" not in rendered
    with pytest.raises(ValueError, match="must be NULLABLE"):
        StagingTableContract(
            table_name="work_topics_staging",
            target_table="work_topics",
            create_mode=StagingCreateMode.EXPLICIT_ALL_NULLABLE,
            columns=_table_contract("work_topics").columns,
            ddl_path=f"{STAGING_SQL_DIR}/work_topics_staging.sql",
        )


# --- Parquet loadability ----------------------------------------------------------


def test_batch2_staging_parquet_loads_into_the_staging_tables(cycle: SyntheticCycleResult) -> None:
    works = _load_check("works_staging", cycle.path_for("batch2/works_staging.parquet"))
    assert works.exact, works.errors

    topics = _load_check(
        "work_topics_staging", cycle.path_for("batch2/work_topics_staging.parquet")
    )
    assert topics.ok, topics.errors
    required = [c.name for c in _table_contract("work_topics").columns if not c.nullable]
    assert list(topics.relaxed_columns) == required

    injection = _load_check(
        "work_topics_staging",
        cycle.path_for("injection/work_topics_staging_null_topic_id.parquet"),
    )
    assert injection.exact, injection.errors
    # The same file could not go straight into work_topics: that is why staging is NULLABLE.
    direct = _load_check(
        "work_topics", cycle.path_for("injection/work_topics_staging_null_topic_id.parquet")
    )
    assert not direct.ok
    assert any("topic_id: NULLABLE Parquet column into REQUIRED" in e for e in direct.errors)


def test_batch1_parquet_loads_into_staging_and_canonical_tables(
    cycle: SyntheticCycleResult,
) -> None:
    assert _load_check("works_staging", cycle.path_for("batch1/works.parquet")).exact
    topics = _load_check("work_topics_staging", cycle.path_for("batch1/work_topics.parquet"))
    assert topics.ok and topics.relaxed_columns
    for contract in build_table_contracts():
        name = contract.table_name
        check = _load_check(name, cycle.path_for(f"batch1/{name}.parquet"))
        assert check.exact, check.errors


def test_parquet_columns_map_to_bigquery_names_types_and_modes(cycle: SyntheticCycleResult) -> None:
    schema = pq.ParquetFile(cycle.path_for("batch2/works_staging.parquet")).schema
    assert bigquery_columns_from_parquet_schema(schema) == _table_contract("works").columns


def _write(tmp_path: Path, name: str, table: pa.Table) -> pq.ParquetSchema:
    path = tmp_path / f"{name}.parquet"
    pq.write_table(table, path)
    return pq.ParquetFile(path).schema


def test_load_check_rejects_name_type_mode_and_timestamp_mismatches(tmp_path: Path) -> None:
    destination = (
        BigQueryColumn(name="work_id", bq_type=BigQueryType.STRING, nullable=False),
        BigQueryColumn(name="score", bq_type=BigQueryType.FLOAT64, nullable=True),
        BigQueryColumn(name="processed_at", bq_type=BigQueryType.TIMESTAMP, nullable=True),
    )
    utc = pa.timestamp("us", tz="UTC")
    fields = [("score", pa.float64()), ("processed_at", utc)]
    good = pa.table(
        {"work_id": ["W1"], "score": [0.5], "processed_at": [datetime(2026, 1, 1)]},
        schema=pa.schema([pa.field("work_id", pa.string(), nullable=False), *fields]),
    )
    assert check_parquet_load_compatibility("t", destination, _write(tmp_path, "good", good)).exact

    nullable_key = good.cast(pa.schema([("work_id", pa.string()), *fields]))
    check = check_parquet_load_compatibility("t", destination, _write(tmp_path, "nk", nullable_key))
    assert check.errors == ("t.work_id: NULLABLE Parquet column into REQUIRED table column",)

    wrong_type = good.set_column(1, pa.field("score", pa.string()), pa.array(["x"]))
    check = check_parquet_load_compatibility("t", destination, _write(tmp_path, "wt", wrong_type))
    assert check.errors == ("t.score: Parquet loads as STRING, table column is FLOAT64",)

    renamed = good.rename_columns(["work_id", "weight", "processed_at"])
    check = check_parquet_load_compatibility("t", destination, _write(tmp_path, "rn", renamed))
    assert not check.ok and "missing=['score'], extra=['weight']" in check.errors[0]

    naive = good.set_column(
        2, pa.field("processed_at", pa.timestamp("us")), pa.array([datetime(2026, 1, 1)])
    )
    check = check_parquet_load_compatibility("t", destination, _write(tmp_path, "nv", naive))
    assert not check.ok and "UTC-adjusted microseconds" in check.errors[0]

    nested = pa.table({"work_id": [["W1"]]})
    check = check_parquet_load_compatibility("t", destination, _write(tmp_path, "ns", nested))
    assert not check.ok and "nested or repeated" in check.errors[0]


# --- staged Gold audit build ------------------------------------------------------


def test_gold_audit_render_matches_on_disk() -> None:
    rendered = render_gold_audit_sql()
    assert tuple(rendered) == STAGED_GOLD_AUDIT_TABLES
    for table_name, text in rendered.items():
        relative = gold_audit_sql_path(table_name)
        assert relative == f"{GOLD_AUDIT_SQL_DIR}/{table_name}.sql"
        on_disk = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert on_disk == text, relative
        assert on_disk.isascii(), relative
        code = re.sub(r"--.*?$", "", on_disk, flags=re.M)
        assert code.count(";") == 1 and code.rstrip().endswith(";")
        assert f"CREATE OR REPLACE TABLE `openalex.{table_name}` AS" in code
    on_disk_names = sorted(path.stem for path in (_REPO_ROOT / GOLD_AUDIT_SQL_DIR).iterdir())
    assert on_disk_names == sorted(STAGED_GOLD_AUDIT_TABLES)
    with pytest.raises(GoldAuditError, match="unknown staged Gold audit table"):
        gold_audit_sql_path("gold_research_discovery")


def test_manifest_equals_registry_quality_unnest_and_quality_registry() -> None:
    registry_ids = tuple(mart.mart_id for mart in load_gold_registry())
    manifest_sql = _repo_sql(gold_audit_sql_path("staged_gold_mart_manifest"))
    manifest_ids = mart_ids_in_unnest_sql(manifest_sql)
    quality_ids = mart_ids_in_unnest_sql(_repo_sql(GOLD_EXCLUSION_QUALITY_SQL))
    assert len(registry_ids) == 9
    assert manifest_ids == registry_ids == quality_ids == GOLD_MART_IDS
    gate = next(
        c for c in load_quality_registry() if c.check_id == "gold.deleted_source_work_exclusion"
    )
    assert gate.sql_path == GOLD_EXCLUSION_QUALITY_SQL
    assert set(STAGED_GOLD_AUDIT_TABLES) == set(gate.input_tables) - {"works"}
    with pytest.raises(GoldAuditError, match="found 0"):
        mart_ids_in_unnest_sql("SELECT 1")


def test_every_gold_file_is_one_embeddable_select() -> None:
    for mart in load_gold_registry():
        body = gold_select_statement(mart)
        assert body.split()[0] in {"SELECT", "WITH"}, mart.mart_id
        assert not body.endswith(";") and ";" not in re.sub(r"--.*?$", "", body, flags=re.M)
    citation = next(m for m in load_gold_registry() if m.mart_id == "citation_edges")
    rendered = render_staged_gold_citation_edges_sql()
    for line in gold_select_statement(citation).splitlines():
        assert (f"  {line}" if line.strip() else "") in rendered.splitlines()
    for column in STAGED_GOLD_CITATION_EDGE_COLUMNS:
        assert f"  edges.`{column}`" in rendered


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("-- header\nSELECT 1\n", "final line holding only ';'"),
        ("SELECT 1;\nSELECT 2\n;\n", "exactly one statement"),
        ("CREATE TABLE x AS SELECT 1\n;\n", "SELECT or WITH"),
    ],
)
def test_gold_select_statement_rejects_unembeddable_files(
    tmp_path: Path, text: str, message: str
) -> None:
    mart = load_gold_registry()[0].model_copy(update={"sql_path": "gold/bad.sql"})
    (tmp_path / "gold").mkdir()
    (tmp_path / "gold/bad.sql").write_text(text, encoding="utf-8")
    with pytest.raises(GoldAuditError, match=re.escape(message)):
        gold_select_statement(mart, repo_root=tmp_path)


def test_render_refuses_marts_it_cannot_build() -> None:
    marts = load_gold_registry()
    unknown = (*marts, marts[0].model_copy(update={"mart_id": "new_mart"}))
    with pytest.raises(GoldAuditError, match=r"no contribution SQL for Gold marts \['new_mart'\]"):
        render_staged_gold_work_contributions_sql(unknown)
    without_citations = tuple(m for m in marts if m.mart_id != "citation_edges")
    with pytest.raises(GoldAuditError, match="no citation_edges mart"):
        render_gold_audit_sql(without_citations)
    with pytest.raises(GoldAuditError, match="unique mart_ids"):
        render_gold_audit_sql((*marts, marts[0]))


def test_gold_audit_build_matches_duckdb_semantic_build_on_gold_fixture() -> None:
    duckdb = pytest.importorskip("duckdb")
    reference = duckdb.connect(database=":memory:")
    ported = duckdb.connect(database=":memory:")
    try:
        _seed_gold_semantic_fixture(reference)
        build_all_gold_marts(reference)
        build_staged_gold_audit_tables(reference)
        _seed_gold_semantic_fixture(ported)
        _build_audit_with_repo_sql(ported)

        for table in STAGED_GOLD_AUDIT_TABLES:
            assert _columns(ported, table) == _columns(reference, table), table
            assert _rows(ported, table) == _rows(reference, table), table
        def values(sql: str) -> list[object]:
            return [row[0] for row in ported.execute(sql).fetchall()]

        manifest = values("SELECT mart_id FROM staged_gold_mart_manifest")
        assert sorted(manifest) == sorted(GOLD_MART_IDS)
        contributors = set(values("SELECT work_id FROM staged_gold_work_contributions"))
        assert "W2" not in contributors  # DELETED fixture work
        marts = set(values("SELECT mart_id FROM staged_gold_work_contributions"))
        assert marts == set(GOLD_MART_IDS)

        counts = _quality_counts(ported)
        assert counts == dict.fromkeys(counts, 0)
        assert len(counts) == 6

        # Not vacuous: canonical state moving past the staged build trips the gate.
        (stale,) = ported.execute(
            """
            SELECT (SELECT COUNT(*) FROM staged_gold_work_contributions WHERE work_id = 'W1')
                 + (SELECT COUNT(*) FROM staged_gold_citation_edges WHERE source_work_id = 'W1')
            """
        ).fetchone()
        ported.execute("UPDATE works SET activity_state = 'DELETED' WHERE work_id = 'W1'")
        assert _quality_counts(ported)["inactive_source_work_count"] == stale > 0
    finally:
        reference.close()
        ported.close()


def test_gold_audit_build_matches_duckdb_semantic_build_on_synthetic_batch1(
    cycle: SyntheticCycleResult,
) -> None:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(database=":memory:")
    try:
        for table in _GOLD_FIXTURE_TABLES:
            conn.execute(
                f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                [str(cycle.path_for(f"batch1/{table}.parquet"))],
            )
        build_all_gold_marts(conn)
        build_staged_gold_audit_tables(conn)
        reference = {table: _rows(conn, table) for table in STAGED_GOLD_AUDIT_TABLES}
        _build_audit_with_repo_sql(conn)
        for table in STAGED_GOLD_AUDIT_TABLES:
            assert _rows(conn, table) == reference[table], table
        per_mart = dict(
            conn.execute(
                "SELECT mart_id, COUNT(*) FROM staged_gold_work_contributions GROUP BY mart_id"
            ).fetchall()
        )
        assert set(per_mart) == set(GOLD_MART_IDS) and min(per_mart.values()) > 0
        (deleted,) = conn.execute(
            "SELECT COUNT(*) FROM works WHERE activity_state = 'DELETED'"
        ).fetchone()
        assert deleted > 0
        counts = _quality_counts(conn)
        assert counts == dict.fromkeys(counts, 0)
    finally:
        conn.close()


def test_gold_static_validation_detects_audit_drift(tmp_path: Path) -> None:
    root = _copy_sql_tree(tmp_path)
    assert validate_static_gold_contracts(repo_root=tmp_path).ok
    manifest = root / "gold_audit/staged_gold_mart_manifest.sql"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace("  'citation_edges'\n", "  'citation_edgez'\n"), encoding="utf-8"
    )
    quality = root / "quality/active_gold_deleted_work_exclusion.sql"
    quality.write_text(
        quality.read_text(encoding="utf-8").replace("    'open_access_trends',\n", ""),
        encoding="utf-8",
    )
    errors = validate_static_gold_contracts(repo_root=tmp_path).errors
    drift = "staged_gold_mart_manifest.sql differs from render_gold_audit_sql"
    assert any(drift in e for e in errors)
    assert any("staged_gold_mart_manifest.sql: mart_ids" in e for e in errors)
    assert any("active_gold_deleted_work_exclusion.sql: mart_ids" in e for e in errors)
    (root / "gold_audit/staged_gold_citation_edges.sql").unlink()
    errors = validate_static_gold_contracts(repo_root=tmp_path).errors
    assert any("missing staged Gold audit build" in e for e in errors)


# --- static table coverage --------------------------------------------------------


def test_every_table_read_by_repo_sql_has_exactly_one_producer() -> None:
    coverage = sql_table_coverage()
    assert coverage.unproduced == {}
    assert coverage.multiply_produced == {}
    assert coverage.unrecognized_references == ()
    for table, readers in _PREVIOUSLY_MISSING.items():
        assert set(readers) <= set(coverage.references[table]), table
    assert coverage.producers["works_staging"] == ("staging/works_staging.sql",)
    assert coverage.producers["work_topics_staging"] == ("staging/work_topics_staging.sql",)
    for table in STAGED_GOLD_AUDIT_TABLES:
        assert coverage.producers[table] == (f"gold_audit/{table}.sql",)
    for contract in build_table_contracts():
        assert coverage.producers[contract.table_name] == (f"ddl/{contract.table_name}.sql",)
    decision_tables = (
        "work_publication_decisions",
        "accepted_work_ids",
        "relationship_publish_work_ids",
    )
    for table in decision_tables:
        assert coverage.producers[table] == (f"models/{table}.sql",)
    assert coverage.producers["active_works"] == ("models/active_works.sql",)
    assert {"models", "quality", "gold"} <= set(SQL_TABLE_CONSUMER_DIRS)


def test_coverage_flags_the_five_tables_missing_before_103(tmp_path: Path) -> None:
    root = _copy_sql_tree(tmp_path)
    shutil.rmtree(root / "staging")
    shutil.rmtree(root / "gold_audit")
    assert sql_table_coverage(root).unproduced == _PREVIOUSLY_MISSING


def test_coverage_only_counts_producer_folders_and_flags_odd_references(tmp_path: Path) -> None:
    root = _copy_sql_tree(tmp_path)
    (root / "quality/makes_a_table.sql").write_text(
        "CREATE OR REPLACE TABLE `openalex.scratch_only` AS SELECT 1 AS x;\n"
        "SELECT x FROM `openalex.scratch_only`;\n",
        encoding="utf-8",
    )
    (root / "gold/unquoted.sql").write_text(
        "-- `openalex.in_a_comment` is ignored\nSELECT 1 FROM openalex.works\n;\n",
        encoding="utf-8",
    )
    (root / "staging/works_staging_copy.sql").write_text(
        "CREATE TABLE IF NOT EXISTS `openalex.works_staging` LIKE `openalex.works`\n;\n",
        encoding="utf-8",
    )
    coverage = sql_table_coverage(root)
    assert coverage.unproduced == {"scratch_only": ("quality/makes_a_table.sql",)}
    assert "in_a_comment" not in coverage.references
    assert coverage.unrecognized_references == ("gold/unquoted.sql",)
    assert coverage.multiply_produced == {
        "works_staging": ("staging/works_staging.sql", "staging/works_staging_copy.sql")
    }


def test_bigquery_static_validation_includes_staging_and_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = validate_static_contracts()
    assert report.ok, report.errors

    root = _copy_sql_tree(tmp_path)
    shutil.rmtree(root / "gold_audit")
    (root / "staging/work_topics_staging.sql").write_text("-- emptied\n", encoding="utf-8")
    monkeypatch.setattr(
        "research_platform.analytics.bigquery.validation.sql_root", lambda: root
    )
    monkeypatch.setattr(
        "research_platform.analytics.bigquery.validation.repo_path",
        lambda relative: tmp_path / relative,
    )
    errors = validate_static_contracts().errors
    assert any("work_topics_staging.sql differs from render_staging_ddl" in e for e in errors)
    flagged = sorted(
        match.group(1)
        for e in errors
        if (match := re.match(r"`openalex\.(\w+)` has no DDL or creating SQL", e))
    )
    assert flagged == [*sorted(STAGED_GOLD_AUDIT_TABLES), "work_topics_staging"]
