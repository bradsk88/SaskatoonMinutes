"""
Scraper for Saskatoon City Council meetings from the eSCRIBE platform.

Fetches meeting lists, agenda items, and video timestamp bookmarks from
pub-saskatoon.escribemeetings.com.
"""

import re
import json
import urllib3
import requests
from dataclasses import dataclass, asdict, field
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://pub-saskatoon.escribemeetings.com"

# Browser-like headers required by the eSCRIBE AJAX endpoints.
_AJAX_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/MeetingsCalendarView.aspx",
}

_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}

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


@dataclass
class AgendaItem:
    item_id: int
    title: str
    content: str
    section_number: str  # e.g. "4.1.2"
    time_start_ms: int | None = None
    time_end_ms: int | None = None
    recommendation: str = ""
    vote_result: str = ""
    vote_detail: str = ""
    is_contested: bool = False
    timestamp_inherited: bool = False
    is_recess: bool = False
    attachments: list = field(default_factory=list)

    @property
    def time_start_formatted(self) -> str | None:
        if self.time_start_ms is None:
            return None
        total_seconds = self.time_start_ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time_start_formatted"] = self.time_start_formatted
        d["is_contested"] = self.is_contested
        d["timestamp_inherited"] = self.timestamp_inherited
        d["is_recess"] = self.is_recess
        return d


@dataclass
class Meeting:
    meeting_id: str
    title: str
    date: str  # ISO date string
    start_time: str
    location: str
    has_video: bool
    has_agenda: bool
    video_url: str | None = None
    is_cancelled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_past_meetings(page: int = 1, meeting_type: str | None = None) -> tuple[list[Meeting], int]:
    """Fetch a page of past City Council meetings from eSCRIBE.

    *meeting_type* is the eSCRIBE display-name string (e.g.
    ``"CITY COUNCIL AGENDA - REGULAR BUSINESS MEETING"``).  When *None*
    the default :data:`MEETING_TYPE` constant is used.

    Returns (meetings, total_count).
    """
    url = f"{BASE_URL}/MeetingsCalendarView.aspx/PastMeetings"
    payload = {
        "type": meeting_type or MEETING_TYPE,
        "pageNumber": page,
    }
    resp = requests.post(url, json=payload, headers=_AJAX_HEADERS, timeout=30, verify=False)
    resp.raise_for_status()

    data = resp.json().get("d", {})
    total_count = data.get("TotalCount", 0)
    raw_meetings = data.get("Meetings", [])

    meetings = []
    for m in raw_meetings:
        meeting_id = m.get("Id", "")
        has_video = m.get("HasVideo", False)
        is_cancelled = m.get("Cancelled", False) or any(
            "cancel" in link.get("Title", "").lower()
            for link in m.get("MeetingLinks", [])
        )

        meeting = Meeting(
            meeting_id=meeting_id,
            title=m.get("MeetingType", "City Council Meeting").strip(),
            date=_parse_escribemeetings_date(m.get("Start", "")),
            start_time=m.get("FormattedStart", ""),
            location=m.get("LocationName", ""),
            has_video=has_video,
            has_agenda=m.get("HasAgenda", False),
            video_url=_build_video_url(meeting_id) if has_video else None,
            is_cancelled=is_cancelled,
        )
        meetings.append(meeting)

    return meetings, total_count


def fetch_meeting_detail(meeting_id: str, include_votes: bool = False) -> dict:
    """Fetch the full meeting page and extract agenda items + video bookmarks.

    When *include_votes* is True, also fetches the PostMinutes page to obtain
    vote results and merges recommendation text + vote data into each item.

    Returns a dict with 'agenda_items' and 'video_url'.
    """
    url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=Agenda&lang=English"
    resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    html = resp.text

    bookmarks = _extract_bookmarks(html)
    agenda_items = _extract_agenda_items(html, bookmarks)
    _propagate_timestamps(agenda_items)
    _mark_brief_items(agenda_items)
    agenda_items = _insert_recesses(agenda_items)
    video_url = _build_video_url(meeting_id) if bookmarks else None

    if include_votes:
        # Extract recommendations and descriptions from the Agenda page
        recs = _extract_recommendations(html)
        descs = _extract_descriptions(html)
        attachments = _extract_attachments(html)
        _distribute_confirmation_attachments(agenda_items, attachments)
        # Fetch votes and discussion minutes from the PostMinutes page
        post = fetch_post_minutes(meeting_id)
        votes = post["votes"]
        minutes = post["minutes"]

        for item in agenda_items:
            if item.item_id in recs:
                item.recommendation = recs[item.item_id]
            # Prefer minutes text (richer discussion summary) over agenda description
            if item.item_id in minutes:
                item.content = minutes[item.item_id]
            elif item.item_id in descs:
                item.content = descs[item.item_id]
            if item.item_id in attachments:
                item.attachments = attachments[item.item_id]
            if item.item_id in votes:
                v = votes[item.item_id]
                item.vote_result = v["result"]
                item.vote_detail = v["detail"]
                item.is_contested = v["is_contested"]
                # If the actual motion text differs from the agenda
                # recommendation (e.g. a motion to defer), use it instead
                motion = v.get("motion_text", "")
                if motion and motion != item.recommendation:
                    item.recommendation = motion

    return {
        "agenda_items": agenda_items,
        "video_url": video_url,
    }


def _build_video_url(meeting_id: str) -> str:
    return f"{BASE_URL}/Players/ISIStandAlonePlayer.aspx?Id={meeting_id}"


def _parse_escribemeetings_date(date_str: str) -> str:
    """Parse eSCRIBE date format like '/Date(1719457800000)/' to ISO date."""
    match = re.search(r"/Date\((\d+)\)/", date_str)
    if match:
        timestamp_ms = int(match.group(1))
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%Y-%m-%d")
    return date_str


def _extract_bookmarks(html: str) -> dict[int, dict]:
    """Extract video bookmark timestamps from the meeting page JavaScript.

    The page embeds a JS object like:
        Bookmarks : [{"AgendaItemId":1,"TimeStart":275697,"TimeEnd":650293}, ...]
    These are already valid JSON since the keys are quoted.
    """
    match = re.search(r"Bookmarks\s*:\s*(\[.*?\])", html, re.DOTALL)
    if not match:
        return {}

    raw = match.group(1)

    try:
        bookmark_list = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back: keys might not be quoted in some pages
        fixed = re.sub(r"(\w+)\s*:", r'"\1":', raw)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        try:
            bookmark_list = json.loads(fixed)
        except json.JSONDecodeError:
            return {}

    bookmarks: dict[int, dict] = {}
    for b in bookmark_list:
        aid = b.get("AgendaItemId")
        if aid is None:
            continue
        ts = b.get("TimeStart")
        te = b.get("TimeEnd")
        if aid in bookmarks:
            existing = bookmarks[aid]
            if ts is not None and (existing["TimeStart"] is None or ts < existing["TimeStart"]):
                existing["TimeStart"] = ts
            if te is not None and (existing["TimeEnd"] is None or te > existing["TimeEnd"]):
                existing["TimeEnd"] = te
        else:
            bookmarks[aid] = {"TimeStart": ts, "TimeEnd": te}
    return bookmarks


def _extract_agenda_items(html: str, bookmarks: dict) -> list[AgendaItem]:
    """Extract agenda items from the meeting page HTML.

    The eSCRIBE page uses this structure per agenda item::

        <DIV class='AgendaItem AgendaItem{N}'>
          <DIV class='AgendaItemTitleRow'>
            <H2 Id='AgendaItemAgendaItem{N}TitleHeader'>
              <DIV class='AgendaItemCounter'>1.</DIV>
              <DIV class='AgendaItemNavigate indent'>
                <DIV class='AgendaItemTitle'>
                  <a href="javascript:SelectItem({N});">TITLE HERE</a>
                </DIV>
              </DIV>
            </H2>
          </DIV>
        </DIV>

    Note: the counter appears *before* the SelectItem link in the HTML, so
    we match counter first, then the SelectItem/title.
    """
    pattern = re.compile(
        r"AgendaItemCounter.*?>([\d.]+)</DIV"  # number from AgendaItemCounter
        r".*?AgendaItemTitle.*?"
        r"SelectItem\((\d+)\).*?>"             # item ID from javascript:SelectItem(N)
        r"(.*?)</a>",                          # title text inside the <a> tag
        re.IGNORECASE | re.DOTALL,
    )

    items = []
    seen_ids: set[int] = set()
    for match in pattern.finditer(html):
        item_id = int(match.group(2))
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        number = _clean_html(match.group(1)).strip()
        title = _clean_html(match.group(3)).strip()

        bookmark = bookmarks.get(item_id, {})

        item = AgendaItem(
            item_id=item_id,
            title=title,
            content="",
            section_number=number,
            time_start_ms=bookmark.get("TimeStart"),
            time_end_ms=bookmark.get("TimeEnd"),
        )
        items.append(item)

    return items


def _propagate_timestamps(items: list[AgendaItem]) -> None:
    """Inherit timestamps from parent sections for items without bookmarks.

    Consent-agenda sub-items (e.g. 8.2.1, 8.5.3) are approved in a single
    motion under the parent section (e.g. 8.) and only the parent has a video
    bookmark.  This propagates the parent's timestamp to those children so
    they sort correctly in video order.
    """
    # Build a lookup from section_number to timestamp
    ts_by_section: dict[str, tuple[int, int | None]] = {}
    for item in items:
        if item.time_start_ms is not None:
            ts_by_section[item.section_number] = (
                item.time_start_ms,
                item.time_end_ms,
            )

    for item in items:
        if item.time_start_ms is not None:
            continue
        # Walk up the section hierarchy: "8.2.1" -> "8.2" -> "8."
        parts = item.section_number.rstrip(".").split(".")
        for depth in range(len(parts) - 1, 0, -1):
            ancestor = ".".join(parts[:depth]) + "."
            if ancestor in ts_by_section:
                item.time_start_ms = ts_by_section[ancestor][0]
                item.time_end_ms = ts_by_section[ancestor][1]
                item.timestamp_inherited = True
                break


# Items whose video bookmark spans less than this are treated as not-discussed.
# Consent-agenda items often get their own bookmark but the video only lingers
# for a second or two before jumping to the next item.
MIN_DISCUSSION_MS = 60_000  # 60 seconds


def _mark_brief_items(items: list[AgendaItem]) -> None:
    """Flag items whose bookmark duration is too short for real discussion.

    Some agenda items receive their own video bookmark even though they were
    approved as part of a consent block.  The bookmark covers only a trivial
    duration (sometimes ≤ 1 second).  This marks them the same way as
    inherited-timestamp items so the UI shows "Not discussed".
    """
    for item in items:
        if item.timestamp_inherited:
            continue
        if item.time_start_ms is None or item.time_end_ms is None:
            continue
        duration = item.time_end_ms - item.time_start_ms
        if duration <= MIN_DISCUSSION_MS:
            item.timestamp_inherited = True


# Gaps longer than this between consecutive bookmarks are treated as recesses.
MIN_RECESS_MS = 300_000  # 5 minutes


def _insert_recesses(items: list[AgendaItem]) -> list[AgendaItem]:
    """Insert synthetic Recess items into gaps between agenda items.

    Scans items with their own (non-inherited) timestamps in video order,
    and inserts a Recess item wherever there is a gap longer than
    MIN_RECESS_MS between one item's end and the next item's start.
    """
    if not items:
        return []

    # Collect items with their own timestamps, sorted by start time
    timed = sorted(
        [i for i in items if i.time_start_ms is not None and not i.timestamp_inherited],
        key=lambda i: i.time_start_ms,
    )

    # Detect recess gaps
    recesses: list[AgendaItem] = []
    for i in range(1, len(timed)):
        prev_end = timed[i - 1].time_end_ms
        curr_start = timed[i].time_start_ms
        if prev_end is None or curr_start is None:
            continue
        gap = curr_start - prev_end
        if gap >= MIN_RECESS_MS:
            recesses.append(AgendaItem(
                item_id=-1,
                title="Recess",
                content="",
                section_number="",
                time_start_ms=prev_end,
                time_end_ms=curr_start,
                is_recess=True,
            ))

    if not recesses:
        return items

    # Insert recesses into the original item list at the right positions.
    # Each recess goes after the last item whose start time <= recess start.
    result: list[AgendaItem] = []
    recess_iter = iter(sorted(recesses, key=lambda r: r.time_start_ms))
    next_recess = next(recess_iter, None)

    for item in items:
        result.append(item)
        # After appending this item, check if a recess belongs here
        while (
            next_recess is not None
            and item.time_start_ms is not None
            and not item.timestamp_inherited
            and item.time_end_ms is not None
            and item.time_end_ms <= next_recess.time_start_ms
        ):
            # Only insert if the next non-inherited item starts after the recess
            result.append(next_recess)
            next_recess = next(recess_iter, None)

    # Append any remaining recesses (shouldn't happen normally)
    while next_recess is not None:
        result.append(next_recess)
        next_recess = next(recess_iter, None)

    return result


def _clean_html(text: str) -> str:
    """Remove HTML tags from a string, preserving word boundaries."""
    # Block-level closing tags get a space to avoid running words together
    text = re.sub(r"</(?:div|td|tr|th|p|li|br)[^>]*>", " ", text, flags=re.IGNORECASE)
    # All other tags are stripped without adding space
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _item_blocks(html: str) -> dict[int, str]:
    """Split the page HTML into per-item blocks keyed by item ID.

    Uses SelectItem(N) positions as boundaries so that nested items are
    correctly separated (parent content before the first child).
    """
    positions: list[tuple[int, int]] = []
    for m in re.finditer(r"SelectItem\((\d+)\)", html):
        positions.append((int(m.group(1)), m.start()))
    # Deduplicate by item_id, keep first occurrence only
    seen: set[int] = set()
    unique: list[tuple[int, int]] = []
    for item_id, pos in positions:
        if item_id not in seen:
            seen.add(item_id)
            unique.append((item_id, pos))
    unique.sort(key=lambda x: x[1])

    blocks: dict[int, str] = {}
    for i, (item_id, pos) in enumerate(unique):
        end = unique[i + 1][1] if i + 1 < len(unique) else len(html)
        blocks[item_id] = html[pos:end]
    return blocks


def _extract_recommendations(html: str) -> dict[int, str]:
    """Extract recommendation/motion text per agenda item from the Agenda page.

    Returns {item_id: recommendation_text}.
    """
    results: dict[int, str] = {}
    for item_id, block in _item_blocks(html).items():
        mt = re.search(
            r"MotionText RichText.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        if mt:
            text = _clean_html(mt.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                results[item_id] = text
    return results


def _extract_descriptions(html: str) -> dict[int, str]:
    """Extract AgendaItemDescription content per item.

    Returns {item_id: description_text}.
    """
    results: dict[int, str] = {}
    for item_id, block in _item_blocks(html).items():
        dm = re.search(
            r"AgendaItemDescription RichText.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        if dm:
            text = _clean_html(dm.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                results[item_id] = text
    return results


def _extract_minutes(html: str) -> dict[int, str]:
    """Extract AgendaItemMinutes content per item from the PostMinutes page.

    These are discussion summaries written into the official minutes —
    e.g. "Director X presented the report and responded to questions
    related to traffic volumes and funding strategy."

    Returns {item_id: minutes_text}.
    """
    results: dict[int, str] = {}
    for item_id, block in _item_blocks(html).items():
        dm = re.search(
            r"AgendaItemMinutes RichText.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        if dm:
            text = _clean_html(dm.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                results[item_id] = text
    return results


def _extract_attachments(html: str) -> dict[int, list[dict]]:
    """Extract document attachment links per agenda item.

    Returns {item_id: [{"name": str, "url": str}, ...]}.
    """
    results: dict[int, list[dict]] = {}
    for item_id, block in _item_blocks(html).items():
        seen_ids: set[str] = set()
        attachments: list[dict] = []
        for m in re.finditer(
            r'<a\s[^>]*href="(filestream\.ashx\?DocumentId=(\d+))"[^>]*>(.*?)</a>',
            block, re.DOTALL | re.IGNORECASE,
        ):
            doc_id = m.group(2)
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            name = _clean_html(m.group(3)).strip()
            if not name:
                continue
            url = f"{BASE_URL}/{m.group(1)}"
            attachments.append({"name": name, "url": url})
        if attachments:
            results[item_id] = attachments
    return results


def _extract_votes(html: str) -> dict[int, dict]:
    """Extract vote results per agenda item from the PostMinutes page.

    Returns {item_id: {"result": str, "detail": str, "is_contested": bool}}.
    """
    results: dict[int, dict] = {}
    for item_id, block in _item_blocks(html).items():
        rt = re.search(
            r"MotionResult.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        if not rt:
            continue
        result_text = _clean_html(rt.group(1)).strip()
        if not result_text:
            continue

        vt = re.search(
            r"VoterVote.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        detail = _clean_html(vt.group(1)).strip() if vt else ""

        # Extract the actual motion text (may differ from the agenda
        # recommendation, e.g. a motion to defer)
        mt = re.search(
            r"MotionText RichText.*?>(.*?)</DIV>",
            block, re.DOTALL | re.IGNORECASE,
        )
        motion_text = _clean_html(mt.group(1)).strip() if mt else ""

        is_contested = (
            "UNANIMOUSLY" not in result_text.upper()
            and "DEFEATED" not in result_text.upper()
            and re.search(r"\(\d+\s+to\s+\d+\)", result_text) is not None
        ) or "DEFEATED" in result_text.upper()

        results[item_id] = {
            "result": result_text,
            "detail": detail,
            "motion_text": motion_text,
            "is_contested": is_contested,
        }
    return results


def _normalize_name(name: str) -> str:
    """Normalize an attachment name for deduplication.

    Strips the file extension, trailing ``(N)`` copy suffixes, the
    ``_Redacted`` tag, and leading reference codes (e.g. ``CC2026-0101``)
    so that e.g. ``Foo_Redacted.pdf`` and ``CC2026-0101 Foo_Redacted(1).pdf``
    compare as equal.
    """
    # Remove file extension
    n = re.sub(r'\.\w{2,4}$', '', name)
    # Remove trailing (1), (2), etc.
    n = re.sub(r'\(\d+\)\s*$', '', n)
    # Remove _Redacted suffix
    n = re.sub(r'_Redacted\s*$', '', n, flags=re.IGNORECASE)
    # Remove leading reference codes like "CC2026-0101 " or "6.2.3 "
    n = re.sub(r'^[\w]{2,4}\d{4}-\d{3,5}\s+', '', n)
    n = re.sub(r'^\d+(?:\.\d+)*\s+', '', n)
    return n.strip().lower()


def _tokenize_for_match(text: str) -> set[str]:
    """Extract meaningful lowercase words from text for fuzzy matching."""
    # Remove common attachment prefixes
    text = re.sub(
        r'^(Comments|RTS|Request to Speak|Submitting Comments)\s*[-–—]\s*',
        '', text, flags=re.IGNORECASE,
    )
    # Remove person names (word patterns like "J. Smith" or "John Smith")
    # by stripping everything before the second " - " separator
    parts = re.split(r'\s*[-–—]\s*', text, maxsplit=2)
    if len(parts) >= 2:
        # Use the last part which is typically the topic
        text = parts[-1]
    # Remove file extension and _Redacted
    text = re.sub(r'_Redacted(?:\(\d+\))?\.\w{2,4}$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\.\w{2,4}$', '', text)
    words = set(re.findall(r'[a-z]{3,}', text.lower()))
    # Remove very common stopwords
    words -= {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was'}
    return words


def _distribute_confirmation_attachments(
    agenda_items: list["AgendaItem"],
    attachments: dict[int, list[dict]],
) -> None:
    """Move attachments from 'Confirmation of Agenda' items to matching items.

    Public comments and requests-to-speak are filed under the Confirmation
    item on eSCRIBE but actually relate to specific agenda items.  This
    function matches them by keyword overlap and adds them to the correct
    items (skipping duplicates).
    """
    # Find confirmation-of-agenda items
    confirmation_ids: list[int] = []
    for item in agenda_items:
        if "confirmation of agenda" in item.title.lower():
            confirmation_ids.append(item.item_id)

    if not confirmation_ids:
        return

    # Build candidate targets (all non-procedural items with titles)
    candidates: list[tuple["AgendaItem", set[str]]] = []
    for item in agenda_items:
        if item.item_id in confirmation_ids:
            continue
        title_lower = item.title.lower()
        if any(kw in title_lower for kw in (
            "call to order", "adjournment", "roll call",
            "adoption of minutes", "confirmation of",
            "consent agenda", "committee reports (not on consent",
        )):
            continue
        words = set(re.findall(r'[a-z]{3,}', title_lower))
        words -= {'the', 'and', 'for', 'that', 'this', 'with', 'from',
                   'are', 'was', 'report', 'committee', 'standing',
                   'policy', 'city', 'council'}
        if words:
            candidates.append((item, words))

    for conf_id in confirmation_ids:
        conf_attachments = attachments.get(conf_id, [])
        if not conf_attachments:
            continue

        for att in conf_attachments:
            att_words = _tokenize_for_match(att["name"])
            if not att_words:
                continue

            # Score each candidate by word overlap
            best_item = None
            best_score = 0.0
            for item, title_words in candidates:
                overlap = att_words & title_words
                if not overlap:
                    continue
                # Score = overlap relative to attachment words
                score = len(overlap) / len(att_words)
                if score > best_score:
                    best_score = score
                    best_item = item

            # Require at least 30% word overlap to match
            if best_item is None or best_score < 0.3:
                continue

            # Check for duplicates on the target item
            target_id = best_item.item_id
            existing = attachments.get(target_id, [])
            att_norm = _normalize_name(att["name"])
            already_exists = any(
                _normalize_name(e["name"]) == att_norm for e in existing
            )
            if already_exists:
                continue

            # Add the attachment to the target item
            if target_id not in attachments:
                attachments[target_id] = []
            attachments[target_id].append(att)


def fetch_meeting_votes(meeting_id: str) -> dict[int, dict]:
    """Fetch PostMinutes page and extract vote results per item."""
    return fetch_post_minutes(meeting_id)["votes"]


def fetch_post_minutes(meeting_id: str) -> dict:
    """Fetch the PostMinutes page and extract votes + meeting minutes.

    Returns ``{"votes": {item_id: vote_dict}, "minutes": {item_id: str}}``.
    """
    url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=PostMinutes&lang=English"
    try:
        resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
    except Exception:
        return {"votes": {}, "minutes": {}}
    html = resp.text
    return {
        "votes": _extract_votes(html),
        "minutes": _extract_minutes(html),
    }
