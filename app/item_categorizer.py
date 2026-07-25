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

import hashlib
import json
import os
import re

from app.agenda_items import (
    PROCEDURAL_KEYWORDS,
    format_outcome,
    is_procedural,
)
from app.agenda_text import (
    clean_entities,
    format_money,
    plainify,
    trim_to_chip,
)
from app.models import Transcript
from app.transcript_text import (
    sentence_around,
    split_sentences,
    strip_filler_leads,
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


# ── Transcript slicing ──────────────────────────────────────────────────────


def _slice_transcript(
    segments: list[dict], item: dict
) -> list[dict]:
    """Return transcript segments that overlap [item.start, item.end]."""
    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return []
    # Delegate the overlap check to Transcript so segment-shape knowledge
    # lives in one place.
    transcript = Transcript.from_dict(segments)
    kept = [
        s for s in transcript.segments
        if s.end_ms >= start and s.start_ms <= end
    ]
    return Transcript(segments=kept).to_dict()


def _chip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Build a chip from non-transcript text: entities, then length-trim."""
    return trim_to_chip(clean_entities(text), limit)


def _transcript_chip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Build a chip from transcript text: entities, filler, then length-trim."""
    return trim_to_chip(strip_filler_leads(clean_entities(text)), limit)


# ── Deterministic extractors ────────────────────────────────────────────────


def _extract_outcome(item: dict) -> list[dict]:
    vote = item.get("vote_result") or ""
    rec = item.get("recommendation") or ""
    outcome = format_outcome(vote, rec)
    if not outcome or outcome == "Discussed":
        return []
    title = plainify(item.get("title") or "")
    if title:
        contextual = f"{outcome}: {title}"
        chip = _chip(contextual)
        if chip:
            return [{"category": "Outcome", "text": chip}]
    return [{"category": "Outcome", "text": _chip(outcome)}]


# The two sides are matched independently because a unanimous vote has no
# "Against:" section at all — eSCRIBE renders only the sides that have
# members, e.g. "In Favour: (5) ... Absent: (1) ... CARRIED UNANIMOUSLY".
# Requiring both sides in one pattern silently dropped the Vote Breakdown
# chip for every unanimous committee vote.  "Absent" is deliberately not
# counted: an absent member did not vote against.
_VOTE_FOR_RE = re.compile(r"In\s+Favour:\s*\((\d+)\)", re.IGNORECASE)
_VOTE_AGAINST_RE = re.compile(r"\bAgainst:\s*\((\d+)\)", re.IGNORECASE)
_VOTE_RESULT_TALLY_RE = re.compile(r"\((\d+)\s*(?:to|-)\s*(\d+)\)")


def _parse_vote_tally(item: dict) -> tuple[int, int] | None:
    """Return ``(for, against)`` for the item's vote, or ``None``.

    Prefers the structured ``vote_detail`` sides, then falls back to an
    "(N to M)" tally embedded in ``vote_result``.
    """
    detail = item.get("vote_detail") or ""
    for_m = _VOTE_FOR_RE.search(detail)
    against_m = _VOTE_AGAINST_RE.search(detail)
    if for_m or against_m:
        for_n = int(for_m.group(1)) if for_m else 0
        against_n = int(against_m.group(1)) if against_m else 0
        if for_n or against_n:
            return for_n, against_n

    m = _VOTE_RESULT_TALLY_RE.search(item.get("vote_result") or "")
    if m:
        for_n, against_n = int(m.group(1)), int(m.group(2))
        if for_n or against_n:
            return for_n, against_n
    return None


def _extract_vote_breakdown(item: dict) -> list[dict]:
    tally = _parse_vote_tally(item)
    if tally is None:
        return []
    for_n, against_n = tally
    return [{
        "category": "Vote Breakdown",
        "text": _chip(f"{for_n} for, {against_n} against"),
    }]


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
    chip = _chip(text)
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
        formatted = format_money(raw)
        tail = combined[m.end(): m.end() + 80]
        purpose = _money_purpose_snippet(tail)
        # Require contextual words — a bare amount is not a useful chip.
        if not purpose:
            continue
        label = _transcript_chip(f"{formatted} {purpose}".strip())
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
    return [{"category": "Declared Conflict", "text": _transcript_chip(m.group(0))}]


_DELEGATION_RE = re.compile(
    r"(Director|Mr\.?|Ms\.?|Mrs\.?|Dr\.?)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
    r"\s+(?:presented|spoke|addressed|appeared|responded)"
    r"[^.!?]{0,80}",
)


def _extract_delegation(transcript_text: str) -> list[dict]:
    m = _DELEGATION_RE.search(transcript_text[:4000])
    if not m:
        return []
    return [{"category": "Delegation", "text": _transcript_chip(m.group(0))}]


_NEXT_STEP_KW_RE = re.compile(
    r"\b(report back|bring (?:this |it )?back|return to council|"
    r"next meeting|next year|by (?:Q[1-4]|\d{4}|the end of))\b",
    re.IGNORECASE,
)


def _extract_next_step(transcript_text: str) -> list[dict]:
    for m in _NEXT_STEP_KW_RE.finditer(transcript_text):
        sentence = sentence_around(transcript_text, m.start(), m.end())
        before_kw = sentence.split(m.group(0), 1)[0]
        if re.search(r"\bif\b", before_kw, re.IGNORECASE):
            continue
        if re.search(r"\bpreviously\b", sentence, re.IGNORECASE):
            continue
        chip = _transcript_chip(sentence)
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
                {"category": "Deferred From", "text": _transcript_chip(m.group(0))}
            )
    own = (item.get("section_number") or "").rstrip(".")
    for m in re.finditer(
        r"\bitem\s+(\d+(?:\.\d+){1,3})\b",
        transcript_text, re.IGNORECASE,
    ):
        ref = m.group(1)
        if ref == own:
            continue
        sentence = sentence_around(transcript_text, m.start(), m.end())
        if re.search(r"\b(recuse|conflict of interest)\b", sentence, re.IGNORECASE):
            continue
        chip = _transcript_chip(sentence)
        if not chip:
            continue
        results.append(
            {"category": "Related Item", "text": chip}
        )
        break
    return results


def _extract_procedural_note(item: dict) -> list[dict]:
    title = item.get("title") or ""
    if not is_procedural(title):
        return []
    match_kw = next(
        (kw for kw in PROCEDURAL_KEYWORDS if kw in title.lower()), "procedural"
    )
    return [{"category": "Procedural Note", "text": _chip(match_kw.title())}]


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
    return [{"category": "Data Cited", "text": _transcript_chip(m.group(0))}]


def _extract_in_plain_terms(item: dict) -> list[dict]:
    """Fallback 'In Plain Terms' chip derived from the item title and recommendation.

    Always fires so that every substantive item gets at least one chip
    describing what the item is about, even if the Gemini pass fails or
    transcript data is unavailable.
    """
    title = plainify(item.get("title") or "")
    if not title or len(title) < 10:
        return []
    rec = (item.get("recommendation") or "").strip()
    rec_snippet = ""
    if rec:
        rec_clean = re.sub(r"\s+", " ", clean_entities(rec)).strip()
        first_sentence = re.split(r"[.;]", rec_clean, maxsplit=1)[0].strip()
        if 10 < len(first_sentence) <= 90 and not _is_boilerplate_rec(first_sentence):
            rec_snippet = first_sentence
    if rec_snippet:
        combined = f"{title} — {rec_snippet}"
    else:
        combined = title
    chip = _chip(combined)
    if not chip:
        chip = _chip(title)
    if not chip:
        return []
    return [{"category": "In Plain Terms", "text": chip}]


_BOILERPLATE_REC_RE = re.compile(
    r"^that the (?:report|information|presentation|correspondence|"
    r"communication|minutes|letter|petition) be (?:received|noted|filed)",
    re.IGNORECASE,
)


def _is_boilerplate_rec(text: str) -> bool:
    return bool(_BOILERPLATE_REC_RE.search(text))


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
                # Zero, not 0.2: the eval loop diffs one run against a
                # committed baseline, and sampling noise buries the signal
                # from an actual prompt change under a screenful of
                # rewording.  Chips are extraction, not composition —
                # there is nothing here that variety improves.
                "temperature": 0.0,
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
            cleaned = (response.text or "").strip()
        except Exception as exc:
            print(f"    Gemini cleanup failed, using raw transcript: {exc}", flush=True)
            return transcript_text
        if not cleaned:
            return transcript_text
        if _cleanup_looks_truncated(response, transcript_text, cleaned):
            # A truncated CleanTranscript is the dangerous case: it reads as
            # clean prose, so nothing downstream can tell that the tail of
            # the item is missing — and it would be cached under a valid
            # fingerprint.  Prefer the raw transcript, loudly.
            print(
                f"    Gemini cleanup truncated "
                f"({len(transcript_text):,} chars in, {len(cleaned):,} out) "
                f"— using raw transcript",
                flush=True,
            )
            return transcript_text
        return cleaned

    def _has_metadata(self, item: dict) -> bool:
        """True when the item has enough metadata to run the LLM without transcript."""
        rec = (item.get("recommendation") or "").strip()
        content = (item.get("content") or "").strip()
        return bool(rec or content)

    def extract(
        self, item: dict, transcript_text: str, exclude: set[str],
    ) -> list[dict]:
        if not transcript_text.strip() and not self._has_metadata(item):
            return []
        allowed = [c for c in SEMANTIC_CATEGORIES if c not in exclude]
        if not allowed:
            return []
        prompt = _build_prompt(item, transcript_text, allowed)
        try:
            raw = self._call(prompt, allowed)
            parsed = json.loads(raw)
        except Exception as exc:
            print(f"    Gemini extract failed: {exc}", flush=True)
            return []
        raw_count = len(parsed) if isinstance(parsed, list) else 0
        chips = _sanitize_chips(parsed, allowed)
        if raw_count > 0 and not chips:
            print(
                f"    Gemini returned {raw_count} chips but all were filtered out",
                flush=True,
            )
        return chips


_SASKATOON_NAMES = (
    # Current council (2024-2028)
    "Mayor Cynthia Block, "
    "Councillor Kathryn MacDonald, Councillor Senos Timon, "
    "Councillor Robert Pearce, Councillor Troy Davies, "
    "Councillor Randy Donauer, Councillor Jasmin Parker, "
    "Councillor Holly Kelleher, Councillor Scott Ford, "
    "Councillor Bev Dubois, Councillor Zach Jeffries. "
    # Previous council (2020-2024)
    "Mayor Charlie Clark, "
    "Councillor Darren Hill, Councillor Hilary Gough, "
    "Councillor David Kirton, Councillor Mairin Loewen, "
    "Councillor Sarina Gersher. "
    # Local vocabulary
    "Meewasin Valley Authority, Swale Watchers, Remai Modern, "
    "Métis, Cree, Dakota, Nakota, Dene, Saulteaux, Treaty 6, "
    "Idylwyld, Nutana, Riversdale, Caswell Hill, Sutherland, "
    "Buena Vista, Haultain, Stonebridge, Willowgrove, Blairmore, "
    "Attridge, Chief Mistawasis Bridge."
)


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
        "Dubois said\").\n"
        "- CORRECT garbled proper nouns to the closest match from this "
        "list of real names and places:\n"
        f"  {_SASKATOON_NAMES}\n"
        "  (e.g. \"Du Boa\" or \"DiBoa\" → \"Dubois\", "
        "\"Me was in\" → \"Meewasin\", \"May-T\" → \"Métis\", "
        "\"Swail\" → \"Swale\").\n"
        "- Output PLAIN TEXT only — no headers, no commentary, no JSON, no "
        "speaker labels you didn't see in the input.\n"
        "\n"
        "Raw transcript:\n"
        f"{transcript_text}\n"
        "\n"
        "Cleaned transcript:"
    )


# Cleanup removes fillers and false starts, so the output is legitimately
# shorter than the input — but not by this much.  Below this ratio the
# model stopped early rather than tightened prose.
_MIN_CLEANUP_RETENTION = 0.5


def _cleanup_looks_truncated(response, raw: str, cleaned: str) -> bool:
    """True when the cleanup response stopped short of covering the input.

    Two signals: the API telling us it hit the output cap, and the output
    being far shorter than filler-removal alone can explain.  The second
    matters because a long slice can exhaust the output budget without
    the finish reason surviving the SDK's response shaping.
    """
    finish = ""
    try:
        finish = str(response.candidates[0].finish_reason or "")
    except (AttributeError, IndexError, TypeError):
        pass
    if "MAX_TOKENS" in finish.upper():
        return True
    return len(cleaned) < len(raw) * _MIN_CLEANUP_RETENTION


# Cleanup is bounded by how fast the model can *emit* text, so one call
# per agenda item does not scale: a 100-minute item is ~117k characters,
# which is both far too slow serially and close enough to the output cap
# to risk silent truncation.  Slices are split into chunks on segment
# boundaries and cleaned concurrently instead.
CLEANUP_CHUNK_CHARS = 8000


def cleanup_fingerprint() -> str:
    """Identity of the cleanup prompt, the model, and the chunking.

    Cached CleanTranscripts are stored under this fingerprint so that
    editing the cleanup prompt — or switching models, or re-chunking —
    cannot read through to text produced by the old one.  A stale
    fingerprint is a cache miss, not a silently-wrong hit.

    Chunk size belongs in the basis because it changes how much context
    the model sees per call, and therefore the text it produces.

    The transcript itself is excluded (an empty slice is rendered)
    because it varies per item; only the instructions and the name roster
    define the prompt's identity.
    """
    basis = f"{GEMINI_MODEL}\n{CLEANUP_CHUNK_CHARS}\n{_build_cleanup_prompt('')}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _cleanup_chunks(
    item: dict,
    transcript_segments: list[dict],
    max_chars: int = CLEANUP_CHUNK_CHARS,
) -> list[str]:
    """Split *item*'s transcript slice into chunks for the cleanup pass.

    Splits on segment boundaries so a chunk never begins or ends mid-way
    through a spoken phrase.  A single segment longer than *max_chars*
    becomes its own oversized chunk rather than being cut.
    """
    texts = [
        s.get("text", "")
        for s in _slice_transcript(transcript_segments, item)
        if s.get("text", "").strip()
    ]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and size + len(text) > max_chars:
            chunks.append(" ".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def clean_item_transcript(
    item: dict,
    transcript_segments: list[dict],
    gemini_extractor: GeminiExtractor,
    max_workers: int = 8,
) -> str:
    """Slice the transcript to *item*, clean it, and return the CleanTranscript.

    Split out of :func:`extract_item_summaries` so callers can cache the
    result: cleanup is the expensive half of summarization and the half
    that chip-prompt changes don't affect.
    """
    chunks = _cleanup_chunks(item, transcript_segments)
    if not chunks:
        return ""
    if not gemini_extractor.enabled:
        return " ".join(chunks)
    return " ".join(_clean_chunks(chunks, gemini_extractor, max_workers))


def _clean_chunks(
    chunks: list[str], gemini_extractor: GeminiExtractor, max_workers: int,
) -> list[str]:
    if len(chunks) == 1:
        return [gemini_extractor.clean(chunks[0])]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(gemini_extractor.clean, chunks))


DEFAULT_CLEAN_WORKERS = 8


def clean_meeting_transcripts(
    items: list[dict],
    transcript_segments: list[dict],
    gemini_extractor: GeminiExtractor,
    cached: dict[str, str] | None = None,
    max_workers: int = DEFAULT_CLEAN_WORKERS,
) -> dict[str, str]:
    """Return ``{item_id: CleanTranscript}`` for every item in *items*.

    Entries already present in *cached* are reused untouched; the rest
    are cleaned concurrently, since each is an independent network call.
    Callers own the cache — this function neither reads nor writes one,
    it just fills the gaps.
    """
    cached = cached or {}
    result: dict[str, str] = {}
    missing: list[dict] = []
    for item in items:
        key = str(item["item_id"])
        if key in cached:
            result[key] = cached[key]
        else:
            missing.append(item)

    if not missing:
        return result

    # Chunk every outstanding item up front and clean the whole meeting's
    # chunks through one pool.  Fanning out per item instead would nest
    # pools (items x chunks) and multiply the concurrency limit; this way
    # a single 100-minute item parallelises just as well as fifteen short
    # ones, and the ceiling stays where the caller put it.
    chunks_by_item = {
        str(it["item_id"]): _cleanup_chunks(it, transcript_segments)
        for it in missing
    }
    flat = [
        (key, index, text)
        for key, chunks in chunks_by_item.items()
        for index, text in enumerate(chunks)
    ]

    if not gemini_extractor.enabled or not flat:
        for key, chunks in chunks_by_item.items():
            result[key] = " ".join(chunks)
        return result

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        cleaned = list(pool.map(
            lambda task: (task[0], task[1], gemini_extractor.clean(task[2])),
            flat,
        ))

    parts: dict[str, list[tuple[int, str]]] = {k: [] for k in chunks_by_item}
    for key, index, text in cleaned:
        parts[key].append((index, text))
    for key, indexed in parts.items():
        result[key] = " ".join(text for _, text in sorted(indexed))
    return result


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
    title = item.get("title") or "(untitled)"
    rec = (item.get("recommendation") or "").strip()
    motion = (item.get("motion_text") or "").strip()
    vote = (item.get("vote_result") or "").strip()
    vote_detail = (item.get("vote_detail") or "").strip()
    content = (item.get("content") or "").strip()

    lines = [
        "You are extracting short 'chip' summaries from a Saskatoon city "
        "council meeting agenda item. Use ALL the context below — the "
        "official recommendation and motion text are clean and reliable; "
        "the transcript is rough automatic speech-to-text and may contain "
        "errors, but captures discussion not in the official text.",
        "",
        f"Agenda item title: {title}",
    ]
    if rec:
        lines.extend(["", f"Official recommendation: {rec[:500]}"])
    if motion and motion != rec:
        lines.extend(["", f"Motion text: {motion[:300]}"])
    if vote:
        lines.append(f"Vote result: {vote}")
    if vote_detail:
        lines.append(f"Vote detail: {vote_detail[:300]}")
    if content:
        lines.extend(["", f"Item content (from agenda notes): {content[:800]}"])

    lines.extend([
        "",
        "For each relevant category below, emit at most ONE chip. Each chip "
        f"`text` must be a complete, self-contained phrase of at most "
        f"{MAX_SUMMARY_CHARS} characters (no trailing ellipsis, no cut-off "
        "sentences). Paraphrase tightly — do not quote raw transcript "
        "verbatim. Write each chip so a busy resident understands it "
        "without reading the agenda.",
        "",
        "Rate each chip's `usefulness`:",
        "- \"high\": adds a specific, concrete fact — a number, a named "
        "commitment, an identified impact, a real disagreement, a concrete "
        "next step, or a pointed quote.",
        "- \"medium\": accurate and relevant, but softer — explains what the "
        "item is about, notes a group that spoke, or summarises a line of "
        "debate.",
        "- \"low\": vague, procedural filler, truisms, or phrasing that "
        "could apply to any meeting (e.g. 'That the report be received').",
        "",
        "Include \"high\" and \"medium\" chips. Omit anything you would rate "
        "\"low\" — skip the category rather than emit a weak chip.",
        "",
        "Categories:",
    ])
    for cat in allowed_cats:
        lines.append(f"- {cat}: {SEMANTIC_DEFINITIONS[cat]}")
    if transcript_text.strip():
        lines.extend(["", "Transcript (rough, may contain errors):", transcript_text])
    lines.extend(["", "Return a JSON array.  An empty array is valid if nothing fits."])
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
        chip = _transcript_chip(text)
        if not chip or chip in seen_texts:
            continue
        seen_texts.add(chip)
        results.append({"category": cat, "text": chip})
    return results


# ── Public API ──────────────────────────────────────────────────────────────


def _is_unanimous_tally(item: dict) -> bool:
    """True when the vote has no dissenting side (either all for or all against)."""
    tally = _parse_vote_tally(item)
    if tally is None:
        return False
    for_n, against_n = tally
    return for_n == 0 or against_n == 0


def is_eligible_for_summary(item: dict) -> bool:
    """Only non-consent, non-procedural, non-brief items are worth analyzing."""
    if item.get("timestamp_inherited"):
        return False
    if item.get("is_recess"):
        return False
    title = item.get("title") or ""
    if is_procedural(title):
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
    cleaned_transcript_text: str | None = None,
) -> list[dict]:
    """Extract category chip summaries for a single agenda item.

    Returns a list of ``{"category": str, "text": str}`` sorted by the
    canonical 23-category order.  The deterministic pass always runs.  The
    Gemini pass runs only when ``gemini_extractor`` is provided or
    ``GEMINI_API_KEY`` is set in the environment.

    Pass ``cleaned_transcript_text`` to supply an already-cleaned slice
    (e.g. from ``CleanTranscriptCache``) and skip the cleanup call.
    """
    extractor = gemini_extractor if gemini_extractor is not None else GeminiExtractor()

    if cleaned_transcript_text is not None:
        transcript_text = cleaned_transcript_text
    else:
        # Pre-process: turn raw automatic-transcription rambling into clean
        # sentences so both the regex extractors and the semantic pass have
        # well-punctuated input. Falls back to the raw text if cleanup fails
        # or the extractor isn't enabled.
        transcript_text = clean_item_transcript(
            item, transcript_segments, extractor,
        )

    # Metadata-based deterministic extractors — always run because they
    # operate on clean structured data, not raw transcript.
    results: list[dict] = []
    results.extend(_extract_outcome(item))
    results.extend(_extract_vote_breakdown(item))
    results.extend(_extract_amendment(item))
    results.extend(_extract_cost_funding(item, transcript_text))
    results.extend(_extract_procedural_note(item))

    # Transcript-based regex extractors — only run when Gemini is disabled
    # because raw automatic transcript produces too much noise (garbled
    # fragments, mismatched keywords).  When Gemini is enabled, it handles
    # these categories far more reliably from the combined metadata +
    # transcript context.
    if not extractor.enabled:
        results.extend(_extract_declared_conflict(transcript_text))
        results.extend(_extract_delegation(transcript_text))
        results.extend(_extract_next_step(transcript_text))
        results.extend(_extract_related_deferred(item, transcript_text))
        results.extend(_extract_data_cited(transcript_text))

    # Drop deterministic chips that couldn't be fit at a natural break.
    results = [r for r in results if r.get("text")]
    covered = {r["category"] for r in results}

    # Suppress logically impossible semantic categories.
    vote = (item.get("vote_result") or "").upper()
    if "UNANIM" in vote or _is_unanimous_tally(item):
        covered.add("Dissenting View")

    if extractor.enabled:
        results.extend(extractor.extract(item, transcript_text, exclude=covered))

    # Fallback: if no "In Plain Terms" chip was produced by Gemini, generate
    # one from the item metadata so every substantive item has at least a
    # description of what it's about.
    covered_after = {r["category"] for r in results}
    if "In Plain Terms" not in covered_after:
        results.extend(_extract_in_plain_terms(item))

    results.sort(key=lambda r: _CATEGORY_ORDER.get(r["category"], 999))
    return results
