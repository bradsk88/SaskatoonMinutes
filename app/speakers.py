"""Extracts the guest speakers who addressed council on an agenda item.

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

import difflib
import re
import zlib

from app.agenda_items import is_procedural
from app.agenda_text import clean_entities
from app.models import AgendaItem, Speaker

# A stable set of verbs eSCRIBE's minutes use to introduce a delegate's
# turn at the podium. Deliberately narrow: a staff member "responding to a
# question" is not a guest speaker, and casting the net wider starts
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
# eSCRIBE is not consistent about the prefix: most filings are
# "RTS - Name - Topic.pdf", but some clerks write the words out —
# "Request to Speak - Robert Clipperton - Bus Riders of Saskatoon.pdf".
# Matching only the acronym lost every speaker on the July 29 transit
# bylaw: they filed, they spoke, and no list of them existed anywhere.
_RTS_ATTACHMENT_RE = re.compile(
    r"(?:RTS|Request to Speak)\s+-\s+(?P<name>.+?)\s+-\s+(?P<topic>.+?)"
    r"(?:_Redacted)?(?:\s*\(\d+\))?\.pdf$",
    re.IGNORECASE,
)


def extract_speakers(item: AgendaItem) -> list[Speaker]:
    """Return the guest speakers found in one agenda item.

    Procedural items are skipped, and that is not a nicety.  eSCRIBE hangs
    the meeting's whole document package off ADJOURNMENT — 125 attachments
    on the June 24 council meeting — so every Request to Speak in the
    meeting was found a second time there.  The published count was double
    the truth (22 filings from 11 people) and the detail page grew a
    "Guest speakers" block under Adjournment.
    """
    if item.is_recess or is_procedural(item.title or ""):
        return []
    results = _from_minutes(item.content or "")
    results.extend(_from_registered_attachments(item.attachments or [], results))
    return results


# Someone who came to speak for nobody but themselves.  Sixty-three of
# the archive's seventy-two speakers came on behalf of an organization;
# these nine did not, and a blank where every other speaker has a chip
# reads as missing data rather than as the fact that it is.
UNAFFILIATED_LABEL = "Resident"

# How many chip colours the palette holds.  A colour is a recognition
# aid, not an identifier: forty-one organizations share ten colours, so
# two of them will collide, and the name is still right there on the chip.
ORGANIZATION_COLOURS = 10


# Job titles the model put in the organization field before the prompt
# told it not to.  Deliberately separate from ``_ROLE_WORDS``: that set
# disqualifies a *name* and has to stay narrow enough to let "Chief Kelly
# Wolfe" through, while this one only ever reads the words in front of a
# comma in an organization, where "chief" carries no such risk.
_ORG_ROLE_TAIL = {
    "administrator", "ceo", "cfo", "chair", "chairman", "chairperson",
    "chairwoman", "commissioner", "coo", "coordinator", "director",
    "executive", "manager", "officer", "owner", "president",
    "superintendent", "supervisor", "treasurer",
}


def clean_organization(organization: str) -> str:
    """Drop a job title the model left in front of the organization.

    "Executive Director, The Salvation Army" is the Salvation Army; the
    title tells a reader nothing, because every organization sends one.
    The prompt asks for the body alone now, but the archive was written
    before it did and a re-run costs a full summarize pass — so this
    cleans what is already cached, every build, for free.

    Only the unambiguous shape: a comma with a role noun in front of it.
    "CEO of Nutrien Wonderhub" and "Director of Planning and Development"
    are the same shape as each other and mean different things — one is a
    job at a named organization, the other is a job at no organization at
    all — and no rule here can tell them apart. They are left alone
    rather than guessed at, because the fallback for an emptied field is
    "Resident", and calling a CEO a resident is a worse error than
    printing their title.
    """
    text = " ".join((organization or "").split())
    head, sep, tail = text.partition(",")
    if not sep or not tail.strip():
        return text
    words = head.strip().rstrip(".").split()
    if words and words[-1].lower() in _ORG_ROLE_TAIL:
        return tail.strip()
    return text


def organization_label(organization: str) -> str:
    """What the chip says: the organization, or that there was none."""
    return clean_organization(organization) or UNAFFILIATED_LABEL


def organization_color(organization: str) -> int | None:
    """Which palette slot an organization gets. ``None`` when it has none.

    The point is that Saskatoon Police Service is the same colour on
    every page a reader ever sees it on, so the answer must not move.
    ``hash()`` is salted per interpreter and would give one colour in the
    Flask app and another in the static build — and a different one again
    on the next build.  crc32 does not move.
    """
    name = clean_organization(organization).casefold()
    if not name:
        return None
    return zlib.crc32(name.encode("utf-8")) % ORGANIZATION_COLOURS


# A surname this long is distinctive enough to stand alone as evidence
# the chair introduced its owner. Shorter ones need the full name, or
# "Taylor Street" puts a resident at a podium they never reached.
_HEARD_MIN_SURNAME = 7


def _close_word(probe: str, words: list[str], threshold: float) -> bool:
    """True when some word is a Whisper-garbled rendering of *probe*.

    Words under four letters are excluded: "of" sits at 0.57 from
    "wolfe", and a two-letter word is close to everything.
    """
    return any(
        len(w) >= 4
        and abs(len(w) - len(probe)) <= 3
        and difflib.SequenceMatcher(None, probe, w).ratio() >= threshold
        for w in words
    )


# A title leading the roster entry ("Chief Kelly Wolfe") is not a first
# name; matching on it puts every mention of the chief at his podium.
_FIRST_NAME_TITLES = {"chief", "dr", "mayor", "councillor", "elder"}


def _name_match_tier(name: str, text: str) -> str | None:
    """How *text* (lowercased) names this speaker: "exact", "fuzzy", None.

    Whisper mangles names ("Wilgenhof" became "Wilgunhof", "Naytowhow"
    became "nitaohau"), so a full name match is not required.  A long
    surname stands alone, exact or close (0.8 reads a letter swap, not
    a different name); a distinctive first name needs a vaguely
    surname-shaped word beside it, which catches the garblings too
    heavy for the surname rule without putting every "Robert" at a
    podium.

    The tier matters because fuzzy is wrong often enough — "gather"
    sits at 0.86 from "gauthier" — that a fuzzy match on its own is
    not evidence of a podium moment; see ``mark_timestamps``.  0.75
    reads a clipped name ("Mr. Clipper" for Clipperton); below that
    is a different name.
    """
    if name in text:
        return "exact"
    parts = name.split()
    first, last = parts[0], parts[-1]
    if first in _FIRST_NAME_TITLES and len(parts) > 2:
        first = parts[1]
    # Words, not substrings: "robert" sits inside "robertson", and a
    # trailing period ("nitaohau.") sinks the fuzzy comparison.
    words = re.findall(r"[a-z'\-]+", text)
    surnames = last.split("-") if "-" in last else [last]
    for surname in surnames:
        if len(surname) >= _HEARD_MIN_SURNAME and surname in text:
            return "exact"
    for surname in surnames:
        if len(surname) >= _HEARD_MIN_SURNAME and _close_word(surname, words, 0.75):
            return "fuzzy"
    if (
        len(first) >= 4
        and first in words
        and any(_close_word(s, words, 0.45) for s in surnames)
    ):
        return "fuzzy"
    return None


def _name_in_text(name: str, text: str) -> bool:
    """True when *text* (lowercased) names this speaker, exact or fuzzy."""
    return _name_match_tier(name, text) is not None


# A fuzzy name match is only a podium moment when the chair is doing
# podium things in the same breath.  Exact matches need no such
# chaperone.
_INTRO_CUE = re.compile(
    r"speaker|podium|microphone|welcom|introduc|name is|five minutes"
)


def mark_timestamps(item: dict, segments: list[dict]) -> None:
    """Stamp each speaker with the moment the chair introduced them.

    The chair names every speaker at the podium, so the first segment in
    the item's window that names them is within seconds of when they
    started speaking — the precision a "jump to where they spoke" link
    needs, where the item's own start bookmark is not.  A speaker the
    transcript never names keeps no stamp, and the UI leaves their name
    unlinked rather than seeking to a guess.  Mutates
    ``item["speakers"]``; call after ``merge_remarks``.
    """
    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return
    window = [
        s for s in segments
        if s.get("end_ms", 0) > start and s.get("start_ms", 0) < end
    ]
    if not window:
        return
    for speaker in item.get("speakers") or []:
        if not isinstance(speaker, dict):
            continue
        name = " ".join((speaker.get("name") or "").lower().split())
        if not name:
            continue
        prev_text = ""
        for seg in window:
            text = seg.get("text", "").lower()
            tier = _name_match_tier(name, text)
            # The cue may be one breath behind: \"We have one more
            # speaker.\" / \"Sorry, Mr. Clipper.\" is one introduction
            # split across two segments.
            if tier == "exact" or (
                tier == "fuzzy"
                and _INTRO_CUE.search(prev_text + " " + text)
            ):
                speaker["time_start_ms"] = seg.get("start_ms")
                break
            prev_text = text
    # Speakers are shown in the order they spoke. The sort is stable,
    # so an unstamped speaker keeps their roster place behind everyone
    # whose moment is known.
    speakers = item.get("speakers")
    if speakers:
        speakers.sort(
            key=lambda s: (
                s.get("time_start_ms") is None,
                s.get("time_start_ms") or 0,
            )
        )


def mark_heard(item: dict, segments: list[dict]) -> None:
    """Flag registered speakers the transcript actually captured.

    An RTS filing proves intent, not attendance, and the substance pass
    only produces remarks for the speakers it is handed — so \"no
    remarks\" cannot tell a no-show from a speaker the pipeline missed.
    The chair, however, introduces every speaker by name during their
    item, a deterministic check that needs no model.

    Mutates ``item[\"speakers\"]``; call after ``merge_remarks``.
    """
    start = item.get("time_start_ms")
    end = item.get("time_end_ms")
    if start is None or end is None:
        return
    heard_text = " ".join(
        s.get("text", "")
        for s in segments
        if s.get("end_ms", 0) > start and s.get("start_ms", 0) < end
    ).lower()
    if not heard_text:
        return
    for speaker in item.get("speakers") or []:
        if not isinstance(speaker, dict):
            continue
        if speaker.get("source") != "registered" or speaker.get("said"):
            continue
        name = " ".join((speaker.get("name") or "").lower().split())
        if not name:
            continue
        if not _name_in_text(name, heard_text):
            continue
        speaker["heard"] = True
        # The introduction often names who they came for — "the first
        # speaker Robert Clipperton with Bus Riders of Saskatoon" — and
        # a filing never does. An org that attended belongs in the
        # digest as itself, not rolled up into "N Residents".
        if not (speaker.get("organization") or "").strip():
            m = re.search(
                re.escape(name)
                + r"\s+(?:with|from|of|representing)\s+([a-z][a-z &'\-]{2,60})",
                heard_text,
            )
            if m:
                org = m.group(1)
                # Whisper runs on past the name: "with bus riders of
                # saskatoon and you are well familiar". An org name is
                # short; the first discourse marker ends it.
                for stop in (" and ", " you ", " we ", " i ", " thank ",
                             " members ", " your ", " Mr ".lower(), " Ms ".lower(),
                             ",", "."):
                    org = org.split(stop)[0]
                org = org.strip()
                if org:
                    # Whisper lowercases freely; an org chip reads wrong
                    # in all-lowercase. Small words stay small unless
                    # they lead.
                    small = {"of", "and", "the", "for", "on", "in"}
                    cased = [
                        w if w in small and i else w.capitalize()
                        for i, w in enumerate(org.split())
                    ]
                    speaker["organization"] = " ".join(cased)


def merge_remarks(item: dict) -> list[dict]:
    """The item's speaker roster with cached remarks folded in.

    Two producers meet here.  The **roster** is rebuilt from the agenda on
    every page build, so it is always current and needs no cache.  What
    each speaker **argued** costs a Gemini call, so it is cached on the
    summaries branch with the rest of the ItemSummary — and a meeting a
    summarize run has not reached yet has a roster and no substance.

    Keyed by name, which is safe because the substance pass is given the
    roster as an enum and cannot answer with anyone else.
    """
    roster = item.get("speakers") or []
    cached = ((item.get("summary") or {}).get("speakers")) or []
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
    # Decided once, here, so the card and the detail page never disagree
    # about what a speaker's chip says or what colour it is -- and so the
    # job titles already cached on the summaries branch are cleaned on
    # every build rather than waiting on a re-run.
    for entry in merged:
        entry["organization"] = clean_organization(entry.get("organization") or "")
        entry["org_label"] = organization_label(entry["organization"])
        entry["org_color"] = organization_color(entry["organization"])
    return merged


def _from_minutes(content: str) -> list[Speaker]:
    text = clean_entities(content)
    results: list[Speaker] = []
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
        organization = _organization_from_parts(parts[1:])
        if _is_city_unit(organization):
            continue
        if _is_staff_presenting(sentence, name, organization):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(Speaker(
            name=name,
            organization=organization,
            stance=_classify_stance(sentence[m.start():]),
            summary=_trim(sentence),
            source="minutes",
        ))
    return results


def _is_role_not_person(name: str) -> bool:
    """True when the "name" is a job title — staff or a council member."""
    return any(word.lower().strip(".,") in _ROLE_WORDS for word in name.split())


# A member of the public does not present *the report*.  The report is
# Administration's, and "presented the report and responded to questions
# of the Board" is the clerk's standing formula for a staff member
# working through their own item.  ``_VERB_RE`` matches "presented",
# which is how thirty-two staff appearances were published as guest
# speakers -- the police chief, the city auditor, the fire chief, the
# Development Review Manager six times over.
_PRESENTED_RE = re.compile(
    r"\bpresented\s+the\s+(?:report|item|presentation)\b", re.IGNORECASE,
)

# The other half of that formula.  Council and its committees put
# questions to the people who work for them.
_ANSWERED_THE_BODY_RE = re.compile(
    r"\b(?:responded|answered)\b[^.]{0,40}?\bquestions\s+of\s+(?:the\s+)?"
    r"(?:board|committee|council)\b",
    re.IGNORECASE,
)

# Ranks and titles that only an employee of the body carries.  Used only
# to settle a bare "X presented the report." with no formula behind it.
#
# "Chief" alone is deliberately absent, for the same reason it is absent
# from ``_ROLE_WORDS``: Chief Kelly Wolfe of Muskeg Lake Cree Nation
# addressed council as a guest, and a rule that reads an Indigenous
# leader's title as a city job is worse than the staff it would catch.
# Every city chief in the archive is caught by the formula instead.
_STAFF_TITLES = {
    "auditor", "commissioner", "constable", "deputy", "director",
    "inspector", "manager", "sergeant", "solicitor", "superintendent",
}


# The City's own organizational units.  A guest speaker comes from a
# business, a First Nation, an association or a neighbourhood; nobody
# introduces themselves as a Division.  This is the employer test that
# ``_ROLE_WORDS`` cannot do -- that one reads a person's name, and
# "Darryl Dawson" is a perfectly good name.  He is the Development
# Review Manager, Community Services Division, and he appeared as a
# guest speaker six times.
#
# Matched after ``clean_organization`` has taken the job title off, so
# "Executive Director, The Salvation Army" is tested as "The Salvation
# Army" and stays.
# Anywhere in the name: "City of Saskatoon" and "City of Saskatoon
# Administration" are the same employer.
_THE_CITY_RE = re.compile(r"\bcity\s+of\s+saskatoon\b", re.IGNORECASE)

# Only as the last word, so a "Division" that is the unit's own name is
# caught while one buried in a longer title is not.
_CITY_DIVISION_RE = re.compile(r"\bdivision$", re.IGNORECASE)


def _is_city_unit(organization: str) -> bool:
    """True when the organization is a part of the City, not a guest."""
    name = clean_organization(organization)
    return bool(_THE_CITY_RE.search(name) or _CITY_DIVISION_RE.search(name))


def _is_staff_presenting(sentence: str, name: str, organization: str) -> bool:
    """True when the minutes are narrating staff working through an item.

    Two ways to be sure, because one is not enough.  The clerk's full
    formula — presented the report, then answered the body's questions —
    is unambiguous on its own.  A bare "City Auditor Thomson presented
    the report." is not, so it also needs a rank or title that only an
    employee carries.

    That second gate is the whole point of the split.  "Auntie Advocate
    Swiftwolfe presented the report with a PowerPoint" is the Office of
    the Matriarchs presenting its own work, and dropping her would
    silence the guest this feature exists to surface.  Erring toward
    keeping a speaker is the right way to be wrong here: a staff row is
    clutter, a missing delegate is a person the page says was not there.
    """
    if not _PRESENTED_RE.search(sentence):
        return False
    if _ANSWERED_THE_BODY_RE.search(sentence):
        return True
    words = f"{name} {organization}".lower().replace(",", " ").split()
    return any(word.strip(".") in _STAFF_TITLES for word in words)


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
    attachments: list[dict], already: list[Speaker],
) -> list[Speaker]:
    known = {p.name.lower() for p in already}
    results: list[Speaker] = []
    for att in attachments:
        m = _RTS_ATTACHMENT_RE.search(att.get("name", ""))
        if not m:
            continue
        name = m.group("name").strip()
        if not name or name.lower() in known:
            continue
        known.add(name.lower())
        topic = m.group("topic").strip()
        results.append(Speaker(
            name=name,
            summary=f"Registered to speak on: {topic}" if topic else "Registered to speak.",
            source="registered",
        ))
    return results
