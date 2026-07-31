# A card topic slot has to hold business, because a quarter of them were holding headings and breaks

The card shows five topics. Across the 310 built meetings, **at least 23% of those slots were Section Headers** — "Decision Reports", "Approval Reports", "Information Reports", "Communications", "Referrals from Council or Committee". The Governance and Priorities card on the first screen opened with three of them in a row, each with an outcome badge reading "Discussed", and no summary line under any of them. Nine more slots held "Recess".

`extract_meeting_topics` filtered Procedural Items and nothing else. `count_agenda_items` already refused to count Section Headers and recesses, so a card could spend a slot on a row and then advertise "43 other items" that did not include it — the two numbers on the same card were counting different things.

Both filters now apply to ranking as well: `is_section_header` and `is_recess`.

## Considered options

**Score them down instead of excluding them.** Rejected. There is no meeting where "Decision Reports" is the fifth-most-worth-reading row; it is not a row at all. A weight leaves the failure in place for any meeting with few substantive items — which is exactly the advisory-committee meetings where the card has the least to say already.

**Add the header titles to `PROCEDURAL_KEYWORDS`.** Rejected. It is a hand-maintained list of phrases, and eSCRIBE writes headings differently across bodies. `is_section_header` decides structurally — no recommendation, no content, and no time span of its own — which is the same test `count_agenda_items` trusts.

## Consequences

Cards for thin agendas now show fewer than five topics rather than padding with headings. That is the honest result: a meeting with three items of business has three.

What is left in the low-value tail is standing business, not structure — "Report of the Chair", "Rise and Report", "Work Plan Consideration", "Chief's Report". These are real agenda items with real text, so they are a ranking question (`0009`) and not this one.
