---
status: accepted
---

# Settled no-recording meetings land on the body's past tab

ADR `0026` settled the rule: a meeting that sits with no recording
settles by the clock, and the feed and the detail page share one
line. The site's tabs were left out of that rule. A no-video meeting
is in neither `list_past` (the upstream marks it not-passed for a
while) nor the recorded pass (`list_recorded` wants a video), so it
fell out of every list: no card on its body's past tab, and the past
feeds never saw it either, which also left the feed half of `0026`
unreachable.

The 2026-09-23 council meeting hit exactly this: held at 9:30 a.m.,
never recorded, invisible everywhere.

The fix adds a third calendar pass, `list_settled_no_video`, that
returns no-video, not-passed meetings whose start is at least
`RECORDING_GRACE_HOURS` behind the build clock. The build merges it
into each body's tab list the same way it merges the recorded pass.

- On the day a meeting is held it stays on the Future tab, as it does
  today.
- After the day, while it is still pending, it is nowhere, the gap
  `0026` accepts for a pending recording.
- When it settles, the tab, the past feed, and the detail page's
  not-recorded state all flip together, because they all settle
  against the same 12-hour line fed by the same build clock.
- The meeting's content is the agenda plus the not-recorded note.
  The transcribe and summarize jobs are untouched: there is no video
  to run on.

The pass runs on the same 45-day lookback window as the recorded
pass, and a body with no settled no-video meeting gets nothing, so
existing tab contents are unchanged.
