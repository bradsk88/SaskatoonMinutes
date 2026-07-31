# A row earns a card slot by having a recorded outcome, because five slots were being filled whether or not there was anything to fill them with

`0008` took the agenda's structure off the cards. What was left in the tail was standing business — "Report of the Chair", "Rise and Report", "Work Plan Consideration", "Committee or Resource Member Update", "Chief's Report". Real agenda items with real text, and nothing decided in any of them. On advisory-committee meetings, where the agenda genuinely holds little else, that was most of the card.

The card no longer takes the top five unconditionally. A row earns a slot by carrying a recorded outcome: anything except `Discussed`, which is precisely what `format_outcome` returns when there is no vote **and** no recommendation. Approved, Defeated, Deferred, Recommended, Received as information all qualify — "received as information" is council declining to decide, but it is a thing council resolved, and it is one of the most common labels in the archive.

Three is the floor. Below three rows a card stops reading as a summary of a meeting, so the best-ranked of the rest fill in — which is why topics now carry the server's `rank` alongside their agenda order.

Measured over the 310 built meetings: rows with no recorded outcome fell from roughly a third of card slots to **0.7% — 9 rows in 1,255**. Card sizes: 205 meetings show five rows, 31 show four, 28 show three, 12 show one or two, and 34 show none because their agenda came back empty (a pre-existing fetch problem, not this rule).

## Considered options

**Pad every card to five.** Rejected — padding is exactly the noise being cut.

**Show only what clears the bar, however few.** Rejected as too thin: a one-row card looks like a failed summary rather than a short meeting, and a reader cannot tell the two apart.

**Require a written Description instead of an outcome.** Rejected. That is a fact about our coverage, not about the meeting — a body we have not summarized yet would go blank. It stays a ranking weight (`0009`), not a gate.

**Score standing business down by title.** Rejected for the same reason as in `0008`: a hand-kept phrase list, per-body wording, and it fails silently when the wording changes. "Has an outcome" is a property of the record.

## Consequences

Selection now lives in two places — the server ranks and the card chooses. `rank` is the seam, and it has to keep travelling in the topics payload; a card without it falls back to agenda order and pads with the earliest rows instead of the best ones.

Some genuinely interesting items are labelled `Discussed` because eSCRIBE recorded no motion for them. Those can now be pushed off a card by duller items that happen to carry a recommendation. The floor of three limits the damage, and the fix is better outcome extraction rather than a softer gate.
