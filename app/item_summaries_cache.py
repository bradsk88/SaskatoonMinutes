"""Typed :class:`~app.cache.Cache` for per-meeting item-summary mappings.

The on-disk shape is ``{item_id_str: [{"category": str, "text": str}, ...]}``
— a dict keyed by stringified ``item_id`` whose values are lists of
:class:`~app.models.ItemSummary`.
"""

from __future__ import annotations

from app.cache_git import GitBranchCache
from app.models import ItemSummary

SUMMARIES_BRANCH = "summaries"
SUMMARIES_DIR = "summaries"


class ItemSummariesCache:
    """Cache of per-meeting item summaries on the ``summaries`` orphan branch."""

    def __init__(
        self,
        branch: str = SUMMARIES_BRANCH,
        dir_name: str = SUMMARIES_DIR,
    ) -> None:
        self._inner = GitBranchCache(branch, dir_name)

    @classmethod
    def open(cls) -> "ItemSummariesCache":
        return cls()

    def load(self, meeting_id: str) -> dict[str, list[ItemSummary]] | None:
        raw = self._inner.load(meeting_id)
        if raw is None:
            return None
        return {
            item_id: [ItemSummary.from_dict(s) for s in entries]
            for item_id, entries in raw.items()
        }

    def save(
        self,
        meeting_id: str,
        summaries: dict[str, list[ItemSummary]],
    ) -> None:
        payload = {
            item_id: [s.to_dict() for s in entries]
            for item_id, entries in summaries.items()
        }
        self._inner.save(meeting_id, payload)

    def __enter__(self) -> "ItemSummariesCache":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._inner.__exit__(exc_type, exc, tb)
