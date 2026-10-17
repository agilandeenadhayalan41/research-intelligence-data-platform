"""DE-07 (#128): offline rehearsal and pins of the opt-in live Silver release checks.

Part of ``make test``. Runs the live module's own check functions
(``tests/support/canonical_release_live.py``) against a real
``google.cloud.storage.Client`` over ``FakeGcsHttp``: nothing leaves the process.
Never executes the live module: the collection test only collects it.
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
from typing import Any, BinaryIO
from uuid import uuid4

import google.auth
import google.auth.transport.requests as google_requests
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.base import ObjectStat, ObjectStore
from tests.support import canonical_release_live as live
from tests.support import gcs_live_harness as harness
from tests.support.fake_gcs_http import BUCKET, FakeGcsHttp

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_MODULE = REPO_ROOT / "tests" / "integration" / "test_canonical_gcs_release.py"
SUPPORT_MODULES = (
    REPO_ROOT / "tests" / "support" / "canonical_release_live.py",
    REPO_ROOT / "tests" / "support" / "gcs_live_harness.py",
)
PROJECT = "synthetic-project"
TOKEN = "20000101t000000z-deadbeef"
REFUSED = pytest.fail.Exception
# Measured offline on this code (FakeGcsHttp): landing a1 and d1, then the six checks.
REHEARSAL_PUT_ATTEMPTS = 40


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "GOOGLE_CLOUD_PROJECT": PROJECT, "GCS_LIVE_CONFIRM_PROJECT": PROJECT,
        "GCS_LIVE_BUCKET": BUCKET, "GCS_LIVE_CONFIRM_BUCKET": BUCKET,
        "GCS_LIVE_PROJECT_CLASS": "operator-sandbox", "GCS_LIVE_EVIDENCE_DIR": str(tmp_path / "evidence"),
    }


def _offline_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> live.LiveContext:
    env = _env(tmp_path)
    for name in ("GOOGLE_CLOUD_PROJECT", "GCS_LIVE_BUCKET"):  # the pipelines resolve the placeholder config
        monkeypatch.setenv(name, env[name])
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    session = harness.FaultSession(FakeGcsHttp(), max_requests=live.MAX_HTTP_REQUESTS)
    client = storage.Client(project=PROJECT, credentials=AnonymousCredentials(), _http=session)
    return live.open_live_context(env, REPO_ROOT, tmp_path / "cfg", client=client, session=session, token=TOKEN)


def test_live_release_checks_pass_offline_within_the_de07_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _offline_context(tmp_path, monkeypatch)
    assert ctx.store.prefix == f"de07-live/{TOKEN}" and ctx.session.max_requests == 1_000
    live.land(ctx, ("a1", "d1"))
    landed_keys = len(ctx.store.keys)
    live.check_release_writes_parts_then_manifest(ctx)
    assert ctx.store.keys[landed_keys + 16] == ctx.releases["n"].manifest_key  # the 17th new key
    live.check_identical_release_replay_writes_nothing(ctx)
    live.check_conflicting_release_is_refused(ctx)
    live.check_rebuild_reconciles(ctx)
    live.check_next_release_lands_beside_prior(ctx)
    live.check_release_objects_hold_no_identifiers(ctx)
    assert ctx.recorder.passed == live.CHECKS_EXPECTED == 6
    assert [result["result"] for result in ctx.recorder.results] == ["PASS"] * 6
    assert ctx.store.put_attempts == REHEARSAL_PUT_ATTEMPTS < live.MAX_PUT_ATTEMPTS
    assert ctx.store.put_bytes <= live.MAX_TOTAL_PUT_BYTES
    assert ctx.session.http_requests <= 800 and ctx.session.library_delete_attempts == 0
    summary = live.live_summary(ctx)
    assert set(summary) == {
        "run_token", "prefix", "credential_type", "project_class", "label_candidate", "checks_expected", "checks",
        "checks_passed", "put_attempts", "put_bytes", "http_requests", "library_delete_attempts", "release_ids",
        "content_fingerprints", "manifest_sha256s", "google_cloud_storage_version", "pyarrow_version",
        "python_version",
    }
    assert summary["checks_passed"] == 6 and set(summary["release_ids"]) == {"n", "n1"}
    path = tmp_path / "summary.json"
    harness.write_summary(path, summary, (BUCKET, PROJECT))
    assert json.loads(path.read_text(encoding="utf-8"))["label_candidate"] == "CLOUD_SYNTHETIC_MEASURED"


def test_de07_live_caps_and_prefix_are_pinned_and_differ_from_de06() -> None:
    assert (live.LIVE_PREFIX, live.MAX_PUT_ATTEMPTS, live.MAX_OBJECT_BYTES, live.MAX_TOTAL_PUT_BYTES,
            live.MAX_HTTP_REQUESTS) == ("de07-live", 48, 262_144, 1_048_576, 1_000)
    assert (harness.LIVE_PREFIX, harness.MAX_PUT_ATTEMPTS, harness.MAX_OBJECT_BYTES, harness.MAX_TOTAL_PUT_BYTES,
            harness.MAX_HTTP_REQUESTS) == ("de06-live", 16, 262_144, 1_048_576, 200)
    assert (live.CHECKS_EXPECTED, live.ROWS_PER_PART, live.ROW_GROUP_SIZE) == (6, 1_000, 1_000)


class _Recording(ObjectStore):
    def __init__(self) -> None:
        self.puts: list[str] = []

    def put_if_absent(self, key: str, content: BinaryIO, provenance: IngestionProvenance) -> None:
        self.puts.append(key)

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(b"")

    def stat(self, key: str) -> ObjectStat | None:
        return ObjectStat(size_bytes=0)


class _Inner:
    def __init__(self) -> None:
        self.requests = 0

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        self.requests += 1
        return None


def _put(store: harness.BudgetedPrefixStore, name: str) -> None:
    data = b"x"
    store.put_if_absent(f"openalex/canonical/{name}/part.parquet", io.BytesIO(data), IngestionProvenance(
        run_id=uuid4(), source="research-platform", source_uri="urn:t",
        retrieved_at=datetime(2026, 10, 17, tzinfo=UTC), sha256=hashlib.sha256(data).hexdigest()))


def test_budget_store_and_fault_session_accept_explicit_caps_and_keep_de06_defaults() -> None:
    inner = _Recording()
    store = harness.BudgetedPrefixStore(
        inner, "t1", live_prefix=live.LIVE_PREFIX, max_put_attempts=live.MAX_PUT_ATTEMPTS,
        max_object_bytes=live.MAX_OBJECT_BYTES, max_total_put_bytes=live.MAX_TOTAL_PUT_BYTES,
    )
    for n in range(live.MAX_PUT_ATTEMPTS):
        _put(store, f"p{n}")
    with pytest.raises(REFUSED, match="live budget exceeded"):
        _put(store, "p48")
    assert len(inner.puts) == 48 and all(key.startswith("de07-live/t1/") for key in inner.puts)
    session = harness.FaultSession(_Inner(), max_requests=live.MAX_HTTP_REQUESTS)
    for _ in range(live.MAX_HTTP_REQUESTS):
        session.request("GET", "https://storage.example.invalid/storage/v1/b/b/o/x")
    with pytest.raises(REFUSED, match="live budget exceeded"):
        session.request("GET", "https://storage.example.invalid/storage/v1/b/b/o/x")
    assert session.http_requests == 1_000 and session._inner.requests == 1_000
    default_store = harness.BudgetedPrefixStore(_Recording(), "t2")
    assert (default_store.prefix, default_store.max_put_attempts, default_store.max_object_bytes,
            default_store.max_total_put_bytes) == ("de06-live/t2", 16, 262_144, 1_048_576)
    assert harness.FaultSession(_Inner()).max_requests == 200


def test_open_live_client_passes_the_de07_request_cap_to_the_fault_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: (object(), None))
    monkeypatch.setattr(google_requests, "AuthorizedSession", lambda credentials: object())
    monkeypatch.setattr(storage, "Client", lambda **kwargs: kwargs)
    env = {**_env(tmp_path), "GCS_LIVE_PROJECT_CLASS": "personal"}
    _, client, session, _ = harness.open_live_client(env, REPO_ROOT, max_http_requests=live.MAX_HTTP_REQUESTS)
    assert session.max_requests == live.MAX_HTTP_REQUESTS == 1_000 and client["_http"] is session
    _, _, default_session, _ = harness.open_live_client(env, REPO_ROOT)
    assert default_session.max_requests == harness.MAX_HTTP_REQUESTS == 200


def test_dependent_live_checks_refuse_when_release_n_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _offline_context(tmp_path, monkeypatch)
    for check in (live.check_identical_release_replay_writes_nothing, live.check_conflicting_release_is_refused,
                  live.check_rebuild_reconciles, live.check_next_release_lands_beside_prior):
        with pytest.raises(REFUSED, match=live.RELEASE_N_MISSING):
            check(ctx)
    assert [result["result"] for result in ctx.recorder.results] == ["FAIL"] * 4
    assert all(result["error_type"] != "KeyError" for result in ctx.recorder.results)
    assert ctx.store.put_attempts == 0


def test_canonical_live_module_is_deselected_by_the_make_test_expression() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    test_line = makefile.split("\ntest:\n", 1)[1].splitlines()[0]
    expression = re.search(r'-m "([^"]+)"', test_line)
    assert expression is not None
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GCS_LIVE", "CANONICAL_GCS_LIVE"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", expression.group(1), str(LIVE_MODULE.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode in (0, 5), completed.stdout + completed.stderr
    assert "6 deselected" in completed.stdout
    assert "::test_" not in completed.stdout


def test_canonical_live_tests_are_opt_in_and_deselected_by_default() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-canonical-gcs-live" in makefile.split("\n.PHONY:", 1)[1].splitlines()[0]
    for target in ("test:", "test-unit:"):
        line = makefile.split(f"\n{target}\n", 1)[1].splitlines()[0]
        assert "not gcs_live" in line
    recipe = makefile.split("\ntest-canonical-gcs-live:\n", 1)[1].split("\n\n", 1)[0]
    for needle in (
        "GCS_LIVE_CONFIRM_BUCKET", "GCS_LIVE_CONFIRM_PROJECT", "GCS_LIVE_PROJECT_CLASS", "GCS_LIVE_EVIDENCE_DIR",
        'case "$(abspath $(GCS_LIVE_EVIDENCE_DIR))/" in "$(CURDIR)/"*)', "CANONICAL_GCS_LIVE_TESTS=1",
        "tests/integration/test_canonical_gcs_release.py", "-m gcs_live", "-q --tb=no -rN", "--junitxml=",
        "--basetemp=", "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true",
    ):
        assert needle in recipe, needle
    assert "GCS_LIVE_TESTS=1" not in recipe.replace("CANONICAL_GCS_LIVE_TESTS=1", "")
    assert recipe.index("case ") < recipe.index("mkdir")
    assert recipe.index("evidence_dir(") < recipe.index("mkdir")
    assert all(line.startswith("\t@") for line in recipe.splitlines())
    module = LIVE_MODULE.read_text(encoding="utf-8")
    assert "pytest.mark.gcs_live" in module and 'os.environ.get("CANONICAL_GCS_LIVE_TESTS"' in module
    assert "pytest.mark.skipif(not LIVE" in module
    for source in (module, *(path.read_text(encoding="utf-8") for path in SUPPORT_MODULES)):
        for call in (".delete(", "list_blobs(", "create_bucket(", "set_iam_policy("):
            assert call not in source, call
    assert "write_publication_record" not in module + SUPPORT_MODULES[0].read_text(encoding="utf-8")
    for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"):
        assert "CANONICAL_GCS_LIVE_TESTS" not in workflow.read_text(encoding="utf-8")
