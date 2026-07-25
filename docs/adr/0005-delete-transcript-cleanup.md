# The transcript cleanup pass is deleted, because it lost the A/B it was kept for

ADR `0004` kept the Gemini cleanup pass for one reason: correcting garbled proper nouns against a fixed Saskatoon roster, "Whisper mangles these constantly in a civic corpus where names are the point". It also said to measure whether cleanup still earns its keep and cut it if not. That measurement has now run, twice, and cleanup loses. So the pass, `CleanTranscriptCache`, the roster it corrected against, the chunking, the truncation guard and the fingerprint are all deleted. The chip call now reads the raw transcript slice.

The rule was written down **before** the data, because the person running the experiment had already argued for deleting cleanup: it survived only if good corrections outnumbered bad substitutions **and** blind judges scored the cleaned arm at least even with the raw one. A tie deleted it.

Both arms were produced by the same model and the same chip prompt, differing only in whether the slice made a round trip through cleanup first.

- **Blind judges: raw 11, clean 9, tie 2** by majority across 22 items, with three independent judges per item, none of them shown the key and none allowed to reason across items (the A/B labels are re-randomized per item). 19 of 22 items unanimous; 34-26-6 across all 66 judgements. An earlier run at 9 items had raw ahead 4-2, so doubling the sample kept the direction and removed the small-sample defence.
- **Roster attractors: 3 flags in the cleaned arm, 1 in the raw.** A roster attractor is a name from the roster that a summary asserts and no source contains — not the official recommendation, not the agenda notes, not the raw transcript. Of the cleaned arm's three, two were real corrections (`Nutana ← "Nutanic"`, `Caswell Hill ← "Casual Hill"`) and one was a fabrication: **`Remai Modern ← "Remly"`**, an art gallery standing in for the Rumely condo corporation on a homeless-shelter item. So cleanup passed this gate 2-1 and still lost overall, because the gates are conjunctive.
- **The raw arm's single flag is the reason this is not close.** The raw arm produced `Nutana` from a transcript that only ever says "Nutanic", with no cleaned text in front of it. The chip model corrects Saskatoon proper nouns on its own, from the item's own official text — which is precisely the job cleanup was being kept for, done for free and, on this evidence, more conservatively.

Cleanup was ~99% of a backfill's token cost: it must *emit* every slice, roughly 15M output tokens across the 226 in-term meetings.

## Considered options

**Keep it behind an off-by-default flag.** Rejected. Dead code still has to be read, tested and kept working by everyone who touches the summarizer, and a flag invites "just try it with cleanup on" as a debugging move for problems cleanup does not cause. Git history is a better off switch.

**Make correction conservative rather than deleting it** — only rewrite a token when its target also appears in that item's own official text, so a councillor named in the minutes gets fixed and a condo corporation never gets renamed to an art gallery. This was the planned repair *if* cleanup had survived. It is moot now: the chip model already does the conservative version, because the official text is in its prompt.

**Move the roster into the chip prompt as a spelling aid.** Rejected, and this is the finding worth carrying forward: **every roster entry is an attractor competing for every garbled token.** A larger roster does not buy more corrections, it trades missed corrections for confident wrong ones — and a wrong name reads as correct where a garble reads as a garble. That is exactly how "Remly" became "Remai Modern". Relocating the list would relocate the failure.

## Consequences

The backfill becomes affordable — roughly 100x cheaper — which was the point of measuring rather than arguing.

Names now ship as the transcriber heard them unless the model recognizes them, so garbles remain visible in published chips: `Kakaushita Hall First Nation` (Kahkewistahaw) still appears, uncorrected, and would have with cleanup too — no First Nation was ever on the roster. A visible garble is the honest failure and the one a reader can discount.

The A/B harness (`ab_cleanup.py`, `ab_judge.py`, `roster_attractors.py`) is deleted with its subject: each imports the machinery being removed, and a script that cannot run is a trap rather than evidence. The numbers above and the working notes in `docs/plans/2026-07-25-001-summary-quality-plan.md` are the record; git history holds the harness if anyone reopens the question.

Already-cached CleanTranscripts on the `clean-transcripts` branch are left in place. No code reads them; deleting them would only discard work already paid for.

This supersedes `0004`.
