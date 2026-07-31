# A card reads names, not filing records, because half of them arrived in full caps with the clerk's reference codes attached

Two upstream habits landed on the card unfiltered.

**Full caps.** eSCRIBE writes meeting types and many agenda item titles in capitals. The detail page already titleizes the body name (`titleize`, added when body names were fixed), but the index took `Meeting.title` straight from the list API, so the same meeting read "GOVERNANCE AND PRIORITIES COMMITTEE" on the card and "Governance and Priorities Committee" on the page it opened. Within one card it was worse: "Rise and Report" sat under a normal-case title and above a shouted one, because eSCRIBE is inconsistent item to item.

Titleizing now happens where the shout enters — `EscribeMeetingSource.list_past` for the meeting name, `_format_topic` for the topic title. `titleize` only touches fully-uppercase text, so anything written mixed-case deliberately is left alone.

**Reference codes.** `plainify` strips trailing codes like `[CC2025-0402]`, but its character class had no period in it, so the clerk's own alternative spelling of the same code survived: "Work Plan and Referrals to Standing Policy [CK. 225-18]", "Report of the Chair [File No. CK 225-83]". Roughly 40 slots across the archive carried one. The class now includes the period.

## Considered options

**Titleize in `plainify` so both pages get it.** Rejected. `plainify` also feeds `item_categorizer`, which matches on the text; changing what it returns changes categorization for a cosmetic reason. Casing is applied at the two render seams instead.

**Uppercase-to-title in CSS with `text-transform`.** Rejected. It cannot tell a shouted name from a deliberate one, so it would lower-case genuine acronyms on every card, and the underlying data would still be shouted for anything that reads it — the API, a future search index.

**Keep the file number as a citation.** Rejected. The item is one tap away on the detail page, where the reference belongs. On a 390px card the code costs a line of the title it is attached to.

## Consequences

`titleize` loses acronyms inside a shouted name — "SPC" becomes "Spc" — which is its documented limitation, not a new one. It applies to more strings now, so the cost of that limitation is higher; a word list is the fix if it starts showing.
