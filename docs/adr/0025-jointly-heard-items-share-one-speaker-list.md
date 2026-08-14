---
status: accepted
---

# Jointly-heard items share one speaker list

A committee can take two agenda items as one discussion. G&P 2026-08-12 heard 6.3.2 (Public Art Policy) with 7.1 (the clause-by-clause motion) as a single conversation: one speech answered both, so every speaker was stamped on both cards, and the eSCRIBE bookmark windows sprawled over the same three and a half hours (7:08–3:36:29 against 1:10:51–3:08:50). Same name, same stamp, two cards — it read as a leak, not as one discussion.

Detection is automatic, no manual override files:

- The two windows overlap by at least half the shorter window.
- Neither section number nests under the other ("6." contains "6.1" legitimately — hierarchy, not a joint hearing).
- Both items have speaker rosters, and the rosters share a name.

Bookmark overlap alone fires on 228 of 255 meetings of sloppy bookmarks; the shared witness — the same person on both rosters, which happens precisely when one speech answered both items — is what proves one discussion. It also stops union-find from chaining a meeting's worth of laggy bookmarks into one blob (Council 2026-06-24 had four brushing windows with no shared speaker: rejected).

Presentation: the item whose window starts first is the **primary** — the discussion began there, so the merged roster lives on its card, one row per person in speaking order with their single stamp, titled "heard together with item 7.1". The partner card points at it ("Heard together with item 6.3.2 — speakers are listed there") instead of repeating the list. Per-item rosters in the data are untouched; the merge happens at render time from the `heard_with` annotation that `mark_jointly_heard` stamps at build time.

A side effect worth wanting: the shared list draws from the group, so a speaker stamped outside their own item's lagging bookmark — Em Ironstar spoke at 42:20, four minutes before 7.1's bookmark — still lands on the card where the discussion is listed.
