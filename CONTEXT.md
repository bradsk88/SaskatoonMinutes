# Domain & Architecture Vocabulary

Use these terms exactly. Drift creates re-litigation.

## Scope

**TODO — eventually this should serve other city councils.** Not yet.
Saskatoon-specific detail is **accepted on purpose** for now: a second
city is the only thing that reveals which of these details are actually
municipal-generic, and generalizing before then produces abstractions
shaped by one city wearing a neutral name.

Where the specificity currently lives, as a starting list for whoever
does the generalization:

- `_KNOWN_NAMES` (`scripts/add_eval_fixture.py`) — councillors,
  neighbourhoods, and local vocabulary. Per-city by nature. Only used to
  rank candidate eval fixtures by how many *unfamiliar* names they carry;
  it is never shown to a model (ADR `0005`).
- The prompts in `app/item_categorizer.py` and `app/summary_judge.py`
  name Saskatoon and its council structure directly.
- `EscribeMeetingSource` / `EscribeTransport` assume eSCRIBE. Many
  Canadian municipalities use it, so this is the *most* portable layer —
  but "the upstream agenda system" is the seam a second city would test.
- The meeting bodies and tabs (`council`, `public-hearing`, `police`,
  `governance`, …) are Saskatoon's committee structure.
- Templates and site copy in `app/templates/`.

Rule of thumb until then: keep Saskatoon facts in *data and prompts*
rather than in control flow, so generalizing later is a matter of
swapping values, not untangling branches.

## Domain

- **Scheduled Meeting** — a Meeting that has not happened yet: announced on the upstream calendar, `meeting_id` already assigned, agenda possibly published, but no video, minutes, or votes. A distinct concept from Meeting, not a state of one — it carries no Outcome vocabulary, and nothing about it is an Outcome. Shown only on the **Future tab** (always the left-most tab), scoped to the union of bodies in `MEETING_TABS`, sorted by date, each row naming its body. A Scheduled Meeting with no published agenda yet appears as a bare row (date and body, no items). Scheduled Meetings never appear in the Atom feeds — they are not Meeting Days and nothing about them is Settled. The Future tab is exempt from the Index Card's Space Budget: its purpose is seeing everything clearly, not finding things at a glance, so a Scheduled Meeting shows all its agenda items in agenda order, title plus provisional Description, with no outcome badges and no ranking. Each Scheduled Meeting carries a static "how to register to speak" blurb linking to the City's submission form, plus its **Request-to-Speak Deadline**: 5:00 p.m. on the Monday of the meeting week, computed from the meeting date (unchanged on holidays, per the City). Once a Scheduled Meeting appears in PastMeetings it is an ordinary Meeting and the concept no longer applies to it.

  Its agenda items may carry **provisional ItemSummaries**: produced Consent-Item-style (official text only, discussion-only categories excluded by construction), stored in the normal ItemSummariesCache under the same `meeting_id` (ADR `0021`). Provisional summaries are never revised pre-meeting — agenda revisions are ignored — because the flip to Meeting triggers regeneration with the transcript, which corrects any drift. They run at **lower priority** than post-meeting summaries: quota goes to real summaries first, provisional ones only when caught up. The whole feature is an **experiment** — the value of pre-meeting summaries is unproven and the design may be revisited.
- **Meeting** — a city council (or related body) meeting, identified by a `meeting_id` string assigned by the upstream escribemeetings system. `meeting_id` is the canonical key throughout the codebase.
- **Transcript** — the time-aligned text of a Meeting's video, produced by Whisper. A first-class domain type (not just a list of segment dicts).
- **ItemSummary** — the structured summary of one agenda item within a Meeting. One Meeting has many. An **aggregate** of two parts:
  - **Description** — a mandatory plain-language explanation of what the item actually does, written for a busy resident. Not a Chip and not a category: it is a required field of the LLM response schema, so the model cannot decline it. Its absence is a bug, never a fallback.
  - **Chips** — zero or more, each carrying one specific fact.

  An ItemSummary with chips but no Description is invalid. Restating the agenda item's title is not a Description.
- **Legacy ItemSummary** — a summary cached before the aggregate format: a bare `list[{category, text}]` with no Description. Structurally distinguishable on load, so no migration is needed. Meetings outside the current council term keep theirs until backfilled, and the UI marks them as lower-confidence rather than pretending they meet the current bar.
- **Chip** — a `(category, text)` pair emitted by the categorizer for one agenda item. `category` is drawn from a closed list of 22 labels. `text` is trimmed to ≤100 characters at a natural clause break.
- **Outcome vocabulary** — the Outcome chip records *what kind of action was taken*, which lives in the recommendation, not in the vote. A carried vote is not automatically an approval:
  - `Approved` — the deciding body authorized, funded, or directed something.
  - `Recommended to Council` — a committee carried a motion recommending something **to City Council**. Council has not acted. Reporting this as `Approved` tells a resident the opposite of what happened.
  - `Received as information` — the body declined to decide ("That the information be received").
  - `Deferred`, `Defeated`, `Withdrawn` as named.

- **Hard chip / soft chip** — chips split by extraction trust, not by cost:
  - **Hard chips** (11 categories: Outcome, Vote Breakdown, Cost & Funding, Amendment Made, Procedural Note, Delegation, Next Step, Related Item, Deferred From, Declared Conflict, Data Cited) carry civic/legal weight. They are extracted deterministically (regex + structured eSCRIBE fields) so the source of truth is auditable. **LLMs do not produce hard chips.**
  - **Soft chips** (the remaining 11 categories — Debate Highlight, Who's Affected, Dissenting View, etc.) are interpretive. They are produced by a single Gemini call against a JSON schema of `{description, chips: [{category, text, usefulness}]}`.

  "In Plain Terms" was formerly a 23rd, soft category. It is **retired** — what it tried to express is now the mandatory Description. A declinable description is what produced title-echo summaries.
- **Speaker** — one member of the public who addressed council on an agenda item: name, organization, stance (`support` / `concern` / `""` for informational), and `said`, the bullets of what they argued. Built in two halves. The **roster** — who came — is deterministic and rebuilt on every page build (`app/speakers.py`): PostMinutes prose, plus a pass over Request-to-Speak ("RTS") attachment filenames for anyone the prose only mentions in passing. What they **said** costs a Gemini call, so it is cached on `ItemSummary.speakers` and merged back onto the roster by `merge_remarks`. A meeting no summarize run has reached has a roster and no substance; that is expected, not missing data.

  Not "presentation": the city never uses the word (the June 24 agenda has 0 uses against 27 for "RTS"), it describes what *staff* do with a report and a PowerPoint — which is the false positive the name test exists to reject — and it collided with the Delegation chip below. Distinct from the **Delegation chip**: the chip is a single first-match soundbite folded into an item's Chips grid; the roster is every speaker, deduplicated, each with their own card on the detail page and, for up to three of them, a row on the index card.
- **Eval Fixture** — a real Meeting trimmed to a handful of agenda items, committed under `tests/fixtures/eval` as a `.detail.json` / `.transcript.json` pair. **Not a whole meeting**: the three original fixtures carry 1, 5 and 6 items. The trim is what keeps a full eval to seconds and a dozen Gemini calls, and it means fixture item counts say nothing about real meeting sizes.
- **Arm** — one side of a summarization A/B. An arm is a property of *an item's* comparison, not of a label: the blind pairs randomize the A/B labels per item, so "A" is one arm on some items and the other arm on the rest. Cross-item claims about "A" are meaningless — aggregate only after unblinding. The cleanup A/B (clean arm vs raw arm) is settled and its harness is gone; the term stays because the next A/B will re-use the shape.
- **MeetingTopics** — LLM-derived high-level topics for a Meeting. Currently not cached.
- **Procedural Item** — an agenda item whose title matches a fixed set of ceremonial/housekeeping keywords (call to order, adjournment, roll call, adoption of minutes, etc.). Procedural items are filtered out of topic ranking and short-circuited to a fixed "Procedural item." summary.
- **Consent Item** — an agenda item approved inside the consent-agenda block, in one motion, without individual debate. Detected by an inherited timestamp: it shares its parent section's time span because no distinct span exists for it.

  A Consent Item **does** get an ItemSummary, provided its recommendation is substantive. It has no transcript, so its Description comes from official text alone, and the five **discussion-only categories** — Debate Highlight, Staff vs. Council, Unanswered Question, Public Sentiment, Dissenting View — are excluded **by construction**, not left for the model to decline. An item that was never discussed cannot have a debate highlight, and a prompt that offers the category invites invention.

  A Consent Item whose recommendation is **boilerplate** ("That the report be received as information") is *not* summarizable: council resolved nothing, there is no transcript, and any description would be the title restated. It gets no ItemSummary. Length is not the signal — a 90-character "That Councillor MacDonald be appointed to the Meewasin Valley Authority" summarizes fine.

  An inherited timestamp identifies the *parent's* audio, never the item's, so transcript slicing refuses it outright. Otherwise every Consent Item in a block would be handed the same recording of the clerk reading the block into the record.

  Distinct from a **Section Header** — an agenda entry with no recommendation and no content, which either has no time span or borrows its parent's (`COMMITTEE REPORTS`, `ADMINISTRATIVE REPORTS`, `Standing Policy Committee on …`). Section Headers are structural containers and never get an ItemSummary.
- **Feed Entry** — one published unit of the Atom feeds. Two feeds, differing only in granularity, never in what qualifies:
  - **Meeting Day** (`/feed.xml`) — one entry per calendar day the city sat, whatever number of bodies sat that day, with the bodies as headings inside it. A busy Tuesday is one entry, never three. This is the default feed.
  - **Item Entry** (`/feed-items.xml`) — one entry per qualifying agenda item.

  An item **qualifies** by having something to say — a Description or an interpretive chip — and qualifying items are then ranked by discussion minutes, capped at 8 per meeting. Deliberately **not** the index card's ranking: `extract_meeting_topics` saturates duration at twenty minutes and mixes Speaker rows in with items, so it drops the longest debates of the year (`TODO.md` item 15).
- **Index Card** — a Meeting's summary block on the index page, meant to get a reader into the right Meeting, not to list it. What it may show is bounded by a **Space Budget** of 15 units (roughly one mobile screen), spent on rows of different costs:
  - **Detailed Row** (3 units) — an agenda item's title, outcome, and summary bullets. Earned by having a recorded outcome; up to five.
  - **Title-Only Row** (1 unit) — an agenda item's title and outcome badge, nothing else. How a ranked item past the detailed five still gets named, and what a Detailed Row demotes to under pressure.
  - **Speaker Row** (2 units) — one Speaker's name, organization, and stance. Up to three.
  - **Speaker Digest** (1 unit per row) — the collapsed form of a Meeting's Speakers: one slim row per **represented organization** (all of them, never a sample) plus a residents roll-up. The digest is the one thing the Space Budget never cuts — hiding which orgs had a voice is not an acceptable saving. Only voices actually heard count: a registered-only name (an RTS filing with no remarks and no chair introduction in the transcript) proves intent, not attendance, and is excluded.

  When a card exceeds its budget it spends down in a fixed order (ADR `0020`): Speaker Rows collapse to the digest first, then Title-Only Rows drop from the bottom, then Detailed Rows demote. Council rows keep priority over speaker detail throughout.
- **Settled** — a Meeting Day is publishable once every Meeting on it has cached summaries, or seven days have passed. Nothing is published thin and filled in later: a reader who saw the thin version would never be shown the full one. The seven days are what stops a meeting with no video from holding a day hostage forever.

## Architecture

- **Cache** — a typed key-value store for derived artifacts that are expensive to recompute. Not a TTL/eviction cache; closer to "the durable home for derived data, recomputed only when missing." Keyed by `meeting_id`.
  - Interface: `load(meeting_id) -> T | None`, `save(meeting_id, T) -> None`
  - Lifecycle: opened as a context manager. Setup (fetch from remote) on enter. Flush (push to remote) on exit, **including on exceptions** — partial progress is preserved.
  - Per-key storage on disk (one file per meeting), not all-at-once blobs.
- **TranscriptCache** — `Cache[Transcript]`. Owns Transcript (de)serialization.
- **ItemSummariesCache** — `Cache[list[ItemSummary]]`. Owns ItemSummary (de)serialization.
- **CleanTranscript** / **CleanTranscriptCache** — **retired** (ADR `0005`). A CleanTranscript was an agenda item's transcript slice rewritten by Gemini before summarization. Blind judges preferred summaries made from the untouched slice, so the pass and its cache are deleted and the chip call reads the raw slice. The cached text still sits on the `clean-transcripts` branch, which no code reads.
- **Cache adapters:**
  - `GitBranchCache` — production adapter; persists to a git orphan branch, pushes on flush.
  - `InMemoryCache` — test adapter; no I/O, no git.

Scraped upstream data (escribemeetings) is **not** routed through Cache — it's a TTL/invalidation problem with different semantics.

- **MeetingSource** — the seam for "give me Meetings and MeetingDetails." Domain-shaped; returns `Meeting`, `MeetingDetail`, `AgendaItem` — no HTML, no JSON. Adapters:
  - `EscribeMeetingSource` — production adapter; built on `EscribeTransport`. Owns eSCRIBE-specific normalization (timestamp propagation, brief-item marking, recess insertion, confirmation-attachment redistribution, preferring minutes over agenda descriptions).
  - `InMemoryMeetingSource` — test adapter; hand-written `MeetingDetail` objects. Lets pipeline tests (summary, topics) run without HTML parsing.
- **EscribeTransport** — the seam for "carry bytes to/from eSCRIBE." Returns raw HTML strings or parsed JSON dicts; does not parse HTML or shape domain types. Adapters:
  - `LiveEscribeTransport` — production; HTTP via `requests`.
  - `FixtureEscribeTransport` — test; replays recorded HTML/JSON from disk. Lets `EscribeMeetingSource` be tested end-to-end without network.

- **`app/feeds.py`** — the Atom feeds. Pure: dicts in, XML strings out, so a real feed can be built from fixtures with no network and no site build. `build_site` calls it once at the end, from data it already holds, so the feeds cost no extra fetch. XML is assembled with `ElementTree` rather than string templates — one unescaped `&` in an upstream title makes the whole file invalid, and readers reject invalid XML outright rather than degrading.

`Meeting` and `AgendaItem` are domain types and live in `app/models.py`, not inside any adapter.