# Typed cache wrappers (`TranscriptCache`, `ItemSummariesCache`) own their (de)serialization

The `Cache` seam (`app/cache.py`) is a generic protocol with adapters `GitBranchCache` (production) and `InMemoryCache` (tests). On top of it, `TranscriptCache` and `ItemSummariesCache` are deliberately separate wrapper classes — each owns its (de)serialization (`Transcript.from_dict`/`to_dict`, `ItemSummary.from_dict`/`to_dict`) and pins its branch and directory name. They were introduced 2026-05-01 (commits `7593949`, `5888375`) per `docs/plans/2026-05-01-001-refactor-typed-cache-seam-plan.md`, replacing duplicated git-orphan-branch lifecycle code in `app/transcriber.py` and `app/item_summaries_store.py`.

Future architecture reviews should not propose collapsing the wrappers into a generic `GitBranchCache[T]` with an injected codec on the basis that the wrappers look like "shallow shims." The wrappers exist so callers never touch raw dicts, so each cached type's serialization lives next to that type's domain code, and so each branch's identity (`transcripts`, `summaries`) is named rather than configured at every call site. The pass-through `__enter__`/`__exit__` is the price of typed `load`/`save` returning the right type — that's a feature, not a smell.

## Note on documentation drift

CONTEXT.md describes `ItemSummariesCache` as `Cache[list[ItemSummary]]`, but the on-disk shape is `dict[str, list[ItemSummary]]` keyed by stringified `item_id` (one entry per agenda item within a meeting). Update CONTEXT.md to match the code, not the other way around.
