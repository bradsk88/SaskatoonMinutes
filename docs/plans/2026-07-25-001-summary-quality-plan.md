---
title: "quality: ItemSummary aggregate, consent-item coverage, and a fast prompt-iteration loop"
type: quality
status: active
date: 2026-07-25
---

# quality: ItemSummary aggregate, consent-item coverage, and a fast prompt-iteration loop

## Overview

The summaries on the site are bad in three independent ways, and only one of them is the prompt. This plan fixes the format that makes good summaries impossible, opens up the 27-items-per-meeting the pipeline currently refuses to look at, and — first — makes the iteration loop fast enough that we can actually collaborate on prompt wording instead of waiting twelve minutes per experiment.

Settled through a grilling session on 2026-07-25. Decisions are recorded in `CONTEXT.md` (ItemSummary, Legacy ItemSummary, Consent Item, Section Header, CleanTranscript, CleanTranscriptCache) and in ADRs `0003` and `0004`.

---

## Problem Frame

### Evidence

Audit of the full cached corpus (655 meetings, 16,210 agenda items):

| | count |
|---|---|
| items with **any** chip | 3,396 (21%) |
| Outcome chips | 2,951 |
| **In Plain Terms chips** | **2,567** |
| Vote Breakdown | 573 |
| Cost & Funding | 410 |
| **all 11 soft categories combined** | **118** |

Who's Affected: 33. Debate Highlight: 17. Dissenting View: 2. Legal Risk Flagged: 1 — across 655 meetings. The interpretive layer that the product exists to provide is statistically absent, and what fills the gap is the item's own title read back to the reader.

On the 2026-06-24 council meeting specifically (73 items):

| dropped by | count |
|---|---|
| `timestamp_inherited` (Consent Items) | 27 |
| no timestamps (Section Headers) | 19 |
| procedural | 15 |
| **eligible** | **12** |

### Three distinct causes

1. **The published cache is stale.** Current code already produces `Outcome: Approved` and `Vote Breakdown: 11 for, 0 against` where the cache says `Outcome: Recommended` and nothing else. Some of the badness is fixes that exist in the tree but never reached the site.

2. **The format makes the failure inevitable.** The plain-language explanation is a *category the model may decline* ("In Plain Terms"). When it declines, `_extract_in_plain_terms` fires and echoes the title. Prompt wording cannot make a declinable field mandatory.

3. **Coverage is gated before the LLM.** The 27 Consent Items carry substantial official recommendation text (321c, 555c, 2084c) and no transcript. `GeminiExtractor._has_metadata` (`app/item_categorizer.py:532`) already supports running with no transcript — `is_eligible_for_summary` rejects them upstream. The 19 Section Headers are correctly excluded and stay excluded.

### Why iteration was impossible

The cleanup pass must *emit* every item's transcript slice: ~270k chars ≈ 68k output tokens for one council meeting, serially, per item. Measured 18.4s for the *smallest* item (1.2k chars); a full-meeting run exceeded 10 minutes without finishing. Every chip-prompt experiment re-paid for cleanup it hadn't changed.

---

## Requirements Trace

- **R1.** `CleanTranscriptCache` exists as a `Cache[dict[item_id, str]]`, following the `TranscriptCache` / `ItemSummariesCache` wrapper pattern (ADR `0002`) on branch `clean-transcripts`.
- **R2.** The cleanup prompt's identity is stored with the cached value. A changed cleanup prompt cannot read through to stale text. (ADR `0004`.)
- **R3.** `ItemSummary` is `{description: str, chips: list[Chip]}`. `description` is a required field of the Gemini response schema. "In Plain Terms" is removed from `CATEGORIES` (23 → 22) and `SEMANTIC_DEFINITIONS` (12 → 11). `_extract_in_plain_terms` and `_BOILERPLATE_REC_RE` are deleted.
- **R4.** `ItemSummary.from_dict` detects a bare `list[{category, text}]` and loads it as a **Legacy ItemSummary** with no description. No migration script.
- **R5.** Consent Items are eligible for summary. Their prompt states there was no discussion, and Debate Highlight / Dissenting View / Public Sentiment are excluded from `allowed_cats` by construction.
- **R6.** Section Headers (no timestamp, no recommendation, no content) remain ineligible.
- **R7.** `scripts/eval_chips.py --diff` prints only what changed against a committed baseline snapshot; unchanged items are counted, not printed.
- **R8.** Per-item extraction runs concurrently (~8 in flight) in both the eval script and `scripts/summarize_meetings.py`.
- **R9.** The eval fixture set includes at least one Consent Item and one long debated item.
- **R10.** `Cost & Funding` does not attribute money mentioned in one item to a different item.
- **R11.** The UI renders `description` as the item's lede and marks Legacy ItemSummaries as lower-confidence.
- **R12.** Meetings from the 2024-2028 council term forward are re-summarized. Older meetings keep their Legacy ItemSummary.

---

## Units of Work

Ordered so the loop gets fast **first** — every later unit is iterated on using it.

### U1 — Concurrency + CleanTranscriptCache (the loop) — **done**

Makes every subsequent unit cheap to evaluate. No behaviour change to chip content.

- `app/clean_transcript_cache.py` — `Cache[dict[str, str]]` wrapper, branch `clean-transcripts`, dir `clean-transcripts`.
- Store the cleanup prompt's identity alongside each value; a mismatch is a miss, not a hit (R2).
- Thread-pool the per-item loop in `scripts/eval_chips.py` and `scripts/summarize_meetings.py` (R8). Gemini calls are I/O-bound; a `ThreadPoolExecutor` is sufficient.
- **Exit check:** second run of the eval over the same fixtures completes in seconds with zero cleanup calls. **Met: 2m42s cold → 27s warm, silent stderr.**

**Not in the original plan, required by reality:**

- **Chunked cleanup.** Caching alone did not fix the loop. The cold run stalled >10 minutes on one 117k-character agenda item, because a single Gemini call cannot emit a 100-minute transcript at a usable speed. Cleanup now splits on segment boundaries (`CLEANUP_CHUNK_CHARS = 8000`) and a whole meeting's chunks fan out through **one** pool — fanning out per item would nest pools and multiply the concurrency ceiling. Chunk size is part of the fingerprint, since it changes how much context the model sees per call.
- **A truncation guard.** `clean()` returned `response.text` unchecked. A cleanup that hit the output cap would produce fluent prose missing its tail, cache it under a valid fingerprint, and look correct downstream. It now rejects `MAX_TOKENS` finishes and sub-50%-retention output and falls back to raw, loudly.

### U2 — `--diff` against a committed baseline — **done**

- Snapshot current eval output to `tests/fixtures/eval/baseline.json`, committed.
- `--diff` prints per-item added/removed/changed lines only, plus an `N changed, M unchanged` footer (R7).
- `--check` gates stay as regression guards. Add a Consent Item and a long debated item to the fixtures (R9).
- **Exit check:** a no-op code change prints `0 changed`. **Partially met — see below.**

**What U2 turned up:**

- **Vote Breakdown was broken for every unanimous vote.** `_VOTE_TALLY_RE` required both `In Favour: (N)` *and* `Against: (M)` in one pattern, but eSCRIBE omits sides with no members — a unanimous carry reads `In Favour: (5) … Absent: (1) … CARRIED UNANIMOUSLY` with no `Against:` at all. The two sides are now parsed independently (`_parse_vote_tally`, shared with `_is_unanimous_tally`), and `Absent` is explicitly not counted as against. This is why the corpus had 573 Vote Breakdown chips against 2,951 Outcomes. Fixing it added the chip to 6 of 9 fixture items.
- **Chip temperature dropped 0.2 → 0.** At 0.2 an unchanged run reported 9/9 items changed, burying real deltas under paraphrase. At 0 it reports 2/9. Chips are extraction, not composition.
- **Residual nondeterminism is real and does not fully go away.** The two longest items still churn between runs, so `0 changed` is not achievable — the diff footer instead separates *categories gained/lost* (structural, usually why we ran the eval) from *chips reworded within a category* (model paraphrase). Read the structural lines first.
- **`load_dotenv` at import scope leaked a live API key into `os.environ` for the whole test process**, so unit tests that merely constructed a `GeminiExtractor` began calling Gemini for real (suite time 1.0s → 18.4s). It now loads inside `main()`, with a test asserting the module has no `load_dotenv` attribute.
- **`In Plain Terms` flaps run-to-run** between a real description and the title-echo fallback on identical input — stronger evidence for U3's mandatory-field fix than the corpus audit was.

### U3 — `ItemSummary` becomes an aggregate — **done**

The format change. ADR `0003`.

- `Chip` dataclass `(category, text)`. `ItemSummary` becomes `(description, chips)`.
- Response schema → `{description: str, chips: [{category, text, usefulness}]}`, `description` required.
- Retire "In Plain Terms"; delete `_extract_in_plain_terms` (R3).
- `from_dict` legacy detection (R4).
- `ItemSummariesCache` serialization follows.
- **Exit check:** no fixture item produces a description equal to its title; `is_title_echo` rate on descriptions is 0. **Met.**

**Result on the 9 fixture items:**

| | before U3 | after U3 |
|---|---|---|
| title-echo chips | 32% (10/31) | **0%** (0/46) |
| descriptions present | n/a | **9/9** |
| descriptions that echo the title | n/a | **0** |
| chips per item | 4.4 | 5.1 |

**What U3 turned up:**

- **`_extract_outcome` was appending the item title**, which accounted for **100%** of the remaining title echo once the description existed (`Approved: Shaw Centre – Score Clock and Timing Equipment – Request for Additional Funding`). The title was added there to give the chip context back when a summary was nothing but chips; the Description carries that now, so Outcome is just the verdict — `Approved`, `Approved (8-3)`. Removing it took chip echo from 17% to 0%.
- **`MAX_DESCRIPTION_CHARS` is a target, not a ceiling.** Raising it 220 → 280 to accommodate four overruns made the model write *longer* (335, 314, 310 chars), pad process detail back in ("received the report as information and reaffirmed…"), and push one item into a title echo. Reverted to 220, which produces tighter and better writing. Do not raise it to make the overrun count go down — the overruns are the model reaching for substance.
- **`--check` now fails on a missing or title-echoing description.** A missing description is a violated schema contract, not a soft quality miss, so it is not allowed to slide.
- **The Cost & Funding bleed is reproducing in the fixtures** as intended: item 10.3.1 (the 210 Pacific Avenue shelter) carries `$187K for the Shaw Centre score clock` and `$187K for complete the project`, both belonging to 10.3.2. That is R10, still open in U5.

### U4 — Consent Item coverage

- Name the concept in `app/agenda_items.py`: `is_consent_item(item)` (inherited timestamp), `is_section_header(item)`.
- `is_eligible_for_summary` admits Consent Items, still rejects Section Headers (R5, R6).
- Consent branch of the prompt: no transcript, states the item passed on consent without individual debate, restricted `allowed_cats`.
- Skip cleanup entirely for Consent Items — no transcript to clean.
- **Exit check:** 39/73 items on `b71ff753` produce an ItemSummary, up from 12.

### U5 — Prompt and extractor quality

This is the unit we actually collaborate on, using U1–U2. Expect several passes.

- Rewrite the chip prompt around the new aggregate: description first, chips as supporting specifics.
- Fix `Cost & Funding` cross-item bleed (R10) — the `$187K` Shaw Centre score clock currently lands on the 210 Pacific Avenue shelter item.
- Re-examine `usefulness` gating now that description is mandatory: soft chips no longer have to carry the "what is this" burden, so the bar for a chip can rise.
- **Exit check:** soft-chip coverage materially above the current 118-per-655-meetings floor, reviewed by diff.

### U6 — LLM-as-judge gate

Deferred until U5 stabilizes, per the eval decision.

- Rubric: faithfulness (judge must quote the supporting source span), specificity, non-redundancy with the title.
- Gates CI on mean score.

### U7 — UI + backfill

- Render `description` as the lede; mark Legacy ItemSummaries as lower-confidence (R11).
- Re-summarize the 2024-2028 term forward, parallelized (R12).
- **Not** in scope: splitting Outcome into a headline presentation (deferred in ADR `0003`).

---

## Open Questions

- **Cleanup's remaining value is unmeasured.** ADR `0004` keeps it only for proper-noun correction. Once U1 makes A/B cheap, run the fixtures with cleanup off and check whether chip quality actually drops. If it doesn't, delete the pass and the cache with it.
- **The name roster is stale for the archive.** `_SASKATOON_NAMES` covers the 2020-2024 and 2024-2028 councils. Meetings older than 2020 would have their proper nouns "corrected" toward the wrong people — an argument for never backfilling the deep archive.
- **`MeetingTopics` is untouched here** and is still uncached. It shares the summarizer's prompt problems but has its own scope.
