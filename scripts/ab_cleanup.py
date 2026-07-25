#!/usr/bin/env python3
"""A/B the transcript cleanup pass: is it worth 99% of the backfill cost?

Cleanup emits every item's transcript slice through Gemini before the chip
call — roughly 68k output tokens for one council meeting, ~15M across the
226-meeting term.  That is very nearly the entire cost of a backfill, and
it is spent on an assumption nobody has measured: ADR 0004 keeps cleanup
for proper-noun correction alone.

This script extracts every eligible fixture item **twice** — once from the
cached CleanTranscript, once from the raw transcript slice — and writes
the pairs out blind, labelled A and B, for a human or a sub-agent to
judge without knowing which arm is which.  If the raw arm holds up,
cleanup and its cache can be deleted and the backfill gets ~100x cheaper.

Deliberately not judged by Gemini.  The question is whether a Gemini
preprocessing step earns its cost; asking the same model family to grade
its own preprocessing invites exactly the bias we are testing for.

    python scripts/ab_cleanup.py --out .eval/ab-cleanup

Cost: two chip calls per item (~11 items), no cleanup calls — the
CleanTranscripts are committed alongside the fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.cache import LocalDirCache  # noqa: E402
from app.clean_transcript_cache import CleanTranscriptCache  # noqa: E402
from app.item_categorizer import (  # noqa: E402
    GeminiExtractor,
    _cleanup_chunks,
    cleanup_fingerprint,
    extract_item_summaries,
    is_eligible_for_summary,
)
from scripts.eval_chips import (  # noqa: E402
    CLEAN_SUFFIX,
    FIXTURE_DIR,
    load_fixtures,
)

WORKERS = 8

ARMS = ("clean", "raw")


def raw_slice(item: dict, segments: list[dict]) -> str:
    """The transcript slice exactly as cleanup would have received it.

    ``_cleanup_chunks`` is the same splitter cleanup uses, so the raw arm
    differs from the clean arm in one respect only: whether each chunk
    made a round trip through Gemini.
    """
    return " ".join(_cleanup_chunks(item, segments))


def blind_orders(keys: list[str]) -> dict[str, tuple[str, str]]:
    """Assign arms to labels A and B, balanced across the whole run.

    An independent coin flip per item is the obvious approach and the
    wrong one at this sample size: eleven flips land 9-2 often enough to
    matter, and a judge grading a set that is nine-tenths one arm is
    barely blinded.  Assignment alternates over the sorted keys instead,
    so the split is even by construction, with the starting side derived
    from the key set — stable across re-runs (a judgement recorded
    against "item 41, A" keeps meaning the same thing) and not
    predictable from the item alone.
    """
    ordered = sorted(keys)
    digest = hashlib.sha1("".join(ordered).encode()).digest()[0]
    return {
        key: ((ARMS[1], ARMS[0]) if (rank + digest) % 2 else ARMS)
        for rank, key in enumerate(ordered)
    }


def collect(extractor: GeminiExtractor) -> tuple[list[dict], dict[str, int]]:
    """Extract every eligible fixture item under both arms.

    Returns the pairs plus a count of what was skipped and why — an item
    with no transcript (a Consent Item) has identical arms and would
    dilute the comparison with guaranteed ties.
    """
    clean_cache = CleanTranscriptCache(
        cleanup_fingerprint(),
        inner=LocalDirCache(FIXTURE_DIR, suffix=CLEAN_SUFFIX),
    )
    pairs: list[dict] = []
    skipped = {"no_transcript": 0, "no_clean_cache": 0}

    for mid, detail, segments in load_fixtures():
        items = [i for i in detail["agenda_items"] if is_eligible_for_summary(i)]
        if not items:
            continue

        cached = clean_cache.load(mid)
        if cached is None:
            # Refuse to re-clean.  Re-cleaning here would spend the very
            # tokens this script exists to question, and silently: the
            # run would look like it worked and cost hours.
            skipped["no_clean_cache"] += len(items)
            print(
                f"  {mid[:8]}: no CleanTranscript for this cleanup "
                f"fingerprint — skipping {len(items)} items. Run "
                f"scripts/eval_chips.py first to populate the cache.",
                file=sys.stderr,
            )
            continue

        jobs = []
        for item in items:
            raw = raw_slice(item, segments)
            clean = cached.get(str(item["item_id"]), "")
            if not raw.strip():
                skipped["no_transcript"] += 1
                continue
            jobs.append((item, {"clean": clean, "raw": raw}))

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            def extract(job):
                item, sources = job
                return item, {
                    arm: extract_item_summaries(
                        item, segments,
                        gemini_extractor=extractor,
                        cleaned_transcript_text=text,
                    )
                    for arm, text in sources.items()
                }

            for item, payloads in pool.map(extract, jobs):
                pairs.append({
                    "key": f"{mid}/{item['item_id']}",
                    "section_number": item.get("section_number") or "",
                    "title": item.get("title") or "",
                    "recommendation": (item.get("recommendation") or "").strip(),
                    "content": (item.get("content") or "").strip(),
                    "transcript": raw_slice(item, segments),
                    "_payloads": payloads,
                })

    # Blinded only once every item is known, so the A/B split can be
    # balanced across the run rather than flipped item by item.
    orders = blind_orders([p["key"] for p in pairs])
    for pair in pairs:
        first, second = orders[pair["key"]]
        payloads = pair.pop("_payloads")
        pair["A"] = _summary(payloads[first])
        pair["B"] = _summary(payloads[second])
        pair["_key"] = {"A": first, "B": second}
    return pairs, skipped


def _summary(payload: dict) -> dict:
    return {
        "description": payload.get("description"),
        "chips": [
            {"category": c["category"], "text": c["text"]}
            for c in payload.get("chips") or []
        ],
    }


TRANSCRIPT_WRAP = 100


def wrap_transcript(text: str) -> str:
    """Hard-wrap the transcript so a judge can actually read it.

    A transcript slice arrives as one unbroken line.  On the longest
    fixture item that line is ~34k tokens, which exceeds a single-file
    read cap: all three blind judges hit it, and two of them fell back to
    judging that item on the official text alone.  The evidence was in
    the file and unreachable, which is worse than absent — the run looks
    complete and quietly rests on less than it claims.
    """
    return "\n".join(
        textwrap.fill(line, width=TRANSCRIPT_WRAP) if line.strip() else line
        for line in text.splitlines() or [""]
    )


def render_pairs(pairs: list[dict]) -> str:
    """The blind document a judge reads.  Carries no arm labels."""
    out = [
        "# Cleanup A/B — blind pairs",
        "",
        "Each item below was summarized twice from the same meeting, by the "
        "same model and prompt. The two summaries differ only in how the "
        "transcript was pre-processed. You are not told which is which, and "
        "the difference is not necessarily visible.",
        "",
        "For each item, judge **A vs B** on:",
        "",
        "- **faithfulness** — does every claim trace to the source below?",
        "- **specificity** — concrete numbers, names, places over generalities",
        "- **proper nouns** — are names and places spelled plausibly and "
        "consistently with the official text?",
        "",
        "Answer per item: `A`, `B`, or `tie`, with one sentence of reason. "
        "`tie` is the right answer when the two are equally good — do not "
        "manufacture a preference.",
        "",
    ]
    for pair in pairs:
        out.extend([
            "---",
            "",
            f"## {pair['section_number']} {pair['title']}".strip(),
            "",
            "### Source",
            "",
            f"**Official recommendation:** {pair['recommendation'] or '(none)'}",
            "",
            f"**Agenda notes:** {pair['content'] or '(none)'}",
            "",
            "**Transcript:**",
            "",
            "```",
            wrap_transcript(pair["transcript"]),
            "```",
            "",
        ])
        for label in ("A", "B"):
            summary = pair[label]
            out.extend([f"### {label}", "", summary["description"] or "(no description)", ""])
            for chip in summary["chips"]:
                out.append(f"- **{chip['category']}** — {chip['text']}")
            out.append("")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default=os.path.join(PROJECT_ROOT, ".eval", "ab-cleanup"),
        help="directory for pairs.md (blind), pairs.json, and key.json",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    extractor = GeminiExtractor()
    if not extractor.enabled:
        # Both arms would fall back to the deterministic extractors and
        # produce identical output, which reads as "cleanup makes no
        # difference" — the conclusion this script is meant to earn.
        sys.exit("GEMINI_API_KEY is not set: both arms would be identical.")

    pairs, skipped = collect(extractor)
    if not pairs:
        sys.exit("No comparable items — every fixture item was skipped.")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "pairs.json"), "w") as f:
        json.dump([{k: v for k, v in p.items() if k != "_key"} for p in pairs], f, indent=2)
    with open(os.path.join(args.out, "key.json"), "w") as f:
        json.dump({p["key"]: p["_key"] for p in pairs}, f, indent=2)
    with open(os.path.join(args.out, "pairs.md"), "w") as f:
        f.write(render_pairs(pairs))

    identical = sum(1 for p in pairs if p["A"] == p["B"])
    print(f"{len(pairs)} comparable items -> {args.out}/pairs.md")
    print(f"  byte-identical under both arms: {identical}")
    for reason, count in skipped.items():
        if count:
            print(f"  skipped ({reason}): {count}")
    print("\nJudge pairs.md WITHOUT reading key.json, then unblind.")


if __name__ == "__main__":
    main()
