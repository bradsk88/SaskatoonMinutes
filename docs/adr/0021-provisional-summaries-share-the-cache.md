# Provisional summaries for Scheduled Meetings share the ItemSummariesCache, keyed by meeting_id, and are discarded on the flip to Meeting

The Future tab shows Scheduled Meetings with LLM summaries of their agenda
items — written days before the meeting, from official text alone, with no
transcript and no discussion. These summaries are provisional by nature: the
agenda can still be revised, and everything is regenerated after the meeting
anyway. The question was where provisional output lives, given that
`CONTEXT.md` defines the Cache as "the durable home for derived data,
recomputed only when missing" — a definition written for artifacts that are
never wrong on purpose.

The decision: provisional summaries go in the normal `ItemSummariesCache`
under the meeting's real `meeting_id`. They are marked provisional so the
pipeline can tell them apart. When the meeting first appears in PastMeetings
(the flip), the cached entry is treated as absent and regeneration proceeds
with the transcript. Pre-meeting agenda revisions are ignored entirely —
no re-summarization, no revision tracking — because the flip corrects all
drift in one pass.

Provisional summaries are produced Consent-Item-style: official text only,
discussion-only categories excluded by construction, because an item that
has not been discussed cannot have a debate highlight. They run at lower
priority than post-meeting summaries — quota goes to real summaries first —
since they are disposable by design.

## Considered options

**A separate cache for provisional summaries.** Rejected. Two caches for
the same conceptual artifact means every reader (build, feeds, index) must
know which to consult and when, and the flip becomes a copy-and-delete
across stores. One cache with a provisional marker makes the flip a local
decision at the point that already decides "is this meeting summarized?"

**Keying provisional summaries by agenda revision** (date or eSCRIBE's
"Revised Agenda" version) and re-summarizing on each revision. Rejected.
Agendas post Wednesday 4 p.m. one week before the meeting; a revision costs
a full second LLM pass to buy at most a few days of slightly-more-accurate
disposable text, and the post-meeting regeneration fixes everything anyway.
Revisions also arrive with no signal we can poll cheaply beyond refetching
and diffing every agenda on every build.

**No provisional summaries at all** — show raw agenda text until the
meeting. Rejected as the default, but held in reserve: this whole feature
is an experiment, and if pre-meeting summaries prove not to be read, the
fallback design is this option and the marker machinery still earns its
keep by being small.

## Consequences

Anything that reads `ItemSummariesCache` must respect the provisional
marker: the build renders provisional summaries only on the Future tab, the
flip logic must not let a provisional entry suppress regeneration, and the
summarize workflow's "already summarized" skip check needs the same
distinction. A provisional summary that leaks onto a post-meeting page —
or one that blocks regeneration — is the failure mode this ADR exists to
prevent.

The experiment framing is load-bearing: if the Future tab is kept but its
summaries are cut, the provisional marker and flip logic are deleted with
it, and this ADR becomes the record of why they existed.
