---
title: "refactor: MeetingSource / EscribeTransport seam for eSCRIBE intake"
type: refactor
status: active
date: 2026-05-02
---

# refactor: MeetingSource / EscribeTransport seam for eSCRIBE intake

## Overview

Replace the flat `app/scraper.py` (765 LOC, no seam) with a two-layer architecture:

- **`MeetingSource`** (domain seam) — `list_past`, `load_detail`. Returns `Meeting` / `MeetingDetail` / `AgendaItem`. Two adapters: `EscribeMeetingSource` (production) and `InMemoryMeetingSource` (test).
- **`EscribeTransport`** (bytes seam) — `fetch_past_meetings_json`, `fetch_agenda_html`, `fetch_postminutes_html`. Two adapters: `LiveEscribeTransport` (HTTP) and `FixtureEscribeTransport` (replays recorded HTML/JSON from disk).

This is opportunity #1 from the architectural review (`CONTEXT.md`). After this work, the eSCRIBE intake is testable end-to-end via fixture replay, and downstream pipelines (summarizer, topics, build_site) can be tested against `InMemoryMeetingSource` without touching HTML at all.

---

## Problem Frame

`app/scraper.py` mixes five concerns inline with no seams:

1. HTTP calls to eSCRIBE (3 endpoints, embedded in 3 functions)
2. HTML parsing with regex fallbacks
3. Domain type definitions (`Meeting`, `AgendaItem` — owned by the parser, not by `models.py`)
4. Post-fetch normalization (timestamp propagation, brief-item marking, recess insertion, confirmation-attachment redistribution, minutes-vs-description preference)
5. Presentation config (`MEETING_TABS`, `_SLUG_TO_TYPE`)

Three concrete frictions follow:

1. **No isolation seam.** Every call site reaches `requests.get/post` directly. Tests can only mock by patching `requests` at each call site. No regression test for "eSCRIBE changed their HTML."
2. **Inconsistent error contract.** `fetch_meeting_detail` raises on HTTP failure. `fetch_post_minutes` swallows all exceptions and returns empty dicts. Same module, opposite policies, no signal when the postminutes page actually fails.
3. **Domain types live inside the adapter.** `Meeting` and `AgendaItem` are dataclasses inside `app/scraper.py`. Consumers (main, scripts, item_categorizer) import a domain type from a module named after its upstream — backwards.

This was settled through a grilling conversation captured in `CONTEXT.md`. Decisions:

- Two-layer seam: `MeetingSource` on top of `EscribeTransport`. Both layers fakeable.
- `Meeting` / `AgendaItem` move to `app/models.py`. New `MeetingDetail` joins them.
- Normalization stays inside `EscribeMeetingSource` (eSCRIBE-specific, not a general concept).
- `include_votes` flag drops — both current callers pass `True`. Always fetch both pages.
- `EscribeTransport` always raises on HTTP failure; `EscribeMeetingSource` catches the postminutes failure to keep votes best-effort (an explicit policy in one place, not a silent swallow in the transport).
- Wiring via Flask `app.config["meeting_source"]`. Routes pull through `current_app.config`. Test client constructs an app with `InMemoryMeetingSource`.
- Flat file layout: one cohesive `app/escribe.py` for transport + parsers + source. Not a package.
- `InMemoryMeetingSource` takes passive data in the constructor (`details: dict[str, MeetingDetail]`), matching the `InMemoryCache` shape.

---

## Requirements Trace

- R1. `MeetingSource` Protocol exists at `app/meeting_source.py` with `list_past(page, meeting_type) -> tuple[list[Meeting], int]` and `load_detail(meeting_id) -> MeetingDetail`.
- R2. `EscribeTransport` Protocol exists at `app/escribe.py` with `fetch_past_meetings_json`, `fetch_agenda_html`, `fetch_postminutes_html`. All raise on HTTP failure.
- R3. `EscribeMeetingSource` (built on `EscribeTransport`) and `InMemoryMeetingSource` both satisfy `MeetingSource`. Tests run against `InMemoryMeetingSource` without HTML.
- R4. `LiveEscribeTransport` and `FixtureEscribeTransport` both satisfy `EscribeTransport`. `EscribeMeetingSource` end-to-end tests run against fixture replay without network.
- R5. `Meeting`, `AgendaItem`, `MeetingDetail` live in `app/models.py`. No domain type is owned by an adapter.
- R6. `MEETING_TABS`, `_SLUG_TO_TYPE`, `MEETING_TYPE` live in `app/meeting_types.py`. Neither `MeetingSource` nor `EscribeTransport` depends on tabs/slugs.
- R7. `app/main.py` installs `app.config["meeting_source"] = EscribeMeetingSource(LiveEscribeTransport())` at module load. Routes read via `current_app.config["meeting_source"]`. No direct `from app.scraper import` remains in `main.py`.
- R8. Scripts (`scripts/build_site.py`, `scripts/summarize_meetings.py`, `scripts/transcribe_meetings.py`) construct an `EscribeMeetingSource(LiveEscribeTransport())` and use it. No `from app.scraper import` remains.
- R9. `app/scraper.py` is deleted. Its public functions (`fetch_past_meetings`, `fetch_meeting_detail`, `fetch_post_minutes`, `fetch_meeting_votes`) are gone. The `include_votes` flag is gone.
- R10. `tests/test_scraper.py` becomes `tests/test_escribe.py` — same coverage of pure parser helpers, re-targeted to `app.escribe`. New `tests/test_meeting_source.py` covers `InMemoryMeetingSource` and `EscribeMeetingSource` (driven by `FixtureEscribeTransport`).
- R11. CI workflows continue to work without modification.

---

## Scope Boundaries

- Not refactoring `app/item_categorizer.py`'s extractor passes (opportunity #3 from the review). Only the `AgendaItem` import path moves.
- Not introducing an LLM seam (opportunity #4) — separate plan.
- Not touching `app/transcriber.py` (opportunity #2) — separate plan.
- Not consolidating the agenda-text helpers (opportunity #5) — separate plan.
- Not changing the on-the-wire eSCRIBE protocol or any HTTP behavior. The Live transport is a 1:1 lift of the current `requests.*` calls.
- Not introducing dependency injection for `EscribeTransport` into `EscribeMeetingSource` outside its constructor — no DI framework.

### Deferred to Follow-Up Work

- Recording a real fixture set for `FixtureEscribeTransport`. U4 ships with a small hand-built fixture; expanding the fixture corpus to cover edge cases is follow-up.
- Caching anything from `MeetingSource` — explicit non-goal (CONTEXT.md says scrape data has different semantics).
- Migrating the Flask test setup itself — there is none today; U5 enables it but does not write a test harness.

---

## Context & Research

### Relevant Code and Patterns

- `app/scraper.py:113-156` — `fetch_past_meetings`, the JSON endpoint call. Lifts directly into `LiveEscribeTransport.fetch_past_meetings_json` + parsing in `EscribeMeetingSource.list_past`.
- `app/scraper.py:159-214` — `fetch_meeting_detail`, the 55-line orchestration. Becomes `EscribeMeetingSource.load_detail`.
- `app/scraper.py:750-765` — `fetch_post_minutes`, the silent-swallow branch. Becomes a `try/except` inside `EscribeMeetingSource.load_detail`; the transport itself raises.
- `app/scraper.py:60-110` — `Meeting`, `AgendaItem` dataclasses. Move to `app/models.py`.
- `app/scraper.py:217-743` — pure parser helpers. Lift wholesale into `app/escribe.py`.
- `app/main.py:12, 43-67, 95` — the only Flask consumer of scraper. Switch to `current_app.config["meeting_source"]`.
- `scripts/build_site.py`, `scripts/summarize_meetings.py`, `scripts/transcribe_meetings.py` — script consumers. Each constructs its own `EscribeMeetingSource`.
- `tests/test_scraper.py` — already tests private parser helpers via direct import; the same pattern works against `app.escribe`.
- `app/cache.py` + `app/transcript_cache.py` + `app/item_summaries_cache.py` — precedent for the Protocol + adapter pattern. Match the style.

### Institutional Learnings

- No `docs/solutions/` directory in this repo. Latest plan is the typed-cache-seam refactor (`docs/plans/2026-05-01-001-refactor-typed-cache-seam-plan.md`); follow its U1/U2/... slicing convention so each slice compiles and tests cleanly.

### External References

- None. Internal refactor against well-understood patterns (Python protocols, Flask app config).

---

## Key Technical Decisions

- **Protocol over ABC.** Match the existing `Cache` style. Lighter, duck-typed, no inheritance.
- **`AgendaItem` stays mutable.** The orchestration in `EscribeMeetingSource.load_detail` mutates fields (recommendation, vote_result, content, attachments). Freezing it would force a rewrite that is out of scope. `Meeting` and `MeetingDetail` are also dataclasses; freezing them is fine but not required for this plan.
- **Transport always raises.** Silent failure in `fetch_post_minutes` becomes an explicit `try/except` in `EscribeMeetingSource.load_detail` with a comment naming the policy ("votes are best-effort"). One place to find the decision.
- **Drop `include_votes`.** Both call sites pass `True`. Removing the flag tightens the interface; reintroducing it later is cheap if a caller ever needs the cheap-path.
- **Flat file layout.** One `app/escribe.py` for transport + parsers + source. A package would optimize for hypothetical reuse.
- **Flask `app.config["meeting_source"]` over module-level singleton.** Heavier wiring than a singleton, but lets a future Flask test client construct an app with `InMemoryMeetingSource` without monkey-patching. Scripts do their own construction; they are not Flask consumers.
- **`InMemoryMeetingSource(details=..., past=...)` is passive.** Matches `InMemoryCache`. No `.add()` mutator. Tests construct one literal per fixture. Spying needs go through `unittest.mock.Mock(spec=MeetingSource)`.
- **Parsers stay private (`_extract_*`).** They are tested via direct import from `app.escribe`, just as today's tests reach into `app.scraper`. The publicness is for testing, not for cross-module use.

---

## Open Questions

### Resolved During Planning

- Layering: bytes-only transport under domain-shaped source.
- Where normalization lives: inside `EscribeMeetingSource` (eSCRIBE-shaped).
- How `main.py` gets its source: Flask `app.config`.
- Public surface: `fetch_past_meetings`, `fetch_meeting_detail`, `fetch_post_minutes`, `fetch_meeting_votes`, `include_votes` all gone.

### Deferred to Implementation

- Exact `FixtureEscribeTransport` fixture-loading shape (file-per-meeting? file-per-endpoint? both?). Settle in U4.
- Whether `MeetingDetail` should be `frozen=True`. Default mutable to mirror today's behavior; promote to frozen if the orchestration can be reshaped to build the detail in one go without later mutation.

---

## High-Level Technical Design

```
                    +------------------------+
                    |   MeetingSource        |   <-- Protocol in app/meeting_source.py
                    |   list_past            |
                    |   load_detail          |
                    +-----------+------------+
                                |
                +---------------+----------------+
                |                                |
       +--------+----------+           +---------+-------------+
       | InMemoryMeeting   |           | EscribeMeetingSource  |
       | Source (tests)    |           | (production)          |
       +-------------------+           +---------+-------------+
                                                 |
                                                 v
                              +------------------+----------------+
                              |   EscribeTransport                |   <-- Protocol in app/escribe.py
                              |   fetch_past_meetings_json        |
                              |   fetch_agenda_html               |
                              |   fetch_postminutes_html          |
                              +------------------+----------------+
                                                 |
                              +------------------+------------------+
                              |                                     |
                       +------+----------+                  +-------+----------+
                       | LiveEscribe     |                  | FixtureEscribe   |
                       | Transport       |                  | Transport (tests)|
                       | (HTTP / requests)|                 | (disk replay)    |
                       +-----------------+                  +------------------+
```

Wiring in Flask:

```python
# app/main.py
app.config["meeting_source"] = EscribeMeetingSource(LiveEscribeTransport())

@app.route("/api/meeting/<meeting_id>")
def api_meeting_detail(meeting_id: str):
    source: MeetingSource = current_app.config["meeting_source"]
    detail = source.load_detail(meeting_id)
    ...
```

---

## Implementation Units

Each unit is a compileable + test-passing slice. Order matters; later units depend on earlier.

---

- [ ] **U1. Move `Meeting` and `AgendaItem` to `app/models.py`**

**Goal:** Get the domain types out of the adapter without touching any consumer. After U1, every existing import path still works because `app/scraper.py` re-exports.

**Requirements:** R5

**Dependencies:** None

**Files:**
- Modify: `app/models.py` — add `Meeting`, `AgendaItem` (lift verbatim from `app/scraper.py:60-110`, including `to_dict` + `time_start_formatted`). Keep `AgendaItem` mutable (no `frozen=True`).
- Modify: `app/scraper.py` — replace the `Meeting` / `AgendaItem` definitions with `from app.models import Meeting, AgendaItem` re-exports at top of file.
- Modify: `tests/test_models.py` — extend with smoke tests for `Meeting`, `AgendaItem`, `time_start_formatted` round-trip.

**Approach:**
- Lift the `@dataclass` blocks from `app/scraper.py:60-110` into `app/models.py` unchanged.
- Re-export from `scraper.py` so `from app.scraper import AgendaItem, Meeting` keeps working in `tests/test_scraper.py`, scripts, and any other consumer.
- Run the existing test suite — should pass with zero edits to other files.

**Verify U1:** `pytest tests/` — all green. `grep -rn "from app.scraper import.*\(AgendaItem\|Meeting\)"` still works.

---

- [ ] **U2. Extract presentation config to `app/meeting_types.py`**

**Goal:** Move `MEETING_TABS`, `_SLUG_TO_TYPE`, `MEETING_TYPE` out of the adapter. Re-export from `scraper.py` for now.

**Requirements:** R6

**Dependencies:** None (parallel-safe with U1)

**Files:**
- Create: `app/meeting_types.py` — contains `MEETING_TYPE`, `MEETING_TABS`, `_SLUG_TO_TYPE`.
- Modify: `app/scraper.py` — replace the literal definitions with `from app.meeting_types import MEETING_TABS, _SLUG_TO_TYPE, MEETING_TYPE` re-exports.

**Approach:**
- Lift `app/scraper.py:33-57` verbatim into `app/meeting_types.py`.
- Re-export so existing imports in `app/main.py`, `scripts/build_site.py`, `scripts/summarize_meetings.py`, `scripts/transcribe_meetings.py` keep working.

**Verify U2:** `pytest tests/` — all green. App still boots: `python -c "from app.main import app"`.

---

- [ ] **U3. Define `EscribeTransport` Protocol + `LiveEscribeTransport`; route existing scraper functions through it**

**Goal:** Introduce the bytes seam. Existing public functions (`fetch_past_meetings`, `fetch_meeting_detail`, `fetch_post_minutes`) keep working but now call through a `LiveEscribeTransport` instance instead of `requests.*` directly.

**Requirements:** R2 (partial — only `LiveEscribeTransport`), R11

**Dependencies:** U1, U2

**Files:**
- Create: `app/escribe.py` — contains:
  - `class EscribeTransport(Protocol)` with three methods.
  - `class LiveEscribeTransport` — wraps `requests`. **Always raises** on HTTP failure (no silent swallow). Owns `BASE_URL`, `_AJAX_HEADERS`, `_PAGE_HEADERS`, `_build_video_url`.
- Modify: `app/scraper.py` — replace inline `requests.*` calls in `fetch_past_meetings`, `fetch_meeting_detail`, `fetch_post_minutes` with calls through a module-level `_default_transport = LiveEscribeTransport()`. Inside `fetch_post_minutes`, keep the `try/except: return {"votes": {}, "minutes": {}}` swallow at the *scraper* layer (transport raises; scraper catches) — so existing public-surface behavior is preserved.

**Approach:**
- `LiveEscribeTransport.fetch_past_meetings_json(page, meeting_type) -> dict` — POST, returns `resp.json()` (the full envelope, not the `"d"` blob — let the source unwrap).
- `LiveEscribeTransport.fetch_agenda_html(meeting_id) -> str` — GET the Agenda page. Raises on non-2xx.
- `LiveEscribeTransport.fetch_postminutes_html(meeting_id) -> str` — GET the PostMinutes page. Raises on non-2xx.
- All three share the existing headers/timeouts/`verify=False`.
- The scraper-layer swallow in `fetch_post_minutes` is a known-temporary inversion (the policy will move into `EscribeMeetingSource` in U4). Mark it with a comment: `# TEMP: swallow moves to EscribeMeetingSource in U4`.

**Verify U3:** `pytest tests/` — all green. The Live transport has no direct tests yet (it requires network). The existing parser tests still pass because the parsing helpers are untouched.

**Risk:** subtle — make sure the `_AJAX_HEADERS` `Origin` / `Referer` / timeouts are preserved exactly. The eSCRIBE backend is sensitive to headers; mismatched headers return 200 with empty payloads.

---

- [ ] **U4. Add `MeetingSource` Protocol + `EscribeMeetingSource` + `InMemoryMeetingSource` + `FixtureEscribeTransport`. Real tests.**

**Goal:** The full new seam exists end-to-end. Not yet wired into `main.py` or scripts — those still go through the old `app/scraper.py` public functions.

**Requirements:** R1, R3, R4 (full)

**Dependencies:** U3

**Files:**
- Modify: `app/models.py` — add `MeetingDetail` dataclass (`agenda_items: list[AgendaItem]`, `video_url: str | None`).
- Create: `app/meeting_source.py`:
  - `class MeetingSource(Protocol)` with `list_past`, `load_detail`.
  - `class InMemoryMeetingSource` — `__init__(self, details: dict[str, MeetingDetail], past: Sequence[Meeting] = ())`. `list_past` returns `(list(self.past), len(self.past))` (ignores paging). `load_detail(meeting_id)` looks up in `details`, raises `KeyError` on miss.
- Modify: `app/escribe.py`:
  - Add `class FixtureEscribeTransport` — `__init__(self, fixtures_dir: Path | str)`. Loads files by convention: `past_meetings_{type_slug}_{page}.json`, `agenda_{meeting_id}.html`, `postminutes_{meeting_id}.html`. Raises `FileNotFoundError` for missing fixtures (mirrors the "transport raises" contract).
  - Add `class EscribeMeetingSource` — `__init__(self, transport: EscribeTransport)`. Methods:
    - `list_past(page, meeting_type)` — calls `transport.fetch_past_meetings_json`, parses `Meeting` objects (current logic from `fetch_past_meetings`).
    - `load_detail(meeting_id)` — calls `transport.fetch_agenda_html`, runs the existing orchestration (extract bookmarks, agenda items, propagate timestamps, mark brief, insert recesses, extract recommendations/descriptions/attachments, redistribute confirmation attachments). Then calls `transport.fetch_postminutes_html` inside a `try/except Exception: votes, minutes = {}, {}` block — this is where the policy lives. Merges votes/minutes/recommendations into items. Returns `MeetingDetail`.
  - Lift the pure parser helpers from `app/scraper.py:217-743` into `app/escribe.py` (`_extract_bookmarks`, `_extract_agenda_items`, `_extract_recommendations`, `_extract_descriptions`, `_extract_minutes`, `_extract_attachments`, `_extract_votes`, `_propagate_timestamps`, `_mark_brief_items`, `_insert_recesses`, `_distribute_confirmation_attachments`, `_clean_html`, `_item_blocks`, `_normalize_name`, `_tokenize_for_match`, `_parse_escribemeetings_date`). The constants `MIN_DISCUSSION_MS` and `MIN_RECESS_MS` come too.
  - In `app/scraper.py`, replace the parser implementations with re-exports from `app/escribe.py` (or just delete the bodies and re-import). The public functions `fetch_past_meetings`, `fetch_meeting_detail`, `fetch_post_minutes`, `fetch_meeting_votes` keep working; they now delegate to a module-level `_default_source = EscribeMeetingSource(_default_transport)`. Remove the U3 `# TEMP` swallow — the policy now lives in `EscribeMeetingSource`.
- Create: `tests/fixtures/escribe/` — small hand-built fixture set: one `past_meetings_*.json`, one `agenda_*.html`, one `postminutes_*.html` representative of a real meeting (or trimmed-down).
- Create: `tests/test_meeting_source.py` — cover:
  - `InMemoryMeetingSource` — `list_past` returns supplied data; `load_detail` returns supplied detail; `load_detail` of unknown id raises `KeyError`.
  - `EscribeMeetingSource` driven by `FixtureEscribeTransport` — end-to-end load_detail produces expected `MeetingDetail`; postminutes failure (use a fixture transport that raises for postminutes) yields a `MeetingDetail` with empty vote fields but populated agenda items.

**Approach:**
- The orchestration body of `EscribeMeetingSource.load_detail` is a near-verbatim lift of `fetch_meeting_detail`'s `include_votes=True` branch. The only change is: it always fetches votes (no flag), and the postminutes `try/except` lives here instead of in the transport.
- `InMemoryMeetingSource` is ~15 lines.
- `FixtureEscribeTransport` is ~25 lines (just `Path.read_text` / `json.loads` keyed by filename).

**Verify U4:** `pytest tests/` — all green, including new `test_meeting_source.py`. Existing `tests/test_scraper.py` passes unchanged (its private-helper imports now resolve through the re-export shim, but tests don't notice).

---

- [ ] **U5. Wire Flask `main.py` to `app.config["meeting_source"]`. Drop `include_votes`.**

**Goal:** `app/main.py` no longer imports anything from `app.scraper`. Routes pull `MeetingSource` from `current_app.config`.

**Requirements:** R7

**Dependencies:** U4

**Files:**
- Modify: `app/main.py`:
  - Replace `from app.scraper import fetch_past_meetings, fetch_meeting_detail, MEETING_TABS, _SLUG_TO_TYPE` with `from app.meeting_types import MEETING_TABS, _SLUG_TO_TYPE` and `from app.escribe import EscribeMeetingSource, LiveEscribeTransport` and `from app.meeting_source import MeetingSource` and `from flask import current_app`.
  - At app construction (after `app = Flask(__name__)`): `app.config["meeting_source"] = EscribeMeetingSource(LiveEscribeTransport())`.
  - In each route, replace `fetch_past_meetings(...)` with `current_app.config["meeting_source"].list_past(...)` and `fetch_meeting_detail(meeting_id, include_votes=True)` with `current_app.config["meeting_source"].load_detail(meeting_id)`.
  - Update the response shape: `detail.agenda_items` and `detail.video_url` (attribute access on `MeetingDetail`) instead of `detail["agenda_items"]` / `detail["video_url"]`.
  - Keep the existing `(ConnectionError, SSLError)` handling — those are raised by `LiveEscribeTransport` propagating through `EscribeMeetingSource`.

**Approach:**
- Direct mechanical edit. No behavior change observable to the HTTP client.
- The `summarize=true` branch and `extract_meeting_topics` call still work — they consume `items` (the dict-converted form), unchanged.

**Verify U5:** `pytest tests/` — all green. Manual smoke: `python -c "from app.main import app; c = app.test_client(); print(c.get('/').status_code)"` returns 200.

---

- [ ] **U6. Migrate scripts to construct their own `EscribeMeetingSource`**

**Goal:** No script imports from `app.scraper`. Each script builds its own `EscribeMeetingSource(LiveEscribeTransport())`.

**Requirements:** R8

**Dependencies:** U4 (does not depend on U5; can run in parallel)

**Files:**
- Modify: `scripts/build_site.py` — replace `from app.scraper import fetch_past_meetings, fetch_meeting_detail, MEETING_TABS` with `from app.escribe import EscribeMeetingSource, LiveEscribeTransport` + `from app.meeting_types import MEETING_TABS`. Construct `source = EscribeMeetingSource(LiveEscribeTransport())` at the top of `main()`. Replace `fetch_past_meetings(...)` → `source.list_past(...)`. Replace `fetch_meeting_detail(meeting_id, include_votes=True)` → `source.load_detail(meeting_id)`. Update `.agenda_items` / `.video_url` attribute access. Verify: line 120, 162, 186 references to `MEETING_TABS` keep working.
- Modify: `scripts/summarize_meetings.py` — same pattern. Lines 24, 84, 86 touched.
- Modify: `scripts/transcribe_meetings.py` — same pattern. Lines 23, 45, 47 touched. Note: this script only uses `fetch_past_meetings` + `MEETING_TABS`, not `fetch_meeting_detail`, so the `load_detail` migration is a no-op here.

**Approach:**
- Identical mechanical edit across three scripts. Build the source once per script (not per loop iteration); pass it down or capture in closure.

**Verify U6:** `python -c "import scripts.build_site, scripts.summarize_meetings, scripts.transcribe_meetings"` imports cleanly. `pytest tests/` all green.

---

- [ ] **U7. Delete `app/scraper.py`. Rename `tests/test_scraper.py` → `tests/test_escribe.py`. Drop dead public functions.**

**Goal:** No more `app.scraper` module. The shim is gone; the public surface is exactly `MeetingSource` + `EscribeTransport` + their adapters.

**Requirements:** R9, R10

**Dependencies:** U5, U6

**Files:**
- Delete: `app/scraper.py`.
- Rename + modify: `tests/test_scraper.py` → `tests/test_escribe.py`. Update top imports from `from app.scraper import (AgendaItem, Meeting, _parse_escribemeetings_date, _clean_html, _extract_bookmarks, _extract_votes, _extract_recommendations, _extract_minutes, _propagate_timestamps, _mark_brief_items, _insert_recesses)` to pull `AgendaItem, Meeting` from `app.models` and the rest from `app.escribe`.
- Verify nothing else imports `app.scraper`: `grep -rn "from app.scraper\|import app.scraper" --include="*.py"` returns empty.

**Approach:**
- The rename is purely an import update. The test bodies (assertions on parser behavior, AgendaItem construction) are unchanged — `Meeting` and `AgendaItem` are the same classes, just in `app.models`; the parser helpers are the same functions, just in `app.escribe`.
- After this unit, the `fetch_*` functions are gone. Anything that still depends on them will fail at import. Treat any such failure as a missed consumer in the U5/U6 audit.

**Verify U7:** `pytest tests/` — all green. `grep -rn "fetch_past_meetings\|fetch_meeting_detail\|fetch_post_minutes\|fetch_meeting_votes\|include_votes" --include="*.py"` returns empty (or only matches inside CHANGELOG / docs).

---

## Risk Register

- **eSCRIBE header sensitivity (U3).** `_AJAX_HEADERS` mismatch causes silent empty payloads from the backend. Lift verbatim; don't rewrite.
- **Postminutes silent-swallow inversion window (U3 → U4).** Between U3 and U4, the swallow lives in scraper.py; the transport raises. Make sure U3 doesn't ship in isolation if you can avoid it — the public-surface behavior is unchanged, but a debugger looking at `LiveEscribeTransport.fetch_postminutes_html` will see a raise that didn't happen in the old code. Mark with a `# TEMP` comment; remove in U4.
- **`MeetingDetail` dict→object shape change (U5).** Routes change from `detail["agenda_items"]` to `detail.agenda_items`. Easy to miss the `.video_url` access too. Manual smoke required.
- **Scripts that emit JSON (U6).** `build_site.py` likely serializes `AgendaItem` via `to_dict`. Confirm `MeetingDetail` is iterated correctly — agenda items are still `AgendaItem` instances, no shape change for them.
- **`AgendaItem` mutability (U4).** The orchestration mutates `recommendation`, `vote_result`, `vote_detail`, `is_contested`, `content`, `attachments`. Do not freeze. A future plan can move to a builder + frozen `AgendaItem` if desired; out of scope here.
- **Fixture corpus thinness (U4).** Initial fixture set is hand-built and small. `EscribeMeetingSource` end-to-end coverage is shallow until real eSCRIBE responses are recorded. Acceptable starting point; flag for follow-up.

---

## Verification After Each Slice

| Unit | Command | Expected |
|------|---------|----------|
| U1   | `pytest tests/` | All green; existing imports unchanged. |
| U2   | `pytest tests/` + `python -c "from app.main import app"` | All green; app boots. |
| U3   | `pytest tests/` | All green; behavior unchanged at the public surface. |
| U4   | `pytest tests/test_meeting_source.py tests/test_scraper.py` | New tests + existing parser tests both green. |
| U5   | `pytest tests/` + `curl localhost:5000/api/meetings` (manual) | All green; routes return identical JSON. |
| U6   | `python -m scripts.build_site --help` (or equivalent for each) | Imports cleanly. |
| U7   | `pytest tests/` + `grep -rn "from app.scraper" --include="*.py"` | All green; grep empty. |

---

## Definition of Done

- All 11 requirements (R1–R11) satisfied.
- `app/scraper.py` does not exist.
- `grep -rn "from app.scraper\|fetch_past_meetings\|fetch_meeting_detail\|fetch_post_minutes\|fetch_meeting_votes\|include_votes" --include="*.py"` returns empty.
- `pytest tests/` is fully green, including new `tests/test_meeting_source.py`.
- `app/main.py` boots; smoke-tested routes return identical JSON to pre-refactor responses.
- CONTEXT.md is consistent with the implementation (already updated as part of grilling — verify no drift on close).
