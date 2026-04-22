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
import time
import traceback

os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.scraper import fetch_past_meetings, fetch_meeting_detail, MEETING_TABS
from app.transcriber import load_cached_transcript, _git, TRANSCRIPT_BRANCH
from app.item_categorizer import (
    extract_item_summaries,
    is_eligible_for_summary,
    GeminiExtractor,
)
from app.item_summaries_store import (
    SUMMARIES_BRANCH,
    load_cached_summaries,
    save_summaries,
)


def ensure_branches() -> None:
    """Fetch the transcripts and summaries orphan branches if they exist."""
    for branch in (TRANSCRIPT_BRANCH, SUMMARIES_BRANCH):
        try:
            _git("fetch", "origin", f"{branch}:refs/remotes/origin/{branch}")
        except RuntimeError:
            print(f"  Branch '{branch}' not present on origin yet.")

    # Set up local tracking for the summaries branch so save_summaries can
    # find it via `rev-parse --verify`.
    try:
        _git("rev-parse", "--verify", f"origin/{SUMMARIES_BRANCH}")
    except RuntimeError:
        return
    try:
        _git("branch", "-f", SUMMARIES_BRANCH, f"origin/{SUMMARIES_BRANCH}")
    except RuntimeError:
        pass


def push_summaries_branch() -> None:
    for attempt in range(4):
        try:
            _git("push", "origin", SUMMARIES_BRANCH)
            return
        except RuntimeError as exc:
            if attempt < 3:
                wait = 2 ** (attempt + 1)
                print(f"  Push failed, retrying in {wait}s: {exc}")
                time.sleep(wait)
            else:
                raise


def summarize_meeting(meeting_id: str, extractor) -> dict[str, list[dict]]:
    """Run the extractor across every eligible agenda item in the meeting."""
    transcript = load_cached_transcript(meeting_id)
    if not transcript:
        return {}
    detail = fetch_meeting_detail(meeting_id, include_votes=True)
    items = [it.to_dict() for it in detail["agenda_items"]]

    summaries: dict[str, list[dict]] = {}
    for item in items:
        if not is_eligible_for_summary(item):
            summaries[str(item["item_id"])] = []
            continue
        entries = extract_item_summaries(
            item, transcript, gemini_extractor=extractor,
        )
        summaries[str(item["item_id"])] = entries
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

    ensure_branches()

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

    for tab in tabs:
        slug = tab["slug"]
        print(f"\n--- {tab['label']} ({slug}) ---")
        try:
            meetings, _ = fetch_past_meetings(page=1, meeting_type=tab["type"])
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
            if not args.force and load_cached_summaries(mid) is not None:
                print(f"  [{m.date}] {mid[:8]}... already summarized")
                skipped += 1
                continue
            if load_cached_transcript(mid) is None:
                print(f"  [{m.date}] {mid[:8]}... no transcript yet, skipping")
                continue

            print(f"  [{m.date}] {mid[:8]}... summarizing...", flush=True)
            try:
                summaries = summarize_meeting(mid, extractor)
                save_summaries(mid, summaries)
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

    if summarized > 0:
        print(f"\nPushing {summarized} new summary file(s)...")
        push_summaries_branch()

    print(
        f"\nFinished: {summarized} summarized, {skipped} already cached, "
        f"{errors} errors"
    )
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
