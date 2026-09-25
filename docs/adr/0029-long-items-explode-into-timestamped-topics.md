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
- **The index and feeds do not change.** The card skims, the details page
  proves.
