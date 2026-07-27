"""Extracts guest-speaker presentations (delegations) from an agenda item.

Council's PostMinutes narrate each delegate in their own sentence, e.g.
"Karen Kobussen, Saskatoon West Business Association, expressed concerns
with the effectiveness of existing approaches...".  This is a deterministic
regex pass over the official minutes text — the same "hard chip" approach
``app.item_categorizer`` uses for Delegation, Outcome, etc. — rather than an
LLM pass: the sentence shape is a stable eSCRIBE convention, not free text
that needs interpretation.

A second, independent pass looks at Request-to-Speak ("RTS") attachments,
which eSCRIBE names ``"<section> RTS - <Name> - <Topic>.pdf"``.  This
catches delegates the prose only mentions in passing (a name dropped into
someone else's sentence, e.g. "...along with Tammy MacFarlane.").
"""

from __future__ import annotations

import re

from app.agenda_items import is_procedural
from app.agenda_text import clean_entities
from app.models import AgendaItem, Presentation

# A stable set of verbs eSCRIBE's minutes use to introduce a delegate's
# turn at the podium. Deliberately narrow: a staff member "responding to a
# question" is not a presentation, and casting the net wider starts
# matching narrative filler instead of delegate sentences.
_VERB_RE = re.compile(
    r"\b(?:presented|addressed|appeared before|"
    r"spoke in (?:support|opposition)|"
    r"expressed (?:support|concerns?|opposition)|"
    r"was called forward)\b",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Sentences that open on a pronoun or a narrative marker are continuations
# ("She responded to questions of Committee."), not a new delegate.
_NON_NAME_LEADS = {"he", "she", "they", "it", "discussion", "questions", "the"}

# A personal name is a short run of capitalized words. A title like
# "Director of Planning and Development" fails this on purpose — "of" and
# "and" are lowercase — which is what keeps staff titles out of a feature
# about guest speakers without a separate staff/public distinction to
# maintain.
_NAME_RE = re.compile(r"^[A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*){0,5}$")

# ...except when every word is capitalized, which is how "General Manager"
# slipped through and was published as a guest speaker from "General
# Manager, Community Services Anger presented the report".  A role word
# anywhere in the name disqualifies it: this feature is about who came to
# address council, and staff and council members are neither.
#
# "Chief" is deliberately absent — "Chief Kelly Wolfe" spoke to the
# Downtown Event District item as a guest, and a rule that reads an
# Indigenous leader's title as a city job is worse than the staff line it
# was trying to draw.
_ROLE_WORDS = {
    "manager", "director", "solicitor", "clerk", "superintendent",
    "officer", "engineer", "planner", "administrator", "treasurer",
    "controller", "commissioner", "coordinator", "supervisor",
    "councillor", "mayor", "alderman", "deputy",
}

#  eSCRIBE separates the name and topic fields with " - " (space-dash-space).
#  Matching on bare "-" would split a hyphenated surname like
#  "Christopherson-Cote" in two, so the separator requires the spaces.
#
#  The topic is a filename, so it carries filename debris: eSCRIBE writes
#  "_Redacted" on documents with personal information removed, and a
#  re-upload adds "(1)".  Published verbatim this read "Registered to
#  speak on: Fixed-term Loan to TCU Place_Redacted(1)".
_RTS_ATTACHMENT_RE = re.compile(
    r"RTS\s+-\s+(?P<name>.+?)\s+-\s+(?P<topic>.+?)"
    r"(?:_Redacted)?(?:\s*\(\d+\))?\.pdf$",
    re.IGNORECASE,
)


def extract_presentations(item: AgendaItem) -> list[Presentation]:
    """Return the guest presentations found in one agenda item.

    Procedural items are skipped, and that is not a nicety.  eSCRIBE hangs
    the meeting's whole document package off ADJOURNMENT — 125 attachments
    on the June 24 council meeting — so every Request to Speak in the
    meeting was found a second time there.  The published count was double
    the truth (22 filings from 11 people) and the detail page grew a
    "Presentations" block under Adjournment.
    """
    if item.is_recess or is_procedural(item.title or ""):
        return []
    results = _from_minutes(item.content or "")
    results.extend(_from_registered_attachments(item.attachments or [], results))
    return results


def merge_substance(item: dict) -> list[dict]:
    """The item's speaker roster with cached remarks folded in.

    Two producers meet here.  The **roster** is rebuilt from the agenda on
    every page build, so it is always current and needs no cache.  What
    each speaker **argued** costs a Gemini call, so it is cached on the
    summaries branch with the rest of the ItemSummary — and a meeting a
    summarize run has not reached yet has a roster and no substance.

    Keyed by name, which is safe because the substance pass is given the
    roster as an enum and cannot answer with anyone else.
    """
    roster = item.get("presentations") or []
    cached = ((item.get("summary") or {}).get("presentations")) or []
    said_by_name = {
        entry.get("name"): entry
        for entry in cached
        if isinstance(entry, dict) and entry.get("name")
    }
    merged = []
    for entry in roster:
        if not isinstance(entry, dict):
            continue
        found = said_by_name.get(entry.get("name"))
        if not found:
            merged.append(dict(entry))
            continue
        combined = dict(entry)
        combined["said"] = list(found.get("said") or [])
        combined["stance"] = found.get("stance") or entry.get("stance") or ""
        # A Request to Speak filing is a name and a filename, so the
        # organization can only come from what the speaker said at the
        # podium. The minutes' version wins when there is one.
        if not (entry.get("organization") or "").strip():
            combined["organization"] = found.get("organization") or ""
        merged.append(combined)
    return merged


def _from_minutes(content: str) -> list[Presentation]:
    text = clean_entities(content)
    results: list[Presentation] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        m = _VERB_RE.search(sentence)
        if not m:
            continue
        who = sentence[: m.start()].strip().rstrip(",")
        if not who:
            continue
        if who.split()[0].lower() in _NON_NAME_LEADS:
            continue
        parts = [p.strip() for p in who.split(",") if p.strip()]
        name = parts[0]
        if not _NAME_RE.match(name):
            continue
        if _is_role_not_person(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(Presentation(
            name=name,
            organization=_organization_from_parts(parts[1:]),
            stance=_classify_stance(sentence[m.start():]),
            summary=_trim(sentence),
            source="minutes",
        ))
    return results


def _is_role_not_person(name: str) -> bool:
    """True when the "name" is a job title — staff or a council member."""
    return any(word.lower().strip(".,") in _ROLE_WORDS for word in name.split())


def _organization_from_parts(parts: list[str]) -> str:
    # Stop at the first fragment that is not itself a title/org clause
    # ("Executive Director", "The Salvation Army") — anything starting
    # lowercase ("was in the gallery and...") is the rest of the sentence
    # that spilled past the comma, not another appositive.
    kept = []
    for part in parts:
        if not part or not part[0].isupper():
            break
        kept.append(part)
    return ", ".join(kept)


def _classify_stance(tail: str) -> str:
    lowered = tail.lower()
    if "support" in lowered:
        return "support"
    if "concern" in lowered or "oppos" in lowered:
        return "concern"
    return ""


def _trim(text: str, limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _from_registered_attachments(
    attachments: list[dict], already: list[Presentation],
) -> list[Presentation]:
    known = {p.name.lower() for p in already}
    results: list[Presentation] = []
    for att in attachments:
        m = _RTS_ATTACHMENT_RE.search(att.get("name", ""))
        if not m:
            continue
        name = m.group("name").strip()
        if not name or name.lower() in known:
            continue
        known.add(name.lower())
        topic = m.group("topic").strip()
        results.append(Presentation(
            name=name,
            summary=f"Registered to speak on: {topic}" if topic else "Registered to speak.",
            source="registered",
        ))
    return results
