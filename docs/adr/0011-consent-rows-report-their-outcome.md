# A consent row reports its outcome, because "Not discussed" was hiding decisions council made

With one badge per row, a consent item's badge read `Not discussed` and nothing else. Across the archive's 269 consent topic slots the badges being suppressed were:

- **Approved — 103**
- Received as information — 89
- Recommended / Recommended to Council — 39
- Discussed — 36
- **Defeated — 1**

So a card could say council did not discuss a thing while staying silent on the fact that council approved it, or defeated it. That is an outcome misreported by omission, which this project does not do.

The row now leads with the outcome badge and follows it with a quiet "in consent, not debated". Both facts, outcome first.

The exception is `Discussed`. That is what `format_outcome` falls back to when there is no vote and no recommendation, and on a consent row it is not merely uninformative but false — the item was passed in a block precisely without being discussed. Those rows keep `Not discussed` alone.

## Considered options

**Two badges.** Rejected. Two coloured pills on a 390px row wrap the title and read as two competing claims of the same kind. The consent fact is context for the outcome, not a second outcome, and it is set as muted text to say so.

**"Approved · not discussed" inside one badge.** Rejected — this was the other option on the table. A single badge would carry a compound sentence at badge size and would have to be truncated or wrapped on a narrow card, and the outcome colour would then be colouring the consent clause too.

**Drop consent rows from cards entirely.** Rejected outright. Consent is where the least-scrutinised business passes; a summary product that quietly omits it is worse than one that shows it flatly.

## Consequences

Consent rows are slightly taller. On the June 24 council card, where four of five topics are consent items, that cost is real — and that card is the argument for the ranking question still open, not against reporting the outcome.

Consent items still cannot be linked individually (`TODO.md` item 7), so the row's outcome is now visible but the item behind it is still only reachable by opening the meeting.
