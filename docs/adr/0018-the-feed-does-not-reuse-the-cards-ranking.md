# The feed picks its entries by discussion time, not by the card's ranking, because the card's ranking saturates at twenty minutes and misses the biggest debates of the year

`extract_meeting_topics` already answers something that looks like the feed's question: given a meeting, which items mattered. It is what the index card is drawn from, it is already computed in `build_site.py`, and reusing it would mean the feed and the card could never disagree about a meeting. Duplicating a ranking is the kind of thing this codebase has been bitten by before — "43 agenda items" printed above 73 rendered cards came from two functions counting the same meeting.

It was measured before being reused. Same substance gate on both sides — an item qualifies by carrying a Description or an interpretive chip — top 8 each, over three real meetings:

- **2025-11-25, budget.** 74 items, 31 qualify. The card's ranking misses Capital Options (155.5 minutes), Summary of Funding for Housing and Homelessness Initiatives (83.6) and Arts, Culture and Events Venues (81.3). It includes Land Development (3.5) and Community Support (10.2).
- **2025-12-03.** Misses Downtown Event and Entertainment District (100.3) and Response to Motions – Proposed New Development Incentives Policy (71.1). Includes a right-of-way dedication (1.4) and two items with no recorded discussion at all.
- **2025-12-17.** 6 of 8 agree. Misses an 11.0-minute item.

The cause is one line, `summarizer.py:96`:

```
duration_score = 0.25 * min(1.0, _discussion_minutes(item) / 20.0)
```

Duration saturates at twenty minutes. A 155-minute debate and a 21-minute one score identically, so above that threshold the ordering is decided by whether a dollar sign appears in the text and how many dots are in the section number. Those are reasonable proxies for ranking a card, where every row is one tap from the full page and being wrong costs a reader a scroll. They are not reasons to tell four hundred subscribers that a right-of-way dedication was one of the eight things that happened at a council meeting.

The two surfaces answer different questions. A card row is chosen for whether it is worth *clicking*; a feed entry is chosen for whether it is worth *reading in full*, because a feed entry arrives with no tabs, no filter and no second page — the same premise `0017` was written on.

So the feed applies the substance gate, ranks what survives by `_discussion_minutes`, and caps at 8 per meeting. It reuses the *helper*, not the score: `_discussion_minutes` already returns zero for a Consent Item's inherited span and for a recess, which is what keeps `TODO.md` item 13's broken spans — twenty-two over three hours, four running about 6.9 days — out of the ordering.

This overrides the rule already written down for the feed in `TODO.md` item 12, which said duration ranks and substance gates. The gate survives unchanged. What changed is that the cap is 8 per meeting rather than open-ended, and that the ranking runs on the guarded helper rather than on raw spans.

## Considered options

**Reuse `extract_meeting_topics`, then apply the substance gate to what it returns.** Rejected on the measurements above. It also returns Speaker rows mixed in with agenda items — "Jasmine Carlton", "Sherry Tarasoff" — which carry no Description and so fall to the gate, leaving the feed silently short of its cap: 6 entries where 8 were asked for.

**Fix the saturation and then reuse it.** The better long-term answer and it may still happen — the finding is `TODO.md` item 15, and it is a live defect on the index card today. Rejected as a prerequisite: it changes what every card on the site shows, which is a bigger decision than the feed, and `TODO.md` item 13's dirty spans should be settled first.

## Consequences

The two surfaces can now disagree about a meeting, and there is no test that would tell you. A reader who arrives from a feed entry may not find that item among the eight rows on the index card. That is accepted: the card links to the same detail page, and the entry links to the item's own anchor, so neither reader is stranded.

Coverage, not ranking, is what binds today. Across the archive roughly 400 of 6,952 items carry a Description or an interpretive chip — about 1.5 per meeting — so the cap of 8 almost never applies. It becomes the real selector only as summarize coverage grows, which is the same ceiling `0009` and `0017` both ran into.
