---
title: "refactor: Typed Cache seam for transcripts and item summaries"
type: refactor
status: active
date: 2026-05-01
---

# refactor: Typed Cache seam for transcripts and item summaries

## Overview

Extract a real `Cache` seam with one production adapter (`GitBranchCache`) and one test adapter (`InMemoryCache`), then wrap it in two typed caches (`TranscriptCache`, `ItemSummariesCache`). Today the same git-orphan-branch lifecycle is duplicated in `app/transcriber.py` and `app/item_summaries_store.py`, and callers in `scripts/` open-code fetch / push around it. After this work, git is an implementation detail of one adapter, callers depend on a tiny typed interface, and tests no longer need to mock `_git()` or set up real branches.

This is opportunity #2 from the architectural review (`CONTEXT.md`), with the minimum viable slice of opportunity #1 (Transcript / ItemSummary as real types) included because the typed cache cannot exist without them.

---

## Problem Frame

The codebase has two artifacts that get expensive to recompute and need to survive between CI runs: Whisper transcripts and Gemini item summaries. Both are stored on git orphan branches (`transcripts`, `summaries`), one JSON file per meeting. The mechanics of "ensure branch exists, fetch from remote, write file in a worktree, commit, push at end" are spelled out twice — once in `app/transcriber.py:261-342`, once in `app/item_summaries_store.py:22-66` — and the latter even imports `_git` from the former, so a refactor of the transcriber accidentally cascades.

Three concrete frictions follow:

1. **No typed surface.** Callers receive `dict` shapes and reach into them. `app/item_categorizer.py:144-159` slices transcripts by reading `start_ms`/`end_ms` keys directly; if the transcriber's segment shape changes, the categorizer breaks silently.
2. **No injectable cache.** Tests either mock `_git` (brittle) or stand up real git branches (slow). There is one adapter, hardcoded.
3. **Per-call worktree churn.** `save_summaries` in `app/item_summaries_store.py` creates and removes a temporary worktree on every save. In a loop over N meetings, that's N worktree setup/teardown cycles.

This was settled through a grilling conversation captured in `CONTEXT.md`. Decisions:
- Mental model: key-value store; git is one implementation detail
- Two typed caches, each owning its (de)serialization
- Lifecycle: context manager; push on exit including on exceptions
- Per-meeting storage (already true on disk for both branches — no migration needed)
- Scope: transcripts + item summaries only; meeting topics and scrape are out

---

## Requirements Trace

- R1. A single `Cache` interface exists at `app/cache.py` with `load(meeting_id) -> T | None` and `save(meeting_id, T) -> None`, opened as a context manager that fetches on enter and pushes on exit (including on exceptions).
- R2. `Transcript` and `ItemSummary` exist as importable types in `app/models.py` with `from_dict` / `to_dict` round-trips. The Transcript module owns segment-shape knowledge (`start_ms`, `end_ms`, `text`).
- R3. A `GitBranchCache` adapter and an `InMemoryCache` adapter both satisfy the `Cache` protocol. Tests run against `InMemoryCache` without git or filesystem.
- R4. `TranscriptCache` and `ItemSummariesCache` exist as typed wrappers; callers never see raw dicts.
- R5. `app/transcriber.py` and `scripts/transcribe_meetings.py` consume `TranscriptCache`; the `_git` helper, branch setup, and push logic are removed from `app/transcriber.py`.
- R6. `app/item_summaries_store.py` is deleted; `scripts/summarize_meetings.py` consumes `ItemSummariesCache`.
- R7. The CI workflows (`.github/workflows/transcribe.yml`, `summarize.yml`) continue to work without modification — the entry-point scripts retain their existing CLI surface and the orphan branches keep the same names (`transcripts`, `summaries`) and on-disk layout.
- R8. The categorizer's transcript slicing (`app/item_categorizer.py:144-159`) goes through `Transcript` methods rather than reading segment dicts.

---

## Scope Boundaries

- Not caching `extract_meeting_topics()` — explicit deferral.
- Not caching scraped escribemeetings data — different semantics (TTL/invalidation).
- Not refactoring `app/item_categorizer.py`'s extractor passes (opportunity #4 from the review). Only the transcript-shape coupling is fixed here.
- Not extracting the agenda-text normalization helpers (opportunity #3) — separate plan.
- Not introducing an orchestration / pipeline module (opportunity #5) — separate plan.
- Not changing on-disk file layout. Both branches already use one JSON file per meeting; this plan preserves that exactly so existing branches keep working.

### Deferred to Follow-Up Work

- Extending `Transcript` with richer behaviour (find_section, etc.) — out of scope here, will land with opportunity #1's full version.
- Caching meeting topics — separate plan if/when API latency becomes a concern.

---

## Context & Research

### Relevant Code and Patterns

- `app/transcriber.py:261-342` — current `_git`, `load_cached_transcript`, `save_transcript`. Source of truth for git lifecycle to extract.
- `app/item_summaries_store.py` (entire file) — the duplicated half. To be deleted.
- `scripts/transcribe_meetings.py:35-53` — `ensure_branch` / `push_transcript_branch` orchestration. Becomes `with TranscriptCache.open() as cache:`.
- `scripts/summarize_meetings.py:40-62` — same pattern for summaries.
- `app/main.py:74-98` — API endpoints that call `extract_item_summaries` / `extract_meeting_topics`. Will read from `ItemSummariesCache` via the new typed surface.
- `app/item_categorizer.py:144-159` — `_slice_transcript` reaches into segment dicts; gets converted to a `Transcript.slice()` call.
- `tests/test_transcriber.py`, `tests/test_summarizer.py`, `tests/test_item_categorizer.py` — existing test patterns; new tests should follow style.

### Institutional Learnings

- No `docs/solutions/` directory exists in this repo.

### External References

- None — purely an internal refactor against well-understood patterns (Python protocols, context managers, git plumbing already in use).

---

## Key Technical Decisions

- **Context manager lifecycle, push on `__exit__` even on exceptions.** Preserves current behavior (one push per run) and ensures partial progress is durable when a long Whisper run dies mid-loop. Rationale captured in `CONTEXT.md`.
- **Hold one worktree open for the duration of the context, not one per save.** Eliminates per-call worktree churn in `item_summaries_store.py`. Behavior change relative to today, but strictly more efficient with no observable difference to the produced branch.
- **Typed wrappers (`TranscriptCache`, `ItemSummariesCache`), not generic `Cache[T]`.** Each typed cache owns its (de)serialization; callers never touch JSON. Avoids the "generic cache + serializer kwarg" trap that ends up duplicating serialization at every call site.
- **`GitBranchCache` self-contained.** Owns its own `_git` subprocess helper rather than depending on the one in `app/transcriber.py`. Lets U4 and U5 land independently without a sequencing trap.
- **No on-disk format change.** Existing `transcripts/{meeting_id}.json` and `summaries/{meeting_id}.json` layouts are preserved exactly. The typed cache's serializer must round-trip with files already on the orphan branches.
- **Protocol over ABC.** Use `typing.Protocol` for the `Cache` interface. Lighter than `abc.ABC`, matches the duck-typed style already in the codebase, and keeps adapters from inheriting machinery they don't need.

---

## Open Questions

### Resolved During Planning

- Per-meeting vs. all-at-once storage: per-meeting (matches what's already on disk for both branches).
- Should `Cache` be generic or typed: typed wrappers around a generic protocol — see Key Technical Decisions.
- Crash semantics: push on exit including on exceptions.
- Migration of summaries data: not needed — already per-meeting on disk.

### Deferred to Implementation

- Exact subprocess call shape for the worktree lifecycle (the existing `_git()` is a starting point but the new adapter may simplify it now that it owns the full lifecycle).
- Whether `Transcript.from_dict` should validate / coerce on load or trust the file. Default to trust for now; revisit if a corrupt file actually surfaces.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
                    +-------------------+
                    |   Cache[T]        |   <-- Protocol in app/cache.py
                    |   load / save     |
                    |   __enter__/exit  |
                    +---------+---------+
                              |
              +---------------+---------------+
              |                               |
       +------+------+                +-------+------+
       | InMemoryCache|                | GitBranchCache|
       | (tests)      |                | (production)  |
       +--------------+                +-------+------+
                                              |
                              owns: branch name, worktree lifecycle,
                                    fetch on enter, push on exit
                                              |
              +-------------------------------+-------------------------------+
              |                                                               |
       +------+----------+                                          +---------+--------+
       | TranscriptCache  |                                          | ItemSummariesCache|
       | (de)serializes   |                                          | (de)serializes    |
       | Transcript       |                                          | list[ItemSummary] |
       +------+-----------+                                          +---------+---------+
              |                                                                |
              v                                                                v
   used by transcriber + scripts/transcribe_meetings.py            used by scripts/summarize_meetings.py + app/main.py
```

Lifecycle:

```
with TranscriptCache.open() as cache:        # GitBranchCache: fetch + worktree
    for m in meetings:
        if cache.load(m.id) is None:
            cache.save(m.id, transcribe(m))  # writes file in worktree, commits
# __exit__: push to origin (including on exceptions); remove worktree
```

---

## Implementation Units

- [ ] U1. **Define `Transcript` and `ItemSummary` dataclasses**

**Goal:** Establish the two domain types the typed caches need. Minimum viable surface — just enough behavior to remove dict-shaped coupling at the cache and slice seams.

**Requirements:** R2, R8

**Dependencies:** None

**Files:**
- Create: `app/models.py`
- Modify: `app/item_categorizer.py` (replace `_slice_transcript`'s dict access with `Transcript.slice` once available; if the full slice migration is too large, leave a TODO and pin it to U4)
- Test: `tests/test_models.py`

**Approach:**
- `Transcript`: a frozen dataclass holding a list of segment records (each with `start_ms: int`, `end_ms: int`, `text: str`). Methods: `slice(start_s: float, end_s: float) -> Transcript`, `text` property (joined segment text), `from_dict(data) -> Transcript`, `to_dict() -> dict`.
- `ItemSummary`: a frozen dataclass holding the fields currently in the summaries JSON values (`category: str`, `text: str`, plus whatever other fields the existing files contain — read one to confirm before finalizing). `from_dict` / `to_dict`.
- The on-disk JSON shapes must round-trip exactly through `from_dict` → `to_dict`. This is load-bearing: existing branches have to keep working.
- Time conversion (seconds vs. milliseconds) lives in `Transcript`, not in callers. `slice` takes seconds (matching what the categorizer uses today) and converts internally.

**Patterns to follow:**
- Existing dataclass usage in `app/scraper.py` (`Meeting` dataclass at line 99).

**Test scenarios:**
- Happy path: `Transcript.from_dict(t.to_dict()) == t` for a representative segment list.
- Happy path: `Transcript.slice(60.0, 120.0)` returns segments whose `[start_ms, end_ms)` overlap `[60000, 120000)` ms, and only those.
- Edge case: `Transcript.slice` with a range that contains no segments returns an empty Transcript, not None.
- Edge case: `Transcript.slice` where a segment straddles the boundary — segment is included if any part overlaps.
- Edge case: empty Transcript (no segments) — `text` is `""`, `slice` returns empty.
- Happy path: `ItemSummary.from_dict(s.to_dict()) == s` for every category present in a real summaries JSON file (sample one from the `summaries` branch).
- Edge case: `ItemSummary.from_dict` on a file with extra fields (forward-compat) — either preserved or explicitly dropped; pick one and document.

**Verification:**
- `Transcript` and `ItemSummary` are importable from `app.models`.
- Round-trip tests pass against fixtures derived from real on-disk files.
- `_slice_transcript` in `app/item_categorizer.py` either uses `Transcript.slice` directly or has a single TODO comment pinned to U4 with the line range to update.

---

- [ ] U2. **Define `Cache` protocol and `InMemoryCache` adapter**

**Goal:** Establish the seam interface and the test adapter, with no git involvement.

**Requirements:** R1, R3

**Dependencies:** U1

**Files:**
- Create: `app/cache.py`
- Test: `tests/test_cache.py`

**Approach:**
- `Cache[T]` as a `typing.Protocol` with `load(meeting_id: str) -> T | None`, `save(meeting_id: str, value: T) -> None`, `__enter__` / `__exit__`.
- `InMemoryCache[T]` as a generic class storing a `dict[str, T]` in memory. `__enter__` returns self; `__exit__` is a no-op.
- No serialization at this layer — the protocol is generic in `T`. Typed wrappers in U3 handle JSON.
- Document the lifecycle contract in the protocol's docstring: "fetch on enter, push on exit including on exceptions; per-key save is durable on context exit, not before."

**Patterns to follow:**
- Stdlib context manager idioms (`__enter__`/`__exit__`).

**Test scenarios:**
- Happy path: `with InMemoryCache[int]() as c: c.save("m1", 7); assert c.load("m1") == 7`.
- Edge case: `c.load("missing")` returns `None`, not raises.
- Edge case: `c.save("m1", 7); c.save("m1", 8); c.load("m1") == 8` — last write wins.
- Edge case: opening a fresh `InMemoryCache` instance does not see writes from a previous instance (per-instance isolation).
- Edge case: protocol conformance — a tiny test class defining only `load`/`save`/`__enter__`/`__exit__` is accepted by `isinstance(x, Cache)` (or a structural check, depending on protocol decoration).

**Verification:**
- `app.cache.Cache` and `app.cache.InMemoryCache` are importable.
- Tests pass without touching git or filesystem.

---

- [ ] U3. **Implement `GitBranchCache` adapter and typed `TranscriptCache` / `ItemSummariesCache` wrappers**

**Goal:** One self-contained git adapter, two typed wrappers around it. After this unit, all behavior the old code provides is available behind the new seam — but not yet wired to callers.

**Requirements:** R1, R3, R4

**Dependencies:** U1, U2

**Files:**
- Create: `app/cache_git.py` (or extend `app/cache.py` if it stays small)
- Create: `app/transcript_cache.py`
- Create: `app/item_summaries_cache.py`
- Test: `tests/test_cache_git.py`
- Test: `tests/test_transcript_cache.py`
- Test: `tests/test_item_summaries_cache.py`

**Approach:**
- `GitBranchCache(branch: str, dir_name: str)`:
  - `__enter__`: fetch the branch from origin (best-effort — a missing remote branch is fine, treat as cold start). Set up one tempdir worktree, pointed at the branch (creating an orphan if the branch doesn't exist locally or remotely).
  - `load(key)`: read `{dir_name}/{key}.json` from the worktree if present, return raw dict. Returns None if file doesn't exist.
  - `save(key, value)`: write `{dir_name}/{key}.json` in the worktree, `git add` + `git commit -m "Add {dir_name} for {key}"`. No push here.
  - `__exit__`: push the branch to origin. Remove the worktree. **Always run on exception too** (use try/finally inside `__exit__` to make push and worktree-cleanup independent — a push failure should still clean up the worktree, and an exception during the `with` body should still attempt the push).
  - Owns its own `_git()` subprocess helper. No dependency on `app/transcriber.py`.
- `TranscriptCache.open(branch="transcripts", dir_name="transcripts")`: thin factory that wraps a `GitBranchCache` and adds typed (de)serialization through `Transcript.from_dict` / `to_dict`. `load(meeting_id) -> Transcript | None`, `save(meeting_id, Transcript) -> None`.
- `ItemSummariesCache.open(branch="summaries", dir_name="summaries")`: same, for `dict[str, list[ItemSummary]]` (the existing on-disk shape — item_id → list of summary dicts; preserve exactly).
- The typed wrappers expose `.open()` as a classmethod returning a context manager. Body of the wrapper just delegates to a held `GitBranchCache` and (de)serializes around it.

**Execution note:** Test `GitBranchCache` against a temp directory acting as a fake remote (run `git init --bare` in a tempdir, push/pull against it). No mocking of subprocess. Slower but real.

**Patterns to follow:**
- Existing `_git` helper in `app/transcriber.py:261-280` for subprocess shape and error handling.
- Existing worktree pattern in `app/item_summaries_store.py:42-66` for the orphan-branch initialization dance.

**Test scenarios:**
- *GitBranchCache:*
  - Happy path: `with GitBranchCache("test-branch", "data") as c: c.save("k1", {"x": 1})` produces a commit on `test-branch` and `__exit__` pushes to the fake remote.
  - Happy path: opening a second `GitBranchCache` against the same remote, `c.load("k1")` returns `{"x": 1}`.
  - Edge case: branch doesn't exist anywhere — `__enter__` succeeds, `load` returns None for any key, `save` works, `__exit__` creates the branch on the remote.
  - Edge case: `load("missing")` on an existing branch returns None.
  - Error path: an exception raised inside the `with` body still results in committed saves being pushed (push-on-exit-including-exceptions). Verify with the fake remote.
  - Error path: if the push itself fails (e.g., simulated by pointing at a bad remote), the worktree is still cleaned up — no leaked tempdir.
  - Integration: save N items in a loop, verify exactly one push at exit and N commits on the branch.
- *TranscriptCache:*
  - Happy path: `cache.save(mid, transcript); cache.load(mid) == transcript` round-trip with a real Transcript.
  - Integration: save via `TranscriptCache`, then read the raw file from the worktree via `GitBranchCache` — JSON shape matches what `app/transcriber.py` writes today (compare against a fixture from the real branch).
- *ItemSummariesCache:*
  - Same shape as TranscriptCache tests.
  - Integration: a fixture file copied from the real `summaries` branch round-trips through `ItemSummariesCache.load` → `.save` byte-identically (modulo JSON formatting).

**Verification:**
- All three classes importable.
- Tests pass against a fake bare git remote, no mocks of subprocess.
- No reference to `app.transcriber._git` from `app/cache_git.py` or the typed wrappers.

---

- [ ] U4. **Cut transcriber over to `TranscriptCache`**

**Goal:** Replace the transcript persistence path in `app/transcriber.py` and `scripts/transcribe_meetings.py` with the new typed cache. Delete the now-unused load/save/setup helpers from `app/transcriber.py`.

**Requirements:** R5, R7, R8

**Dependencies:** U3

**Files:**
- Modify: `app/transcriber.py` (remove `_git`, `load_cached_transcript`, `save_transcript`, branch constants)
- Modify: `scripts/transcribe_meetings.py` (replace `ensure_branch`/`push_transcript_branch` with `with TranscriptCache.open() as cache:` wrapping the loop)
- Modify: `app/item_categorizer.py` (if U1 left a TODO, resolve it now — `_slice_transcript` uses `Transcript.slice`)
- Modify: `tests/test_transcriber.py` (remove tests of deleted helpers, add tests of the new flow if not covered by U3)

**Approach:**
- The transcribe entrypoint in `scripts/transcribe_meetings.py:64-126` becomes: open the `TranscriptCache` once, loop meetings, `cache.load(mid)` for "already done" check, call Whisper if missing, `cache.save(mid, transcript)`. Push happens automatically on context exit.
- Delete `app/transcriber.py:261-342` (the `_git`, `load_cached_transcript`, `save_transcript`, branch-name constants region — exact lines to confirm during implementation).
- `app/transcriber.py`'s public interface shrinks to "given a video URL, return a `Transcript`." It becomes a pure transformation; persistence lives in `TranscriptCache`.
- Confirm by grep: nothing else in the codebase imports `_git` or `load_cached_transcript` from `app.transcriber` after this unit. **Note:** `app/item_summaries_store.py` still imports `_git` — leave that file alone in this unit. U5 deletes it. Until U5 lands, keep `_git` available either by leaving it in place or temporarily moving it to a private module the store can import. Pick whichever is simpler at implementation time.

**Patterns to follow:**
- Existing CLI structure in `scripts/transcribe_meetings.py`.

**Test scenarios:**
- Covers R5: `scripts/transcribe_meetings.py` runs end-to-end against a fake remote, produces commits on `transcripts` branch matching today's layout (one JSON file per meeting, same shape).
- Happy path: an already-cached meeting is skipped (no Whisper call). Mock Whisper to assert.
- Happy path: a fresh meeting is transcribed and saved.
- Error path: Whisper raises mid-loop — partial progress (already-saved meetings) is pushed by `__exit__`. Verify on fake remote.
- Integration: byte-for-byte compare a transcript JSON written by the new code against a fixture from the existing `transcripts` branch (proves R7's no-format-change requirement).

**Verification:**
- `app/transcriber.py` no longer contains `_git`, `load_cached_transcript`, or `save_transcript`.
- `scripts/transcribe_meetings.py` has no direct git calls; the only persistence touchpoint is `TranscriptCache`.
- `.github/workflows/transcribe.yml` runs unchanged (CLI surface preserved).

---

- [ ] U5. **Cut item summaries over to `ItemSummariesCache`; delete `item_summaries_store.py`**

**Goal:** Replace the summaries persistence path with the typed cache. Delete `app/item_summaries_store.py` entirely. Apply the deletion test: this file goes, complexity concentrates in `GitBranchCache` (good), nothing scatters.

**Requirements:** R6, R7

**Dependencies:** U3, U4

**Files:**
- Delete: `app/item_summaries_store.py`
- Modify: `scripts/summarize_meetings.py` (replace `setup_branches`/`push_summaries_branch` and the `load_cached_summaries`/`save_summaries` calls with `with ItemSummariesCache.open() as cache:`)
- Modify: `app/main.py` (the `/api/meeting/<id>` endpoint at lines 74-76 currently calls `summarize_agenda_items` — if it reads cached summaries anywhere, route through `ItemSummariesCache.open()` per request; if it doesn't, no change needed — verify during implementation)
- Modify: `tests/test_*.py` for any test that imports from `app.item_summaries_store`

**Approach:**
- `scripts/summarize_meetings.py:101-184` becomes: open `ItemSummariesCache` and (if needed) `TranscriptCache` for read-only access, loop meetings, check cache, run extraction, save.
- For the read-only transcript access in `summarize_meetings.py`, `TranscriptCache.open()` opens for read+write but only `load` is called — that's fine, the worktree just isn't modified, and the no-op push at exit is cheap. **Optional refinement:** if read-only opens are a real performance concern, add a `read_only=True` flag to skip the push. Defer unless measured.
- After this unit, `app/transcriber.py:261-342` deletion from U4 can be finalized — `_git` has no remaining importers.

**Patterns to follow:**
- The cutover pattern from U4 — same shape, different cache type.

**Test scenarios:**
- Covers R6: `scripts/summarize_meetings.py` runs end-to-end against fake remotes for both branches, produces summaries commits matching today's layout.
- Happy path: an already-summarized meeting is skipped (no Gemini call). Mock the extractor.
- Happy path: a meeting with a cached transcript and no summaries gets summarized and saved.
- Edge case: meeting with no cached transcript is skipped (matches today's behavior at `scripts/summarize_meetings.py:151`).
- Error path: extractor raises on meeting 5 of 10 — meetings 1-4 are pushed on context exit.
- Integration: byte-for-byte compare a summaries JSON written by the new code against a fixture from the existing `summaries` branch (R7).
- Integration: `app/main.py`'s `/api/meeting/<id>` endpoint serves a summary identical to today's response.

**Verification:**
- `app/item_summaries_store.py` does not exist.
- `grep -r 'item_summaries_store' app/ scripts/ tests/` returns nothing.
- `grep -r 'from app.transcriber import _git' app/ scripts/ tests/` returns nothing — `_git` can be deleted from `app/transcriber.py` if U4 left it as a transitional shim.
- Both CI workflows (`transcribe.yml`, `summarize.yml`) run unchanged.

---

## System-Wide Impact

- **Interaction graph:** The CI workflows (`.github/workflows/transcribe.yml`, `summarize.yml`, `deploy.yml`) consume the entry-point scripts. CLI surface and orphan branch names are preserved exactly (R7), so workflows don't change.
- **Error propagation:** Failures inside the `with` body now reliably push partial progress. This is a small but real semantic improvement over today, where a mid-loop crash before the explicit `push_*_branch()` call loses everything since the last successful run.
- **State lifecycle risks:** The held-open worktree across an entire run is new (today, each save creates and tears down its own worktree). The risk is leaked tempdirs if `__exit__` itself crashes. Mitigated by structuring `__exit__` so worktree cleanup runs even if push fails.
- **API surface parity:** `app/main.py`'s endpoints continue to read summaries identically. The internal signature change (typed `ItemSummary` vs. raw dict) needs to be visible at exactly one boundary — wherever the API serializes for JSON response.
- **Integration coverage:** Round-trip tests against fixtures from the real `transcripts` and `summaries` branches are the load-bearing safety net for R7. Unit tests on the typed wrappers don't prove on-disk compatibility on their own.
- **Unchanged invariants:** Branch names (`transcripts`, `summaries`), on-disk file layout (`{dir}/{meeting_id}.json`), CLI flags, the public function `extract_item_summaries` in `app/item_categorizer.py`, the public function `transcribe_meeting` in `app/transcriber.py` (return-type change from dict to `Transcript` is a parity break — note this is contained to internal callers).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| On-disk JSON format drifts from existing branches, breaking deployed CI | Round-trip integration tests against fixtures copied from the real `transcripts` and `summaries` branches. Run before merging. |
| Held-open worktree leaks if `__exit__` itself raises | Structure `__exit__` so worktree cleanup is in a `finally` independent of push. Test the exception path explicitly. |
| `_git` import from `app.transcriber` in `app/item_summaries_store.py` creates a sequencing trap between U4 and U5 | Plan addresses this directly: `GitBranchCache` is self-contained (no dependency on transcriber's `_git`), and U4 may keep `_git` as a transitional shim until U5 lands. |
| Return-type change for internal `transcribe_meeting` (dict → Transcript) silently breaks a caller not seen during planning | Grep all importers of `app.transcriber.transcribe_meeting` before U4; update each in the same commit. |
| Push-on-exit changes operational characteristics under crash | Behavior is strictly an improvement (more durable), not a regression. Documented in Key Technical Decisions. |

---

## Documentation / Operational Notes

- Update `CONTEXT.md` if any term in the architecture vocabulary section drifts during implementation.
- No runbook or README changes expected — CLI and workflow surfaces are preserved.
- No feature-flag or rollout concerns: this is a pure refactor with byte-compatible on-disk output.

---

## Sources & References

- Architecture vocabulary and design decisions: `CONTEXT.md`
- Origin: grilling conversation in the same Claude Code session that produced `CONTEXT.md` (no separate brainstorm doc).
- Related code:
  - `app/transcriber.py:261-342` (git lifecycle to extract)
  - `app/item_summaries_store.py` (to delete)
  - `app/item_categorizer.py:144-159` (`_slice_transcript`, transcript-shape coupling)
  - `app/main.py:74-98` (API endpoints)
  - `scripts/transcribe_meetings.py`, `scripts/summarize_meetings.py`
  - `.github/workflows/transcribe.yml`, `.github/workflows/summarize.yml`
