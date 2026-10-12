"""In-process TTL cache for Work point lookups (#118, the #23 gate's cache input).

``CachedWorkMetadataRepository`` wraps any ``ConsumerDataRepository`` and
caches ``get_work_metadata`` only, keyed by the registry's ``cache_key_hint``
(``work:id:{work_id}``, ``work:doi:{doi}``). Every other capability passes
straight through. Misses, including NOT_FOUND results, are cached too, so a
hot missing key does not reach the backend on every call.

- **Freshness:** entries expire after ``ttl_seconds``. The default is 24 h, the
  daily freshness the operator stated for #23, so a cached answer is never
  older than the requirement allows.
- **Publication:** a new snapshot should be followed by ``invalidate()``, so
  readers don't keep serving the previous one until the TTL.
- **Size:** least-recently-used entries are evicted above ``max_entries``.
- **Errors:** a backend error is never cached; it propagates unchanged.

This is a measurement-grade cache for #118, not a deployed service. A shared
cache (for example Memorystore) adds a network hop that this in-process cache
does not; #118's evidence must say which one was measured.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from research_platform.service.contracts import WorkMetadataRequest
from research_platform.service.models import FreshnessMetadata, WorkMetadataRecord
from research_platform.service.repository import ConsumerDataRepository

DAY_SECONDS = 24 * 60 * 60


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return None if total == 0 else self.hits / total


class TtlLruCache[T]:
    """A bounded mapping whose entries expire ``ttl_seconds`` after they were stored."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DAY_SECONDS,
        max_entries: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self.stats = CacheStats()

    def __len__(self) -> int:
        return len(self._entries)

    def get_or_load(self, key: str, load: Callable[[], T]) -> tuple[T, bool]:
        """The cached value and ``True``, or the freshly loaded value and ``False``."""
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None:
            stored_at, value = entry
            if now - stored_at < self.ttl_seconds:
                self._entries.move_to_end(key)
                self.stats.hits += 1
                return value, True
            del self._entries[key]
            self.stats.expirations += 1
        self.stats.misses += 1
        value = load()  # an exception propagates and nothing is stored
        self._entries[key] = (now, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
        return value, False

    def invalidate(self) -> None:
        self._entries.clear()


def work_cache_key(request: WorkMetadataRequest) -> str:
    """The registry's ``cache_key_hint`` for the request's identifier."""
    if request.work_id:
        return f"work:id:{request.work_id}"
    return f"work:doi:{request.doi}"


class CachedWorkMetadataRepository:
    """A ``ConsumerDataRepository`` that caches ``get_work_metadata`` in front of another one."""

    def __init__(self, backend: ConsumerDataRepository, cache: TtlLruCache[WorkMetadataRecord | None] | None = None) -> None:
        self._backend = backend
        self.cache: TtlLruCache[WorkMetadataRecord | None] = cache or TtlLruCache()
        self.last_was_hit: bool | None = None

    def get_work_metadata(self, request: WorkMetadataRequest) -> WorkMetadataRecord | None:
        value, hit = self.cache.get_or_load(work_cache_key(request), lambda: self._backend.get_work_metadata(request))
        self.last_was_hit = hit
        return value

    def invalidate(self) -> None:
        """Drop every entry; call after a new snapshot is published."""
        self.cache.invalidate()

    def freshness(self) -> FreshnessMetadata:
        return self._backend.freshness()

    def __getattr__(self, name: str) -> Any:
        # search_research, get_journal_metrics, ... are not cached.
        return getattr(self._backend, name)
