"""eSCRIBE meeting-type slugs and tab metadata.

These are presentation-layer values shared between the Flask app and the
scripts.  Lives outside the scraper/transport so the seam stays bytes-only.
"""

# eSCRIBE uses the meeting type display name (not a GUID) for filtering.
MEETING_TYPE = "CITY COUNCIL AGENDA - REGULAR BUSINESS MEETING"

# Named meeting type tabs shown in the UI.  Each entry maps a short slug to
# the eSCRIBE "type" string used by the PastMeetings API.
MEETING_TABS: list[dict] = [
    {"slug": "council",        "label": "Council",              "type": "CITY COUNCIL AGENDA - REGULAR BUSINESS MEETING"},
    {"slug": "public-hearing", "label": "Public Hearing",       "type": "CITY COUNCIL AGENDA - PUBLIC HEARING MEETING"},
    {"slug": "budget",         "label": "Budget",               "type": "CITY COUNCIL AGENDA - BUDGET"},
    {"slug": "governance",     "label": "Governance & Priorities", "type": "GOVERNANCE AND PRIORITIES COMMITTEE - PUBLIC"},
    {"slug": "planning",       "label": "Planning & Dev",       "type": "SPC-PLANNING, DEVELOPMENT AND COMMUNITY SERVICES - PUBLIC"},
    {"slug": "transportation", "label": "Transportation",       "type": "SPC-TRANSPORTATION - PUBLIC"},
    {"slug": "environment",    "label": "Environment & Utilities", "type": "SPC-ENVIRONMENT, UTILITIES AND CORPORATE SERVICES - PUBLIC"},
    {"slug": "finance",        "label": "Finance",              "type": "SPC-FINANCE - PUBLIC"},
    {"slug": "police",         "label": "Police Board",         "type": "BOARD OF POLICE COMMISSIONERS - PUBLIC"},
    {"slug": "municipal-planning", "label": "Municipal Planning", "type": "MUNICIPAL PLANNING COMMISSION"},
    {"slug": "heritage",       "label": "Heritage",             "type": "MUNICIPAL HERITAGE ADVISORY COMMITTEE"},
    {"slug": "accessibility",  "label": "Accessibility",        "type": "SASKATOON ACCESSIBILITY ADVISORY COMMITTEE"},
    {"slug": "env-advisory",   "label": "Env Advisory",         "type": "SASKATOON ENVIRONMENTAL ADVISORY COMMITTEE"},
    {"slug": "diversity",      "label": "Diversity & Inclusion", "type": "DIVERSITY, EQUITY AND INCLUSION ADVISORY COMMITTEE"},
    {"slug": "public-art",     "label": "Public Art",           "type": "PUBLIC ART ADVISORY COMMITTEE"},
    {"slug": "civic-naming",   "label": "Civic Naming",         "type": "CIVIC NAMING COMMITTEE"},
]

# Quick lookup from slug → eSCRIBE type string.
_SLUG_TO_TYPE = {tab["slug"]: tab["type"] for tab in MEETING_TABS}

# Quick lookup from eSCRIBE type string → tab.  The calendar endpoint
# returns every civic body; the app only covers the bodies above, so this
# is how a Scheduled Meeting finds its tab (or is dropped).
TYPE_TO_TAB = {tab["type"]: tab for tab in MEETING_TABS}
