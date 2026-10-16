"""DE-05 (#126): LIVE, opt-in checks against the public OpenAlex snapshot.

Deselected by ``make test`` (``-m "not openalex_live"``) and skipped unless
``OPENALEX_LIVE_TESTS=1``. Run them only through ``make test-openalex-live`` with
``OPENALEX_LIVE_SCRATCH`` and ``OPENALEX_LIVE_BUDGET_LEDGER`` set to paths outside
the repository. The module reserves 8,000,000 body bytes and 30 requests in the
session byte ledger before its first request and settles the bytes it read when it
finishes. Requests go only to ``openalex.s3.amazonaws.com``, anonymously, through
the allow-listed client of ``scripts/openalex_snapshot_inspection_126.py``.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from research_platform.analytics.bigquery.sandbox.live import evidence_dir
from research_platform.sources.openalex import OpenAlexNetworkError
from tests.support.openalex_fake_bucket import load_inspection_script

LIVE = os.environ.get("OPENALEX_LIVE_TESTS", "").strip() == "1"
pytestmark = [
    pytest.mark.openalex_live,
    pytest.mark.skipif(not LIVE, reason="set OPENALEX_LIVE_TESTS=1 to read the public OpenAlex snapshot (bounded)"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "tests" / "fixtures" / "openalex_real" / "snapshot_evidence_2026-10-14.json"
LIVE_MAX_BYTES = 8_000_000
LIVE_MAX_REQUESTS = 30
m = load_inspection_script()


@pytest.fixture(scope="module")
def live() -> Iterator[SimpleNamespace]:
    scratch_env = os.environ.get("OPENALEX_LIVE_SCRATCH", "").strip()
    ledger_env = os.environ.get("OPENALEX_LIVE_BUDGET_LEDGER", "").strip()
    if not scratch_env or not ledger_env:
        pytest.fail("OPENALEX_LIVE_SCRATCH and OPENALEX_LIVE_BUDGET_LEDGER must be set (outside the repo)")
    try:
        scratch = evidence_dir(Path(scratch_env), REPO_ROOT)
        ledger_path = evidence_dir(Path(ledger_env), REPO_ROOT)
    except SystemExit as refusal:
        pytest.fail(str(refusal))
    ledger = m.SessionByteLedger(ledger_path)
    budget = m.SnapshotByteBudget(
        m.InspectionLimits(max_total_bytes=LIVE_MAX_BYTES, max_requests=LIVE_MAX_REQUESTS),
        ledger=ledger, label="de05-live-tests",
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    raw = scratch / "live_raw"
    recorder = m.Recorder(raw)
    run = m._Inspection(
        out=scratch, raw=raw, expected=m.load_snapshot_evidence(EVIDENCE).snapshot_date, pair=None,
        budget=budget, recorder=recorder, opener=None, connector_client=None,
        started=datetime.now(UTC), now=lambda: datetime.now(UTC), replayed=False,
    )
    state = SimpleNamespace(budget=budget, run=run, client=run.client, cache={})
    try:
        yield state
    finally:
        body_bytes = budget.body_bytes
        budget.close()
        assert body_bytes <= LIVE_MAX_BYTES and budget.requests <= LIVE_MAX_REQUESTS


def _works(live: SimpleNamespace) -> dict[str, Any]:
    if "works" not in live.cache:
        works = {}
        for fmt in ("jsonl", "parquet"):
            key = f"data/{fmt}/works/manifest.json"
            head = live.client.head(key)
            assert head.content_length <= 1_000_000
            connector = live.run._connector(fmt)
            live.run.budgeted.expect(connector.manifest_uri, head.content_length)
            selection = connector.discover_metadata()
            body = live.run.recorder.last_body()
            summary, assets = m.summarize_works_manifest(body, fmt)
            works[fmt] = SimpleNamespace(summary=summary, assets=assets, selection=selection)
        live.cache["works"] = works
    return live.cache["works"]


def _pair(live: SimpleNamespace) -> dict[str, Any]:
    if "pair" not in live.cache:
        works = _works(live)
        digest = m.works_manifest_digest(works["jsonl"].summary.date, works["jsonl"].assets, works["parquet"].assets)
        day, part = m.select_profile_pair(digest)
        jsonl_uri, parquet_uri = m.pair_uris(day, part)
        downloaded = {
            "jsonl": live.run._download(jsonl_uri, works["jsonl"].assets),
            "parquet": live.run._download(parquet_uri, works["parquet"].assets),
        }
        live.cache["pair"] = {fmt: (item, live.run._profile(item)) for fmt, item in downloaded.items()}
    return live.cache["pair"]


def test_live_snapshot_publishes_cc0_license(live: SimpleNamespace) -> None:
    head = live.client.head("LICENSE.txt")
    fetched = live.client.get_text("LICENSE.txt", expected_size=head.content_length)
    recorded = m.load_snapshot_evidence(EVIDENCE).rights
    assert fetched.sha256 == recorded.license_sha256
    assert fetched.body.decode("utf-8").splitlines()[2].strip() == "CC0 1.0 Universal"


def test_live_discovery_excludes_out_of_limit_assets(live: SimpleNamespace) -> None:
    for fmt, works in _works(live).items():
        summary = works.summary
        assert summary.within_manifest_limit
        assert 0 < summary.eligible < summary.listed
        assert summary.oversized == summary.listed - summary.eligible
        assert all(asset.byte_size <= 25_000_000 for asset in works.selection.selected)
        assert all(item.asset.byte_size > 25_000_000 for item in works.selection.skipped if item.reason == "OVERSIZED")


def test_live_pair_is_gzip_jsonl_and_snappy_parquet_with_matching_counts(live: SimpleNamespace) -> None:
    pair = _pair(live)
    (_, jsonl), (_, parquet) = pair["jsonl"], pair["parquet"]
    assert jsonl.framing.header_hex.startswith("1f8b08") and jsonl.framing.trailer_crc_ok
    assert jsonl.profile.row_count == jsonl.record_count == parquet.profile.row_count
    assert parquet.framing.codecs == ("SNAPPY",) and parquet.framing.leading_magic


def test_live_pair_checksums_match_s3_crc64nvme(live: SimpleNamespace) -> None:
    for item, obj in _pair(live).values():
        assert obj.crc64nvme_s3 is not None and obj.crc64nvme_local == obj.crc64nvme_s3
        assert obj.head_etag_equals_get_etag and obj.md5_equals_etag in (True, None)
        assert hashlib.sha256(item["path"].read_bytes()).hexdigest() == obj.sha256


def test_live_pair_field_paths_are_faithful(live: SimpleNamespace) -> None:
    pair = _pair(live)
    paths = m.compare_field_paths(pair["jsonl"][1].profile.schema_fields, pair["parquet"][1].profile.schema_fields)
    assert paths.missing_in_parquet == () and paths.type_conflicts == ()


class _ResetAfter:
    """Wraps one real response below the connector: after ``fail_after`` body bytes the
    next read raises ``ConnectionResetError`` (an ``OSError``), as a dropped socket would."""

    def __init__(self, response: Any, fail_after: int) -> None:
        self._response = response
        self._left = fail_after
        self.status = response.status
        self.headers = response.headers
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            raise ConnectionResetError("simulated connection reset after 64 KiB")
        data = self._response.read(min(size, self._left))
        self._left -= len(data)
        return data

    def close(self) -> None:
        self.closed = True
        self._response.close()


class _InterruptingClient:
    """A transport client whose responses are reset mid-body (``_ResetAfter``)."""

    def __init__(self, inner: Any, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self.responses: list[_ResetAfter] = []

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> Any:
        response = _ResetAfter(self._inner.request(uri, timeout=timeout, headers=headers), self._fail_after)
        self.responses.append(response)
        return response


def test_live_interrupted_stream_fails_closed_then_refetch_matches_checksum(live: SimpleNamespace) -> None:
    """A transport reset mid-body surfaces as ``OpenAlexNetworkError`` from the connector,
    which closes its stream and the response itself; a whole-object refetch then
    matches the recorded SHA-256."""
    item, obj = _pair(live)["jsonl"]
    asset = item["asset"]
    connector = live.run._connector("jsonl")
    budgeted = live.run.budgeted
    transport = budgeted._inner
    interrupting = _InterruptingClient(transport, 64 * 1024)
    budgeted._inner = interrupting
    try:
        budgeted.expect(asset.file_uri, asset.byte_size)
        stream = connector.fetch(asset.to_source_asset())
        with pytest.raises(OpenAlexNetworkError):
            while stream.read(16 * 1024):
                pass
    finally:
        budgeted._inner = transport
    # Closed by the connector on the error path; the test never closes them itself.
    assert stream.closed
    assert [response.closed for response in interrupting.responses] == [True]
    refetch = connector.fetch(asset.to_source_asset())
    try:
        digest = hashlib.sha256()
        while chunk := refetch.read(64 * 1024):
            digest.update(chunk)
    finally:
        refetch.close()
    assert digest.hexdigest() == obj.sha256
