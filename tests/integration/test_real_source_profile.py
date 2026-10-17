"""DE-05 (#126): offline tests on real-derived OpenAlex snapshot evidence.

The committed files under ``tests/fixtures/openalex_real/`` come from one LOCAL
bounded inspection (sandbox container, anonymous HTTPS to the public bucket,
2026-10-16) made by ``scripts/openalex_snapshot_inspection_126.py``: aggregate
evidence, a per-file Works manifest digest (sizes and counts only), and five real
Works records masked (pseudonymised) by a fail-closed policy. No source bytes are
committed. These tests need no network; a module-wide guard fails any socket use.
The live, opt-in counterpart is ``test_real_source_profile_live.py``.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import re
import socket
import ssl
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

import pytest
import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import FIELD_CATALOG, IdentifierError, MappingError, map_openalex_work
from research_platform.canonical.openalex.models import CanonicalLineage
from research_platform.config import SampleSelectionConfig
from research_platform.config.models import StorageConfig
from research_platform.control import ControlStatus, FailureCategory, InMemoryControlStore, PipelineRunStatus
from research_platform.ingestion import run_openalex_works_local_ingest
from research_platform.ingestion.deletions_csv import iter_deleted_work_records
from research_platform.ingestion.errors import IngestionError
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex import (
    OpenAlexAssetMetadata,
    OpenAlexConnector,
    OpenAlexDecompressionError,
    OpenAlexNetworkError,
    OpenAlexSizeMismatchError,
    OpenAlexTruncatedError,
    ProfilingLimits,
    parse_openalex_works_manifest,
    select_openalex_works_sample,
)
from research_platform.sources.openalex.profiling import profile_openalex_asset
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.errors import ObjectNotFoundError
from research_platform.storage.local import LocalObjectStore
from research_platform.storage.openalex_layout import openalex_raw_object_key
from tests.support.openalex_fake_bucket import load_inspection_script

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "openalex_real"
SNAPSHOT_DATE = "2026-10-14"
EVIDENCE = FIXTURES / f"snapshot_evidence_{SNAPSHOT_DATE}.json"
DIGEST = FIXTURES / f"works_manifest_digest_{SNAPSHOT_DATE}.json"
SAMPLE = FIXTURES / f"works_redacted_sample_{SNAPSHOT_DATE}.json"
PROFILING_DOC = REPO_ROOT / "docs" / "architecture" / "openalex-source-profiling.md"
LIVE_MODULE = REPO_ROOT / "tests" / "integration" / "test_real_source_profile_live.py"
SCRIPT = REPO_ROOT / "scripts" / "openalex_snapshot_inspection_126.py"
m = load_inspection_script()
# Fixture paths that the 300-record JSONL schema sample of the pair did not observe: the
# fixture records were selected from all 2,798 records of the file.
FIXTURE_PATHS_OUTSIDE_JSONL_SAMPLE = {
    "best_oa_location.source.host_organization_lineage_names[]",
    "sustainable_development_goals[]",
    "sustainable_development_goals[].display_name",
    "sustainable_development_goals[].id",
    "sustainable_development_goals[].score",
}
UNCATALOGUED = {
    "authors_count", "citation_normalized_percentile", "cited_by_percentile_year",
    "countries_distinct_count", "funders", "fwci", "has_content", "indexed_in", "institutions",
    "institutions_distinct_count", "is_xpac", "locations_count", "primary_topic", "study_designs", "volume",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network in offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", refuse)


@pytest.fixture(scope="module")
def evidence() -> Any:
    return m.load_snapshot_evidence(EVIDENCE)


@pytest.fixture(scope="module")
def digest() -> Any:
    return m.load_works_manifest_digest(DIGEST)


@pytest.fixture(scope="module")
def sample() -> Any:
    return m.load_redacted_fixture(SAMPLE)


def shim(record: dict[str, Any]) -> dict[str, Any]:
    """Test-only diagnostic normalisation (F4): the canonical mapper itself is unchanged."""
    return m.diagnostic_normalisation(record)


# --- T01 ---------------------------------------------------------------------------------


def test_real_works_manifest_digest_matches_recorded_totals(evidence: Any, digest: Any) -> None:
    assert len(digest.rows) == digest.jsonl.files == digest.parquet.files == 1_898
    assert sum(row[2] for row in digest.rows) == digest.jsonl.content_length
    assert sum(row[3] for row in digest.rows) == digest.parquet.content_length
    assert sum(row[4] for row in digest.rows) == digest.jsonl.record_count == digest.parquet.record_count
    for name in ("jsonl", "parquet"):
        works = evidence.works_manifests[name]
        totals = getattr(digest, name)
        assert (works.record_count, works.content_length, works.listed) == (
            totals.record_count, totals.content_length, totals.files)
        entity = next(e for e in evidence.combined_manifests[name].entities if e.entity == "works")
        assert (entity.record_count, entity.content_length, entity.files) == (
            totals.record_count, totals.content_length, totals.files)
    assert (digest.jsonl.record_count, digest.jsonl.content_length) == (464_765_485, 675_298_563_638)
    assert digest.parquet.content_length == 744_042_462_611


def test_real_works_manifest_digest_selection_excludes_oversized_assets(evidence: Any, digest: Any) -> None:
    expected = {"jsonl": (296, 1_602), "parquet": (289, 1_609)}
    for name, (eligible, oversized) in expected.items():
        assets = parse_openalex_works_manifest(m.manifest_from_digest(digest, name), content_format=name)
        selection = select_openalex_works_sample(assets, SampleSelectionConfig())
        reasons = Counter(item.reason for item in selection.skipped)
        assert selection.eligible_count == eligible
        assert reasons["OVERSIZED"] == oversized and reasons["UNKNOWN_SIZE"] == 0
        assert all(item.asset.byte_size <= 25_000_000 for item in selection.skipped
                   if item.reason == "FILE_LIMIT_REACHED") and selection.selected[0].byte_size <= 25_000_000
        assert all(item.asset.byte_size > 25_000_000 for item in selection.skipped if item.reason == "OVERSIZED")
        assert selection.selected[0].file_uri == evidence.works_manifests[name].default_selection_uri
        assert (evidence.works_manifests[name].eligible, evidence.works_manifests[name].oversized) == (
            eligible, oversized)


def test_real_pair_rule_selects_both_files_within_the_per_file_cap(evidence: Any, digest: Any) -> None:
    day, part = m.select_profile_pair(digest)
    assert evidence.profiled_pair.key == f"updated_date={day}/part_{part:04d}"
    row = next(r for r in digest.rows if (r[0], r[1]) == (day, part))
    assert row[2] <= 2_000_000 and row[3] <= 2_000_000 and row[4] >= 1_000
    assert (evidence.profiled_pair.jsonl.size, evidence.profiled_pair.parquet.size) == (row[2], row[3])
    assert evidence.works_pairs.both_within_object_cap == 283
    with pytest.raises(ValueError):
        m.select_profile_pair(digest, max_file_bytes=25_000_001)
    with pytest.raises(LookupError):
        m.select_profile_pair(digest, max_file_bytes=1_000)


def test_manifest_versions_mark_out_of_limit_prior_releases(evidence: Any) -> None:
    recorded = {(v.key, v.size): v.within_manifest_limit for v in evidence.prior_release_visibility.manifest_versions}
    assert recorded[("data/jsonl/manifest.json", 1_121_660)] is False
    assert recorded[("data/parquet/manifest.json", 1_158_613)] is False
    assert recorded[("data/jsonl/manifest.json", 671_964)] is True
    decided = m.summarize_manifest_versions([
        m.ObjectVersion("data/jsonl/manifest.json", "v", False, None, None, 1_121_660),
        m.ObjectVersion("data/jsonl/manifest.json", "v", True, None, None, 1_000_000),
    ])
    assert [v.within_manifest_limit for v in decided] == [False, True]
    assert all(e.byte_range is None or not e.path.endswith("manifest.json")
               for e in evidence.run.request_log)
    assert not any("versionId" in e.path for e in evidence.run.request_log)


def test_full_listing_matches_manifests_and_names_every_unreferenced_object(evidence: Any) -> None:
    assert evidence.layout.listing_coverage.full_listing is True
    for name in ("jsonl", "parquet"):
        check = evidence.listing_checks[name]
        assert (check.manifest_files, check.missing_from_listing, check.size_mismatches) == (3_108, 0, 0)
        assert check.listed_files == check.manifest_files + 23 == evidence.layout.listing_coverage.keys[name]
        assert check.checksum_algorithm == {"CRC64NVME": check.listed_files}
        assert check.etag_single_part + check.etag_multipart == check.listed_files
        assert {(i.key_pattern, i.count) for i in evidence.layout.unreferenced_objects[name]} == {
            ("<entity>/manifest.json", 21), ("manifest.json", 1), ("works/deleted_ids.csv.gz", 1)}
    assert len(evidence.layout.entities) == 21 and evidence.layout.parquet_entities_equal
    assert evidence.deletions.non_works_deletion_objects == ()


# --- T02 ---------------------------------------------------------------------------------


def test_redacted_fixture_paths_are_within_the_real_jsonl_profile(
    evidence: Any, sample: Any, tmp_path: Path
) -> None:
    profile, _ = m.profile_redacted_fixture(sample, tmp_path)
    fixture_paths = {f.path for f in profile.schema_fields}
    jsonl_paths = {f.path for f in evidence.profiled_pair.jsonl.profile.schema_fields}
    fidelity = m.compare_field_paths(profile.schema_fields, evidence.profiled_pair.parquet.profile.schema_fields)
    assert fidelity.missing_in_parquet == () and fidelity.type_conflicts == ()
    assert fixture_paths - jsonl_paths == FIXTURE_PATHS_OUTSIDE_JSONL_SAMPLE
    assert {p.split(".")[0].split("[")[0].split("{")[0] for p in fixture_paths} <= set(
        evidence.canonical_compatibility.top_level_keys)


def test_redacted_real_jsonl_profile_is_reproducible(evidence: Any, sample: Any, tmp_path: Path) -> None:
    """Reproducibility only: the same code on the committed records gives the committed profile."""
    profile, body = m.profile_redacted_fixture(sample, tmp_path)
    produced = profile.model_dump(mode="json")
    recorded = evidence.fixture_profile.model_dump(mode="json")
    for name in m.REPRODUCIBLE_PROFILE_EXCLUDES:
        produced.pop(name)
        recorded.pop(name)
    assert produced == recorded
    assert profile.observed_size_bytes == profile.declared_size_bytes == len(body)
    assert profile.sha256 == hashlib.sha256(body).hexdigest()
    assert profile.row_count == len(sample.records) == 5
    # The profile describes the re-framed fixture, not the real part named by source_uri.
    assert profile.limitations[0] == m.FIXTURE_PROFILE_LIMITATION
    assert profile.source_uri == evidence.profiled_pair.jsonl.uri
    assert profile.sha256 != evidence.profiled_pair.jsonl.sha256
    assert profile.content_type is None and profile.evidence["content_type"].value == "UNKNOWN"


def test_real_formats_are_recorded_from_bytes_not_suffixes(evidence: Any) -> None:
    jsonl, parquet = evidence.profiled_pair.jsonl, evidence.profiled_pair.parquet
    assert jsonl.framing.kind == "gzip" and jsonl.framing.header_hex == "1f8b0800000000000003"
    assert jsonl.framing.members == 1 and jsonl.framing.trailer_crc_ok and jsonl.framing.trailer_isize_ok
    assert jsonl.profile.compression == "gzip" and jsonl.profile.row_count == jsonl.record_count
    assert parquet.framing.kind == "parquet"
    assert parquet.framing.leading_magic and parquet.framing.trailing_magic
    assert parquet.framing.created_by.startswith("parquet-mr version 1.12.3-databricks")
    assert parquet.framing.codecs == ("SNAPPY",) and parquet.framing.statistics == "all"
    assert parquet.profile.row_count == parquet.record_count == jsonl.record_count
    assert (parquet.profile.column_count, parquet.profile.physical_column_count) == (51, 200)
    assert parquet.profile.sampled_record_count == 0


def test_real_parquet_footer_schema_covers_every_jsonl_path(evidence: Any) -> None:
    paths = m.compare_field_paths(evidence.profiled_pair.jsonl.profile.schema_fields,
                                  evidence.profiled_pair.parquet.profile.schema_fields)
    assert paths.missing_in_parquet == () and paths.type_conflicts == ()
    assert paths.representation_rules == {"abstract_inverted_index": "object -> JSON string",
                                          "ids": "object -> map<string,string>"}
    assert paths == evidence.fidelity.paths
    arrow_paths = {f.path for f in evidence.arrow_schema_fields}
    assert arrow_paths == {f.path for f in evidence.profiled_pair.parquet.profile.schema_fields}


def test_recorded_pair_fidelity_has_no_value_mismatches(evidence: Any) -> None:
    values = evidence.fidelity.values
    assert values.records_compared == values.jsonl_records_parsed == 2_798
    assert values.mismatched_fields == {} and values.jsonl_absent_parquet_null_fields == {}
    assert values.ids_equal is True and values.row_order_equal is False
    assert values.fields_compared == 51
    assert set(values.float32_widened_fields) <= {"concepts", "topics", "primary_topic", "keywords"}
    assert values.parquet_sub_millisecond_fields == {"updated_date": 2_798}


def test_large_part_footer_matches_manifest_and_schema(evidence: Any, digest: Any) -> None:
    footer = evidence.large_parquet_footer
    assert footer.size == max(row[3] for row in digest.rows) == 1_070_341_481
    assert footer.num_rows == footer.manifest_record_count
    assert footer.schema_equals_profiled_pair is True and footer.codecs == ("SNAPPY",)
    assert footer.footer_bytes + 8 + 8 == footer.range_bytes <= 1_000_008 + 8
    ranges = [e for e in evidence.run.request_log if e.byte_range and "parquet" in e.path]
    assert len(ranges) == 2 and all(e.status == 206 for e in ranges)
    assert all(int(e.byte_range.split("-")[1]) == footer.size - 1 for e in ranges)


def test_field_catalog_against_real_top_level_keys(evidence: Any) -> None:
    compatibility = evidence.canonical_compatibility
    assert len(compatibility.top_level_keys) == 51
    assert set(compatibility.uncatalogued) == UNCATALOGUED == set(compatibility.top_level_keys) - set(FIELD_CATALOG)
    assert compatibility.catalogued_absent == ("grants",)


def test_canonical_mapper_rejects_real_timestamps_and_institution_hosts(evidence: Any, sample: Any) -> None:
    lineage = CanonicalLineage.model_validate({
        "source_asset_id": "oa-" + "a" * 64, "source_checksum_sha256": "ab" * 32,
        "source_updated_date": date(2026, 9, 28), "run_id": "00000000-0000-0000-0000-000000000000",
        "processed_at": datetime(2026, 10, 16, tzinfo=UTC),
    })
    for record in sample.records:
        with pytest.raises((MappingError, IdentifierError)):
            map_openalex_work(record, lineage=lineage)
        map_openalex_work(shim(record), lineage=lineage)
    compatibility = evidence.canonical_compatibility
    assert compatibility.mapped_unchanged == 0
    assert compatibility.mapped_with_documented_normalisation == compatibility.records == 2_798
    assert set(compatibility.mapper_first_failures) == {
        "MappingError: created_date must be exact YYYY-MM-DD",
        "IdentifierError: openalex_id prefix must be P, got I",
    }


def test_redaction_policy_covers_every_observed_string_path(evidence: Any) -> None:
    strings = {
        f.path for f in evidence.profiled_pair.jsonl.profile.schema_fields
        if "string" in f.data_type.split("|")
    }
    assert strings and strings <= set(m.REDACTION_POLICY)
    assert set(m.MASK_PERSON_PATHS) == set(evidence.rights.personal_data_paths)
    assert evidence.redaction.unknown_string_paths == () and evidence.redaction.skipped.unknown_path == 0
    assert evidence.redaction.policy_counts == {"keep": 114, "mask_person": 9, "mask_price": 2, "replace_text": 18}


# --- T05 -----------------------------------------------------------------------------------


class _Response:
    def __init__(self, body: bytes, *, content_length: int | None = None) -> None:
        self._body = io.BytesIO(body)
        self.status = 200
        self.headers = {"Content-Length": str(len(body) if content_length is None else content_length)}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        assert size >= 0
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> _Response:
        return self.response


def _framed(sample: Any) -> bytes:
    return m.real_framed_gzip(sample.records)


def _asset(body: bytes, *, size: int | None = None, records: int | None = 5,
           snapshot: str = SNAPSHOT_DATE, uri: str | None = None) -> OpenAlexAssetMetadata:
    uri = uri or "s3://openalex/data/jsonl/works/updated_date=2026-09-28/part_0000.gz"
    return OpenAlexAssetMetadata(
        source="openalex", snapshot_date=snapshot, entity="works", file_uri=uri,
        byte_size=len(body) if size is None else size, record_count=records,
        updated_date=re.search(r"updated_date=(\d{4}-\d{2}-\d{2})", uri).group(1), content_format="jsonl",
    )


def _profile(body: bytes, limits: ProfilingLimits, *, content_length: int | None = None, **asset: Any) -> Any:
    response = _Response(body, content_length=content_length)
    connector = OpenAlexConnector(client=_Client(response), max_attempts=1, retry_backoff_seconds=0)
    try:
        return profile_openalex_asset(connector, _asset(body, **asset), limits), response
    finally:
        assert response.closed


def test_truncated_real_framed_gzip_is_detected_only_when_drained(sample: Any) -> None:
    body = _framed(sample)[:-8]
    result, _ = _profile(body, ProfilingLimits(max_profile_records=2))
    assert result.row_count is None and result.sha256 is None
    assert any("not read to EOF" in item for item in result.limitations)
    with pytest.raises(OpenAlexDecompressionError, match="truncated"):
        _profile(body, ProfilingLimits(max_profile_records=2, drain_jsonl_to_eof=True))


def test_corrupted_gzip_crc_is_a_decompression_error_when_drained(sample: Any) -> None:
    body = bytearray(_framed(sample))
    body[-8] ^= 0xFF
    result, _ = _profile(bytes(body), ProfilingLimits(max_profile_records=2))
    assert result.row_count is None
    with pytest.raises(OpenAlexDecompressionError, match="cannot be decompressed"):
        _profile(bytes(body), ProfilingLimits(max_profile_records=2, drain_jsonl_to_eof=True))


def test_real_framed_payload_shorter_than_content_length_is_truncated(sample: Any) -> None:
    body = _framed(sample)
    response = _Response(body[:-100], content_length=len(body))
    connector = OpenAlexConnector(client=_Client(response), max_attempts=1)
    with pytest.raises(OpenAlexTruncatedError):
        profile_openalex_asset(connector, _asset(body), m.JSONL_PROFILE_LIMITS)
    assert response.closed


def test_content_length_differing_from_manifest_is_a_size_mismatch(sample: Any) -> None:
    body = _framed(sample)
    with pytest.raises(OpenAlexSizeMismatchError, match="Content-Length differs"):
        _profile(body, m.JSONL_PROFILE_LIMITS, content_length=len(body) + 1, size=len(body))


def test_record_count_differing_from_manifest_is_a_size_mismatch_when_drained(sample: Any) -> None:
    body = _framed(sample)
    with pytest.raises(OpenAlexSizeMismatchError, match="record count"):
        _profile(body, ProfilingLimits(max_profile_records=2, drain_jsonl_to_eof=True), records=6)
    result, _ = _profile(body, ProfilingLimits(max_profile_records=2), records=6)
    assert result.row_count is None


class _InterruptedStream(io.RawIOBase):
    def __init__(self, payload: bytes, fail_after: int, error: Exception) -> None:
        self._payload = payload
        self._fail_after = fail_after
        self._error = error
        self._offset = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self._offset >= self._fail_after:
            raise self._error
        size = min(len(buffer), self._fail_after - self._offset)
        buffer[:size] = self._payload[self._offset:self._offset + size]
        self._offset += size
        return size


class _FlakyConnector:
    """Fails the first fetches mid-stream with the given errors, then serves the payload."""

    def __init__(self, asset: OpenAlexAssetMetadata, payload: bytes, failures: list[Exception]) -> None:
        self._asset = asset
        self._payload = payload
        self._failures = list(failures)
        self.fetch_calls = 0

    def discover_metadata(self) -> OpenAlexSampleSelection:
        return OpenAlexSampleSelection(max_files=1, max_file_size_bytes=25_000_000, eligible_count=1,
                                       selected=(self._asset,), skipped=())

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        self.fetch_calls += 1
        if self._failures:
            return io.BufferedReader(_InterruptedStream(self._payload, len(self._payload) // 2,
                                                        self._failures.pop(0)))
        return io.BytesIO(self._payload)


def _ingest_setup(tmp_path: Path) -> tuple[Path, LocalObjectStore]:
    landing = tmp_path / "landing"
    config = {
        "environment": "local", "log_level": "INFO", "source": "openalex",
        "storage": {"backend": "local", "landing_path": str(landing)},
        "warehouse": {"transactional": "postgres", "analytical": "duckdb",
                      "postgres_dsn_env": "POSTGRES_DSN", "duckdb_path": ":memory:"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": 25_000_000},
    }
    path = tmp_path / "local.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path, LocalObjectStore(StorageConfig(backend="local", landing_path=landing))


def _clock() -> Any:
    state = {"now": datetime(2026, 10, 16, 1, tzinfo=UTC)}

    def clock() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return clock


def _ingest(config: Path, objects: LocalObjectStore, control: Any, canonical: Any, connector: Any) -> Any:
    return run_openalex_works_local_ingest(
        config, control_store=control, canonical_store=canonical, object_store=objects,
        connector=connector, now=_clock(),
    )


def _shimmed_payload(sample: Any) -> bytes:
    return m.real_framed_gzip([shim(copy.deepcopy(record)) for record in sample.records])


def test_interrupted_retrieval_lands_nothing_then_retry_and_replay_are_idempotent(sample: Any, tmp_path: Path) -> None:
    payload = _shimmed_payload(sample)
    asset = _asset(payload, records=None)
    config, objects = _ingest_setup(tmp_path)
    control, canonical = InMemoryControlStore(), InMemoryCanonicalStore()
    connector = _FlakyConnector(asset, payload, [OpenAlexNetworkError("OpenAlex payload stream failed")])
    with pytest.raises(IngestionError):
        _ingest(config, objects, control, canonical, connector)
    row = control.get_source_file(asset.asset_id)
    assert row.status is ControlStatus.FAILED and row.failure_category is FailureCategory.RETRIEVAL
    with pytest.raises(ObjectNotFoundError):
        objects.open(openalex_raw_object_key(asset))
    retry = _ingest(config, objects, control, canonical, connector)
    assert retry.run.status is PipelineRunStatus.SUCCESS and canonical.work_count() == 5
    with objects.open(openalex_raw_object_key(asset)) as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == hashlib.sha256(payload).hexdigest()
    replay = _ingest(config, objects, control, canonical, connector)
    assert replay.skipped_reason == "ALREADY_SUCCESS"
    assert connector.fetch_calls == 2 and canonical.work_count() == 5


def test_truncated_retrieval_lands_nothing_and_is_retried(sample: Any, tmp_path: Path) -> None:
    payload = _shimmed_payload(sample)
    asset = _asset(payload, records=None)
    config, objects = _ingest_setup(tmp_path)
    control, canonical = InMemoryControlStore(), InMemoryCanonicalStore()
    connector = _FlakyConnector(asset, payload, [OpenAlexTruncatedError("OpenAlex payload ended before its declared length")])
    with pytest.raises(IngestionError):
        _ingest(config, objects, control, canonical, connector)
    assert control.get_source_file(asset.asset_id).status is ControlStatus.FAILED
    with pytest.raises(ObjectNotFoundError):
        objects.open(openalex_raw_object_key(asset))
    assert _ingest(config, objects, control, canonical, connector).run.status is PipelineRunStatus.SUCCESS


def test_next_release_lands_beside_prior_release(sample: Any, tmp_path: Path) -> None:
    uri = "s3://openalex/data/jsonl/works/updated_date=2016-06-24/part_0000.gz"
    prior_payload = _shimmed_payload(sample)
    next_payload = m.real_framed_gzip([shim(copy.deepcopy(r)) for r in sample.records[:3]])
    prior = _asset(prior_payload, records=None, snapshot="2026-09-23", uri=uri)
    current = _asset(next_payload, records=None, snapshot="2026-10-14", uri=uri)
    assert prior.file_uri == current.file_uri and prior.asset_id != current.asset_id
    config, objects = _ingest_setup(tmp_path)
    # One object store; separate control/canonical stores keep this test to raw landing
    # (canonical versioning across releases of one partition belongs to DE-11, #132).
    _ingest(config, objects, InMemoryControlStore(), InMemoryCanonicalStore(),
            _FlakyConnector(prior, prior_payload, []))
    _ingest(config, objects, InMemoryControlStore(), InMemoryCanonicalStore(),
            _FlakyConnector(current, next_payload, []))
    prior_key, current_key = openalex_raw_object_key(prior), openalex_raw_object_key(current)
    assert prior_key != current_key and "snapshot_date=2026-09-23" in prior_key
    with objects.open(prior_key) as handle:
        assert handle.read() == prior_payload
    with objects.open(current_key) as handle:
        assert handle.read() == next_payload


def test_every_committed_snapshot_evidence_validates() -> None:
    files = sorted(FIXTURES.glob("snapshot_evidence_*.json"))
    assert files
    for path in files:
        loaded = m.load_snapshot_evidence(path)
        day = loaded.snapshot_date.isoformat()
        assert path.name == f"snapshot_evidence_{day}.json"
        assert (FIXTURES / f"works_manifest_digest_{day}.json").exists()
        assert (FIXTURES / f"works_redacted_sample_{day}.json").exists()
        assert loaded.evidence == "LOCAL bounded inspection" and loaded.host == "openalex.s3.amazonaws.com"


# --- checksums and deletions ----------------------------------------------------------------


def test_recorded_checksums_are_internally_consistent(evidence: Any, sample: Any) -> None:
    for obj in (evidence.profiled_pair.jsonl, evidence.profiled_pair.parquet):
        assert re.fullmatch(r"[0-9a-f]{64}", obj.sha256) and obj.sha256 == obj.profile.sha256
        assert obj.crc64nvme_local == obj.crc64nvme_s3 and re.fullmatch(r"[A-Za-z0-9+/]{11}=", obj.crc64nvme_s3)
        assert (obj.md5_equals_etag is None) == ("-" in obj.etag)
        assert obj.head_etag_equals_get_etag is True
    assert (sample.source.jsonl_sha256, sample.source.parquet_sha256) == (
        evidence.profiled_pair.jsonl.sha256, evidence.profiled_pair.parquet.sha256)
    for published in evidence.published_files:
        assert (published.md5_equals_etag is None) == ("-" in (published.etag or "-"))
        assert published.md5_equals_etag in (True, None)
    for item in evidence.deletions.current:
        assert item.etag.endswith("-4") and item.crc64nvme_s3


def test_observed_deletion_header_is_accepted_by_the_deletion_parser(evidence: Any) -> None:
    header = evidence.deletions.prefix.header
    assert header == "work_id,deleted_date"
    day = next(iter(evidence.deletions.prefix.deleted_date_counts))
    stream = io.BytesIO(gzip.compress(
        (f"{header}\nhttps://openalex.org/W1,{day}\nhttps://openalex.org/W22,{day}\n").encode(), mtime=0))
    rows = list(iter_deleted_work_records(stream))
    assert [(row.work_id, row.deleted_date.isoformat()) for row in rows] == [("W1", day), ("W22", day)]


def test_deletion_ledger_is_recorded_as_observed_and_bounded(evidence: Any) -> None:
    deletions = evidence.deletions
    assert all(item.size == 234_102_326 for item in deletions.current)
    assert deletions.identical_content_under_both_trees is True
    first, second = deletions.current
    assert (first.etag, first.crc64nvme_s3) == (second.etag, second.crc64nvme_s3)
    assert first.uri != second.uri and first.last_modified != second.last_modified
    assert [v.size for v in first.versions] == [234_102_326, 168_365_444]
    assert second.uri.endswith("/parquet/works/deleted_ids.csv.gz") and second.versions is None
    listed = [e.path for e in evidence.run.request_log if e.path.startswith("/?versions&")]
    assert any("prefix=data%2Fjsonl%2Fworks%2Fdeleted_ids.csv.gz&" in path for path in listed)
    assert not any("parquet%2Fworks%2Fdeleted_ids" in path for path in listed)
    prefix = deletions.prefix
    assert prefix.bytes == 65_536 and prefix.truncated is True and prefix.row_shape_ok
    assert prefix.complete_rows == sum(prefix.deleted_date_counts.values()) > 0
    ranges = [e for e in evidence.run.request_log if "deleted_ids" in e.path and e.byte_range]
    assert [(e.byte_range, e.status, e.body_bytes) for e in ranges] == [("0-65535", 206, 65_536)]
    assert evidence.run.largest_body_bytes < 25_000_000
    assert len(deletions.legacy_merged_ids_prefixes) == 5


# --- rights and cadence ----------------------------------------------------------------------


def test_rights_record_quotes_the_snapshot_and_marks_unverified_terms(evidence: Any) -> None:
    rights = evidence.rights
    assert rights.license_title == "CC0 1.0 Universal" and rights.license_title_line.line == 3
    assert rights.readme_license_line.line == 8 and "CC0" in rights.readme_license_line.text
    assert rights.third_party_rights_clause[0].line == 113
    assert rights.attribution_statements == () and rights.full_text_statements == ()
    assert rights.statuses == {
        "license": "OBSERVED", "readme_license_line": "OBSERVED", "third_party_rights_clause": "OBSERVED",
        "attribution": "NOT PUBLISHED IN SNAPSHOT", "terms_of_use": "NOT VERIFIED",
        "full_article_text": "NOT IN SNAPSHOT; full-text rights NOT PUBLISHED",
        "abstracts": "PRESENT; no specific statement", "per_work_license_paths": "OBSERVED",
        "personal_data_paths": "OBSERVED",
    }
    assert rights.full_text_field_present is False and "has_fulltext" in rights.full_text_indicator_paths
    assert [(q.file, q.line) for q in rights.help_links] == [
        ("README.txt", 11), ("README.txt", 12), ("RELEASE_NOTES.txt", 4)]
    assert all("help.openalex.org" in q.text for q in rights.help_links)
    assert any("help.openalex.org" in item for item in rights.unverified)
    license_file = next(f for f in evidence.published_files if f.key == "LICENSE.txt")
    assert license_file.sha256 == rights.license_sha256 and rights.license_versions_same_etag


def test_cadence_record_keeps_stated_and_observed_apart(evidence: Any) -> None:
    cadence = evidence.cadence
    assert [(q.file, q.line) for q in cadence.stated] == [
        ("README.txt", 3), ("RELEASE_NOTES.txt", 3), ("RELEASE_NOTES.txt", 77)]
    assert cadence.release_headings == 56 and cadence.releases_by_year["2026"] == 9
    assert cadence.on_stated_schedule_current_year == (date(2026, 10, 14),)
    assert m.second_wednesday(2026, 10) == date(2026, 10, 14)
    assert cadence.current_layout_manifest_versions == 3
    assert cadence.inference.startswith("Inference, not a published statement")


# --- docs, fixtures and wiring ----------------------------------------------------------------


def test_profiling_doc_real_snapshot_section_matches_the_evidence(evidence: Any) -> None:
    text = PROFILING_DOC.read_text(encoding="utf-8")
    start = text.index(m.SECTION_HEADING_PREFIX)
    section = text[start: text.index("\n## ", start + 1) + 1]
    assert section == m.render_snapshot_evidence_section(evidence)


def test_rendered_section_is_mdx_safe_outside_code_spans(evidence: Any) -> None:
    # The docs site parses Markdown as MDX: a bare "<", "{" or "}" in prose breaks the build.
    section = m.render_snapshot_evidence_section(evidence)
    prose = re.sub(r"`[^`\n]*`", "", section)
    assert not re.search(r"(?<!\\)[<{}]", prose)
    assert m._mdx_safe("a <= b {c} `x <= {y}`") == r"a \<= b \{c\} `x <= {y}`"


def test_evidence_json_is_in_canonical_form(evidence: Any, digest: Any, sample: Any) -> None:
    assert EVIDENCE.read_text(encoding="utf-8") == m.canonical_json(evidence)
    assert DIGEST.read_text(encoding="utf-8") == m.canonical_json(digest)
    assert SAMPLE.read_text(encoding="utf-8") == m.canonical_json(sample)


def test_evidence_contains_no_record_values(evidence: Any, sample: Any) -> None:
    text = EVIDENCE.read_text(encoding="utf-8")
    assert not re.search(r'"records": \[', text)
    for record in sample.records:
        for path, value in m.string_leaf_paths(record):
            if m.REDACTION_POLICY.get(path) in {"mask_person", "replace_text"} and len(value) > 8:
                assert value not in text
    for forbidden in ("x-amz-request-id", "x-amz-id-2", "clusterId", "jobGroupId"):
        assert forbidden not in text
    assert not m.EMAIL_PATTERN.search(text)
    tokens = [e.path for e in evidence.run.request_log if "continuation-token" in e.path]
    assert tokens and all(p.endswith("continuation-token=<token>") for p in tokens)


def test_committed_schema_paths_are_structural_only(evidence: Any) -> None:
    assert m.structural_path_errors(m.evidence_schema_paths(evidence)) == ()
    assert m.structural_path_errors(["a.b", "a[]", "m{key}", 'a["x.y"]', "Abstract.Word"]) == (
        "Abstract.Word", 'a["x.y"]')


def test_fixture_is_redacted(sample: Any) -> None:
    assert sample.schema_version == 1 and len(sample.records) == sample.selection.count == 5
    text = SAMPLE.read_text(encoding="utf-8")
    assert not m.EMAIL_PATTERN.search(text)
    for record in sample.records:
        assert m.redaction_errors(record) == ()
        assert m.unknown_string_paths(record) == ()
    assert sample.redaction.rules_version == m.REDACTION_RULES_VERSION
    assert sample.redaction.policy_counts == m.policy_counts()


def test_live_tests_are_opt_in_and_deselected_by_default() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"openalex_live: ' in pyproject
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("test:", "test-unit:"):
        line = makefile.split(f"\n{target}\n", 1)[1].splitlines()[0]
        assert 'not openalex_live' in line and "not postgres" in line
    assert "OPENALEX_LIVE_BUDGET_LEDGER" in makefile and "--basetemp=" in makefile
    live = LIVE_MODULE.read_text(encoding="utf-8")
    assert "pytest.mark.openalex_live" in live and 'os.environ.get("OPENALEX_LIVE_TESTS"' in live
    assert "pytest.mark.skipif(not LIVE" in live
    for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"):
        assert "OPENALEX_LIVE_TESTS" not in workflow.read_text(encoding="utf-8")
