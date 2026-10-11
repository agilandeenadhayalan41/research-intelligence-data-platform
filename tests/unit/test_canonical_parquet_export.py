"""Offline tests for the canonical → Arrow → Parquet exporter (#103).

No network, cloud or BigQuery calls. Every file is written under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import io
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from research_platform.analytics.bigquery.contracts import (
    build_table_contracts,
    columns_from_arrow_schema,
)
from research_platform.canonical.memory import CanonicalSnapshot, InMemoryCanonicalStore
from research_platform.canonical.openalex import (
    CANONICAL_SCHEMAS,
    CanonicalActivityState,
    CanonicalLineage,
    map_openalex_work,
)
from research_platform.canonical.openalex.export import (
    CANONICAL_TABLE_MODELS,
    CANONICAL_TABLE_NAMES,
    PARQUET_COMPRESSION,
    CanonicalExportError,
    CanonicalParquetExport,
    canonical_parquet_path,
    canonical_row,
    canonical_table_to_arrow,
    export_snapshot_to_parquet,
    read_canonical_parquet,
    snapshot_to_arrow,
    validate_canonical_arrow_table,
    write_canonical_parquet,
)
from research_platform.canonical.openalex.models import WorkTopic
from research_platform.config.models import StorageConfig
from research_platform.control import InMemoryControlStore, PipelineRunStatus
from research_platform.ingestion import run_openalex_works_local_ingest
from research_platform.persistence.postgres.row_codec import lineage_from_row
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.local import LocalObjectStore

_RUN_ID = UUID("00000000-0000-4000-8000-000000000103")
_DELETION_RUN_ID = UUID("00000000-0000-4000-8000-000000000104")
_ASSET_A = "oa-works-asset-a"
_ASSET_B = "oa-works-asset-b"
_DELETION_ASSET = "oa-deletions-asset"
_PROCESSED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
# Non-UTC offset on purpose: Parquet stores the UTC instant (BigQuery TIMESTAMP).
_PROCESSED_AT_B = datetime(2024, 6, 2, 14, 30, 0, tzinfo=timezone(timedelta(hours=2)))
_DELETED_DATE = date(2024, 3, 1)
_DELETED_AT = datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC)

_DDL_COLUMN = re.compile(r"^\s+`(?P<name>\w+)` (?P<type>[A-Z0-9]+)(?P<not_null> NOT NULL)?,?$")


def _lineage(asset: str, *, checksum: str, updated: date, processed_at: datetime) -> CanonicalLineage:
    return CanonicalLineage.model_validate(
        {
            "source_asset_id": asset,
            "source_checksum_sha256": checksum,
            "source_updated_date": updated,
            "run_id": _RUN_ID,
            "processed_at": processed_at,
            "activity_state": CanonicalActivityState.ACTIVE,
        }
    )


def _rich_work() -> dict[str, Any]:
    """One record that populates every relationship and dimension table."""
    source = {
        "id": "https://openalex.org/S1",
        "display_name": "Journal One",
        "type": "journal",
        "issn_l": "1234-5678",
        "host_organization": "https://openalex.org/P1",
        "host_organization_name": "Publisher One",
    }
    return {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1234/Example",
        "title": "A fully populated work",
        "publication_year": 2020,
        "publication_date": "2020-05-01",
        "type": "article",
        "language": "en",
        "cited_by_count": 3,
        "referenced_works_count": 3,
        "open_access": {"is_oa": True, "oa_status": "gold"},
        "created_date": "2020-01-01",
        "updated_date": "2024-01-10",
        "primary_location": {
            "source": source,
            "is_oa": True,
            "landing_page_url": "https://example.org/w1",
        },
        "locations": [
            {
                "source": source,
                "is_oa": True,
                "landing_page_url": "https://example.org/w1",
                "pdf_url": "https://example.org/w1.pdf",
                "license": "cc-by",
                "version": "publishedVersion",
            },
            {"source": None, "landing_page_url": "https://repo.example.org/w1"},
        ],
        "authorships": [
            {
                "author_position": "first",
                "is_corresponding": True,
                "raw_author_name": "Ada L.",
                "author": {
                    "id": "https://openalex.org/A1",
                    "display_name": "Ada Lovelace",
                    "orcid": "https://orcid.org/0000-0002-1825-0097",
                },
                "institutions": [
                    {
                        "id": "https://openalex.org/I1",
                        "display_name": "Inst One",
                        "ror": "https://ror.org/02n415q13",
                        "country_code": "GB",
                        "type": "education",
                    }
                ],
            },
            {
                "author_position": "last",
                "author": {"id": "https://openalex.org/A2", "display_name": "Alan"},
                "institutions": [
                    {"id": "https://openalex.org/I1", "display_name": "Inst One"},
                    {"id": "https://openalex.org/I2", "display_name": "Inst Two"},
                ],
            },
        ],
        "topics": [
            {"id": "https://openalex.org/T1", "display_name": "Topic One", "score": 0.9},
            {"id": "https://openalex.org/T2", "display_name": "Topic Two", "score": 0.4},
        ],
        "keywords": [
            {
                "id": "https://openalex.org/keywords/machine-learning",
                "display_name": "Machine learning",
                "score": 0.5,
            }
        ],
        "mesh": [
            {
                "descriptor_ui": "D000001",
                "descriptor_name": "Descriptor",
                "qualifier_ui": "Q000001",
                "qualifier_name": "Qualifier",
                "is_major_topic": True,
            },
            {"descriptor_ui": "D000002", "descriptor_name": "Other", "qualifier_ui": None},
        ],
        "referenced_works": ["https://openalex.org/W2", "not-a-work", ""],
        "grants": [
            {
                "funder": "https://openalex.org/F1",
                "funder_display_name": "Funder One",
                "award_id": "AWARD-1",
            },
            {"funder": None, "award_id": None},
        ],
    }


def _records() -> list[tuple[str, dict[str, Any], CanonicalLineage]]:
    lineage_a = _lineage(
        _ASSET_A, checksum="aa" * 32, updated=date(2024, 1, 10), processed_at=_PROCESSED_AT
    )
    lineage_b = _lineage(
        _ASSET_B, checksum="bb" * 32, updated=date(2024, 2, 1), processed_at=_PROCESSED_AT_B
    )
    return [
        ("W1", _rich_work(), lineage_a),
        # Every array key absent: all presences MISSING, every nullable field NULL.
        ("W2", {"id": "https://openalex.org/W2"}, lineage_a),
        (
            "W3",
            {
                "id": "https://openalex.org/W3",
                "title": "To be deleted",
                "publication_year": 2019,
                "authorships": [],
                "topics": None,
                "referenced_works": ["https://openalex.org/W1"],
                "keywords": [],
                "mesh": [],
                "locations": [],
                "grants": [],
                "updated_date": "2024-02-01",
            },
            lineage_b,
        ),
    ]


def _build_store(*, reverse: bool = False) -> InMemoryCanonicalStore:
    store = InMemoryCanonicalStore()
    records = _records()
    for _, record, lineage in reversed(records) if reverse else records:
        store.upsert_work_bundle(map_openalex_work(record, lineage=lineage))
    store.apply_work_deletion(
        "W3",
        deletion_asset_id=_DELETION_ASSET,
        source_checksum_sha256="cc" * 32,
        run_id=_DELETION_RUN_ID,
        deleted_date=_DELETED_DATE,
        processed_at=_DELETED_AT,
        deleted_at=_DELETED_AT,
    )
    return store


def _snapshot() -> CanonicalSnapshot:
    return _build_store().snapshot()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bigquery_column_from_parquet(column: pq.ColumnSchema) -> tuple[str, str, str]:
    """Name, type and mode BigQuery derives from one flat Parquet column.

    Mirrors BigQuery's documented Parquet conversions for the types the
    canonical schemas use; anything else fails the test.
    """
    assert column.max_repetition_level == 0, column.name
    assert column.max_definition_level in {0, 1}, column.name
    physical = column.physical_type
    logical = column.logical_type.type
    if physical == "BYTE_ARRAY" and logical == "STRING":
        bq_type = "STRING"
    elif physical == "INT32" and logical == "DATE":
        bq_type = "DATE"
    elif physical == "INT64" and logical == "TIMESTAMP":
        spec = json.loads(column.logical_type.to_json())
        # isAdjustedToUTC=false would load as DATETIME, not TIMESTAMP.
        assert spec["isAdjustedToUTC"] is True, column.name
        assert spec["timeUnit"] == "microseconds", column.name
        bq_type = "TIMESTAMP"
    elif physical in {"INT32", "INT64"} and logical in {"NONE", "INT"}:
        bq_type = "INT64"
    elif physical == "DOUBLE" and logical == "NONE":
        bq_type = "FLOAT64"
    elif physical == "BOOLEAN" and logical == "NONE":
        bq_type = "BOOL"
    else:
        raise AssertionError(f"unmapped Parquet column {column.name}: {physical}/{logical}")
    mode = "REQUIRED" if column.max_definition_level == 0 else "NULLABLE"
    return column.name, bq_type, mode


def _ddl_columns(ddl: str) -> list[tuple[str, str, str]]:
    columns: list[tuple[str, str, str]] = []
    for line in ddl.splitlines():
        match = _DDL_COLUMN.match(line)
        if match:
            mode = "REQUIRED" if match.group("not_null") else "NULLABLE"
            columns.append((match.group("name"), match.group("type"), mode))
    return columns


def _entity_from_row(table_name: str, row: dict[str, Any]) -> Any:
    model = CANONICAL_TABLE_MODELS[table_name]
    payload = {name: row[name] for name in model.model_fields if name != "lineage"}
    return model.model_validate({**payload, "lineage": lineage_from_row(row)})


def test_table_registry_matches_snapshot_schemas_and_models() -> None:
    snapshot_fields = tuple(field.name for field in dataclasses.fields(CanonicalSnapshot))
    assert CANONICAL_TABLE_NAMES == tuple(CANONICAL_SCHEMAS) == snapshot_fields
    assert tuple(CANONICAL_TABLE_MODELS) == CANONICAL_TABLE_NAMES
    assert len(CANONICAL_TABLE_NAMES) == 15
    assert {c.table_name for c in build_table_contracts()} == set(CANONICAL_TABLE_NAMES)
    lineage_names = [
        "source_asset_id",
        "source_checksum_sha256",
        "lineage_source_updated_date",
        "run_id",
        "processed_at",
        "activity_state",
        "deleted_at",
    ]
    for name, model in CANONICAL_TABLE_MODELS.items():
        entity_fields = [field for field in model.model_fields if field != "lineage"]
        assert [*entity_fields, *lineage_names] == CANONICAL_SCHEMAS[name].names, name


def test_fixture_populates_every_table_with_active_and_deleted_works() -> None:
    snapshot = _snapshot()
    for name in CANONICAL_TABLE_NAMES:
        assert len(getattr(snapshot, name)) > 0, name
    states = {work.work_id: work.lineage.activity_state for work in snapshot.works}
    assert states == {
        "W1": CanonicalActivityState.ACTIVE,
        "W2": CanonicalActivityState.ACTIVE,
        "W3": CanonicalActivityState.DELETED,
    }


def test_arrow_schema_equals_canonical_schema_and_bigquery_contract() -> None:
    tables = snapshot_to_arrow(_snapshot())
    assert tuple(tables) == CANONICAL_TABLE_NAMES
    for contract in build_table_contracts():
        table = tables[contract.table_name]
        assert table.schema.equals(CANONICAL_SCHEMAS[contract.table_name], check_metadata=True)
        assert columns_from_arrow_schema(table.schema) == contract.columns
        assert [
            (field.name, field.nullable) for field in table.schema
        ] == [(column.name, column.nullable) for column in contract.columns]


def test_parquet_schema_matches_canonical_schema_contract_and_ddl(
    tmp_path: Path, repo_root: Path
) -> None:
    export = export_snapshot_to_parquet(_snapshot(), tmp_path / "export")
    for contract in build_table_contracts():
        path = export.file_for(contract.table_name).path
        assert path == canonical_parquet_path(tmp_path / "export", contract.table_name)
        arrow_schema = pq.read_schema(path)
        assert arrow_schema.equals(CANONICAL_SCHEMAS[contract.table_name], check_metadata=False)

        parquet_file = pq.ParquetFile(path)
        parquet_schema = parquet_file.schema
        as_bigquery = [
            _bigquery_column_from_parquet(parquet_schema.column(i))
            for i in range(len(parquet_schema))
        ]
        expected = [
            (column.name, column.bq_type.value, "NULLABLE" if column.nullable else "REQUIRED")
            for column in contract.columns
        ]
        assert as_bigquery == expected, contract.table_name

        ddl = (repo_root / contract.ddl_path).read_text(encoding="utf-8")
        assert _ddl_columns(ddl) == expected, contract.ddl_path

        metadata = parquet_file.metadata
        for group in range(metadata.num_row_groups):
            for index in range(metadata.num_columns):
                column = metadata.row_group(group).column(index)
                assert column.compression == PARQUET_COMPRESSION.upper()


def test_required_columns_are_non_null_and_nullable_columns_keep_nulls(tmp_path: Path) -> None:
    export = export_snapshot_to_parquet(_snapshot(), tmp_path)
    tables = {
        name: read_canonical_parquet(name, export.file_for(name).path)
        for name in CANONICAL_TABLE_NAMES
    }
    for name, table in tables.items():
        for field in CANONICAL_SCHEMAS[name]:
            if not field.nullable:
                assert table.column(field.name).null_count == 0, (name, field.name)
    works = tables["works"]
    # The fixture must exercise NULLs, not only populated values.
    assert works.column("doi").null_count == 2
    assert works.column("publication_year").null_count == 1
    assert works.column("deleted_at").null_count == 2
    assert tables["work_grants"].column("funder_id").null_count == 1
    assert tables["work_mesh"].column("qualifier_ui").null_count == 1
    assert tables["work_references"].column("referenced_work_id").null_count == 2


def test_required_null_is_rejected_before_any_file_is_written(tmp_path: Path) -> None:
    lineage = _records()[0][2]
    corrupt = WorkTopic.model_construct(
        work_id="W1", topic_id=None, score=None, lineage=lineage
    )
    with pytest.raises(CanonicalExportError, match="topic_id"):
        canonical_table_to_arrow("work_topics", [corrupt])

    # Arrow itself accepts nulls in a non-nullable field; the exporter must not.
    valid = canonical_table_to_arrow("work_topics", _snapshot().work_topics[:1])
    index = valid.schema.get_field_index("topic_id")
    table = valid.set_column(index, valid.schema.field(index), pa.nulls(1, type=pa.string()))
    assert table.schema.equals(CANONICAL_SCHEMAS["work_topics"])
    with pytest.raises(CanonicalExportError, match=r"non-nullable columns contain nulls \['topic_id'\]"):
        validate_canonical_arrow_table("work_topics", table)
    target = tmp_path / "work_topics.parquet"
    with pytest.raises(CanonicalExportError):
        write_canonical_parquet("work_topics", table, target)
    assert list(tmp_path.iterdir()) == []

    snapshot = dataclasses.replace(_snapshot(), work_topics=(corrupt,))
    with pytest.raises(CanonicalExportError, match="topic_id"):
        export_snapshot_to_parquet(snapshot, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_wrong_entity_type_schema_and_unknown_table_are_rejected() -> None:
    snapshot = _snapshot()
    with pytest.raises(CanonicalExportError, match="expected Work"):
        canonical_table_to_arrow("works", [snapshot.authors[0]])
    with pytest.raises(CanonicalExportError, match="unknown canonical table"):
        canonical_table_to_arrow("works_staging", snapshot.works)
    with pytest.raises(CanonicalExportError, match="unknown canonical table"):
        canonical_parquet_path("/unused", "openalex.works")
    relaxed = pa.schema([field.with_nullable(True) for field in CANONICAL_SCHEMAS["authors"]])
    authors = canonical_table_to_arrow("authors", snapshot.authors).cast(relaxed)
    with pytest.raises(CanonicalExportError, match="Arrow schema differs"):
        validate_canonical_arrow_table("authors", authors)


def test_rows_use_enum_values_uuid_strings_and_flattened_lineage() -> None:
    snapshot = _snapshot()
    works = {work.work_id: work for work in snapshot.works}
    rich = canonical_row("works", works["W1"])
    assert list(rich) == CANONICAL_SCHEMAS["works"].names
    assert type(rich["authorships_presence"]) is str
    assert rich["authorships_presence"] == "PRESENT"
    assert rich["run_id"] == str(_RUN_ID)
    assert type(rich["run_id"]) is str
    assert rich["activity_state"] == "ACTIVE"
    assert rich["source_updated_date"] == date(2024, 1, 10)
    assert rich["lineage_source_updated_date"] == date(2024, 1, 10)
    assert rich["doi"] == "10.1234/example"

    missing = canonical_row("works", works["W2"])
    assert missing["topics_presence"] == "MISSING"
    assert missing["title"] is None

    tombstone = canonical_row("works", works["W3"])
    assert tombstone["activity_state"] == "DELETED"
    assert tombstone["run_id"] == str(_DELETION_RUN_ID)
    assert tombstone["source_asset_id"] == _DELETION_ASSET
    assert tombstone["lineage_source_updated_date"] == _DELETED_DATE
    # Entity payload keeps the source Work date; only lineage moves to the deletion.
    assert tombstone["source_updated_date"] == date(2024, 2, 1)
    assert tombstone["deleted_at"] == _DELETED_AT
    assert tombstone["topics_presence"] == "NULL"

    statuses = {row.reference_status.value for row in snapshot.work_references}
    assert statuses == {"RESOLVED_ID", "MALFORMED", "MISSING"}
    reference = canonical_row("work_references", snapshot.work_references[0])
    assert type(reference["reference_status"]) is str
    location = canonical_row("work_locations", snapshot.work_locations[0])
    assert location["location_origin"] == "LOCATIONS_ARRAY"
    assert type(location["location_origin"]) is str


def test_output_bytes_are_deterministic_for_identical_input(tmp_path: Path) -> None:
    first = export_snapshot_to_parquet(_snapshot(), tmp_path / "first")
    # Independently built store, Works upserted in the opposite order.
    second = export_snapshot_to_parquet(_build_store(reverse=True).snapshot(), tmp_path / "second")
    for name in CANONICAL_TABLE_NAMES:
        left = first.file_for(name)
        right = second.file_for(name)
        assert left.path.read_bytes() == right.path.read_bytes(), name
        assert left.sha256 == right.sha256 == _sha256(left.path)
        assert left.byte_size == right.byte_size == left.path.stat().st_size

    rewritten = export_snapshot_to_parquet(_snapshot(), tmp_path / "first", overwrite=True)
    assert [item.sha256 for item in rewritten.files] == [item.sha256 for item in first.files]
    assert not [path for path in (tmp_path / "first").iterdir() if path.suffix != ".parquet"]


def test_round_trip_row_counts_and_values(tmp_path: Path) -> None:
    store = _build_store()
    snapshot = store.snapshot()
    export = export_snapshot_to_parquet(snapshot, tmp_path)
    assert isinstance(export, CanonicalParquetExport)
    store_counts = {"works": store.work_count(), **store.relationship_counts()}
    assert export.row_counts() == store_counts
    for name in CANONICAL_TABLE_NAMES:
        entities = getattr(snapshot, name)
        table = read_canonical_parquet(name, export.file_for(name).path)
        assert table.num_rows == len(entities) == export.row_counts()[name], name
        rows = table.to_pylist()
        assert rows == [canonical_row(name, entity) for entity in entities], name
        assert [_entity_from_row(name, row) for row in rows] == list(entities), name

    works = read_canonical_parquet("works", export.file_for("works").path).to_pylist()
    by_id = {row["work_id"]: row for row in works}
    assert by_id["W3"]["processed_at"] == _DELETED_AT
    assert by_id["W3"]["deleted_at"] == _DELETED_AT
    rel = read_canonical_parquet("work_references", export.file_for("work_references").path)
    w3_ref = [row for row in rel.to_pylist() if row["work_id"] == "W3"][0]
    # Stored as the UTC instant of the +02:00 asset-B processed_at.
    assert w3_ref["processed_at"] == datetime(2024, 6, 2, 12, 30, 0, tzinfo=UTC)
    assert w3_ref["processed_at"].utcoffset() == timedelta(0)
    # Policy A: the tombstone keeps its relationship rows with their ACTIVE lineage.
    assert w3_ref["activity_state"] == "ACTIVE"


def test_export_refuses_existing_files_without_overwrite(tmp_path: Path) -> None:
    export = export_snapshot_to_parquet(_snapshot(), tmp_path)
    before = {item.table_name: item.sha256 for item in export.files}
    empty = InMemoryCanonicalStore().snapshot()
    with pytest.raises(CanonicalExportError, match="refusing to overwrite"):
        export_snapshot_to_parquet(empty, tmp_path)
    after = {name: _sha256(canonical_parquet_path(tmp_path, name)) for name in CANONICAL_TABLE_NAMES}
    assert after == before
    table = canonical_table_to_arrow("works", ())
    with pytest.raises(CanonicalExportError, match="refusing to overwrite"):
        write_canonical_parquet("works", table, canonical_parquet_path(tmp_path, "works"))


def test_empty_snapshot_exports_schema_only_files(tmp_path: Path) -> None:
    export = export_snapshot_to_parquet(InMemoryCanonicalStore().snapshot(), tmp_path)
    assert set(export.row_counts().values()) == {0}
    for name in CANONICAL_TABLE_NAMES:
        table = read_canonical_parquet(name, export.file_for(name).path)
        assert table.num_rows == 0
        assert table.schema.equals(CANONICAL_SCHEMAS[name], check_metadata=False)


class _FakeConnector:
    def __init__(self, *, asset: OpenAlexAssetMetadata, payload: bytes) -> None:
        self._asset = asset
        self._payload = payload

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(
            max_files=1,
            max_file_size_bytes=25_000_000,
            eligible_count=1,
            selected=(self._asset,),
            skipped=(),
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        assert asset.uri == self._asset.file_uri
        return io.BytesIO(self._payload)


def _clock():
    state = {"current": datetime(2024, 6, 1, 1, 0, 0, tzinfo=UTC)}

    def clock() -> datetime:
        current = state["current"]
        state["current"] = current + timedelta(seconds=1)
        return current

    return clock


def test_step12_memory_ingest_snapshot_exports_cleanly(tmp_path: Path) -> None:
    records = [record for _, record, _ in _records()]
    body = "\n".join(json.dumps(record) for record in records) + "\n"
    payload = gzip.compress(body.encode("utf-8"), mtime=0)
    asset = OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": date(2024, 1, 15),
            "entity": "works",
            "file_uri": "s3://openalex/data/jsonl/works/updated_date=2024-01-10/part_000.gz",
            "byte_size": len(payload),
            "updated_date": date(2024, 1, 10),
            "content_format": "jsonl",
        }
    )
    landing = tmp_path / "landing"
    config_path = tmp_path / "local.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "environment": "local",
                "log_level": "INFO",
                "source": "openalex",
                "storage": {"backend": "local", "landing_path": str(landing)},
                "warehouse": {
                    "transactional": "postgres",
                    "analytical": "duckdb",
                    "postgres_dsn_env": "POSTGRES_DSN",
                    "duckdb_path": ":memory:",
                },
                "sample_selection": {"max_files": 1, "max_file_size_bytes": 25_000_000},
            }
        ),
        encoding="utf-8",
    )
    canonical = InMemoryCanonicalStore()
    result = run_openalex_works_local_ingest(
        config_path,
        backend="memory",
        control_store=InMemoryControlStore(),
        canonical_store=canonical,
        object_store=LocalObjectStore(StorageConfig(backend="local", landing_path=landing)),
        connector=_FakeConnector(asset=asset, payload=payload),  # type: ignore[arg-type]
        now=_clock(),
    )
    assert result.run.status is PipelineRunStatus.SUCCESS
    assert canonical.work_count() == 3

    export = export_snapshot_to_parquet(canonical.snapshot(), tmp_path / "export")
    assert export.row_counts() == {"works": 3, **canonical.relationship_counts()}
    works = read_canonical_parquet("works", export.file_for("works").path).to_pylist()
    assert {row["run_id"] for row in works} == {str(result.run.run_id)}
    assert {row["source_asset_id"] for row in works} == {asset.asset_id}
    assert {row["source_checksum_sha256"] for row in works} == {
        hashlib.sha256(payload).hexdigest()
    }
    assert {row["lineage_source_updated_date"] for row in works} == {date(2024, 1, 10)}
    for name in CANONICAL_TABLE_NAMES:
        read_canonical_parquet(name, export.file_for(name).path)
    assert {item.path.parent for item in export.files} == {tmp_path / "export"}
