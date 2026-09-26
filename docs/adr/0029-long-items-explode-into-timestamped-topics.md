---
status: accepted
---

# Long items explode into timestamped topics

A 155-minute budget debate is one agenda item, but it plays as a series of
distinct topics: the staff presentation, the police numbers, transit, the
library cut, the motions. The details page drew it as one card with one
takeaway and one timestamp (the item's start), so a resident wanting "the
part where the police budget went up" got "the budget debate, somewhere".

The line is a topic or presentation, not a speaker. Speakers already have
their own rows with what they argued (ADR `0022`); a budget debate's
findable units are the issues the council moved through inside the item.

- **The gate.** An item qualifies when it runs 30 minutes or longer and
  the span is its own (not inherited), not a recess, not procedural, and
  not the partner side of a jointly-heard discussion. In a 100-meeting
  sample, 292 items ran over 25 minutes, and the long tail was broken
  spans doing the running: a "Recess" inheriting five hours, a "GIVING
  NOTICE" placeholder at 235 minutes. A floor on the transcript's word
  count in the span catches what the duration check cannot.
- **One LLM pass.** A second Gemini call beside the description call, on
  the item's transcript slice with a timestamp on every line. It returns
  the distinct topics in order, each with a short title, the moment the
  topic began, and a one-sentence takeaway. It fires only for an item the
  gate admits, so the archive is untouched and a meeting costs a call for
  the long item that needs it. The result rides in the item's cached
  summary next to the description, and an entry with no topics is an
  ordinary short item, not a failure.
- **Timestamps snap, they are not trusted.** The prompt requires the model
  to copy the timestamp of the first line of a topic from the transcript
  it was shown. The code checks the answer against that same list of
  starts, and a time the model invented or fumbled snaps to the nearest
  real one. Order is enforced and duplicates dropped.
- **Presentation.** The parent card keeps its title, outcome, description
  and chips. Beneath it, the topics draw as their own rows — timestamp,
  topic, takeaway — bound by a spine that says they are one item opened
  up, not a new agenda.
- **The skip rule learns about segments, so the run converges.** The
  summarize walk skips a meeting whose summaries are current, and every
  archive meeting is current: without an exception the long items would
  wait for a re-summarize that never comes. The exception is narrow: a
  meeting is not current while it carries a long item that has not had
  the segment pass, and whose span carries real transcript. "Not had the
  pass" is one of three on-disk states, and the distinction is what makes
  the walk stop: a missing key is *never* (or the last attempt failed,
  which retries as never), an explicit empty list is *attempted, nothing
  to split*, and a populated list is the topics. An item the model read
  and declined to split is done, not pending, or the walk would re-do
  that meeting on every dispatch. Thin-span items never flag: re-doing
  them would produce nothing, and the meeting would stay not-current
  forever.
- **A one-off backfill does the archive.** The daily walk re-does a
  flagged meeting in full, which is right for meetings it meets as it
  walks, but the archive sits at the far end of the walk. A separate
  workflow (`backfill-topics.yml`, manual) walks the summaries branch,
  asks the segment pass only the long items that lack it, one Gemini
  call per item, and merges the answer into the existing cached
  summary. Descriptions and chips are untouched, an item that already
  has topics is skipped, and a meeting with nothing to add is left
  alone. Re-running is safe: the three states above make the pass
  idempotent, and a quota stop pushes its finished work, so the next
  dispatch resumes.
- **The index and feeds do not change.** The card skims, the details page
  proves.
