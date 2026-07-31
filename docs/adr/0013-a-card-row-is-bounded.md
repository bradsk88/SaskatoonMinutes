# A card row is two lines of title and three of summary, because an unbounded card was 36 lines long and a phone holds about 40

Shape C asked for five topics, "each one plain sentence". Measured over the 276 built cards that have topics, at a 390px width, the average card draws **36 text lines** and the worst draws **54** — before badges, rules and padding. A 390×844 screen holds roughly 40 lines of body text in total. One meeting was one screen, and the reader who came to choose between meetings could not see two of them at once.

Neither half of the row was near one sentence:

- **Titles.** 35% of the 1,255 rows wrap past two lines, 10% past three, and two reach six — "Proposed Amendments to City Council Policy C03-036, Multi-Year Business Plan and Budget Policy" is a filing name, not a headline.
- **Summaries.** 85% run past two lines and 48% past four; the longest is eleven. The written Description was sized for the detail page and the card took it whole.

Both are now clamped in CSS: the title to two lines, the summary to three, each ending in an ellipsis. Mean lines per card fall to **20 and the worst case is bounded at 25** — a card can no longer be longer than five rows of five lines, whatever eSCRIBE writes.

## Considered options

**Clip by character count when the payload is built.** Rejected. Characters are not lines: 120 characters is four lines on a phone and one and a half on a desktop, so a server-side clip either over-trims the wide viewport or under-trims the narrow one. The clamp is a property of the rendered width, so it belongs in CSS.

**Ask the summarizer for shorter Descriptions.** Rejected. The Description is also the detail page's account of the item, and that page exists to prove — shortening it there to fit a card would cost the reader the thing the card is advertising. One text, two presentations.

**Clamp only below 640px.** Rejected. The desktop card was 36 lines too. "The index skims, the details page proves" is not a claim about screen width, and two rules would drift.

**Show fewer rows instead of shorter rows.** Rejected. `0012` already decided which rows earn a slot; cutting five to three would drop business a reader might be looking for in order to keep bureaucratic titles at full length. The row count is a question about substance, the row height is a question about presentation, and this is the second one.

## Consequences

A clipped title can end mid-phrase, and a clipped summary can end before the number in the sentence. That is a real cost: a reader who stops at the card sometimes gets less than the whole claim. It is bounded by the fact that the row is a link to the item, and it is the trade the card was thinned for in the first place — three quarters of summaries and a third of titles are now clipped for someone.

An ellipsis is not a promise that the rest is short. If a later change makes the detail page harder to reach from a row, this decision gets worse with it.

`-webkit-line-clamp` is prefixed and universally supported; the unprefixed `line-clamp` is set alongside it. A browser that supports neither shows the old full-height row, which is the pre-existing behaviour rather than a broken one.
