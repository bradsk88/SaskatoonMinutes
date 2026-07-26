"""Domain interpretation of agenda items.

Classifiers that read agenda-item metadata (title, recommendation, vote
result) and return a domain label. No raw text manipulation lives here —
see ``app.agenda_text`` for that.
"""

from __future__ import annotations

import re


# Common procedural agenda item keywords. A Procedural Item is one whose
# title matches any of these phrases (case-insensitive substring).
PROCEDURAL_KEYWORDS = {
    "call to order", "adjournment", "roll call", "adoption of agenda",
    "confirmation of agenda", "confirmation of minutes", "adoption of minutes",
    "declarations of conflict", "declaration of conflict",
    "communications to council", "o canada",
    "consent agenda", "public acknowledgments", "public acknowledgements",
    "question period", "inquiries", "in camera session", "urgent business",
    "committee reports (not on consent",
    "unfinished business", "giving notice", "motions (notice",
    "legislative reports", "administrative reports", "other reports",
    "in remembrance", "council members",
}


def is_procedural(title: str) -> bool:
    """True when ``title`` matches a Procedural Item keyword."""
    title_lower = title.lower().strip()
    return any(kw in title_lower for kw in PROCEDURAL_KEYWORDS)


def _has_own_substance(item: dict) -> bool:
    """True when the item carries official text of its own."""
    return bool(
        (item.get("recommendation") or "").strip()
        or (item.get("content") or "").strip()
    )


# "That the report be received as information" records that council
# resolved nothing.  An item whose recommendation is only this has no
# substance to summarize, and with no transcript either there is nothing
# to write but the title back at the reader.
_BOILERPLATE_REC_RE = re.compile(
    r"^that the (?:report|information|presentation|correspondence|"
    r"communication|minutes|letter|petition) be (?:received|noted|filed)",
    re.IGNORECASE,
)


def is_boilerplate_recommendation(text: str) -> bool:
    """True when the recommendation says council resolved nothing specific."""
    return bool(_BOILERPLATE_REC_RE.search(text.strip()))


def is_section_header(item: dict) -> bool:
    """True when the entry is a structural container, not an agenda item.

    Headers like ``COMMITTEE REPORTS`` or ``Standing Policy Committee on
    Finance`` carry no recommendation and no content, and they either have
    no time span at all or borrow their parent's.  They exist to group the
    items beneath them and never get an ItemSummary.
    """
    if _has_own_substance(item):
        return False
    return item.get("time_start_ms") is None or bool(item.get("timestamp_inherited"))


def count_agenda_items(items: list[dict]) -> int:
    """How many real agenda items a meeting has.

    What "N other items" on an index card counts.  Recesses and Section
    Headers are excluded: a reader counting the meeting's business does
    not count the break or the heading above the business.  Procedural
    items *are* counted — the roll call is on the agenda and appears on
    the detail page, so leaving it out would make the number disagree
    with the page it points at.
    """
    return sum(
        1 for item in items
        if not item.get("is_recess") and not is_section_header(item)
    )


def is_consent_item(item: dict) -> bool:
    """True when the item passed in the consent block without individual debate.

    Detected by an inherited timestamp: the item shares its parent
    section's span because council approved the whole block in one motion,
    so no distinct span exists for it.  A Consent Item still has real
    official text — that is what separates it from a Section Header, which
    also inherits a timestamp but says nothing of its own.
    """
    if not item.get("timestamp_inherited"):
        return False
    if item.get("is_recess"):
        return False
    if is_procedural(item.get("title") or ""):
        return False
    # The recommendation is what council actually resolved, and with no
    # transcript it is the only account of what the item does.  If it is
    # boilerplate there is nothing to summarize -- content alone is
    # supporting material (attachments, letters of support), not a
    # statement of the decision.  Length is not the signal: a 90-character
    # "That Councillor MacDonald be appointed to the Meewasin Valley
    # Authority" summarizes fine, while a longer boilerplate does not.
    rec = (item.get("recommendation") or "").strip()
    return bool(rec) and not is_boilerplate_recommendation(rec)


# "That City Council consider Bylaw No. 10169" is the standing form of a
# public-hearing recommendation, and the clerk's own note calls the vote
# on it first reading.  "That Bylaw No. X be given first reading" is the
# same motion written out.
_FIRST_READING_RE = re.compile(
    r"\bCONSIDER\s+BYLAW\b|\bFIRST\s+READING\b",
)


def format_outcome(vote_result: str, recommendation: str) -> str:
    """Convert raw vote result + recommendation into a short outcome label."""
    if not vote_result and not recommendation:
        return "Discussed"

    if not vote_result:
        return "Recommended"

    upper = vote_result.upper()
    rec_upper = recommendation.upper()
    counts = re.search(r"\((\d+)\s+to\s+(\d+)\)", vote_result)
    tally = f" ({counts.group(1)}-{counts.group(2)})" if counts else ""

    if "DEFEATED" in upper:
        return f"Defeated{tally}"
    if "DEFERRED" in upper or "TABLED" in upper:
        return "Deferred"
    if "WITHDRAWN" in upper:
        return "Withdrawn"
    # What kind of action was moved is in the RECOMMENDATION; whether it
    # happened is in the vote.  The motion has to be read first, or every
    # carried motion looks like an approval regardless of what it did.
    #
    # A motion to defer/table that carried is a deferral, not an approval.
    if re.search(r"\bDEFER(?:RED)?\b", rec_upper) or "TABLED" in rec_upper:
        return "Deferred"
    # A committee that carried a motion to recommend something has
    # recommended it.  City Council has not acted, and saying "Approved"
    # tells a resident the opposite of what happened.
    if re.search(r"\bRECOMMEND(?:S|ED)?\s+TO\s+(?:CITY\s+)?COUNCIL\b", rec_upper):
        return f"Recommended to Council{tally}"
    # "That the information be received" is council declining to decide.
    if re.search(r"\bBE\s+(?:RECEIVED|NOTED|FILED)\b", rec_upper):
        return "Received as information"
    # A public-hearing item's recorded vote is on FIRST READING -- the
    # motion that puts the bylaw in front of council so the hearing can
    # happen.  Whether the rezoning passes is decided by later readings,
    # which eSCRIBE records elsewhere or not at all.  Labelling that vote
    # "Approved" tells a resident the application succeeded when the
    # record does not say so: on the 2026-04-29 hearing, "Approved" sat
    # above a description reporting that council denied the application.
    #
    # A third of the eval fixtures are these, because a public-hearing
    # agenda is almost entirely bylaws.
    if _FIRST_READING_RE.search(rec_upper):
        return f"First reading passed{tally}"
    if "UNANIMOUSLY" in upper:
        return "Approved"
    if "CARRIED" in upper:
        return f"Approved{tally}"
    if "RECEIVED" in upper or "NOTED" in upper:
        return "Received"
    return vote_result


# Maps urban-development category labels to keyword patterns.
# Matched case-insensitively against title + recommendation + content.
TOPIC_CATEGORIES = {
    "Homelessness": [
        r"homeless", r"drop.in", r"shelter\b", r"supportive housing",
        r"vulnerable\s+p", r"encampment", r"unshelter",
    ],
    "Housing": [
        r"affordable housing", r"housing\b", r"residential",
        r"infill\b", r"densif", r"rental",
    ],
    "Zoning & Dev": [
        r"rezon", r"zoning\b", r"land\s+use", r"corridor\s+plan",
        r"redevelop", r"subdivision", r"building\s+standard",
        r"development\s+review", r"reserve\s+redesignation",
        r"land\s+development", r"neighbourhood\s+plan",
    ],
    "Transit": [
        r"\btransit\b", r"bus rapid", r"\bBRT\b", r"grade.separation",
        r"rail\s+(grade|cross)", r"public\s+transport",
    ],
    "Active Transport": [
        r"active\s+transport", r"cycling", r"\bbike\b", r"bicycle",
        r"bike\s+lane", r"protected.*\blane", r"multi.use\s+(path|trail)",
        r"pedestrian", r"sidewalk", r"crosswalk", r"walkability",
    ],
    "Traffic": [
        r"\btraffic\b", r"intersection", r"speed\s+limit",
        r"road\s+(clos|safe|improv)", r"\btransportation\b",
    ],
    "Greenspace": [
        r"\bpark\b(?!ing)", r"\belm\b", r"urban\s+forest", r"tree\b",
        r"green\s*space", r"natural\s+area", r"river\s*bank",
        r"meewasin", r"\btrail\b",
    ],
    "Small Business": [
        r"business\s+improvement", r"\bBID\b", r"small\s+business",
        r"merchant", r"commercial\s+district", r"storefront",
    ],
    "Infrastructure": [
        r"water\s+main", r"waterworks", r"sewer", r"storm\s*water",
        r"utilit", r"landfill", r"waste\b", r"capital\s+project",
        r"bridge\b", r"road\s+construct", r"pipeline\b",
    ],
    "Public Safety": [
        r"police", r"fire\s+(?:dep|serv|stat)", r"\bSPS\b",
        r"community\s+safety", r"crime\b", r"bylaw\s+enforce",
    ],
    "Recreation": [
        r"ice\s+sheet", r"arena\b", r"leisure", r"recreation",
        r"pool\b", r"sport\s+facil", r"playground", r"golf\s+course",
    ],
    "Property Tax": [
        r"property\s+tax", r"tax\s+lien", r"mill\s+rate",
        r"assessment\b", r"tax\s+levy",
    ],
    "Arts & Culture": [
        r"public\s+art", r"art\s+gallery", r"cultur",
        r"heritage\b", r"festival\b", r"mural\b",
    ],
    "Environment": [
        r"climate\b", r"emission", r"sustainab", r"solar\b",
        r"energy\s+effic", r"electric\s+vehicle", r"\bEV\b",
        r"greenhouse\s+gas", r"carbon\b", r"environmental",
    ],
}


def categorize_topic(title: str, recommendation: str, content: str = "") -> list[str]:
    """Return up to 2 urban-development category labels for an agenda item."""
    combined = title + " " + recommendation + " " + content
    matches = []
    for category, patterns in TOPIC_CATEGORIES.items():
        for pat in patterns:
            if re.search(pat, combined, re.IGNORECASE):
                matches.append(category)
                break
        if len(matches) >= 2:
            break
    return matches


def is_major_decision(title: str, recommendation: str, is_contested: bool) -> bool:
    """Determine whether a decision warrants visual highlighting."""
    if is_contested:
        return True
    combined = title + " " + recommendation
    if re.search(r"\$[\d,.]+", combined):
        return True
    title_lower = title.lower()
    for keyword in ("bylaw", "budget", "acquisition", "contract", "tax"):
        if keyword in title_lower:
            return True
    return False
