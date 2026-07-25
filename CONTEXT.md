# Domain & Architecture Vocabulary

Use these terms exactly. Drift creates re-litigation.

## Domain

- **Meeting** — a city council (or related body) meeting, identified by a `meeting_id` string assigned by the upstream escribemeetings system. `meeting_id` is the canonical key throughout the codebase.
- **Transcript** — the time-aligned text of a Meeting's video, produced by Whisper. A first-class domain type (not just a list of segment dicts).
- **ItemSummary** — the structured summary of one agenda item within a Meeting. One Meeting has many. An **aggregate** of two parts:
  - **Description** — a mandatory plain-language explanation of what the item actually does, written for a busy resident. Not a Chip and not a category: it is a required field of the LLM response schema, so the model cannot decline it. Its absence is a bug, never a fallback.
  - **Chips** — zero or more, each carrying one specific fact.

  An ItemSummary with chips but no Description is invalid. Restating the agenda item's title is not a Description.
- **Legacy ItemSummary** — a summary cached before the aggregate format: a bare `list[{category, text}]` with no Description. Structurally distinguishable on load, so no migration is needed. Meetings outside the current council term keep theirs until backfilled, and the UI marks them as lower-confidence rather than pretending they meet the current bar.
- **Chip** — a `(category, text)` pair emitted by the categorizer for one agenda item. `category` is drawn from a closed list of 22 labels. `text` is trimmed to ≤100 characters at a natural clause break.
- **Hard chip / soft chip** — chips split by extraction trust, not by cost:
  - **Hard chips** (11 categories: Outcome, Vote Breakdown, Cost & Funding, Amendment Made, Procedural Note, Delegation, Next Step, Related Item, Deferred From, Declared Conflict, Data Cited) carry civic/legal weight. They are extracted deterministically (regex + structured eSCRIBE fields) so the source of truth is auditable. **LLMs do not produce hard chips.**
  - **Soft chips** (the remaining 11 categories — Debate Highlight, Who's Affected, Dissenting View, etc.) are interpretive. They are produced by a single Gemini call against a JSON schema of `{description, chips: [{category, text, usefulness}]}`.

  "In Plain Terms" was formerly a 23rd, soft category. It is **retired** — what it tried to express is now the mandatory Description. A declinable description is what produced title-echo summaries.
- **MeetingTopics** — LLM-derived high-level topics for a Meeting. Currently not cached.
- **Procedural Item** — an agenda item whose title matches a fixed set of ceremonial/housekeeping keywords (call to order, adjournment, roll call, adoption of minutes, etc.). Procedural items are filtered out of topic ranking and short-circuited to a fixed "Procedural item." summary.
- **Consent Item** — an agenda item approved inside the consent-agenda block, in one motion, without individual debate. Detected by an inherited timestamp: it shares its parent section's time span because no distinct span exists for it.

  A Consent Item **does** get an ItemSummary. It has substantial official recommendation text and no transcript, so its Description is derived from metadata alone and it is eligible only for Chip categories that don't require discussion. Debate Highlight, Dissenting View, and Public Sentiment are excluded **by construction**, not left for the model to decline — an item that was never discussed cannot have a debate highlight, and a prompt that offers the category invites invention.

  Distinct from a **Section Header** — an agenda entry with no timestamp, no recommendation, and no content (`COMMITTEE REPORTS`, `ADMINISTRATIVE REPORTS`, `Standing Policy Committee on …`). Section Headers are structural containers and never get an ItemSummary.

## Architecture

- **Cache** — a typed key-value store for derived artifacts that are expensive to recompute. Not a TTL/eviction cache; closer to "the durable home for derived data, recomputed only when missing." Keyed by `meeting_id`.
  - Interface: `load(meeting_id) -> T | None`, `save(meeting_id, T) -> None`
  - Lifecycle: opened as a context manager. Setup (fetch from remote) on enter. Flush (push to remote) on exit, **including on exceptions** — partial progress is preserved.
  - Per-key storage on disk (one file per meeting), not all-at-once blobs.
- **TranscriptCache** — `Cache[Transcript]`. Owns Transcript (de)serialization.
- **ItemSummariesCache** — `Cache[list[ItemSummary]]`. Owns ItemSummary (de)serialization.
- **CleanTranscriptCache** — `Cache[dict[item_id, str]]`. Holds the **CleanTranscript** for each agenda item of a Meeting.
- **CleanTranscript** — one agenda item's transcript slice after the Gemini cleanup pass: fillers and false starts removed, sentences punctuated, garbled proper nouns corrected against a fixed Saskatoon name list. Plain text, not a typed Transcript — the time alignment is spent by this point.

  It is cached because it is the expensive half of summarization: cleanup must *emit* the whole slice, ~68k output tokens for one council meeting, which is what made prompt iteration unworkable. Caching it splits the cost in two — changing the **cleanup** prompt busts the cache and costs a full re-run; changing the **chip** prompt does not and costs seconds. Cache-busting is therefore a correctness requirement, not an optimization.
- **Cache adapters:**
  - `GitBranchCache` — production adapter; persists to a git orphan branch, pushes on flush.
  - `InMemoryCache` — test adapter; no I/O, no git.
  - `LocalDirCache` — fixture adapter; file-per-key JSON in a plain local directory, written through immediately, no remote. Exists so committed fixtures can back a cache: the eval loop reads CleanTranscripts out of `tests/fixtures/eval` instead of re-deriving them, which keeps the loop fast and CI free of cleanup calls. Because the directory is version-controlled, what the cleanup pass produced is reviewable in a diff.

Scraped upstream data (escribemeetings) is **not** routed through Cache — it's a TTL/invalidation problem with different semantics.

- **MeetingSource** — the seam for "give me Meetings and MeetingDetails." Domain-shaped; returns `Meeting`, `MeetingDetail`, `AgendaItem` — no HTML, no JSON. Adapters:
  - `EscribeMeetingSource` — production adapter; built on `EscribeTransport`. Owns eSCRIBE-specific normalization (timestamp propagation, brief-item marking, recess insertion, confirmation-attachment redistribution, preferring minutes over agenda descriptions).
  - `InMemoryMeetingSource` — test adapter; hand-written `MeetingDetail` objects. Lets pipeline tests (summary, topics) run without HTML parsing.
- **EscribeTransport** — the seam for "carry bytes to/from eSCRIBE." Returns raw HTML strings or parsed JSON dicts; does not parse HTML or shape domain types. Adapters:
  - `LiveEscribeTransport` — production; HTTP via `requests`.
  - `FixtureEscribeTransport` — test; replays recorded HTML/JSON from disk. Lets `EscribeMeetingSource` be tested end-to-end without network.

`Meeting` and `AgendaItem` are domain types and live in `app/models.py`, not inside any adapter.