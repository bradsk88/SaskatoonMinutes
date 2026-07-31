"""Integration tests for app.cache_git.GitBranchCache.

Tests run against a real bare git remote (no subprocess mocking).
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from app.cache_git import GitBranchCache, _git


def _run(*args: str, cwd: str | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo_with_remote(tmp_path, monkeypatch):
    """Set up a fresh worker repo cloned from a fresh bare remote.

    Returns ``(worker_path, remote_path)``.  The cwd is set to the worker
    repo so :func:`app.cache_git._git` operates on it.
    """
    remote = tmp_path / "remote.git"
    _run("git", "init", "--bare", "-b", "main", str(remote))

    worker = tmp_path / "worker"
    worker.mkdir()
    _run("git", "init", "-b", "main", str(worker))
    _run("git", "config", "user.email", "test@example.com", cwd=str(worker))
    _run("git", "config", "user.name", "Test", cwd=str(worker))
    _run("git", "remote", "add", "origin", str(remote), cwd=str(worker))
    # Initial commit on main so HEAD exists.
    (worker / "README").write_text("hi\n")
    _run("git", "add", "README", cwd=str(worker))
    _run("git", "commit", "-m", "init", cwd=str(worker))
    _run("git", "push", "-u", "origin", "main", cwd=str(worker))

    monkeypatch.chdir(worker)
    return worker, remote


def _read_remote_blob(remote: str, branch: str, path: str) -> str:
    """Read a file from the bare remote without checking it out."""
    result = subprocess.run(
        ["git", f"--git-dir={remote}", "show", f"{branch}:{path}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _remote_log(remote: str, branch: str) -> list[str]:
    result = subprocess.run(
        ["git", f"--git-dir={remote}", "log", "--format=%s", branch],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.strip().splitlines() if line]


class TestGitBranchCacheLifecycle:
    def test_save_and_push_creates_branch_on_remote(self, repo_with_remote):
        worker, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})
        # After context exit the branch lives on the remote.
        content = _read_remote_blob(str(remote), "test-cache", "data/k1.json")
        assert json.loads(content) == {"x": 1}

    def test_round_trip_via_second_open(self, repo_with_remote):
        worker, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})

        # Simulate a fresh process: drop local branch, reopen.
        _run("git", "branch", "-D", "test-cache", cwd=str(worker))
        with GitBranchCache("test-cache", "data") as c:
            assert c.load("k1") == {"x": 1}

    def test_load_missing_returns_none(self, repo_with_remote):
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"a": 1})
            assert c.load("missing") is None

    def test_n_saves_produce_n_commits_one_push(self, repo_with_remote):
        _, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            for i in range(3):
                c.save(f"k{i}", {"i": i})
        commits = _remote_log(str(remote), "test-cache")
        assert len(commits) == 3
        # Most recent first.
        assert commits[0] == "Add data for k2"

    def test_exception_in_body_still_pushes(self, repo_with_remote):
        _, remote = repo_with_remote
        with pytest.raises(RuntimeError, match="boom"):
            with GitBranchCache("test-cache", "data") as c:
                c.save("k1", {"x": 1})
                raise RuntimeError("boom")
        # The single committed save survived.
        content = _read_remote_blob(str(remote), "test-cache", "data/k1.json")
        assert json.loads(content) == {"x": 1}

    def test_no_save_no_push_attempted(self, repo_with_remote):
        _, remote = repo_with_remote
        # Open and close with no saves: should not raise even though the
        # orphan branch was never given any commits.
        with GitBranchCache("never-used", "data"):
            pass
        # Branch shouldn't exist on remote.
        result = subprocess.run(
            ["git", f"--git-dir={remote}", "rev-parse", "--verify", "never-used"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_a_read_only_session_does_not_push(self, repo_with_remote, monkeypatch):
        """Reading an existing branch must not require write credentials."""
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})

        def _refuse(self):
            raise AssertionError("pushed after a read-only session")

        monkeypatch.setattr(GitBranchCache, "_push_branch", _refuse)
        with GitBranchCache("test-cache", "data") as c:
            assert c.load("k1") == {"x": 1}

    def test_worktree_cleaned_up_on_push_failure(
        self, repo_with_remote, monkeypatch,
    ):
        worker, _ = repo_with_remote
        cache = GitBranchCache("test-cache", "data")
        # Replace _push_branch with one that always fails.
        monkeypatch.setattr(
            cache, "_push_branch",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated push fail")),
        )
        with pytest.raises(RuntimeError, match="simulated push fail"):
            with cache:
                cache.save("k1", {"x": 1})
        # Worktree dir should be gone.
        worktrees = subprocess.run(
            ["git", "worktree", "list"], cwd=str(worker),
            capture_output=True, text=True,
        ).stdout
        assert "test-cache" not in worktrees

    def test_load_outside_context_raises(self):
        c = GitBranchCache("x", "y")
        with pytest.raises(RuntimeError, match="outside its context"):
            c.load("k")
        with pytest.raises(RuntimeError, match="outside its context"):
            c.save("k", {})

    def test_two_caches_on_the_same_branch_can_be_open_at_once(
        self, repo_with_remote,
    ):
        """Regression: summarize_meetings.py opens ItemSummariesCache and
        AttachmentGistsCache together, both on the ``summaries`` branch.
        Git refuses to check out one branch into two worktrees, so the
        second ``__enter__`` used to fail with "already used by worktree".
        """
        _, remote = repo_with_remote
        with GitBranchCache("shared-branch", "summaries") as summaries, \
                GitBranchCache("shared-branch", "attachment-gists") as gists:
            summaries.save("m1", {"7": []})
            gists.save("m1", {"a": 1})

        content = _read_remote_blob(
            str(remote), "shared-branch", "summaries/m1.json",
        )
        assert json.loads(content) == {"7": []}
        content = _read_remote_blob(
            str(remote), "shared-branch", "attachment-gists/m1.json",
        )
        assert json.loads(content) == {"a": 1}

    def test_shared_worktree_removed_only_after_last_user_exits(
        self, repo_with_remote,
    ):
        worker, _ = repo_with_remote
        outer = GitBranchCache("shared-branch", "summaries")
        outer.__enter__()
        with GitBranchCache("shared-branch", "attachment-gists") as inner:
            inner.save("m1", {"a": 1})
            worktrees = subprocess.run(
                ["git", "worktree", "list"], cwd=str(worker),
                capture_output=True, text=True,
            ).stdout
            assert "shared-branch" in worktrees
        # Inner exited but outer still holds the checkout open.
        worktrees = subprocess.run(
            ["git", "worktree", "list"], cwd=str(worker),
            capture_output=True, text=True,
        ).stdout
        assert "shared-branch" in worktrees
        outer.__exit__(None, None, None)
        worktrees = subprocess.run(
            ["git", "worktree", "list"], cwd=str(worker),
            capture_output=True, text=True,
        ).stdout
        assert "shared-branch" not in worktrees


class TestTypedWrappers:
    def test_transcript_cache_round_trip(self, repo_with_remote):
        from app.models import Segment, Transcript
        from app.transcript_cache import TranscriptCache

        t = Transcript(segments=[
            Segment(start_ms=0, end_ms=1000, text="hi"),
            Segment(start_ms=1000, end_ms=2000, text="there"),
        ])
        with TranscriptCache.open() as cache:
            cache.save("m1", t)
            assert cache.load("m1") == t
            assert cache.load("missing") is None

    def test_transcript_cache_on_disk_format_matches_legacy(
        self, repo_with_remote,
    ):
        """The JSON written by TranscriptCache must be byte-compatible with
        the existing app/transcriber.py output (same keys, same separators).
        """
        from app.models import Segment, Transcript
        from app.transcript_cache import TranscriptCache

        t = Transcript(segments=[
            Segment(start_ms=0, end_ms=1000, text="hi"),
        ])
        with TranscriptCache.open() as cache:
            cache.save("m1", t)
            # Reach into the worktree to read the raw file.
            worktree = cache._inner._worktree
            raw = (
                open(os.path.join(worktree, "transcripts", "m1.json")).read()
            )
        # Match transcriber.save_transcript's separators=(",", ":") output.
        assert raw == '[{"start_ms":0,"end_ms":1000,"text":"hi"}]'

    def test_item_summaries_cache_round_trip(self, repo_with_remote):
        from app.item_summaries_cache import ItemSummariesCache
        from app.models import Chip, ItemSummary

        summaries = {
            "1": ItemSummary(description=None, chips=[]),
            "7": ItemSummary(
                description=["Approves the subcommittee's 2026 work plan."],
                chips=[
                    Chip(category="Outcome", text="Approved"),
                    Chip(category="Vote Breakdown", text="5 for, 0 against"),
                ],
            ),
        }
        with ItemSummariesCache.open() as cache:
            cache.save("m1", summaries)
            assert cache.load("m1") == summaries

    def test_legacy_entries_load_without_migration(self, repo_with_remote):
        """Cached summaries predating the aggregate are still readable."""
        from app.item_summaries_cache import ItemSummariesCache

        with ItemSummariesCache.open() as cache:
            cache._inner.save("m1", {
                "7": [{"category": "Outcome", "text": "Approved"}],
            })
            loaded = cache.load("m1")
        assert loaded["7"].is_legacy is True
        assert loaded["7"].description is None
        assert loaded["7"].chips[0].category == "Outcome"

    def test_item_summaries_on_disk_format_is_compact(
        self, repo_with_remote,
    ):
        from app.item_summaries_cache import ItemSummariesCache
        from app.models import Chip, ItemSummary

        summaries = {
            "7": ItemSummary(
                description=["Approves the plan."],
                chips=[Chip(category="Outcome", text="Approved")],
            ),
        }
        with ItemSummariesCache.open() as cache:
            cache.save("m1", summaries)
            worktree = cache._inner._worktree
            raw = open(
                os.path.join(worktree, "summaries", "m1.json")
            ).read()
        assert raw == (
            '{"7":{"description":["Approves the plan."],'
            '"chips":[{"category":"Outcome","text":"Approved"}]}}'
        )


class TestPushPreflight:
    """GitBranchCache pushes on exit, so credential failures surface last.

    Without a preflight, a backfill does all its work, spends whatever the
    work costs, and discards everything when the temp worktree is removed.
    """

    def test_passes_when_the_remote_accepts_a_push(self, repo_with_remote):
        from app.cache_git import GitBranchCache, verify_push_access

        with GitBranchCache("summaries", "summaries") as cache:
            cache.save("m1", {"7": []})
        verify_push_access("summaries")  # must not raise

    def test_passes_when_the_branch_does_not_exist_remotely_yet(
        self, repo_with_remote,
    ):
        """The first run of a new cache creates its branch."""
        from app.cache_git import verify_push_access

        verify_push_access("a-branch-that-does-not-exist")  # must not raise

    def test_absent_branch_is_not_confused_with_an_unreachable_remote(
        self, repo_with_remote,
    ):
        """Both look like a failed fetch, but only one is safe to proceed on."""
        from app.cache_git import PushAccessError, verify_push_access, _git

        _git("remote", "set-url", "origin", "/nonexistent/remote.git")
        with pytest.raises(PushAccessError):
            verify_push_access("a-branch-that-does-not-exist")

    def test_raises_when_the_remote_is_unreachable(self, repo_with_remote):
        from app.cache_git import (
            GitBranchCache, PushAccessError, verify_push_access, _git,
        )

        with GitBranchCache("summaries", "summaries") as cache:
            cache.save("m1", {"7": []})
        # Point origin at a path that cannot accept a push.
        _git("remote", "set-url", "origin", "/nonexistent/remote.git")
        with pytest.raises(PushAccessError):
            verify_push_access("summaries")

    def test_the_error_names_the_branch(self, repo_with_remote):
        from app.cache_git import (
            GitBranchCache, PushAccessError, verify_push_access, _git,
        )

        with GitBranchCache("summaries", "summaries") as cache:
            cache.save("m1", {"7": []})
        _git("remote", "set-url", "origin", "/nonexistent/remote.git")
        with pytest.raises(PushAccessError, match="summaries"):
            verify_push_access("summaries")


class TestSavingAnUnchangedValue:
    """Re-saving a key whose bytes did not change is a no-op, not an error.

    A summarize run redid two committee meetings that had no eligible
    items, so their summaries came out byte-identical to what the branch
    already held.  ``git commit`` exits 1 with nothing staged, the caller
    counted each as a failed meeting, and the job was marked failed having
    done all of its work and lost nothing.
    """

    def test_resaving_the_same_value_does_not_raise(self, repo_with_remote):
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})
            c.save("k1", {"x": 1})  # must not raise

    def test_resaving_the_same_value_adds_no_commit(self, repo_with_remote):
        _, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})
            c.save("k1", {"x": 1})
        assert _remote_log(str(remote), "test-cache") == ["Add data for k1"]

    def test_a_later_session_resaving_the_same_value_does_not_raise(
        self, repo_with_remote,
    ):
        """The incident's shape: the unchanged value is on the branch already."""
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})

        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})  # must not raise

        _, remote = repo_with_remote
        assert _remote_log(str(remote), "test-cache") == ["Add data for k1"]

    def test_a_changed_value_still_commits(self, repo_with_remote):
        _, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})
            c.save("k1", {"x": 2})
        assert _remote_log(str(remote), "test-cache") == [
            "Add data for k1", "Add data for k1",
        ]
        content = _read_remote_blob(str(remote), "test-cache", "data/k1.json")
        assert json.loads(content) == {"x": 2}

    def test_a_session_of_only_unchanged_saves_does_not_push(
        self, repo_with_remote, monkeypatch,
    ):
        """It wrote nothing, so it must not demand write credentials."""
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})

        def _refuse(self):
            raise AssertionError("pushed a session that committed nothing")

        monkeypatch.setattr(GitBranchCache, "_push_branch", _refuse)
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})

    def test_an_unchanged_save_does_not_mask_a_real_one(self, repo_with_remote):
        _, remote = repo_with_remote
        with GitBranchCache("test-cache", "data") as c:
            c.save("k1", {"x": 1})
            c.save("k1", {"x": 1})
            c.save("k2", {"y": 2})
        assert _remote_log(str(remote), "test-cache") == [
            "Add data for k2", "Add data for k1",
        ]
