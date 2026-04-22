"""
Hybrid extractor that turns an agenda item + its transcript slice into a
list of short category-chip summaries.

Categories are a closed list of 23 labels.  Extraction runs in three
passes when Gemini is enabled (cleanup + deterministic + LLM); just the
deterministic pass otherwise.

  0. Cleanup pass — a single Gemini call that normalizes the rambling,
     filler-laden automatic transcription into well-punctuated sentences
     while preserving every fact, name, number, and quote.

  1. Deterministic pass — regex and heuristics over the item's metadata
     and the cleaned transcript text. Covers Outcome, Vote Breakdown,
     Cost & Funding, Amendment Made, Procedural Note, Delegation, Next
     Step, Related Item, Deferred From, Declared Conflict, Data Cited.

  2. LLM pass — a single Gemini 2.5 Flash call per item, constrained to a
     JSON schema of ``{category, text, usefulness}`` for the 12 remaining
     "soft" categories.

When ``GEMINI_API_KEY`` is unset the cleanup and LLM passes are skipped
and only deterministic chips are emitted.

All summaries are trimmed to <=100 characters at a natural clause break.

Tests can inject a stub extractor via the ``gemini_extractor`` parameter
of ``extract_item_summaries``.
"""

from __future__ import annotations

import json
import os
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

MAX_SUMMARY_CHARS = 100

# Categories handled by the LLM pass.  Each maps to a plain-English
# definition that is included verbatim in the Gemini prompt.
SEMANTIC_DEFINITIONS: dict[str, str] = {
    "In Plain Terms": "a 1-sentence plain-language explanation of what this item actually does, written so a busy resident understands it",
    "Debate Highlight": "a sharp or notable moment of debate — a memorable quote, a pointed exchange, or a striking argument from a named speaker. Do NOT use this category to restate the vote outcome (e.g. 'council unanimously opposed the proposal' belongs to Outcome, not here).",
    "Who's Affected": "which specific residents, neighbourhoods, businesses, or groups are directly affected",
    "Staff vs. Council": "a real disagreement between city administration and elected councillors (not just a clarifying question)",
    "Precedent Set": "a first-of-its-kind decision that will be referenced in future cases",
    "Unanswered Question": "a substantive question raised that was NOT answered (if it was answered or clarified, do NOT emit this)",
    "Public Sentiment": "members of the public expressing clear support or opposition (not just 'public engagement is required')",
    "Dissenting View": "a councillor's stated reason for voting against the motion (omit if the vote was unanimous)",
    "Legal Risk Flagged": "legal liability, lawsuits, or statutory-risk being raised",
    "Equity Impact": "concrete impact on marginalized, low-income, Indigenous, or under-served groups",
    "Environmental Impact": "environmental, ecological, or emissions impact (not just the word 'environment' appearing)",
    "Promise Made": "a specific public commitment or promise from council or staff (not a question or wondering)",
}

SEMANTIC_CATEGORIES: list[str] = list(SEMANTIC_DEFINITIONS.keys())


def _sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence containing text[start:end], using . ! ? as boundaries."""
    sent_start = 0
    for p in ".!?":
        pos = text.rfind(p, 0, start)
        if pos + 1 > sent_start:
            sent_start = pos + 1
    sent_end = len(text)
    for p in ".!?":
        pos = text.find(p, end)
        if pos != -1 and pos < sent_end:
            sent_end = pos + 1
    return text[sent_start:sent_end].strip()


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
    r"and\s+|but\s+|okay,?\s+|ok,?\s+|right,?\s+|yeah,?\s+|yep,?\s+|"
    r"actually,?\s+|basically,?\s+)+",
    re.IGNORECASE,
)


def _trim_to_chip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Clean text for a chip.  Returns '' if it can't fit at a natural break.

    Prefers dropping a chip over mid-word truncation: overflow is only
    accepted when there is a sentence or clause boundary that fits within
    ``limit`` characters.
    """
    text = _clean_entities(text)
    text = _FILLER_LEADS.sub("", text)
    text = text.strip().strip(",;:")
    if not text:
        return ""
    if len(text) <= limit:
        return text
    for sep in (". ", "! ", "? ", "; ", ", "):
        idx = text.rfind(sep, 0, limit)
        if idx > 20:
            return text[:idx].rstrip(",;:")
    return ""


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
    if not m:
        return []
    text = m.group(0).strip()
    # Require real content beyond the amendment keyword itself.
    tail = re.sub(r"^amend(?:ed|ment|ing)?\b[\s,.:;-]*", "", text, flags=re.IGNORECASE)
    if len(tail.split()) < 3:
        return []
    chip = _trim_to_chip(text)
    if not chip:
        return []
    return [{"category": "Amendment Made", "text": chip}]


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
        if _money_too_small(raw):
            continue
        formatted = _format_money(raw)
        tail = combined[m.end(): m.end() + 80]
        purpose = _money_purpose_snippet(tail)
        # Require contextual words — a bare amount is not a useful chip.
        if not purpose:
            continue
        label = _trim_to_chip(f"{formatted} {purpose}".strip())
        if not label or label in seen:
            continue
        seen.add(label)
        results.append({"category": "Cost & Funding", "text": label})
        if len(results) >= 3:
            break
    return results


def _money_too_small(raw: str) -> bool:
    """Filter out bare dollar amounts under $100 (likely OCR / speech noise)."""
    has_suffix = bool(
        re.search(r"(million|billion|\bM\b|\bB\b|\bK\b|thousand)", raw, re.I)
    )
    if has_suffix:
        return False
    numeric = re.sub(r"[^\d.]", "", raw)
    if not numeric or numeric == ".":
        return True
    try:
        return float(numeric) < 100
    except ValueError:
        return True


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


_NEXT_STEP_KW_RE = re.compile(
    r"\b(report back|bring (?:this |it )?back|return to council|"
    r"next meeting|next year|by (?:Q[1-4]|\d{4}|the end of))\b",
    re.IGNORECASE,
)


def _extract_next_step(transcript_text: str) -> list[dict]:
    for m in _NEXT_STEP_KW_RE.finditer(transcript_text):
        sentence = _sentence_around(transcript_text, m.start(), m.end())
        before_kw = sentence.split(m.group(0), 1)[0]
        if re.search(r"\bif\b", before_kw, re.IGNORECASE):
            continue
        if re.search(r"\bpreviously\b", sentence, re.IGNORECASE):
            continue
        chip = _trim_to_chip(sentence)
        # The matched keyword must survive trimming; otherwise we trimmed
        # to a useless fragment.
        if not chip or not re.search(re.escape(m.group(0)), chip, re.IGNORECASE):
            continue
        # Questions are not commitments.
        if chip.rstrip().endswith("?"):
            continue
        # Rambling conversational speech tends to pile up commas.
        if chip.count(",") >= 3:
            continue
        return [{"category": "Next Step", "text": chip}]
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
        sentence = _sentence_around(transcript_text, m.start(), m.end())
        if re.search(r"\b(recuse|conflict of interest)\b", sentence, re.IGNORECASE):
            continue
        chip = _trim_to_chip(sentence)
        if not chip:
            continue
        results.append(
            {"category": "Related Item", "text": chip}
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


GEMINI_MODEL = "gemini-2.5-flash"


class GeminiExtractor:
    """Calls Gemini for transcript cleanup and semantic chip extraction.

    Tests can substitute stubs by providing custom ``generate`` and
    ``clean_generate`` callables.
    """

    def __init__(
        self,
        api_key: str | None = None,
        generate=None,
        clean_generate=None,
    ):
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self._generate = generate
        self._clean_generate = clean_generate
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) or self._generate is not None

    def _get_client(self):
        if self._client is None:
            from google import genai  # lazy import

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _call(self, prompt: str, allowed_cats: list[str]) -> str:
        if self._generate is not None:
            return self._generate(prompt, allowed_cats)
        client = self._get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _chip_list_schema(allowed_cats),
                "temperature": 0.2,
            },
        )
        return response.text or "[]"

    def clean(self, transcript_text: str) -> str:
        """Normalize a rambling transcript slice into well-punctuated sentences.

        Returns the input unchanged when no cleanup hook is wired and no
        API key is configured (so tests using a stub extractor that only
        mocks ``generate`` see the raw text).
        """
        if not transcript_text.strip():
            return transcript_text
        if self._clean_generate is not None:
            return self._clean_generate(transcript_text)
        if not self._api_key:
            return transcript_text
        prompt = _build_cleanup_prompt(transcript_text)
        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"temperature": 0.0},
            )
            return (response.text or "").strip() or transcript_text
        except Exception as exc:
            print(f"    Gemini cleanup failed, using raw transcript: {exc}")
            return transcript_text

    def extract(
        self, item: dict, transcript_text: str, exclude: set[str],
    ) -> list[dict]:
        if not transcript_text.strip():
            return []
        allowed = [c for c in SEMANTIC_CATEGORIES if c not in exclude]
        if not allowed:
            return []
        prompt = _build_prompt(item, transcript_text, allowed)
        try:
            raw = self._call(prompt, allowed)
            parsed = json.loads(raw)
        except Exception as exc:
            print(f"    Gemini call failed: {exc}")
            return []
        return _sanitize_chips(parsed, allowed)


def _build_cleanup_prompt(transcript_text: str) -> str:
    return (
        "You are normalizing the raw automatic transcription of one segment "
        "of a Saskatoon city council meeting into well-punctuated, coherent "
        "English sentences for downstream summarisation.\n"
        "\n"
        "Hard rules:\n"
        "- Restructure ONLY. Do not add facts, opinions, or details that "
        "aren't in the input.\n"
        "- Do not omit substantive content: every name, number, dollar "
        "amount, agenda item reference, and direct quote must survive.\n"
        "- You MAY remove fillers (um, uh, like, you know, yeah, yep, "
        "I mean), false starts, and word-level stutters.\n"
        "- You MAY merge run-on conversational chunks into proper sentences "
        "and add periods, commas, and question marks where they belong.\n"
        "- Preserve speaker attributions when present (e.g. \"Councillor "
        "Pierce said\").\n"
        "- Output PLAIN TEXT only — no headers, no commentary, no JSON, no "
        "speaker labels you didn't see in the input.\n"
        "\n"
        "Raw transcript:\n"
        f"{transcript_text}\n"
        "\n"
        "Cleaned transcript:"
    )


USEFULNESS_LEVELS: list[str] = ["high", "medium", "low"]

# Chips must clear this bar to survive sanitization.
ACCEPTED_USEFULNESS: set[str] = {"high", "medium"}


def _chip_list_schema(allowed_cats: list[str]) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": allowed_cats},
                "text": {"type": "string"},
                "usefulness": {"type": "string", "enum": USEFULNESS_LEVELS},
            },
            "required": ["category", "text", "usefulness"],
        },
    }


def _build_prompt(
    item: dict, transcript_text: str, allowed_cats: list[str],
) -> str:
    lines = [
        "You are extracting short 'chip' summaries from a Saskatoon city "
        "council meeting transcript for ONE agenda item.",
        "",
        f"Agenda item title: {item.get('title') or '(untitled)'}",
        "",
        "For each relevant category below, emit at most ONE chip. Each chip "
        f"`text` must be a complete, self-contained phrase of at most "
        f"{MAX_SUMMARY_CHARS} characters (no trailing ellipsis, no cut-off "
        "sentences). Paraphrase tightly rather than quoting filler, avoid "
        "procedural or merely clarifying statements, and never fabricate — "
        "if the transcript doesn't clearly support a chip, omit it.",
        "",
        "Rate each chip's `usefulness`:",
        "- \"high\": adds a specific, concrete fact — a number, a named "
        "commitment, an identified impact, a real disagreement, a concrete "
        "next step, or a pointed quote.",
        "- \"medium\": accurate and relevant, but softer — explains what the "
        "item is about, notes a group that spoke, or summarises a line of "
        "debate.",
        "- \"low\": vague, procedural filler, truisms, or phrasing that "
        "could apply to any meeting.",
        "",
        "Include \"high\" and \"medium\" chips. Omit anything you would rate "
        "\"low\" — skip the category rather than emit a weak chip.",
        "",
        "Categories:",
    ]
    for cat in allowed_cats:
        lines.append(f"- {cat}: {SEMANTIC_DEFINITIONS[cat]}")
    lines.extend([
        "",
        "Transcript:",
        transcript_text,
        "",
        "Return a JSON array.  An empty array is valid if nothing fits.",
    ])
    return "\n".join(lines)


def _sanitize_chips(parsed, allowed_cats: list[str]) -> list[dict]:
    """Filter the LLM output down to clean, high-usefulness chips."""
    if not isinstance(parsed, list):
        return []
    allowed = set(allowed_cats)
    results: list[dict] = []
    seen_texts: set[str] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        cat = entry.get("category")
        text = entry.get("text")
        usefulness = entry.get("usefulness")
        if cat not in allowed or not isinstance(text, str) or not text.strip():
            continue
        if usefulness not in ACCEPTED_USEFULNESS:
            continue
        chip = _trim_to_chip(text)
        if not chip or chip in seen_texts:
            continue
        seen_texts.add(chip)
        results.append({"category": cat, "text": chip})
    return results


# ── Public API ──────────────────────────────────────────────────────────────


def _is_unanimous_tally(item: dict) -> bool:
    """True when the vote has no dissenting side (either all for or all against)."""
    detail = item.get("vote_detail") or ""
    m = _VOTE_TALLY_RE.search(detail)
    if m and (int(m.group(1)) == 0 or int(m.group(2)) == 0):
        return True
    vote = item.get("vote_result") or ""
    m2 = re.search(r"\((\d+)\s*(?:to|-)\s*(\d+)\)", vote)
    if m2 and (int(m2.group(1)) == 0 or int(m2.group(2)) == 0):
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
    gemini_extractor: GeminiExtractor | None = None,
) -> list[dict]:
    """Extract category chip summaries for a single agenda item.

    Returns a list of ``{"category": str, "text": str}`` sorted by the
    canonical 23-category order.  The deterministic pass always runs.  The
    Gemini pass runs only when ``gemini_extractor`` is provided or
    ``GEMINI_API_KEY`` is set in the environment.
    """
    slice_segments = _slice_transcript(transcript_segments, item)
    transcript_text = " ".join(s.get("text", "") for s in slice_segments)

    extractor = gemini_extractor if gemini_extractor is not None else GeminiExtractor()

    # Pre-process: turn raw automatic-transcription rambling into clean
    # sentences so both the regex extractors and the semantic pass have
    # well-punctuated input. Falls back to the raw text if cleanup fails
    # or the extractor isn't enabled.
    if extractor.enabled and transcript_text.strip():
        transcript_text = extractor.clean(transcript_text)

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

    # Drop deterministic chips that couldn't be fit at a natural break.
    results = [r for r in results if r.get("text")]
    covered = {r["category"] for r in results}

    # Suppress logically impossible semantic categories.
    vote = (item.get("vote_result") or "").upper()
    if "UNANIM" in vote or _is_unanimous_tally(item):
        covered.add("Dissenting View")

    if extractor.enabled and transcript_text.strip():
        results.extend(extractor.extract(item, transcript_text, exclude=covered))

    results.sort(key=lambda r: _CATEGORY_ORDER.get(r["category"], 999))
    return results
