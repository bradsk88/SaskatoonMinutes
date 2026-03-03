"""
Scraper for Saskatoon City Council meetings from the eSCRIBE platform.

Fetches meeting lists, agenda items, and video timestamp bookmarks from
pub-saskatoon.escribemeetings.com.
"""

import re
import json
import requests
from dataclasses import dataclass, field, asdict
from datetime import datetime

BASE_URL = "https://pub-saskatoon.escribemeetings.com"

# eSCRIBE meeting type IDs for City Council meetings.
# These were extracted from the eSCRIBE portal's filter dropdown.
CITY_COUNCIL_TYPE_IDS = [
    "9cd3a6ca-d6b5-4062-97e3-0e498b386857",  # City Council - Regular Business Meeting
]


@dataclass
class AgendaItem:
    item_id: int
    title: str
    content: str  # text content of the agenda item
    section_number: str  # e.g. "4.1.2"
    time_start_ms: int | None = None  # video bookmark start (milliseconds)
    time_end_ms: int | None = None  # video bookmark end (milliseconds)
    attachments: list[dict] = field(default_factory=list)
    children: list["AgendaItem"] = field(default_factory=list)

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
    agenda_items: list[AgendaItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["agenda_items"] = [item.to_dict() for item in self.agenda_items]
        return d


def fetch_past_meetings(page: int = 1) -> tuple[list[Meeting], int]:
    """Fetch a page of past City Council meetings from eSCRIBE.

    Returns (meetings, total_count).
    """
    url = f"{BASE_URL}/MeetingsCalendarView.aspx/PastMeetings"
    payload = {
        "type": CITY_COUNCIL_TYPE_IDS[0],
        "pageNumber": page,
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    data = resp.json().get("d", {})
    total_count = data.get("TotalCount", 0)
    raw_meetings = data.get("Meetings", [])

    meetings = []
    for m in raw_meetings:
        meeting = Meeting(
            meeting_id=m.get("ID", ""),
            title=m.get("MeetingName", "").strip(),
            date=_parse_escribemeetings_date(m.get("StartDate", "")),
            start_time=m.get("FormattedStart", ""),
            location=m.get("Location", ""),
            has_video=m.get("HasVideo", False),
            has_agenda=m.get("HasAgenda", False),
            video_url=_build_video_url(m.get("ID", "")) if m.get("HasVideo") else None,
        )
        meetings.append(meeting)

    return meetings, total_count


def fetch_meeting_detail(meeting_id: str) -> dict:
    """Fetch the full meeting page and extract agenda items + video bookmarks.

    Returns a dict with 'agenda_items' and 'video_bookmarks'.
    """
    url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=Agenda&lang=English"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    bookmarks = _extract_bookmarks(html)
    agenda_items = _extract_agenda_items(html, bookmarks)
    video_url = _build_video_url(meeting_id) if bookmarks else None

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

    Returns a dict mapping AgendaItemId -> {TimeStart, TimeEnd}.
    """
    # The page embeds: Bookmarks: [{AgendaItemId:1,TimeStart:189810,TimeEnd:342578}, ...]
    match = re.search(r"Bookmarks:\s*(\[.*?\])", html, re.DOTALL)
    if not match:
        return {}

    raw = match.group(1)
    # The JS object keys aren't quoted, so we need to fix that for JSON parsing
    fixed = re.sub(r"(\w+)\s*:", r'"\1":', raw)
    # Remove trailing commas before ] or }
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

    try:
        bookmark_list = json.loads(fixed)
    except json.JSONDecodeError:
        return {}

    return {
        b["AgendaItemId"]: {"TimeStart": b.get("TimeStart"), "TimeEnd": b.get("TimeEnd")}
        for b in bookmark_list
    }


def _extract_agenda_items(html: str, bookmarks: dict) -> list[AgendaItem]:
    """Extract agenda items from the meeting page HTML.

    Uses regex to parse the structured agenda from the eSCRIBE page.
    """
    # Pattern to match agenda item containers.
    # eSCRIBE uses elements like: <div class="AgendaItemContainer" id="AgendaItem_123">
    item_pattern = re.compile(
        r'<div[^>]*class="[^"]*AgendaItemContainer[^"]*"[^>]*id="AgendaItem[_-]?(\d+)"[^>]*>',
        re.IGNORECASE,
    )

    # Pattern for agenda item title/number
    title_pattern = re.compile(
        r'<span[^>]*class="[^"]*AgendaItemTitle[^"]*"[^>]*>(.*?)</span>',
        re.IGNORECASE | re.DOTALL,
    )

    # Simpler approach: extract all agenda item data from the page's JavaScript
    # The page also includes agenda item data in script blocks
    items = []

    # Try extracting from the HTML structure
    # Look for agenda item sections with their content
    section_pattern = re.compile(
        r'<tr[^>]*class="[^"]*AgendaItem[^"]*"[^>]*data-agendaitemid="(\d+)"[^>]*>.*?'
        r'<span[^>]*class="[^"]*Number[^"]*"[^>]*>(.*?)</span>.*?'
        r'<span[^>]*class="[^"]*Title[^"]*"[^>]*>(.*?)</span>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in section_pattern.finditer(html):
        item_id = int(match.group(1))
        number = _clean_html(match.group(2)).strip()
        title = _clean_html(match.group(3)).strip()

        bookmark = bookmarks.get(item_id, {})

        item = AgendaItem(
            item_id=item_id,
            title=title,
            content="",  # Will be populated if we fetch item details
            section_number=number,
            time_start_ms=bookmark.get("TimeStart"),
            time_end_ms=bookmark.get("TimeEnd"),
        )
        items.append(item)

    # If the table-row approach didn't work, try the div-based approach
    if not items:
        items = _extract_agenda_items_div(html, bookmarks)

    return items


def _extract_agenda_items_div(html: str, bookmarks: dict) -> list[AgendaItem]:
    """Fallback extraction using div-based structure."""
    # Look for patterns like: <div ... data-agendaitemid="123"> or id="AgendaItemNum_123"
    pattern = re.compile(
        r'(?:data-agendaitemid|id\s*=\s*"AgendaItemNum)[_"](\d+)',
        re.IGNORECASE,
    )

    seen_ids = set()
    items = []

    for match in pattern.finditer(html):
        item_id = int(match.group(1))
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        # Try to find the title near this element
        context_start = max(0, match.start() - 200)
        context_end = min(len(html), match.end() + 2000)
        context = html[context_start:context_end]

        title = _extract_title_from_context(context)
        number = _extract_number_from_context(context)

        bookmark = bookmarks.get(item_id, {})

        item = AgendaItem(
            item_id=item_id,
            title=title or f"Item {item_id}",
            content="",
            section_number=number or "",
            time_start_ms=bookmark.get("TimeStart"),
            time_end_ms=bookmark.get("TimeEnd"),
        )
        items.append(item)

    return items


def _extract_title_from_context(html_context: str) -> str | None:
    """Try to extract agenda item title from surrounding HTML context."""
    # Multiple patterns to try
    patterns = [
        re.compile(r'class="[^"]*Title[^"]*"[^>]*>(.*?)</(?:span|div|td)', re.DOTALL | re.IGNORECASE),
        re.compile(r'class="[^"]*ItemTitle[^"]*"[^>]*>(.*?)</(?:span|div|td)', re.DOTALL | re.IGNORECASE),
        re.compile(r'<h\d[^>]*>(.*?)</h\d>', re.DOTALL | re.IGNORECASE),
    ]
    for p in patterns:
        m = p.search(html_context)
        if m:
            return _clean_html(m.group(1)).strip()
    return None


def _extract_number_from_context(html_context: str) -> str | None:
    """Try to extract agenda item number from surrounding HTML context."""
    patterns = [
        re.compile(r'class="[^"]*Number[^"]*"[^>]*>(.*?)</(?:span|div|td)', re.DOTALL | re.IGNORECASE),
        re.compile(r'class="[^"]*ItemNumber[^"]*"[^>]*>(.*?)</(?:span|div|td)', re.DOTALL | re.IGNORECASE),
    ]
    for p in patterns:
        m = p.search(html_context)
        if m:
            return _clean_html(m.group(1)).strip()
    return None


def _clean_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()
