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

  **Current shape (2026-10).** The heavy breakdown runs as a dedicated
  forward-only job, not inline in the light pass. That is the change from
  the original design: we moved the heavy work off the light loop, because
  that was the recurring cost we were trying to remove, and we made the
  trigger a two-hour floor, so the pass lands only on the genuinely long
  items.

- **The trigger, not a gate.** An item earns the heavy breakdown when it
  held the floor over two hours, its own start to the next item's start,
  its own end if it is last. Measured how a resident feels length, the pass
  lands on the genuinely long items only, at most a couple per meeting, once
  each. Recess and procedural items, and the partner side of a jointly-heard
  item, never qualify (ADR `0025`). The job still requires the span to
  carry real transcript; a two-hour bookmark that says nothing is a broken
  span or a recess, not a topic to split, so that stays a check the job
  makes against the transcript, not the fire.
- **A dedicated job, off the light pass.** The breakdown is one Gemini call
  per long item, on the item's transcript slice, with a timestamp on every
  line, run by a separate forward-only workflow (`heavy-breakdown.yml`), not
  beside the description call. The summarize walk, whose job was reduced to
  description and chips only, fires that workflow the moment it summarizes a
  meeting that held over two hours on an item. The fire itself is an event,
  not an LLM call, and stays in the recurring path only as that event, so
  the heavy spend is never in the daily loop. The job returns the distinct
  topics in order, each a mini item: a short title, the moment the topic
  began, and its own aggregate, description bullets and chips drawn from the
  item's chip vocabulary, because a fifteen-minute topic holds as many facts
  as a typical agenda item. A topic the body voted on earns its own Outcome
  chip, exactly the case the item-level rule against tallies cannot cover.
  The result rides in the item's cached summary next to the description, and
  an entry with no topics is an ordinary short item, not a failure.
- **Timestamps snap, they are not trusted.** The prompt requires the model
  to copy the timestamp of the first line of a topic from the transcript
  it was shown. The code checks the answer against that same list of
  starts, and a time the model invented or fumbled snaps to the nearest
  real one. Order is enforced and duplicates dropped.
- **Presentation.** The parent card keeps its title, outcome, description
  and chips. The topics draw as sibling cards of the item's own — the
  same card chrome, a timestamp where the section number would be,
  description bullets and chips through the item's own summary view — in
  the order the body moved the discussion, flat and chronological rather
  than nested. Each card carries the parent's categories, so a category
  filter moves a topic with the item it belongs to.
- **The light pass converges; the heavy job is one-shot.** With the heavy
  pass off it, the light pass's only current check is description-and-chips
  coverage: a meeting whose summaries are current is skipped, and it stays
  current, and the run covers the term in about ten days as before. The
  heavy job has no recurring cost of its own. An item it has already broken
  down, and an item it read and declined to split, both carry `segments`
  and are skipped, so a call is spent at most once per long item, and only
  an item that failed, its key unset, retries. That is the same three-state
  record the chip pass uses, absent is never, empty is a finding, populated
  is the topics, and it is what makes the job idempotent.
- **Forward-only: the archive is not re-broken down.** The heavy job runs
  only for a meeting the walk is summarizing now, and it reads the
  meeting's existing description and chips, writing only the item's
  `segments`. The old manual archive backfill (`backfill-topics.yml`) is
  retired: a heavy pass over the whole term is a real charge, not a re-run,
  and the lighter summary is what the archive carries. A meeting's long
  item earns the heavy pass once, when that meeting is summarized, and
  never again.
- **The index and feeds do not change.** The card skims, the details page
  proves.
