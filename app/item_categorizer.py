"""
Hybrid extractor that turns an agenda item + its transcript slice into a
list of short category-chip summaries.

Categories are a closed list of 23 labels.  Extraction runs in two passes:

  1. Deterministic pass — regex and heuristics over the item's metadata and
     transcript text.  Covers Outcome, Vote Breakdown, Cost & Funding,
     Amendment Made, Procedural Note, Delegation, Next Step, Related Item,
     Deferred From, Declared Conflict, Data Cited.

  2. Semantic pass — sentence-transformers (all-MiniLM-L6-v2, CPU, ~80 MB)
     encodes every transcript sentence and every remaining category's
     representative query; the best sentence above a per-category cosine
     threshold becomes the summary text for that category.

All summaries are trimmed to <=60 characters at a word boundary.

The module has no hard dependency on sentence-transformers at import time —
the model is lazy-loaded.  Tests can inject a stub encoder via the
``encoder`` parameter of ``extract_item_summaries``.
"""

from __future__ import annotations

import math
import re

from app.summarizer import (
    _PROCEDURAL_KEYWORDS,
    _clean_entities,
    _format_money,
    _format_outcome,
    _is_procedural,
)
from app.transcriber import _section_number_patterns


# ── Categories ──────────────────────────────────────────────────────────────

CATEGORIES: list[str] = [
    "Outcome",
    "Vote Breakdown",
    "Amendment Made",
    "Cost & Funding",
    "In Plain Terms",
    "Delegation",
    "Debate Highlight",
    "Who's Affected",
    "Staff vs. Council",
    "Next Step",
    "Declared Conflict",
    "Procedural Note",
    "Related Item",
    "Deferred From",
    "Precedent Set",
    "Unanswered Question",
    "Data Cited",
    "Public Sentiment",
    "Dissenting View",
    "Legal Risk Flagged",
    "Equity Impact",
    "Environmental Impact",
    "Promise Made",
]

CATEGORY_GROUP: dict[str, str] = {
    "Outcome": "decision",
    "Vote Breakdown": "decision",
    "Amendment Made": "decision",
    "Dissenting View": "decision",
    "Cost & Funding": "money",
    "In Plain Terms": "context",
    "Debate Highlight": "context",
    "Data Cited": "context",
    "Unanswered Question": "context",
    "Delegation": "voices",
    "Staff vs. Council": "voices",
    "Public Sentiment": "voices",
    "Declared Conflict": "voices",
    "Who's Affected": "impact",
    "Equity Impact": "impact",
    "Environmental Impact": "impact",
    "Legal Risk Flagged": "impact",
    "Procedural Note": "future",
    "Related Item": "future",
    "Deferred From": "future",
    "Precedent Set": "future",
    "Next Step": "future",
    "Promise Made": "future",
}

_CATEGORY_ORDER: dict[str, int] = {c: i for i, c in enumerate(CATEGORIES)}

MAX_SUMMARY_CHARS = 60

# Query strings embedded once and compared against transcript sentences.
SEMANTIC_QUERIES: dict[str, str] = {
    "In Plain Terms": "a plain-language explanation of what this item actually does",
    "Debate Highlight": "a sharp or notable moment of disagreement during debate",
    "Who's Affected": "which residents, neighbourhoods, or groups will be directly affected",
    "Staff vs. Council": "a disagreement between city administration and elected councillors",
    "Precedent Set": "setting a first-of-its-kind precedent for future decisions",
    "Unanswered Question": "a question raised by a councillor that went unanswered",
    "Public Sentiment": "members of the public expressing support or opposition",
    "Dissenting View": "a councillor explaining why they voted against the motion",
    "Legal Risk Flagged": "legal liability or litigation risk being raised",
    "Equity Impact": "impact on marginalized, low-income, or under-served groups",
    "Environmental Impact": "environmental, ecological, or emissions impact",
    "Promise Made": "a public commitment or promise from council or staff",
}

# Below this cosine similarity we assume the category did not really appear.
_SEMANTIC_THRESHOLD = 0.50


# ── Transcript slicing ──────────────────────────────────────────────────────


def _slice_transcript(
    segments: list[dict], item: dict
) -> list[dict]:
    """Return transcript segments that overlap [item.start, item.end]."""
    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return []
    sliced = []
    for seg in segments:
        seg_start = seg.get("start_ms", 0)
        seg_end = seg.get("end_ms", seg_start)
        if seg_end < start or seg_start > end:
            continue
        sliced.append(seg)
    return sliced


def _split_sentences(text: str) -> list[str]:
    """Tokenize free-form transcript text into sentence-like chunks."""
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    # Split on sentence terminators followed by whitespace.
    parts = re.split(r"(?<=[.!?])\s+", text)
    cleaned = []
    for p in parts:
        p = p.strip()
        if 12 <= len(p) <= 300:
            cleaned.append(p)
    return cleaned


# ── Truncation ──────────────────────────────────────────────────────────────

_FILLER_LEADS = re.compile(
    r"^(?:um+,?\s+|uh+,?\s+|well,?\s+|so,?\s+|i think\s+|you know,?\s+|"
    r"and\s+|but\s+|okay,?\s+|right,?\s+)+",
    re.IGNORECASE,
)


def _trim_to_chip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Trim text to a chip-sized string at a word boundary."""
    text = _clean_entities(text)
    text = _FILLER_LEADS.sub("", text)
    text = text.strip().strip(",;:")
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    sp = cut.rfind(" ")
    if sp > 10:
        cut = cut[:sp]
    return cut.rstrip(",;:") + "…"


# ── Deterministic extractors ────────────────────────────────────────────────


def _extract_outcome(item: dict) -> list[dict]:
    vote = item.get("vote_result") or ""
    rec = item.get("recommendation") or ""
    outcome = _format_outcome(vote, rec)
    if not outcome or outcome == "Discussed":
        return []
    return [{"category": "Outcome", "text": _trim_to_chip(outcome)}]


_VOTE_TALLY_RE = re.compile(
    r"In\s+Favour:\s*\((\d+)\).*?Against:\s*\((\d+)\)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_vote_breakdown(item: dict) -> list[dict]:
    detail = item.get("vote_detail") or ""
    m = _VOTE_TALLY_RE.search(detail)
    if not m:
        # Fall back to "(N to M)" embedded in vote_result
        vote = item.get("vote_result") or ""
        m2 = re.search(r"\((\d+)\s*(?:to|-)\s*(\d+)\)", vote)
        if not m2:
            return []
        for_n, against_n = int(m2.group(1)), int(m2.group(2))
    else:
        for_n, against_n = int(m.group(1)), int(m.group(2))
    total = for_n + against_n
    if total == 0:
        return []
    text = f"{for_n} for, {against_n} against"
    return [{"category": "Vote Breakdown", "text": _trim_to_chip(text)}]


_AMENDMENT_RE = re.compile(
    r"\b(amend(?:ed|ment|ing)?)\b[^.]*",
    re.IGNORECASE,
)


def _extract_amendment(item: dict) -> list[dict]:
    combined = " ".join(
        str(item.get(k) or "") for k in ("motion_text", "vote_result", "recommendation")
    )
    if not re.search(r"\bamend(?:ed|ment|ing)?\b", combined, re.IGNORECASE):
        return []
    m = _AMENDMENT_RE.search(combined)
    text = m.group(0).strip() if m else "Amended at the meeting"
    return [{"category": "Amendment Made", "text": _trim_to_chip(text)}]


_MONEY_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K|thousand))?",
    re.IGNORECASE,
)


def _extract_cost_funding(item: dict, transcript_text: str) -> list[dict]:
    combined = " ".join(
        str(item.get(k) or "")
        for k in ("title", "recommendation", "motion_text", "content")
    ) + " " + transcript_text
    results: list[dict] = []
    seen: set[str] = set()
    for m in _MONEY_RE.finditer(combined):
        raw = m.group(0)
        formatted = _format_money(raw)
        # Extract a tiny purpose snippet: 3-6 words after the amount
        tail = combined[m.end(): m.end() + 80]
        purpose = _money_purpose_snippet(tail)
        label = f"{formatted} {purpose}".strip() if purpose else formatted
        label = _trim_to_chip(label)
        if label in seen:
            continue
        seen.add(label)
        results.append({"category": "Cost & Funding", "text": label})
        if len(results) >= 3:
            break
    return results


_PURPOSE_RE = re.compile(
    r"\s*(?:for|to(?:\s+the)?|toward(?:s)?)\s+([a-z][\w\s-]{2,40}?)"
    r"(?=[.,;]|\s+(?:and|that|which)\b|$)",
    re.IGNORECASE,
)


def _money_purpose_snippet(tail: str) -> str:
    m = _PURPOSE_RE.match(tail)
    if not m:
        return ""
    return "for " + m.group(1).strip()


def _extract_declared_conflict(transcript_text: str) -> list[dict]:
    m = re.search(
        r"[^.!?]*\b(declare[ds]?|declaration of|conflict of interest)\b[^.!?]*",
        transcript_text, re.IGNORECASE,
    )
    if not m:
        return []
    return [{"category": "Declared Conflict", "text": _trim_to_chip(m.group(0))}]


_DELEGATION_RE = re.compile(
    r"(Director|Mr\.?|Ms\.?|Mrs\.?|Dr\.?)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
    r"\s+(?:presented|spoke|addressed|appeared|responded)"
    r"[^.!?]{0,80}",
)


def _extract_delegation(transcript_text: str) -> list[dict]:
    m = _DELEGATION_RE.search(transcript_text[:4000])
    if not m:
        return []
    return [{"category": "Delegation", "text": _trim_to_chip(m.group(0))}]


_NEXT_STEP_RE = re.compile(
    r"[^.!?]*\b(?:report back|bring (?:this |it )?back|return to council|"
    r"next meeting|next year|by (?:Q[1-4]|\d{4}|the end of))\b[^.!?]*",
    re.IGNORECASE,
)


def _extract_next_step(transcript_text: str) -> list[dict]:
    for m in _NEXT_STEP_RE.finditer(transcript_text):
        text = m.group(0).strip()
        if re.match(r"^if\b", text, re.IGNORECASE):
            continue
        return [{"category": "Next Step", "text": _trim_to_chip(text)}]
    return []


def _extract_related_deferred(item: dict, transcript_text: str) -> list[dict]:
    """Detect cross-references to other agenda items and prior deferrals."""
    results: list[dict] = []
    if re.search(
        r"\b(previously deferred|deferred from|referred back from|"
        r"postponed from)\b",
        transcript_text, re.IGNORECASE,
    ):
        m = re.search(
            r"[^.!?]*\b(previously deferred|deferred from|referred back from|"
            r"postponed from)\b[^.!?]*",
            transcript_text, re.IGNORECASE,
        )
        if m:
            results.append(
                {"category": "Deferred From", "text": _trim_to_chip(m.group(0))}
            )
    own = (item.get("section_number") or "").rstrip(".")
    for m in re.finditer(
        r"\bitem\s+(\d+(?:\.\d+){1,3})\b",
        transcript_text, re.IGNORECASE,
    ):
        ref = m.group(1)
        if ref == own:
            continue
        snippet = transcript_text[max(0, m.start() - 30): m.end() + 30]
        results.append(
            {"category": "Related Item", "text": _trim_to_chip(snippet)}
        )
        break
    return results


def _extract_procedural_note(item: dict) -> list[dict]:
    title = item.get("title") or ""
    if not _is_procedural(title):
        return []
    match_kw = next(
        (kw for kw in _PROCEDURAL_KEYWORDS if kw in title.lower()), "procedural"
    )
    return [{"category": "Procedural Note", "text": _trim_to_chip(match_kw.title())}]


_DATA_RE = re.compile(
    r"[^.!?]*\b\d+(?:,\d{3})*(?:\.\d+)?\s*"
    r"(?:%|percent|km|kilometres|kilometers|metres|meters|hectares|"
    r"units|residents|households|people|jobs|trips)"
    r"[^.!?]*",
    re.IGNORECASE,
)


def _extract_data_cited(transcript_text: str) -> list[dict]:
    m = _DATA_RE.search(transcript_text)
    if not m:
        return []
    return [{"category": "Data Cited", "text": _trim_to_chip(m.group(0))}]


# ── Semantic pass ───────────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Encoder:
    """Abstract encoder; implementations return list[list[float]] vectors."""

    def encode(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError


class MiniLMEncoder(Encoder):
    """Real encoder backed by sentence-transformers MiniLM."""

    _model = None

    def _load(self):
        if MiniLMEncoder._model is None:
            from sentence_transformers import SentenceTransformer

            MiniLMEncoder._model = SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2"
            )
        return MiniLMEncoder._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        arr = model.encode(
            texts, convert_to_numpy=True, show_progress_bar=False,
        )
        return [list(map(float, row)) for row in arr]


_TRAILING_JUNK_RE = re.compile(
    r"\s+(?:by|and|the|to|for|of|in|a|an|that|which|with|from|on|or|but|as|"
    r"their|our|its|is|are|was|were|has|have|had|this|it|we|they|some|"
    r"about|into|over|at|than|not|all|no)…$"
)

_MIN_SEMANTIC_CHIP_LEN = 25


def _extract_semantic(
    sentences: list[str], encoder: Encoder, exclude: set[str],
) -> list[dict]:
    """Run semantic matching for categories not in *exclude*."""
    if not sentences:
        return []
    target_cats = [c for c in SEMANTIC_QUERIES if c not in exclude]
    if not target_cats:
        return []

    queries = [SEMANTIC_QUERIES[c] for c in target_cats]
    # Encode queries + sentences in a single batch for efficiency.
    vectors = encoder.encode(queries + sentences)
    query_vecs = vectors[: len(queries)]
    sent_vecs = vectors[len(queries):]

    # Build scored list per category, then pick best without reusing sentences.
    cat_best: list[tuple[str, float, str]] = []
    for cat, qv in zip(target_cats, query_vecs):
        best_score = 0.0
        best_sentence = ""
        for sent, sv in zip(sentences, sent_vecs):
            score = _cosine(qv, sv)
            if score > best_score:
                best_score = score
                best_sentence = sent
        cat_best.append((cat, best_score, best_sentence))

    # Sort by score descending so higher-confidence categories claim first.
    cat_best.sort(key=lambda t: t[1], reverse=True)

    results: list[dict] = []
    used_sentences: set[str] = set()
    for cat, score, sent in cat_best:
        if score < _SEMANTIC_THRESHOLD or not sent:
            continue
        if sent in used_sentences:
            continue
        chip = _trim_to_chip(sent)
        if len(chip) < _MIN_SEMANTIC_CHIP_LEN:
            continue
        if _TRAILING_JUNK_RE.search(chip):
            continue
        used_sentences.add(sent)
        results.append({"category": cat, "text": chip})
    return results


# ── Public API ──────────────────────────────────────────────────────────────


def _is_unanimous_tally(item: dict) -> bool:
    """True when vote detail or result shows 0 dissenting votes."""
    detail = item.get("vote_detail") or ""
    m = _VOTE_TALLY_RE.search(detail)
    if m and int(m.group(2)) == 0:
        return True
    vote = item.get("vote_result") or ""
    m2 = re.search(r"\((\d+)\s*(?:to|-)\s*(\d+)\)", vote)
    if m2 and int(m2.group(2)) == 0:
        return True
    return False


def is_eligible_for_summary(item: dict) -> bool:
    """Only non-consent, non-procedural, non-brief items are worth analyzing."""
    if item.get("timestamp_inherited"):
        return False
    if item.get("is_recess"):
        return False
    title = item.get("title") or ""
    if _is_procedural(title):
        return False
    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return False
    if end - start < 60_000:
        return False
    return True


def extract_item_summaries(
    item: dict,
    transcript_segments: list[dict],
    encoder: Encoder | None = None,
) -> list[dict]:
    """Extract category chip summaries for a single agenda item.

    Returns a list of ``{"category": str, "text": str}`` sorted by the
    canonical 23-category order.  The same category may appear more than
    once (e.g. multiple Cost & Funding entries).
    """
    slice_segments = _slice_transcript(transcript_segments, item)
    transcript_text = " ".join(s.get("text", "") for s in slice_segments)

    results: list[dict] = []
    results.extend(_extract_outcome(item))
    results.extend(_extract_vote_breakdown(item))
    results.extend(_extract_amendment(item))
    results.extend(_extract_cost_funding(item, transcript_text))
    results.extend(_extract_declared_conflict(transcript_text))
    results.extend(_extract_delegation(transcript_text))
    results.extend(_extract_next_step(transcript_text))
    results.extend(_extract_related_deferred(item, transcript_text))
    results.extend(_extract_procedural_note(item))
    results.extend(_extract_data_cited(transcript_text))

    covered = {r["category"] for r in results}

    # Suppress logically impossible semantic categories.
    vote = (item.get("vote_result") or "").upper()
    if "UNANIM" in vote or _is_unanimous_tally(item):
        covered.add("Dissenting View")

    sentences = _split_sentences(transcript_text)
    if sentences:
        enc = encoder or MiniLMEncoder()
        results.extend(_extract_semantic(sentences, enc, exclude=covered))

    results.sort(key=lambda r: _CATEGORY_ORDER.get(r["category"], 999))
    return results
