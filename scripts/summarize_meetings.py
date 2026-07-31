#!/usr/bin/env python3
"""
Extract categorized per-item summaries from cached transcripts and store
them on the ``summaries`` orphan branch.

For each meeting tab, walks the most recent meetings and summarizes any
that have a cached transcript but no existing summaries file.  Meetings
already covered are skipped before any extraction work runs.

Usage:
    python scripts/summarize_meetings.py [--limit 2] [--tabs council ...]

Backfilling the current council term (see
``docs/plans/2026-07-25-001-summary-quality-plan.md``, U7):

    python scripts/summarize_meetings.py \
        --since 2024-11-01 --pages 3 --limit 30 --force

Meetings whose cached summaries are already in the current format are
skipped; the pre-aggregate ones are re-summarized.  So the backfill can
be dispatched repeatedly and walks forward each time, and ``--force`` is
reserved for redoing a meeting that is already current.  Meetings older
than the current term keep their Legacy ItemSummary on purpose: they are
outside the scope this plan set out to fix, and re-summarizing them costs
a chip call each.
"""

import argparse
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

# Chip calls are I/O-bound, so run a meeting's items concurrently.
#
# Four, not eight: eight workers can trip Gemini's per-minute request cap
# on their own while the daily quota is still fine, and the run then
# spends its time waiting out a limit it created.  Halving this is a
# cheaper fix than retrying harder.
EXTRACT_WORKERS = 4

os.environ.setdefault("PYTHONUNBUFFERED", "1")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.cache_git import PushAccessError, verify_push_access
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.item_categorizer import (
    extract_item_summaries,
    item_transcript_text,
    is_eligible_for_summary,
    ExtractionFailed,
    GeminiExtractor,
    QuotaExhausted,
)
from app.item_summaries_cache import ItemSummariesCache
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS
from app.models import ItemSummary
from app.transcript_cache import TranscriptCache


def is_current(cached: dict[str, ItemSummary] | None) -> bool:
    """True when *cached* holds summaries in the current ItemSummary format.

    The backfill's skip rule.  Asking "does a summary exist?" cannot run
    the backfill at all: every in-term meeting already carries the
    pre-aggregate chip list, so the run needed ``--force``, which turned
    the skip off entirely and made each dispatch redo the meetings the
    last one paid for instead of moving on.

    ``any``, not ``all``: an *ineligible* item is stored as an empty
    summary with no description, so a meeting is judged by whether
    anything in it cleared the current bar.  A meeting summarized without
    a Gemini key has no descriptions anywhere and is correctly treated as
    not current — that run produced degraded output and should be redone.
    """
    if not cached:
        return False
    # A provisional summary was written before the meeting, from official
    # text alone.  Once the meeting has happened it is disposable: the
    # flip to Meeting regenerates everything with the transcript
    # (ADR 0021), so provisional coverage does not count as current.
    return any(
        not summary.is_legacy and not summary.provisional
        for summary in cached.values()
    )


def summarize_meeting(
    source: MeetingSource,
    meeting_id: str,
    extractor,
    transcript_cache,
) -> dict[str, ItemSummary]:
    """Run the extractor across every eligible agenda item in the meeting."""
    transcript = transcript_cache.load(meeting_id)
    if not transcript or not transcript.segments:
        return {}
    print(f"    Transcript has {len(transcript.segments)} segments", flush=True)
    detail = source.load_detail(meeting_id)
    items = [it.to_dict() for it in detail.agenda_items]
    transcript_segments = transcript.to_dict()

    eligible = [i for i in items if is_eligible_for_summary(i)]
    # Ineligible items get an empty summary so the page can tell "nothing
    # to summarize here" apart from "not summarized yet".
    summaries: dict[str, ItemSummary] = {
        str(i["item_id"]): ItemSummary(description=None, chips=[])
        for i in items if not is_eligible_for_summary(i)
    }

    def run(item: dict) -> tuple[dict, dict]:
        try:
            return item, extract_item_summaries(
                item, transcript_segments,
                gemini_extractor=extractor,
                transcript_text=item_transcript_text(item, transcript_segments),
            )
        except ExtractionFailed as exc:
            # Name the item — the caller only sees the meeting, and "one
            # item failed" is not actionable without knowing which.
            raise ExtractionFailed(
                f"item {item.get('item_id')} "
                f"({(item.get('title') or '')[:60]!r}): {exc}"
            ) from exc

    missing_description = 0
    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        for item, payload in pool.map(run, eligible):
            summary = ItemSummary.from_dict(payload)
            title = (item.get("title") or "")[:60]
            cats = [c.category for c in summary.chips]
            print(f"    Item {item['item_id']}: {title}", flush=True)
            print(
                f"      → {'description + ' if summary.description else 'NO DESCRIPTION, '}"
                f"{len(summary.chips)} chips: {cats}",
                flush=True,
            )
            if summary.description is None:
                missing_description += 1
            summaries[str(item["item_id"])] = summary

    print(f"    {len(eligible)}/{len(items)} items eligible for summary", flush=True)
    if missing_description:
        # Worth seeing, but not an error: every item here got an answer
        # from the model and the model had nothing worth writing.  A call
        # that *failed* never reaches this line — it raised.
        print(
            f"    NOTE: {missing_description}/{len(eligible)} items — the model "
            f"answered but offered no description",
            flush=True,
        )
    return summaries


def summarize_scheduled_meeting(
    source: MeetingSource,
    meeting_id: str,
    extractor,
) -> dict[str, ItemSummary]:
    """Provisional summaries for a Scheduled Meeting: official text only.

    No transcript exists, so every item is summarized Consent-Item-style
    — the ``scheduled`` flag on the item dicts is what routes the
    extractor to the future-meeting prompt and withholds the
    discussion-only categories.  Every entry is marked provisional so the
    flip to Meeting regenerates it (ADR 0021).
    """
    detail = source.load_detail(meeting_id)
    items = [it.to_dict() for it in detail.agenda_items]
    for item in items:
        item["scheduled"] = True

    summaries: dict[str, ItemSummary] = {
        str(i["item_id"]): ItemSummary(description=None, chips=[], provisional=True)
        for i in items if not is_eligible_for_summary(i)
    }

    def run(item: dict) -> tuple[dict, dict]:
        try:
            return item, extract_item_summaries(
                item, [],
                gemini_extractor=extractor,
                transcript_text="",
            )
        except ExtractionFailed as exc:
            raise ExtractionFailed(
                f"item {item.get('item_id')} "
                f"({(item.get('title') or '')[:60]!r}): {exc}"
            ) from exc

    eligible = [i for i in items if is_eligible_for_summary(i)]
    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        for item, payload in pool.map(run, eligible):
            summary = ItemSummary.from_dict(payload)
            summary = ItemSummary(
                description=summary.description,
                chips=summary.chips,
                provisional=True,
            )
            title = (item.get("title") or "")[:60]
            print(
                f"    Item {item['item_id']}: {title} → "
                f"{'description' if summary.description else 'NO DESCRIPTION'}, "
                f"{len(summary.chips)} chips",
                flush=True,
            )
            summaries[str(item["item_id"])] = summary

    print(
        f"    {len(eligible)}/{len(items)} items eligible for provisional summary",
        flush=True,
    )
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
        help="Re-summarize meetings whose summaries are already current.",
    )
    parser.add_argument(
        "--since", default=None, metavar="YYYY-MM-DD",
        help=(
            "Only process meetings on or after this date.  The backfill "
            "scope is the current council term: --since 2024-11-01.  Older "
            "meetings keep their Legacy ItemSummary -- they are outside "
            "the scope of the summary-quality work."
        ),
    )
    parser.add_argument(
        "--pages", type=int, default=1,
        help="How many pages of past meetings to walk per tab (default: 1).",
    )
    parser.add_argument(
        "--no-scheduled", action="store_true",
        help=(
            "Skip Scheduled Meetings.  By default, once post-meeting "
            "summaries are caught up, remaining quota goes to provisional "
            "summaries for upcoming meetings (lower priority — ADR 0021)."
        ),
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

    # Checked before any work: the caches push on exit, so a credential
    # failure discovered at the end costs the whole run's tokens and
    # discards everything it produced.
    try:
        verify_push_access("summaries")
    except PushAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Run this where credentials exist — the summarize.yml workflow "
            "has contents: write — or authenticate this shell first.",
            file=sys.stderr,
        )
        sys.exit(2)

    summarized = 0
    skipped = 0
    errors = 0
    quota_gone = False

    source: MeetingSource = EscribeMeetingSource(LiveEscribeTransport())
    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache:
        for tab in tabs:
            if quota_gone:
                break
            slug = tab["slug"]
            print(f"\n--- {tab['label']} ({slug}) ---")
            meetings = []
            try:
                for page in range(1, args.pages + 1):
                    batch, _ = source.list_past(
                        page=page, meeting_type=tab["type"],
                    )
                    if not batch:
                        break
                    meetings.extend(batch)
            except Exception as exc:
                print(f"  Failed to fetch meetings: {exc}")
                continue

            processed_this_tab = 0
            for m in meetings:
                if args.since and (m.date or "") < args.since:
                    continue
                if processed_this_tab >= args.limit:
                    break
                if not m.has_video:
                    continue

                mid = m.meeting_id
                if not args.force and is_current(summaries_cache.load(mid)):
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
                        source, mid, extractor, transcript_cache,
                    )
                    summaries_cache.save(mid, summaries)
                    summarized += 1
                    processed_this_tab += 1
                    counted = sum(
                        1 for s in summaries.values()
                        if s.description or s.chips
                    )
                    print(
                        f"    Done: {counted}/{len(summaries)} items have chips",
                        flush=True,
                    )
                except QuotaExhausted as exc:
                    # Every remaining call in this run would fail the same
                    # way.  Stop here rather than spending the rest of the
                    # 350-minute budget producing nothing.
                    print(f"    STOPPING: {exc}", flush=True)
                    errors += 1
                    quota_gone = True
                    break
                except ExtractionFailed as exc:
                    # Not saved on purpose: a meeting with an unknown item
                    # is not a summarized meeting.  is_current rejects the
                    # absent file, so the next run redoes it.
                    errors += 1
                    print(f"    ERROR: not saved — {exc}", flush=True)
                except Exception as exc:
                    errors += 1
                    print(f"    ERROR: {exc}", flush=True)
                    traceback.print_exc()

        # Provisional summaries for Scheduled Meetings — lower priority
        # than everything above: only run when post-meeting work finished
        # without exhausting the quota (ADR 0021).
        if not args.no_scheduled and not quota_gone:
            from datetime import date, timedelta
            start = date.today().isoformat()
            end = (date.today() + timedelta(days=60)).isoformat()
            print("\n--- Scheduled Meetings (provisional) ---")
            try:
                scheduled = source.list_scheduled(start, end)
            except Exception as exc:
                print(f"  Failed to fetch scheduled meetings: {exc}")
                scheduled = []
            for s in scheduled:
                if not s.has_agenda:
                    continue
                mid = s.meeting_id
                if not args.force and is_current(summaries_cache.load(mid)):
                    continue
                print(f"  [{s.date}] {s.body}: summarizing...", flush=True)
                try:
                    summaries = summarize_scheduled_meeting(source, mid, extractor)
                    summaries_cache.save(mid, summaries)
                    summarized += 1
                except QuotaExhausted as exc:
                    print(f"    STOPPING: {exc}", flush=True)
                    errors += 1
                    quota_gone = True
                    break
                except ExtractionFailed as exc:
                    errors += 1
                    print(f"    ERROR: not saved — {exc}", flush=True)
                except Exception as exc:
                    errors += 1
                    print(f"    ERROR: {exc}", flush=True)
                    traceback.print_exc()

    print(
        f"\nFinished: {summarized} summarized, {skipped} already cached, "
        f"{errors} errors"
    )
    if quota_gone:
        print(
            "\nERROR: the run stopped early — the daily Gemini quota is "
            "gone.  Meetings it did not reach were not written, so the "
            "next run picks them up unchanged.",
            file=sys.stderr,
        )
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
