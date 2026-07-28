# A feed entry is a day the city sat, not a meeting and not an item, because granularity is the one thing a feed cannot change later

The index is going out as a feed (`TODO.md` item 12, `0017`). The unit had three candidates: an agenda item, a meeting, or a calendar day.

Granularity is close to irreversible. A subscriber's reader remembers entries by their `id`. Change what an entry *is* and every `id` changes, so every entry in the retention window reappears as unread — for everyone, at once, with no way to say sorry. Nothing else about the feed carries that cost: the ranking can be retuned, the entry body rewritten, the retention raised, and a subscriber sees only that the next entry is better.

So the default feed, `/feed.xml`, is one entry per calendar day the city sat, with the bodies that sat that day as headings inside it. Committees stack in Saskatoon — Planning & Development and Transportation often sit the same day, and Council sometimes sits alongside a Public Hearing. Per-meeting granularity means those days arrive as three entries and the daily framing quietly stops being true; per-day means a subscriber never gets two entries for one day, which is the promise the feed is making.

A second feed, `/feed-items.xml`, carries one entry per qualifying item, for a reader who wants each decision as its own thing to star or share. The two feeds differ **only** in granularity. They never differ in what qualifies — same substance gate, same ranking, same cap (`0018`). Two feeds with two thresholds would have made one a subset of the other, so anyone subscribed to both would see the important items twice, and a second cutoff would have to be defended on top of the first.

## Publishing late rather than thin

A day is published once it is **Settled**: every meeting on it has cached summaries, or seven days have passed.

The pipeline is why. A meeting sits in the evening; transcription runs at 05:30 UTC; summarization runs after that. But the deploy fires every four hours regardless, so a build on the night of a meeting sees the agenda with zero summaries. Publishing on first qualification would send a thin entry, and a reader who saw the thin version would never be shown the full one — most readers render a changed entry once and never resurface it. That is the same failure as shipping a summarize run that could not reach Gemini: an entry that looks like coverage and is not.

The seven days are what stops a meeting with no video from holding a day hostage forever.

## Stateless

The build wipes `_site/` and regenerates from live eSCRIBE plus the cached summaries. The feed is rebuilt with it, six times a day, and it keeps no record of what it published. Entry `id` and `<updated>` come from the meeting date, never from build time, so a rebuild produces a byte-identical entry whenever the data has not moved and no subscriber sees anything.

The gap this leaves is narrow: a day published thin under the seven-day fallback that gains summaries on day ten changes under readers who already have it. Accepted — it is rare, and the drift is an improvement rather than a regression. A `feed` branch alongside `summaries` and `transcripts` would make entries genuinely immutable, at the cost of a fourth piece of persistent state and a build that can fail on a push race.

## Considered options

**One entry per meeting.** Simpler, and the title always names one body. Rejected: a busy Tuesday becomes three entries.

**One entry per item, as the only feed.** Rejected as the default. A well-summarized meeting would publish eight entries at once, which reads as a flood rather than as a day's news. It survives as the second feed for readers who want it.

**Only ever publish fully-settled days.** Removes the drift by never publishing a day the pipeline could not finish. Rejected: a meeting with no video would then vanish from the feed permanently, and a missing day is a worse error than a day that improves.

## Consequences

Retention is 30 day-entries and 100 item-entries — roughly three months either way. The build has enough data for far more (20 meetings per tab across 16 tabs), but most readers import every entry in the file on first subscribe, and a new subscriber's first experience should not be three hundred unread items dating back a year. The archive stays on the site; the feed is a notification channel, not a mirror.

An entry links to `/meeting/<meeting_id>.html#item-<item_id>` and uses that URL as its `id`. That anchor did not exist — deep links were `?t=<ms>` only, which Consent Items cannot use — so the feed closes `TODO.md` item 7 as a side effect. The `id` is only as stable as eSCRIBE's `item_id`, which the summaries cache has been keyed on all along; if it churned, cached summaries would have detached from old meetings long ago.
