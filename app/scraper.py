"""
Scraper for Saskatoon City Council meetings from the eSCRIBE platform.

Fetches meeting lists, agenda items, and video timestamp bookmarks from
pub-saskatoon.escribemeetings.com.
"""

import re
import json
import certifi
import requests
from dataclasses import dataclass, field, asdict
from datetime import datetime

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


@dataclass
class AgendaItem:
    item_id: int
    title: str
    content: str
    section_number: str  # e.g. "4.1.2"
    time_start_ms: int | None = None
    time_end_ms: int | None = None

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

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_past_meetings(page: int = 1) -> tuple[list[Meeting], int]:
    """Fetch a page of past City Council meetings from eSCRIBE.

    Returns (meetings, total_count).
    """
    url = f"{BASE_URL}/MeetingsCalendarView.aspx/PastMeetings"
    payload = {
        "type": MEETING_TYPE,
        "pageNumber": page,
    }
    resp = requests.post(url, json=payload, headers=_AJAX_HEADERS, timeout=30, verify=certifi.where())
    resp.raise_for_status()

    data = resp.json().get("d", {})
    total_count = data.get("TotalCount", 0)
    raw_meetings = data.get("Meetings", [])

    meetings = []
    for m in raw_meetings:
        meeting_id = m.get("Id", "")
        has_video = m.get("HasVideo", False)

        meeting = Meeting(
            meeting_id=meeting_id,
            title=m.get("MeetingType", "City Council Meeting").strip(),
            date=_parse_escribemeetings_date(m.get("Start", "")),
            start_time=m.get("FormattedStart", ""),
            location=m.get("LocationName", ""),
            has_video=has_video,
            has_agenda=m.get("HasAgenda", False),
            video_url=_build_video_url(meeting_id) if has_video else None,
        )
        meetings.append(meeting)

    return meetings, total_count


def fetch_meeting_detail(meeting_id: str) -> dict:
    """Fetch the full meeting page and extract agenda items + video bookmarks.

    Returns a dict with 'agenda_items' and 'video_url'.
    """
    url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=Agenda&lang=English"
    resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=certifi.where())
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

    return {
        b["AgendaItemId"]: {"TimeStart": b.get("TimeStart"), "TimeEnd": b.get("TimeEnd")}
        for b in bookmark_list
    }


def _extract_agenda_items(html: str, bookmarks: dict) -> list[AgendaItem]:
    """Extract agenda items from the meeting page HTML.

    The eSCRIBE page uses this structure per agenda item:
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
    """
    # Match each AgendaItemTitleRow and extract the item ID, number, and title.
    pattern = re.compile(
        r"<DIV\s+class='AgendaItemTitleRow'\s*>"
        r".*?SelectItem\((\d+)\).*?"         # item ID from javascript:SelectItem(N)
        r"AgendaItemCounter.*?>([\d.]+)</DIV" # number from AgendaItemCounter
        r".*?AgendaItemTitle.*?>(.*?)</a>",   # title text inside the <a> tag
        re.IGNORECASE | re.DOTALL,
    )

    items = []
    seen_ids = set()
    for match in pattern.finditer(html):
        item_id = int(match.group(1))
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        number = _clean_html(match.group(2)).strip()
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


def _clean_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()
