#!/usr/bin/env python3
"""Score both arms of the cleanup A/B with the Gemini judge.

A fallback, not the intended method. `scripts/ab_cleanup.py` is built to
be judged blind by a human or a non-Gemini sub-agent, because asking the
model family whose preprocessing step is on trial to grade that step
invites the bias being tested for. Read the verdict here with that
discount applied.

What makes it *usable* despite the bias: the judge scores each summary
independently, on an absolute rubric, without seeing the other arm and
without being told cleanup exists. It is not asked "which is better",
which is the question a biased grader answers badly.

Both arms are judged against the **raw** transcript, never the cleaned
one. Judging a cleaned-arm summary against its own cleaned input would
make cleanup's own errors invisible: a proper noun that cleanup invented
would read as fully supported. Raw is the ground truth for both.

    python scripts/ab_judge.py --pairs .eval/ab-cleanup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.summary_judge import SummaryJudge  # noqa: E402
from scripts.eval_chips import _source_material, load_fixtures  # noqa: E402

WORKERS = 8
DIMENSIONS = ("faithfulness", "specificity", "non_redundancy")


def pair_up(scored: dict[str, dict]) -> tuple[dict[str, dict], int]:
    """Keep only items scored under BOTH arms; report how many were dropped.

    The first run lost one arm of one item to a failed call and averaged
    the surviving arm in anyway.  That handed the survivor a free score
    with nothing to compare against, and it was enough to reverse the
    sign of the headline faithfulness delta on a 9-item sample.  An
    unpaired item carries no comparison and must not vote.
    """
    paired = {k: v for k, v in scored.items() if len(v) == 2}
    return paired, len(scored) - len(paired)


def _sources() -> dict[str, dict]:
    """Map ``meeting_id/item_id`` to the agenda item it came from.

    The first version of this script built the judge's source from the
    three fields ``pairs.json`` happens to carry — recommendation, agenda
    notes, transcript — and left out ``motion_text``, ``vote_result`` and
    ``vote_detail``.  Every description that said the body approved
    something was then unsupported by construction, and faithfulness
    collapsed to 1 for both arms: the harness was measuring its own
    missing fields.  Reusing the eval's ``_source_material`` keeps the
    two scripts honest about what "the source" means.
    """
    return {
        f"{mid}/{item['item_id']}": item
        for mid, detail, _ in load_fixtures()
        for item in detail["agenda_items"]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=os.path.join(PROJECT_ROOT, ".eval", "ab-cleanup"))
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    with open(os.path.join(args.pairs, "pairs.json")) as f:
        pairs = json.load(f)
    with open(os.path.join(args.pairs, "key.json")) as f:
        key = json.load(f)

    judge = SummaryJudge()
    if not judge.enabled:
        sys.exit("GEMINI_API_KEY is not set.")
    # Force the lazy client to exist before the pool touches it: eight
    # threads racing to build it produced "client has been closed".
    judge.judge(title="warmup", source="warmup", description=None, chips=[])

    items = _sources()
    jobs = [
        (pair, label)
        for pair in pairs
        for label in ("A", "B")
    ]

    def score(job):
        pair, label = job
        summary = pair[label]
        item = items.get(pair["key"])
        verdict = judge.judge(
            title=pair["title"],
            # Both arms judged against the RAW transcript — see module
            # docstring.  Cleanup's own inventions must not be able to
            # vouch for themselves.
            source=_source_material(item or {}, pair.get("transcript", "")),
            description=summary.get("description"),
            chips=summary.get("chips") or [],
        )
        return pair["key"], label, verdict

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(score, jobs))

    scored: dict[str, dict[str, dict]] = {}
    failed = 0
    for item_key, label, verdict in results:
        if verdict is None:
            # A failed call is not a zero.  Averaging it in as one would
            # be inventing evidence against whichever arm the network
            # happened to fail on.
            failed += 1
            continue
        scored.setdefault(item_key, {})[key[item_key][label]] = verdict

    paired, dropped = pair_up(scored)

    per_item = [
        {"key": k, "arm": arm, **{d: v[arm].get(d) for d in DIMENSIONS}}
        for k, v in paired.items() for arm in ("clean", "raw")
    ]

    print(f"Judged {len(results) - failed}/{len(results)} summaries"
          + (f" ({failed} calls failed)" if failed else ""))
    print(f"Comparing {len(paired)} items scored under BOTH arms"
          + (f"; dropped {dropped} half-scored item(s)" if dropped else ""))
    print()
    print(f"{'dimension':<16} {'clean':>8} {'raw':>8} {'delta':>8}")
    for dimension in DIMENSIONS:
        values = {
            arm: [v[arm][dimension] for v in paired.values()
                  if isinstance(v[arm].get(dimension), int)]
            for arm in ("clean", "raw")
        }
        if not values["clean"] or not values["raw"]:
            continue
        c = sum(values["clean"]) / len(values["clean"])
        r = sum(values["raw"]) / len(values["raw"])
        print(f"{dimension:<16} {c:>8.2f} {r:>8.2f} {c - r:>+8.2f}")

    wins = {"clean": 0, "raw": 0, "tie": 0}
    for v in paired.values():
        c, r = v["clean"].get("faithfulness"), v["raw"].get("faithfulness")
        if not isinstance(c, int) or not isinstance(r, int):
            continue
        wins["clean" if c > r else "raw" if r > c else "tie"] += 1
    print(f"\nFaithfulness head-to-head: clean={wins['clean']} "
          f"raw={wins['raw']} tie={wins['tie']}")

    print("\nPer item (arm: faithfulness/specificity/non-redundancy):")
    for item_key in sorted({p["key"] for p in per_item}):
        row = {p["arm"]: p for p in per_item if p["key"] == item_key}
        parts = " ".join(
            f"{arm}={row[arm]['faithfulness']}/{row[arm]['specificity']}"
            f"/{row[arm]['non_redundancy']}"
            for arm in ("clean", "raw") if arm in row
        )
        print(f"  {item_key.split('/')[-1]:>4}  {parts}")

    with open(os.path.join(args.pairs, "judged.json"), "w") as f:
        json.dump(per_item, f, indent=2)


if __name__ == "__main__":
    main()
