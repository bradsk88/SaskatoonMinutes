# On a phone the page says what it is once, because it was saying it three times before any meeting appeared

The index opened with four blocks of chrome that all said the same thing:

- the header tagline, "Plain-language summaries of City Council meetings", wrapping to two lines
- the h1, "Saskatoon Council & Committee Meetings"
- the hero subtitle, "Browse recent meetings and see what was discussed and decided"
- the h2, "Recent Meetings"

Together with the tab row and the filter bar they filled 390×844 entirely. Below 640px the page now keeps the h1 and drops the rest: the tagline and the hero subtitle are hidden, and the "Recent Meetings" heading is visually hidden — it stays in the document for a screen reader, where a heading costs nothing and helps.

With `0006`, the first screen went from zero visible agenda items to a meeting's date, body, and three topics with their written summaries.

## Considered options

**Shrink the type instead of removing lines.** Rejected. It buys a fraction of what removal buys and makes the page harder to read to save space for text that repeats.

**Delete the hero on the desktop page too.** Rejected. There is room there, and an h1 with a sentence under it is the ordinary shape of a landing page. The claim here is about a 390px viewport, not about the copy being wrong.

**Hide the h2 outright rather than visually.** Rejected. A list with no heading in the accessibility tree is worse for a screen-reader user, who is not paying the 45px.

## Consequences

Two viewports now show different copy. The mobile page is the reduced one, so anything added to the hero later has to justify itself against the first screen again.
