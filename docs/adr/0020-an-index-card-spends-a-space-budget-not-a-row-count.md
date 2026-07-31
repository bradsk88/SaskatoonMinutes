# An index card spends a space budget, not a row count

The index card used to show a fixed five council topics plus up to three speaker rows. The July 29, 2026 regular meeting exposed the flaw: roughly ten items of clear public interest (transit bylaw, homelessness plan, BRT relocation, $31.7M borrowing, fire stations, the $281.6M surplus) competed for five slots, and significant decisions vanished behind "15 other items."

The replacement bounds **vertical space, not rows**: a card has a budget of 15 units (roughly one mobile screen), where a detailed row costs 3, a speaker row 2, a title-only row 1, and an org digest row 1. Ranked items past the detailed five now earn a title-only row — title plus outcome badge — so a heavy meeting names the rest of what council did instead of burying it whole.

When a card exceeds its budget it spends down in a fixed order:

1. Speaker rows collapse into a **digest** — one slim row per represented organization, plus a residents roll-up.
2. Title-only rows drop from the bottom.
3. Detailed rows demote to title-only.

Two rules were non-negotiable and shape the design:

- **Council keeps priority over speaker detail.** Speaker rows are the *first* saving, not the last: knowing the 6th-ranked item's title and outcome outranks hearing what one delegate argued.
- **The digest is never cut, and it lists every organization.** Which orgs had a voice is the thing a resident scans for; showing a representative sample would hide attendance, which is the failure the digest exists to prevent. This is why the topics payload now carries the full speaker roster (`speaker_roster`), not just the ranked few the speaker rows need.

## Considered options

**A fixed higher row cap (e.g., 10 rows).** Rejected: ten detailed rows is a long scroll on a phone — the problem it solves on heavy meetings it recreates as a reading problem. Demotion under pressure is what keeps the card digestible.

**Floors** (the #1 item always stays detailed; speaker detail always survives). Rejected: any floor can be blown past by a sufficiently packed meeting, and partial floors produce unpredictable layouts. The only protected element is the org digest, and it is protected absolutely.

**A bare "N guest speakers" count as the digest.** Rejected: it throws away *who* came — the one fact the speaker feature exists to report. That badge survives only as the fallback for meetings whose roster has no organization data at all.
