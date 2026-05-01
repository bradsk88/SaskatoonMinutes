"""Typed key-value cache seam for derived artifacts.

The :class:`Cache` protocol is what callers depend on; concrete adapters
(:class:`InMemoryCache` here, :class:`~app.cache_git.GitBranchCache` in
production) plug in behind it.

Lifecycle contract: caches are opened as a context manager.  Setup (e.g.
fetching from a remote) happens on ``__enter__``; flush (e.g. pushing to
a remote) happens on ``__exit__`` *including on exceptions*, so partial
progress made before a crash is durable.  Per-key ``save`` is durable on
context exit, not before.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class Cache(Protocol[T]):
    """Typed cache keyed by ``meeting_id`` strings."""

    def load(self, meeting_id: str) -> T | None: ...

    def save(self, meeting_id: str, value: T) -> None: ...

    def __enter__(self) -> "Cache[T]": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...


class InMemoryCache(Generic[T]):
    """In-memory adapter for tests; no I/O, no git."""

    def __init__(self) -> None:
        self._store: dict[str, T] = {}

    def load(self, meeting_id: str) -> T | None:
        return self._store.get(meeting_id)

    def save(self, meeting_id: str, value: T) -> None:
        self._store[meeting_id] = value

    def __enter__(self) -> "InMemoryCache[T]":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
