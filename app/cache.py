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

import json
import os
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

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


class LocalDirCache:
    """File-per-key JSON adapter over a plain local directory.

    No git, no remote, no flush — ``save`` writes through immediately.
    Exists so committed fixtures can back a cache: the eval loop reads
    CleanTranscripts from ``tests/fixtures/eval`` rather than re-deriving
    them, which is what keeps the loop fast and CI free of Gemini
    cleanup calls.

    Unlike :class:`~app.cache_git.GitBranchCache`, the directory is
    expected to be under version control by the caller, so writes are
    reviewable in a diff.
    """

    def __init__(self, dir_path: str, suffix: str = ".json") -> None:
        self.dir_path = dir_path
        self.suffix = suffix

    def _path(self, meeting_id: str) -> str:
        return os.path.join(self.dir_path, f"{meeting_id}{self.suffix}")

    def load(self, meeting_id: str) -> Any | None:
        path = self._path(meeting_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, meeting_id: str, value: Any) -> None:
        os.makedirs(self.dir_path, exist_ok=True)
        with open(self._path(meeting_id), "w", encoding="utf-8") as f:
            json.dump(value, f, indent=1, ensure_ascii=False)
            f.write("\n")

    def __enter__(self) -> "LocalDirCache":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
