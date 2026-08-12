# Future meetings get their own feed

Scheduled Meetings were originally kept out of the Atom feeds entirely ("they
are not Meeting Days and nothing about them is Settled"). That rule still
holds for the two settled feeds (`feed.xml`, `feed-items.xml`), but the
user need — "tell me ahead of time whether I should register to speak, in my
reader/inbox, without checking the site" — is deadline-driven and legitimate,
so Scheduled Meetings get a dedicated third feed: `feed-future.xml`, one entry
per Scheduled Meeting, full agenda, Request-to-Speak Deadline up top, entries
dropped once the meeting happens.

**Considered Options.** Mixing Scheduled Meetings into the existing feeds was
rejected — it would dilute the "what was decided" contract those feeds
promise. Per-item entries were rejected — the deadline is per-meeting and
per-item would flood readers. Publishing on announcement and re-pinging when
the agenda posts was rejected — an agenda-less entry can't answer "should I
sign up?", and "agenda posted" is a pipeline detail, not a user event. Keeping
entries after the meeting with a "has happened" note was rejected in favour of
dropping them: readers keep what they fetched, so dropping only spares new
subscribers stale noise.
