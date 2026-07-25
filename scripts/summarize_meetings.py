#!/usr/bin/env python3
"""
Extract categorized per-item summaries from cached transcripts and store
them on the ``summaries`` orphan branch.

For each meeting tab, walks the most recent meetings and summarizes any
that have a cached transcript but no existing summaries file.  Meetings
already covered are skipped before any extraction work runs.

Usage:
    python scripts/summarize_meetings.py [--limit 2] [--tabs council ...]
"""

import argparse
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

# Chip calls are I/O-bound, so run a meeting's items concurrently.
EXTRACT_WORKERS = 8

os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.clean_transcript_cache import CleanTranscriptCache
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.item_categorizer import (
    clean_meeting_transcripts,
    cleanup_fingerprint,
    extract_item_summaries,
    is_eligible_for_summary,
    GeminiExtractor,
)
from app.item_summaries_cache import ItemSummariesCache
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS
from app.models import ItemSummary
from app.transcript_cache import TranscriptCache


def summarize_meeting(
    source: MeetingSource,
    meeting_id: str,
    extractor,
    transcript_cache,
    clean_cache,
) -> dict[str, list[ItemSummary]]:
    """Run the extractor across every eligible agenda item in the meeting."""
    transcript = transcript_cache.load(meeting_id)
    if not transcript or not transcript.segments:
        return {}
    print(f"    Transcript has {len(transcript.segments)} segments", flush=True)
    detail = source.load_detail(meeting_id)
    items = [it.to_dict() for it in detail.agenda_items]
    transcript_segments = transcript.to_dict()

    eligible = [i for i in items if is_eligible_for_summary(i)]
    summaries: dict[str, list[ItemSummary]] = {
        str(i["item_id"]): [] for i in items if not is_eligible_for_summary(i)
    }

    # Cleanup is the expensive half and chip-prompt changes don't affect
    # it, so reuse whatever the cache still considers current.
    cached_clean = clean_cache.load(meeting_id)
    if cached_clean:
        print(f"    Reusing {len(cached_clean)} cached CleanTranscripts", flush=True)
    clean = clean_meeting_transcripts(
        eligible, transcript_segments, extractor, cached=cached_clean,
    )
    if clean != (cached_clean or {}):
        clean_cache.save(meeting_id, clean)

    def run(item: dict) -> tuple[dict, list[dict]]:
        return item, extract_item_summaries(
            item, transcript_segments,
            gemini_extractor=extractor,
            cleaned_transcript_text=clean[str(item["item_id"])],
        )

    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        for item, entries in pool.map(run, eligible):
            title = (item.get("title") or "")[:60]
            cats = [e["category"] for e in entries]
            print(f"    Item {item['item_id']}: {title}", flush=True)
            print(f"      → {len(entries)} chips: {cats}", flush=True)
            summaries[str(item["item_id"])] = [
                ItemSummary.from_dict(e) for e in entries
            ]

    print(f"    {len(eligible)}/{len(items)} items eligible for summary", flush=True)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=2,
        help="Max meetings to summarize per tab (default: 2).",
    )
    parser.add_argument(
        "--tabs", nargs="*", default=None,
        help="Tab slugs to process (default: all).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-summarize meetings that already have cached summaries.",
    )
    args = parser.parse_args()

    tabs = MEETING_TABS
    if args.tabs:
        tabs = [t for t in MEETING_TABS if t["slug"] in args.tabs]

    extractor = GeminiExtractor()
    if not extractor.enabled:
        print(
            "WARNING: GEMINI_API_KEY is not set — only deterministic chips "
            "will be produced (no LLM pass)."
        )

    summarized = 0
    skipped = 0
    errors = 0

    source: MeetingSource = EscribeMeetingSource(LiveEscribeTransport())
    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache, \
            CleanTranscriptCache.open(cleanup_fingerprint()) as clean_cache:
        for tab in tabs:
            slug = tab["slug"]
            print(f"\n--- {tab['label']} ({slug}) ---")
            try:
                meetings, _ = source.list_past(page=1, meeting_type=tab["type"])
            except Exception as exc:
                print(f"  Failed to fetch meetings: {exc}")
                continue

            processed_this_tab = 0
            for m in meetings:
                if processed_this_tab >= args.limit:
                    break
                if not m.has_video:
                    continue

                mid = m.meeting_id
                if not args.force and summaries_cache.load(mid) is not None:
                    print(f"  [{m.date}] {mid[:8]}... already summarized")
                    skipped += 1
                    continue
                if transcript_cache.load(mid) is None:
                    print(
                        f"  [{m.date}] {mid[:8]}... no transcript yet, skipping"
                    )
                    continue

                print(f"  [{m.date}] {mid[:8]}... summarizing...", flush=True)
                try:
                    summaries = summarize_meeting(
                        source, mid, extractor, transcript_cache, clean_cache,
                    )
                    summaries_cache.save(mid, summaries)
                    summarized += 1
                    processed_this_tab += 1
                    counted = sum(1 for v in summaries.values() if v)
                    print(
                        f"    Done: {counted}/{len(summaries)} items have chips",
                        flush=True,
                    )
                except Exception as exc:
                    errors += 1
                    print(f"    ERROR: {exc}", flush=True)
                    traceback.print_exc()

    print(
        f"\nFinished: {summarized} summarized, {skipped} already cached, "
        f"{errors} errors"
    )
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
