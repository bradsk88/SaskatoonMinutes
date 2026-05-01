# Domain & Architecture Vocabulary

Use these terms exactly. Drift creates re-litigation.

## Domain

- **Meeting** — a city council (or related body) meeting, identified by a `meeting_id` string assigned by the upstream escribemeetings system. `meeting_id` is the canonical key throughout the codebase.
- **Transcript** — the time-aligned text of a Meeting's video, produced by Whisper. A first-class domain type (not just a list of segment dicts).
- **ItemSummary** — the structured summary of one agenda item within a Meeting (plain-language description, outcome, chips). One Meeting has many.
- **MeetingTopics** — LLM-derived high-level topics for a Meeting. Currently not cached.
- **Procedural Item** — an agenda item whose title matches a fixed set of ceremonial/housekeeping keywords (call to order, adjournment, roll call, adoption of minutes, etc.). Procedural items are filtered out of topic ranking and short-circuited to a fixed "Procedural item." summary.

## Architecture

- **Cache** — a typed key-value store for derived artifacts that are expensive to recompute. Not a TTL/eviction cache; closer to "the durable home for derived data, recomputed only when missing." Keyed by `meeting_id`.
  - Interface: `load(meeting_id) -> T | None`, `save(meeting_id, T) -> None`
  - Lifecycle: opened as a context manager. Setup (fetch from remote) on enter. Flush (push to remote) on exit, **including on exceptions** — partial progress is preserved.
  - Per-key storage on disk (one file per meeting), not all-at-once blobs.
- **TranscriptCache** — `Cache[Transcript]`. Owns Transcript (de)serialization.
- **ItemSummariesCache** — `Cache[list[ItemSummary]]`. Owns ItemSummary (de)serialization.
- **Cache adapters:**
  - `GitBranchCache` — production adapter; persists to a git orphan branch, pushes on flush.
  - `InMemoryCache` — test adapter; no I/O, no git.

Scraped upstream data (escribemeetings) is **not** routed through Cache — it's a TTL/invalidation problem with different semantics.