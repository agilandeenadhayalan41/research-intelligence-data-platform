"""Offline checks for the manual GCS object-store smoke script (fake storage; no cloud calls)."""

from __future__ import annotations

import importlib.util
import io
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import Forbidden

from research_platform.storage.gcs import GCSObjectStore
from tests.support.fake_gcs import FakeBlob, FakeBucket

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "gcs_object_store_smoke.py"
PROJECT = "synthetic-project"
BUCKET = "synthetic-bucket"
PREFIX = "smoke/issue-108"
RUN_ID = "20261011t000000z"
THROWAWAY = f"{PREFIX}/throwaway.bin"
OUTSIDE = "smoke/outside-108/probe.bin"
CREATE, GET, DELETE, LIST = (f"storage.objects.{p}" for p in ("create", "get", "delete", "list"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load("gcs_object_store_smoke")


@dataclass
class IamBucket(FakeBucket):
    """A FakeBucket behind a prefix-conditioned grant, like the least-privilege sandbox identity.

    Creates are allowed only under ``writable``. Overwriting without a generation
    precondition also needs storage.objects.delete, so it is refused with 403;
    ``if_generation_match=0`` on an existing object gets 412, which the adapter
    classifies. ``forbid_conflicts`` models a provider that answers that with 403.
    Overwriting a ``retained`` object is refused with a 403 that names no
    permission, as a retention policy or hold does; ``outside_denial`` replaces
    the create refusal outside ``writable`` (e.g. a VPC Service Controls 403).
    """

    writable: str = PREFIX + "/"
    granted: tuple[str, ...] = (CREATE, GET)
    forbid_conflicts: bool = False
    retained: tuple[str, ...] = ()
    outside_denial: str | None = None

    def blob(self, blob_name: str) -> IamBlob:
        return IamBlob(name=blob_name, bucket=self)

    def get_blob(self, blob_name: str) -> SimpleNamespace | None:
        stored = self.objects.get(blob_name)
        return None if stored is None else SimpleNamespace(name=blob_name, generation=stored.generation)

    def test_iam_permissions(self, permissions: list[str]) -> list[str]:
        return [p for p in permissions if p in self.granted]


class IamBlob(FakeBlob):
    def upload_from_file(self, file_obj, *, rewind=False, content_type=None, if_generation_match=None) -> None:
        bucket, exists = self.bucket, self.name in self.bucket.objects
        if not self.name.startswith(bucket.writable):
            raise Forbidden(bucket.outside_denial or f"POST https://storage.googleapis.com/upload: caller does not have {CREATE} access")
        if exists and self.name in bucket.retained and if_generation_match is None:
            raise Forbidden(f"Object '{self.name}' is subject to bucket's retention policy or object retention and cannot be deleted or overwritten")
        if exists and DELETE not in bucket.granted and (if_generation_match is None or bucket.forbid_conflicts):
            raise Forbidden(f"POST https://storage.googleapis.com/upload: caller does not have {DELETE} access")
        super().upload_from_file(file_obj, rewind=rewind, content_type=content_type, if_generation_match=if_generation_match)

    def upload_from_string(self, data: bytes, content_type: str = "text/plain", if_generation_match: int | None = None) -> None:
        self.upload_from_file(io.BytesIO(data), content_type=content_type, if_generation_match=if_generation_match)


class OneBucketClient:
    def __init__(self, bucket: IamBucket, credentials: object = None) -> None:
        self._bucket = bucket
        self._credentials = credentials or FakeServiceAccountCredentials()

    def bucket(self, name: str) -> IamBucket:
        assert name == self._bucket.name
        return self._bucket


class FakeServiceAccountCredentials:
    service_account_email = "smoke-runner@example.org"
    token = "never-recorded"  # adc_principal reads attributes only; must not reach the evidence


class FakeMetadataCredentials:
    """Like compute_engine.Credentials: the email reads "default" until a request refreshes it."""

    service_account_email = "default"
    token = "never-recorded"


class AcceptingStore(GCSObjectStore):
    """A broken adapter that treats every lost create race as an identical replay."""

    def _replay_or_conflict(self, object_name, provenance) -> None:
        return None


def _install(monkeypatch: pytest.MonkeyPatch, bucket: IamBucket, store_type: type = GCSObjectStore, credentials: object = None) -> list[dict]:
    """Route the script's raw client and its GCSObjectStore to one fake bucket; returns the store kwargs it used."""
    client, built = OneBucketClient(bucket, credentials), []

    def build(storage, cloud, **kwargs):
        built.append(kwargs)
        return store_type(storage, cloud, client=client)

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setattr(smoke, "GCSObjectStore", build)
    monkeypatch.setattr(smoke, "connect", lambda project: (client._credentials, client))
    return built


def _main(tmp_path: Path, *extra: str, prefix: str = PREFIX, run_id: str | None = RUN_ID) -> tuple[int, dict]:
    argv = ["--confirm-project", PROJECT, "--bucket", BUCKET, "--prefix", prefix, "--out", str(tmp_path / "out"), *extra]
    code = smoke.main(argv + (["--run-id", run_id] if run_id else []))
    return code, json.loads((tmp_path / "out" / "summary.json").read_text())


def _by_name(summary: dict) -> dict[str, dict]:
    return {c["check"]: c for c in summary["checks"]}


def _least_privilege_args() -> list[str]:
    return ["--least-privilege", "--throwaway-key", THROWAWAY, "--outside-key", OUTSIDE]


def test_import_creates_no_client_or_adc_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth
    from google.cloud import storage

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("no client or ADC lookup at import")

    monkeypatch.setattr(storage, "Client", refuse)
    monkeypatch.setattr(google.auth, "default", refuse)
    assert callable(_load("gcs_object_store_smoke_reimport").main)


def test_connect_builds_the_raw_client_from_scoped_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials, requested = FakeServiceAccountCredentials(), []

    def default(**kwargs: object) -> tuple[object, str]:
        requested.append(kwargs)
        return credentials, "ignored"

    monkeypatch.setattr(smoke.google.auth, "default", default)
    monkeypatch.setattr(smoke.storage, "Client", lambda **kwargs: kwargs)
    assert smoke.connect(PROJECT) == (credentials, {"project": PROJECT, "credentials": credentials})
    # Scoped up front, so the client keeps and refreshes this object rather than a scoped copy.
    assert requested == [{"scopes": ["https://www.googleapis.com/auth/cloud-platform"]}]


@pytest.mark.parametrize(
    ("env", "extra", "message"),
    [
        (None, [], "GOOGLE_CLOUD_PROJECT"),
        ("other-project", [], "GOOGLE_CLOUD_PROJECT"),
        (PROJECT, ["--prefix", "/"], "safe object key"),
        (PROJECT, ["--prefix", "smoke//issue-108"], "safe object key"),
        (PROJECT, ["--prefix", f"gs://{BUCKET}/smoke"], "safe object key"),
        (PROJECT, ["--run-id", "a/b"], "safe object key"),
        (PROJECT, ["--least-privilege", "--throwaway-key", THROWAWAY], "needs both"),
        (PROJECT, ["--least-privilege", "--outside-key", OUTSIDE], "needs both"),
        (PROJECT, ["--throwaway-key", THROWAWAY, "--outside-key", OUTSIDE], "need --least-privilege"),
        (PROJECT, ["--least-privilege", "--throwaway-key", "elsewhere/t.bin", "--outside-key", OUTSIDE], "--throwaway-key must"),
        (PROJECT, ["--least-privilege", "--throwaway-key", f"{PREFIX}/{RUN_ID}/t.bin", "--outside-key", OUTSIDE], "--throwaway-key must"),
        (PROJECT, ["--least-privilege", "--throwaway-key", THROWAWAY, "--outside-key", f"{PREFIX}/x.bin"], "--outside-key must"),
        (PROJECT, ["--bucket", f"gs://{BUCKET}"], "bare bucket name"),
    ],
)
def test_argument_guards_stop_before_any_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str | None, extra: list[str], message: str
) -> None:
    def refuse(project: str) -> None:
        raise AssertionError("guards must fail before any client is created")

    monkeypatch.setattr(smoke, "connect", refuse)
    if env is None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", env)
    argv = ["--confirm-project", PROJECT, "--bucket", BUCKET, "--prefix", PREFIX, "--run-id", RUN_ID, "--out", str(tmp_path / "out")]
    with pytest.raises(SystemExit, match=message):
        smoke.main(argv + extra)
    assert not (tmp_path / "out").exists()


def test_out_inside_the_repository_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setattr(smoke, "connect", lambda project: pytest.fail("no client before the --out guard"))
    inside = REPO / "build" / "gcs-smoke-evidence"
    with pytest.raises(SystemExit, match="outside the repository"):
        smoke.main(["--confirm-project", PROJECT, "--bucket", BUCKET, "--prefix", PREFIX, "--out", str(inside)])
    assert not inside.exists()


def test_happy_path_records_the_91_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bucket = IamBucket(name=BUCKET)
    built = _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, prefix=f"/{PREFIX}/")  # normalised to smoke/issue-108

    assert code == 0 and summary["failed"] == 0
    assert built == [{}]  # GCSObjectStore built without an injected client
    checks = _by_name(summary)
    assert list(checks)[:6] == [
        "1. construct GCSObjectStore(backend=gcs) without an injected client",
        "precondition: key absent before the run",
        "2. put_if_absent on a new key creates the object",
        "3. identical replay is a no-op",
        "4a. conflicting bytes raise ObjectConflictError",
        "4b. same bytes, different provenance raise ObjectConflictError",
    ]
    assert len(checks) == 13 and all(c["ok"] for c in checks.values())
    for name in ("3. identical replay is a no-op", "4a. conflicting bytes raise ObjectConflictError",
                 "4b. same bytes, different provenance raise ObjectConflictError"):
        assert checks[name]["detail"]["classified_by_adapter"] is True
        assert checks[name]["detail"]["raw_forbidden"] is False
    assert checks["extra: checksum mismatch rejected"]["detail"] == {"raised": "ChecksumMismatchError"}

    # Only the one synthetic object, under <prefix>/<run-id>/, always create-only.
    assert set(bucket.objects) == {f"{PREFIX}/{RUN_ID}/content.bin"}
    assert {c["if_generation_match"] for c in bucket.upload_calls} == {0}
    assert summary["prefix"] == PREFIX and summary["run_id"] == RUN_ID
    assert summary["objects"] == [{"name": f"{RUN_ID}/content.bin", "generation": 1}]
    assert summary["identity"]["type"] == f"{__name__}.FakeServiceAccountCredentials"
    assert summary["identity"]["service_account_email"] == "smoke-runner@example.org"
    assert summary["least_privilege"] == "NOT RUN"

    # Evidence never carries payloads or tokens.
    evidence = capsys.readouterr().out + (tmp_path / "out" / "summary.json").read_text()
    assert "issue-108 smoke" not in evidence and "different bytes" not in evidence
    assert "never-recorded" not in evidence


def test_default_run_id_is_a_utc_timestamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, IamBucket(name=BUCKET))
    code, summary = _main(tmp_path, run_id=None)
    assert code == 0
    assert len(summary["run_id"]) == 16 and summary["run_id"][8] == "t" and summary["run_id"].endswith("z")


def test_failing_check_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, IamBucket(name=BUCKET), store_type=AcceptingStore)
    code, summary = _main(tmp_path)
    failed = [c["check"] for c in summary["checks"] if not c["ok"]]
    assert code == 1 and summary["failed"] == 2
    assert failed == [
        "4a. conflicting bytes raise ObjectConflictError",
        "4b. same bytes, different provenance raise ObjectConflictError",
    ]


def test_raw_forbidden_on_replay_is_recorded_as_unclassified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, IamBucket(name=BUCKET, forbid_conflicts=True))
    code, summary = _main(tmp_path)
    checks = _by_name(summary)
    replay = checks["3. identical replay is a no-op"]
    assert code == 1 and not replay["ok"]
    assert replay["detail"] == {
        "raised": "Forbidden", "code": 403, "denied": [DELETE],
        "classified_by_adapter": False, "raw_forbidden": True, "generation": 1,
    }
    assert checks["4a. conflicting bytes raise ObjectConflictError"]["detail"]["raw_forbidden"] is True


def test_reused_run_id_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bucket = IamBucket(name=BUCKET)
    bucket.seed(f"{PREFIX}/{RUN_ID}/content.bin", b"earlier run")
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path)
    assert code == 1
    assert [c["check"] for c in summary["checks"]][-1] == "precondition: key absent before the run"
    assert bucket.upload_calls == [] and summary["objects"] == []


def test_unexpected_error_fails_the_phase_and_keeps_the_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, IamBucket(name=BUCKET, writable="elsewhere/"))  # no create grant on the prefix
    code, summary = _main(tmp_path)
    last = summary["checks"][-1]
    assert code == 1
    assert last == {
        "check": "adapter checks ran to completion",
        "ok": False,
        "detail": {"raised": "Forbidden", "code": 403, "denied": [CREATE]},  # message (and its host) not recorded
    }


def test_least_privilege_refusals_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bucket = IamBucket(name=BUCKET)
    bucket.seed(THROWAWAY, b"operator throwaway", generation=7)
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, *_least_privilege_args())

    checks = _by_name(summary)
    assert code == 0 and summary["failed"] == 0
    assert checks["lp-a. bucket test_iam_permissions holds neither objects.delete nor objects.list"]["detail"]["held"] == []
    raw = checks[smoke.LP_B]["detail"]
    assert raw == {"raised": "Forbidden", "code": 403, "denied": [DELETE], "generation_before": 7,
                   "generation_after": 7, "sacrificial_target_modified": False}
    assert checks[smoke.LP_C]["detail"]["denied"] == [CREATE]
    assert bucket.objects[THROWAWAY].data == b"operator throwaway" and OUTSIDE not in bucket.objects
    assert summary["least_privilege"] == {
        "throwaway_key": "throwaway.bin",
        "outside_key": OUTSIDE,
        "sacrificial_modified": [],
        "note": smoke.BUCKET_TEST_NOTE,
    }


def test_least_privilege_unexpected_success_fails_and_names_modified_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bucket = IamBucket(name=BUCKET, writable="", granted=(CREATE, GET, DELETE, LIST))  # over-privileged
    bucket.seed(THROWAWAY, b"operator throwaway")
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, *_least_privilege_args())

    failed = {c["check"]: c["detail"] for c in summary["checks"] if not c["ok"]}
    assert code == 1
    assert failed["lp-a. bucket test_iam_permissions holds neither objects.delete nor objects.list"]["held"] == [DELETE, LIST]
    raw = failed[smoke.LP_B]
    assert raw["raised"] is None and raw["generation_after"] == 2 and raw["sacrificial_target_modified"] is True
    assert failed[smoke.LP_C] == {"raised": None, "sacrificial_target_modified": True}
    assert summary["least_privilege"]["sacrificial_modified"] == ["throwaway.bin", OUTSIDE]
    assert len(failed) == 3  # the adapter checks still pass


def test_least_privilege_outside_create_past_iam_fails_without_modifying(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bucket = IamBucket(name=BUCKET, writable="smoke/")  # the grant leaks to a sibling of the prefix
    bucket.seed(THROWAWAY, b"operator throwaway")
    bucket.seed(OUTSIDE, b"operator outside object")
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, *_least_privilege_args())

    outside = _by_name(summary)[smoke.LP_C]
    assert code == 1 and not outside["ok"]
    assert outside["detail"] == {"raised": "FakePreconditionFailed", "code": 412, "sacrificial_target_modified": False}
    assert bucket.objects[OUTSIDE].data == b"operator outside object"
    assert summary["least_privilege"]["sacrificial_modified"] == []


def test_least_privilege_missing_throwaway_skips_the_raw_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bucket = IamBucket(name=BUCKET)
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, *_least_privilege_args())
    checks = _by_name(summary)
    assert code == 1 and not checks["lp-b precondition: throwaway object exists"]["ok"]
    assert smoke.LP_B not in checks
    assert THROWAWAY not in bucket.objects


def test_describe_keeps_permissions_not_hosts_or_messages() -> None:
    error = Forbidden(f"POST https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o: runner lacks {DELETE}")
    assert smoke.describe(error) == {"raised": "Forbidden", "code": 403, "denied": [DELETE]}
    assert smoke.describe(None) == {"raised": None}


def test_least_privilege_403_for_another_reason_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Over-privileged (holds delete), but a retention policy refuses the overwrite and VPC-SC the create:
    # both 403s, neither shows the missing permission.
    bucket = IamBucket(name=BUCKET, granted=(CREATE, GET, DELETE), retained=(THROWAWAY,),
                       outside_denial="Request is prohibited by organization's policy. vpcServiceControlsUniqueIdentifier: x")
    bucket.seed(THROWAWAY, b"operator throwaway", generation=7)
    _install(monkeypatch, bucket)
    code, summary = _main(tmp_path, *_least_privilege_args())

    checks = _by_name(summary)
    assert code == 1
    assert not checks[smoke.LP_B]["ok"] and not checks[smoke.LP_C]["ok"]
    assert checks[smoke.LP_B]["detail"] == {"raised": "Forbidden", "code": 403, "generation_before": 7,
                                            "generation_after": 7, "sacrificial_target_modified": False}
    assert checks[smoke.LP_C]["detail"] == {"raised": "Forbidden", "code": 403, "sacrificial_target_modified": False}
    assert bucket.objects[THROWAWAY].data == b"operator throwaway" and OUTSIDE not in bucket.objects


def test_refused_needs_the_named_permission() -> None:
    assert smoke.refused(Forbidden(f"caller does not have {DELETE} access"), DELETE)
    assert not smoke.refused(Forbidden(f"caller does not have {CREATE} access"), DELETE)
    assert not smoke.refused(Forbidden("Object is subject to bucket's retention policy"), DELETE)
    assert not smoke.refused(None, DELETE)


def test_identity_is_read_after_the_requests_refresh_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    credentials, bucket = FakeMetadataCredentials(), IamBucket(name=BUCKET)
    unrefreshed = bucket.get_blob

    def get_blob(name: str) -> object:
        credentials.service_account_email = "smoke-runner@example.org"  # the first request refreshes it
        return unrefreshed(name)

    bucket.get_blob = get_blob
    _install(monkeypatch, bucket, credentials=credentials)
    code, summary = _main(tmp_path)
    assert code == 0
    assert summary["identity"]["service_account_email"] == "smoke-runner@example.org"
    assert summary["identity"]["type"] == f"{__name__}.FakeMetadataCredentials"
    assert "never-recorded" not in (tmp_path / "out" / "summary.json").read_text()
