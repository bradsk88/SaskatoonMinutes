"""Typed :class:`~app.cache.Cache` for :class:`~app.models.Transcript` values."""

from __future__ import annotations

from app.cache_git import GitBranchCache
from app.models import Transcript

TRANSCRIPT_BRANCH = "transcripts"
TRANSCRIPT_DIR = "transcripts"


class TranscriptCache:
    """Cache of meeting transcripts on the ``transcripts`` orphan branch."""

    def __init__(
        self,
        branch: str = TRANSCRIPT_BRANCH,
        dir_name: str = TRANSCRIPT_DIR,
    ) -> None:
        self._inner = GitBranchCache(branch, dir_name)

    @classmethod
    def open(cls) -> "TranscriptCache":
        return cls()

    def load(self, meeting_id: str) -> Transcript | None:
        raw = self._inner.load(meeting_id)
        if raw is None:
            return None
        return Transcript.from_dict(raw)

    def save(self, meeting_id: str, transcript: Transcript) -> None:
        self._inner.save(meeting_id, transcript.to_dict())

    def __enter__(self) -> "TranscriptCache":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._inner.__exit__(exc_type, exc, tb)
