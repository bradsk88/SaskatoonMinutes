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
    # A motion to defer/table that carried is a deferral, not an approval
    if re.search(r"\bDEFER(?:RED)?\b", rec_upper) or "TABLED" in rec_upper:
        return "Deferred"
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
