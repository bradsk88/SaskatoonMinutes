"""Audit the chip taxonomy against what recent meetings actually produced.

Run this before adding, removing, or redefining a chip category.  A
category is a subscriber-facing filter vocabulary (the feeds tag entries
with them), so a label that never fires -- or fires on every second
item -- is tag noise a subscriber cannot opt out of.  ADR ``0023`` is
the first audit, done by hand; this script is that session codified.

Data sources, both already on disk after any deploy:

- ``_site/meeting/*.html`` -- the page titles carry meeting dates,
  which the summaries cache does not.  This is how "recent" is defined.
- the ``summaries`` git branch -- per-meeting chips and Descriptions,
  read directly with ``git show`` (read-only; the cache classes are
  for loading through the push machinery, which an audit must not
  touch).

Usage::

    venv/bin/python scripts/audit_categories.py [--months 6]

Prints a frequency table and a list of flags.  Exits 0 regardless --
it is a report to reason with, not a gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.item_categorizer import CATEGORIES  # noqa: E402
from app.summarizer import CARD_CHIP_CATEGORIES  # noqa: E402

# Only these can become feed tags (feeds tag entries from the
# interpretive set), so only they earn the tag-noise check.  Outcome
# and Vote Breakdown fire on most items by design and no subscriber
# ever sees them as a filter.
TAGGABLE = set(CARD_CHIP_CATEGORIES)

_TITLE_DATE_RE = re.compile(r"<title>[^<]*?([A-Z][a-z]+ \d{1,2}, \d{4})")

# The audit's money check: Cost & Funding chips against items whose
# Description carries a figure.  The 2026 audit found 9 chips against
# 114 such items -- a "money" filter missing most money items.
_MONEY_RE = re.compile(r"\$\s?[\d,]")

# A category on more than this share of chipped items is a filter that
# selects "most of the feed".  Who's Affected sat at ~half in 2026 by
# design ("emit whenever identifiable"); the flag exists so that is a
# known cost, not a discovered one.
TAG_NOISE_SHARE = 0.4


def meeting_dates(site_dir: Path) -> dict[str, date]:
    """Meeting id -> date, from built page titles."""
    out: dict[str, date] = {}
    for page in (site_dir / "meeting").glob("*.html"):
        m = _TITLE_DATE_RE.search(page.read_text(encoding="utf8"))
        if not m:
            continue
        try:
            out[page.stem] = datetime.strptime(m.group(1), "%B %d, %Y").date()
        except ValueError:
            continue
    return out


def load_summary(meeting_id: str) -> dict | None:
    """One meeting's cached summaries from the git branch, or None."""
    result = subprocess.run(
        ["git", "show", f"summaries:summaries/{meeting_id}.json"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--site-dir", type=Path, default=PROJECT_ROOT / "_site")
    args = parser.parse_args()

    dated = meeting_dates(args.site_dir)
    if not dated:
        sys.exit(
            f"No dated meeting pages under {args.site_dir}/meeting -- "
            "run a build first."
        )
    newest = max(dated.values())
    cutoff = date.fromordinal(newest.toordinal() - args.months * 31)
    recent = [mid for mid, d in dated.items() if d >= cutoff]

    chips_per_cat: Counter[str] = Counter()
    meetings_per_cat: Counter[str] = Counter()
    unknown_cats: Counter[str] = Counter()
    meetings_with_data = 0
    items_total = 0
    items_chipped = 0
    desc_items = 0
    desc_with_money = 0

    for mid in recent:
        data = load_summary(mid)
        if not data:
            continue
        meetings_with_data += 1
        seen_here = set()
        for value in data.values():
            # New shape: {"description": ..., "chips": [...]}.
            # Legacy shape: a bare chip list -- no Description, but the
            # chips still count.
            if isinstance(value, dict):
                chips = value.get("chips") or []
                desc = value.get("description")
                if isinstance(desc, str):
                    desc = [desc]
            elif isinstance(value, list):
                chips, desc = value, None
            else:
                continue
            items_total += 1
            if chips:
                items_chipped += 1
            if desc:
                desc_items += 1
                if any(_MONEY_RE.search(b) for b in desc):
                    desc_with_money += 1
            for chip in chips:
                if not isinstance(chip, dict):
                    continue
                cat = chip.get("category") or ""
                chips_per_cat[cat] += 1
                seen_here.add(cat)
                if cat not in CATEGORIES:
                    unknown_cats[cat] += 1
        for cat in seen_here:
            meetings_per_cat[cat] += 1

    print(f"Window: {cutoff} to {newest} "
          f"({len(recent)} meetings, {meetings_with_data} with cached summaries)")
    print(f"Items: {items_total} ({items_chipped} with chips, "
          f"{desc_items} with a Description)\n")
    print(f"{'category':<24} {'chips':>6} {'meetings':>9}   status")
    for cat in CATEGORIES:
        n = chips_per_cat.get(cat, 0)
        tag = " [feed tag]" if cat in TAGGABLE else ""
        status = ""
        if n == 0:
            status = ("ZERO FIRINGS -- removal candidate" if cat in TAGGABLE
                      else "never produced -- dead extractor?")
        elif cat in TAGGABLE and items_chipped \
                and n / items_chipped > TAG_NOISE_SHARE:
            status = (f"on {n / items_chipped:.0%} of chipped items -- "
                      "too common to filter on?")
        print(f"{cat:<24} {n:>6} {meetings_per_cat.get(cat, 0):>9}   "
              f"{status}{tag}")
    if unknown_cats:
        print("\nIn the archive but not in CATEGORIES "
              "(stale labels; harmless, unfilterable):")
        for cat, n in unknown_cats.most_common():
            print(f"  {cat}: {n}")

    money_chips = chips_per_cat.get("Cost & Funding", 0)
    print(f"\nMoney check: {money_chips} Cost & Funding chips vs "
          f"{desc_with_money} Descriptions carrying a $ figure.")
    if desc_with_money and money_chips < desc_with_money * 0.5:
        print("  -> under-firing: a money filter misses most money items. "
              "Check both passes (ADR 0023).")


if __name__ == "__main__":
    main()
