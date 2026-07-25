"""Git-orphan-branch :class:`Cache` adapter.

Persists JSON-serializable values to ``{dir_name}/{key}.json`` on a named
orphan branch.  Fetches from ``origin`` on enter, holds one worktree open
for the duration of the context, pushes on exit *including on exceptions*
so partial progress survives a mid-run crash.

This adapter is self-contained: it owns its own ``_git`` subprocess
helper rather than depending on the one in :mod:`app.transcriber`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any


def _git(*args: str, cwd: str | None = None) -> str:
    """Run a git command from *cwd* and return stdout, raising on failure."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class PushAccessError(RuntimeError):
    """Raised when the remote will not accept a push from this environment."""


def verify_push_access(branch: str) -> None:
    """Fail now if we could not push *branch* later.

    ``GitBranchCache`` pushes on context **exit**, so without this a long
    run does all its work, spends whatever the work costs, and only then
    discovers it has no credentials — losing everything it computed when
    the temporary worktree is removed.  A dry-run push of the branch onto
    itself is a no-op that still exercises authentication.

    A branch that does not exist on the remote yet is fine — the first run
    of a new cache creates it — but "branch absent" and "remote
    unreachable" must not be confused, or an unauthenticated environment
    passes the check and loses its work anyway.  They are told apart by
    asking the remote for its heads first.
    """
    try:
        heads = _git("ls-remote", "--heads", "origin", branch)
    except RuntimeError as exc:
        raise PushAccessError(
            f"cannot reach origin to check push access for {branch}.\n  {exc}"
        ) from exc

    if heads.strip():
        # Push the remote branch onto itself: a no-op that cannot be
        # rejected as non-fast-forward, so a failure here is about access.
        _git("fetch", "origin", f"{branch}:refs/remotes/origin/{branch}")
        refspec = f"refs/remotes/origin/{branch}:refs/heads/{branch}"
    else:
        # Nothing to push onto, so dry-run creating it. --dry-run writes
        # nothing, so this cannot actually create the branch.
        refspec = f"HEAD:refs/heads/{branch}"

    try:
        _git("push", "--dry-run", "origin", refspec)
    except RuntimeError as exc:
        raise PushAccessError(
            f"cannot push to origin/{branch} from this environment — work "
            f"would be computed and then discarded on exit.\n  {exc}"
        ) from exc


class GitBranchCache:
    """A :class:`~app.cache.Cache` adapter backed by a git orphan branch.

    Values are JSON-serialized.  Typed callers should wrap this in a
    typed cache (e.g. :class:`~app.transcript_cache.TranscriptCache`) so
    they never see raw dicts.
    """

    def __init__(self, branch: str, dir_name: str) -> None:
        self.branch = branch
        self.dir_name = dir_name
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._worktree: str | None = None
        self._saved = False

    # ------------------------------------------------------------------
    # Context-manager lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "GitBranchCache":
        self._fetch_branch()
        self._open_worktree()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        push_exc: Exception | None = None
        try:
            # A session that saved nothing has nothing of its own to push.
            # Pushing anyway makes every read-only consumer require write
            # credentials — a fixture builder that only reads transcripts
            # would fail at exit having done its work correctly.
            if self._saved and self._has_commits():
                self._push_branch()
        except RuntimeError as e:
            push_exc = e
        finally:
            self._cleanup_worktree()
        # Don't shadow an in-body exception.  Surface push failures only
        # when the body finished cleanly — otherwise the original error
        # is the more informative one.
        if push_exc is not None and exc_type is None:
            raise push_exc

    # ------------------------------------------------------------------
    # Cache interface
    # ------------------------------------------------------------------

    def load(self, key: str) -> Any | None:
        if self._worktree is None:
            raise RuntimeError("GitBranchCache used outside its context")
        path = os.path.join(self._worktree, self.dir_name, f"{key}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def save(self, key: str, value: Any) -> None:
        if self._worktree is None:
            raise RuntimeError("GitBranchCache used outside its context")
        sub_dir = os.path.join(self._worktree, self.dir_name)
        os.makedirs(sub_dir, exist_ok=True)
        rel_path = f"{self.dir_name}/{key}.json"
        with open(os.path.join(self._worktree, rel_path), "w") as f:
            json.dump(value, f, separators=(",", ":"))
        _git("add", rel_path, cwd=self._worktree)
        _git(
            "commit", "-m",
            f"Add {self.dir_name} for {key}",
            cwd=self._worktree,
        )
        self._saved = True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_branch(self) -> None:
        try:
            _git(
                "fetch", "origin",
                f"{self.branch}:refs/remotes/origin/{self.branch}",
            )
        except RuntimeError:
            # Remote branch doesn't exist yet — that's fine, we'll create it.
            return

        try:
            _git("branch", "-f", self.branch, f"origin/{self.branch}")
        except RuntimeError:
            pass

    def _open_worktree(self) -> None:
        branch_exists = True
        try:
            _git("rev-parse", "--verify", self.branch)
        except RuntimeError:
            branch_exists = False

        self._tmpdir = tempfile.TemporaryDirectory()
        wt = os.path.join(self._tmpdir.name, "wt")

        if branch_exists:
            _git("worktree", "add", wt, self.branch)
        else:
            _git("worktree", "add", "--detach", wt)
            _git("checkout", "--orphan", self.branch, cwd=wt)
            try:
                _git("rm", "-rf", ".", cwd=wt)
            except RuntimeError:
                # Empty index (fresh repo) — nothing to clear.
                pass
        self._worktree = wt

    def _has_commits(self) -> bool:
        try:
            _git("rev-parse", "--verify", self.branch)
            return True
        except RuntimeError:
            return False

    def _push_branch(self) -> None:
        _git("push", "origin", self.branch)

    def _cleanup_worktree(self) -> None:
        if self._worktree is not None:
            try:
                _git("worktree", "remove", "--force", self._worktree)
            except RuntimeError:
                pass
            self._worktree = None
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            except OSError:
                pass
            self._tmpdir = None
