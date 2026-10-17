"""DE-07 (#128): LIVE, opt-in Silver release checks against a real GCS bucket.

Deselected by ``make test`` (``-m "not gcs_live"``) and skipped unless
``CANONICAL_GCS_LIVE_TESTS=1``; ``GCS_LIVE_TESTS=1`` (the DE-06 gate) does not
run it. Run only through ``make test-canonical-gcs-live``, by the repository
owner, in a project the owner controls, after the owner's own approval.
Synthetic Bronze inputs land through the DE-06 opt-in path; a release is built,
replayed, conflicted, rebuilt and extended under ``de07-live/<run-token>/`` in
one existing bucket, within the caps in
``tests/support/canonical_release_live.py`` (at most 48 writes, 1 MiB and 1,000
HTTP requests). The test code never lists or removes objects and writes no
publication record; the owner removes the run prefix afterwards.

Evidence: ``GCS_LIVE_EVIDENCE_DIR/<run-token>/summary.json`` only (outside the
repository), with PASS or FAIL per check and no project, bucket or account
names. Never paste console output or ``junit-canonical.xml``.

What a run proves: the release builder, manifest-last visibility, create-only
replay and conflict, and a rebuild reading Bronze through ``GCSObjectStore``,
against real GCS. It does not prove the two-bucket layout (one bucket, one
prefix), production keys (prefixed), scale, a BigQuery load (DE-14), the
release pointer (DE-12) or enterprise readiness.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support import canonical_release_live as live
from tests.support import gcs_live_harness as harness

LIVE = os.environ.get("CANONICAL_GCS_LIVE_TESTS", "").strip() == "1"
pytestmark = [
    pytest.mark.gcs_live,
    pytest.mark.skipif(not LIVE, reason="set CANONICAL_GCS_LIVE_TESTS=1 (make test-canonical-gcs-live)"),
]
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ctx(tmp_path_factory: pytest.TempPathFactory) -> Iterator[live.LiveContext]:
    state = live.open_live_context(os.environ, REPO_ROOT, tmp_path_factory.mktemp("cfg"))
    out = state.settings.evidence_dir / state.token
    out.mkdir(parents=True, exist_ok=False)
    try:  # the summary is written even when landing fails part-way
        try:
            live.land(state, ("a1", "d1"))
        except Exception as error:
            harness.refuse(f"landing the synthetic Bronze inputs failed ({type(error).__name__})")
        yield state
    finally:
        harness.write_summary(
            out / "summary.json", live.live_summary(state), (state.settings.bucket, state.settings.project)
        )


def test_live_release_writes_parts_then_manifest(ctx: live.LiveContext) -> None:
    live.check_release_writes_parts_then_manifest(ctx)


def test_live_identical_release_replay_writes_nothing(ctx: live.LiveContext) -> None:
    live.check_identical_release_replay_writes_nothing(ctx)


def test_live_conflicting_release_is_refused_and_keeps_the_original(ctx: live.LiveContext) -> None:
    live.check_conflicting_release_is_refused(ctx)


def test_live_rebuild_from_gcs_bronze_reconciles(ctx: live.LiveContext) -> None:
    live.check_rebuild_reconciles(ctx)


def test_live_next_release_lands_beside_the_prior_release(ctx: live.LiveContext) -> None:
    live.check_next_release_lands_beside_prior(ctx)


def test_live_release_objects_hold_no_identifiers(ctx: live.LiveContext) -> None:
    live.check_release_objects_hold_no_identifiers(ctx)
