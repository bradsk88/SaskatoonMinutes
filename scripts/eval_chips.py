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
A run costs one chip call per item and nothing else.
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

from app.summary_judge import (  # noqa: E402
    FAITHFULNESS_CONCERN,
    FAITHFULNESS_FLOOR,
    MIN_MEAN_FAITHFULNESS,
    MIN_MEAN_SPECIFICITY,
    SummaryJudge,
)
from app.item_categorizer import (  # noqa: E402
    CATEGORIES,
    CONTINUATION_OPENERS,
    MAX_DESCRIPTION_CHARS,
    SEMANTIC_CATEGORIES,
    GeminiExtractor,
    extract_item_summaries,
    item_transcript_text,
    is_eligible_for_summary,
)

FIXTURE_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "eval")

# Minimum share of items that must carry at least one soft (LLM) chip
# before we call the semantic pass healthy.  Deliberately low: some items
# genuinely have nothing interpretive to say.  Zero, however, means broken.
MIN_SOFT_COVERAGE = 0.5

# A chip that merely restates the title is filler.  Above this share of
# all chips, the summary is a title echo rather than a summary.
MAX_TITLE_ECHO = 0.35

# A bullet that continues the one above it is one sentence chopped up.
# The rule is not negotiable, but the gate is a share rather than zero,
# because the generator is a sampled model: prompt work took this from
# 4/24 to 1/24 and no wording takes it reliably to 0/24.  A gate that
# goes red on one unlucky sample is a gate people learn to ignore.  The
# count is printed on every run whether it fails or not.
#
# Repairing them in code is not the way out — see the note beside
# CONTINUATION_OPENERS for what a string join did to faithfulness.
MAX_DESCRIPTION_CONTINUATION = 0.10


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


# A description that merely names its subject is fine -- "the Saskatoon
# Homelessness Action Plan 2026" is what the thing is called.  What is not
# fine is a description that adds nothing beyond the title.  So novelty is
# measured by word content, not by whether the title appears verbatim,
# which is what is_title_echo (a chip heuristic) checks.
MIN_DESCRIPTION_NOVELTY = 0.5


def as_bullets(description) -> list[str]:
    """The description as a bullet list, whatever shape it arrived in."""
    if isinstance(description, str):
        description = [description]
    if not isinstance(description, list):
        return []
    return [b.strip() for b in description if isinstance(b, str) and b.strip()]


def continuation_bullets(bullets: list[str]) -> list[str]:
    """Bullets after the first that continue the bullet above them."""
    return [
        b for b in bullets[1:]
        if b.lower().startswith(CONTINUATION_OPENERS)
    ]


def is_description_echo(description, title: str) -> bool:
    """True when the description says little the title didn't already say."""
    desc_n, title_n = _normalize(" ".join(as_bullets(description))), _normalize(title)
    if not desc_n or not title_n:
        return False
    desc_words = desc_n.split()
    if not desc_words:
        return False
    title_words = set(title_n.split())
    novel = [w for w in desc_words if w not in title_words]
    return len(novel) / len(desc_words) < MIN_DESCRIPTION_NOVELTY


class Report:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.items = 0
        self.chips = 0
        self.items_with_soft = 0
        self.title_echoes = 0
        self.categories: dict[str, int] = {}
        self.descriptions = 0
        self.missing_descriptions = 0
        self.description_echoes = 0
        self.description_overruns = 0
        self.description_continuations = 0
        self.bullets = 0

    def add_item(
        self, mid: str, item: dict, chips: list[dict],
        description=None,
    ) -> None:
        self.items += 1
        title = item.get("title") or ""
        bullets = as_bullets(description)
        continued = continuation_bullets(bullets)
        if bullets:
            self.descriptions += 1
            self.bullets += len(bullets)
            if is_description_echo(bullets, title):
                self.description_echoes += 1
            # Measured over the whole description, all bullets together:
            # the reader's cost is the block, not the line.
            if sum(len(b) for b in bullets) > MAX_DESCRIPTION_CHARS:
                self.description_overruns += 1
            if continued:
                self.description_continuations += 1
        else:
            self.missing_descriptions += 1
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
        if bullets:
            echo = " ⚠️ title echo" if is_description_echo(bullets, title) else ""
            for bullet in bullets:
                mark = " ⚠️ continues the bullet above" if bullet in continued else ""
                self.rows.append(f"> - {bullet}{mark}")
            self.rows.append(f"{echo}\n")
        else:
            self.rows.append("> _**no description**_\n")
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
    def continuation_share(self) -> float:
        return (
            self.description_continuations / self.descriptions
            if self.descriptions else 0.0
        )

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
            f"- Descriptions: **{self.descriptions}/{self.items}** "
            f"· missing: **{self.missing_descriptions}** "
            f"· title echoes: **{self.description_echoes}** "
            f"· over {MAX_DESCRIPTION_CHARS} chars: **{self.description_overruns}**",
            f"- Bullets: **{self.bullets}** "
            f"({self.bullets / self.descriptions:.1f} per description) "
            f"· continuation bullets: **{self.description_continuations}**"
            if self.descriptions else "- no descriptions",
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
    results: dict = {}
    for mid, detail, segments in load_fixtures():
        items = [i for i in detail["agenda_items"] if is_eligible_for_summary(i)]
        if not items:
            continue

        transcripts = {
            str(it["item_id"]): item_transcript_text(it, segments)
            for it in items
        }

        with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
            extracted = list(pool.map(
                lambda it: (it, extract_item_summaries(
                    it, segments,
                    gemini_extractor=extractor,
                    transcript_text=transcripts[str(it["item_id"])],
                )),
                items,
            ))
        for item, payload in extracted:
            results[f"{mid}/{item['item_id']}"] = {
                "section_number": item.get("section_number") or "",
                "title": item.get("title") or "",
                # Kept out of the baseline (see save_baseline): it is the
                # judge's input, not part of what we diff.
                "_source": _source_material(
                    item, transcripts[str(item["item_id"])]
                ),
                "description": payload.get("description"),
                "chips": [
                    {"category": c["category"], "text": c["text"]}
                    for c in payload.get("chips") or []
                ],
            }
    return results


def _source_material(item: dict, transcript: str) -> str:
    """Everything the summary was allowed to draw on, for the judge."""
    parts = []
    for label, key in (
        ("Official recommendation", "recommendation"),
        ("Motion text", "motion_text"),
        ("Vote result", "vote_result"),
        ("Vote detail", "vote_detail"),
        ("Agenda notes", "content"),
    ):
        value = (item.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    if transcript.strip():
        parts.append(f"Transcript:\n{transcript}")
    return "\n\n".join(parts)


def _desc_line(description) -> str:
    """A description on one line, for a diff row that is read as a pair."""
    return " · ".join(as_bullets(description)) or "(none)"


def build_report(results: dict) -> Report:
    report = Report()
    for key, entry in results.items():
        mid = key.split("/", 1)[0]
        item = {
            "item_id": key.split("/", 1)[1],
            "title": entry["title"],
            "section_number": entry["section_number"],
        }
        report.add_item(
            mid, item, entry["chips"], description=entry.get("description"),
        )
    return report


# ── Baseline snapshot / diff ─────────────────────────────────────────────────

BASELINE_PATH = os.path.join(FIXTURE_DIR, "baseline.json")


def load_baseline() -> dict | None:
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_baseline(results: dict) -> None:
    # Source material is the judge's input, not part of the diff — it is
    # large, it does not change with the prompt, and committing it would
    # duplicate the fixtures.
    stripped = {
        k: {kk: vv for kk, vv in v.items() if kk != "_source"}
        for k, v in results.items()
    }
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(stripped, f, indent=1, ensure_ascii=False, sort_keys=True)
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
            lines.append(f"+ _description_ — {_desc_line(new.get('description'))}")
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

        rows: list[str] = []
        old_desc = as_bullets(old.get("description"))
        new_desc = as_bullets(new.get("description"))
        if old_desc != new_desc:
            rows.append(f"- _description_ — {_desc_line(old_desc)}")
            rows.append(f"+ _description_ — {_desc_line(new_desc)}")

        old_cats, new_cats = _by_category(old["chips"]), _by_category(new["chips"])
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

    shared = set(baseline) & set(current)
    unchanged = len(shared) - sum(
        1 for k in shared
        if baseline[k]["chips"] != current[k]["chips"]
        or as_bullets(baseline[k].get("description"))
        != as_bullets(current[k].get("description"))
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


# ── LLM-as-judge ─────────────────────────────────────────────────────────────


def run_judge(results: dict) -> dict:
    judge = SummaryJudge()
    if not judge.enabled:
        print("judge: no GEMINI_API_KEY — skipping", file=sys.stderr)
        return {}

    # Only what the model wrote.  The judge's question is "does the
    # source support this sentence?", which is a question about a
    # sentence someone composed.  Outcome and Vote Breakdown are neither
    # composed nor a sentence -- format_outcome derives them from
    # vote_result and the recommendation, and their own unit tests are
    # what checks that derivation.
    #
    # Judging them anyway is what kept this eval red.  The judge grounds
    # a claim against a single field, so it read the correct "First
    # reading passed (9-0)" as unsupported: the tally is in vote_result
    # and the fact that the first-reading motion carried is in the
    # agenda notes, and it will not put the two together.  Three items
    # scored <= 2 on chips that match the record exactly.
    def one(item: tuple):
        key, entry = item
        written = [
            c for c in (entry.get("chips") or [])
            if c["category"] in SEMANTIC_CATEGORIES
        ]
        return key, judge.judge(
            entry["title"], entry.get("_source", ""),
            entry.get("description"), written,
        )

    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        return dict(pool.map(one, results.items()))


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def render_judge(verdicts: dict) -> str:
    scored = {k: v for k, v in verdicts.items() if v}
    if not scored:
        return "\n## Judge\n\n_no verdicts_"
    faith = [v["faithfulness"] for v in scored.values()]
    spec = [v["specificity"] for v in scored.values()]
    nonred = [v["non_redundancy"] for v in scored.values()]
    lines = [
        "",
        "## Judge",
        "",
        f"- Faithfulness: **{_mean(faith):.2f}** (gate {MIN_MEAN_FAITHFULNESS})",
        f"- Specificity: **{_mean(spec):.2f}** (gate {MIN_MEAN_SPECIFICITY})",
        f"- Non-redundancy: **{_mean(nonred):.2f}**",
        f"- Scored: **{len(scored)}/{len(verdicts)}**",
        "",
    ]
    concerns = [
        (k, v) for k, v in scored.items()
        if v["faithfulness"] <= FAITHFULNESS_CONCERN or v["unsupported_claims"]
    ]
    if concerns:
        lines.append("### Flagged")
        for key, v in concerns:
            lines.append(
                f"- `{key}` faith={v['faithfulness']} "
                f"spec={v['specificity']} nonred={v['non_redundancy']}"
            )
            for claim in v["unsupported_claims"]:
                lines.append(f"  - unsupported: {claim}")
            if not v["supporting_quote"]:
                lines.append("  - judge found no supporting span in the source")
    return "\n".join(lines)


def judge_failures(verdicts: dict) -> list[str]:
    scored = {k: v for k, v in verdicts.items() if v}
    if not scored:
        return []
    failures = []
    unscored = len(verdicts) - len(scored)
    if unscored:
        # A judge that did not return is not a pass.
        failures.append(f"{unscored} summaries could not be judged")
    mean_faith = _mean([v["faithfulness"] for v in scored.values()])
    mean_spec = _mean([v["specificity"] for v in scored.values()])
    if mean_faith < MIN_MEAN_FAITHFULNESS:
        failures.append(
            f"mean faithfulness {mean_faith:.2f} is below "
            f"{MIN_MEAN_FAITHFULNESS} — summaries are asserting things "
            f"the source does not support"
        )
    if mean_spec < MIN_MEAN_SPECIFICITY:
        failures.append(
            f"mean specificity {mean_spec:.2f} is below {MIN_MEAN_SPECIFICITY}"
        )
    floor = [k for k, v in scored.items() if v["faithfulness"] <= FAITHFULNESS_FLOOR]
    if floor:
        failures.append(
            f"{len(floor)} summaries scored <= {FAITHFULNESS_FLOOR} on "
            f"faithfulness: {', '.join(sorted(floor)[:5])}"
        )
    return failures


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
    parser.add_argument(
        "--judge", action="store_true",
        help="Score each summary against its source with an LLM judge.",
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

    verdicts: dict = {}
    if args.judge:
        verdicts = run_judge(results)
        judge_text = render_judge(verdicts)
        print(judge_text)
        if step_summary:
            with open(step_summary, "a") as f:
                f.write(judge_text + "\n")

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
    if args.judge:
        failures.extend(judge_failures(verdicts))
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
    # The Description is a required field of the response schema, so a
    # missing one is a contract violation rather than a quality shortfall.
    # It is not allowed to slide.
    if extractor.enabled and report.missing_descriptions:
        failures.append(
            f"{report.missing_descriptions}/{report.items} items have no "
            f"description — it is a required schema field, so this is a "
            f"broken contract, not a soft miss"
        )
    # One fact chopped across several bullets is longer than the
    # paragraph it replaced and says less — it is the reason the bullet
    # count follows the facts instead of a target.
    if report.continuation_share > MAX_DESCRIPTION_CONTINUATION:
        failures.append(
            f"{report.description_continuations}/{report.descriptions} "
            f"descriptions ({report.continuation_share:.0%}) have a bullet "
            f"that continues the one above it — that is one sentence "
            f"chopped up, not several facts "
            f"(limit {MAX_DESCRIPTION_CONTINUATION:.0%})"
        )
    if report.description_echoes:
        failures.append(
            f"{report.description_echoes}/{report.descriptions} descriptions "
            f"restate the item title — that is the failure the mandatory "
            f"description exists to prevent"
        )
    if failures:
        print("\nFAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    main()
