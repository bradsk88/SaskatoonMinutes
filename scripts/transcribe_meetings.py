#!/usr/bin/env python3
"""
Transcribe recent meeting videos and cache results on the 'transcripts' branch.

Intended to run in a GitHub Action.  For each meeting tab, fetches the most
recent meetings and transcribes any that have video but no cached transcript.

Usage:
    python scripts/transcribe_meetings.py [--model base] [--limit 5]

Also walks the eSCRIBE calendar and transcribes meetings whose recording is
up but the upstream still marks not-passed (the gap ``list_past`` misses).
"""

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

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
    parser.add_argument(
        "--since", default=None,
        help="Calendar window start (ISO date); default 45 days ago",
    )
    parser.add_argument(
        "--until", default=None,
        help="Calendar window end (ISO date); default 15 days from now",
    )
    args = parser.parse_args()

    tabs = MEETING_TABS
    if args.tabs:
        tabs = [t for t in MEETING_TABS if t["slug"] in args.tabs]

    transcribed = 0
    skipped = 0

    # Meeting ids already handled by the per-tab (list_past) pass, so the
    # calendar pass does not re-attempt the same meeting.
    seen_ids: set[str] = set()

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
                    seen_ids.add(mid)
                    print(f"  Done: {len(segments)} segments", flush=True)
                except Exception as exc:
                    seen_ids.add(mid)
                    print(f"  ERROR: {exc}", flush=True)
                    traceback.print_exc()

        # Calendar pass: transcribe meetings whose recording is up but the
        # upstream still marks not-passed. list_past only returns passed
        # meetings, so without this the gap waits for the upstream flag
        # rather than the next scheduled run.
        since = args.since or (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
        until = args.until or (datetime.now(timezone.utc) + timedelta(days=15)).date().isoformat()
        print(f"\n--- Calendar (recorded, not passed: {since}..{until}) ---")
        try:
            recorded = source.list_recorded(since, until)
        except Exception as exc:
            recorded = []
            print(f"  Calendar fetch failed: {exc}")

        cal_done = 0
        for m in recorded:
            mid = m.meeting_id
            if cal_done >= args.limit:
                break
            if mid in seen_ids:
                continue
            if not m.has_video:
                continue

            cached = cache.load(mid)
            if cached is not None:
                print(
                    f"  [{m.date}] {mid[:8]}... already transcribed "
                    f"({len(cached.segments)} segments)"
                )
                skipped += 1
                continue

            print(f"  [{m.date}] {mid[:8]}... transcribing (not passed)...", flush=True)
            try:
                segments = transcribe_meeting(mid, model_size=args.model)
                cache.save(mid, Transcript.from_dict(segments))
                transcribed += 1
                cal_done += 1
                seen_ids.add(mid)
                print(f"  Done: {len(segments)} segments", flush=True)
            except Exception as exc:
                seen_ids.add(mid)
                print(f"  ERROR: {exc}", flush=True)
                traceback.print_exc()

    print(f"\nFinished: {transcribed} transcribed, {skipped} already cached")


if __name__ == "__main__":
    main()
