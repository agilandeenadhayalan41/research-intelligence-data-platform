"""DE-06 (#127): offline tests of the opt-in live GCS harness and its gating.

Part of ``make test``. Never executes the live module: the collection test only
collects it, as ``make test`` already does.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO
from uuid import uuid4

import google.api_core.retry.retry_base as retry_base
import google.api_core.retry.retry_unary as retry_unary
import google.auth
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStat, ObjectStore
from research_platform.storage.errors import InvalidObjectKeyError
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.keys import validate_object_key
from tests.support import gcs_live_harness as harness
from tests.support.fake_gcs_http import BUCKET, FakeGcsHttp

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_MODULE = REPO_ROOT / "tests" / "integration" / "test_approved_gcs_raw_landing.py"
HARNESS_MODULE = REPO_ROOT / "tests" / "support" / "gcs_live_harness.py"
REFUSED = pytest.fail.Exception


def _provenance(data: bytes) -> IngestionProvenance:
    return IngestionProvenance.model_validate({
        "run_id": uuid4(), "source": "openalex", "source_uri": "s3://openalex/data/jsonl/works/part.gz",
        "retrieved_at": datetime(2026, 10, 17, tzinfo=UTC), "sha256": hashlib.sha256(data).hexdigest(),
    })


class _Recording(ObjectStore):
    def __init__(self) -> None:
        self.puts: list[str] = []
        self.reads: list[str] = []

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        self.puts.append(key)

    def open(self, key: str) -> BinaryIO:
        self.reads.append(key)
        return io.BytesIO(b"")

    def stat(self, key: str) -> ObjectStat | None:
        self.reads.append(key)
        return ObjectStat(size_bytes=0)


def _put(store: harness.BudgetedPrefixStore, size: int, name: str = "x") -> None:
    data = b"\0" * size
    store.put_if_absent(f"openalex/works/{name}/source.gz", io.BytesIO(data), _provenance(data))


def test_budget_store_refuses_oversize_total_and_extra_puts_before_delegating() -> None:
    assert (harness.MAX_PUT_ATTEMPTS, harness.MAX_OBJECT_BYTES, harness.MAX_TOTAL_PUT_BYTES) == (
        16, 262_144, 1_048_576
    )
    inner = _Recording()
    store = harness.BudgetedPrefixStore(inner, "t1")
    with pytest.raises(REFUSED, match="live budget exceeded"):
        _put(store, harness.MAX_OBJECT_BYTES + 1)
    assert inner.puts == [] and store.put_attempts == 0
    for n in range(4):
        _put(store, harness.MAX_OBJECT_BYTES, f"full{n}")  # exactly 1 MiB in total
    with pytest.raises(REFUSED, match="live budget exceeded"):
        _put(store, 1, "over-total")
    assert len(inner.puts) == 4 and store.put_bytes == harness.MAX_TOTAL_PUT_BYTES

    inner = _Recording()
    store = harness.BudgetedPrefixStore(inner, "t2")
    for n in range(harness.MAX_PUT_ATTEMPTS):
        _put(store, 1, f"small{n}")
    with pytest.raises(REFUSED, match="live budget exceeded"):
        _put(store, 1, "attempt17")
    assert len(inner.puts) == harness.MAX_PUT_ATTEMPTS


def test_budget_store_confines_every_key_under_the_run_prefix() -> None:
    inner = _Recording()
    token = harness.run_token(datetime(2026, 10, 17, 1, 2, 3, tzinfo=UTC))
    assert re.fullmatch(r"20261017t010203z-[0-9a-f]{8}", token)
    store = harness.BudgetedPrefixStore(inner, token)
    _put(store, 3, "a")
    store.open("manifests/openalex/works/run_id=r/asset_id=a/landing.json")
    store.stat("openalex/works/a/source.gz")
    assert store.keys == ["openalex/works/a/source.gz"]
    for key in inner.puts + inner.reads:
        assert key.startswith(f"de06-live/{token}/")
        validate_object_key(key)
    for bad_token in ("../escape", "a/../b", ""):
        with pytest.raises(InvalidObjectKeyError):
            harness.BudgetedPrefixStore(inner, bad_token)


@pytest.fixture
def _offline_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    state = {"now": 1_000.0}

    def sleep(seconds: float) -> None:
        state["now"] += seconds

    clock = SimpleNamespace(sleep=sleep, monotonic=lambda: state["now"], time=lambda: state["now"])
    monkeypatch.setattr(retry_unary, "time", clock)
    monkeypatch.setattr(retry_base, "time", clock)


def _store_over(session: harness.FaultSession) -> GCSObjectStore:
    client = storage.Client(project="synthetic-project", credentials=AnonymousCredentials(), _http=session)
    return GCSObjectStore(
        StorageConfig(backend="gcs", bucket=BUCKET), CloudConfig(project_id="synthetic-project"), client=client
    )


def _upload(store: GCSObjectStore, name: str, data: bytes = b"synthetic bytes") -> None:
    store.put_if_absent(f"openalex/works/{name}/source.gz", io.BytesIO(data), _provenance(data))


class _Inner:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[tuple[str, str]] = []
        self.status = status

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        self.requests.append((method, url))
        return SimpleNamespace(status_code=self.status)


@pytest.mark.usefixtures("_offline_client")
def test_fault_session_touches_only_upload_chunk_puts_not_status_queries() -> None:
    upload = "https://storage.example.invalid/upload/storage/v1/b/b/o?uploadType=resumable&upload_id=s0"
    inner = _Inner()
    session = harness.FaultSession(inner)
    session.abort_next_chunk_before_send = session.raise_after_next_final_chunk = True
    session.request("POST", upload.split("&")[0], headers={})
    session.request("PUT", upload, headers={"Content-Range": "bytes */3"})  # status query
    assert [method for method, _ in inner.requests] == ["POST", "PUT"]
    assert session.abort_next_chunk_before_send and session.raise_after_next_final_chunk  # untouched

    http = FakeGcsHttp(drop_next_chunk=True)
    session = harness.FaultSession(http)
    store = _store_over(session)
    _upload(store, "dropped")  # the library's resend passes through
    assert "openalex/works/dropped/source.gz" in http.objects

    session.abort_next_chunk_before_send = True
    with pytest.raises(harness.InjectedAbort):
        _upload(store, "aborted")
    assert "openalex/works/aborted/source.gz" not in http.objects
    assert session.abort_next_chunk_before_send is False  # fired once

    session.raise_after_next_final_chunk = True
    with pytest.raises(harness.InjectedAbort):
        _upload(store, "after-commit")
    assert "openalex/works/after-commit/source.gz" in http.objects  # sent, then the error
    assert session.raise_after_next_final_chunk is False

    _upload(store, "clean")
    assert "openalex/works/clean/source.gz" in http.objects
    assert session.http_requests == len(http.log) + 1  # the aborted chunk was counted, never sent


UPLOAD = "https://storage.example.invalid/upload/storage/v1/b/b/o?uploadType=resumable"


def test_fault_session_records_412_at_initiation_and_finalization() -> None:
    session = harness.FaultSession(_Inner(status=412))
    session.request("POST", UPLOAD, headers={})
    session.request("PUT", f"{UPLOAD}&upload_id=s0", headers={"Content-Range": "bytes 0-9/10"})
    session.request("GET", "https://storage.example.invalid/storage/v1/b/b/o/x")  # not an upload
    assert session.precondition_failed_at == ["initiation", "finalization"]


def test_fault_session_raise_after_commit_fires_only_on_the_final_chunk() -> None:
    inner = _Inner()
    session = harness.FaultSession(inner)
    session.raise_after_next_final_chunk = True
    session.request("PUT", f"{UPLOAD}&upload_id=s0", headers={"Content-Range": "bytes 0-9/20"})
    assert session.raise_after_next_final_chunk  # a non-final chunk leaves it armed
    with pytest.raises(harness.InjectedAbort):
        session.request("PUT", f"{UPLOAD}&upload_id=s0", headers={"Content-Range": "bytes 10-19/20"})
    assert session.raise_after_next_final_chunk is False and len(inner.requests) == 2  # sent, then the error


def test_fault_session_refuses_deletes_and_caps_http_requests() -> None:
    inner = _Inner()
    session = harness.FaultSession(inner)
    with pytest.raises(harness.RefusedDelete):
        session.request("DELETE", "https://storage.example.invalid/storage/v1/b/b/o/x")
    assert inner.requests == [] and session.library_delete_attempts == 1
    for _ in range(harness.MAX_HTTP_REQUESTS - 1):
        session.request("GET", "https://storage.example.invalid/storage/v1/b/b/o/x")
    assert session.http_requests == harness.MAX_HTTP_REQUESTS == 200
    with pytest.raises(REFUSED, match="live budget exceeded"):
        session.request("GET", "https://storage.example.invalid/storage/v1/b/b/o/x")
    assert len(inner.requests) == harness.MAX_HTTP_REQUESTS - 1


def test_live_client_guard_refuses_anonymous_credentials_and_emulators() -> None:
    with pytest.raises(REFUSED, match="anonymous credentials"):
        harness.require_real_cloud_client(AnonymousCredentials(), {})
    with pytest.raises(REFUSED, match="STORAGE_EMULATOR_HOST"):
        harness.require_real_cloud_client(object(), {"STORAGE_EMULATOR_HOST": "localhost:9023"})
    harness.require_real_cloud_client(object(), {})


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "GOOGLE_CLOUD_PROJECT": "synthetic-project-a", "GCS_LIVE_CONFIRM_PROJECT": "synthetic-project-a",
        "GCS_LIVE_BUCKET": "synthetic-bucket-a", "GCS_LIVE_CONFIRM_BUCKET": "synthetic-bucket-a",
        "GCS_LIVE_PROJECT_CLASS": "operator-sandbox", "GCS_LIVE_EVIDENCE_DIR": str(tmp_path / "evidence"),
    }
    env.update(overrides)
    return {key: value for key, value in env.items() if value != "<unset>"}


def test_live_settings_refuse_before_any_client_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adc_calls: list[object] = []
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: adc_calls.append(a) or (AnonymousCredentials(), None))
    settings = harness.live_settings_from_env(_env(tmp_path), REPO_ROOT)
    assert (settings.project, settings.bucket, settings.project_class) == (
        "synthetic-project-a", "synthetic-bucket-a", "operator-sandbox"
    )
    refusals = {
        "project": _env(tmp_path, GCS_LIVE_CONFIRM_PROJECT="synthetic-project-b"),
        "no project": _env(tmp_path, GOOGLE_CLOUD_PROJECT="<unset>"),
        "bucket": _env(tmp_path, GCS_LIVE_CONFIRM_BUCKET="synthetic-bucket-b"),
        "bucket uri": _env(tmp_path, GCS_LIVE_BUCKET="gs://synthetic-bucket-a", GCS_LIVE_CONFIRM_BUCKET="gs://synthetic-bucket-a"),
        "class": _env(tmp_path, GCS_LIVE_PROJECT_CLASS="<unset>"),
        "unknown class": _env(tmp_path, GCS_LIVE_PROJECT_CLASS="enterprise-prod"),
        "evidence in repo": _env(tmp_path, GCS_LIVE_EVIDENCE_DIR=str(REPO_ROOT / "de06-evidence")),
        "no evidence": _env(tmp_path, GCS_LIVE_EVIDENCE_DIR="<unset>"),
    }
    for case, env in refusals.items():
        with pytest.raises(REFUSED) as raised:
            harness.open_live_client(env, REPO_ROOT)
        message = str(raised.value)
        for value in env.values():
            assert value not in message, case
    assert adc_calls == []
    assert not (REPO_ROOT / "de06-evidence").exists()


def test_open_live_client_refuses_anonymous_adc_and_emulator_after_settings_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adc: dict[str, Any] = {"credentials": AnonymousCredentials(), "calls": 0}

    def default(*args: object, **kwargs: object) -> tuple[object, None]:
        adc["calls"] += 1
        return adc["credentials"], None

    def no_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("a storage client was built")

    monkeypatch.setattr(google.auth, "default", default)
    monkeypatch.setattr(storage, "Client", no_client)
    with pytest.raises(REFUSED, match="anonymous credentials"):
        harness.open_live_client(_env(tmp_path), REPO_ROOT)
    adc["credentials"] = object()
    with pytest.raises(REFUSED, match="STORAGE_EMULATOR_HOST"):
        harness.open_live_client(_env(tmp_path, STORAGE_EMULATOR_HOST="localhost:9023"), REPO_ROOT)
    assert adc["calls"] == 2  # both refusals came after the settings passed and ADC was read


def test_check_recorder_records_fail_with_the_type_name_only_and_reraises() -> None:
    recorder = harness.CheckRecorder()
    with recorder.check("passes") as facts:
        facts["put_attempts"] = 1
    with pytest.raises(ValueError), recorder.check("fails"):
        raise ValueError("synthetic-bucket-a")
    assert recorder.results == [
        {"check": "passes", "result": "PASS", "put_attempts": 1},
        {"check": "fails", "result": "FAIL", "error_type": "ValueError"},
    ]
    assert recorder.passed == 1


def test_write_summary_refuses_identifiers_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    forbidden = ["synthetic-bucket-a", "synthetic-project-a"]
    for value in forbidden:
        summary = {"checks": [{"check": "one", "facts": {"note": f"gs://{value}/x"}}]}
        with pytest.raises(REFUSED, match="nothing written") as raised:
            harness.write_summary(path, summary, forbidden)
        assert value not in str(raised.value)
        assert not path.exists()
    harness.write_summary(path, {"checks": []}, ["", "synthetic-bucket-a"])  # an empty needle is ignored
    assert json.loads(path.read_text(encoding="utf-8")) == {"checks": []}


def test_gcs_live_module_is_deselected_by_the_make_test_expression() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    test_line = makefile.split("\ntest:\n", 1)[1].splitlines()[0]
    expression = re.search(r'-m "([^"]+)"', test_line)
    assert expression is not None
    env = {key: value for key, value in os.environ.items() if not key.startswith("GCS_LIVE")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", expression.group(1), str(LIVE_MODULE.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode in (0, 5), completed.stdout + completed.stderr
    assert "6 deselected" in completed.stdout
    assert "::test_" not in completed.stdout


def test_gcs_live_tests_are_opt_in_and_deselected_by_default() -> None:
    assert '"gcs_live: ' in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("test:", "test-unit:"):
        line = makefile.split(f"\n{target}\n", 1)[1].splitlines()[0]
        assert "not gcs_live" in line and "not openalex_live" in line and "not postgres" in line
    assert "test-gcs-live" in makefile.split("\n.PHONY:", 1)[1].splitlines()[0]
    recipe = makefile.split("\ntest-gcs-live:\n", 1)[1].split("\n\n", 1)[0]
    for needle in (
        "GCS_LIVE_CONFIRM_BUCKET", "GCS_LIVE_CONFIRM_PROJECT", "GCS_LIVE_PROJECT_CLASS", "GCS_LIVE_EVIDENCE_DIR",
        'case "$(abspath $(GCS_LIVE_EVIDENCE_DIR))/" in "$(CURDIR)/"*)', "-q --tb=no -rN", "--junitxml=",
        "--basetemp=", "-m gcs_live", "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true",
    ):
        assert needle in recipe, needle
    assert recipe.index("case ") < recipe.index("mkdir")  # refuse an in-repo directory before writing
    assert recipe.index("evidence_dir(") < recipe.index("mkdir")  # also one reached through a symlink
    assert all(line.startswith("\t@") for line in recipe.splitlines())  # recipe lines are not echoed
    live = LIVE_MODULE.read_text(encoding="utf-8")
    assert "pytest.mark.gcs_live" in live and 'os.environ.get("GCS_LIVE_TESTS"' in live
    assert "pytest.mark.skipif(not LIVE" in live
    for source in (live, HARNESS_MODULE.read_text(encoding="utf-8")):
        for call in (".delete(", "list_blobs(", "create_bucket(", "set_iam_policy("):
            assert call not in source
    assert (harness.MAX_HTTP_REQUESTS, harness.LIVE_PREFIX) == (200, "de06-live")
    for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"):
        assert "GCS_LIVE_TESTS" not in workflow.read_text(encoding="utf-8")
