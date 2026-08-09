# The chip taxonomy is audited against what it publishes, because a category nothing emits is tag noise

The feed tags entries with chip categories (`0022`-era feeds), which made the taxonomy a subscriber-facing filter vocabulary and raised the stakes on every label in it. An audit of the six months to July 2026 (68 meetings, 1,735 items, 465 with chips) measured each category against what it actually produced.

## Deleted: six unreachable categories

`Declared Conflict`, `Delegation`, `Next Step`, `Related Item`, `Deferred From` and `Data Cited` were produced by transcript-regex extractors that only ran when Gemini was *disabled* — which production never is — and were not in the LLM's remit either. Zero firings in six months; the plan doc had already flagged the decision as open.

The options were delete, move to the LLM, or fix the eSCRIBE bookmark lag that makes an item's transcript slice contain a neighbouring item's words. Moving to the LLM was rejected per-category rather than wholesale: none of the six cleared the bar the kept categories clear. `Delegation` duplicates the speakers block; `Next Step` is `Promise Made`'s weaker twin; `Related Item`, `Deferred From` and `Data Cited` are navigation aids, not facts a subscriber filters on; `Declared Conflict` is real but rare and self-announcing in the official record. Fixing the bookmark lag is worth doing for its own sake and would not by itself make these categories worth a tag.

Stale chips in the cached archive keep their labels. Rendering treats an unknown category as the `context` group and the takeaway falls back to the first chip, so nothing breaks and nothing is migrated.

## Fixed: Cost & Funding

The audit's worst finding: 9 chips against 114 items whose Description carried a dollar figure. The deterministic extractor reads only official text and demands a figure plus purpose words within eighty characters, so money raised in debate — most of what a "money" filter subscriber wants — never got the tag.

Cost & Funding is now in both passes. The deterministic chip still cites only official text, because a figure there is auditable. When it finds nothing, the category stays in the LLM's prompt and the model may emit a figure it heard in debate; when it fires, the category is excluded from the prompt by the existing covered-category mechanism, so the two passes never duplicate. The prompt definition carries the bookmark-lag warning: a transcript figure whose connection to the item is uncertain must be omitted.

## Tightened: Precedent Set

37 chips in six months, roughly a sixth of them not precedents — one-off facts ("City now owns school land"), routine firsts ("first year implementing priority-based budgeting"), a funding condition. The definition now requires the chip to name the pattern future decisions will be judged against, and explicitly excludes things that merely happened for the first time.

## Kept: the rare ones

`Dissenting View` (12) and `Legal Risk Flagged` (22) fire rarely because the events are rare. A filter subscriber wants exactly these; rarity is the value. `Amendment Made` fired zero times but is deterministic, cheap, and its absence is plausibly genuine — amendments surface as their own agenda rows. Kept under observation.

## Consequences

The taxonomy is 16 categories, down from 22. `CARD_CHIP_CATEGORIES` is simply `SEMANTIC_CATEGORIES` now that Cost & Funding is semantic too — one list fewer to keep in sync.

The first audit was manual: cache files, a date index scraped from built pages, ad-hoc counters. It is now codified as `scripts/audit_categories.py`, which reproduces the frequency table, the zero-firing and tag-noise flags, and the money check against any recent window. Re-run it after a few months of the new Cost & Funding definition, and before any future taxonomy change — the comment above `CATEGORIES` points there.
