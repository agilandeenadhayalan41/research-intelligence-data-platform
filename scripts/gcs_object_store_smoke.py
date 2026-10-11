"""Manual #108 GCS smoke for GCSObjectStore: the recorded #91 checks, with evidence.

Not part of ``make test`` or CI. Run by an operator against an existing bucket
and an operator-chosen test prefix (WSL, ADC already configured)::

    GOOGLE_CLOUD_PROJECT=<project> .venv/bin/python scripts/gcs_object_store_smoke.py \\
        --confirm-project <project> --bucket <existing bucket> --prefix smoke/issue-108 \\
        --out /tmp/g108 [--run-id <id>]

The adapter checks write only synthetic objects under ``<prefix>/<run-id>/``
(one object, ``content.bin``). The script never creates buckets and never
deletes or overwrites objects through the adapter; the operator removes the
objects afterwards (or a lifecycle rule does).

``--least-privilege`` adds negative checks for an identity that may create and
read under the prefix but not delete or list. They need two sacrificial
targets the operator sets up first::

    --throwaway-key <prefix>/<path>   an existing throwaway object under the prefix
    --outside-key <path>              an object path outside the prefix

(a) bucket ``test_iam_permissions`` holds neither ``storage.objects.delete``
nor ``storage.objects.list`` (a bucket-level test does not evaluate IAM
conditions on object-name prefixes); (b) a raw upload with no generation
precondition to the throwaway object is refused with a 403 naming
``storage.objects.delete``, since overwriting needs delete; (c) a raw create
(``if_generation_match=0``, so it can never overwrite) at the outside path is
refused with a 403 naming ``storage.objects.create``. A 403 for another reason
(a retention policy or hold, VPC Service Controls, billing) fails (b) or (c):
it does not show the missing permission. If (b) or (c) succeeds, the check
fails and the evidence names the sacrificial target it modified.

Evidence: PASS/FAIL lines on stdout and ``summary.json`` in ``--out``, which
must be outside the repository: checks, object names relative to the prefix,
and the ADC principal (credential type, service-account email if any), read
after the checks so a metadata-server credential has been refreshed. Errors
are recorded as type, HTTP code and denied ``storage.*`` permissions only;
never messages, tokens or object payloads. Exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import google.auth
from google.api_core import exceptions as gexc
from google.cloud import storage

from research_platform.analytics.bigquery.sandbox.live import (
    adc_principal,
    evidence_dir,
)
from research_platform.config.models import CloudConfig, StorageConfig
from research_platform.provenance.models import IngestionProvenance
from research_platform.storage.errors import (
    ChecksumMismatchError,
    InvalidObjectKeyError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectStoreError,
)
from research_platform.storage.gcs import GCSObjectStore
from research_platform.storage.keys import validate_object_key

REPO = Path(__file__).resolve().parents[1]
# IAM permission names in a provider error message (not hosts like storage.googleapis.com).
PERMISSION = re.compile(r"\bstorage\.(?:objects|buckets|managedFolders|multipartUploads)\.[A-Za-z]+\b")
NOT_HELD = ("storage.objects.delete", "storage.objects.list")
BUCKET_TEST_NOTE = "bucket-level testIamPermissions does not evaluate IAM conditions on object-name prefixes"
PROBE = b"issue-108 least-privilege probe\n"
OCTET = "application/octet-stream"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
LP_B = "lp-b. raw upload without a generation precondition to the throwaway object refused (403, storage.objects.delete)"
LP_C = "lp-c. raw create outside the prefix refused (403, storage.objects.create)"


def connect(project: str) -> tuple[Any, storage.Client]:
    """ADC credentials (identity evidence only) and a raw client for generation reads and the negative checks.

    Scoped up front, so the client keeps (and refreshes) this credential instead
    of a scoped copy, and its service-account email is the refreshed one.
    """
    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM])
    return credentials, storage.Client(project=project, credentials=credentials)


def refused(error: BaseException | None, permission: str) -> bool:
    """A 403 that names ``permission``; any other 403 (retention, holds, VPC-SC, billing) does not show it is missing."""
    return isinstance(error, gexc.Forbidden) and permission in describe(error).get("denied", [])


def attempt(fn: Callable[[], object]) -> Exception | None:
    try:
        fn()
    except Exception as error:  # recorded by the caller, never re-raised
        return error
    return None


def describe(error: BaseException | None) -> dict[str, Any]:
    """Type, HTTP code and denied storage.* permissions; never the message, which names principals and buckets."""
    if error is None:
        return {"raised": None}
    detail: dict[str, Any] = {"raised": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, int):
        detail["code"] = int(code)
    if denied := sorted(set(PERMISSION.findall(str(error)))):
        detail["denied"] = denied
    return detail


def classified(error: BaseException | None) -> dict[str, bool]:
    """Whether a replay/conflict outcome came from the adapter (a return or typed error), not a raw provider error."""
    return {
        "classified_by_adapter": error is None or isinstance(error, ObjectStoreError),
        "raw_forbidden": isinstance(error, gexc.Forbidden),
    }


def safe_key(key: str) -> bool:
    try:
        validate_object_key(key)
    except InvalidObjectKeyError:
        return False
    return True


class Smoke:
    def __init__(self, client: Any, project: str, bucket: str, prefix: str, run_id: str) -> None:
        self.project = project
        self.bucket_name = bucket
        self.bucket = client.bucket(bucket)  # no request; generation reads and the negative checks only
        self.prefix = prefix
        self.run_id = run_id
        self.started = datetime.now(UTC)
        self.checks: list[dict[str, Any]] = []
        self.objects: list[dict[str, Any]] = []  # created by this run, relative to the prefix
        self.modified: list[str] = []  # sacrificial targets a negative check unexpectedly changed

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail != '' else ''}", flush=True)
        return bool(ok)

    def expect(self, name: str, exc_type: type[BaseException], fn: Callable[[], object], *, adapter: bool = False) -> None:
        error = attempt(fn)
        self.check(name, isinstance(error, exc_type), describe(error) | (classified(error) if adapter else {}))

    def guard(self, phase: str, fn: Callable[[], object]) -> None:
        """Run a phase; an unexpected error fails it on the record instead of losing the evidence."""
        error = attempt(fn)
        if error is not None:
            self.check(f"{phase} checks ran to completion", False, describe(error))

    def key(self, name: str) -> str:
        return f"{self.prefix}/{self.run_id}/{name}"

    def rel(self, key: str) -> str:
        return key.removeprefix(self.prefix + "/")

    def generation(self, key: str) -> int | None:
        blob = self.bucket.get_blob(key)
        return None if blob is None else blob.generation

    def provenance(self, content: bytes) -> IngestionProvenance:
        return IngestionProvenance(
            run_id=uuid4(),
            source="smoke",
            source_uri=f"synthetic://issue-108/{self.run_id}",
            retrieved_at=self.started,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def adapter_checks(self) -> None:
        """The recorded #91 sequence, through a GCSObjectStore that resolves ADC itself."""
        store = GCSObjectStore(StorageConfig(backend="gcs", bucket=self.bucket_name), CloudConfig(project_id=self.project))
        self.check("1. construct GCSObjectStore(backend=gcs) without an injected client", True)

        key = self.key("content.bin")
        content = f"issue-108 smoke {self.run_id}\n".encode()
        prov = self.provenance(content)
        if not self.check("precondition: key absent before the run", self.generation(key) is None, self.rel(key)):
            return  # a reused run id: write nothing
        store.put_if_absent(key, io.BytesIO(content), prov)
        created = self.generation(key)
        if created is not None:
            self.objects.append({"name": self.rel(key), "generation": created})
        self.check("2. put_if_absent on a new key creates the object", created is not None, {"generation": created})

        error = attempt(lambda: store.put_if_absent(key, io.BytesIO(content), prov))
        replayed = self.generation(key)
        self.check("3. identical replay is a no-op", error is None and replayed == created, describe(error) | classified(error) | {"generation": replayed})

        other = b"different bytes\n"
        self.expect("4a. conflicting bytes raise ObjectConflictError", ObjectConflictError,
                    lambda: store.put_if_absent(key, io.BytesIO(other), self.provenance(other)), adapter=True)
        self.expect("4b. same bytes, different provenance raise ObjectConflictError", ObjectConflictError,
                    lambda: store.put_if_absent(key, io.BytesIO(content), self.provenance(content)), adapter=True)
        after = self.generation(key)
        self.check("4c. conflicts leave the object unchanged", after == created, {"generation": after})

        stream = store.open(key)
        try:
            read_back = stream.read()
        finally:
            stream.close()
        self.check("5. open returns the original bytes", read_back == content, {"bytes": len(read_back)})
        self.check("5b. caller closed the stream", stream.closed)

        mismatch = self.key("mismatch.bin")
        self.expect("extra: checksum mismatch rejected", ChecksumMismatchError,
                    lambda: store.put_if_absent(mismatch, io.BytesIO(b"x"), self.provenance(b"y")))
        self.check("extra: checksum mismatch created nothing", self.generation(mismatch) is None, self.rel(mismatch))
        self.expect("extra: missing key raises ObjectNotFoundError", ObjectNotFoundError,
                    lambda: store.open(self.key("missing.bin")))
        self.expect("extra: gs:// key rejected", InvalidObjectKeyError,
                    lambda: store.open(f"gs://{self.bucket_name}/{key}"))

    def least_privilege(self, throwaway: str, outside: str) -> None:
        """Raw-client writes to the operator's sacrificial targets; each must be refused with a 403 naming the missing permission."""
        held = sorted(self.bucket.test_iam_permissions(list(NOT_HELD)))
        self.check("lp-a. bucket test_iam_permissions holds neither objects.delete nor objects.list",
                   not set(held) & set(NOT_HELD), {"held": held, "note": BUCKET_TEST_NOTE})

        before = self.generation(throwaway)
        if self.check("lp-b precondition: throwaway object exists", before is not None, self.rel(throwaway)):
            error = attempt(lambda: self.bucket.blob(throwaway).upload_from_string(PROBE, content_type=OCTET))
            after = self.generation(throwaway)
            if after != before:
                self.modified.append(self.rel(throwaway))
            self.check(LP_B, refused(error, "storage.objects.delete") and after == before,
                       describe(error) | {"generation_before": before, "generation_after": after, "sacrificial_target_modified": after != before})

        # 412 here means IAM let the create through and only the existing object stopped it: still a FAIL.
        error = attempt(lambda: self.bucket.blob(outside).upload_from_string(PROBE, content_type=OCTET, if_generation_match=0))
        if error is None:
            self.modified.append(outside)
        self.check(LP_C, refused(error, "storage.objects.create"), describe(error) | {"sacrificial_target_modified": error is None})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confirm-project", required=True)
    parser.add_argument("--bucket", required=True, help="an existing bucket (bare name); never created")
    parser.add_argument("--prefix", required=True, help="the operator's test prefix, e.g. smoke/issue-108")
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz"), help="default: UTC timestamp")
    parser.add_argument("--out", required=True, type=Path, help="evidence directory, outside the repository")
    parser.add_argument("--least-privilege", action="store_true", help="add the negative checks (needs both keys below)")
    parser.add_argument("--throwaway-key", help="an existing throwaway object under the prefix")
    parser.add_argument("--outside-key", help="an object path outside the prefix that the identity must not write")
    args = parser.parse_args(argv)

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project or project != args.confirm_project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set and equal --confirm-project")
    if not args.bucket or "/" in args.bucket or ":" in args.bucket:
        raise SystemExit("--bucket must be a bare bucket name")
    prefix, run_id = args.prefix.strip("/"), args.run_id
    if not prefix or "/" in run_id or not safe_key(f"{prefix}/{run_id}/content.bin"):
        raise SystemExit("--prefix and --run-id must form a safe object key <prefix>/<run-id>/<name>")
    throwaway, outside = args.throwaway_key, args.outside_key
    if args.least_privilege:
        if not (throwaway and outside):
            raise SystemExit("--least-privilege needs both --throwaway-key and --outside-key")
        if not safe_key(throwaway) or not throwaway.startswith(prefix + "/") or throwaway.startswith(f"{prefix}/{run_id}/"):
            raise SystemExit("--throwaway-key must be an existing object under the prefix, outside this run's folder")
        if not safe_key(outside) or outside.startswith(prefix + "/"):
            raise SystemExit("--outside-key must be an object path outside the prefix")
    elif throwaway or outside:
        raise SystemExit("--throwaway-key and --outside-key need --least-privilege")
    out = evidence_dir(args.out, REPO)
    out.mkdir(parents=True, exist_ok=True)

    credentials, client = connect(project)
    smoke = Smoke(client, project, args.bucket, prefix, run_id)
    print(f"run {run_id}: ADC {adc_principal(credentials)['type']}")
    smoke.guard("adapter", smoke.adapter_checks)
    if args.least_privilege:
        smoke.guard("least-privilege", lambda: smoke.least_privilege(throwaway, outside))
    # After the raw client's requests: a metadata-server credential reports "default" until refreshed.
    identity = adc_principal(getattr(client, "_credentials", credentials))

    failed = [c for c in smoke.checks if not c["ok"]]
    summary = {
        "run_id": run_id,
        "prefix": prefix,
        "started": smoke.started.isoformat(),
        "identity": identity | {"note": "ADC from google.auth.default() (cloud-platform scope), read after the checks; GCSObjectStore resolves the same ADC itself"},
        "checks": smoke.checks,
        "failed": len(failed),
        "objects": smoke.objects,
        "least_privilege": "NOT RUN" if not args.least_privilege else {
            "throwaway_key": smoke.rel(throwaway),
            "outside_key": outside,
            "sacrificial_modified": smoke.modified,
            "note": BUCKET_TEST_NOTE,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"\n{len(smoke.checks) - len(failed)}/{len(smoke.checks)} checks passed; "
          f"objects under <prefix>/{run_id}/: {[o['name'].rsplit('/', 1)[1] for o in smoke.objects]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
