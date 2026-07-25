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
                description="Approves the subcommittee's 2026 work plan.",
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
                description="Approves the plan.",
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
            '{"7":{"description":"Approves the plan.",'
            '"chips":[{"category":"Outcome","text":"Approved"}]}}'
        )
