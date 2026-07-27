"""
Hybrid extractor that turns an agenda item + its transcript slice into an
ItemSummary: a mandatory plain-language Description plus its Chips.

Chip categories are a closed list of 22 labels.  Extraction runs in two
passes when Gemini is enabled (deterministic + LLM); just the
deterministic pass otherwise.

  1. Deterministic pass — regex and heuristics over the item's metadata
     and the item's raw transcript slice. Covers Outcome, Vote Breakdown,
     Cost & Funding, Amendment Made, Procedural Note, Delegation, Next
     Step, Related Item, Deferred From, Declared Conflict, Data Cited.

  2. LLM pass — a single Gemini 2.5 Flash call per item, constrained to a
     JSON schema of ``{description, chips: [{category, text, usefulness}]}``
     for the 11 remaining "soft" categories.  ``description`` is
     **required**: as an optional category the model declined it on about
     half of all items and a metadata fallback echoed the item's title.
     See ``docs/adr/0003-item-summary-aggregate.md``.

A cleanup pass used to run ahead of these, rewriting each slice through
Gemini before extraction.  It was measured against no cleanup at all and
lost: see ``docs/adr/0005-delete-transcript-cleanup.md``.

When ``GEMINI_API_KEY`` is unset the LLM pass is skipped,
and the result is a Legacy ItemSummary — deterministic chips with no
description.  There is deliberately no non-LLM description fallback: text
derived from the title is a title echo by construction.

Chips are trimmed to <=100 characters at a natural clause break.

Tests can inject a stub extractor via the ``gemini_extractor`` parameter
of ``extract_item_summaries``.
"""

from __future__ import annotations

import json
import os
import re
import time

from app.agenda_items import (
    PROCEDURAL_KEYWORDS,
    format_outcome,
    is_consent_item,
    is_procedural,
    is_section_header,
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
# Each definition states what the category IS and, where a neighbouring
# category could plausibly take the same fact, which one wins.  Without
# those tie-breaks the model emitted the same fact twice: an impact on a
# low-income neighbourhood arrived as both Who's Affected and Equity
# Impact, and a delegate's objection as both Debate Highlight and Public
# Sentiment.  Overlapping definitions do not produce more coverage, they
# produce the same sentence wearing two labels.
SEMANTIC_DEFINITIONS: dict[str, str] = {
    "Debate Highlight": "a sharp or notable moment of debate among the decision-makers — a memorable quote, a pointed exchange, or a striking argument from a named councillor or staff member. Do NOT use this to restate the vote outcome ('council unanimously opposed the proposal' is Outcome). If the speaker is a member of the public or a delegation, use Public Sentiment. If it is a councillor explaining a vote against, use Dissenting View.",
    "Who's Affected": "name the specific residents, neighbourhoods, businesses, or organizations this decision lands on — \"residents of Nutana, Varsity View and City Park\", \"13 non-profit groups\", \"taxpayers, at about $14,000 a year\". Emit it whenever a concrete group is identifiable; a named group is exactly the fact a reader is looking for. Do not settle for \"residents\" or \"the community\" in general — if you cannot name who, omit the chip. Two categories take precedence and REPLACE this chip rather than suppress it: Equity Impact when the group is affected precisely because it is marginalized, low-income, Indigenous, or under-served, and Environmental Impact when what is affected is ecological rather than a group of people.",
    "Staff vs. Council": "a real disagreement between city administration and elected councillors (not just a clarifying question)",
    "Precedent Set": "a first-of-its-kind decision that will be referenced in future cases",
    "Unanswered Question": "a substantive question raised that was NOT answered (if it was answered or clarified, do NOT emit this)",
    "Public Sentiment": "members of the public, delegations, or petitioners expressing clear support or opposition (not just 'public engagement is required'). This is the category for non-councillor voices; a councillor's argument is Debate Highlight.",
    "Dissenting View": "a councillor's stated reason for voting against the motion (omit if the vote was unanimous)",
    "Legal Risk Flagged": "legal liability, lawsuits, or statutory-risk being raised",
    "Equity Impact": "concrete impact on marginalized, low-income, Indigenous, or under-served groups. Prefer this over Who's Affected whenever the affected group is one of those — do not emit both for the same group.",
    "Environmental Impact": "environmental, ecological, or emissions impact (not just the word 'environment' appearing). Prefer this over Who's Affected for an ecological impact — do not emit both for the same effect.",
    "Promise Made": "a specific public commitment or promise from council or staff (not a question or wondering)",
}

SEMANTIC_CATEGORIES: list[str] = list(SEMANTIC_DEFINITIONS.keys())

# Categories that can only be observed in discussion.  A Consent Item was
# approved in a block without individual debate, so these are withheld
# from its prompt **by construction** rather than left for the model to
# decline — an item that was never discussed cannot have a debate
# highlight, and offering the category invites the model to invent one.
DISCUSSION_ONLY_CATEGORIES: frozenset[str] = frozenset({
    "Debate Highlight",
    "Staff vs. Council",
    "Unanswered Question",
    "Public Sentiment",
    "Dissenting View",
})


# ── Transcript slicing ──────────────────────────────────────────────────────


def _slice_transcript(
    segments: list[dict], item: dict
) -> list[dict]:
    """Return transcript segments that overlap [item.start, item.end]."""
    # An inherited timestamp is the *parent section's* span, not this
    # item's.  Slicing on it would hand every Consent Item the same audio
    # — the clerk reading the consent block into the record — and attribute
    # it to each item individually.  A borrowed timestamp identifies no
    # audio, so there is none to return.
    if item.get("timestamp_inherited"):
        return []
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
    """The verdict alone — "Approved (8-3)" — with no title.

    The title used to be appended here to give the chip context back when
    a summary was nothing but chips.  The Description now carries that
    context, so repeating the title makes the Outcome chip a title echo
    sitting directly beneath a sentence that already said it better.
    """
    vote = item.get("vote_result") or ""
    rec = item.get("recommendation") or ""
    outcome = format_outcome(vote, rec)
    if not outcome or outcome == "Discussed":
        return []
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


# "Amendment Made" means council amended the motion in front of it.  The
# bare word "amend" does not mean that -- official text is full of it:
# "until such time as a new or amended Naming of City Property Policy is
# developed" produced the chip "amended Naming of City Property and
# Development Areas Policy, or related policy is developed", which
# describes a future policy nobody amended.  So the trigger has to be
# language about the motion's own fate, not any occurrence of the word.
_AMENDMENT_TRIGGER_RE = re.compile(
    r"\bas\s+amended\b"
    r"|\bamendment\s+(?:was\s+)?(?:carried|adopted|approved|defeated|lost)\b"
    r"|\bmoved\s+(?:an?\s+)?amendment\b"
    r"|\bamendment\s+to\s+the\s+(?:motion|recommendation)\b"
    # "That the motion be amended to include parks" — the motion's own fate.
    r"|\b(?:motion|recommendation|resolution|clause)\s+be\s+amended\b",
    re.IGNORECASE,
)


def _extract_amendment(item: dict) -> list[dict]:
    combined = " ".join(
        str(item.get(k) or "") for k in ("motion_text", "vote_result", "recommendation")
    )
    m = _AMENDMENT_TRIGGER_RE.search(combined)
    if not m:
        return []
    # Report the clause the trigger sits in, so the chip says what was
    # amended rather than just that something was.
    sentence = sentence_around(combined, m.start(), m.end())
    chip = _chip(sentence or m.group(0))
    if not chip:
        return []
    return [{"category": "Amendment Made", "text": chip}]


_MONEY_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K|thousand))?",
    re.IGNORECASE,
)


def _extract_cost_funding(item: dict) -> list[dict]:
    """Money figures from the item's own official text.

    Deliberately does **not** read the transcript.  Cost & Funding is a
    hard chip, which means its source has to be auditable — and agenda
    item boundaries in the transcript come from eSCRIBE bookmarks that
    routinely lag what was actually said.  On the 2026-06-24 council
    meeting the Shaw Centre score-clock presentation begins three minutes
    inside the *previous* item's bookmarked span, so a transcript-derived
    money chip put "$187K for the Shaw Centre score clock" on the 210
    Pacific Avenue emergency shelter.  No slicing rule fixes that; the
    timestamps are simply wrong.

    Money spoken in debate but absent from the official text is not lost —
    the Description and the soft chips still draw on the transcript.  What
    changes is that a chip claiming civic/legal weight now cites a source
    that can be checked.
    """
    combined = " ".join(
        str(item.get(k) or "")
        for k in ("title", "recommendation", "motion_text", "content")
    )
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
        label = _chip(f"{formatted} {purpose}".strip())
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


# The terminator set includes en/em dashes because official agenda text
# is full of them — "$187,000 to Shaw Centre – Score Clock and Timing
# Equipment" previously matched nothing at all, so the chip was dropped.
_PURPOSE_RE = re.compile(
    r"\s*(for|to(?:\s+the)?|towards?)\s+([a-z][\w\s'&-]{2,40}?)"
    r"(?=[.,;:–—()\[\]]|\s+(?:and|that|which|to|be)\b|$)",
    re.IGNORECASE,
)


def _money_purpose_snippet(tail: str) -> str:
    """Return the "for X" / "to X" phrase following a money amount.

    The preposition is preserved rather than normalized to "for": the
    original code rewrote every match as "for …", which turned "to
    complete the project" into the ungrammatical "for complete the
    project".
    """
    m = _PURPOSE_RE.match(tail)
    if not m:
        return ""
    preposition = re.sub(r"\s+", " ", m.group(1).strip().lower())
    return f"{preposition} {m.group(2).strip()}"


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


# ── Semantic pass ───────────────────────────────────────────────────────────


GEMINI_MODEL = "gemini-2.5-flash"


class ExtractionFailed(Exception):
    """The extractor could not reach a verdict for one item.

    Distinct from the model answering and having nothing to say — that is
    a real outcome and returns ``description=None``.  This means the call
    did not happen, or did not come back usable, so the item's summary is
    *unknown* rather than *empty*.  Callers must not save a meeting
    holding one of these: a saved summary is indistinguishable from a
    considered one, and nothing would ever retry it.
    """


class QuotaExhausted(Exception):
    """The daily Gemini quota is gone.

    Deliberately not an :class:`ExtractionFailed`.  That one is about a
    single item and the run continues past it; this one means every
    remaining call in the run will fail too, so it travels all the way up
    and stops the run.  Retrying is pointless — the run is unattended and
    nobody is there to raise the limit, and the next scheduled run picks
    the meetings up for free because ``is_current`` rejects them.
    """


# Gemini answers a 429 with the identifier of the quota that was hit.
# The distinction is the whole reason we can retry one and not the other:
# a per-minute limit clears on its own, a per-day limit does not.
_PER_DAY_QUOTA_MARKER = "perday"

# A rate limit is worth waiting out.  Three attempts, and the wait comes
# from the server's own RetryInfo rather than a guess.
_RATE_LIMIT_ATTEMPTS = 3

# An unrecognised 429 gets the benefit of the doubt twice, then is
# treated as quota.  Worst case that costs a minute of runner time before
# the run stops; the alternative — assuming quota — would abandon a run
# over a rate limit we could have waited out.
_UNKNOWN_LIMIT_ATTEMPTS = 2

# Long enough to clear a per-minute window, short enough that a bad
# retryDelay cannot park the run for hours.
_MAX_RETRY_WAIT_SECONDS = 60


def _error_payload(exc: Exception) -> dict:
    """The Gemini error body, whichever of its two shapes it arrived in.

    ``APIError.details`` is the raw response JSON, which is sometimes the
    error object and sometimes ``{"error": {...}}`` wrapping it.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return {}
    inner = details.get("error")
    return inner if isinstance(inner, dict) else details


def _quota_ids(exc: Exception) -> list[str]:
    """Quota identifiers named in a 429, e.g. ``...PerDayPerProject...``."""
    ids = []
    for detail in _error_payload(exc).get("details") or []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations") or []:
            if isinstance(violation, dict) and violation.get("quotaId"):
                ids.append(str(violation["quotaId"]))
    return ids


def _retry_delay_seconds(exc: Exception) -> float | None:
    """The server's requested wait, from the RetryInfo detail."""
    for detail in _error_payload(exc).get("details") or []:
        if not isinstance(detail, dict):
            continue
        raw = detail.get("retryDelay")
        if not raw:
            continue
        try:
            return float(str(raw).rstrip("s"))
        except ValueError:
            return None
    return None


def _is_rate_limited(exc: Exception) -> bool:
    """True for a 429 — either flavour."""
    return getattr(exc, "code", None) == 429 or (
        getattr(exc, "status", None) == "RESOURCE_EXHAUSTED"
    )


def _is_daily_quota(exc: Exception) -> bool:
    """True when a 429 names a per-day quota."""
    return any(_PER_DAY_QUOTA_MARKER in q.lower() for q in _quota_ids(exc))


class GeminiExtractor:
    """Calls Gemini for semantic chip extraction.

    Tests can substitute a stub by providing a custom ``generate``
    callable.
    """

    def __init__(self, api_key: str | None = None, generate=None):
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self._generate = generate
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
                "response_json_schema": _summary_schema(allowed_cats),
                # Zero, not 0.2: the eval loop diffs one run against a
                # committed baseline, and sampling noise buries the signal
                # from an actual prompt change under a screenful of
                # rewording.  Chips are extraction, not composition —
                # there is nothing here that variety improves.
                "temperature": 0.0,
            },
        )
        return response.text or "{}"

    def _call_with_retry(self, prompt: str, allowed_cats: list[str]) -> str:
        """:meth:`_call`, waiting out a rate limit but not a spent quota.

        A per-day quota raises :class:`QuotaExhausted` on the first
        rejection and never retries.  Anything else propagates for the
        caller to turn into an :class:`ExtractionFailed`.
        """
        attempt = 0
        while True:
            try:
                return self._call(prompt, allowed_cats)
            except Exception as exc:
                if not _is_rate_limited(exc):
                    raise
                if _is_daily_quota(exc):
                    raise QuotaExhausted(
                        f"daily Gemini quota exhausted ({', '.join(_quota_ids(exc))})"
                    ) from exc

                # An unrecognised 429 could be either.  Give it fewer
                # attempts than a known rate limit, then treat it as
                # quota so the run stops instead of grinding.
                known = bool(_quota_ids(exc))
                budget = _RATE_LIMIT_ATTEMPTS if known else _UNKNOWN_LIMIT_ATTEMPTS
                attempt += 1
                if attempt >= budget:
                    if known:
                        raise
                    raise QuotaExhausted(
                        f"429 with no quota detail, still failing after "
                        f"{attempt} attempts — treating it as exhausted quota"
                    ) from exc

                wait = _retry_delay_seconds(exc)
                wait = _MAX_RETRY_WAIT_SECONDS if wait is None else min(
                    wait, _MAX_RETRY_WAIT_SECONDS
                )
                print(
                    f"    Rate limited, waiting {wait:.0f}s "
                    f"(attempt {attempt}/{budget})",
                    flush=True,
                )
                time.sleep(wait)

    def _has_metadata(self, item: dict) -> bool:
        """True when the item has enough metadata to run the LLM without transcript."""
        rec = (item.get("recommendation") or "").strip()
        content = (item.get("content") or "").strip()
        return bool(rec or content)

    def extract(
        self, item: dict, transcript_text: str, exclude: set[str],
    ) -> tuple[str | None, list[dict]]:
        """Return ``(description, chips)`` for one agenda item.

        ``description`` is ``None`` only when the model answered and had
        nothing usable to say — never as a routine outcome, because the
        schema requires it.  A ``None`` here is a signal to look, not a
        cue to substitute filler.

        A call that *failed* does not return ``None``; it raises.  Those
        two used to be the same value, which is how a run wrote 35
        meetings of empty summaries and exited 0.
        """
        if not transcript_text.strip() and not self._has_metadata(item):
            return None, []
        if is_consent_item(item):
            exclude = set(exclude) | DISCUSSION_ONLY_CATEGORIES
        allowed = [c for c in SEMANTIC_CATEGORIES if c not in exclude]
        prompt = _build_prompt(item, transcript_text, allowed)
        try:
            raw = self._call_with_retry(prompt, allowed)
        except QuotaExhausted:
            raise
        except Exception as exc:
            print(f"    Gemini extract failed: {exc}", flush=True)
            raise ExtractionFailed(f"Gemini call failed: {exc}") from exc

        try:
            parsed = json.loads(raw)
        except Exception as exc:
            print(f"    Gemini returned unparseable JSON: {exc}", flush=True)
            raise ExtractionFailed(f"Gemini returned unparseable JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            print(
                f"    Gemini returned {type(parsed).__name__}, expected an object",
                flush=True,
            )
            raise ExtractionFailed(
                f"Gemini returned {type(parsed).__name__}, expected an object"
            )

        description = _sanitize_description(parsed.get("description"))
        if description is None:
            print(
                "    Gemini returned no usable description for "
                f"{(item.get('title') or '')[:50]!r}",
                flush=True,
            )

        raw_chips = parsed.get("chips")
        raw_count = len(raw_chips) if isinstance(raw_chips, list) else 0
        chips = _sanitize_chips(raw_chips, allowed)
        if raw_count > 0 and not chips:
            print(
                f"    Gemini returned {raw_count} chips but all were filtered out",
                flush=True,
            )
        return description, chips


def item_transcript_text(item: dict, transcript_segments: list[dict]) -> str:
    """The transcript slice the summarizer reads for *item*.

    One space between segments, which is what the cleanup pass used to
    receive before it was deleted (ADR `0005`) — so the text the chip
    call sees is byte-identical to the raw arm the A/B measured.
    """
    return " ".join(
        text
        for s in _slice_transcript(transcript_segments, item)
        if (text := s.get("text", "").strip())
    )


USEFULNESS_LEVELS: list[str] = ["high", "medium", "low"]

# Chips must clear this bar to survive sanitization.  This is the *only*
# usefulness gate.  The prompt used to also tell the model to withhold
# anything it would rate "low", which made the bar two gates deep: a chip
# had to be both emitted and then rated above the floor, and the model
# resolved a borderline chip differently on each run — sometimes by
# withholding it, sometimes by emitting it as "low", sometimes as
# "medium".  That flapping was a large share of the run-to-run churn the
# eval's --diff kept reporting.  The model now rates and never withholds
# on usefulness grounds; the cut happens here, deterministically.
#
# The prompt's *accuracy* gate ("a chip you inferred rather than found is
# worse than no chip") is a different rule and stays where it is — the
# model is the only thing that can tell whether it found a fact or
# invented it.
ACCEPTED_USEFULNESS: set[str] = {"high", "medium"}


# A Description is 1-2 sentences, so it gets more room than a chip.  Not
# hard-trimmed: cutting a description mid-clause would be worse than one
# that runs slightly long, and the eval reports overruns instead.
#
# Do not raise this to "fit" descriptions that overrun.  The model reads
# the number as a target rather than a ceiling — raising it 220 -> 280
# produced 300+ character descriptions that padded process detail back in
# ("received the report as information and reaffirmed...") and pushed one
# item into a title echo.  The tighter bound produces better writing; the
# overruns are the model reaching for substance, which is fine.
MAX_DESCRIPTION_CHARS = 220

# What the *prompt* asks for.  A language model cannot count characters —
# it has no access to its own tokenization — so a character budget is an
# instruction it can only guess at, and 7 of 11 fixture descriptions
# overran.  Words it can count.
#
# Calibrated, not guessed.  The first attempt converted 220 characters at
# 6 chars/word and asked for 35, which still produced 6 overruns of 11:
# civic-agenda prose runs nearer 7 characters per word once
# "Administration", "recommendation" and department names are in it, so
# 35 words is closer to 245 characters.  30 words is the honest
# conversion.  The character constants stay as the *measurement* unit so
# the eval's overrun count remains comparable across this change.
MAX_DESCRIPTION_WORDS = 30
MAX_SUMMARY_WORDS = 16


def _summary_schema(allowed_cats: list[str]) -> dict:
    """Response schema for one agenda item's ItemSummary.

    ``description`` is **required**.  That is the whole point: as an
    optional chip category the model declined it on roughly half of all
    items, and a metadata fallback then echoed the item's title back at
    the reader.  A required field cannot be declined, and a missing one
    is a parse error we can see rather than filler we cannot.
    """
    return {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "chips": {
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
            },
        },
        "required": ["description", "chips"],
    }


_RECOMMENDS_TO_COUNCIL_RE = re.compile(
    r"\brecommend(?:s|ed)?\s+to\s+(?:city\s+)?council\b", re.IGNORECASE,
)


def _field(text: str, limit: int) -> str:
    """Prepare one official-text field for the prompt.

    Two fixes over raw interpolation:

    * HTML entities are decoded.  ``clean_entities`` was applied only to
      model *output*, so the prompt told the model the official text was
      "clean and reliable" and then handed it
      ``"recommend to City Council&#58;"``.
    * Truncation is marked.  A silent mid-clause cut looks to the model
      like the recommendation simply ended there, so it summarises a
      fragment as though it were the whole motion.
    """
    cleaned = clean_entities(text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}… [truncated]"


def _build_prompt(
    item: dict, transcript_text: str, allowed_cats: list[str],
) -> str:
    title = item.get("title") or "(untitled)"
    rec = (item.get("recommendation") or "").strip()
    motion = (item.get("motion_text") or "").strip()
    vote = (item.get("vote_result") or "").strip()
    vote_detail = (item.get("vote_detail") or "").strip()
    content = (item.get("content") or "").strip()

    consent = is_consent_item(item)

    if consent:
        lines = [
            "You are summarizing one item from a Saskatoon city council "
            "meeting's CONSENT AGENDA. Council approved it as part of a "
            "block, in a single motion, with NO individual discussion.",
            "",
            "This matters for what you can say:",
            "- There is no transcript, because nothing was said about this "
            "item specifically. That is expected, not missing data.",
            "- Do NOT describe, infer, or imply any debate, questions, "
            "concerns, speakers, or public input. None occurred.",
            "- Work only from the official recommendation and agenda notes "
            "below. They are clean and reliable.",
            "- A resident reading this wants to know what the city just "
            "agreed to do, since nobody discussed it out loud.",
            "",
            f"Agenda item title: {title}",
        ]
    else:
        lines = [
            "You are extracting a summary of a Saskatoon city council "
            "meeting agenda item. Draw on the context below — the "
            "official recommendation and motion text are clean and reliable; "
            "the transcript is rough automatic speech-to-text and may contain "
            "errors, but captures discussion not in the official text.",
            "",
            f"Agenda item title: {title}",
        ]
    if rec:
        lines.extend(["", f"Official recommendation: {_field(rec, 2000)}"])
    if motion and motion != rec:
        lines.extend(["", f"Motion text: {_field(motion, 1000)}"])
    if vote:
        lines.extend(["", f"Vote result: {vote}"])
    if vote_detail:
        lines.append(f"Vote detail: {_field(vote_detail, 600)}")

    # Which body decided, and whether it decided at all.  Without this the
    # model asserts "City Council approved …" on Standing Policy Committee
    # items, where the committee only *recommends* to Council — a real
    # civic error a reader cannot detect.
    #
    # Keyed off the recommendation text, not the vote.  A committee item
    # normally *has* a carried vote, so testing the outcome label alone
    # let every committee item fall through to "This body's decision:
    # Approved" — which is how the baseline came to assert that City
    # Council reaffirmed a plan it had never seen.
    outcome = format_outcome(vote, rec)
    if outcome.startswith("Recommended") or _RECOMMENDS_TO_COUNCIL_RE.search(rec):
        lines.extend([
            "",
            "IMPORTANT: this is a COMMITTEE item. The committee voted to "
            "RECOMMEND this to City Council. City Council has NOT decided "
            "it. Do not write that City Council approved, adopted, "
            "reaffirmed, endorsed, or funded anything. The recommendation "
            "text below contains clauses like \"That City Council reaffirm "
            "X\" — that is what the committee is ASKING Council to do, not "
            "something Council did. Write what the committee is "
            "recommending, in future or conditional voice.",
        ])
    elif outcome.startswith("First reading passed"):
        # Same failure as the committee case, one step earlier: the vote
        # on record is the motion to put the bylaw before council, not the
        # decision on the application.  Told only "Approved", the model
        # wrote "City Council denied a rezoning request" beneath it —
        # picking the transcript over the label, and contradicting the
        # Outcome chip in the same summary.
        lines.extend([
            "",
            "IMPORTANT: this is a PUBLIC HEARING item. The recorded vote "
            "gave the bylaw FIRST READING — it put the matter before "
            "council so the hearing could be held. Council has NOT decided "
            "the application here. Do not write that it was approved, "
            "denied, passed, or rejected, even if the discussion points "
            "one way. Describe what is proposed and what was heard.",
        ])
    elif outcome and outcome != "Discussed":
        lines.extend([
            "",
            f"This body's decision: {outcome}. (This is already recorded "
            f"as the Outcome chip — do NOT restate it in the description.)",
        ])
    if content:
        lines.extend(["", f"Item content (from agenda notes): {_field(content, 2000)}"])

    lines.extend([
        "",
        "Return TWO things: a `description`, and a list of `chips`.",
        "",
        "## description (required)",
        "",
        "One or two sentences, at most "
        f"{MAX_DESCRIPTION_WORDS} words, saying what this item "
        "actually does and why it matters to a resident of Saskatoon. "
        "This is the single most important field — it is what someone "
        "reads instead of the agenda.",
        "",
        "Rules for `description`:",
        "- Do NOT restate the agenda item's title. The reader can already "
        "see the title; repeating it tells them nothing. If your "
        "description would just be the title reworded, dig into the "
        "recommendation and transcript for what the item concretely "
        "changes, costs, permits, or requires.",
        "- Lead with the substance, not the process. Prefer \"Raises transit "
        "fare-evasion fines to $250 and lets inspectors issue tickets\" "
        "over \"Council considered a report about the transit bylaw\".",
        "- Never open with what council did procedurally. \"Council "
        "received the report as information\", \"Council considered\", "
        "\"Council approved the recommendation\" — all of these waste the "
        "sentence a reader actually reads. The Outcome chip already "
        "records the verdict. Open with what changes in the city.",
        "- Never make the agenda item, the report, or the motion the "
        "SUBJECT of your sentence. \"The item approves funding for 13 "
        "groups\", \"The report highlights a shortage\", \"This item "
        "outlines\", \"The report provides an update on\" — all of these "
        "tell the reader a document exists, which they already assumed. "
        "The subject is the city, the deciding body, or the thing that "
        "changes: \"Saskatoon will fund 13 environmental projects\", "
        "\"Shelters are full almost every night\".",
        "- Spell proper nouns as the official recommendation and agenda "
        "notes spell them, never as the transcript spells them. The "
        "transcript is speech-to-text: a delegate written as \"Kobussen\" "
        "in the official text may appear as \"Colbison\" in the "
        "transcript. When the two disagree, the official text is right. If "
        "a name appears ONLY in the transcript and looks phonetically "
        "garbled, leave the name out rather than publish a guess at "
        "someone's name.",
        "- If the official recommendation is boilerplate (\"that the report "
        "be received as information\"), it tells you nothing — get the "
        "substance from the agenda notes and the transcript instead.",
        "- Include the concrete specifics — amounts, dates, locations, "
        "who is affected — when the source supports them.",
        "- Carry a number's unit and period with it. \"$14,000 a year\" is "
        "not \"$14,000\"; \"an 8-month extension\" is not \"an "
        "extension\". Dropping the qualifier changes the fact, and the "
        "reader has no way to notice. Never drop a unit or a period to "
        "fit the word budget — cut adjectives, cut a clause, cut the "
        "second sentence, but a number keeps its unit.",
        "- Plain language. No bureaucratic phrasing, no file numbers, no "
        "\"the Administration recommends that\".",
        "- State only what the source supports. If the material is thin, "
        "write a shorter description rather than inventing detail.",
        "- Do NOT append a benefit, purpose, or consequence the source "
        "does not state. \"This upgrade will benefit users of the "
        "facility\", \"aiming to improve sustainability\", \"ensuring "
        "continued support for residents\" — these are your inferences, "
        "not the city's decision, and a reader cannot tell the "
        "difference. Stop the sentence when the source stops.",
        "- Never state the verdict or the vote. No \"passed\", "
        "\"approved\", \"defeated\", \"carried\", \"received as "
        "information\", \"council has not yet decided\", and no tally "
        "like \"9-0\" or \"passed unanimously\". The card already shows "
        "the outcome beside your sentence, so this is at best a repeat. "
        "It is also where these summaries most often become wrong: a "
        "single item carries several votes — first reading, an "
        "amendment, the main motion — and the recorded tally belongs to "
        "only one of them. Attaching it to the wrong one states a fact "
        "the source does not support. Describe what the decision does, "
        "and let the outcome field say how it went.",
        "",
        "## chips",
        "",
        "For each relevant category below, emit at most ONE chip. Each chip "
        f"`text` must be a complete, self-contained phrase of at most "
        f"{MAX_SUMMARY_WORDS} words (no trailing ellipsis, no cut-off "
        "sentences). Paraphrase tightly — do not quote raw transcript "
        "verbatim.",
        "",
        "A chip adds a fact the description does not already carry. Before "
        "emitting a chip, check it against the description you just wrote: "
        "if the description already states that fact, drop the chip. A chip "
        "that restates the description in different words costs the reader "
        "a second read for no new information — it is the most common way "
        "these summaries go wrong. The exception is a number or name the "
        "description mentions in passing and the chip pins down precisely.",
        "",
        "Every chip must be traceable to something actually said or "
        "written in the item metadata or between the TRANSCRIPT fences. "
        "A chip you inferred rather than "
        "found is worse than no chip — omit the category instead.",
        "",
        "The no-verdict rule above applies to chips too, and harder. A "
        "chip is a short line, so a vote tally is most of it: \"First "
        "reading passed 9-0\" and \"Outcome: Recommended to Council\" "
        "spend the whole chip on what the outcome field already says, "
        "and pin a tally to a vote that may not be the one it belongs "
        "to. No chip states how a vote went.",
        "",
        "Rate each chip you emit with an honest `usefulness`. Rate it, do "
        "not act on it — emitting a chip and labelling it \"low\" is the "
        "correct move for a weak chip, and something downstream decides "
        "what to do with the label.",
        "- \"high\": adds a specific, concrete fact — a number, a named "
        "commitment, an identified impact, a real disagreement, a concrete "
        "next step, or a pointed quote.",
        "- \"medium\": accurate and relevant, but softer — explains what the "
        "item is about, notes a group that spoke, or summarises a line of "
        "debate.",
        "- \"low\": vague, procedural filler, truisms, or phrasing that "
        "could apply to any meeting (e.g. 'That the report be received').",
        "",
        "Categories:",
    ])
    for cat in allowed_cats:
        lines.append(f"- {cat}: {SEMANTIC_DEFINITIONS[cat]}")
    if not allowed_cats:
        lines.append(
            "- (none — every category is already covered; return an empty "
            "`chips` list and focus on the description)"
        )
    if transcript_text.strip():
        # Fenced and labelled.  The rules refer to "the material above",
        # but the transcript is the one source that appears below them —
        # naming it explicitly stops the traceability rule from reading
        # as though the transcript does not count as a source.
        lines.extend([
            "",
            "<<<TRANSCRIPT — rough automatic speech-to-text, may contain errors>>>",
            transcript_text,
            "<<<END TRANSCRIPT>>>",
        ])
    lines.extend([
        "",
        "Return a JSON object with `description` and `chips`. An empty "
        "`chips` list is valid if nothing fits; an empty `description` is "
        "not.",
    ])
    return "\n".join(lines)


def _sanitize_description(value) -> str | None:
    """Normalize the model's description, or ``None`` if it gave us nothing.

    Deliberately does not fall back to the item's title.  Substituting the
    title is what the retired "In Plain Terms" fallback did, and a title
    echo is indistinguishable from a real summary once it is on the page —
    a missing description is visibly missing, which is the safer failure.
    """
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", clean_entities(value)).strip()
    return text or None


def _sanitize_chips(parsed, allowed_cats: list[str]) -> list[dict]:
    """Filter the LLM output down to clean, high-usefulness chips."""
    if not isinstance(parsed, list):
        return []
    allowed = set(allowed_cats)
    results: list[dict] = []
    seen_texts: set[str] = set()
    # The prompt asks for at most one chip per category, but nothing
    # enforced it: dedup was on exact trimmed text, so two differently
    # worded Debate Highlights both survived.
    seen_categories: set[str] = set()
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
        if cat in seen_categories:
            continue
        chip = _transcript_chip(text)
        if not chip or chip in seen_texts:
            continue
        seen_texts.add(chip)
        seen_categories.add(cat)
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


MIN_DISCUSSED_MS = 60_000


def is_eligible_for_summary(item: dict) -> bool:
    """True when the item is worth summarizing at all.

    Two kinds of item qualify, for different reasons:

    * **Discussed items** — council spent real time on them, so they have
      their own timed span and a transcript to draw on.
    * **Consent Items** — approved in a block with no individual debate,
      so they have no transcript, but they carry substantial official
      recommendation text that is worth explaining to a resident.

    Section Headers, procedural items, and recesses never qualify.
    """
    if item.get("is_recess"):
        return False
    if is_procedural(item.get("title") or ""):
        return False
    if is_section_header(item):
        return False

    # Checked before the inherited-timestamp rejection below, because an
    # inherited timestamp is exactly what identifies a Consent Item.
    if is_consent_item(item):
        return True

    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return False
    if item.get("timestamp_inherited"):
        return False
    # Too brief to have been discussed, and not a Consent Item, so there
    # is neither transcript nor official text worth summarizing.
    if end - start < MIN_DISCUSSED_MS:
        return False
    return True


def extract_item_summaries(
    item: dict,
    transcript_segments: list[dict],
    gemini_extractor: GeminiExtractor | None = None,
    transcript_text: str | None = None,
) -> dict:
    """Build the ItemSummary payload for a single agenda item.

    Returns ``{"description": str | None, "chips": [{"category", "text"}]}``
    with chips sorted by the canonical 22-category order — the shape
    :meth:`app.models.ItemSummary.from_dict` consumes.

    The deterministic pass always runs.  The Gemini pass runs only when
    ``gemini_extractor`` is provided or ``GEMINI_API_KEY`` is set, and it
    is the sole source of ``description``; without it the result is a
    Legacy ItemSummary carrying chips but no description.

    Pass ``transcript_text`` to supply the item's slice directly; by
    default it is sliced from *transcript_segments*.
    """
    extractor = gemini_extractor if gemini_extractor is not None else GeminiExtractor()

    if transcript_text is None:
        transcript_text = item_transcript_text(item, transcript_segments)

    # Metadata-based deterministic extractors — always run because they
    # operate on clean structured data, not raw transcript.
    results: list[dict] = []
    results.extend(_extract_outcome(item))
    results.extend(_extract_vote_breakdown(item))
    results.extend(_extract_amendment(item))
    results.extend(_extract_cost_funding(item))
    results.extend(_extract_procedural_note(item))

    # Transcript-based regex extractors — only run when Gemini is disabled.
    #
    # NOTE: this means Declared Conflict, Delegation, Next Step, Related
    # Item, Deferred From and Data Cited are produced by NOTHING in
    # production.  They are not in SEMANTIC_CATEGORIES either, so the LLM
    # never sees them.  Six of the twenty-two categories are unreachable.
    #
    # Running them on a cleaned-up transcript was tried and is worse, not
    # better: on the eval fixtures they emit sentence fragments ("McDonald,
    # and we will understand by the end of it…") and — because eSCRIBE
    # bookmarks lag what was said — facts belonging to adjacent items
    # ("445 hectares" from the Homewood concept plan landing on the 210
    # Pacific Avenue shelter).  Cleaning the input does not fix a slice
    # that contains the wrong item.
    #
    # Fixing this is a design decision, not a code tweak: either delete
    # the six categories, move them to the LLM's remit, or fix the item
    # boundaries.  See the plan's open questions.
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

    # The Description comes only from the LLM.  With no extractor there is
    # nothing honest to put there — deterministic text derived from the
    # title is a title echo by construction, which is the failure this
    # aggregate exists to prevent.
    description: str | None = None
    if extractor.enabled:
        description, semantic_chips = extractor.extract(
            item, transcript_text, exclude=covered,
        )
        results.extend(semantic_chips)

    results.sort(key=lambda r: _CATEGORY_ORDER.get(r["category"], 999))
    return {"description": description, "chips": results}
