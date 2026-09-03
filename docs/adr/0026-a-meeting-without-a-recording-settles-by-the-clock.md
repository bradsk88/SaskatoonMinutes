---
status: accepted
---

# A meeting without a recording settles by the clock, and both surfaces say so

The feed published an entry for a meeting whose recording was not yet
available, and the entry had no useful summary in it. Two things had
lined up. The meeting had sat and been marked passed, but the City had
not posted the video. While it was still a Scheduled Meeting, the
provisional path had written agenda-derived summaries into the normal
cache (ADR `0021`). The site's feed flag was `bool(item_summaries)`,
which counted that provisional coverage as "has summaries", so the
meeting settled immediately and its entry led with the agenda, dressed
up as what happened.

Two decisions.

**The feed's "has summaries" means real summaries.** The feed flag is
now `has_current_summaries`, the same predicate the summarize job's
skip rule uses (both live on `app.models`, so the two call sites cannot
drift). A provisional or legacy entry does not count: it was written
before the meeting, from official text alone, and an entry settled on
it would say what the agenda promised, not what happened. A meeting
with only provisional coverage therefore keeps waiting, exactly like a
meeting with no cache at all.

**A missing recording is a state, and it is shown where the video
would be.** The detail page's video slot, which was empty when there
was no video, now says which is true: the recording is **pending**
(within seven days of the meeting sitting, the City has simply not
posted it yet) or the meeting was **not recorded** (the week has run
out). A cancelled meeting and one that has not happened yet say
nothing. The settled feeds use the same seven days to settle a
no-video meeting (`SETTLE_DAYS` is now the shared
`RECORDING_GRACE_DAYS`), and such an entry carries a plain note that
its item descriptions were written from the published agenda rather
than from the discussion, instead of reading as an account of what
council did.

## Considered options

**Never publish a no-recording meeting.** Rejected. The seven-day
settle already exists to keep a meeting whose video never arrives from
waiting forever, and a meeting missing from the feed is a worse error
than one that publishes with a note. The note is what makes the late
entry honest rather than thin.

**A separate feed for unrecorded meetings.** Rejected. Subscribers do
not want a second subscription to hear the negative; they want the one
entry to say the truth when it arrives.

**Waiting on `has_video` directly in the feed instead of on summaries.**
Rejected. The feed's unit is the summary, and a video that lands but
has not been transcribed or summarized yet is still not publishable.
Counting real summaries is the stricter, correct gate; the video only
matters for the not-recorded note and the page's indicator.
