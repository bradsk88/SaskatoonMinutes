"""Count roster attractors: names the summary asserts that no source contains.

The cleanup pass is told to "CORRECT garbled proper nouns to the closest
match from this list" — ``_SASKATOON_NAMES``.  Every name on that list is
therefore an attractor: a garbled token can be snapped onto it whether or
not the real word was ever that name.  This script finds where that
happened.

A **roster attractor** is an entry of ``_SASKATOON_NAMES`` that appears in
a published summary but appears in *neither* the item's official text nor
its raw transcript slice.  Nothing in the sources says that name, so the
summary is the first place it exists.

The raw arm should score 0 by construction — it never sees a corrected
transcript.  If it doesn't, the harness is wrong before cleanup is.

**This script flags; it does not score.**  A correct fix and a bad
substitution have the same signature:

    Meewasin      <- ASR "Me was in"   good
    Remai Modern  <- ASR "Remly"       bad (a condo corporation, Rumely)

Edit distance cannot separate them — "Remly" is closer to "Remai" than
"Me was in" is to "Meewasin", so a distance rule scores the known failure
as the success.  Each hit is printed beside its nearest raw-transcript
tokens for a person to rule on.

    python scripts/roster_attractors.py --pairs .eval/ab-cleanup

Costs nothing: it reads the pairs the A/B already wrote.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import unicodedata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.item_categorizer import _SASKATOON_NAMES  # noqa: E402

TITLES = ("Mayor ", "Councillor ")
# Single words too common to be evidence of anything on their own.  A
# summary saying "Ford" is not claiming Councillor Scott Ford.
AMBIGUOUS_ALONE = {
    "block", "ford", "parker", "hill", "pearce", "clark", "davies",
    "gough", "kirton", "timon", "dubois", "jeffries", "donauer",
    "macdonald", "kelleher", "loewen", "gersher", "pearce",
}


def fold(text: str) -> str:
    """Lowercase, strip accents — so Métis matches Metis."""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower()


def roster_terms() -> list[str]:
    """The list as the cleanup prompt presents it, one term per entry."""
    terms: list[str] = []
    for chunk in re.split(r"[,.]", _SASKATOON_NAMES):
        entry = chunk.strip()
        if not entry:
            continue
        for title in TITLES:
            if entry.startswith(title):
                entry = entry[len(title):]
        terms.append(entry)
        # A person is usually named by surname alone in a chip.
        parts = entry.split()
        if len(parts) > 1 and parts[-1].lower() not in AMBIGUOUS_ALONE:
            terms.append(parts[-1])
    seen, out = set(), []
    for term in terms:
        if fold(term) not in seen:
            seen.add(fold(term))
            out.append(term)
    return out


def mentions(term: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(fold(term))}\b", fold(text)) is not None


def summary_text(arm: dict) -> str:
    parts = [arm.get("description") or ""]
    parts += [chip["text"] for chip in arm.get("chips", [])]
    return "\n".join(parts)


def nearest_raw(term: str, transcript: str, count: int = 3) -> list[str]:
    """Raw tokens closest to the flagged name — context for the human ruling.

    Matched over windows of the same word count as the term, because the
    interesting garbles span word boundaries ("Me was in" -> Meewasin).
    """
    words = re.findall(r"[A-Za-z'\-]+", transcript)
    width = len(term.split())
    windows = {
        " ".join(words[i:i + width])
        for i in range(max(0, len(words) - width + 1))
    }
    return difflib.get_close_matches(term, sorted(windows), n=count, cutoff=0.6)


def scan(pairs: list[dict], key: dict) -> list[dict]:
    terms = roster_terms()
    hits = []
    for pair in pairs:
        sources = f"{pair.get('recommendation') or ''}\n{pair.get('content') or ''}"
        transcript = pair.get("transcript") or ""
        for label in ("A", "B"):
            arm = key.get(pair["key"], {}).get(label, label)
            text = summary_text(pair[label])
            for term in terms:
                if not mentions(term, text):
                    continue
                if mentions(term, sources) or mentions(term, transcript):
                    continue
                hits.append({
                    "key": pair["key"],
                    "section": pair["section_number"],
                    "title": pair["title"],
                    "arm": arm,
                    "term": term,
                    "quote": next(
                        (
                            line for line in text.splitlines()
                            if mentions(term, line)
                        ),
                        "",
                    ),
                    "nearest_raw": nearest_raw(term, transcript),
                })
    return dedupe(hits)


def dedupe(hits: list[dict]) -> list[dict]:
    """One flag per substitution.

    "Remai Modern" and "Modern" are the same event seen twice, because the
    roster contributes both the entry and its last word.  Keep the longer.
    """
    out = []
    for hit in hits:
        covered = any(
            other is not hit
            and other["key"] == hit["key"]
            and other["arm"] == hit["arm"]
            and len(other["term"]) > len(hit["term"])
            and fold(hit["term"]) in fold(other["term"])
            for other in hits
        )
        if not covered:
            out.append(hit)
    return out


def render(hits: list[dict], pairs: list[dict]) -> str:
    by_arm = {"clean": 0, "raw": 0}
    for hit in hits:
        by_arm[hit["arm"]] = by_arm.get(hit["arm"], 0) + 1
    out = [
        "# Roster attractors",
        "",
        "Entries of `_SASKATOON_NAMES` that a summary asserts and no source "
        "contains — not the official recommendation, not the agenda notes, "
        "not the raw transcript slice.",
        "",
        f"- Items compared: **{len(pairs)}**",
        f"- Flagged in the **cleaned** arm: **{by_arm.get('clean', 0)}**",
        f"- Flagged in the **raw** arm: **{by_arm.get('raw', 0)}** "
        "(expected 0 — the raw arm never sees a corrected transcript, so "
        "anything here is either a harness fault or the chip model doing "
        "its own name correction without cleanup's help)",
        "",
        "Each flag is a *substitution*, not an error. A person rules on each: "
        "`Meewasin <- \"Me was in\"` is a correction, `Remai Modern <- "
        "\"Remly\"` is a fabrication, and they look identical from here.",
        "",
    ]
    if not hits:
        out.append("No flags.")
        return "\n".join(out)
    for hit in hits:
        out.extend([
            "---",
            "",
            f"## {hit['term']} — {hit['arm']} arm",
            "",
            f"- item: {hit['section']} {hit['title']} (`{hit['key']}`)",
            f"- summary says: {hit['quote'].strip()}",
            "- nearest raw transcript tokens: "
            + (", ".join(f"`{c}`" for c in hit["nearest_raw"]) or "*none close*"),
            "- ruling: **?**  (correction / fabrication)",
            "",
        ])
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=".eval/ab-cleanup")
    parser.add_argument("--out", default=None, help="write markdown here")
    args = parser.parse_args()

    pairs = json.load(open(os.path.join(args.pairs, "pairs.json")))
    key = json.load(open(os.path.join(args.pairs, "key.json")))
    hits = scan(pairs, key)
    report = render(hits, pairs)

    out = args.out or os.path.join(args.pairs, "roster-attractors.md")
    with open(out, "w") as handle:
        handle.write(report + "\n")
    json.dump(
        hits,
        open(os.path.join(args.pairs, "roster-attractors.json"), "w"),
        indent=2,
    )
    print(report)
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
