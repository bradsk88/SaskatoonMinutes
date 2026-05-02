#!/usr/bin/env python3
"""
Transcribe recent meeting videos and cache results on the 'transcripts' branch.

Intended to run in a GitHub Action.  For each meeting tab, fetches the most
recent meetings and transcribes any that have video but no cached transcript.

Usage:
    python scripts/transcribe_meetings.py [--model base] [--limit 5]
"""

import argparse
import os
import sys
import traceback

# Ensure print output appears immediately in CI logs
os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS
from app.models import Transcript
from app.transcriber import transcribe_meeting
from app.transcript_cache import TranscriptCache


def main():
    parser = argparse.ArgumentParser(description="Transcribe meeting videos")
    parser.add_argument(
        "--model", default="base",
        help="Whisper model size (tiny, base, small, medium, large-v3)",
    )
    parser.add_argument(
        "--limit", type=int, default=3,
        help="Max meetings to check per tab",
    )
    parser.add_argument(
        "--tabs", nargs="*", default=None,
        help="Tab slugs to process (default: all)",
    )
    args = parser.parse_args()

    tabs = MEETING_TABS
    if args.tabs:
        tabs = [t for t in MEETING_TABS if t["slug"] in args.tabs]

    transcribed = 0
    skipped = 0

    source: MeetingSource = EscribeMeetingSource(LiveEscribeTransport())
    with TranscriptCache.open() as cache:
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
                cached = cache.load(mid)
                if cached is not None:
                    print(
                        f"  [{m.date}] {mid[:8]}... already transcribed "
                        f"({len(cached.segments)} segments)"
                    )
                    skipped += 1
                    continue

                print(f"  [{m.date}] {mid[:8]}... transcribing...", flush=True)
                try:
                    segments = transcribe_meeting(mid, model_size=args.model)
                    cache.save(mid, Transcript.from_dict(segments))
                    transcribed += 1
                    processed_this_tab += 1
                    print(f"  Done: {len(segments)} segments", flush=True)
                except Exception as exc:
                    print(f"  ERROR: {exc}", flush=True)
                    traceback.print_exc()

    print(f"\nFinished: {transcribed} transcribed, {skipped} already cached")


if __name__ == "__main__":
    main()
