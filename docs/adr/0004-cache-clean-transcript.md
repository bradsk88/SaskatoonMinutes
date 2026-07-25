# The CleanTranscript is cached, because prompt iteration cannot afford to re-derive it

> **Superseded by `0005`.** The cleanup pass this ADR caches was measured
> against no cleanup at all and lost; the pass and its cache are deleted.
> Kept for the reasoning, which is why the measurement happened.

The Gemini cleanup pass must *emit* every agenda item's transcript slice in full — measured at ~270k characters, roughly 68k output tokens, for a single council meeting, run serially per item. A full pass over the 2026-06-24 council meeting exceeded ten minutes without finishing, which made iterating on the chip prompt impossible in practice: every experiment re-paid for cleanup that the experiment hadn't changed. So the cleanup output is cached per agenda item in a `CleanTranscriptCache`, splitting summarization cost in two — editing the **cleanup** prompt busts the cache and costs a full re-run, editing the **chip** prompt costs seconds.

## Considered options

**Delete the cleanup pass entirely.** Genuinely tempting, and the strongest argument for it is non-obvious: when Gemini is enabled, `extract_item_summaries` already switches *off* every transcript regex extractor (`app/item_categorizer.py:798`), so most of what cleanup was built to serve no longer consumes it. Rejected because one consumer remains that we have not replaced — cleanup corrects garbled proper nouns against a fixed Saskatoon roster ("Du Boa" → Dubois, "Maytee" → Métis, "Me was in" → Meewasin), and Whisper mangles these constantly in a civic corpus where names are the point. Cutting cleanup would have meant moving name correction into the chip prompt in the same change. Cache first, measure whether cleanup still earns its keep, cut later if not.

**Clean only the spans the extractors need** (windows around money mentions and speaker cues) rather than the whole slice. Rejected for now as the cheapest steady state but the most machinery to build *before* we can iterate at all — it optimizes the thing we are trying to stop paying attention to.

## Consequences

Cache-busting is a correctness requirement, not an optimization. A stale CleanTranscript means summaries silently reflect a cleanup prompt that no longer exists, which is exactly the kind of invisible degradation this project treats as worse than no output at all. The cleanup prompt's identity must therefore be part of what the cache stores, so a changed prompt cannot read through to old text.
