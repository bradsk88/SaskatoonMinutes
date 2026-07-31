"""Typed :class:`~app.cache.Cache` for per-meeting item summaries.

The on-disk shape is ``{item_id_str: {"description": [str, ...]|null,
"chips": [{"category": str, "text": str}, ...]}}`` — one
:class:`~app.models.ItemSummary` per agenda item.

``description`` was a single string until it became bullets, and most of
the archive still holds strings.  Those load as a one-bullet Description
rather than needing a rewrite of every cached meeting; the shape is
normalized on load, so nothing downstream sees the old form.

Entries written before the aggregate existed are a bare
``[{"category", "text"}, ...]`` list.  Those load as **Legacy
ItemSummary** (no Description) rather than needing a migration; see
``docs/adr/0003-item-summary-aggregate.md``.
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

    def load(self, meeting_id: str) -> dict[str, ItemSummary] | None:
        raw = self._inner.load(meeting_id)
        if raw is None:
            return None
        return {
            item_id: ItemSummary.from_dict(entry)
            for item_id, entry in raw.items()
        }

    def save(
        self,
        meeting_id: str,
        summaries: dict[str, ItemSummary],
    ) -> None:
        payload = {
            item_id: summary.to_dict()
            for item_id, summary in summaries.items()
        }
        self._inner.save(meeting_id, payload)

    def __enter__(self) -> "ItemSummariesCache":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._inner.__exit__(exc_type, exc, tb)
