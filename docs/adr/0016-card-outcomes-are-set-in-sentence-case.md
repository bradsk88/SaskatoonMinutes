# A card's outcome badge is set in sentence case, because "RECEIVED AS INFORMATION" was the loudest thing on the row

`0010` took shouting out of the card's data — meeting names and item titles arriving from eSCRIBE in full caps are titleized at the render seam. The badge was still shouting by stylesheet: `.badge` sets `text-transform: uppercase`, and the outcome is the only badge a card carries.

The labels are long. Across the 1,255 rows drawn, the outcomes are `Approved` (481), `Received as information` (390), `Recommended to Council` (197) and `Recommended` (92) — so **over half of card rows carry a label of 22 characters or more**, in caps, with letter-spacing, above a muted summary. On a committee card where four of five rows read `RECOMMENDED TO COUNCIL`, the repeated pill was the strongest mark on the card, and it is the least surprising fact on it: a committee recommends to council.

`.topics-table .topic-badge` now sets `text-transform: none` and normal letter-spacing. Same words, same colours, at the volume of the sentence under them.

## Considered options

**Shorten the labels — "Received as information" to "Noted".** Rejected. `format_outcome` produces the words the record uses, and "noted" is not what the minutes say council resolved. Shortening an outcome to fit a badge is misreporting it to save pixels, which is the thing this project does not do.

**Take uppercase off `.badge` globally.** Rejected here, not on the merits. The detail page uses the same class for chip badges among body text, where a short shouted label is doing its normal job of standing apart. That page has not been assessed on a phone yet; when it is, this decision is a candidate to widen.

**Drop the badge on rows where every row shares an outcome.** Rejected. A reader scanning down a card should not have to notice an absence. Repetition that is true is not noise in the same way that shouting is.

## Consequences

The index and the detail page now style the same badge class differently — a card outcome is sentence case, a detail chip is caps. The rule is scoped by `.topics-table` so it cannot leak, but two treatments of one component is a thing to reconcile when the detail page gets the same pass.

Outcome colour is now the only thing separating the badge from the text beside it. That works because outcomes are the coloured vocabulary (`approved`, `defeated`, `deferred`, `contested`, neutral), but a future badge in neutral grey will be quieter than it used to be.
