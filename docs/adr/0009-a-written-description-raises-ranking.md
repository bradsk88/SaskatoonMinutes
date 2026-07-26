# An item with a written Description outranks one without, because the Description is what the card was rebuilt to show

Shape C gives each topic one plain sentence. That sentence is the item's written Description when it has one. When it does not, the card falls back to raw agenda text clipped at 120 characters and prints "Older summary — no plain-language description available" under it.

Only **20% of card topic slots across the archive carry a written Description**. The rest are the fallback: bureaucratic text, cut mid-thought, under an apology. Ranking had no term for this at all — an item the summarizer had actually written about scored no higher than one it had not.

A Description is now worth `0.25`, between a recommendation (`0.2`) and a contested vote (`0.5`).

## Considered options

**Require a Description to appear on a card.** Rejected. It would empty the cards for every meeting summarized before Descriptions existed, and for any meeting a summarize run has not reached. A clipped agenda line that says so is worse than a Description and better than nothing.

**Weight it above a contested vote.** Rejected. A contested vote is the thing a resident is least likely to hear about elsewhere. Being well-described is a property of our coverage; being contested is a property of the meeting, and the meeting wins.

**Suppress the fallback line entirely and show the title alone.** Rejected for now — it is a rendering decision, not a ranking one, and the marked fallback is already honest about what it is.

## Consequences

Cards drift toward the items the summarizer has processed. This is a feedback loop worth naming: if a summarize run skips a body, that body's cards get quieter and no one is told. The existing rule that a run which cannot reach Gemini fails rather than shipping empty is what keeps it from being silent.

The mix barely moved in this corpus — most items in it have no Description at all, so there was little to promote. The effect grows as coverage grows.
