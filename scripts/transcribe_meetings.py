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
import time
import traceback

# Ensure print output appears immediately in CI logs
os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.scraper import fetch_past_meetings, MEETING_TABS
from app.transcriber import (
    load_cached_transcript,
    transcribe_meeting,
    save_transcript,
    TRANSCRIPT_BRANCH,
    _git,
)


def ensure_transcript_branch() -> None:
    """Make sure the orphan branch exists (fetch from remote or create)."""
    try:
        _git("fetch", "origin", TRANSCRIPT_BRANCH)
        # Set up local tracking branch
        try:
            _git("branch", TRANSCRIPT_BRANCH, f"origin/{TRANSCRIPT_BRANCH}")
        except RuntimeError:
            # Already exists locally, update it
            _git("branch", "-f", TRANSCRIPT_BRANCH, f"origin/{TRANSCRIPT_BRANCH}")
    except RuntimeError:
        # Branch doesn't exist on remote yet - will be created on first save
        print(f"Branch '{TRANSCRIPT_BRANCH}' not found on remote, will create on first transcript.")


def push_transcript_branch() -> None:
    """Push the transcripts branch to origin."""
    for attempt in range(4):
        try:
            _git("push", "origin", TRANSCRIPT_BRANCH)
            return
        except RuntimeError as exc:
            if attempt < 3:
                wait = 2 ** (attempt + 1)
                print(f"  Push failed, retrying in {wait}s: {exc}")
                time.sleep(wait)
            else:
                raise


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

    ensure_transcript_branch()

    tabs = MEETING_TABS
    if args.tabs:
        tabs = [t for t in MEETING_TABS if t["slug"] in args.tabs]

    transcribed = 0
    skipped = 0

    for tab in tabs:
        slug = tab["slug"]
        print(f"\n--- {tab['label']} ({slug}) ---")
        try:
            meetings, _ = fetch_past_meetings(page=1, meeting_type=tab["type"])
        except Exception as exc:
            print(f"  Failed to fetch meetings: {exc}")
            continue

        for m in meetings[: args.limit]:
            if not m.has_video:
                continue

            mid = m.meeting_id
            cached = load_cached_transcript(mid)
            if cached is not None:
                print(f"  [{m.date}] {mid[:8]}... already transcribed ({len(cached)} segments)")
                skipped += 1
                continue

            print(f"  [{m.date}] {mid[:8]}... transcribing...", flush=True)
            try:
                segments = transcribe_meeting(mid, model_size=args.model)
                save_transcript(mid, segments)
                transcribed += 1
                print(f"  Done: {len(segments)} segments", flush=True)
            except Exception as exc:
                print(f"  ERROR: {exc}", flush=True)
                traceback.print_exc()

    if transcribed > 0:
        print(f"\nPushing {transcribed} new transcript(s)...")
        push_transcript_branch()

    print(f"\nFinished: {transcribed} transcribed, {skipped} already cached")


if __name__ == "__main__":
    main()
