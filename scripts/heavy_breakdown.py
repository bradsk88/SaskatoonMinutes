#!/usr/bin/env python3
"""Break a long item into its smaller parts, on demand.

The summarize walk fires this workflow the moment it summarizes a meeting
that held the floor over two hours on an item (ADR ``0029``).  It is a
separate, forward-only pass: it reads the meeting's already-stored
description and chips and writes only the item's ``segments`` on top of
them, so when it lands the meeting page renders the breakdown in place of
the lighter summary.  It never walks the archive; a meeting is broken down
only when the walk is summarizing it now, and an item is broken down at
most once.

An item that already has a breakdown — ``segments`` present, even the
empty "declined to split" — is skipped, so a call is spent at most once per
long item.  A call that *fails* leaves the key unset (loads back as never),
which is the one case the job is allowed to meet again.

Usage:
    python scripts/heavy_breakdown.py --meeting-id <id>
        [--item-id <n>] [--max 3]
"""

import argparse
import os
import sys

os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.cache_git import PushAccessError, verify_push_access
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.item_categorizer import (
    GeminiExtractor,
    HEAVY_SPAN_MS,
    ExtractionFailed,
    QuotaExhausted,
    heavily_discussed_item_ids,
    has_segment_content,
)
from app.item_summaries_cache import ItemSummariesCache
from app.models import ItemSummary
from app.speakers import group_window, mark_jointly_heard
from app.transcript_cache import TranscriptCache


def pick_breakdown_targets(cached, items):
    """The items this run should break down, in agenda order.

    The two-hour floor, and an item that has not already been broken down
    (``segments`` absent).  A ``None`` entry — the item was not in the
    cached summary, e.g. it was ineligible — still counts as not-done and
    therefore as a target; the run writes its segments onto the existing
    summary and leaves the description and chips it holds.
    """
    candidates = set(heavily_discussed_item_ids(items, HEAVY_SPAN_MS))
    targets = []
    for item in items:
        item_id = item.get("item_id")
        if item_id not in candidates:
            continue
        entry = cached.get(str(item_id))
        if entry is not None and entry.segments is not None:
            continue  # one-shot: already has its breakdown
        targets.append(item)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument(
        "--item-id", type=int, default=None,
        help="Break down only this agenda item (manual fallback).",
    )
    parser.add_argument(
        "--max", type=int, default=3,
        help="Max items to break down this run (default: 3).",
    )
    args = parser.parse_args()

    extractor = GeminiExtractor()
    if not extractor.enabled:
        print(
            "ERROR: GEMINI_API_KEY is not set — nothing to break down.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Checked before any token is spent: the caches push on exit.
    try:
        verify_push_access("summaries")
    except PushAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Run this where credentials exist — the Heavy Item Breakdown "
            "workflow has contents: write, or authenticate this shell first.",
            file=sys.stderr,
        )
        sys.exit(2)

    source = EscribeMeetingSource(LiveEscribeTransport())
    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache:
        cached = summaries_cache.load(args.meeting_id) or {}
        transcript = transcript_cache.load(args.meeting_id)
        if not transcript or not transcript.segments:
            print(
                f"  {args.meeting_id[:8]}... no transcript — nothing to break down"
            )
            return
        detail = source.load_detail(args.meeting_id)
        items = [it.to_dict() for it in detail.agenda_items]
        mark_jointly_heard(items)
        transcript_segments = transcript.to_dict()

        targets = pick_breakdown_targets(cached, items)
        if args.item_id is not None:
            targets = [t for t in targets if t.get("item_id") == args.item_id]
        if not targets:
            print(
                f"  {args.meeting_id[:8]}... nothing to break down "
                f"(no over-two-hour item, or every one already has a breakdown)"
            )
            return

        done = 0
        for item in targets:
            if done >= args.max:
                break
            item_id = item.get("item_id")
            window = group_window(item, items)
            # A long span that actually said nothing (a bookmark lying, in the
            # shape of a five-hour silence) earns no call and no re-try: it is
            # already done by the empty "nothing to split" answer the last real
            # pass would have left.
            if not has_segment_content(item, transcript_segments, window):
                print(
                    f"    item {item_id}: span has no real transcript — skipped "
                    f"(no call spent)",
                    flush=True,
                )
                # Record the empty "declined to split" so the one-shot holds.
                key = str(item_id)
                base = cached.get(key)
                payload = (
                    base.to_dict() if base is not None
                    else {"description": None, "chips": []}
                )
                payload["segments"] = []
                cached[key] = ItemSummary.from_dict(payload)
                summaries_cache.save(args.meeting_id, cached)
                continue

            title = (item.get("title") or "")[:60]
            print(
                f"    breaking down item {item_id}: {title}",
                flush=True,
            )
            try:
                segments = extractor.extract_segments(
                    item, transcript_segments, window=window,
                )
            except QuotaExhausted as exc:
                # Every remaining call in this run would fail the same way.
                # Stopping leaves the item unset so a later run redoes it.
                print(f"    item {item_id}: STOPPING — {exc}", flush=True)
                break
            except ExtractionFailed as exc:
                # Leave the key unset so the item stays the job's to retry.
                print(f"    item {item_id}: skipped ({exc})", flush=True)
                continue

            key = str(item_id)
            base = cached.get(key)
            payload = (
                base.to_dict() if base is not None
                else {"description": None, "chips": []}
            )
            payload["segments"] = segments
            cached[key] = ItemSummary.from_dict(payload)
            summaries_cache.save(args.meeting_id, cached)
            done += 1
            print(
                f"    item {item_id}: {len(segments)} topic(s) stored — the "
                f"meeting page will render them over the lighter summary",
                flush=True,
            )

        print(
            f"  done: {done} item(s) broken down for {args.meeting_id[:8]}..."
        )


if __name__ == "__main__":
    main()
