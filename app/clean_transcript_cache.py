"""Typed :class:`~app.cache.Cache` for per-meeting CleanTranscripts.

A **CleanTranscript** is one agenda item's transcript slice after the
Gemini cleanup pass: fillers removed, sentences punctuated, garbled
proper nouns corrected.  Plain text — the time alignment is spent by
this point.

The on-disk shape is::

    {"cleanup_fingerprint": "<hex>", "items": {item_id_str: cleaned_text}}

Cleanup is the expensive half of summarization (~68k output tokens for
one council meeting) and the half that chip-prompt changes don't affect,
so caching it is what makes prompt iteration affordable.  See
``docs/adr/0004-cache-clean-transcript.md``.

The fingerprint is not an optimization.  A cached CleanTranscript
produced by a cleanup prompt that no longer exists would make summaries
silently reflect a stale prompt, so a fingerprint mismatch is treated as
a **miss**, not a hit.
"""

from __future__ import annotations

from app.cache_git import GitBranchCache

CLEAN_TRANSCRIPT_BRANCH = "clean-transcripts"
CLEAN_TRANSCRIPT_DIR = "clean-transcripts"


class CleanTranscriptCache:
    """Cache of per-meeting CleanTranscripts on an orphan branch.

    Keyed by ``meeting_id``; the value is a mapping of stringified
    ``item_id`` to cleaned text.  Construct with the fingerprint of the
    cleanup prompt currently in force (see
    :func:`~app.item_categorizer.cleanup_fingerprint`) — values written
    under a different fingerprint are invisible to ``load``.
    """

    def __init__(
        self,
        fingerprint: str,
        branch: str = CLEAN_TRANSCRIPT_BRANCH,
        dir_name: str = CLEAN_TRANSCRIPT_DIR,
        inner=None,
    ) -> None:
        self._fingerprint = fingerprint
        # ``inner`` lets the eval loop swap in a LocalDirCache over
        # committed fixtures instead of reaching for git.
        self._inner = inner if inner is not None else GitBranchCache(branch, dir_name)

    @classmethod
    def open(cls, fingerprint: str) -> "CleanTranscriptCache":
        return cls(fingerprint)

    def load(self, meeting_id: str) -> dict[str, str] | None:
        """Return the meeting's CleanTranscripts, or ``None``.

        ``None`` covers both "never cleaned" and "cleaned by a cleanup
        prompt that has since changed" — the caller re-cleans either way.
        """
        raw = self._inner.load(meeting_id)
        if not isinstance(raw, dict):
            return None
        if raw.get("cleanup_fingerprint") != self._fingerprint:
            return None
        items = raw.get("items")
        if not isinstance(items, dict):
            return None
        return {str(k): v for k, v in items.items()}

    def save(self, meeting_id: str, clean_transcripts: dict[str, str]) -> None:
        self._inner.save(
            meeting_id,
            {
                "cleanup_fingerprint": self._fingerprint,
                "items": {str(k): v for k, v in clean_transcripts.items()},
            },
        )

    def __enter__(self) -> "CleanTranscriptCache":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._inner.__exit__(exc_type, exc, tb)
