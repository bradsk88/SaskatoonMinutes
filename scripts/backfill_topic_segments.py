#!/usr/bin/env python3
"""One-off: give the archive's long items their topic segments (ADR 0029).

The summarize walk skips a meeting whose summaries are current, so a
meeting summarized before segments existed never gets them: every
in-term archive meeting is "already summarized".  This pass walks the
summaries branch, finds the meetings that carry a long item (30+
minutes, real transcript in the span) that has not yet received its
topics, and runs *only* the segment pass for those items.  The
description and chips that are already cached stay untouched — one
Gemini call per long item, not one per item.

It is safe to re-run: an item whose topic passes are done is skipped,
and a meeting with nothing to add is left alone (no commit).  A quota
rejection stops the run; whatever finished before it is pushed on
exit, so the next dispatch resumes where this one stopped.

Usage (local, where the summaries branch and GEMINI_API_KEY exist):

    GEMINI_API_KEY=... python scripts/backfill_topic_segments.py
"""

import dataclasses
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.cache_git import PushAccessError, verify_push_access
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.item_categorizer import (
    ExtractionFailed,
    GeminiExtractor,
    QuotaExhausted,
    has_segment_content,
    needs_topic_segments,
)
from app.item_summaries_cache import ItemSummariesCache
from app.models import ItemSegment, ItemSummary, has_current_summaries, segments_fully_current
from app.speakers import group_window, mark_jointly_heard
from app.transcript_cache import TranscriptCache


def backfill_meeting(
    source,
    extractor,
    meeting_id: str,
    cached: dict[str, ItemSummary],
    transcript,
) -> dict[str, ItemSummary] | None:
    """Merge topic segments into *cached* for the items that lack them.

    Returns the updated mapping when at least one item gained segments,
    else ``None`` (nothing to save).  Raises ``QuotaExhausted`` so the
    caller can stop the run; the caches push their partial progress on
    exit.
    """
    items = [it.to_dict() for it in source.load_detail(meeting_id).agenda_items]
    mark_jointly_heard(items)
    transcript_segs = transcript.to_dict()

    updated = dict(cached)
    changed = False
    for item in items:
        iid = str(item.get("item_id"))
        summary = updated.get(iid)
        # Done: every topic has been through the chip pass (a fully
        # populated list, or an explicit empty list where the model
        # found nothing to split).  Pending: never asked, a failed
        # pass, or the pre-chips archive shape (a topic whose chips
        # key is absent) — re-asked here, same as before the chips
        # existed.
        if summary is not None and segments_fully_current(summary.segments):
            continue
        if not needs_topic_segments(item):
            continue
        if not has_segment_content(item, transcript_segs):
            continue

        window = group_window(item, items)
        try:
            raw = extractor.extract_segments(
                item, transcript_segs, window=window,
            )
        except (QuotaExhausted,):
            raise
        except ExtractionFailed as exc:
            # A flaky item costs its topics, not the meeting's run: the
            # next dispatch retries it (it still has no segments).
            print(f"    item {iid}: segments skipped ({exc})", flush=True)
            continue
        segments = [ItemSegment.from_dict(s) for s in raw]
        if not segments:
            # The model read the span and found nothing to split into.
            # Record the attempt as an explicit empty list — a real
            # state on disk now, so the walk's backfill check does not
            # re-flag this meeting on every dispatch.
            if summary is not None and summary.segments == []:
                continue
            if summary is None:
                summary = ItemSummary(description=None, chips=[])
            updated[iid] = dataclasses.replace(summary, segments=[])
            changed = True
            print(
                f"    item {iid}: no distinct topics (recorded)",
                flush=True,
            )
            continue
        if summary is None:
            summary = ItemSummary(description=None, chips=[])
        updated[iid] = dataclasses.replace(summary, segments=segments)
        changed = True
        print(
            f"    item {iid}: +{len(segments)} topics",
            flush=True,
        )

    return updated if changed else None


def main() -> int:
    extractor = GeminiExtractor()
    if not extractor.enabled:
        print(
            "ERROR: GEMINI_API_KEY is not set — the segment pass is an "
            "LLM call, and there is nothing deterministic to fall back to.",
            file=sys.stderr,
        )
        return 2

    # Checked before any work: the caches push on exit, so a credential
    # failure discovered at the end costs the whole run's tokens.
    try:
        verify_push_access("summaries")
    except PushAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    source = EscribeMeetingSource(LiveEscribeTransport())

    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache:
        meeting_ids = summaries_cache.keys()
        print(f"Walk {len(meeting_ids)} cached meetings...", flush=True)

        updated = 0
        skipped = 0
        for mid in meeting_ids:
            cached = summaries_cache.load(mid)
            if cached is None or not has_current_summaries(cached):
                skipped += 1
                continue
            transcript = transcript_cache.load(mid)
            if transcript is None or not transcript.segments:
                skipped += 1
                continue
            try:
                new_summaries = backfill_meeting(
                    source, extractor, mid, cached, transcript,
                )
            except QuotaExhausted as exc:
                print(f"\nQuota exhausted at {mid[:8]}: {exc}", flush=True)
                print(
                    "Stopping — the finished work pushed on exit; "
                    "re-dispatch to resume.",
                    flush=True,
                )
                return 0
            except Exception as exc:
                # A meeting whose detail no longer fetches (removed,
                # renamed) is not the end of the world.
                print(f"  {mid[:8]}... skipped ({exc})", flush=True)
                skipped += 1
                continue

            if new_summaries is None:
                skipped += 1
                continue
            summaries_cache.save(mid, new_summaries)
            updated += 1
            print(f"  [{mid[:8]}] saved", flush=True)

        print(f"\nDone: {updated} updated, {skipped} left alone.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
