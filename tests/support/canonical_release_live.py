"""DE-07 (#128) live-release checks, shared by the opt-in live module and its offline rehearsal.

``tests/integration/test_canonical_gcs_release.py`` runs these against a real
bucket; ``tests/unit/test_canonical_gcs_release_harness.py`` runs the same
functions offline against a real ``google.cloud.storage.Client`` over
``FakeGcsHttp``, which shows the caps below suffice and leaves no live-only path
untested. Synthetic payloads only (``W99...`` ids, year-2000 partitions); nothing
reads OpenAlex. Writes stay under ``de07-live/<run-token>/`` in one existing
bucket; the code never lists or removes objects and writes no publication
record. Every refusal goes through ``gcs_live_harness.refuse``, whose messages
never repeat a supplied value.
"""

from __future__ import annotations

import io
import platform
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any, BinaryIO, Final
from uuid import UUID, uuid4

import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex import publisher
from research_platform.canonical.openalex.errors import ReleaseConflictError
from research_platform.canonical.openalex.rebuild import ReleaseInputSpec, release_input_from_source_file
from research_platform.config.loader import load_config
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import PipelineRunStatus
from research_platform.ingestion import run_openalex_deletions_local_ingest, run_openalex_works_local_ingest
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import OpenAlexDeletionAssetMetadata
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.factory import object_store_from_config
from research_platform.storage.gcs import GCSObjectStore
from tests.support import gcs_live_harness as harness
from tests.support.canonical_release_fixture import FixtureAsset, StepClock, deletion_asset, works_asset

LIVE_PREFIX: Final = "de07-live"
MAX_PUT_ATTEMPTS: Final = 48
MAX_OBJECT_BYTES: Final = 262_144
MAX_TOTAL_PUT_BYTES: Final = 1_048_576
MAX_HTTP_REQUESTS: Final = 1_000
CHECKS_EXPECTED: Final = 6
ROWS_PER_PART: Final = 1_000
ROW_GROUP_SIZE: Final = 1_000
PARTITION: Final = date(2000, 1, 1)  # synthetic partition: never a real OpenAlex release
RELEASE_N_MISSING: Final = "prior check did not build release N"


def _work(n: int, *, coauthor: bool = True) -> dict[str, Any]:
    record: dict[str, Any] = {"id": f"https://openalex.org/W990000000{n:02d}", "title": f"synthetic {n}",
                              "publication_date": "2000-01-01", "updated_date": "2000-01-01"}
    if coauthor:
        record["authorships"] = [{"author_position": "first",
                                  "author": {"id": "https://openalex.org/A99000000001", "display_name": "Synthetic"},
                                  "institutions": []}]
    return record


def live_assets() -> dict[str, FixtureAsset]:
    """``a1`` three Works sharing one author; ``d1`` deletes a known and an unknown Work;
    ``a2`` a stale ACTIVE Work the barrier blocks plus one new Work."""
    return {
        "a1": works_asset("live_a1", PARTITION, [_work(1), _work(2), _work(3, coauthor=False)]),
        "d1": deletion_asset("live_d1", date(2000, 1, 2), [("W99000000002", date(2000, 1, 2)),
                                                           ("W99000000009", date(2000, 1, 2))]),
        "a2": works_asset("live_a2", PARTITION, [_work(9), _work(4)]),
    }


class _Connector:
    def __init__(self, asset: FixtureAsset) -> None:
        self.asset = asset

    def discover_metadata(self) -> OpenAlexSampleSelection:
        assert isinstance(self.asset.metadata, OpenAlexAssetMetadata)
        return OpenAlexSampleSelection(max_files=1, max_file_size_bytes=MAX_OBJECT_BYTES, eligible_count=1,
                                       selected=(self.asset.metadata,), skipped=())

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        return io.BytesIO(self.asset.payload)


def write_gcs_config(directory: Path) -> Path:
    """A sandbox config with placeholders only: identifiers stay in the environment."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sandbox.yaml"
    path.write_text(yaml.safe_dump({
        "environment": "gcp-sandbox", "storage": {"backend": "gcs", "bucket": "${GCS_LIVE_BUCKET}"},
        "cloud": {"project_id": "${GOOGLE_CLOUD_PROJECT}"}, "warehouse": {"analytical": "duckdb"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": MAX_OBJECT_BYTES},
    }), encoding="utf-8")
    return path


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass
class LiveContext:
    settings: harness.LiveSettings
    store: harness.BudgetedPrefixStore
    session: harness.FaultSession
    credential_type: str
    token: str
    config: Path
    recorder: harness.CheckRecorder = field(default_factory=harness.CheckRecorder)
    control: InMemoryControlStore = field(default_factory=InMemoryControlStore)
    clock: StepClock = field(default_factory=lambda: StepClock(_now()))
    specs: dict[str, ReleaseInputSpec] = field(default_factory=dict)
    build_run_n: UUID = field(default_factory=uuid4)
    built_at_n: datetime = field(default_factory=_now)
    releases: dict[str, publisher.BuiltRelease] = field(default_factory=dict)


def open_live_context(
    env: Mapping[str, str],
    repo: Path,
    config_dir: Path,
    *,
    client: Any = None,
    session: harness.FaultSession | None = None,
    token: str | None = None,
) -> LiveContext:
    """Settings, a ``GCSObjectStore`` from a placeholder config, and the DE-07 caps and prefix.

    Without ``client`` this opens the real client through ``open_live_client``
    with the DE-07 request cap; the offline rehearsal injects a client over
    ``FakeGcsHttp`` and its ``FaultSession`` instead.
    """
    if client is None:
        settings, client, session, credential_type = harness.open_live_client(
            env, repo, max_http_requests=MAX_HTTP_REQUESTS
        )
    else:
        settings = harness.live_settings_from_env(env, repo)
        credential_type = "injected-offline-client"
    if session is None:
        harness.refuse("a live context needs the client's FaultSession")
    run_token = token or harness.run_token()
    config = write_gcs_config(config_dir)
    inner = object_store_from_config(load_config(config, environ=env), gcs_client=client)
    if not isinstance(inner, GCSObjectStore):
        harness.refuse("the configured store is not a GCSObjectStore")
    store = harness.BudgetedPrefixStore(
        inner, run_token, live_prefix=LIVE_PREFIX, max_put_attempts=MAX_PUT_ATTEMPTS,
        max_object_bytes=MAX_OBJECT_BYTES, max_total_put_bytes=MAX_TOTAL_PUT_BYTES,
    )
    return LiveContext(settings=settings, store=store, session=session, credential_type=credential_type,
                       token=run_token, config=config)


def land(ctx: LiveContext, names: tuple[str, ...]) -> None:
    """Land synthetic Bronze through the DE-06 opt-in path, one run per asset, one continuing clock."""
    assets = live_assets()
    for name in names:
        asset = assets[name]
        common: dict[str, Any] = dict(control_store=ctx.control, canonical_store=InMemoryCanonicalStore(),
                                      object_store=ctx.store, connector=_Connector(asset), now=ctx.clock,
                                      allow_gcs_landing=True)
        if isinstance(asset.metadata, OpenAlexDeletionAssetMetadata):
            result = run_openalex_deletions_local_ingest(ctx.config, deletion_asset=asset.metadata, **common)
        else:
            result = run_openalex_works_local_ingest(ctx.config, **common)
        if result.run.status is not PipelineRunStatus.SUCCESS:
            harness.refuse("landing a synthetic Bronze input failed")
        ctx.specs[name] = release_input_from_source_file(ctx.control.get_source_file(asset.metadata.asset_id))


def _build(ctx: LiveContext, names: tuple[str, ...], **overrides: Any) -> publisher.BuiltRelease:
    args: dict[str, Any] = {"build_run_id": ctx.build_run_n, "built_at": ctx.built_at_n,
                            "rows_per_part": ROWS_PER_PART, "row_group_size": ROW_GROUP_SIZE, **overrides}
    return publisher.build_canonical_release(bronze=ctx.store, silver=ctx.store,
                                             inputs=[ctx.specs[name] for name in names], **args)


def _release_n(ctx: LiveContext) -> publisher.BuiltRelease:
    release = ctx.releases.get("n")
    if release is None:
        harness.refuse(RELEASE_N_MISSING)
    return release


def check_release_writes_parts_then_manifest(ctx: LiveContext) -> None:
    with ctx.recorder.check("release_writes_parts_then_manifest") as facts:
        before = ctx.store.put_attempts
        release = _build(ctx, ("a1", "d1"))
        validated = publisher.validate_release(ctx.store, release.manifest.release_id)
        assert release.outcome == "written" and validated.manifest == release.manifest
        assert ctx.store.put_attempts - before == release.parts_written + 1 == 17
        assert ctx.store.keys[-1] == release.manifest_key
        ctx.releases["n"] = release
        facts.update(parts=release.parts_written, barrier_count=release.manifest.barrier_count,
                     works_rows=release.manifest.table("works").row_count)


def check_identical_release_replay_writes_nothing(ctx: LiveContext) -> None:
    with ctx.recorder.check("identical_release_replay_writes_nothing") as facts:
        _release_n(ctx)
        before = ctx.store.put_attempts
        again = _build(ctx, ("a1", "d1"))
        assert again.outcome == "existing_kept" and ctx.store.put_attempts == before
        facts.update(new_puts=0)


def check_conflicting_release_is_refused(ctx: LiveContext) -> None:
    with ctx.recorder.check("conflicting_release_is_refused_and_keeps_the_original") as facts:
        original = _release_n(ctx)
        before = ctx.store.put_attempts
        try:
            _build(ctx, ("a1",))
        except ReleaseConflictError:
            pass
        else:
            raise AssertionError("a conflicting release was accepted")
        assert ctx.store.put_attempts == before
        stored = publisher.read_release_manifest(ctx.store, original.manifest.release_id)
        assert stored == original.manifest
        facts.update(new_puts=0)


def check_rebuild_reconciles(ctx: LiveContext) -> None:
    with ctx.recorder.check("rebuild_from_gcs_bronze_reconciles") as facts:
        release = _release_n(ctx)
        report = publisher.reconcile_release(bronze=ctx.store, silver=ctx.store,
                                             release_id=release.manifest.release_id)
        assert report.passed and len(report.files) == 16
        facts.update(files_matching=sum(1 for item in report.files if item.matches), files=len(report.files))


def check_next_release_lands_beside_prior(ctx: LiveContext) -> None:
    with ctx.recorder.check("next_release_lands_beside_the_prior_release") as facts:
        prior = _release_n(ctx).manifest
        land(ctx, ("a2",))
        n1 = _build(ctx, ("a1", "d1", "a2"), build_run_id=uuid4(),
                    built_at=prior.built_at + timedelta(seconds=1), parent_release_id=prior.release_id)
        assert publisher.validate_release(ctx.store, prior.release_id).manifest == prior
        assert publisher.validate_release(ctx.store, n1.manifest.release_id).manifest == n1.manifest
        assert n1.manifest.table("works").row_count == prior.table("works").row_count + 1  # the barrier holds
        assert n1.manifest.barrier_count == 2
        ctx.releases["n1"] = n1
        facts.update(barrier_count=n1.manifest.barrier_count)


def identifier_texts(store: harness.BudgetedPrefixStore) -> list[str]:
    """Provenance of every written key plus the bytes of every ``.json`` object."""
    texts: list[str] = []
    for key in store.keys:
        stat = store.stat(key)
        texts.append("" if stat is None or stat.provenance is None else stat.provenance.model_dump_json())
        if key.endswith(".json"):
            with store.open(key) as handle:
                texts.append(handle.read().decode("utf-8"))
    return texts


def check_release_objects_hold_no_identifiers(ctx: LiveContext) -> None:
    with ctx.recorder.check("release_objects_hold_no_identifiers"):
        forbidden = (ctx.settings.bucket, ctx.settings.project, "gs://", "googleapis.com")
        if harness.contains_any(identifier_texts(ctx.store), forbidden):
            harness.refuse("identifier found in release objects")


def live_summary(ctx: LiveContext) -> dict[str, Any]:
    """Value-free evidence for ``summary.json``; ``harness.write_summary`` scrubs it."""
    return {
        "run_token": ctx.token, "prefix": ctx.store.prefix, "credential_type": ctx.credential_type,
        "project_class": ctx.settings.project_class, "label_candidate": harness.LABEL_CANDIDATE,
        "checks_expected": CHECKS_EXPECTED, "checks": ctx.recorder.results, "checks_passed": ctx.recorder.passed,
        "put_attempts": ctx.store.put_attempts, "put_bytes": ctx.store.put_bytes,
        "http_requests": ctx.session.http_requests, "library_delete_attempts": ctx.session.library_delete_attempts,
        "release_ids": {name: r.manifest.release_id for name, r in ctx.releases.items()},
        "content_fingerprints": {name: r.manifest.content_fingerprint for name, r in ctx.releases.items()},
        "manifest_sha256s": {name: r.manifest_sha256 for name, r in ctx.releases.items()},
        "google_cloud_storage_version": version("google-cloud-storage"),
        "pyarrow_version": version("pyarrow"), "python_version": platform.python_version(),
    }
