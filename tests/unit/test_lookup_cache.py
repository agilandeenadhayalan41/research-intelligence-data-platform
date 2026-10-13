"""Offline checks for the #118 lookup cache and its measurement script (no BigQuery)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from research_platform.service.contracts import WorkMetadataRequest
from research_platform.service.repository import build_reference_fixture
from research_platform.serving.lookup_cache import (
    DAY_SECONDS,
    CachedWorkMetadataRepository,
    TtlLruCache,
    work_cache_key,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lookup_cache_measure_118.py"
_spec = importlib.util.spec_from_file_location("lookup_cache_measure_118", SCRIPT)
m118 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m118)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountingRepo:
    """The reference repository, counting backend lookups."""

    def __init__(self) -> None:
        self.inner = build_reference_fixture()
        self.calls = 0

    def get_work_metadata(self, request):
        self.calls += 1
        return self.inner.get_work_metadata(request)

    def freshness(self):
        return self.inner.freshness()

    def get_publisher_summary(self, request):
        return ("passthrough", request)


def test_default_ttl_is_the_daily_freshness_requirement() -> None:
    assert TtlLruCache().ttl_seconds == DAY_SECONDS


def test_hit_after_first_load_and_expiry_after_ttl() -> None:
    clock = Clock()
    cache = TtlLruCache(ttl_seconds=10, clock=clock)
    loads = []
    assert cache.get_or_load("k", lambda: loads.append(1) or "v") == ("v", False)
    assert cache.get_or_load("k", lambda: loads.append(1) or "v") == ("v", True)
    clock.now = 10  # exactly the TTL: expired
    assert cache.get_or_load("k", lambda: loads.append(1) or "v2") == ("v2", False)
    assert len(loads) == 2
    assert (cache.stats.hits, cache.stats.misses, cache.stats.expirations) == (1, 2, 1)
    assert cache.stats.hit_rate == pytest.approx(1 / 3)


def test_lru_eviction_keeps_recently_used_keys() -> None:
    cache = TtlLruCache(max_entries=2)
    cache.get_or_load("a", lambda: 1)
    cache.get_or_load("b", lambda: 2)
    cache.get_or_load("a", lambda: 1)  # a is now most recent
    cache.get_or_load("c", lambda: 3)  # evicts b
    assert cache.get_or_load("a", lambda: 0) == (1, True)
    assert cache.get_or_load("b", lambda: 9) == (9, False)
    assert cache.stats.evictions == 2


def test_backend_errors_are_not_cached() -> None:
    cache = TtlLruCache()

    def boom() -> str:
        raise RuntimeError("backend down")

    with pytest.raises(RuntimeError):
        cache.get_or_load("k", boom)
    assert len(cache) == 0
    assert cache.get_or_load("k", lambda: "ok") == ("ok", False)


def test_invalid_settings_are_refused() -> None:
    with pytest.raises(ValueError):
        TtlLruCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        TtlLruCache(max_entries=0)


def test_keys_follow_the_registry_cache_key_hint() -> None:
    assert work_cache_key(WorkMetadataRequest(work_id=" W001 ")) == "work:id:W001"
    assert work_cache_key(WorkMetadataRequest(doi="10.1000/aaa")) == "work:doi:10.1000/aaa"


def test_repository_caches_hits_and_not_found_and_passes_other_calls_through() -> None:
    backend = CountingRepo()
    repo = CachedWorkMetadataRepository(backend)
    first = repo.get_work_metadata(WorkMetadataRequest(work_id="W001"))
    assert repo.last_was_hit is False
    assert repo.get_work_metadata(WorkMetadataRequest(work_id="W001")) == first
    assert repo.last_was_hit is True
    assert repo.get_work_metadata(WorkMetadataRequest(work_id="W404")) is None
    assert repo.get_work_metadata(WorkMetadataRequest(work_id="W404")) is None  # NOT_FOUND cached
    assert backend.calls == 2
    assert repo.freshness() == backend.freshness()
    assert repo.get_publisher_summary("x") == ("passthrough", "x")


def test_invalidate_forces_a_reload() -> None:
    backend = CountingRepo()
    repo = CachedWorkMetadataRepository(backend)
    repo.get_work_metadata(WorkMetadataRequest(work_id="W001"))
    repo.invalidate()
    repo.get_work_metadata(WorkMetadataRequest(work_id="W001"))
    assert backend.calls == 2


def test_zipf_sequence_is_reproducible_and_skewed() -> None:
    a = m118.zipf_sequence(100, 5000, 1.1, seed=118)
    assert a == m118.zipf_sequence(100, 5000, 1.1, seed=118)
    assert a != m118.zipf_sequence(100, 5000, 1.1, seed=119)
    assert a.count(0) > a.count(99) * 10  # rank 1 far more frequent than rank 100
    with pytest.raises(ValueError):
        m118.zipf_sequence(0, 10, 1.1, seed=1)


def test_measure_excludes_warmup_and_records_hits() -> None:
    ticks = iter(range(1000))
    clock = lambda: next(ticks) / 1000
    cache = TtlLruCache()
    records = m118.measure(lambda key: cache.get_or_load(key, lambda: key), ["a", "b"], [0, 1, 0, 0, 1], warmup=2, clock=clock)
    assert [r["i"] for r in records] == [2, 3, 4]
    assert all(r["hit"] for r in records)


def test_summary_verdict_follows_the_target() -> None:
    fast = [{"hit": True, "ms": 1.0}] * 96 + [{"hit": False, "ms": 1200.0}] * 4
    slow = [{"hit": True, "ms": 1.0}] * 90 + [{"hit": False, "ms": 1200.0}] * 10
    meets, misses = m118.summarize(fast, 200), m118.summarize(slow, 200)
    assert (meets["verdict"], meets["overall_ms_p95"], meets["hit_rate"]) == ("MEETS", 1.0, 0.96)
    assert (misses["verdict"], misses["overall_ms_p95"], misses["miss_ms_p95"]) == ("MISSES", 1200.0, 1200.0)
    assert m118.summarize([], 200)["verdict"] is None


def test_guards_refuse_before_any_cloud_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p1")
    with pytest.raises(SystemExit, match="equal --confirm-project"):
        m118.main(["--confirm-project", "p2", "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="outside the repository"):
        m118.main(["--confirm-project", "p1", "--out", str(SCRIPT.parent / "evidence")])
    with pytest.raises(SystemExit):
        m118.main(["--confirm-project", "p1", "--out", str(tmp_path / "o"), "--requests", "0"])


def test_plan_is_written_before_measuring_and_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p1")
    out = tmp_path / "o"

    class Stop(Exception):
        pass

    import google.cloud.bigquery as bq

    def no_client(*_a, **_k):
        raise Stop  # the plan must already exist when the first cloud client is built

    monkeypatch.setattr(bq, "Client", no_client)
    with pytest.raises(Stop):
        m118.main(["--confirm-project", "p1", "--out", str(out), "--zipf-s", "1.3"])
    plan = json.loads((out / "plan.json").read_text())
    assert plan["distribution"].startswith("Zipf(s=1.3)") and plan["target_p95_ms"] == 200.0
    with pytest.raises(SystemExit, match="exists"):
        m118.main(["--confirm-project", "p1", "--out", str(out)])


def test_prewarm_loads_every_key_so_measured_requests_all_hit() -> None:
    cache = TtlLruCache(max_entries=3)
    get = lambda key: cache.get_or_load(key, lambda: key)
    assert m118.prewarm(get, ["a", "b", "c"]) == 3
    assert m118.prewarm(get, ["a", "b", "c"]) == 0
    records = m118.measure(get, ["a", "b", "c"], m118.zipf_sequence(3, 50, 1.1, seed=1), warmup=0)
    assert all(r["hit"] for r in records)
