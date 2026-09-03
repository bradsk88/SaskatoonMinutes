---
status: accepted
---

# The grace is 12 hours after the meeting's start, not seven days after its date

ADR `0026` settled the rule: a no-recording meeting settles by the
clock, and the feed and the page use one shared line. It left the
number at seven days, borrowed from the feeds' old `SETTLE_DAYS`.

The number was wrong for what it now has to do. A resident waiting on
a meeting's content should not wait a week for it; the line that
decides "pending" from "not recorded" should be close to the meeting,
not a week out. The decision: the grace is 12 hours after the
meeting's start, the same line for both surfaces.

- The feed settles a no-video meeting 12 hours after it sat and
  publishes the non-transcript (provisional, agenda-derived) summary
  with the not-recorded note.
- The page flips from pending to not recorded at the same moment.
- A meeting *with* a recording but no real summary keeps the seven-day
  escape: the recording is there, only the pipeline is behind, so the
  clock is our own failure, not the City's.

The feeds read a clock, not a date. `is_settled` takes a Saskatchewan
datetime, and the meeting's start is its date plus the 24-hour time
eSCRIBE carries; a missing or malformed time is midnight on the date,
so a meeting with a date still settles 12 hours after that rather
than never. `SETTLE_DAYS` survives only as the with-video escape.

## Considered options

**Keep seven days for the feed and 12 hours for the page.** Rejected
— two lines for one fact is exactly the drift ADR `0026` closed.

**One hour after adjournment ("the meeting is over").** Rejected — a
recording can land while the room is still clearing, so a one-hour
line would fire "not recorded" before the City has had a chance to
post.

**Waiting for the video to be listed rather than 12 hours to elapse.**
The two are the same test here: the feed is rebuilt every few hours,
so a no-video meeting settles 12 hours after it sat whether the check
is "no video for 12 hours" or "12 hours since the meeting and still no
video". Naming the clock keeps the feed and the page on one line.
