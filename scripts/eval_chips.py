#!/usr/bin/env python3
"""
Run the chip extractor over committed meeting fixtures and report on the
quality of what comes out.

The fixtures in ``tests/fixtures/eval`` are real agenda items and real
transcript slices, trimmed to a handful of items so a full run costs a
few seconds and a dozen Gemini calls.  No network, no cache, no pushes —
the only outbound traffic is the LLM pass, and that only when
``GEMINI_API_KEY`` is set.

    python scripts/eval_chips.py             # print a full report
    python scripts/eval_chips.py --diff      # print only what changed
    python scripts/eval_chips.py --snapshot   # commit this run as the baseline
    python scripts/eval_chips.py --check     # fail on regressions

``--check`` is what CI runs.  It asserts the properties that were silently
lost in May 2026: that the LLM pass produces soft chips at all, and that
chips say something the agenda item's title doesn't already say.

``--diff`` is the iteration loop.  Reviewing a handful of deltas against
``tests/fixtures/eval/baseline.json`` is tractable in a way that re-reading
every summary each pass is not; unchanged items cost one line of footer.
Because CleanTranscripts are cached beside the fixtures, a diff run spends
nothing on the cleanup pass.
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

# Chip calls are I/O-bound; run them concurrently so a fixture sweep is
# seconds rather than minutes.
EXTRACT_WORKERS = 8

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.cache import LocalDirCache  # noqa: E402
from app.clean_transcript_cache import CleanTranscriptCache  # noqa: E402
from app.item_categorizer import (  # noqa: E402
    CATEGORIES,
    SEMANTIC_CATEGORIES,
    GeminiExtractor,
    clean_meeting_transcripts,
    cleanup_fingerprint,
    extract_item_summaries,
    is_eligible_for_summary,
)

FIXTURE_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "eval")

# CleanTranscripts are committed alongside the fixtures they belong to,
# so an eval run costs only the chip calls.  They regenerate whenever the
# cleanup prompt changes (the fingerprint stops matching) — and because
# they live in the repo, what cleanup produced is reviewable in a diff.
CLEAN_SUFFIX = ".clean.json"

# Minimum share of items that must carry at least one soft (LLM) chip
# before we call the semantic pass healthy.  Deliberately low: some items
# genuinely have nothing interpretive to say.  Zero, however, means broken.
MIN_SOFT_COVERAGE = 0.5

# A chip that merely restates the title is filler.  Above this share of
# all chips, the summary is a title echo rather than a summary.
MAX_TITLE_ECHO = 0.35


def load_fixtures() -> list[tuple[str, dict, list[dict]]]:
    """Return ``(meeting_id, detail, transcript_segments)`` per fixture."""
    out = []
    for name in sorted(os.listdir(FIXTURE_DIR)):
        if not name.endswith(".detail.json"):
            continue
        mid = name[: -len(".detail.json")]
        with open(os.path.join(FIXTURE_DIR, name)) as f:
            detail = json.load(f)
        with open(os.path.join(FIXTURE_DIR, f"{mid}.transcript.json")) as f:
            segments = json.load(f)
        out.append((mid, detail, segments))
    return out


def _normalize(text: str) -> str:
    """Lowercase, drop file/report numbers and punctuation, collapse space."""
    text = re.sub(r"\[(?:file no\.?|cc)[^\]]*\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def is_title_echo(chip_text: str, title: str) -> bool:
    """True when the chip adds nothing beyond the item's own title."""
    chip_n, title_n = _normalize(chip_text), _normalize(title)
    if not chip_n or not title_n:
        return False
    # Strip a leading deterministic prefix like "Approved: " before comparing.
    chip_body = re.sub(r"^(?:\w+ )?(?:approved|recommended|defeated|referred|"
                       r"deferred|adopted|discussed)\b[: ]*", "", chip_n).strip()
    if not chip_body:
        return False
    if chip_body == title_n or chip_body in title_n or title_n in chip_body:
        return True
    chip_words = set(chip_body.split())
    title_words = set(title_n.split())
    if not chip_words:
        return False
    return len(chip_words - title_words) / len(chip_words) < 0.25


class Report:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.items = 0
        self.chips = 0
        self.items_with_soft = 0
        self.title_echoes = 0
        self.categories: dict[str, int] = {}

    def add_item(self, mid: str, item: dict, chips: list[dict]) -> None:
        self.items += 1
        title = item.get("title") or ""
        # A soft chip that only restates the title is the metadata fallback
        # wearing an LLM category's name — it doesn't count as coverage.
        if any(
            c["category"] in SEMANTIC_CATEGORIES and not is_title_echo(c["text"], title)
            for c in chips
        ):
            self.items_with_soft += 1
        self.rows.append(
            f"\n### {item.get('section_number', '')} {title[:70]}\n"
            f"<sub>{mid[:8]} · item {item['item_id']}</sub>\n"
        )
        if not chips:
            self.rows.append("_no chips_\n")
        for c in chips:
            self.chips += 1
            self.categories[c["category"]] = self.categories.get(c["category"], 0) + 1
            echo = is_title_echo(c["text"], title)
            if echo:
                self.title_echoes += 1
            flag = " ⚠️ title echo" if echo else ""
            self.rows.append(f"- **{c['category']}** — {c['text']}{flag}")

    @property
    def soft_coverage(self) -> float:
        return self.items_with_soft / self.items if self.items else 0.0

    @property
    def echo_share(self) -> float:
        return self.title_echoes / self.chips if self.chips else 0.0

    def render(self, gemini_enabled: bool) -> str:
        lines = [
            "## Chip quality eval",
            "",
            f"- LLM pass: **{'enabled' if gemini_enabled else 'disabled (no key)'}**",
            f"- Items: **{self.items}** · chips: **{self.chips}** "
            f"({self.chips / self.items:.1f} per item)" if self.items else "- no items",
            f"- Items with a substantive soft chip: "
            f"**{self.items_with_soft}/{self.items}** "
            f"({self.soft_coverage:.0%})",
            f"- Title-echo chips: **{self.title_echoes}/{self.chips}** "
            f"({self.echo_share:.0%})",
            "",
            "| category | count |",
            "|---|---|",
        ]
        for cat in CATEGORIES:
            if cat in self.categories:
                soft = " *(soft)*" if cat in SEMANTIC_CATEGORIES else ""
                lines.append(f"| {cat}{soft} | {self.categories[cat]} |")
        lines.append("")
        lines.append("## Chips")
        lines.extend(self.rows)
        return "\n".join(lines)


def run_eval(extractor: GeminiExtractor) -> dict:
    """Extract chips for every eligible fixture item.

    Returns ``{"<meeting_id>/<item_id>": {...}}`` — a flat mapping so a
    snapshot diffs cleanly and item identity survives fixtures being
    added or reordered.
    """
    clean_cache = CleanTranscriptCache(
        cleanup_fingerprint(),
        inner=LocalDirCache(FIXTURE_DIR, suffix=CLEAN_SUFFIX),
    )
    results: dict = {}
    for mid, detail, segments in load_fixtures():
        items = [i for i in detail["agenda_items"] if is_eligible_for_summary(i)]
        if not items:
            continue

        cached = clean_cache.load(mid)
        if cached is None and extractor.enabled:
            print(
                f"  {mid[:8]}: cleanup fingerprint changed or missing — "
                f"re-cleaning {len(items)} items",
                file=sys.stderr,
            )
        clean = clean_meeting_transcripts(items, segments, extractor, cached=cached)
        if clean != (cached or {}):
            clean_cache.save(mid, clean)

        with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
            extracted = list(pool.map(
                lambda it: (it, extract_item_summaries(
                    it, segments,
                    gemini_extractor=extractor,
                    cleaned_transcript_text=clean[str(it["item_id"])],
                )),
                items,
            ))
        for item, chips in extracted:
            results[f"{mid}/{item['item_id']}"] = {
                "section_number": item.get("section_number") or "",
                "title": item.get("title") or "",
                "chips": [
                    {"category": c["category"], "text": c["text"]} for c in chips
                ],
            }
    return results


def build_report(results: dict) -> Report:
    report = Report()
    for key, entry in results.items():
        mid = key.split("/", 1)[0]
        item = {
            "item_id": key.split("/", 1)[1],
            "title": entry["title"],
            "section_number": entry["section_number"],
        }
        report.add_item(mid, item, entry["chips"])
    return report


# ── Baseline snapshot / diff ─────────────────────────────────────────────────

BASELINE_PATH = os.path.join(FIXTURE_DIR, "baseline.json")


def load_baseline() -> dict | None:
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_baseline(results: dict) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def _by_category(chips: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in chips:
        out.setdefault(c["category"], []).append(c["text"])
    return out


def render_diff(baseline: dict, current: dict) -> str:
    """Render only what changed, grouped per item and per category.

    Reviewing deltas is the point: an unchanged item costs one line of
    footer, not a screenful of chips we have already read.
    """
    lines: list[str] = ["## Chip diff vs baseline", ""]
    changed = 0
    gained: dict[str, int] = {}
    lost: dict[str, int] = {}
    reworded = 0
    for key in sorted(set(baseline) | set(current)):
        old = baseline.get(key)
        new = current.get(key)
        if old is None:
            changed += 1
            lines.append(f"### + NEW {new['section_number']} {new['title'][:60]}")
            for c in new["chips"]:
                lines.append(f"+ **{c['category']}** — {c['text']}")
            lines.append("")
            continue
        if new is None:
            changed += 1
            lines.append(
                f"### − GONE {old['section_number']} {old['title'][:60]}"
            )
            lines.append("")
            continue

        old_cats, new_cats = _by_category(old["chips"]), _by_category(new["chips"])
        rows: list[str] = []
        for cat in sorted(set(old_cats) | set(new_cats), key=_category_sort):
            before, after = old_cats.get(cat, []), new_cats.get(cat, [])
            if before == after:
                continue
            # A category appearing or vanishing is a structural change; the
            # same category with different words is the model paraphrasing
            # itself.  Both are worth showing, but only the first is
            # usually why we ran the eval.
            if not before:
                gained[cat] = gained.get(cat, 0) + 1
                mark = "  ⟵ new category"
            elif not after:
                lost[cat] = lost.get(cat, 0) + 1
                mark = "  ⟵ category lost"
            else:
                reworded += 1
                mark = ""
            for text in before:
                if text not in after:
                    rows.append(f"- **{cat}** — {text}")
            for text in after:
                if text not in before:
                    rows.append(f"+ **{cat}** — {text}{mark}")
        if not rows:
            continue
        changed += 1
        lines.append(f"### {new['section_number']} {new['title'][:60]}")
        lines.extend(rows)
        lines.append("")

    unchanged = len(set(baseline) & set(current)) - sum(
        1 for k in set(baseline) & set(current)
        if baseline[k]["chips"] != current[k]["chips"]
    )
    lines.append(f"**{changed} items changed, {unchanged} unchanged**")
    if gained:
        lines.append(
            "- categories gained: "
            + ", ".join(f"{c} ×{n}" for c, n in sorted(gained.items()))
        )
    if lost:
        lines.append(
            "- categories lost: "
            + ", ".join(f"{c} ×{n}" for c, n in sorted(lost.items()))
        )
    if reworded:
        lines.append(
            f"- {reworded} chips reworded within the same category "
            f"(model paraphrase, not a structural change)"
        )
    return "\n".join(lines)


def _category_sort(cat: str) -> tuple[int, str]:
    return (CATEGORIES.index(cat) if cat in CATEGORIES else len(CATEGORIES), cat)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero when quality thresholds are not met.",
    )
    parser.add_argument(
        "--diff", action="store_true",
        help="Print only what changed against the committed baseline.",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        help="Overwrite the committed baseline with this run's output.",
    )
    args = parser.parse_args()

    # Loaded here rather than at import scope: importing this module must
    # not put a live API key into os.environ, or a unit test that merely
    # imports it starts making real Gemini calls.
    from dotenv import load_dotenv

    # Local runs keep the key in .env; CI passes it in the environment.
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    extractor = GeminiExtractor()
    results = run_eval(extractor)
    report = build_report(results)

    if args.diff:
        baseline = load_baseline()
        if baseline is None:
            print(
                f"No baseline at {os.path.relpath(BASELINE_PATH, PROJECT_ROOT)} — "
                f"run with --snapshot first.",
                file=sys.stderr,
            )
            sys.exit(2)
        text = render_diff(baseline, results)
    else:
        text = report.render(extractor.enabled)

    print(text)
    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(text + "\n")

    if args.snapshot:
        save_baseline(results)
        print(
            f"\nBaseline written to "
            f"{os.path.relpath(BASELINE_PATH, PROJECT_ROOT)} "
            f"({len(results)} items)"
        )

    if not args.check:
        return

    failures: list[str] = []
    if report.items == 0:
        failures.append("no eligible items in the fixtures")
    if extractor.enabled and report.soft_coverage < MIN_SOFT_COVERAGE:
        failures.append(
            f"soft-chip coverage {report.soft_coverage:.0%} is below "
            f"{MIN_SOFT_COVERAGE:.0%} — the LLM pass is not producing chips"
        )
    if report.echo_share > MAX_TITLE_ECHO:
        failures.append(
            f"{report.echo_share:.0%} of chips merely restate the item title "
            f"(limit {MAX_TITLE_ECHO:.0%})"
        )
    if failures:
        print("\nFAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    main()
