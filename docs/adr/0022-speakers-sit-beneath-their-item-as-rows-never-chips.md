---
status: accepted
---

# Speakers sit beneath their item, as rows, never chips

Supersedes the speaker-related parts of ADR 0020 (the budget itself stands): the spend-down order, the 2-unit speaker row, and the digest-of-everyone placement.

July 29, 2026 again: the card filled its 15 units with five detailed items, every speaker row collapsed into the digest, and "Guest speakers" rendered at the bottom of the card — the opposite of where a reader's question ("who had a voice on *this*?") arises.

The new rules:

- **Speakers render beneath the item they answered**, in the title-only row's shape (one line: name, organization, stance badge), coloured by the organization's own palette. Brad's standing preference: **speakers are rows, never chips** — a chip is a label on a thing, and a speaker is a thing. The detail page's speaker cards predate this rule and migrate when next touched.
- **Up to three speaker rows per item** at 1 unit each. Past the cap, a free "+N more speakers" row points at the detail page, and the capped-away organizations still appear in the digest. Nothing is hidden; it is *placed*.
- **Engagement is spent last.** The spend-down order is now: demote the *least-attended* detailed items to title-only (their speakers return to the digest), then trim speaker rows off the *most-attended* item one at a time — but only while a trim actually saves space, since a speaker whose organization rejoins the never-cut digest saves nothing and so keeps their row — then drop title-only rows from the bottom, and only then demote the most-attended item itself. Top-up restores trimmed speakers before naming another item. A meeting where one item drew a crowd keeps that item detailed with its speakers, paid for by the other items' detail.
- **The digest lists what inline rows did not**: organizations with no speaker row on the card, plus the residents roll-up. The union of inline rows and digest is always the full roster — the never-cut guarantee from ADR 0020 now holds of the union rather than of the digest alone. A card with no speakers shown has the old full digest; a card showing everyone's org has none.

The "first speaker line is free" idea from the design discussion died when chips became rows: a free line made sense as a traded summary bullet, but a row is honest at 1 unit, the same as a title-only row.

The payload supports the cap directly: each item carries up to three of its speakers plus the item's full eligible-speaker count (`item_speaker_count`), so the card can say "+N more" without being handed everyone.
