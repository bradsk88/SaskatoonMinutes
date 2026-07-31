#!/usr/bin/env python3
"""Add a meeting to ``tests/fixtures/eval`` for the cleanup A/B (U9).

The A/B that decides whether the cleanup pass lives ran at n=9, which is
too thin to retire a subsystem on.  This widens the fixture set — but the
selection has to be mechanical, because the person widening it has
already argued for one outcome.

So items are ranked by **name density**: distinct capitalized tokens in
the item's raw transcript slice that are absent from ``_SASKATOON_NAMES``.
That is exactly the population cleanup acts on.  A garbled proper noun is
either corrected (cleanup's whole claimed value) or snapped onto the
wrong roster member (the ``Remai Modern`` failure), and neither can
happen in an item where nobody is named.

    python scripts/add_eval_fixture.py --tab public-hearing --date 2026-05-27
    python scripts/add_eval_fixture.py --meeting <id> --items 5 --dry-run

The ranking is printed every run, whether or not anything is written, so
the selection is auditable rather than trusted.

Transcripts come from the local ``transcripts`` branch; nothing is
transcribed and nothing is pushed.  A meeting with no cached transcript
is refused rather than silently written as an empty fixture.

Costs nothing.  The new fixtures do have a downstream cost: the next
``eval_chips.py`` run cleans their transcripts, which is the token spend
the A/B exists to price.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.escribe import EscribeMeetingSource, LiveEscribeTransport  # noqa: E402
from app.item_categorizer import (  # noqa: E402
    _slice_transcript,
    is_eligible_for_summary,
)
from app.meeting_types import MEETING_TABS  # noqa: E402
from app.transcript_cache import TranscriptCache  # noqa: E402
from scripts.eval_chips import FIXTURE_DIR  # noqa: E402

DEFAULT_ITEMS = 5

# Capitalization is the only signal available in a raw ASR transcript, and
# it is noisy: the transcriber capitalizes the first word of every
# sentence.  Counting those would rank items by sentence count, not by how
# many people they name, so a token is only a name candidate when
# something other than a sentence boundary capitalized it.
_SENTENCE_START = re.compile(r"[.!?]['\"’”)\]]?\s+$")
_CAPITALIZED = re.compile(r"\b[A-Z][a-zA-Z'’-]+")

# Words that are capitalized mid-sentence by grammar rather than by naming
# anything.  Kept deliberately tiny: every entry here is a judgement call
# about what counts as a name, and this ranking is meant to be mechanical.
_NOT_NAMES = {"I", "I'm", "I've", "I'd", "I'll", "OK"}


# Names the site already knows how to spell, used here as a stop-list so
# the ranking counts *unfamiliar* names — delegates, First Nations, firms.
#
# This is a copy of the roster that used to live in the cleanup prompt.
# It is frozen here on purpose: as prompt input every entry was an
# attractor that a garbled token could be snapped onto, which is what
# produced "Remai Modern" for a condo corporation (ADR `0005`).  As a
# stop-list it is only ever subtracted from a count, never shown to a
# model, so a stale entry costs a slightly worse fixture ranking and
# nothing else.
_KNOWN_NAMES = (
    "Cynthia Block Kathryn MacDonald Senos Timon Robert Pearce Troy Davies "
    "Randy Donauer Jasmin Parker Holly Kelleher Scott Ford Bev Dubois "
    "Zach Jeffries Charlie Clark Darren Hill Hilary Gough David Kirton "
    "Mairin Loewen Sarina Gersher Mayor Councillor "
    "Meewasin Valley Authority Swale Watchers Remai Modern "
    "Métis Cree Dakota Nakota Dene Saulteaux Treaty "
    "Idylwyld Nutana Riversdale Caswell Hill Sutherland Buena Vista "
    "Haultain Stonebridge Willowgrove Blairmore Attridge "
    "Chief Mistawasis Bridge"
)


def _roster_tokens() -> set[str]:
    """Lowercased words of the known-name stop-list, for absence checks."""
    return {w for w in re.split(r"[^A-Za-z]+", _KNOWN_NAMES.lower()) if w}


def name_candidates(text: str, roster: set[str] | None = None) -> set[str]:
    """Distinct capitalized tokens in *text* that the roster does not cover.

    These are the tokens cleanup is told to "correct to the closest match"
    — the ones it can fix and the ones it can wreck.  Roster members are
    excluded because a transcript that already says "Meewasin" gives the
    pass nothing to do.
    """
    roster = _roster_tokens() if roster is None else roster
    found: set[str] = set()
    for match in _CAPITALIZED.finditer(text):
        token = match.group()
        if token in _NOT_NAMES:
            continue
        start = match.start()
        # Only the few characters before the token matter, and a slice can
        # be 100k characters long — re-scanning the whole prefix per match
        # makes ranking quadratic in transcript length.
        if start == 0 or _SENTENCE_START.search(text[max(0, start - 4):start]):
            continue
        if token.lower() in roster:
            continue
        found.add(token)
    return found


def rank_items(items: list[dict], segments: list[dict]) -> list[dict]:
    """Score every eligible item by name density, most dense first.

    Items with no transcript slice score nothing and are reported
    separately: Consent Items inherit their parent section's timestamp, so
    both A/B arms would receive the same empty text and the pair is a
    guaranteed tie.
    """
    roster = _roster_tokens()
    ranked = []
    for item in items:
        if not is_eligible_for_summary(item):
            continue
        text = " ".join(
            s.get("text", "") for s in _slice_transcript(segments, item)
        )
        names = name_candidates(text, roster)
        ranked.append({
            "item": item,
            "names": sorted(names),
            "chars": len(text),
        })
    # item_id breaks ties so re-running picks the same five items.
    ranked.sort(key=lambda r: (-len(r["names"]), r["item"]["item_id"]))
    return ranked


def render_ranking(ranked: list[dict], chosen: list[dict]) -> str:
    """The selection, shown in full — including what was passed over."""
    picked = {id(r) for r in chosen}
    lines = [
        "",
        "| pick | names | chars | item | title |",
        "|---|---|---|---|---|",
    ]
    for row in ranked:
        item = row["item"]
        mark = "✓" if id(row) in picked else ""
        lines.append(
            f"| {mark} | {len(row['names'])} | {row['chars']} "
            f"| {item.get('section_number') or ''} {item['item_id']} "
            f"| {(item.get('title') or '')[:50]} |"
        )
    lines.append("")
    for row in chosen:
        lines.append(
            f"- item {row['item']['item_id']}: {', '.join(row['names'][:12]) or '—'}"
        )
    return "\n".join(lines)


def trim_transcript(segments: list[dict], items: list[dict]) -> list[dict]:
    """Only the segments the chosen items' slices reach.

    Sliced with the same function the summarizer uses, so the fixture
    hands the extractor byte-identical text to what production would have.
    """
    kept: dict[tuple, dict] = {}
    for item in items:
        for seg in _slice_transcript(segments, item):
            kept[(seg["start_ms"], seg["end_ms"], seg["text"])] = seg
    return [kept[k] for k in sorted(kept)]


def resolve_meeting_id(source, tab_slug: str, date: str, pages: int) -> str:
    """Find the meeting on *date* in the *tab_slug* tab."""
    tabs = {t["slug"]: t for t in MEETING_TABS}
    if tab_slug not in tabs:
        raise SystemExit(
            f"unknown tab '{tab_slug}' — one of: "
            f"{', '.join(sorted(tabs))}"
        )
    seen = []
    for page in range(1, pages + 1):
        batch, _ = source.list_past(page=page, meeting_type=tabs[tab_slug]["type"])
        if not batch:
            break
        for meeting in batch:
            seen.append(meeting.date)
            if meeting.date == date:
                return meeting.meeting_id
    raise SystemExit(
        f"no {tab_slug} meeting on {date} in the first {pages} page(s). "
        f"Dates seen: {', '.join(sorted(seen, reverse=True)[:12])}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting", help="eSCRIBE meeting id.")
    parser.add_argument("--tab", help="Tab slug, with --date.")
    parser.add_argument("--date", help="Meeting date YYYY-MM-DD, with --tab.")
    parser.add_argument(
        "--pages", type=int, default=3,
        help="Pages of past meetings to search for --date (default: 3).",
    )
    parser.add_argument(
        "--items", type=int, default=DEFAULT_ITEMS,
        help=f"How many items to keep (default: {DEFAULT_ITEMS}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the ranking and write nothing.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing fixture for this meeting.",
    )
    args = parser.parse_args()

    if not args.meeting and not (args.tab and args.date):
        raise SystemExit("give either --meeting, or --tab with --date")

    source = EscribeMeetingSource(LiveEscribeTransport())
    meeting_id = args.meeting or resolve_meeting_id(
        source, args.tab, args.date, args.pages,
    )

    detail_path = os.path.join(FIXTURE_DIR, f"{meeting_id}.detail.json")
    if os.path.exists(detail_path) and not (args.force or args.dry_run):
        raise SystemExit(
            f"{os.path.relpath(detail_path, PROJECT_ROOT)} already exists — "
            f"pass --force to replace it"
        )

    with TranscriptCache.open() as cache:
        transcript = cache.load(meeting_id)
    if transcript is None or not transcript.segments:
        # Refused rather than written empty: a fixture with no transcript
        # gives both A/B arms the same text and scores as a tie, which
        # reads as "cleanup didn't matter here" instead of "no data".
        raise SystemExit(
            f"no cached transcript for {meeting_id} on the transcripts "
            f"branch — transcribe it first, or pick another meeting"
        )
    segments = transcript.to_dict()

    detail = source.load_detail(meeting_id)
    items = [i.to_dict() for i in detail.agenda_items]
    ranked = rank_items(items, segments)
    with_audio = [r for r in ranked if r["chars"]]
    chosen = with_audio[: args.items]

    print(f"{meeting_id}  ({len(items)} items, {len(segments)} segments)")
    print(
        f"{len(ranked)} eligible, {len(ranked) - len(with_audio)} with no "
        f"transcript slice (excluded — both arms would be identical)"
    )
    print(render_ranking(ranked, chosen))

    if not chosen:
        raise SystemExit("nothing to write: no eligible item has a transcript")

    chosen_items = [r["item"] for r in chosen]
    kept_segments = trim_transcript(segments, chosen_items)

    if args.dry_run:
        print(
            f"dry run — would write {len(chosen_items)} items and "
            f"{len(kept_segments)} segments"
        )
        return

    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(
            {"agenda_items": chosen_items, "video_url": detail.video_url},
            f, indent=1, ensure_ascii=False,
        )
        f.write("\n")
    transcript_path = os.path.join(FIXTURE_DIR, f"{meeting_id}.transcript.json")
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(kept_segments, f, indent=1, ensure_ascii=False)
        f.write("\n")

    print(
        f"\nWrote {os.path.relpath(detail_path, PROJECT_ROOT)} "
        f"({len(chosen_items)} items) and "
        f"{os.path.relpath(transcript_path, PROJECT_ROOT)} "
        f"({len(kept_segments)} segments)"
    )
    print(
        "Next: scripts/eval_chips.py --snapshot to clean the new "
        "transcripts and fold the items into the baseline."
    )


if __name__ == "__main__":
    main()
