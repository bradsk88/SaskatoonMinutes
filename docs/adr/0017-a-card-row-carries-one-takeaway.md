# A card row carries one takeaway in prose, because the card is about to become an RSS entry and a badge cannot travel

The index is going out as a feed. A feed entry arrives with no tabs, no filter, no hover and no second page — it answers "is this worth my time?" on its own or it fails. That raises the bar on the card, because the card *is* the entry.

The material was already there and being thrown away. Every summarized item carries chips, and the interpretive categories are only filled when the model found something to say. Shape C moved them off the card as **badges**, which was right — a badge whose claim appears on hover says nothing on a phone and nothing at all in a feed reader. The mistake was losing the sentence with the badge.

A row now shows one chip as a line of prose:

> **Debate:** Previous homelessness plans have not produced measurable results despite significant funding.

Every row that has one, not just the top row. That was the call: more signal per card is worth the height.

## Choosing which chip

Ranked most telling first — Dissenting View, Debate Highlight, Unanswered Question, Staff vs. Council, then Promise Made, Precedent Set, Legal Risk Flagged, the impact categories, Cost & Funding, Public Sentiment, and Who's Affected last. Who's Affected is the most common category (164 of 978 chips) and the least surprising.

The order was corrected in review against a real card. The Homelessness Action Plan item had three chips, and the first ordering led with *Staff vs. Council: "Administration clarified City Council is not endorsing rent control"* — a procedural clarification — over *Debate Highlight: "Previous homelessness plans have not produced measurable results despite significant funding."* Commentary on what was decided beats commentary on what was clarified.

## Height, and the label

`0013` had just capped a row at two lines of title and three of summary, after measuring cards at 36 text lines. A takeaway line would give that straight back, so **where a row has a takeaway, the description drops to two lines and the takeaway takes three.** The row is the same height it was. A hook cut mid-sentence is worse than a description cut mid-sentence: the description is a summary of something the reader can go read, while the hook is the reason to go.

The label is shortened — "Debate", not "Debate Highlight"; "Unanswered", not "Unanswered Question". At card width every character of label costs a character of takeaway: modelled over all 978 chips at a ~40-character line, full category names pushed **36** past the three-line clamp and short ones **9**.

## Considered options

**Top row only.** Rejected in review. One hook per card is tidier and thinner, but a reader scanning for a reason to open is served by every reason there is.

**Takeaway instead of the Description.** Rejected: the Description says what happened, the chip says why it was interesting. Trading the first for the second leaves a reader hooked and uninformed.

**Keep them as badges with the sentence in a tooltip.** That is what the card did before shape C, and it is unreachable on a phone and absent from a feed.

## Consequences

Only 22% of card rows have a chip, because chips only exist where a summarize run reached the item. The rest of the rows are unchanged. That is a coverage problem, not a rendering one, and it is the same ceiling `0009` ran into.

The feed (`TODO.md` item 12) now has its unit: an entry needs a Description or a chip to qualify, and this is the line that carries the chip.
