"""Typed cache for per-meeting attachment gists.

Lives on the ``summaries`` orphan branch in ``attachment-gists/`` —
same lifecycle as the provisional ItemSummaries it accompanies: written
before the meeting, never revised, and disposable once the meeting
happens (build_site only reads gists for Scheduled Meetings, so stale
files are simply never loaded).

On-disk shape per meeting file: ``{document_id_str: gist_dict}`` where
``document_id`` is the eSCRIBE ``DocumentId`` query parameter — stable
across agenda revisions, unlike the attachment's position or name.
"""

from __future__ import annotations

from app.cache_git import GitBranchCache
from app.models import AttachmentGist

DIR_NAME = "attachment-gists"


class AttachmentGistsCache:
    def __init__(self, branch: str = "summaries", dir_name: str = DIR_NAME) -> None:
        self._inner = GitBranchCache(branch, dir_name)

    @classmethod
    def open(cls) -> "AttachmentGistsCache":
        return cls()

    def __enter__(self) -> "AttachmentGistsCache":
        self._inner.__enter__()
        return self

    def __exit__(self, *args) -> None:
        self._inner.__exit__(*args)

    def load(self, meeting_id: str) -> dict[str, AttachmentGist]:
        raw = self._inner.load(meeting_id) or {}
        return {doc_id: AttachmentGist.from_dict(g) for doc_id, g in raw.items()}

    def save(self, meeting_id: str, gists: dict[str, AttachmentGist]) -> None:
        self._inner.save(meeting_id, {d: g.to_dict() for d, g in gists.items()})
