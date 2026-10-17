"""DE-07 (#128): Silver canonical Parquet releases, built, validated and reconciled offline.

LOCAL, synthetic: Bronze is landed through the real Step 12/13 wrappers
(``tests/support/canonical_release_fixture.py``); Silver is ``LocalObjectStore``
or ``GCSObjectStore`` over the in-memory ``FakeGCSClient`` (``[local|gcs_fake]``).
No network, cloud, OpenAlex, PostgreSQL or BigQuery call is made.
"""

from __future__ import annotations

import ast
import dataclasses
import gzip
import hashlib
import importlib
import inspect
import io
import itertools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, BinaryIO
from uuid import UUID

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from research_platform.analytics.bigquery.contracts import build_table_contracts
from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import lineage_codec
from research_platform.canonical.openalex import manifest as manifest_module
from research_platform.canonical.openalex import publisher as pub
from research_platform.canonical.openalex import rebuild
from research_platform.canonical.openalex.deletions import DeletionOutcome
from research_platform.canonical.openalex.errors import (
    PublicationRecordError,
    ReconciliationMismatchError,
    ReleaseConfigError,
    ReleaseConflictError,
    ReleaseEnvironmentMismatchError,
    ReleaseInputError,
    ReleaseManifestError,
    ReleaseNotFoundError,
    ReleaseParentError,
    ReleasePartError,
    ReleaseRebuildError,
    ReleaseSchemaMismatchError,
)
from research_platform.canonical.openalex.export import (
    CanonicalParquetExport,
    canonical_table_to_arrow,
    export_snapshot_to_parquet,
    pinned_parquet_bytes,
    snapshot_to_arrow,
)
from research_platform.canonical.openalex.manifest import (
    CANONICAL_SCHEMA_MAJOR,
    RELEASE_TABLE_NAMES,
    CanonicalReleaseManifest,
    PublicationRecord,
    WriterSettings,
    canonical_json,
    dumps_release_manifest,
    footer_metadata,
    release_schema_fingerprints,
    release_table_schema,
    schema_fingerprint,
)
from research_platform.canonical.openalex.models import CanonicalActivityState
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS
from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.control.models import ControlStatus, PipelineRunStatus
from research_platform.ingestion import deletion_pipeline, pipeline, run_openalex_deletions_local_ingest
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.errors import IngestionError
from research_platform.persistence.postgres import row_codec
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.canonical_layout import (
    CANONICAL_ROOT,
    canonical_manifest_key,
    canonical_manifest_path_from_root,
    canonical_part_key,
    canonical_part_relative_path,
    canonical_publication_record_key,
    canonical_release_root,
    parse_part_relative_path,
    release_id_for_build,
    validate_release_id,
)
from research_platform.storage.errors import (
    IncompleteObjectError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
)
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.landing_manifest import (
    LandingManifest,
    LandingManifestEntry,
    dumps_landing_manifest,
    landing_manifest_provenance,
)
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_landing_manifest_key
from tests.support.canonical_release_fixture import (
    FixtureConnector,
    deletion_asset,
    fixture_assets,
    land,
    local_config,
    rich_work,
)
from tests.support.fake_gcs import FakeGCSClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILT_N = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
BUILT_N1 = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
RUN_N = UUID(int=1)
RUN_N1 = UUID(int=2)
SMALL: dict[str, Any] = {"rows_per_part": 2, "row_group_size": 1}
SILVER_BUCKET = "synthetic-silver"
BRONZE_BUCKET = "synthetic-bronze"
PROJECT = "synthetic-project"
N_INPUTS = ("a1", "d1", "a2")

GOLDEN_FINGERPRINTS = {
    "works": "951d732eabe0973eac1a020fb306e2ed619828f272bc4cdd8c54c1c85815bf56",
    "authors": "566ab39fa3f51cb9ac15d3880715afe6e4d6f511823dcc0258c8e0cd3b4ea64a",
    "institutions": "63f5b67d66ab2822711766d7525c91f732682b7ef951e74fd98084869ab81035",
    "sources": "b4c08346cae01607a37012854ecc0327f16fa3260fa15b935efe0ad6cd0f17d0",
    "publishers": "eb8f4b0e93173b922fd6ee1427e46f689205218ac27ca1cdc3b34be3ec37bdfa",
    "topics": "1813409229902977ca34202390d272a1bf8ba3b37acec7cea0c5c782042af8be",
    "funders": "803e09940ed1b9bc68536070283fa4db423f8081444f44723d19a5f82f331c57",
    "work_authors": "36bfa5d05044ee42f93169c8fe57cff007d0a015502ff070f7256f405f5302f6",
    "work_author_institutions": "96697729b28ea700e2850e2ff72a3fb4663b92098f79f74202813ff3891b5c7b",
    "work_topics": "84588eb5fcfe22ee7836a8cc99e75979252d8283756e729690d67bfe3cf27d0a",
    "work_keywords": "5cf751eeda776a2c3ef1a6f62e03d35e52fca1830b11e21ff9d026bc188b4eaf",
    "work_references": "6651fa6c7c13d6636e03f07519665c5734efba2f357c3fee120081686177457c",
    "work_mesh": "516351e946902f937e075caed99c9fd72404a563fcb8b1fbdd5685b106a9286c",
    "work_locations": "a4d3c13db12d3f408ee95ea3a90ff8bfb5a00bcbbf387095f893591daeb91a8c",
    "work_grants": "a238c27c32e9d8ab8607d03c2acdcb11def37071ae59abe3a9eab7b93db0f5b9",
    "work_deletion_barriers": "07f5f07f5f40573be35f30968a94fbf70dbd9fc5010015401f07ceaa8a615b13",
}
# Measured LOCAL (Python 3.13, pyarrow 25.0.1) on this code. Every CI leg must
# reproduce it. The raw Bronze bytes feed every row's source checksum, so check
# GOLDEN_FIXTURE_PAYLOAD_SHA256 first; with equal payloads a different value is a
# Parquet reproducibility finding, not a pin to loosen.
GOLDEN_FIXTURE_CONTENT_FINGERPRINT = "d7c824114dbb2793463c2654195c9c17a647750e44ea84c69e516ab908d98371"
GOLDEN_FIXTURE_PAYLOAD_SHA256 = {
    "a1": "024575a9a02f28ffebf968945ba99f2964e91b0e0cbc3d2850f7242685149ac4",
    "d1": "056b34d52d1ae3f640a0cbb850c2f5c7139b148f309a573430697d9107852aed",
    "a2": "2fffc1f26cab609a527ec446bc0f017aeefad6acea473515f2e0d6f246afdc94",
    "a3": "0418d58b1c4aeaf584f0af65ed80754f2b78c22ba0e866f2af3ebf24c8e1cef1",
}
# The intent digest is recomputed independently of the models from these fields.
_INTENT_HEADER_FIELDS = (
    "manifest_schema", "manifest_version", "source", "storage_role", "release_id", "parent_release_id",
    "schema_major", "build_run_id", "built_at", "code_version", "tools", "writer", "decode_limits",
    "source_rights",
)


# --- stores and helpers ---------------------------------------------------------------------------


class Recording(ObjectStore):
    """Records every call; can refuse puts or crash after ``fail_after`` successful puts."""

    def __init__(self, inner: ObjectStore, *, refuse_puts: bool = False, fail_after: int | None = None) -> None:
        self.inner, self.refuse_puts, self.fail_after = inner, refuse_puts, fail_after
        self.puts: list[str] = []
        self.opens: list[str] = []
        self.stats: list[str] = []

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        if self.refuse_puts:
            raise AssertionError("no writes allowed")
        if self.fail_after is not None and len(self.puts) >= self.fail_after:
            raise ConnectionError("injected crash")
        self.inner.put_if_absent(key, content, provenance)
        self.puts.append(key)

    def open(self, key: str) -> BinaryIO:
        self.opens.append(key)
        return self.inner.open(key)

    def stat(self, key: str) -> ObjectStat | None:
        self.stats.append(key)
        return self.inner.stat(key)


class Swap(Recording):
    """Serves replacement bytes (or a missing object) for chosen keys."""

    def __init__(self, inner: ObjectStore, replacements: dict[str, bytes | None]) -> None:
        super().__init__(inner)
        self.replacements = replacements

    def open(self, key: str) -> BinaryIO:
        self.opens.append(key)
        if key in self.replacements:
            data = self.replacements[key]
            if data is None:
                raise ObjectNotFoundError("object not found")
            return io.BytesIO(data)
        return self.inner.open(key)


def _local(root: Path) -> LocalObjectStore:
    return LocalObjectStore(StorageConfig(backend="local", landing_path=root))


def _gcs(bucket: str, client: FakeGCSClient) -> GCSObjectStore:
    return GCSObjectStore(StorageConfig(backend="gcs", bucket=bucket), CloudConfig(project_id=PROJECT), client=client)


def _silver(kind: str, tmp_path: Path, client: FakeGCSClient | None = None) -> ObjectStore:
    if kind == "local":
        return _local(tmp_path / "silver")
    return _gcs(SILVER_BUCKET, client or FakeGCSClient())


@pytest.fixture
def env(tmp_path: Path) -> SimpleNamespace:
    bronze = _local(tmp_path / "bronze")
    assets = fixture_assets()
    config = local_config(tmp_path / "cfg", tmp_path / "bronze")
    landed = land(bronze, config, [assets[name] for name in N_INPUTS])
    return SimpleNamespace(bronze=bronze, landed=landed, assets=assets, tmp_path=tmp_path, config=config)


def _build(env: SimpleNamespace, silver: ObjectStore, *names: str, **overrides: Any) -> pub.BuiltRelease:
    args: dict[str, Any] = {
        "bronze": env.bronze, "silver": silver, "inputs": env.landed.specs_for(*(names or N_INPUTS)),
        "build_run_id": RUN_N, "built_at": BUILT_N, **SMALL, **overrides,
    }
    return pub.build_canonical_release(**args)


def _land_more(env: SimpleNamespace, *names: str) -> None:
    land(env.bronze, env.config, [env.assets[name] for name in names], landed=env.landed)


def _part(silver: ObjectStore, manifest: CanonicalReleaseManifest, table: str, index: int = 0) -> bytes:
    with silver.open(canonical_part_key(manifest.schema_major, manifest.release_id, table, index)) as handle:
        return handle.read()


def _read_table(silver: ObjectStore, manifest: CanonicalReleaseManifest, table: str) -> pa.Table:
    root = canonical_release_root(manifest.schema_major, manifest.release_id)
    parts = []
    for item in manifest.table(table).files:
        with silver.open(f"{root}/{item.path}") as handle:
            parts.append(pq.read_table(io.BytesIO(handle.read())))
    return pa.concat_tables(parts).replace_schema_metadata(None)


def _rows(silver: ObjectStore, manifest: CanonicalReleaseManifest, table: str, work_id: str) -> list[dict[str, Any]]:
    return [row for row in _read_table(silver, manifest, table).to_pylist() if row["work_id"] == work_id]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reseal(data: dict[str, Any]) -> dict[str, Any]:
    """Recompute content fingerprint and intent digest from a manifest dict, without the models."""
    data["content_fingerprint"] = hashlib.sha256(canonical_json(
        [[table["table"], [item["sha256"] for item in table["files"]]] for table in data["tables"]]
    ).encode()).hexdigest()
    intent = {name: data[name] for name in _INTENT_HEADER_FIELDS}
    intent["schema_fingerprints"] = {table["table"]: table["schema_fingerprint"] for table in data["tables"]}
    intent["inputs"] = [{k: v for k, v in item.items() if k != "counts"} for item in data["inputs"]]
    data["intent_sha256"] = hashlib.sha256(canonical_json(intent).encode()).hexdigest()
    return data


def _manifest_dict(manifest: CanonicalReleaseManifest) -> dict[str, Any]:
    return json.loads(dumps_release_manifest(manifest))


def _table_entry(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(table for table in data["tables"] if table["table"] == name)


def _hand_land(bronze: ObjectStore, entity: str, asset_id: str, payload: bytes, *,
               run_id: UUID, retrieved_at: datetime, updated: date) -> None:
    """Land a raw object and its landing manifest directly (for inputs no pipeline would accept)."""
    raw_key = (f"openalex/{entity}/snapshot_date=2026-09-30/updated_date={updated.isoformat()}/"
               f"{asset_id}/source{'.gz' if entity == 'works' else '.csv.gz'}")
    bronze.put_if_absent(raw_key, io.BytesIO(payload), IngestionProvenance(
        run_id=run_id, source="openalex", source_uri=f"s3://openalex/data/hand/{asset_id}.gz",
        retrieved_at=retrieved_at, sha256=_sha(payload)))
    landing = LandingManifest(
        entity=entity, pipeline_name="hand-landed", run_id=run_id, run_created_at=retrieved_at,
        storage_backend="local",
        entry=LandingManifestEntry(
            asset_id=asset_id, raw_object_key=raw_key, source_uri=f"s3://openalex/data/hand/{asset_id}.gz",
            snapshot_date=date(2026, 9, 30), updated_date=updated,
            content_format="jsonl" if entity == "works" else "csv", declared_size_bytes=len(payload),
            size_bytes=len(payload), sha256=_sha(payload), md5_hex=hashlib.md5(payload).hexdigest(),
            source_content_length=None, source_etag=None, source_version_id=None, retrieved_at=retrieved_at,
            landing_outcome="created", committed_run_id=run_id, committed_retrieved_at=retrieved_at,
            object_generation=None, object_crc32c=None, object_md5_base64=None, checks=(),
        ),
    )
    body = dumps_landing_manifest(landing)
    bronze.put_if_absent(landing.key, io.BytesIO(body), landing_manifest_provenance(landing, body))


def _flip_local(path: Path, offset: int = 40) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


def _flip_fake(client: FakeGCSClient, bucket: str, key: str, offset: int = 40) -> None:
    stored = client.bucket(bucket).objects[key]
    data = bytearray(stored.data)
    data[offset] ^= 0xFF
    stored.data = bytes(data)


# --- layout and identity ----------------------------------------------------------------------------


def test_release_id_derives_from_the_build_run_and_sorts_by_time() -> None:
    release_id = release_id_for_build(RUN_N, BUILT_N)
    assert len(release_id) == 49 and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", release_id)
    assert release_id == "20261002T000000Z-00000000000000000000000000000001"
    assert release_id < release_id_for_build(RUN_N, BUILT_N + timedelta(seconds=1))
    assert release_id == release_id_for_build(RUN_N, BUILT_N.astimezone(timezone(timedelta(hours=2))))
    assert release_id == release_id_for_build(UUID(int=1), datetime(2026, 10, 2, tzinfo=UTC))
    assert validate_release_id(release_id) == release_id
    with pytest.raises(InvalidObjectKeyError):
        release_id_for_build(RUN_N, BUILT_N.replace(microsecond=1))


def test_release_id_and_keys_refuse_unsafe_values() -> None:
    release_id = release_id_for_build(RUN_N, BUILT_N)
    for call in (
        lambda: release_id_for_build(RUN_N, BUILT_N.replace(tzinfo=None)),
        lambda: release_id_for_build(RUN_N, BUILT_N.replace(microsecond=500)),
        lambda: release_id_for_build("00000000-0000-0000-0000-000000000001", BUILT_N),  # type: ignore[arg-type]
        lambda: canonical_part_key(1, release_id, "Works", 0),
        lambda: canonical_part_key(1, release_id, "manifest", 0),
        lambda: canonical_part_key(1, release_id, "publications", 0),
        lambda: canonical_part_key(1, release_id, "a/b", 0),
        lambda: canonical_part_key(1, release_id, "works", -1),
        lambda: canonical_part_key(1, release_id, "works", 100_000),
        lambda: canonical_part_key(1, release_id, "works", True),  # type: ignore[arg-type]
        lambda: canonical_part_key(0, release_id, "works", 0),
        lambda: canonical_publication_record_key(0, release_id),
        lambda: canonical_publication_record_key(10**10, release_id),
        lambda: canonical_manifest_key(1, "20261002T000000Z-NOTHEX"),
        lambda: canonical_manifest_key(1, "20261002T000000Z-" + "AB" * 16),  # upper-case hex
        lambda: canonical_manifest_key(1, f"{release_id}/../x"),
        lambda: parse_part_relative_path("manifest/part-00000/part-00000.parquet"),
        lambda: parse_part_relative_path("works/part-00000/part-00001.parquet"),
    ):
        with pytest.raises(InvalidObjectKeyError):
            call()
    assert parse_part_relative_path(canonical_part_relative_path("work_grants", 7)) == ("work_grants", 7)


def test_layout_keys_obey_the_local_leaf_invariant(tmp_path: Path) -> None:
    release_id = release_id_for_build(RUN_N, BUILT_N)
    store = _local(tmp_path)

    def put(key: str, data: bytes) -> None:
        store.put_if_absent(key, io.BytesIO(data), IngestionProvenance(
            run_id=RUN_N, source="t", source_uri="urn:t", retrieved_at=BUILT_N, sha256=_sha(data)))

    for index in range(3):
        put(canonical_part_key(1, release_id, "works", index), b"x%d" % index)
    put(canonical_manifest_key(1, release_id), b"{}")
    put(canonical_publication_record_key(1, release_id), b"{}")
    root = canonical_release_root(1, release_id)
    with pytest.raises(IncompleteObjectError):  # ADR literal: manifest.json beside the table directories
        put(f"{root}/manifest.json", b"{}")
    put(f"{root}/authors/part-00000.parquet", b"a")
    with pytest.raises(IncompleteObjectError):  # ADR literal: a second part in one directory
        put(f"{root}/authors/part-00001.parquet", b"b")
    assert canonical_publication_record_key(1, release_id) == (
        f"openalex/canonical/publications/0000000001-{release_id}/0000000001-{release_id}.json"
    )


# --- manifest ---------------------------------------------------------------------------------------


def test_manifest_is_canonical_and_holds_no_store_identifiers(env: SimpleNamespace) -> None:
    silver = _silver("gcs_fake", env.tmp_path)
    release = _build(env, silver)
    payload = dumps_release_manifest(release.manifest)
    with silver.open(release.manifest_key) as handle:
        assert handle.read() == payload
    assert CanonicalReleaseManifest.model_validate_json(payload) == release.manifest
    assert payload.endswith(b"\n") and payload.count(b"\n") == 1
    assert release.manifest_sha256 == _sha(payload)
    for needle in (SILVER_BUCKET, PROJECT, "gs://", str(env.tmp_path), "/tmp"):
        assert needle.encode() not in payload, needle


def test_manifest_validators_refuse_inconsistent_content(env: SimpleNamespace) -> None:
    manifest = _build(env, _silver("local", env.tmp_path)).manifest
    original = _manifest_dict(manifest)
    assert CanonicalReleaseManifest.model_validate(_reseal(json.loads(json.dumps(original)))) == manifest

    def landing_key(item: dict[str, Any]) -> str:
        return openalex_landing_manifest_key(item["entity"], UUID(item["run_id"]), item["asset_id"])

    def duplicate_asset(d: dict[str, Any]) -> None:
        d["inputs"][2]["asset_id"] = d["inputs"][0]["asset_id"]
        d["inputs"][2]["landing_manifest_key"] = landing_key(d["inputs"][2])

    def swap_processed_at(d: dict[str, Any]) -> None:
        first, second = d["inputs"][1], d["inputs"][2]
        first["processed_at"], second["processed_at"] = second["processed_at"], first["processed_at"]

    def bump(path: Callable[[dict[str, Any]], dict[str, Any]], key: str, by: int = 1) -> Callable[[dict[str, Any]], None]:
        def mutate(d: dict[str, Any]) -> None:
            path(d)[key] += by
        return mutate

    def barrier_counts(d: dict[str, Any]) -> None:
        d["barrier_count"] += 1
        d["barrier_only_count"] += 1

    def tie_processed_at(d: dict[str, Any]) -> None:
        d["inputs"][2]["processed_at"] = d["inputs"][1]["processed_at"]

    def deleted_rows_above_rows(d: dict[str, Any]) -> None:
        entry = _table_entry(d, "work_references")
        entry["rows_of_deleted_works"] = entry["row_count"] + 1

    def counts_as_text(d: dict[str, Any]) -> None:
        entry = _table_entry(d, "works")
        entry["activity_state_counts"] = {k: str(v) for k, v in entry["activity_state_counts"].items()}

    later = release_id_for_build(RUN_N, BUILT_N + timedelta(days=1))
    cases: dict[str, tuple[Callable[[dict[str, Any]], None], bool]] = {
        "non_contiguous_sequence": (lambda d: d["inputs"][1].update(sequence=5), True),
        "duplicate_asset": (duplicate_asset, True),
        "id_not_derived": (lambda d: d.update(build_run_id=str(UUID(int=3))), True),
        "built_at_microseconds": (lambda d: d.update(built_at="2026-10-02T00:00:00.500000Z"), True),
        "non_utc_processed_at": (lambda d: d["inputs"][0].update(
            processed_at=datetime.fromisoformat(d["inputs"][0]["processed_at"]).astimezone(
                timezone(timedelta(hours=2))).isoformat()), True),
        "non_increasing_processed_at": (swap_processed_at, True),
        "tied_processed_at": (tie_processed_at, True),
        "tables_out_of_order": (lambda d: d["tables"].insert(1, d["tables"].pop(2)), True),
        "row_sum": (bump(lambda d: _table_entry(d, "works"), "row_count"), True),
        "activity_sum": (bump(lambda d: _table_entry(d, "works")["activity_state_counts"], "ACTIVE"), True),
        "rows_of_deleted_works_on_entity_table": (lambda d: _table_entry(d, "authors").update(
            rows_of_deleted_works=0), True),
        "barrier_counts": (barrier_counts, True),
        "barrier_only_count": (bump(lambda d: d, "barrier_only_count"), True),
        "rows_of_deleted_works_above_row_count": (deleted_rows_above_rows, True),
        "activity_counts_as_text": (counts_as_text, True),
        "abstracts_included": (lambda d: d["source_rights"].update(abstracts_included=True), True),
        "content_fingerprint": (lambda d: d.update(content_fingerprint="0" * 64), False),
        "intent_digest": (lambda d: d.update(intent_sha256="0" * 64), False),
        "parent_not_older": (lambda d: d.update(parent_release_id=later), True),
        "counts_kind": (lambda d: d["inputs"][0]["counts"].update(kind="works-deletions"), True),
        "records_not_raw_lines": (bump(lambda d: d["inputs"][0]["counts"], "raw_lines"), True),
    }
    for name, (mutate, reseal) in cases.items():
        data = json.loads(json.dumps(original))
        mutate(data)
        if reseal:
            _reseal(data)
        with pytest.raises(ValueError):
            CanonicalReleaseManifest.model_validate(data)
        assert data != original, name


def test_t06_release_schema_fingerprints_are_pinned() -> None:
    fingerprints = release_schema_fingerprints()
    assert tuple(fingerprints) == RELEASE_TABLE_NAMES and CANONICAL_SCHEMA_MAJOR == 1
    assert fingerprints == GOLDEN_FINGERPRINTS


def test_t06_barrier_table_stays_outside_canonical_schemas_and_bigquery_contracts(tmp_path: Path) -> None:
    assert len(CANONICAL_SCHEMAS) == 15 and "work_deletion_barriers" not in CANONICAL_SCHEMAS
    assert len(RELEASE_TABLE_NAMES) == 16 and RELEASE_TABLE_NAMES[:15] == tuple(CANONICAL_SCHEMAS)
    assert {contract.table_name for contract in build_table_contracts()} >= set(CANONICAL_SCHEMAS)
    assert "work_deletion_barriers" not in {contract.table_name for contract in build_table_contracts()}
    exported = export_snapshot_to_parquet(InMemoryCanonicalStore().snapshot(), tmp_path / "export")
    extra = exported.files[0].model_copy(update={"table_name": "works"})
    with pytest.raises(ValueError):
        CanonicalParquetExport(output_dir=exported.output_dir, files=(*exported.files, extra))


def test_schema_differences_are_refused_only_where_bytes_must_match(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    release = _build(env, silver, "a1", "d1")
    other = {name: "0" * 64 for name in RELEASE_TABLE_NAMES}
    with monkeypatch.context() as patch:  # code whose schemas differ from the stored release
        patch.setattr(pub, "release_schema_fingerprints", lambda: other)
        assert pub.validate_release(silver, release.manifest.release_id).manifest == release.manifest
        with pytest.raises(ReleaseSchemaMismatchError):
            pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=release.manifest.release_id)
        puts = len(silver.puts)
        with pytest.raises(ReleaseSchemaMismatchError):
            _build(env, silver, "a1", "d1", "a2", build_run_id=RUN_N1, built_at=BUILT_N1,
                   parent_release_id=release.manifest.release_id)
        assert len(silver.puts) == puts
    with monkeypatch.context() as patch:  # a release under another schema_major stays addressable
        patch.setattr(pub, "CANONICAL_SCHEMA_MAJOR", 2)
        patch.setattr(manifest_module, "CANONICAL_SCHEMA_MAJOR", 2)
        major2 = _build(env, silver, build_run_id=RUN_N1, built_at=BUILT_N1)
    assert major2.manifest.schema_major == 2 and "/schema_major=2/" in major2.manifest_key
    assert pub.read_release_manifest(silver, major2.manifest.release_id, schema_major=2) == major2.manifest
    assert pub.validate_release(silver, major2.manifest.release_id, schema_major=2).manifest == major2.manifest
    with pytest.raises(ReleaseNotFoundError):
        pub.read_release_manifest(silver, major2.manifest.release_id)
    with pytest.raises(ReleaseSchemaMismatchError):
        pub._check_environment(major2.manifest)


def test_fingerprints_use_project_owned_type_tokens() -> None:
    class RenamedType:
        """The same Arrow type with other text, as a PyArrow upgrade could print it."""

        def __init__(self, inner: pa.DataType) -> None:
            self.inner = inner

        def equals(self, other: pa.DataType) -> bool:
            return self.inner.equals(other)

        def __str__(self) -> str:
            return f"renamed<{self.inner}>"

    for name in RELEASE_TABLE_NAMES:
        schema = release_table_schema(name)
        renamed = [SimpleNamespace(name=f.name, nullable=f.nullable, type=RenamedType(f.type)) for f in schema]
        assert schema_fingerprint(renamed) == schema_fingerprint(schema) == GOLDEN_FINGERPRINTS[name]
    tokens = {pa.string(): "string", pa.int32(): "int32", pa.int64(): "int64", pa.bool_(): "bool",
              pa.float64(): "float64", pa.date32(): "date32", pa.timestamp("us", tz="UTC"): "timestamp_us_utc"}
    works = release_table_schema("works")
    document = [{"name": f.name, "nullable": f.nullable,
                 "type": next(token for arrow, token in tokens.items() if f.type.equals(arrow))} for f in works]
    expected = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert schema_fingerprint(works) == expected
    with pytest.raises(KeyError):
        schema_fingerprint(pa.schema([pa.field("x", pa.large_string())]))


@pytest.mark.parametrize(
    "forgery",
    ["activity_counts_swapped", "part_with_other_schema", "footer_table_swapped", "non_canonical_bytes",
     "duplicate_json_keys", "rows_of_deleted_works_shifted", "row_group_count_shifted", "footer_schema_major_swapped",
     "footer_fingerprint_swapped", "barrier_row_count_shifted", "work_id_int64"],
)
def test_validate_release_refuses_a_forged_self_consistent_manifest(env: SimpleNamespace, forgery: str) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver, rows_per_part=1000, row_group_size=1000).manifest
    key = canonical_manifest_key(1, manifest.release_id)
    data = _manifest_dict(manifest)
    replacements: dict[str, bytes | None] = {}
    expected: type[Exception] = ReleasePartError

    def reencode(table: str, new: pa.Table, metadata: dict[bytes, bytes]) -> None:
        payload = pinned_parquet_bytes(new.replace_schema_metadata(metadata), row_group_size=1000)
        part_key = canonical_part_key(1, manifest.release_id, table, 0)
        replacements[part_key] = payload
        entry = _table_entry(data, table)["files"][0]
        entry.update(sha256=_sha(payload), size_bytes=len(payload),
                     row_group_count=pq.ParquetFile(io.BytesIO(payload)).metadata.num_row_groups)

    if forgery == "activity_counts_swapped":
        entry = _table_entry(data, "work_authors")
        entry["activity_state_counts"] = {"ACTIVE": 0, "DELETED": entry["row_count"]}
    elif forgery == "part_with_other_schema":
        original = pq.read_table(io.BytesIO(_part(silver, manifest, "work_keywords")))
        reencode("work_keywords", original.drop_columns([original.schema.names[-1]]), footer_metadata("work_keywords"))
    elif forgery == "footer_table_swapped":
        original = pq.read_table(io.BytesIO(_part(silver, manifest, "authors")))
        reencode("authors", original, {**footer_metadata("authors"), b"rp.table": b"publishers"})
    elif forgery == "rows_of_deleted_works_shifted":
        _table_entry(data, "work_references")["rows_of_deleted_works"] = 0
    elif forgery == "row_group_count_shifted":
        _table_entry(data, "works")["files"][0]["row_group_count"] += 1
    elif forgery == "footer_schema_major_swapped":
        original = pq.read_table(io.BytesIO(_part(silver, manifest, "authors")))
        reencode("authors", original, {**footer_metadata("authors"), b"rp.schema_major": b"2"})
    elif forgery == "footer_fingerprint_swapped":
        original = pq.read_table(io.BytesIO(_part(silver, manifest, "authors")))
        reencode("authors", original, {**footer_metadata("authors"), b"rp.schema_fingerprint": b"0" * 64})
    elif forgery == "barrier_row_count_shifted":  # the barrier table has no activity recount
        entry = _table_entry(data, "work_deletion_barriers")
        entry["files"][0]["row_count"] += 1
        entry["row_count"] += 1
        data["barrier_count"] += 1
        data["barrier_only_count"] += 1
    elif forgery == "work_id_int64":  # a self-consistent schema whose counted column is not a string
        original = pq.read_table(io.BytesIO(_part(silver, manifest, "work_grants")))
        forged = original.set_column(original.schema.get_field_index("work_id"),
                                     pa.field("work_id", pa.int64(), nullable=False),
                                     pa.array(range(original.num_rows), pa.int64()))
        fingerprint = schema_fingerprint(forged.schema)
        reencode("work_grants", forged,
                 {**footer_metadata("work_grants"), b"rp.schema_fingerprint": fingerprint.encode()})
        _table_entry(data, "work_grants")["schema_fingerprint"] = fingerprint
    if forgery == "non_canonical_bytes":
        replacements[key] = json.dumps(data, indent=2).encode()
        expected = ReleaseManifestError
    elif forgery == "duplicate_json_keys":
        text = dumps_release_manifest(manifest).decode()
        replacements[key] = ('{"barrier_count":%d,' % manifest.barrier_count + text[1:]).encode()
        expected = ReleaseManifestError
    else:
        resealed = CanonicalReleaseManifest.model_validate(_reseal(data))  # self-consistent forgery
        replacements[key] = dumps_release_manifest(resealed)
    with pytest.raises(expected):
        pub.validate_release(Swap(silver, replacements), manifest.release_id)


def test_stored_manifest_digest_is_checked_on_its_json_not_the_models(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    key = canonical_manifest_key(1, manifest.release_id)
    data = _manifest_dict(manifest)
    assert data["built_at"].endswith("Z")
    data["built_at"] = data["built_at"].replace("Z", "+00:00")  # another valid ISO spelling of UTC
    for item in data["inputs"]:
        item["processed_at"] = item["processed_at"].replace("Z", "+00:00")
    _reseal(data)
    validated = pub.validate_release(Swap(silver, {key: f"{canonical_json(data)}\n".encode()}), manifest.release_id)
    assert validated.manifest.model_copy(update={"intent_sha256": manifest.intent_sha256}) == manifest
    data["intent_sha256"] = "0" * 64
    with pytest.raises(ReleaseManifestError):
        pub.read_release_manifest(Swap(silver, {key: f"{canonical_json(data)}\n".encode()}), manifest.release_id)


# --- writer -------------------------------------------------------------------------------------------


def test_parts_split_by_rows_per_part_with_pinned_row_groups_and_footer(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver, rows_per_part=3, row_group_size=2).manifest
    works = manifest.table("works")
    assert [item.row_count for item in works.files] == [3, 2]
    assert [item.row_group_count for item in works.files] == [2, 1]
    parquet = pq.ParquetFile(io.BytesIO(_part(silver, manifest, "works")))
    assert parquet.metadata.created_by == f"parquet-cpp-arrow version {pa.__version__}"
    footer = parquet.schema_arrow.metadata
    assert footer[b"rp.table"] == b"works" and footer[b"rp.schema_major"] == b"1"
    assert footer[b"rp.schema_fingerprint"] == GOLDEN_FINGERPRINTS["works"].encode()
    assert manifest.writer.rows_per_part == 3 and manifest.writer.row_group_size == 2
    assert manifest.writer.parquet_options == dict(pub.PARQUET_WRITE_OPTIONS)


def test_empty_tables_get_one_schema_only_part(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver, "d1").manifest  # only a deletion: every row table is empty
    assert all(len(table.files) == 1 for table in manifest.tables)
    works = manifest.table("works")
    assert works.row_count == 0 and works.files[0].row_group_count == 1 and works.files[0].size_bytes > 0
    assert (manifest.barrier_count, manifest.barrier_only_count) == (2, 2)
    assert pub.validate_release(silver, manifest.release_id).manifest == manifest


def test_part_bytes_are_independent_of_the_release_id(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    first = _build(env, silver).manifest
    second = _build(env, silver, build_run_id=RUN_N1, built_at=BUILT_N1).manifest
    assert first.release_id != second.release_id
    assert first.content_fingerprint == second.content_fingerprint
    assert first.intent_sha256 != second.intent_sha256


def test_streamed_row_group_encoding_equals_write_table_bytes(env: SimpleNamespace) -> None:
    limits = {"works_limits": JsonlDecodeLimits(), "deletion_limits": CsvDeletionDecodeLimits()}
    writer = WriterSettings.pinned(rows_per_part=5, row_group_size=2)
    state = rebuild.replay_bronze_inputs(
        env.bronze, rebuild.resolve_bronze_inputs(env.bronze, env.landed.specs_for(*N_INPUTS)), **limits)
    works = next(part for part in pub.render_release_parts(state, writer) if part.table == "works")
    assert works.file.row_count == 5 and works.file.row_group_count == 3
    table = canonical_table_to_arrow("works", state.snapshot.works).replace_schema_metadata(footer_metadata("works"))
    assert works.payload == pinned_parquet_bytes(table, row_group_size=2)
    deletion_only = rebuild.replay_bronze_inputs(
        env.bronze, rebuild.resolve_bronze_inputs(env.bronze, env.landed.specs_for("d1")), **limits)
    empty = next(part for part in pub.render_release_parts(deletion_only, writer) if part.table == "works")
    expected = canonical_table_to_arrow("works", ()).replace_schema_metadata(footer_metadata("works"))
    assert empty.file.row_count == 0 and empty.payload == pinned_parquet_bytes(expected, row_group_size=2)


def test_build_refuses_invalid_arguments_before_any_store_call(env: SimpleNamespace) -> None:
    bronze, silver = Recording(env.bronze), Recording(_silver("local", env.tmp_path))
    cases: dict[str, dict[str, Any]] = {
        "rows_per_part": {"rows_per_part": 0},
        "row_group_size": {"rows_per_part": 2, "row_group_size": 3},
        "naive_built_at": {"built_at": BUILT_N.replace(tzinfo=None)},
        "sub_second_built_at": {"built_at": BUILT_N.replace(microsecond=1)},
        "run_id": {"build_run_id": str(RUN_N)},
        "upper_revision": {"code_revision": "ABCDEF" * 6 + "ABCD"},
        "short_revision": {"code_revision": "a" * 39},
        "parent": {"parent_release_id": "not-a-release"},
        "works_limits": {"works_decode_limits": {"max_records": 1}},
        "deletion_limits": {"deletion_decode_limits": JsonlDecodeLimits()},
        "no_inputs": {"inputs": ()},
        "naive_processed_at": {"inputs": [dataclasses.replace(
            env.landed.specs["a1"], processed_at=env.landed.specs["a1"].processed_at.replace(tzinfo=None))]},
    }
    for name, asset_id in (("dot_dot", "../evil"), ("empty", ""), ("slash", "a/b"), ("long", "a" * 129)):
        cases[f"asset_id_{name}"] = {"inputs": [env.landed.specs["a1"], dataclasses.replace(
            env.landed.specs["d1"], asset_id=asset_id)]}
    for name, overrides in cases.items():
        with pytest.raises(ReleaseConfigError):
            _build(env, silver, bronze=bronze, **overrides)
        assert (bronze.opens, bronze.stats, silver.opens, silver.stats, silver.puts) == ([], [], [], [], []), name


def test_pinned_parquet_bytes_match_the_export_file_bytes(env: SimpleNamespace) -> None:
    snapshot = env.landed.canonical.snapshot()
    exported = export_snapshot_to_parquet(snapshot, env.tmp_path / "export")
    tables = snapshot_to_arrow(snapshot)
    for item in exported.files:
        assert item.path.read_bytes() == pinned_parquet_bytes(tables[item.table_name], row_group_size=1_048_576)


def test_lineage_columns_moved_and_row_codec_still_exports_them() -> None:
    from research_platform.canonical.openalex import export

    assert row_codec.lineage_columns is lineage_codec.lineage_columns is export.lineage_columns
    assert row_codec.lineage_from_row is lineage_codec.lineage_from_row
    assert lineage_codec.__all__ == ["lineage_columns", "lineage_from_row"]


def test_release_modules_import_no_postgres_bigquery_or_duckdb() -> None:
    code = (
        "import sys\n"
        "import research_platform.canonical.openalex.export\n"
        "import research_platform.canonical.openalex.lineage_codec\n"
        "import research_platform.canonical.openalex.manifest\n"
        "import research_platform.canonical.openalex.publisher\n"
        "import research_platform.canonical.openalex.rebuild\n"
        "import research_platform.storage.canonical_layout\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('psycopg', 'duckdb')\n"
        "       or m.startswith(('google.cloud.bigquery', 'research_platform.persistence.postgres'))]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


class _Lying(ObjectStore):
    """A foreign store that keeps a flipped byte of the first works part and reports no facts."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        data = content.read()
        if key.endswith("/works/part-00000/part-00000.parquet"):
            data = data[:100] + bytes([data[100] ^ 0xFF]) + data[101:]
        self.objects[key] = data
        self.puts.append(key)

    def open(self, key: str) -> BinaryIO:
        if key not in self.objects:
            raise ObjectNotFoundError("absent")
        return io.BytesIO(self.objects[key])


def test_written_part_is_rechecked_when_the_store_reports_no_facts(env: SimpleNamespace) -> None:
    lying = _Lying()
    assert lying.stat("any/key") is None
    with pytest.raises(ReleasePartError):
        _build(env, lying)
    assert len(lying.puts) == 1 and not any(key.endswith("manifest.json") for key in lying.puts)


# --- T02 real formats and nested fidelity (synthetic) ---------------------------------------------------


def test_t02_gzip_jsonl_bronze_and_parquet_parts_are_real_formats(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    for item in manifest.inputs:
        with env.bronze.open(item.raw_object_key) as handle:
            raw = handle.read()
        assert raw[:2] == b"\x1f\x8b" and gzip.decompress(raw)
    root = canonical_release_root(1, manifest.release_id)
    for table in manifest.tables:
        for item in table.files:
            with silver.open(f"{root}/{item.path}") as handle:
                data = handle.read()
            assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"
            parquet = pq.ParquetFile(io.BytesIO(data))
            for index, field in enumerate(release_table_schema(table.table)):
                column = parquet.schema.column(index)
                assert column.name == field.name
                assert column.max_definition_level == (1 if field.nullable else 0), (table.table, field.name)
    works = pq.ParquetFile(io.BytesIO(_part(silver, manifest, "works"))).schema
    by_name = {works.column(i).name: works.column(i) for i in range(len(works))}
    assert by_name["publication_date"].logical_type.type == "DATE"
    processed = by_name["processed_at"].logical_type
    assert processed.type == "TIMESTAMP"
    facts = json.loads(processed.to_json())
    assert (facts["Type"], facts["isAdjustedToUTC"], facts["timeUnit"]) == ("Timestamp", True, "microseconds")


def test_t02_parts_read_back_with_duckdb_and_keep_nested_and_presence_values(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    root = env.tmp_path / "silver" / canonical_release_root(1, manifest.release_id)
    con = duckdb.connect()

    def paths(table: str) -> list[str]:
        return [str(root / item.path) for item in manifest.table(table).files]

    for table in manifest.tables:
        assert con.execute(f"select count(*) from read_parquet({paths(table.table)!r})").fetchone()[0] == table.row_count
    presence = dict(con.execute(f"select work_id, authorships_presence from read_parquet({paths('works')!r})").fetchall())
    assert (presence["W1"], presence["W2"], presence["W3"]) == ("PRESENT", "MISSING", "EMPTY")
    institutions = con.execute(
        f"select authorship_index, institution_index, institution_id from read_parquet({paths('work_author_institutions')!r}) "
        "where work_id = 'W1' order by 1, 2").fetchall()
    assert institutions == [(0, 0, "I1"), (1, 0, "I1"), (1, 1, "I2")]
    grants = con.execute(f"select funder_id, award_id from read_parquet({paths('work_grants')!r}) "
                         "where work_id = 'W1' order by grant_index").fetchall()
    assert grants == [("F1", "AWARD-1"), (None, None)]


def test_t02_nested_source_arrays_round_trip_to_relationship_rows(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    source = rich_work()

    def rows(table: str, *columns: str, order: str) -> list[tuple[Any, ...]]:
        found = sorted(_rows(silver, manifest, table, "W1"), key=lambda row: row[order])
        return [tuple(row[column] for column in columns) for row in found]

    assert rows("work_authors", "authorship_index", "author_id", "author_position", order="authorship_index") == [
        (index, item["author"]["id"].rsplit("/", 1)[1], item["author_position"])
        for index, item in enumerate(source["authorships"])
    ]
    assert rows("work_author_institutions", "authorship_index", "institution_index", "institution_id",
                order="institution_index") == [(0, 0, "I1"), (1, 0, "I1"), (1, 1, "I2")]
    assert rows("work_locations", "location_index", "license", "version", "landing_page_url",
                order="location_index") == [(0, "cc-by", "publishedVersion", "https://example.org/w1"),
                                            (1, None, None, "https://repo.example.org/w1")]
    assert rows("work_mesh", "mesh_index", "descriptor_ui", "qualifier_ui", order="mesh_index") == [
        (0, "D000001", "Q000001"), (1, "D000002", None)]
    assert rows("work_keywords", "keyword_id", "score", order="keyword_id") == [("keywords/machine-learning", 0.5)]
    assert rows("work_topics", "topic_id", "score", order="topic_id") == [("T1", 0.9), ("T2", 0.4)]
    references = rows("work_references", "reference_index", "referenced_work_id", "raw_reference",
                      order="reference_index")
    assert [(index, work_id) for index, work_id, _ in references] == [(0, "W2"), (1, None), (2, None)]
    assert [raw for _, _, raw in references][1:] == ["not-a-work", ""]
    assert rows("work_grants", "grant_index", "funder_id", "award_id", order="grant_index") == [
        (0, "F1", "AWARD-1"), (1, None, None)]


# --- T06 schema and ID stability, null and missing values ---------------------------------------------


def test_t06_null_missing_and_empty_presence_survive_the_release(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    works = {row["work_id"]: row for row in _read_table(silver, manifest, "works").to_pylist()}
    presence_columns = [name for name in works["W1"] if name.endswith("_presence")]
    assert len(presence_columns) == 7
    assert all(works["W2"][name] == "MISSING" for name in presence_columns)
    assert works["W2"]["title"] is None and works["W2"]["doi"] is None
    assert works["W3"]["topics_presence"] == "NULL" and works["W3"]["authorships_presence"] == "EMPTY"
    for table in RELEASE_TABLE_NAMES:
        data = _read_table(silver, manifest, table)
        for field in release_table_schema(table):
            if not field.nullable:
                assert data.column(field.name).null_count == 0, (table, field.name)


def test_t06_ids_are_short_form_and_stable_across_releases(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    first = _build(env, silver).manifest
    _land_more(env, "a3")
    second = _build(env, silver, *N_INPUTS, "a3", build_run_id=RUN_N1, built_at=BUILT_N1,
                    parent_release_id=first.release_id).manifest
    works_n = {row["work_id"]: row for row in _read_table(silver, first, "works").to_pylist()}
    works_n1 = {row["work_id"]: row for row in _read_table(silver, second, "works").to_pylist()}
    for row in works_n1.values():
        assert re.fullmatch(r"W\d+", row["work_id"]) and row["work_id_url"] == f"https://openalex.org/{row['work_id']}"
    for table, prefix in (("authors", "A"), ("institutions", "I"), ("sources", "S"), ("topics", "T"), ("funders", "F")):
        id_column = f"{table[:-1]}_id"
        for row in _read_table(silver, second, table).to_pylist():
            assert re.fullmatch(rf"{prefix}\d+", row[id_column]) and row[f"{id_column}_url"].startswith("https://")
    for work_id in ("W2", "W4", "W5"):
        assert works_n[work_id] == works_n1[work_id]
        for table in ("work_authors", "work_author_institutions"):
            assert _rows(silver, first, table, work_id) == _rows(silver, second, table, work_id)
    assert works_n["W1"]["title"] != works_n1["W1"]["title"]


# --- T17 raw to Parquet counts, checksums and lineage (BigQuery leg out) ---------------------------------


@pytest.mark.parametrize("kind", ["local", "gcs_fake"])
def test_t17_rebuild_from_bronze_reproduces_release_bytes_and_counts(env: SimpleNamespace, kind: str) -> None:
    silver = _silver(kind, env.tmp_path)
    release = _build(env, silver)
    bronze_guard, silver_guard = Recording(env.bronze, refuse_puts=True), Recording(silver, refuse_puts=True)
    report = pub.reconcile_release(bronze=bronze_guard, silver=silver_guard, release_id=release.manifest.release_id)
    assert report.passed, report.mismatches
    assert len(report.files) == report.rebuilt_file_count == release.parts_written == 22
    assert all(item.matches for item in report.files)
    assert report.summary()["manifest_sha256"] == report.manifest_sha256 == release.manifest_sha256
    m = release.manifest
    works_inputs = [item.counts for item in m.inputs if item.entity == "works"]
    deletion_inputs = [item.counts for item in m.inputs if item.entity == "works-deletions"]
    for item in m.inputs:  # raw lines (gzip + newlines) = decoded records = sum of outcomes
        counts = item.counts
        decoded = counts.records if item.entity == "works" else counts.rows_seen  # type: ignore[union-attr]
        assert counts.raw_lines == decoded
    assert [c.raw_lines for c in (item.counts for item in m.inputs)] == [4, 3, 2]
    assert m.table("works").row_count == sum(c.inserted for c in works_inputs)  # type: ignore[union-attr]
    assert m.table("works").activity_state_counts == {"ACTIVE": 4, "DELETED": 1}
    assert m.deleted_work_count == sum(c.deleted for c in deletion_inputs)  # type: ignore[union-attr]
    assert (m.barrier_count, m.barrier_only_count) == (2, 1)
    assert m.barrier_only_count == m.barrier_count - m.deleted_work_count
    assert m.table("work_references").rows_of_deleted_works == 1  # W3's reference row is kept
    assert not bronze_guard.puts and not silver_guard.puts


_BUILD_SCRIPT = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from research_platform.canonical.openalex import publisher
from research_platform.config.models import StorageConfig
from research_platform.storage.local import LocalObjectStore
from tests.support.canonical_release_fixture import fixture_assets, land, local_config
root = Path(sys.argv[1])
bronze = LocalObjectStore(StorageConfig(backend="local", landing_path=root / "bronze"))
silver = LocalObjectStore(StorageConfig(backend="local", landing_path=root / "silver"))
assets = fixture_assets()
landed = land(bronze, local_config(root / "cfg", root / "bronze"), [assets[n] for n in ("a1", "d1", "a2")])
release = publisher.build_canonical_release(
    bronze=bronze, silver=silver, inputs=landed.specs_for("a1", "d1", "a2"), build_run_id=UUID(int=1),
    built_at=datetime(2026, 10, 2, tzinfo=UTC), rows_per_part=2, row_group_size=1)
print(release.manifest.release_id)
"""

_RECONCILE_SCRIPT = """
import json
import sys
from pathlib import Path
from research_platform.canonical.openalex import publisher
from research_platform.config.models import StorageConfig
from research_platform.storage.local import LocalObjectStore
root = Path(sys.argv[1])
bronze = LocalObjectStore(StorageConfig(backend="local", landing_path=root / "bronze"))
silver = LocalObjectStore(StorageConfig(backend="local", landing_path=root / "silver"))
report = publisher.reconcile_release(bronze=bronze, silver=silver, release_id=sys.argv[2])
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("psycopg", "duckdb")
                or m.startswith(("google.cloud.bigquery", "research_platform.persistence.postgres")))
print(json.dumps({"passed": report.passed, "mismatches": list(report.mismatches), "loaded": loaded}))
"""


def _python(code: str, seed: int, *args: str) -> str:
    environ = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", code, *args], cwd=REPO_ROOT, env=environ, capture_output=True,
                          text=True, timeout=180, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip().splitlines()[-1]


def test_t17_reconcile_in_fresh_processes_with_other_hash_seeds(tmp_path: Path) -> None:
    release_id = _python(_BUILD_SCRIPT, 0, str(tmp_path))
    for seed in (1, 2, 3, 4):
        outcome = json.loads(_python(_RECONCILE_SCRIPT, seed, str(tmp_path), release_id))
        assert outcome == {"passed": True, "mismatches": [], "loaded": []}, seed


def test_t17_fixture_release_content_fingerprint_is_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    counter = itertools.count(1)
    monkeypatch.setattr(pipeline, "uuid4", lambda: UUID(int=next(counter)))
    monkeypatch.setattr(deletion_pipeline, "uuid4", lambda: UUID(int=next(counter)))
    bronze = _local(tmp_path / "bronze")
    assets = fixture_assets()
    landed = land(bronze, local_config(tmp_path / "cfg", tmp_path / "bronze"), [assets[n] for n in N_INPUTS])
    release = pub.build_canonical_release(bronze=bronze, silver=_local(tmp_path / "silver"),
                                          inputs=landed.specs_for(*N_INPUTS), build_run_id=RUN_N,
                                          built_at=BUILT_N, **SMALL)
    assert release.manifest.content_fingerprint == GOLDEN_FIXTURE_CONTENT_FINGERPRINT


def test_fixture_bronze_bytes_are_pinned() -> None:
    payloads = {name: asset.payload for name, asset in fixture_assets().items()}
    assert all(payload[:10] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff" for payload in payloads.values())
    assert {name: _sha(payload) for name, payload in payloads.items()} == GOLDEN_FIXTURE_PAYLOAD_SHA256


def test_t17_release_equals_the_ingestion_pipeline_state(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    expected = snapshot_to_arrow(env.landed.canonical.snapshot())
    for table in manifest.tables:
        got = _read_table(silver, manifest, table.table)
        if table.table == "work_deletion_barriers":
            barriers = sorted(env.landed.canonical.deletion_barriers().items())
            assert got.column("work_id").to_pylist() == [work_id for work_id, _ in barriers]
            assert got.column("deleted_date").to_pylist() == [deleted for _, deleted in barriers]
        else:
            assert got.equals(expected[table.table]), table.table


def test_t17_input_outcome_counts_equal_the_pipeline_stats(env: SimpleNamespace) -> None:
    manifest = _build(env, _silver("local", env.tmp_path)).manifest
    for name, item in zip(N_INPUTS, manifest.inputs, strict=True):
        stats, counts = env.landed.stats[name], item.counts
        if item.entity == "works":
            assert (counts.inserted, counts.identical, counts.replaced, counts.stale) == (  # type: ignore[union-attr]
                stats.works_inserted, stats.works_identical, stats.works_replaced, stats.works_stale), name
        else:
            assert (counts.rows_seen, counts.unique_ids, counts.duplicates, counts.deleted,  # type: ignore[union-attr]
                    counts.already_deleted, counts.unknown, counts.stale) == (  # type: ignore[union-attr]
                stats.rows_seen, stats.unique_ids, stats.duplicates, stats.deleted, stats.already_deleted,
                stats.unknown, stats.stale), name


def test_t17_raw_line_counts_are_independent_of_the_decoder(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = rebuild.iter_jsonl_gz_records

    def drop_last(stream: Any, **kwargs: Any) -> Any:
        records = list(original(stream, **kwargs))
        yield from records[:-1]

    silver = Recording(_silver("local", env.tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(rebuild, "iter_jsonl_gz_records", drop_last)
        with pytest.raises(ReleaseRebuildError):
            _build(env, silver)
    assert silver.puts == []
    release = _build(env, silver)
    with monkeypatch.context() as patch:
        patch.setattr(rebuild, "iter_jsonl_gz_records", drop_last)
        with pytest.raises(ReleaseRebuildError):
            pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=release.manifest.release_id)


def test_t17_raw_line_counts_are_independent_of_the_deletion_decoder(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = rebuild.iter_deleted_work_records

    def drop_last(stream: Any, **kwargs: Any) -> Any:
        yield from list(original(stream, **kwargs))[:-1]

    silver = Recording(_silver("local", env.tmp_path))
    monkeypatch.setattr(rebuild, "iter_deleted_work_records", drop_last)
    with pytest.raises(ReleaseRebuildError):
        _build(env, silver)
    assert silver.puts == []


@pytest.mark.parametrize("chunk", [1, 7, 65_536])
def test_raw_line_counter_matches_the_decoders_on_chunked_multi_member_gzip(chunk: int) -> None:
    def count(payload: bytes) -> int:
        counter = rebuild._RawLineCounter()
        for start in range(0, len(payload), chunk):
            counter.feed(payload[start : start + chunk])
        return counter.finish()

    lines = [json.dumps({"id": f"https://openalex.org/W{n}", "pad": "x" * (n * 37 % 400)}) for n in range(1, 400)]
    first = ("\n".join(lines[:200]) + "\n\n  \t\n").encode()  # blank and whitespace-only lines
    second = "\r\n".join(lines[200:]).encode()  # CRLF, no final newline
    works = gzip.compress(first, mtime=0) + gzip.compress(second, mtime=0)  # two members, over 64 KiB inflated
    decoded = sum(1 for _ in rebuild.iter_jsonl_gz_records(io.BytesIO(works), limits=JsonlDecodeLimits()))
    assert count(works) == decoded == 399
    deletions = gzip.compress(b"work_id,deleted_date\nW1,2026-09-02\n\nW2,2026-09-02\n", mtime=0)
    rows = sum(1 for _ in rebuild.iter_deleted_work_records(io.BytesIO(deletions), limits=CsvDeletionDecodeLimits()))
    assert count(deletions) - 1 == rows == 2  # the header line
    assert count(b"") == 0  # an empty object decodes to no records
    truncated = rebuild._RawLineCounter()
    truncated.feed(works[: len(works) // 2])
    with pytest.raises(ValueError):
        truncated.finish()


def test_an_empty_bronze_object_replays_as_no_records(env: SimpleNamespace) -> None:
    run_id, landed_at = UUID(int=78), datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
    _hand_land(env.bronze, "works", "oa-hand-empty", b"", run_id=run_id, retrieved_at=landed_at,
               updated=date(2026, 9, 3))
    spec = rebuild.ReleaseInputSpec(entity="works", run_id=run_id, asset_id="oa-hand-empty",
                                    processed_at=landed_at + timedelta(seconds=1))
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver, inputs=[spec]).manifest
    assert (manifest.inputs[0].counts.raw_lines, manifest.table("works").row_count) == (0, 0)
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id).passed


def test_reconcile_replays_with_the_recorded_decode_limits(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    works_limits = JsonlDecodeLimits(max_decompressed_bytes=1_000_000, max_records=10, max_record_bytes=65_536)
    deletion_limits = CsvDeletionDecodeLimits(max_decompressed_bytes=1_000_000, max_ids=10, max_row_bytes=256)
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver, works_decode_limits=works_limits, deletion_decode_limits=deletion_limits).manifest
    assert (manifest.decode_limits.works, manifest.decode_limits.works_deletions) == (works_limits, deletion_limits)
    seen: list[Any] = []
    for name in ("iter_jsonl_gz_records", "iter_deleted_work_records"):
        original = getattr(rebuild, name)

        def spy(stream: Any, *, limits: Any, _original: Any = original) -> Any:
            seen.append(limits)
            return _original(stream, limits=limits)

        monkeypatch.setattr(rebuild, name, spy)
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id).passed
    assert seen == [works_limits, deletion_limits, works_limits]


def test_t17_rebuild_reads_bronze_through_gcs_fake(tmp_path: Path) -> None:
    client = FakeGCSClient()
    bronze = _gcs(BRONZE_BUCKET, client)
    config = tmp_path / "gcs.yaml"
    config.write_text(yaml.safe_dump({
        "environment": "gcp-sandbox", "storage": {"backend": "gcs", "bucket": BRONZE_BUCKET},
        "cloud": {"project_id": PROJECT}, "warehouse": {"analytical": "duckdb"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": 1_000_000},
    }), encoding="utf-8")
    assets = fixture_assets()
    landed = land(bronze, config, [assets[n] for n in N_INPUTS], allow_gcs_landing=True)
    assert any(key.startswith("manifests/openalex/") for key in client.bucket(BRONZE_BUCKET).objects)
    silver = _gcs(SILVER_BUCKET, client)
    release = pub.build_canonical_release(bronze=bronze, silver=silver, inputs=landed.specs_for(*N_INPUTS),
                                          build_run_id=RUN_N, built_at=BUILT_N, **SMALL)
    assert pub.reconcile_release(bronze=bronze, silver=silver, release_id=release.manifest.release_id).passed


def test_t17_release_lineage_links_rows_to_landing_manifests(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    by_asset = {item.asset_id: item for item in manifest.inputs}
    for item in manifest.inputs:
        with env.bronze.open(item.landing_manifest_key) as handle:
            landing = handle.read()
        with env.bronze.open(item.raw_object_key) as handle:
            raw = handle.read()
        assert _sha(landing) == item.landing_manifest_sha256
        assert json.loads(landing)["entry"]["sha256"] == item.raw_sha256 == _sha(raw)
    for table in RELEASE_TABLE_NAMES[:-1]:
        for row in _read_table(silver, manifest, table).to_pylist():
            item = by_asset[row["source_asset_id"]]
            assert row["source_checksum_sha256"] == item.raw_sha256
            assert row["run_id"] == str(item.run_id)
            assert row["processed_at"] == item.processed_at


def _swap_outcome_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    original = rebuild._replay_works

    def swapped(*args: Any) -> Any:
        counts = original(*args)
        return counts.model_copy(update={"identical": counts.stale, "stale": counts.identical})

    monkeypatch.setattr(rebuild, "_replay_works", swapped)


def _patch_unknown_deletions(monkeypatch: pytest.MonkeyPatch, change: Callable[..., Any]) -> None:
    original = InMemoryCanonicalStore.apply_work_deletion

    def patched(self: InMemoryCanonicalStore, work_id: str, **kwargs: Any) -> DeletionOutcome:
        if self.get_work(work_id) is None:
            return change(original, self, work_id, kwargs)
        return original(self, work_id, **kwargs)

    monkeypatch.setattr(InMemoryCanonicalStore, "apply_work_deletion", patched)


def _ignore_unknown_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_unknown_deletions(monkeypatch, lambda original, store, work_id, kwargs: DeletionOutcome.UNKNOWN_WORK)


def _ignore_known_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    original = InMemoryCanonicalStore.apply_work_deletion

    def patched(self: InMemoryCanonicalStore, work_id: str, **kwargs: Any) -> DeletionOutcome:
        if self.get_work(work_id) is not None:
            return DeletionOutcome.UNKNOWN_WORK
        return original(self, work_id, **kwargs)

    monkeypatch.setattr(InMemoryCanonicalStore, "apply_work_deletion", patched)


def _shift_barrier_date(monkeypatch: pytest.MonkeyPatch) -> None:
    def shift(original: Callable[..., Any], store: Any, work_id: str, kwargs: dict[str, Any]) -> Any:
        return original(store, work_id, **{**kwargs, "deleted_date": kwargs["deleted_date"] + timedelta(days=1)})

    _patch_unknown_deletions(monkeypatch, shift)


@pytest.mark.parametrize(
    ("drift", "expected"),
    [
        ("count_logic", ("input_outcome_counts",)),
        # Without the barrier, a2's stale W9 is inserted too, so Work and outcome counts move as well.
        ("unknown_deletion_ignored", ("part_files", "table_row_counts", "activity_state_counts", "barrier_count",
                                      "barrier_only_count", "input_outcome_counts", "content_fingerprint")),
        ("barrier_date_shift", ("part_files", "content_fingerprint")),
        # W3 stays ACTIVE without a barrier, so its tombstone and its relationship rows' count move.
        ("known_deletion_ignored", ("part_files", "table_row_counts", "activity_state_counts", "rows_of_deleted_works",
                                    "barrier_count", "input_outcome_counts", "content_fingerprint")),
    ],
)
def test_t17_reconcile_reports_exactly_the_mismatch_codes_of_each_drift(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, drift: str, expected: tuple[str, ...]
) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    {"count_logic": _swap_outcome_counts, "unknown_deletion_ignored": _ignore_unknown_deletion,
     "barrier_date_shift": _shift_barrier_date, "known_deletion_ignored": _ignore_known_deletion}[drift](monkeypatch)
    report = pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id)
    assert report.mismatches == expected


def test_t17_code_drift_is_reported_as_a_mismatch(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    original = rebuild.map_openalex_work

    def drift(record: Any, *, lineage: Any) -> Any:
        bundle = original(record, lineage=lineage)
        return bundle.model_copy(update={"work": bundle.work.model_copy(update={"title": "drift"})})

    monkeypatch.setattr(rebuild, "map_openalex_work", drift)
    report = pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id)
    assert report.mismatches == ("part_files", "content_fingerprint") and not report.passed
    with pytest.raises(ReconciliationMismatchError) as raised:
        report.raise_for_mismatch()
    assert "W1" not in str(raised.value) and "drift" not in str(raised.value)
    assert set(report.environment) == {"recorded", "current"}
    assert report.environment["recorded"]["tools"] == manifest.tools.model_dump(mode="json")
    assert report.environment["current"]["tools"]["pyarrow"] == pa.__version__


@pytest.mark.parametrize(
    "tamper",
    ["decodable_other_rows", "undecodable", "truncated", "landing_manifest", "missing_raw", "on_disk_flip_local",
     "on_disk_flip_gcs_fake"],
)
def test_t17_tampered_bronze_input_fails_closed(env: SimpleNamespace, tamper: str) -> None:
    bronze: ObjectStore = env.bronze
    client = FakeGCSClient()
    if tamper == "on_disk_flip_gcs_fake":
        bronze = _gcs(BRONZE_BUCKET, client)
        config = env.tmp_path / "gcs.yaml"
        config.write_text(yaml.safe_dump({
            "environment": "gcp-sandbox", "storage": {"backend": "gcs", "bucket": BRONZE_BUCKET},
            "cloud": {"project_id": PROJECT}, "warehouse": {"analytical": "duckdb"},
            "sample_selection": {"max_files": 1, "max_file_size_bytes": 1_000_000}}), encoding="utf-8")
        env = SimpleNamespace(**{**vars(env), "bronze": bronze,
                                 "landed": land(bronze, config, [env.assets[n] for n in N_INPUTS],
                                                allow_gcs_landing=True)})
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    works, deletions = manifest.inputs[0], manifest.inputs[1]
    replacements: dict[str, bytes | None] = {}
    if tamper == "decodable_other_rows":
        replacements[deletions.raw_object_key] = gzip.compress(b"work_id,deleted_date\nW3,2026-09-02\n", mtime=0)
    elif tamper == "undecodable":
        replacements[works.raw_object_key] = b"not a gzip stream"
    elif tamper == "truncated":
        with env.bronze.open(works.raw_object_key) as handle:
            replacements[works.raw_object_key] = handle.read()[:-12]
    elif tamper == "landing_manifest":
        with env.bronze.open(works.landing_manifest_key) as handle:
            replacements[works.landing_manifest_key] = json.dumps(json.loads(handle.read()), indent=1).encode()
    elif tamper == "missing_raw":
        replacements[deletions.raw_object_key] = None
    elif tamper == "on_disk_flip_local":
        _flip_local(env.tmp_path / "bronze" / works.raw_object_key)
    else:
        _flip_fake(client, BRONZE_BUCKET, works.raw_object_key)
    with pytest.raises(ReleaseInputError):
        pub.reconcile_release(bronze=Swap(env.bronze, replacements), silver=silver, release_id=manifest.release_id)


@pytest.mark.parametrize("drift", ["pyarrow_version", "parquet_options"])
def test_environment_drift_refuses_byte_reconciliation(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    if drift == "pyarrow_version":
        monkeypatch.setattr(pub.pa, "__version__", "0.0.0")
    else:
        monkeypatch.setattr(pub, "PARQUET_WRITE_OPTIONS",
                            MappingProxyType({**pub.PARQUET_WRITE_OPTIONS, "compression": "zstd"}))
    with pytest.raises(ReleaseEnvironmentMismatchError):
        pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id)


# --- idempotent replay --------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["local", "gcs_fake"])
def test_identical_release_replay_writes_nothing(env: SimpleNamespace, kind: str) -> None:
    silver = Recording(_silver(kind, env.tmp_path))
    first = _build(env, silver)
    assert first.outcome == "written" and silver.puts[-1] == first.manifest_key
    assert len(silver.puts) == first.parts_written + 1
    puts = len(silver.puts)
    again = _build(env, silver)
    assert again.outcome == "existing_kept" and len(silver.puts) == puts and again.manifest == first.manifest
    assert again.manifest_sha256 == first.manifest_sha256
    plus_two = timezone(timedelta(hours=2))
    shifted = [dataclasses.replace(spec, processed_at=spec.processed_at.astimezone(plus_two))
               for spec in env.landed.specs_for(*N_INPUTS)]
    assert _build(env, silver, inputs=shifted).outcome == "existing_kept" and len(silver.puts) == puts


@pytest.mark.parametrize("kind", ["local", "gcs_fake"])
def test_interrupted_release_is_invisible_then_resumes_under_the_same_build_run(
    env: SimpleNamespace, kind: str
) -> None:
    silver = Recording(_silver(kind, env.tmp_path), fail_after=5)
    with pytest.raises(ConnectionError):
        _build(env, silver)
    release_id = release_id_for_build(RUN_N, BUILT_N)
    with pytest.raises(ReleaseNotFoundError):
        pub.read_release_manifest(silver, release_id)
    with pytest.raises(ReleaseNotFoundError):
        pub.validate_release(silver, release_id)
    silver.fail_after = None
    done = _build(env, silver)
    assert done.outcome == "written" and silver.puts[-1] == done.manifest_key
    assert done.parts_written == 22 and len(silver.puts) == 5 + done.parts_written + 1  # 5 identical no-op replays
    assert pub.validate_release(silver, release_id).manifest == done.manifest


def test_existing_manifest_with_a_missing_part_is_not_kept(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    release = _build(env, silver)
    part_key = canonical_part_key(1, release.manifest.release_id, "work_grants", 0)
    shutil.rmtree((env.tmp_path / "silver" / part_key).parent)
    puts = len(silver.puts)
    with pytest.raises(ReleasePartError):
        _build(env, silver)
    assert len(silver.puts) == puts


class _OtherProvenance(Recording):
    """Reports the first works part at its size but with another digest in its provenance."""

    def stat(self, key: str) -> ObjectStat | None:
        found = super().stat(key)
        if found is not None and found.provenance is not None and key.endswith("/works/part-00000/part-00000.parquet"):
            return dataclasses.replace(found, provenance=found.provenance.model_copy(update={"sha256": "0" * 64}))
        return found


def test_existing_manifest_with_a_part_of_other_provenance_is_not_kept(env: SimpleNamespace) -> None:
    silver = _OtherProvenance(_silver("local", env.tmp_path))
    release = _build(env, silver.inner)
    with pytest.raises(ReleasePartError):
        _build(env, silver)
    assert silver.puts == [] and pub.validate_release(silver.inner, release.manifest.release_id)


def test_build_normalizes_an_offset_built_at_to_utc(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    first = _build(env, silver, built_at=BUILT_N.astimezone(timezone(timedelta(hours=2))))
    assert first.outcome == "written" and first.manifest.built_at == BUILT_N
    assert first.manifest.built_at.utcoffset() == timedelta(0)
    puts = len(silver.puts)
    assert _build(env, silver).outcome == "existing_kept" and len(silver.puts) == puts


@pytest.mark.parametrize("drift", ["mapping", "outcome_counts"])
def test_identical_request_after_code_drift_is_a_conflict_not_existing_kept(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    first = _build(env, silver)
    puts = len(silver.puts)
    if drift == "mapping":
        original = rebuild.map_openalex_work

        def renamed(record: Any, *, lineage: Any) -> Any:
            bundle = original(record, lineage=lineage)
            return bundle.model_copy(update={"work": bundle.work.model_copy(update={"title": "drift"})})

        monkeypatch.setattr(rebuild, "map_openalex_work", renamed)
    else:  # parts unchanged, only the recorded outcome counts move
        _swap_outcome_counts(monkeypatch)
    with pytest.raises(ReleaseConflictError):
        _build(env, silver)
    assert len(silver.puts) == puts
    assert pub.validate_release(silver, first.manifest.release_id).manifest == first.manifest


def test_resumed_build_refuses_parts_it_did_not_render(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    silver = Recording(_silver("local", env.tmp_path), fail_after=3)  # works parts 0-2 (W1-W5, two rows each)
    with pytest.raises(ConnectionError):
        _build(env, silver)
    silver.fail_after = None
    replay = pub.replay_bronze_inputs

    def without_w5(*args: Any, **kwargs: Any) -> Any:  # code drift: one part fewer, same intent
        state = replay(*args, **kwargs)
        works = tuple(work for work in state.snapshot.works if work.work_id != "W5")
        return dataclasses.replace(state, snapshot=dataclasses.replace(state.snapshot, works=works))

    monkeypatch.setattr(pub, "replay_bronze_inputs", without_w5)
    with pytest.raises(ReleaseConflictError):
        _build(env, silver)
    assert not any(key.endswith("manifest.json") for key in silver.puts)
    with pytest.raises(ReleaseNotFoundError):
        pub.read_release_manifest(silver, release_id_for_build(RUN_N, BUILT_N))


# --- negative ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["local", "gcs_fake"])
def test_conflicting_release_with_the_same_id_is_refused_and_keeps_the_original(
    env: SimpleNamespace, kind: str
) -> None:
    silver = Recording(_silver(kind, env.tmp_path))
    first = _build(env, silver)
    puts = len(silver.puts)
    bronze = Recording(env.bronze)
    with pytest.raises(ReleaseConflictError):
        _build(env, silver, "a1", "d1", bronze=bronze)
    assert len(silver.puts) == puts
    raw_keys = {item.raw_object_key for item in first.manifest.inputs}
    assert not raw_keys & set(bronze.opens)  # refused before any replay
    assert pub.read_release_manifest(silver, first.manifest.release_id) == first.manifest
    assert pub.validate_release(silver, first.manifest.release_id).manifest_sha256 == first.manifest_sha256


def test_retry_of_a_partial_release_with_other_inputs_is_refused_and_stays_invisible(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path), fail_after=3)
    with pytest.raises(ConnectionError):
        _build(env, silver)
    assert len(silver.puts) == 3
    silver.fail_after = None
    with pytest.raises(ReleaseConflictError):
        _build(env, silver, "a1", "d1")
    assert len(silver.puts) == 3
    release_id = release_id_for_build(RUN_N, BUILT_N)
    with pytest.raises(ReleaseNotFoundError):
        pub.read_release_manifest(silver, release_id)
    resumed = _build(env, silver)  # the original request still completes
    assert resumed.outcome == "written" and pub.validate_release(silver, release_id).manifest == resumed.manifest


def test_same_build_run_under_other_tool_versions_is_a_conflict(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    _build(env, silver)
    puts = len(silver.puts)
    current = pub.tool_versions()
    monkeypatch.setattr(pub, "tool_versions", lambda: current.model_copy(update={"python": "3.99.0"}))
    with pytest.raises(ReleaseConflictError):
        _build(env, silver)
    assert len(silver.puts) == puts


@pytest.mark.parametrize("corruption", ["store_integrity_local", "store_integrity_gcs_fake", "manifest_digest"])
def test_corrupted_part_is_detected_on_validate(env: SimpleNamespace, corruption: str) -> None:
    client = FakeGCSClient()
    silver = _silver("gcs_fake" if corruption == "store_integrity_gcs_fake" else "local", env.tmp_path, client)
    manifest = _build(env, silver).manifest
    part_key = canonical_part_key(1, manifest.release_id, "works", 0)
    reader: ObjectStore = silver
    if corruption == "store_integrity_local":
        _flip_local(env.tmp_path / "silver" / part_key)
    elif corruption == "store_integrity_gcs_fake":
        _flip_fake(client, SILVER_BUCKET, part_key)
    else:
        data = bytearray(_part(silver, manifest, "works"))
        data[40] ^= 0xFF
        reader = Swap(silver, {part_key: bytes(data)})
    with pytest.raises(ReleasePartError):
        pub.validate_release(reader, manifest.release_id)


class _RacingManifest(Recording):
    """Another writer commits the manifest key between the read and the put."""

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        if key.endswith("/manifest.json"):
            raise ObjectConflictError("object already exists")
        super().put_if_absent(key, content, provenance)


def test_racing_manifest_write_is_a_typed_conflict(env: SimpleNamespace) -> None:
    silver = _RacingManifest(_silver("local", env.tmp_path))
    with pytest.raises(ReleaseConflictError):
        _build(env, silver)
    assert len(silver.puts) == 22


def test_rows_per_part_below_the_part_index_limit_is_refused_before_any_put(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pub, "MAX_PART_INDEX", 2)  # works has 5 rows: 5 parts of 1 row exceed parts 0-2
    silver = Recording(_silver("local", env.tmp_path))
    with pytest.raises(ReleaseConfigError):
        _build(env, silver, rows_per_part=1, row_group_size=1)
    assert silver.puts == []
    assert _build(env, silver, rows_per_part=2, row_group_size=1).outcome == "written"


def test_missing_part_or_manifest_is_never_visible(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path), fail_after=22)  # all 22 parts, no manifest
    with pytest.raises(ConnectionError):
        _build(env, silver)
    release_id = release_id_for_build(RUN_N, BUILT_N)
    with pytest.raises(ReleaseNotFoundError):
        pub.validate_release(silver, release_id)
    silver.fail_after = None
    release = _build(env, silver)
    part_key = canonical_part_key(1, release_id, "authors", 0)
    shutil.rmtree((env.tmp_path / "silver" / part_key).parent)
    with pytest.raises(ReleasePartError):
        pub.validate_release(silver, release.manifest.release_id)
    with pytest.raises(ReleasePartError):
        pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=release_id)


def test_build_refuses_unknown_duplicate_unordered_or_unlanded_inputs(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    specs = env.landed.specs
    unlanded = dataclasses.replace(specs["a2"], run_id=UUID(int=99))
    with pytest.raises(ReleaseInputError):
        _build(env, silver, inputs=[specs["a1"], specs["d1"], unlanded])
    refused: dict[str, Callable[[], Any]] = {
        "duplicate": lambda: _build(env, silver, inputs=[specs["a1"], specs["d1"], specs["a1"]]),
        "swapped_order": lambda: _build(env, silver, inputs=[specs["a1"], specs["a2"], specs["d1"]]),
        "equal_processed_at": lambda: _build(env, silver, inputs=[specs["a1"], dataclasses.replace(
            specs["d1"], processed_at=specs["a1"].processed_at)]),
        "before_landing": lambda: _build(env, silver, inputs=[dataclasses.replace(
            specs["a1"], processed_at=specs["a1"].processed_at - timedelta(days=1))]),
    }
    row = env.landed.control.get_source_file(env.landed.asset_ids["a1"])
    for status, processed_at in ((ControlStatus.FAILED, row.processed_at), (ControlStatus.PROCESSING, None)):
        other = row.model_copy(update={"status": status, "processed_at": processed_at})
        refused[f"control_{status.value}"] = lambda other=other: rebuild.release_input_from_source_file(other)
    tied = row.model_copy(update={"asset_id": "tie", "processed_at": row.processed_at})
    refused["tied_control_rows"] = lambda: rebuild.release_inputs_from_source_files([row, tied])
    for name, call in refused.items():
        with pytest.raises(ReleaseConfigError):
            call()
        assert silver.puts == [], name
    with pytest.raises(ReleaseConfigError, match="strictly increasing"):  # the resolve rule, not the model's
        refused["equal_processed_at"]()


def test_replay_failure_is_typed_and_publishes_nothing(env: SimpleNamespace) -> None:
    run_id, landed_at = UUID(int=77), datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
    payload = gzip.compress(b'{"id": "https://openalex.org/not-a-work-id"}\n', mtime=0)
    _hand_land(env.bronze, "works", "oa-hand-bad", payload, run_id=run_id, retrieved_at=landed_at,
               updated=date(2026, 9, 3))
    spec = rebuild.ReleaseInputSpec(entity="works", run_id=run_id, asset_id="oa-hand-bad",
                                    processed_at=landed_at + timedelta(seconds=1))
    silver = Recording(_silver("local", env.tmp_path))
    with pytest.raises(ReleaseRebuildError) as raised:
        _build(env, silver, inputs=[spec])
    assert type(raised.value) is ReleaseRebuildError and silver.puts == []


def test_replay_refuses_a_conflicting_duplicate_deletion_date_like_the_pipeline(env: SimpleNamespace) -> None:
    asset = deletion_asset("dx", date(2026, 9, 3), [("W2", date(2026, 9, 2)), ("W2", date(2026, 9, 3))])
    try:
        result = run_openalex_deletions_local_ingest(
            env.config, backend="memory", deletion_asset=asset.metadata, control_store=env.landed.control,
            canonical_store=env.landed.canonical, object_store=env.bronze, connector=FixtureConnector(asset),
            now=env.landed.clock)
        assert result.run.status is PipelineRunStatus.FAILED
    except IngestionError:
        pass
    row = env.landed.control.get_source_file(asset.metadata.asset_id)
    assert row.status is ControlStatus.FAILED  # the pipeline refused the ledger
    spec = rebuild.ReleaseInputSpec(entity="works-deletions", run_id=row.run_id, asset_id=row.asset_id,
                                    processed_at=env.landed.clock())
    resolved = rebuild.resolve_bronze_inputs(env.bronze, [spec])  # its Bronze object did land
    with pytest.raises(ReleaseRebuildError):
        rebuild.replay_bronze_inputs(env.bronze, resolved, works_limits=JsonlDecodeLimits(),
                                     deletion_limits=CsvDeletionDecodeLimits())


def test_render_refuses_broken_barrier_invariants(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    real_snapshot, real_barriers = InMemoryCanonicalStore.snapshot, InMemoryCanonicalStore.deletion_barriers

    def deleted_without_barrier(self: InMemoryCanonicalStore) -> Any:
        snap = real_snapshot(self)
        works = tuple(
            work.model_copy(update={"lineage": work.lineage.model_copy(
                update={"activity_state": CanonicalActivityState.DELETED})}) if work.work_id == "W4" else work
            for work in snap.works)
        return dataclasses.replace(snap, works=works)

    def barrier_on_active(self: InMemoryCanonicalStore) -> dict[str, date]:
        return {**real_barriers(self), "W4": date(2026, 9, 2)}

    for method, patched in (("snapshot", deleted_without_barrier), ("deletion_barriers", barrier_on_active)):
        with monkeypatch.context() as patch:
            patch.setattr(InMemoryCanonicalStore, method, patched)
            with pytest.raises(ReleaseRebuildError):
                _build(env, silver)
        assert silver.puts == [], method


def test_barrier_rows_are_sorted_by_work_id_whatever_the_ledger_order(tmp_path: Path) -> None:
    bronze = _local(tmp_path / "bronze")
    asset = deletion_asset("dz", date(2026, 9, 2), [("W9", date(2026, 9, 2)), ("W10", date(2026, 9, 2)),
                                                    ("W3", date(2026, 9, 2))])
    landed = land(bronze, local_config(tmp_path / "cfg", tmp_path / "bronze"), [asset])
    silver = _local(tmp_path / "silver")
    manifest = pub.build_canonical_release(bronze=bronze, silver=silver, inputs=landed.specs_for("dz"),
                                           build_run_id=RUN_N, built_at=BUILT_N, **SMALL).manifest
    ids = _read_table(silver, manifest, "work_deletion_barriers").column("work_id").to_pylist()
    assert ids == ["W10", "W3", "W9"] == sorted(ids)


# --- rollback and prior-release visibility ------------------------------------------------------------------


def _object_bytes(silver: ObjectStore, manifest: CanonicalReleaseManifest) -> dict[str, bytes]:
    root = canonical_release_root(1, manifest.release_id)
    keys = [manifest.key, *(f"{root}/{item.path}" for table in manifest.tables for item in table.files)]
    found = {}
    for key in keys:
        with silver.open(key) as handle:
            found[key] = handle.read()
    return found


def test_next_release_lands_beside_prior_release_and_both_validate(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    prior = _build(env, silver).manifest
    before = _object_bytes(silver, prior)
    _land_more(env, "a3")
    following = _build(env, silver, *N_INPUTS, "a3", build_run_id=RUN_N1, built_at=BUILT_N1,
                       parent_release_id=prior.release_id).manifest
    assert pub.validate_release(silver, prior.release_id).manifest == prior
    assert pub.validate_release(silver, following.release_id).manifest == following
    assert _object_bytes(silver, prior) == before
    assert following.parent_release_id == prior.release_id and following.inputs[-1].counts.replaced == 1  # type: ignore[union-attr]


def test_prior_release_stays_valid_after_a_failed_next_release(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    prior = _build(env, silver).manifest
    _land_more(env, "a3")
    with env.bronze.open(openalex_landing_manifest_key(
            "works", env.landed.specs["a3"].run_id, env.landed.specs["a3"].asset_id)) as handle:
        raw_key = json.loads(handle.read())["entry"]["raw_object_key"]
    tampered = Swap(env.bronze, {raw_key: gzip.compress(b'{"id": "https://openalex.org/W1"}\n', mtime=0)})
    with pytest.raises(ReleaseInputError):
        _build(env, silver, *N_INPUTS, "a3", bronze=tampered, build_run_id=RUN_N1, built_at=BUILT_N1,
               parent_release_id=prior.release_id)
    assert pub.validate_release(silver, prior.release_id).manifest == prior
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=prior.release_id).passed
    with pytest.raises(ReleaseNotFoundError):
        pub.read_release_manifest(silver, release_id_for_build(RUN_N1, BUILT_N1))


def test_prior_release_stays_valid_beside_a_partial_next_release(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    prior = _build(env, silver).manifest
    _land_more(env, "a3")
    silver.fail_after = len(silver.puts) + 5
    with pytest.raises(ConnectionError):
        _build(env, silver, *N_INPUTS, "a3", build_run_id=RUN_N1, built_at=BUILT_N1,
               parent_release_id=prior.release_id)
    following = release_id_for_build(RUN_N1, BUILT_N1)
    assert sum(1 for key in silver.puts if following in key) == 5
    silver.fail_after = None
    assert pub.validate_release(silver, prior.release_id).manifest == prior
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=prior.release_id).passed
    with pytest.raises(ReleaseNotFoundError):
        pub.validate_release(silver, following)


def test_prior_release_still_reconciles_after_the_next_release_lands(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    prior = _build(env, silver).manifest
    _land_more(env, "a3")
    _build(env, silver, *N_INPUTS, "a3", build_run_id=RUN_N1, built_at=BUILT_N1, parent_release_id=prior.release_id)
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=prior.release_id).passed
    barriers = _read_table(silver, prior, "work_deletion_barriers").column("deleted_date").to_pylist()
    assert date(2026, 9, 2) in barriers


def test_barrier_in_release_n_still_blocks_a_stale_active_work_in_n_plus_1_and_after_rebuild(
    env: SimpleNamespace,
) -> None:
    silver = _silver("local", env.tmp_path)
    prior = _build(env, silver).manifest
    _land_more(env, "a3")
    following = _build(env, silver, *N_INPUTS, "a3", build_run_id=RUN_N1, built_at=BUILT_N1,
                       parent_release_id=prior.release_id).manifest
    for manifest in (prior, following):
        works = set(_read_table(silver, manifest, "works").column("work_id").to_pylist())
        barriers = set(_read_table(silver, manifest, "work_deletion_barriers").column("work_id").to_pylist())
        assert "W9" not in works and "W9" in barriers
    assert following.inputs[2].counts.stale == 1  # type: ignore[union-attr]
    assert pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=following.release_id).passed


def test_parent_must_be_an_older_readable_release_whose_inputs_are_a_prefix(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    prior = _build(env, silver).manifest
    newer = _build(env, silver, "a1", build_run_id=UUID(int=5), built_at=BUILT_N1 + timedelta(days=1)).manifest
    _land_more(env, "a3")
    specs = env.landed.specs
    puts = len(silver.puts)
    child = {"build_run_id": RUN_N1, "built_at": BUILT_N1}
    cases: list[tuple[type[Exception], dict[str, Any]]] = [
        (ReleaseConfigError, {"inputs": [specs["a1"], specs["a2"], specs["d1"], specs["a3"]],
                              "parent_release_id": prior.release_id}),
        (ReleaseParentError, {"inputs": [specs["a1"], specs["a2"], specs["a3"]], "parent_release_id": prior.release_id}),
        (ReleaseParentError, {"inputs": env.landed.specs_for(*N_INPUTS, "a3"), "parent_release_id": newer.release_id}),
        (ReleaseParentError, {"inputs": env.landed.specs_for(*N_INPUTS, "a3"), "parent_release_id": prior.release_id,
                              "built_at": BUILT_N, "build_run_id": UUID(int=6)}),
        (ReleaseParentError, {"inputs": env.landed.specs_for(*N_INPUTS, "a3"),
                              "parent_release_id": release_id_for_build(UUID(int=7), BUILT_N)}),
    ]
    for error, overrides in cases:
        with pytest.raises(error):
            _build(env, silver, **{**child, **overrides})
        assert len(silver.puts) == puts, overrides
    monkeypatch.setattr(pub, "release_schema_fingerprints", lambda: {name: "0" * 64 for name in RELEASE_TABLE_NAMES})
    with pytest.raises(ReleaseSchemaMismatchError):
        _build(env, silver, *N_INPUTS, "a3", parent_release_id=prior.release_id, **child)
    assert len(silver.puts) == puts


# --- publication records -------------------------------------------------------------------------------------


def _record(manifest: CanonicalReleaseManifest, manifest_sha256: str, **overrides: Any) -> PublicationRecord:
    fields: dict[str, Any] = {
        "sequence": 1, "release_id": manifest.release_id, "schema_major": manifest.schema_major,
        "manifest_path": canonical_manifest_path_from_root(manifest.schema_major, manifest.release_id),
        "manifest_sha256": manifest_sha256, "published_at": BUILT_N1, "previous_release_id": None,
        "publication_run_id": UUID(int=9), **overrides,
    }
    return PublicationRecord(**fields)


def test_publication_record_is_create_only_and_names_a_complete_release(env: SimpleNamespace) -> None:
    silver = Recording(_silver("local", env.tmp_path))
    release = _build(env, silver)
    record = _record(release.manifest, release.manifest_sha256)
    assert pub.write_publication_record(silver, record) == "written"
    assert silver.puts[-1] == canonical_publication_record_key(1, release.manifest.release_id)
    assert pub.read_publication_record(silver, 1, release.manifest.release_id) == record
    puts = len(silver.puts)
    assert pub.write_publication_record(silver, record) == "existing_kept" and len(silver.puts) == puts
    with pytest.raises(ReleaseConflictError):
        pub.write_publication_record(silver, record.model_copy(update={"publication_run_id": UUID(int=10)}))
    with pytest.raises(ReleaseNotFoundError):
        pub.write_publication_record(silver, _record(release.manifest, release.manifest_sha256, sequence=2,
                                                     release_id=release_id_for_build(RUN_N1, BUILT_N1),
                                                     manifest_path=canonical_manifest_path_from_root(
                                                         1, release_id_for_build(RUN_N1, BUILT_N1))))
    with pytest.raises(PublicationRecordError):
        pub.write_publication_record(silver, _record(release.manifest, "0" * 64, sequence=3))
    with pytest.raises(ValueError):
        _record(release.manifest, release.manifest_sha256, previous_release_id=release.manifest.release_id)
    with pytest.raises(PublicationRecordError):
        pub.read_publication_record(silver, 4, release.manifest.release_id)
    assert len(silver.puts) == puts


def test_publication_record_read_refuses_a_record_stored_under_another_key(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    release = _build(env, silver)
    release_id = release.manifest.release_id
    pub.write_publication_record(silver, _record(release.manifest, release.manifest_sha256))
    with silver.open(canonical_publication_record_key(1, release_id)) as handle:
        payload = handle.read()
    moved = Swap(silver, {canonical_publication_record_key(2, release_id): payload})
    with pytest.raises(PublicationRecordError, match="another key"):
        pub.read_publication_record(moved, 2, release_id)


def test_publication_record_holds_no_store_identifiers_and_uses_relative_manifest_paths(
    env: SimpleNamespace,
) -> None:
    silver = _silver("gcs_fake", env.tmp_path)
    release = _build(env, silver)
    record = _record(release.manifest, release.manifest_sha256)
    pub.write_publication_record(silver, record)
    with silver.open(canonical_publication_record_key(1, release.manifest.release_id)) as handle:
        payload = handle.read()
    for needle in (SILVER_BUCKET, PROJECT, "gs://", str(env.tmp_path), "/tmp"):
        assert needle.encode() not in payload, needle
    assert not record.manifest_path.startswith(("/", "openalex/"))
    assert f"{CANONICAL_ROOT}/{record.manifest_path}" == release.manifest_key
    assert payload == (canonical_json(record.model_dump(mode="json")) + "\n").encode()


# --- hygiene -----------------------------------------------------------------------------------------------------


def test_rebuild_reads_only_bronze_and_never_touches_the_network(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    bronze_root = env.tmp_path / "bronze"

    def tree() -> dict[str, bytes]:
        return {p.relative_to(bronze_root).as_posix(): p.read_bytes() for p in bronze_root.rglob("*") if p.is_file()}

    before = tree()

    def no_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_socket)
    guard = Recording(env.bronze, refuse_puts=True)
    assert pub.reconcile_release(bronze=guard, silver=silver, release_id=manifest.release_id).passed
    assert tree() == before and guard.puts == []
    assert set(guard.opens) == {key for item in manifest.inputs for key in (item.landing_manifest_key, item.raw_object_key)}
    assert all(key.startswith(("manifests/openalex/", "openalex/works")) for key in guard.opens)


def test_reconcile_summary_holds_no_keys_or_paths(env: SimpleNamespace) -> None:
    silver = _silver("local", env.tmp_path)
    manifest = _build(env, silver).manifest
    summary = pub.reconcile_release(bronze=env.bronze, silver=silver, release_id=manifest.release_id).summary()
    text = json.dumps(summary, default=str)
    for needle in ("openalex/", "manifests/", str(env.tmp_path), "part-0", ".parquet"):
        assert needle not in text, needle
    assert summary["passed"] is True and summary["files_compared"] == summary["files_matching"] == 22


def test_runbook_snippet_uses_the_public_api() -> None:
    page = (REPO_ROOT / "docs" / "architecture" / "openalex-canonical-model.md").read_text(encoding="utf-8")
    section = page.split("### Rebuild and reconcile a release", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match is not None
    tree = ast.parse(match.group(1))
    imported: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = importlib.import_module(node.module or "")
            for alias in node.names:
                assert hasattr(module, alias.name), alias.name
                imported[alias.asname or alias.name] = getattr(module, alias.name)
    assert {"reconcile_release", "LocalObjectStore", "StorageConfig"} <= set(imported)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    for call in calls:
        if isinstance(call.func, ast.Name) and call.func.id in imported and callable(imported[call.func.id]):
            target = imported[call.func.id]
            signature = inspect.signature(target if inspect.isfunction(target) else target.__init__)
            arguments = [None] * len(call.args)
            keywords = {keyword.arg: None for keyword in call.keywords if keyword.arg}
            if not inspect.isfunction(target):
                arguments = [None, *arguments]
            signature.bind(*arguments, **keywords)
        elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "report":
            assert hasattr(pub.ReconciliationReport, call.func.attr), call.func.attr
    assert any(isinstance(c.func, ast.Name) and c.func.id == "reconcile_release" for c in calls)
