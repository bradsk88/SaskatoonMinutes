# Raw agenda text is labelled, not apologised for, because the apology was on seven rows in ten and cost two lines each

When a card row has no written Description it falls back to raw agenda text clipped at 120 characters, and it said so:

> *City Clerk Tittemore presented the report.*
> Older summary — no plain-language description available.

**889 of the 1,255 rows drawn across the archive — 71% — are on that path.** At 390px the caveat wraps to two lines, so it was spending roughly 1,778 lines telling readers, over and over, that our coverage is incomplete. On a three-row card it was a third of the text.

The row now reads `**From the agenda:** City Clerk Tittemore presented the report.` — a two-word source label in front of the text, inside the same block, sharing the three-line clamp from `0013`. The italic, muted styling that already set the fallback apart is unchanged.

## Considered options

**Drop the mark entirely and let the italics carry it.** Rejected. Italics say "different", not "these are the clerk's words and not ours". Provenance is the one thing this fallback has to state, and stating it is what makes clipped bureaucratic text acceptable on the card at all.

**Show the title and outcome with no line at all.** Rejected. It is the cheapest option and it makes the majority of rows say nothing about what the item is. The clipped line is poor, but a reader can tell a rezoning from an appointment with it.

**Keep the sentence and let `0013` clamp it.** Rejected. The clamp would then spend a third of every fallback row's budget on our apology instead of on the item, which is the same defect measured in lines.

**Say it once per card instead of once per row.** Rejected — considered seriously. It reads well when every row is a fallback and misleads when only one is, and which rows are fallbacks varies inside a single card. A per-row fact belongs on the row.

## Consequences

The page no longer says *why* the fallback is there. "From the agenda" describes what the text is; it does not tell a reader that a plain-language summary was expected and is missing. That is deliberate — a reader is owed the provenance, not our backlog — but it means the coverage gap is now visible only as a difference in tone between rows.

The label is a `<span>` inside the summary block, so it is subject to the clamp. On a row whose clipped agenda text is itself long, the visible text is now the label plus a little less of the sentence.
