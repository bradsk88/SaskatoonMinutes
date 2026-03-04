"""
Scraper for Saskatoon City Council meetings from the eSCRIBE platform.

Fetches meeting lists, agenda items, and video timestamp bookmarks from
pub-saskatoon.escribemeetings.com.
"""

import re
import json
import urllib3
import requests
from dataclasses import dataclass, asdict
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
    resp = requests.post(url, json=payload, headers=_AJAX_HEADERS, timeout=30, verify=False)
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
    video_url = _build_video_url(meeting_id) if bookmarks else None

    if include_votes:
        # Extract recommendations and descriptions from the Agenda page
        recs = _extract_recommendations(html)
        descs = _extract_descriptions(html)
        # Fetch vote results from the PostMinutes page
        votes = fetch_meeting_votes(meeting_id)

        for item in agenda_items:
            if item.item_id in recs:
                item.recommendation = recs[item.item_id]
            if item.item_id in descs:
                item.content = descs[item.item_id]
            if item.item_id in votes:
                v = votes[item.item_id]
                item.vote_result = v["result"]
                item.vote_detail = v["detail"]
                item.is_contested = v["is_contested"]

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
                break


def _clean_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


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

        is_contested = (
            "UNANIMOUSLY" not in result_text.upper()
            and "DEFEATED" not in result_text.upper()
            and re.search(r"\(\d+\s+to\s+\d+\)", result_text) is not None
        ) or "DEFEATED" in result_text.upper()

        results[item_id] = {
            "result": result_text,
            "detail": detail,
            "is_contested": is_contested,
        }
    return results


def fetch_meeting_votes(meeting_id: str) -> dict[int, dict]:
    """Fetch PostMinutes page and extract vote results per item."""
    url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=PostMinutes&lang=English"
    try:
        resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
    except Exception:
        return {}
    return _extract_votes(resp.text)
