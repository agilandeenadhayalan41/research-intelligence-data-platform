"""Offline tests of ``scripts/openalex_snapshot_inspection_126.py`` (DE-05, #126).

The script is loaded by file location. Every test runs against fakes: a module-wide
guard makes any socket, connect or TLS wrap fail, so a missed fake can never reach
``openalex.s3.amazonaws.com`` from CI.
"""

from __future__ import annotations

import json
import socket
import ssl
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from research_platform.sources.openalex.connector import (
    OpenAlexConnector,
    OpenAlexEndpointUnavailableError,
    OpenAlexTruncatedError,
)
from research_platform.sources.openalex.profile_models import EvidenceType, FieldProfile
from tests.support.openalex_fake_bucket import (
    PAIR_DAY,
    REPO_ROOT,
    FakeBucket,
    FakeResponse,
    load_inspection_script,
    synthetic_work,
)

m = load_inspection_script()
NS = "http://s3.amazonaws.com/doc/2006-03-01/"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network in offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", refuse)


def budget(**limits: int) -> Any:
    return m.SnapshotByteBudget(m.InspectionLimits(**limits))


def client(bucket: FakeBucket, the_budget: Any | None = None) -> Any:
    return m.SnapshotInspectionClient(the_budget or budget(), m.Recorder(None), opener=bucket.opener)


def run_script(tmp_path: Path, bucket: FakeBucket | None, *extra: str, out: str = "out") -> tuple[int, dict]:
    import contextlib
    import io

    argv = [
        "--budget-ledger", str(tmp_path / "ledger.json"), "--ledger-seed-bytes", "0",
        "--out", str(tmp_path / out), "--expect-snapshot-date", "2026-10-14",
        "--pair", f"{PAIR_DAY}/0000", *extra,
    ]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = m.main(argv, opener=bucket.opener if bucket else None, connector_client=bucket)
    return code, json.loads(buffer.getvalue().strip().splitlines()[-1])


# --- transport and allow-list -------------------------------------------------------


def test_construction_opens_no_socket() -> None:
    the_budget = budget()
    recorder = m.Recorder(None)
    m.SnapshotInspectionClient(the_budget, recorder)
    inner = m.BudgetedOpenAlexClient(m.AnonymousOpenAlexHTTPClient(), the_budget, recorder)
    OpenAlexConnector(client=inner)
    assert the_budget.requests == 0


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_text("data/jsonl/authors/updated_date=2026-10-14/part_0000.gz", expected_size=10),
        lambda c: c.get_text("legacy-data/works/manifest", expected_size=10),
        lambda c: c.list_objects("../", delimiter=True),
        lambda c: c.list_objects("data/jsonl/works/x/", delimiter=True),
        lambda c: c.list_versions("data/jsonl/authors/manifest.json"),
        lambda c: c.get_range(
            "data/jsonl/works/updated_date=2026-09-28/part_0000.gz", 0, 9,
            head=m.ObjectHead("data/jsonl/works/updated_date=2026-09-28/part_0000.gz", 100,
                              "0" * 32, None, None, None, None),
        ),
        lambda c: c.head("data/jsonl/works/updated_date=2026-09-28/part_0000.gz\n"),
    ],
    ids=["other-entity-part", "legacy-manifest", "dot-dot-prefix", "deep-prefix", "versions-other-key",
         "range-on-gz-part", "control-character"],
)
def test_paths_outside_the_allow_list_are_refused(call: Any) -> None:
    bucket = FakeBucket()
    with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
        call(client(bucket))
    assert bucket.calls == []


def test_head_sends_checksum_mode_and_reads_no_body() -> None:
    bucket = FakeBucket()
    seen: list[dict[str, str]] = []

    def opener(method: str, path: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        seen.append(headers)
        return bucket.opener(method, path, timeout=timeout, headers=headers)

    the_budget = budget()
    head = m.SnapshotInspectionClient(the_budget, m.Recorder(None), opener=opener).head("data/jsonl/manifest.json")
    assert seen[0]["x-amz-checksum-mode"] == "ENABLED"
    assert head.content_length == len(bucket.objects["data/jsonl/manifest.json"])
    assert head.crc64nvme is not None and head.checksum_type == "FULL_OBJECT"
    assert the_budget.body_bytes == 0 and the_budget.requests == 1


def test_whole_get_of_deletion_ledger_is_not_allow_listed() -> None:
    bucket = FakeBucket()
    c = client(bucket)
    with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
        c.get_text("data/jsonl/works/deleted_ids.csv.gz", expected_size=234_102_326)
    with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
        c.get_combined_manifest("jsonl/works/deleted_ids.csv.gz#", expected_size=10)
    assert bucket.calls == []


def test_range_outside_footer_or_ledger_prefix_is_refused() -> None:
    bucket = FakeBucket()
    c = client(bucket)
    part = f"data/parquet/works/updated_date={PAIR_DAY}/part_0000.parquet"
    head = m.ObjectHead(part, 5_000_000, "a" * 32, None, None, None, None)
    for start, end in ((0, 7), (10, 5_000_000 - 2), (5_000_000 - 1_000_009, 5_000_000 - 1)):
        with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
            c.get_range(part, start, end, head=head)
    ledger = "data/jsonl/works/deleted_ids.csv.gz"
    ledger_head = m.ObjectHead(ledger, 234_102_326, "b" * 32 + "-4", None, None, None, None)
    for start, end in ((1, 100), (0, 65_536)):
        with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
            c.get_range(ledger, start, end, head=ledger_head)
    parquet_ledger = "data/parquet/works/deleted_ids.csv.gz"
    with pytest.raises(OpenAlexEndpointUnavailableError, match="not allow-listed"):
        c.get_range(parquet_ledger, 0, 10, head=m.ObjectHead(parquet_ledger, 100, "c" * 32, None, None, None, None))
    assert bucket.calls == []


def test_get_without_known_size_is_refused() -> None:
    bucket = FakeBucket()
    c = client(bucket)
    with pytest.raises(OpenAlexEndpointUnavailableError, match="size unknown"):
        c.get_text("LICENSE.txt", expected_size=None)
    inner = m.BudgetedOpenAlexClient(bucket, budget(), m.Recorder(None))
    with pytest.raises(OpenAlexEndpointUnavailableError, match="size unknown"):
        inner.request("s3://openalex/data/jsonl/works/manifest.json", timeout=1, headers={})
    assert bucket.calls == []


# --- budget and session ledger ------------------------------------------------------


def test_budget_refuses_declared_object_over_cap_before_reading() -> None:
    the_budget = budget(max_object_bytes=1_000)
    response = FakeResponse(200, {"Content-Length": "5000"}, b"x" * 5000)

    class Inner:
        def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
            return response

    inner = m.BudgetedOpenAlexClient(Inner(), the_budget, m.Recorder(None))
    with pytest.raises(m.OpenAlexBudgetExceededError, match="per-object"):
        inner.expect("s3://openalex/data/jsonl/works/manifest.json", 5000)
    inner.expect("s3://openalex/data/jsonl/works/manifest.json", 900)
    with pytest.raises(m.OpenAlexSnapshotChangedError):
        inner.request("s3://openalex/data/jsonl/works/manifest.json", timeout=1, headers={})
    assert response.closed and the_budget.body_bytes == 0


def test_budget_refuses_declared_234102326_byte_object_before_reading() -> None:
    the_budget = budget()
    with pytest.raises(m.OpenAlexBudgetExceededError, match="per-object"):
        the_budget.admit("data/jsonl/works/deleted_ids.csv.gz", None, 234_102_326)
    assert the_budget.body_bytes == 0 and the_budget.requests == 0


def test_per_key_bytes_are_cumulative_across_ranges() -> None:
    the_budget = budget(max_object_bytes=100)
    the_budget.charge("k", 60)
    with pytest.raises(m.OpenAlexBudgetExceededError, match="per-object"):
        the_budget.admit("k", 50, None)
    assert the_budget.read_allowance("k") == 41
    with pytest.raises(m.OpenAlexBudgetExceededError, match="per-object"):
        the_budget.charge("k", 41)
    the_budget.charge("other", 60)


def test_budget_counts_actual_bytes_when_content_length_is_missing() -> None:
    bucket = FakeBucket()
    the_budget = budget()
    listing = client(bucket, the_budget).list_objects("data/jsonl/", delimiter=False)
    assert listing.objects
    response = bucket.opener("GET", "/?list-type=2&prefix=data%2Fjsonl%2F&max-keys=1000",
                             timeout=1, headers={})
    assert "Content-Length" not in response.headers
    assert the_budget.body_bytes == len(response.read(10_000_000))


def test_budget_is_cumulative_across_connector_and_inspection_reads() -> None:
    bucket = FakeBucket()
    license_size = len(bucket.objects["LICENSE.txt"])
    manifest_size = len(bucket.objects["data/jsonl/works/manifest.json"])
    readme_size = len(bucket.objects["README.txt"])
    the_budget = budget(max_total_bytes=license_size + manifest_size + readme_size - 1)
    recorder = m.Recorder(None)
    c = m.SnapshotInspectionClient(the_budget, recorder, opener=bucket.opener)
    c.get_text("LICENSE.txt", expected_size=license_size)
    inner = m.BudgetedOpenAlexClient(bucket, the_budget, recorder)
    inner.expect("s3://openalex/data/jsonl/works/manifest.json", manifest_size)
    OpenAlexConnector(client=inner, max_attempts=1).discover_metadata()
    calls = len(bucket.calls)
    with pytest.raises(m.OpenAlexBudgetExceededError, match="total byte budget"):
        c.get_text("README.txt", expected_size=readme_size)
    assert len(bucket.calls) == calls
    assert the_budget.body_bytes == license_size + manifest_size


def test_inspection_limits_have_hard_ceilings() -> None:
    for overrides in (
        {"max_total_bytes": 100_000_001}, {"max_object_bytes": 25_000_001},
        {"max_requests": 201}, {"timeout_seconds": 31}, {"max_total_bytes": 0},
        {"max_total_bytes": "1000"},
    ):
        with pytest.raises(ValidationError):
            m.InspectionLimits(**overrides)
    defaults = m.InspectionLimits()
    assert (defaults.max_object_bytes, defaults.max_total_bytes, defaults.max_requests) == (
        25_000_000, 12_000_000, 60
    )


def test_request_budget_is_enforced() -> None:
    bucket = FakeBucket()
    c = client(bucket, budget(max_requests=2))
    c.head("data/jsonl/manifest.json")
    c.head("data/parquet/manifest.json")
    with pytest.raises(m.OpenAlexBudgetExceededError, match="request budget"):
        c.head("data/jsonl/manifest.json")
    assert len(bucket.calls) == 2


def test_session_ledger_refuses_a_run_that_would_exceed_the_total(tmp_path: Path) -> None:
    ledger = m.SessionByteLedger(tmp_path / "ledger.json")
    ledger.create(90_000_000)
    with pytest.raises(m.SessionBudgetExhausted):
        m.SnapshotByteBudget(m.InspectionLimits(max_total_bytes=10_000_001), ledger=ledger)
    the_budget = m.SnapshotByteBudget(m.InspectionLimits(max_total_bytes=10_000_000), ledger=ledger)
    the_budget.charge("k", 1234)
    the_budget.close()
    assert ledger.used() == 90_001_234
    code, output = run_script(tmp_path, FakeBucket(), "--allow-network")
    assert (code, output["error_category"]) == (2, "SessionBudgetExhausted")
    with pytest.raises(FileExistsError):
        ledger.create(0)


def test_session_ledger_keeps_the_reservation_when_a_run_dies(tmp_path: Path) -> None:
    ledger = m.SessionByteLedger(tmp_path / "ledger.json")
    ledger.create(1_000)
    dead = m.SnapshotByteBudget(m.InspectionLimits(max_total_bytes=5_000_000), ledger=ledger)
    dead.charge("k", 10)
    del dead
    assert m.SessionByteLedger(tmp_path / "ledger.json").used() == 5_001_000
    data = json.loads((tmp_path / "ledger.json").read_text())
    assert data["entries"][-1]["used_bytes"] is None


# --- listings, ranges and checksums --------------------------------------------------


def _xml_response(body: str, **headers: str) -> FakeResponse:
    return FakeResponse(200, headers, body.encode())


def test_listing_rejects_doctype_and_oversized_xml() -> None:
    bucket = FakeBucket()
    path = "/?list-type=2&prefix=&delimiter=%2F&max-keys=100"
    bucket.overrides[("GET", path)] = _xml_response(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><ListBucketResult/>'
    )
    with pytest.raises(OpenAlexEndpointUnavailableError, match="DTD"):
        client(bucket).list_objects("", delimiter=True, max_keys=100)
    bucket.overrides[("GET", path)] = _xml_response("<a/>", **{"Content-Length": "1000001"})
    with pytest.raises(m.OpenAlexBudgetExceededError, match="listing exceeds"):
        client(bucket).list_objects("", delimiter=True, max_keys=100)
    bucket.overrides[("GET", path)] = FakeResponse(200, {}, b"<a>" + b"x" * 1_000_001 + b"</a>")
    with pytest.raises(m.OpenAlexBudgetExceededError):
        client(bucket).list_objects("", delimiter=True, max_keys=100)


def test_listing_pagination_is_bounded() -> None:
    bucket = FakeBucket()
    listing = client(bucket).list_objects("data/jsonl/", delimiter=False, max_keys=2, max_pages=6)
    assert listing.pages == 4 and len(listing.objects) == 7
    with pytest.raises(m.OpenAlexBudgetExceededError, match="page limit"):
        client(bucket).list_objects("data/jsonl/", delimiter=False, max_keys=2, max_pages=3)
    with pytest.raises(ValueError):
        client(bucket).list_objects("data/jsonl/", delimiter=False, max_pages=7)
    path = "/?list-type=2&prefix=data%2Fjsonl%2F&max-keys=1000"
    bucket.overrides[("GET", path)] = _xml_response(
        f'<ListBucketResult xmlns="{NS}"><IsTruncated>true</IsTruncated></ListBucketResult>'
    )
    with pytest.raises(OpenAlexEndpointUnavailableError, match="inconsistent"):
        client(bucket).list_objects("data/jsonl/", delimiter=False)


def _ledger_range_setup() -> tuple[FakeBucket, Any, Any]:
    bucket = FakeBucket()
    c = client(bucket)
    head = c.head("data/jsonl/works/deleted_ids.csv.gz")
    return bucket, c, head


def test_range_read_requires_206_exact_content_range_and_matching_etag() -> None:
    bucket, c, head = _ledger_range_setup()
    body = c.get_range(head.key, 0, 99, head=head)
    assert body == bucket.objects[head.key][:100]
    assert bucket.calls[-1] == ("GET", "/data/jsonl/works/deleted_ids.csv.gz", "0-99")
    path = "/data/jsonl/works/deleted_ids.csv.gz"
    cases = [
        (FakeResponse(200, {"ETag": f'"{head.etag}"'}, b"x" * 100), OpenAlexEndpointUnavailableError),
        (FakeResponse(206, {"ETag": f'"{head.etag}"', "Content-Range": "bytes 0-98/176"}, b"x" * 99),
         OpenAlexTruncatedError),
        (FakeResponse(206, {"ETag": '"' + "f" * 32 + '-2"', "Content-Range": f"bytes 0-99/{head.content_length}"},
                      b"x" * 100), m.OpenAlexSnapshotChangedError),
        (FakeResponse(206, {"ETag": f'"{head.etag}"', "Content-Range": f"bytes 0-99/{head.content_length}"},
                      b"x" * 60), OpenAlexTruncatedError),
    ]
    for response, error in cases:
        bucket.overrides[("GET", path)] = response
        with pytest.raises(error):
            c.get_range(head.key, 0, 99, head=head)
        assert response.closed


def test_range_precondition_failure_is_snapshot_changed() -> None:
    bucket, c, head = _ledger_range_setup()
    stale = m.ObjectHead(head.key, head.content_length, "e" * 32 + "-2", None, None, None, None)
    with pytest.raises(m.OpenAlexSnapshotChangedError):
        c.get_range(head.key, 0, 99, head=stale)


def test_etag_change_between_head_and_get_is_snapshot_changed(tmp_path: Path) -> None:
    bucket = FakeBucket()
    key = f"data/jsonl/works/updated_date={PAIR_DAY}/part_0000.gz"
    body = bucket.objects[key]
    bucket.overrides[("GET", "/" + key)] = FakeResponse(
        200, {"Content-Length": str(len(body)), "ETag": '"' + "9" * 32 + '"'}, body
    )
    code, output = run_script(tmp_path, bucket, "--allow-network")
    assert (code, output["error_category"]) == (3, "SnapshotChanged")
    assert not (tmp_path / "out" / "snapshot_evidence.json").exists()


def test_crc64nvme_check_value_and_s3_encoding() -> None:
    assert m.crc64nvme(b"123456789") == 0xAE8B14860A799888
    assert m.crc64nvme(b"") == 0
    assert m.crc64nvme_s3(0xAE8B14860A799888) == "rosUhgp5mIg="
    assert m.md5_matches_etag("a" * 32, "a" * 32) is True
    assert m.md5_matches_etag("a" * 32, "b" * 32) is False
    assert m.md5_matches_etag("a" * 32, "a" * 32 + "-4") is None


def test_checksum_mismatch_is_explicit(tmp_path: Path) -> None:
    bucket = FakeBucket()
    key = f"data/jsonl/works/updated_date={PAIR_DAY}/part_0000.gz"
    real = bucket.opener("HEAD", "/" + key, timeout=1, headers={})
    bucket.calls.clear()
    bucket.overrides[("HEAD", "/" + key)] = FakeResponse(
        200, {**real.headers, "x-amz-checksum-crc64nvme": "AAAAAAAAAAA="}
    )
    code, output = run_script(tmp_path, bucket, "--allow-network")
    assert (code, output["error_category"]) == (1, "OpenAlexChecksumMismatchError")
    objects = tmp_path / "out" / "raw" / "objects"
    assert not objects.exists() or not any(objects.iterdir())


# --- summarisers, rights and cadence -------------------------------------------------


def _combined(**overrides: Any) -> bytes:
    manifest = {
        "date": "2026-10-14", "format": "jsonl", "meta": {"record_count": 3, "content_length": 30},
        "entities": [{"entity": "works", "record_count": 3, "content_length": 30, "files": [
            {"url": "s3://openalex/data/jsonl/works/updated_date=2026-01-01/part_0000.gz",
             "meta": {"content_length": 30, "record_count": 3}}]}],
    }
    manifest.update(overrides)
    return json.dumps(manifest).encode()


def test_combined_manifest_summary_rejects_unknown_fields_and_inconsistent_totals() -> None:
    summary = m.summarize_combined_manifest(_combined(), etag=None, version_id=None)
    assert summary.totals.files == 1 and summary.sums_equal_totals
    assert summary.entities[0].partitions == 1
    for bad in (
        _combined(extra=1),
        _combined(meta={"record_count": 4, "content_length": 30}),
        _combined(meta={"record_count": 3, "content_length": 30, "checksum": "x"}),
        _combined(date="2026-10-14T00:00:00Z"),
        _combined(format="csv"),
        _combined().replace(b'"record_count": 3}}]', b'"record_count": 2}}]'),
    ):
        with pytest.raises(ValueError):
            m.summarize_combined_manifest(bad, etag=None, version_id=None)


def test_manifest_dates_must_agree_with_each_other_and_the_expected_date() -> None:
    expected = date(2026, 10, 14)
    m.check_manifest_dates(expected, {"a": expected, "b": expected})
    for dates in ({"a": expected, "b": date(2026, 9, 23)}, {"a": date(2026, 10, 15)}):
        with pytest.raises(m.SnapshotDateMismatch):
            m.check_manifest_dates(expected, dates)


def _version(etag: str) -> Any:
    return m.ObjectVersion("LICENSE.txt", "v", True, None, etag, 10)


def test_extract_rights_reports_absent_attribution_and_full_text_as_not_published() -> None:
    license_text = "Legal code\n\nCC0 1.0 Universal\n\n c. Affirmer disclaims responsibility for clearing rights of other persons\n    that may apply.\n d. Next.\n"
    readme = "Snapshot\nLicense:   LICENSE.txt (CC0)\nDocs: https://help.openalex.org/x\n"
    notes = "NOTES\n- removed junk abstracts\n"
    paths = ["has_fulltext", "locations[].pdf_url", "abstract_inverted_index", "primary_location.license"]
    rights = m.extract_rights(license_text, readme, notes, license_sha256="0" * 64,
                              license_versions=[_version("a" * 32), _version("a" * 32)], profile_paths=paths)
    assert rights.license_title == "CC0 1.0 Universal" and rights.license_title_line.line == 3
    assert rights.statuses["attribution"] == "NOT PUBLISHED IN SNAPSHOT"
    assert rights.statuses["full_article_text"] == "NOT IN SNAPSHOT; full-text rights NOT PUBLISHED"
    assert rights.statuses["terms_of_use"] == "NOT VERIFIED"
    assert [q.line for q in rights.third_party_rights_clause] == [5, 6]
    assert rights.readme_license_line.line == 2
    assert rights.full_text_indicator_paths == ("has_fulltext", "locations[].pdf_url")
    assert rights.per_work_license_paths == ("primary_location.license",)
    assert not rights.full_text_field_present and rights.license_versions_same_etag
    published = m.extract_rights(license_text, readme + "Please cite OpenAlex.\nfull text terms\n", notes,
                                 license_sha256="0" * 64, license_versions=[], profile_paths=paths)
    assert published.statuses["attribution"] == "PUBLISHED IN SNAPSHOT"
    assert published.statuses["full_article_text"] == "STATEMENT PUBLISHED IN SNAPSHOT"


def test_extract_cadence_separates_stated_and_observed_dates() -> None:
    readme = "Snapshot released once a quarter.\n"
    notes = ("Public releases land on the second Wednesday of January, April, July and October (UTC).\n\n"
             "RELEASE 2026-10-14\n- x\nRELEASE 2026-09-23\n- y\nRELEASE 2026-07-08\n- z\nRELEASE 2025-01-29\n"
             "- after this release, the snapshot will be updated quarterly\n")
    cadence = m.extract_cadence(readme, notes, manifest_versions=3, current_year=2026)
    assert [(q.file, q.line) for q in cadence.stated] == [("README.txt", 1), ("RELEASE_NOTES.txt", 1),
                                                         ("RELEASE_NOTES.txt", 10)]
    assert cadence.release_headings == 4 and cadence.releases_by_year == {"2025": 1, "2026": 3}
    assert cadence.on_stated_schedule_current_year == (date(2026, 7, 8), date(2026, 10, 14))
    assert m.second_wednesday(2026, 10) == date(2026, 10, 14)
    assert cadence.inference.startswith("Inference")


# --- redaction -------------------------------------------------------------------------


def test_redaction_fails_closed_on_an_unknown_string_path() -> None:
    record = synthetic_work(1)
    record["authorships"][0]["author"]["alternative_names"] = ["Zelda Q."]
    with pytest.raises(m.RedactionError, match="unknown string path"):
        m.redact_work_record(record, m.Pseudonyms())
    assert m.unknown_string_paths(record) == ("authorships[].author.alternative_names[]",)
    odd = synthetic_work(2)
    odd["ids"]["Some Key"] = "x"
    assert m.unknown_string_paths(odd) == ("ids.<non-structural key>",)
    assert sum(m.policy_counts().values()) == 143


def test_redaction_fails_closed_inside_abstract_inverted_index_values() -> None:
    abstract = synthetic_work(3)
    abstract["abstract_inverted_index"] = {"word": ["Jane Q Doe"], "other": {"nested": "Jane Doe"}}
    assert m.unknown_string_paths(abstract) == (
        "abstract_inverted_index{value}", "abstract_inverted_index{value}.nested",
        "abstract_inverted_index{value}[]",
    )
    with pytest.raises(m.RedactionError, match="unknown string path"):
        m.redact_work_record(abstract, m.Pseudonyms())
    assert "Jane" in {token for path, value in m.string_leaf_paths(abstract) for token in value.split()}
    for bad in ([1.5], [-1], [True], 3, None):
        odd = synthetic_work(4)
        odd["abstract_inverted_index"] = {"word": bad}
        assert m.unknown_string_paths(odd) == ("abstract_inverted_index{value}",)
    masked = m.redact_work_record(synthetic_work(5), m.Pseudonyms())
    assert m.redaction_errors(masked) == ()
    masked["abstract_inverted_index"]["w1"] = ["Jane Q Doe"]
    assert set(m.redaction_errors(masked)) == {
        "abstract_inverted_index values are not lists of word positions",
        "abstract_inverted_index{value}[] is not in the redaction policy",
    }
    selection = m.select_fixture_records([abstract] + [synthetic_work(i) for i in range(6, 11)], count=5)
    assert selection.skipped.unknown_path == 1
    assert "abstract_inverted_index{value}[]" in selection.unknown_string_paths


def test_leak_scan_catches_tokenized_and_slugged_names() -> None:
    record = synthetic_work(1)
    record["primary_location"]["source"]["display_name"] = "Journal of zelda-quorn1 studies"
    masked = m.redact_work_record(record, m.Pseudonyms())
    assert "author name token remains" in m.leak_errors(record, masked)
    clean = synthetic_work(2)
    assert m.leak_errors(clean, m.redact_work_record(clean, m.Pseudonyms())) == ()
    selection = m.select_fixture_records([record] + [synthetic_work(i) for i in range(3, 8)], count=5)
    assert selection.skipped.leak == 1
    assert all("Zelda" not in json.dumps(r) for r in selection.records)


def test_selection_skips_records_with_email_shaped_strings() -> None:
    record = synthetic_work(1)
    record["primary_location"]["source"]["display_name"] = "Contact editor@example.org"
    others = [synthetic_work(i) for i in range(2, 7)]
    selection = m.select_fixture_records([record, *others], count=5)
    assert selection.skipped.email == 1
    assert all(m.redaction_errors(r) == () for r in selection.records)
    paratext = synthetic_work(9)
    paratext["type"] = "editorial"
    assert m.select_fixture_records([paratext, *others], count=5).skipped.type == 1
    with pytest.raises(m.RedactionError):
        m.select_fixture_records([record, paratext], count=2)


def test_redaction_preserves_types_nulls_and_equal_value_mappings() -> None:
    first, second = synthetic_work(1), synthetic_work(2)
    second["authorships"].append(json.loads(json.dumps(first["authorships"][0])))
    pseudonyms = m.Pseudonyms()
    a = m.redact_work_record(first, pseudonyms)
    b = m.redact_work_record(second, pseudonyms)
    assert a["authorships"][0]["author"] == b["authorships"][1]["author"]
    assert a["corresponding_author_ids"] == [a["authorships"][0]["author"]["id"]]
    assert a["title"] == a["display_name"] == "Title 1"
    assert a["primary_location"]["pdf_url"] is None
    assert a["primary_location"]["id"] == "loc:1"
    assert a["primary_location"]["landing_page_url"] == "https://example.org/u1"
    assert a["apc_list"] == {"value": 0, "currency": "XXX", "value_usd": 0}
    assert list(a["abstract_inverted_index"]) == ["w1", "w2", "w3"]
    assert list(a["abstract_inverted_index"].values()) == [[0], [1, 3], [2]]
    assert a["cited_by_count"] == first["cited_by_count"] and a["is_paratext"] is False
    assert list(a) == list(first)
    assert m.redaction_errors(a) == () and m.redaction_errors(first) != ()
    assert a["authorships"][0]["author"]["orcid"] == "https://orcid.org/0000-0000-0000-001X"
    assert m.orcid_check_character("000000021825009") == "7"


# --- fidelity and mapper messages ---------------------------------------------------------


def _fp(path: str, data_type: str, kind: str) -> FieldProfile:
    return FieldProfile(path=path, data_type=data_type, kind=kind, nullable=None,
                        evidence=EvidenceType.SAMPLED_OBSERVATION,
                        sampled_evidence={"sampled_present_count": EvidenceType.UNKNOWN,
                                          "sampled_missing_count": EvidenceType.UNKNOWN,
                                          "sampled_null_count": EvidenceType.UNKNOWN})


def test_compare_field_paths_applies_representation_rules() -> None:
    jsonl = [_fp("ids", "object", "struct"), _fp("ids.doi", "string", "scalar"),
             _fp("abstract_inverted_index", "null|object", "map"),
             _fp("abstract_inverted_index{value}", "array", "list"),
             _fp("abstract_inverted_index{value}[]", "integer", "scalar"),
             _fp("n", "integer", "scalar"), _fp("s", "null|string", "scalar")]
    parquet = [_fp("ids", "map", "map"), _fp("ids{key}", "string", "scalar"), _fp("ids{value}", "string", "scalar"),
               _fp("abstract_inverted_index", "string", "scalar"), _fp("n", "int32", "scalar"),
               _fp("s", "timestamp[us, tz=UTC]", "scalar"), _fp("extra", "bool", "scalar")]
    result = m.compare_field_paths(jsonl, parquet)
    assert (result.missing_in_parquet, result.type_conflicts) == ((), ())
    assert result.unobserved_in_jsonl_sample == 1
    conflict = m.compare_field_paths([_fp("n", "string", "scalar"), _fp("gone", "string", "scalar")], parquet)
    assert conflict.type_conflicts == ("n",) and conflict.missing_in_parquet == ("gone",)


def test_fidelity_comparator_detects_value_mismatch_and_float32_widening() -> None:
    import pyarrow as pa
    from datetime import UTC, datetime

    schema = pa.schema([("id", pa.string()), ("score", pa.float32()), ("t", pa.timestamp("us", tz="UTC")),
                        ("m", pa.map_(pa.string(), pa.string())), ("a", pa.string())])
    jsonl = [{"id": "W1", "score": 0.652, "t": "2026-01-01T00:00:00.123Z", "m": {"k": "v"}, "a": json.dumps({"x": [1]})},
             {"id": "W2", "score": 1.0, "t": "2026-01-01T00:00:00.000Z", "m": {}, "a": "z"}]
    rows = [{"id": "W2", "score": 1.0, "t": datetime(2026, 1, 1, tzinfo=UTC), "m": [], "a": "DIFFERENT"},
            {"id": "W1", "score": 0.652, "t": datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC),
             "m": [("k", "v")], "a": json.dumps({"x": [1]})}]
    values = m.compare_representations(jsonl, rows, schema=schema)
    assert values.records_compared == 2 and values.ids_equal and not values.row_order_equal
    assert values.mismatched_fields == {"a": 1}
    assert values.float32_widened_fields == {}
    assert values.parquet_sub_millisecond_fields == {"t": 1}
    widened = m.compare_representations(
        [{"id": "W1", "score": 0.652}],
        [{"id": "W1", "score": float(pa.array([0.652], pa.float32())[0].as_py())}],
        schema=pa.schema([("id", pa.string()), ("score", pa.float32())]),
    )
    assert widened.float32_widened_fields == {"score": 1} and widened.mismatched_fields == {}


def test_mapper_failure_messages_are_admitted_only_when_value_free() -> None:
    from research_platform.canonical.openalex import MappingError

    assert m.safe_failure(MappingError("created_date must be exact YYYY-MM-DD")) == (
        "MappingError: created_date must be exact YYYY-MM-DD"
    )
    for leaky in ("bad value https://x.org/a", "user@example.org is invalid", "id 12345678 invalid",
                  "name 'Zelda' is invalid\nsecond line"):
        assert m.safe_failure(MappingError(leaky)) == "MappingError"


# --- single download, replay and CLI ---------------------------------------------------


def test_pair_is_downloaded_once_and_profiled_from_the_local_copy(tmp_path: Path) -> None:
    bucket = FakeBucket()
    code, output = run_script(tmp_path, bucket, "--allow-network", "--keep-raw")
    assert code == 0, output
    for key in (f"/data/jsonl/works/updated_date={PAIR_DAY}/part_0000.gz",
                f"/data/parquet/works/updated_date={PAIR_DAY}/part_0000.parquet"):
        assert [c for c in bucket.calls if c[0] == "GET" and c[1] == key] == [("GET", key, None)]
    objects = sorted(p.name for p in (tmp_path / "out" / "raw" / "objects").iterdir())
    assert objects == [f"data__jsonl__works__updated_date={PAIR_DAY}__part_0000.gz",
                       f"data__parquet__works__updated_date={PAIR_DAY}__part_0000.parquet"]
    evidence = m.load_snapshot_evidence(tmp_path / "out" / "snapshot_evidence.json")
    assert evidence.profiled_pair.jsonl.head_etag_equals_get_etag
    assert evidence.profiled_pair.jsonl.crc64nvme_local == evidence.profiled_pair.jsonl.crc64nvme_s3


def test_rerun_reuses_a_verified_local_object_after_a_fresh_head(tmp_path: Path) -> None:
    assert run_script(tmp_path, FakeBucket(), "--allow-network", "--keep-raw")[0] == 0
    second = FakeBucket()
    code, _ = run_script(tmp_path, second, "--allow-network", "--keep-raw")
    assert code == 0
    pair_gets = [c for c in second.calls if c[0] == "GET" and f"updated_date={PAIR_DAY}" in c[1]]
    pair_heads = [c for c in second.calls if c[0] == "HEAD" and f"updated_date={PAIR_DAY}" in c[1]]
    assert pair_gets == [] and len(pair_heads) == 2


def test_replay_from_raw_reproduces_the_evidence_without_network(tmp_path: Path) -> None:
    bucket = FakeBucket()
    assert run_script(tmp_path, bucket, "--allow-network", "--keep-raw")[0] == 0
    raw = tmp_path / "out" / "raw"
    code, output = run_script(tmp_path, None, "--from-raw", str(raw), out="replayed")
    assert code == 0 and output["network_body_bytes"] == 0
    first = json.loads((tmp_path / "out" / "snapshot_evidence.json").read_text())
    second = json.loads((tmp_path / "replayed" / "snapshot_evidence.json").read_text())
    assert (first.pop("generated_from"), second.pop("generated_from")) == ("network", "recorded raw")
    assert first == second
    for name in ("works_manifest_digest.json", "works_redacted_sample.json"):
        assert (tmp_path / "out" / name).read_bytes() == (tmp_path / "replayed" / name).read_bytes()
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["entries"][-1]["reserved_bytes"] == ledger["entries"][-1]["used_bytes"] == 0


def test_script_refuses_without_allow_network_and_opens_no_socket(tmp_path: Path) -> None:
    bucket = FakeBucket()
    code, output = run_script(tmp_path, bucket)
    assert (code, output) == (2, {"error_category": "NetworkOptInRequired", "inspection": "refused"})
    assert bucket.calls == []
    assert not (tmp_path / "out").exists()


def test_script_refuses_without_budget_ledger(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert m.main(["--allow-network", "--out", str(tmp_path / "o"), "--expect-snapshot-date", "2026-10-14"]) == 2
    assert json.loads(capsys.readouterr().out)["error_category"] == "BudgetLedgerRequired"
    assert m.main(["--allow-network", "--budget-ledger", str(tmp_path / "missing.json"),
                   "--out", str(tmp_path / "o"), "--expect-snapshot-date", "2026-10-14"]) == 2
    assert json.loads(capsys.readouterr().out)["error_category"] == "BudgetLedgerRequired"
    assert not (tmp_path / "missing.json").exists()


def test_script_refuses_out_dir_or_ledger_inside_repository(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bucket = FakeBucket()
    for argv in (
        ["--allow-network", "--budget-ledger", str(REPO_ROOT / "ledger.json"), "--ledger-seed-bytes", "0",
         "--out", str(tmp_path / "o"), "--expect-snapshot-date", "2026-10-14"],
        ["--allow-network", "--budget-ledger", str(tmp_path / "l.json"), "--ledger-seed-bytes", "0",
         "--out", str(REPO_ROOT / "build" / "de05"), "--expect-snapshot-date", "2026-10-14"],
    ):
        assert m.main(argv, opener=bucket.opener, connector_client=bucket) == 2
        assert json.loads(capsys.readouterr().out)["error_category"] == "OutInsideRepository"
    assert bucket.calls == []
    assert not (REPO_ROOT / "ledger.json").exists() and not (REPO_ROOT / "build" / "de05").exists()


def test_script_refuses_an_unowned_raw_and_never_deletes_operator_directories(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "tmp").mkdir(parents=True)
    (out / "tmp" / "keep.txt").write_text("operator", encoding="utf-8")
    code, output = run_script(tmp_path, FakeBucket(), "--allow-network")
    assert code == 0, output
    assert (out / "tmp" / "keep.txt").read_text(encoding="utf-8") == "operator"
    assert not (out / "raw").exists()
    assert not [p.name for p in out.iterdir() if p.name.startswith("de05-tmp-")]
    (out / "raw").mkdir()
    (out / "raw" / "keep.txt").write_text("operator", encoding="utf-8")
    (out / "raw" / "responses").mkdir()
    (out / "raw" / "responses" / "keep.txt").write_text("operator", encoding="utf-8")
    bucket = FakeBucket()
    for extra in ((), ("--keep-raw",)):
        code, output = run_script(tmp_path, bucket, "--allow-network", *extra)
        assert (code, output["error_category"]) == (2, "RawNotOwned")
    assert bucket.calls == []
    assert (out / "raw" / "keep.txt").exists() and (out / "raw" / "responses" / "keep.txt").exists()
    with pytest.raises(m.RawNotOwned):
        m.Recorder(out / "raw")
    assert (out / "raw" / "responses" / "keep.txt").exists()
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert len(ledger["entries"]) == 2  # seed + the one inspected run; refusals reserve nothing


def test_marked_raw_is_reused_and_removed_only_without_keep_raw(tmp_path: Path) -> None:
    assert run_script(tmp_path, FakeBucket(), "--allow-network", "--keep-raw")[0] == 0
    raw = tmp_path / "out" / "raw"
    assert m.raw_is_owned(raw) and (raw / "responses").is_dir()
    assert run_script(tmp_path, FakeBucket(), "--allow-network")[0] == 0
    assert not raw.exists()


def test_script_fails_closed_for_a_pair_not_in_the_works_manifest(tmp_path: Path) -> None:
    bucket = FakeBucket()
    code, output = run_script(tmp_path, bucket, "--allow-network", "--pair", "2001-01-01/0007")
    assert (code, output["error_category"]) == (1, "LookupError")
    assert not [c for c in bucket.calls if "updated_date=" in c[1]]
    assert not (tmp_path / "out" / "snapshot_evidence.json").exists()


def test_profile_content_type_comes_from_the_recorded_get_only(tmp_path: Path) -> None:
    bucket = FakeBucket()
    assert run_script(tmp_path, bucket, "--allow-network", "--keep-raw")[0] == 0
    evidence = m.load_snapshot_evidence(tmp_path / "out" / "snapshot_evidence.json")
    for obj in (evidence.profiled_pair.jsonl, evidence.profiled_pair.parquet):
        assert obj.profile.content_type == "binary/octet-stream"
        assert obj.profile.evidence["content_type"] is EvidenceType.VERIFIED_METADATA
    assert run_script(tmp_path, FakeBucket(), "--allow-network", "--keep-raw")[0] == 0  # reuses the local copy
    reused = m.load_snapshot_evidence(tmp_path / "out" / "snapshot_evidence.json")
    for obj in (reused.profiled_pair.jsonl, reused.profiled_pair.parquet):
        assert obj.profile.content_type is None
        assert obj.profile.evidence["content_type"] is EvidenceType.UNKNOWN
    path = tmp_path / "local.bin"
    path.write_bytes(b"abc")
    assert "Content-Type" not in m.LocalFileClient(path).request("s3://x", timeout=1, headers={}).headers
    served = m.LocalFileClient(path, content_type="binary/octet-stream").request("s3://x", timeout=1, headers={})
    assert served.headers["Content-Type"] == "binary/octet-stream"


def test_script_refuses_unexpected_snapshot_date(tmp_path: Path) -> None:
    code, output = run_script(tmp_path, FakeBucket(snapshot="2026-10-15"), "--allow-network")
    assert (code, output["error_category"]) == (3, "SnapshotChanged")


def test_script_refuses_to_write_fixture_when_redaction_fails(tmp_path: Path) -> None:
    def add_unknown(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "new_free_text": "Zelda wrote this"}

    code, output = run_script(tmp_path, FakeBucket(mutate=add_unknown), "--allow-network")
    assert (code, output["error_category"]) == (1, "RedactionFailed")
    assert not (tmp_path / "out" / "works_redacted_sample.json").exists()
    assert not (tmp_path / "out" / "snapshot_evidence.json").exists()


def test_script_writes_aggregates_fixture_and_request_log_with_fake_bucket(tmp_path: Path) -> None:
    bucket = FakeBucket()
    code, output = run_script(tmp_path, bucket, "--allow-network")
    assert code == 0, output
    assert output["outputs"] == ["snapshot_evidence.json", "works_manifest_digest.json", "works_redacted_sample.json"]
    assert not (tmp_path / "out" / "raw").exists() and not (tmp_path / "out" / "tmp").exists()
    evidence = m.load_snapshot_evidence(tmp_path / "out" / "snapshot_evidence.json")
    assert evidence.run.requests == len(bucket.calls) == output["requests"]
    assert evidence.run.body_bytes == output["network_body_bytes"] <= evidence.run.limits.max_total_bytes
    assert [r.method for r in evidence.run.request_log].count("HEAD") == 9
    text = (tmp_path / "out" / "snapshot_evidence.json").read_text()
    assert "Zelda" not in text and "Synthetic title" not in text and "tok" not in text
    fixture = m.load_redacted_fixture(tmp_path / "out" / "works_redacted_sample.json")
    assert len(fixture.records) == 5 and all(m.redaction_errors(r) == () for r in fixture.records)
    assert "Zelda" not in (tmp_path / "out" / "works_redacted_sample.json").read_text()
    digest = m.load_works_manifest_digest(tmp_path / "out" / "works_manifest_digest.json")
    assert digest.jsonl.files == 2 and len(digest.rows) == 2
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["entries"][-1]["used_bytes"] == evidence.run.body_bytes
