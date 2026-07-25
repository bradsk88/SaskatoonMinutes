# `ItemSummary` is an aggregate with a mandatory Description, not a flat chip list

An audit of the whole cached corpus on 2026-07-25 found 655 meetings and 16,210 agenda items, of which only 21% carried any chip at all. Of the 7,309 chips that existed, 2,567 were "In Plain Terms" — overwhelmingly the `_extract_in_plain_terms` fallback replaying the agenda item's own title — while all eleven interpretive soft categories combined accounted for 118 chips across 655 meetings. The plain-language explanation was a *category the model could decline*, and when it declined, a metadata fallback echoed the title. So `ItemSummary` becomes a real aggregate: a mandatory `description` plus `chips: list[Chip]`, with `description` a required field of the Gemini response schema and "In Plain Terms" retired as a category.

## Considered options

**Keep the flat chip list and fix the echo through prompt wording** — tell the model more firmly that "In Plain Terms" is not optional, and delete the metadata fallback. Rejected: this is the same class of guarantee that already failed. Prompt wording degrades silently under model updates and prompt edits, and the failure mode is invisible (a declined category looks identical to a category that genuinely didn't apply). A required schema field cannot be declined, and its absence is a parse error we can see.

**Split Outcome into a headline presentation at the same time** — CONTEXT.md had long promised this. Deferred: it bundles a UI redesign into a summary-quality fix, and the format change stands on its own.

## Consequences

The on-disk contract changes. Existing summaries are a bare `list[{category, text}]` with no description, which makes them structurally distinguishable on load — so this needs **no migration path**. Given that the old cache is 21% populated with title echoes, there is nothing worth migrating: meetings in the current council term are re-summarized, and older meetings keep their **Legacy ItemSummary** until backfilled, marked in the UI as lower-confidence rather than presented as if they meet the current bar.
