"""
Read/write per-meeting item summaries from the ``summaries`` orphan branch.

The layout mirrors the transcripts branch: one JSON file per meeting at
``summaries/{meeting_id}.json``.  Each file maps ``item_id`` (as string)
to a list of ``{"category": str, "text": str}`` entries.
"""

from __future__ import annotations

import json
import os
import tempfile

from app.transcriber import _git


SUMMARIES_BRANCH = "summaries"
SUMMARIES_DIR = "summaries"


def load_cached_summaries(meeting_id: str) -> dict[str, list[dict]] | None:
    """Return the summaries dict for *meeting_id* or ``None`` if missing."""
    for ref in [SUMMARIES_BRANCH, f"origin/{SUMMARIES_BRANCH}"]:
        blob_path = f"{ref}:{SUMMARIES_DIR}/{meeting_id}.json"
        try:
            raw = _git("show", blob_path)
            return json.loads(raw)
        except (RuntimeError, json.JSONDecodeError):
            continue
    return None


def save_summaries(meeting_id: str, summaries: dict[str, list[dict]]) -> None:
    """Commit a summaries JSON file to the orphan branch via worktree."""
    branch_exists = True
    try:
        _git("rev-parse", "--verify", SUMMARIES_BRANCH)
    except RuntimeError:
        branch_exists = False

    with tempfile.TemporaryDirectory() as tmpdir:
        worktree_path = os.path.join(tmpdir, "wt")

        if branch_exists:
            _git("worktree", "add", worktree_path, SUMMARIES_BRANCH)
        else:
            _git("worktree", "add", "--detach", worktree_path)
            _git("checkout", "--orphan", SUMMARIES_BRANCH, cwd=worktree_path)
            _git("rm", "-rf", ".", cwd=worktree_path)

        try:
            summaries_dir = os.path.join(worktree_path, SUMMARIES_DIR)
            os.makedirs(summaries_dir, exist_ok=True)

            filepath = os.path.join(summaries_dir, f"{meeting_id}.json")
            with open(filepath, "w") as f:
                json.dump(summaries, f, separators=(",", ":"))

            _git("add", f"{SUMMARIES_DIR}/{meeting_id}.json", cwd=worktree_path)
            _git(
                "commit", "-m", f"Add summaries for {meeting_id}",
                cwd=worktree_path,
            )
        finally:
            _git("worktree", "remove", "--force", worktree_path)
