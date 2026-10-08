"""Atomic local consumer publication store (Step 19).

Staging is not consumer-visible. Activation is the visibility boundary.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from research_platform.service.repository import InMemoryConsumerRepository


class PublicationConflictError(ValueError):
    """Same publication_version with different content."""


@dataclass(frozen=True)
class PublicationSnapshot:
    publication_version: str
    run_id: str
    repository: InMemoryConsumerRepository
    content_fingerprint: str


class PublicationStore(Protocol):
    def stage(self, snapshot: PublicationSnapshot) -> PublicationSnapshot: ...

    def activate(self, publication_version: str) -> PublicationSnapshot: ...

    def current(self) -> PublicationSnapshot | None: ...

    def get_staged(self, publication_version: str) -> PublicationSnapshot | None: ...


class InMemoryPublicationStore:
    """Local publication store with atomic activation semantics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._staged: dict[str, PublicationSnapshot] = {}
        self._current: PublicationSnapshot | None = None
        self._activate_fail_once = False

    def stage(self, snapshot: PublicationSnapshot) -> PublicationSnapshot:
        with self._lock:
            existing = self._staged.get(snapshot.publication_version)
            if existing is not None:
                if existing.content_fingerprint == snapshot.content_fingerprint:
                    return existing  # idempotent
                raise PublicationConflictError(
                    "publication_version content conflict"
                )
            self._staged[snapshot.publication_version] = snapshot
            return snapshot

    def activate(self, publication_version: str) -> PublicationSnapshot:
        with self._lock:
            if self._activate_fail_once:
                self._activate_fail_once = False
                raise RuntimeError("simulated activation interruption")
            snap = self._staged.get(publication_version)
            if snap is None:
                raise KeyError(f"no staged publication {publication_version}")
            self._current = snap
            return snap

    def current(self) -> PublicationSnapshot | None:
        with self._lock:
            return self._current

    def get_staged(self, publication_version: str) -> PublicationSnapshot | None:
        with self._lock:
            return self._staged.get(publication_version)

    def simulate_activation_interrupt_once(self) -> None:
        """Test helper: next activate() raises; current remains unchanged."""
        with self._lock:
            self._activate_fail_once = True
