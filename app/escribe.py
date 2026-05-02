"""eSCRIBE transport + parsers + meeting source.

Two seams in one module:

* ``EscribeTransport`` — bytes-level. Always raises on HTTP/IO failure.
  Live and Fixture adapters.
* ``EscribeMeetingSource`` — domain-level. Owns parsing and the
  "votes are best-effort" policy (silent swallow of PostMinutes failures
  lives here, not in the transport).

Pure parser helpers (``_extract_*``, ``_propagate_timestamps``, etc.) live
here too because they are eSCRIBE-specific.  They stay private but are
imported directly by tests.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

import urllib3
import requests

from app.models import AgendaItem, Meeting, MeetingDetail

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://pub-saskatoon.escribemeetings.com"

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

# Items whose video bookmark spans less than this are treated as not-discussed.
MIN_DISCUSSION_MS = 60_000  # 60 seconds

# Gaps longer than this between consecutive bookmarks are treated as recesses.
MIN_RECESS_MS = 300_000  # 5 minutes


def _build_video_url(meeting_id: str) -> str:
    return f"{BASE_URL}/Players/ISIStandAlonePlayer.aspx?Id={meeting_id}"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class EscribeTransport(Protocol):
    """Bytes-level seam over the eSCRIBE site.

    Implementations always raise on HTTP / IO failure.  Callers decide
    whether to surface or swallow.
    """

    def fetch_past_meetings_json(self, page: int, meeting_type: str) -> dict:
        """POST PastMeetings; return the raw envelope (caller unwraps ``"d"``)."""

    def fetch_agenda_html(self, meeting_id: str) -> str:
        """GET the Agenda view of the meeting page; return HTML."""

    def fetch_postminutes_html(self, meeting_id: str) -> str:
        """GET the PostMinutes view of the meeting page; return HTML."""


class LiveEscribeTransport:
    """Real-network transport against pub-saskatoon.escribemeetings.com."""

    def fetch_past_meetings_json(self, page: int, meeting_type: str) -> dict:
        url = f"{BASE_URL}/MeetingsCalendarView.aspx/PastMeetings"
        payload = {"type": meeting_type, "pageNumber": page}
        resp = requests.post(url, json=payload, headers=_AJAX_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        return resp.json()

    def fetch_agenda_html(self, meeting_id: str) -> str:
        url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=Agenda&lang=English"
        resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        return resp.text

    def fetch_postminutes_html(self, meeting_id: str) -> str:
        url = f"{BASE_URL}/Meeting.aspx?Id={meeting_id}&Agenda=PostMinutes&lang=English"
        resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        return resp.text


class FixtureEscribeTransport:
    """Disk-backed transport that replays recorded fixtures.

    Files are looked up in ``fixtures_dir`` by convention:

    * ``past_meetings_{slug}_{page}.json`` — the JSON envelope for a
      past-meetings call. The slug is derived by lowercasing the meeting
      type and replacing non-alphanumerics with underscores.
    * ``agenda_{meeting_id}.html``
    * ``postminutes_{meeting_id}.html``

    Missing files raise ``FileNotFoundError`` — the transport contract is
    "always raises on failure", so the source layer is responsible for
    swallowing where appropriate.
    """

    def __init__(self, fixtures_dir: Path | str):
        self._dir = Path(fixtures_dir)

    @staticmethod
    def _slugify(meeting_type: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", meeting_type.lower()).strip("_")

    def fetch_past_meetings_json(self, page: int, meeting_type: str) -> dict:
        path = self._dir / f"past_meetings_{self._slugify(meeting_type)}_{page}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def fetch_agenda_html(self, meeting_id: str) -> str:
        return (self._dir / f"agenda_{meeting_id}.html").read_text(encoding="utf-8")

    def fetch_postminutes_html(self, meeting_id: str) -> str:
        return (self._dir / f"postminutes_{meeting_id}.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------

def _parse_escribemeetings_date(date_str: str) -> str:
    """Parse eSCRIBE date format like '/Date(1719457800000)/' to ISO date."""
    match = re.search(r"/Date\((\d+)\)/", date_str)
    if match:
        timestamp_ms = int(match.group(1))
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%Y-%m-%d")
    return date_str


def _extract_bookmarks(html: str) -> dict[int, dict]:
    """Extract video bookmark timestamps from the meeting page JavaScript."""
    match = re.search(r"Bookmarks\s*:\s*(\[.*?\])", html, re.DOTALL)
    if not match:
        return {}

    raw = match.group(1)

    try:
        bookmark_list = json.loads(raw)
    except json.JSONDecodeError:
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
    """Extract agenda items from the meeting page HTML."""
    pattern = re.compile(
        r"AgendaItemCounter.*?>([\d.]+)</DIV"
        r".*?AgendaItemTitle.*?"
        r"SelectItem\((\d+)\).*?>"
        r"(.*?)</a>",
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
    """Inherit timestamps from parent sections for items without bookmarks."""
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
        parts = item.section_number.rstrip(".").split(".")
        for depth in range(len(parts) - 1, 0, -1):
            ancestor = ".".join(parts[:depth]) + "."
            if ancestor in ts_by_section:
                item.time_start_ms = ts_by_section[ancestor][0]
                item.time_end_ms = ts_by_section[ancestor][1]
                item.timestamp_inherited = True
                break


def _mark_brief_items(items: list[AgendaItem]) -> None:
    """Flag items whose bookmark duration is too short for real discussion."""
    for item in items:
        if item.timestamp_inherited:
            continue
        if item.time_start_ms is None or item.time_end_ms is None:
            continue
        duration = item.time_end_ms - item.time_start_ms
        if duration <= MIN_DISCUSSION_MS:
            item.timestamp_inherited = True


def _insert_recesses(items: list[AgendaItem]) -> list[AgendaItem]:
    """Insert synthetic Recess items into gaps between agenda items."""
    if not items:
        return []

    timed = sorted(
        [i for i in items if i.time_start_ms is not None and not i.timestamp_inherited],
        key=lambda i: i.time_start_ms,
    )

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

    result: list[AgendaItem] = []
    recess_iter = iter(sorted(recesses, key=lambda r: r.time_start_ms))
    next_recess = next(recess_iter, None)

    for item in items:
        result.append(item)
        while (
            next_recess is not None
            and item.time_start_ms is not None
            and not item.timestamp_inherited
            and item.time_end_ms is not None
            and item.time_end_ms <= next_recess.time_start_ms
        ):
            result.append(next_recess)
            next_recess = next(recess_iter, None)

    while next_recess is not None:
        result.append(next_recess)
        next_recess = next(recess_iter, None)

    return result


def _clean_html(text: str) -> str:
    """Remove HTML tags from a string, preserving word boundaries."""
    text = re.sub(r"</(?:div|td|tr|th|p|li|br)[^>]*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _item_blocks(html: str) -> dict[int, str]:
    """Split the page HTML into per-item blocks keyed by item ID."""
    positions: list[tuple[int, int]] = []
    for m in re.finditer(r"SelectItem\((\d+)\)", html):
        positions.append((int(m.group(1)), m.start()))
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
    """Extract recommendation/motion text per agenda item from the Agenda page."""
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
    """Extract AgendaItemDescription content per item."""
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
    """Extract AgendaItemMinutes content per item from the PostMinutes page."""
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
    """Extract document attachment links per agenda item."""
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
    """Extract vote results per agenda item from the PostMinutes page."""
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
    """Normalize an attachment name for deduplication."""
    n = re.sub(r'\.\w{2,4}$', '', name)
    n = re.sub(r'\(\d+\)\s*$', '', n)
    n = re.sub(r'_Redacted\s*$', '', n, flags=re.IGNORECASE)
    n = re.sub(r'^[\w]{2,4}\d{4}-\d{3,5}\s+', '', n)
    n = re.sub(r'^\d+(?:\.\d+)*\s+', '', n)
    return n.strip().lower()


def _tokenize_for_match(text: str) -> set[str]:
    """Extract meaningful lowercase words from text for fuzzy matching."""
    text = re.sub(
        r'^(Comments|RTS|Request to Speak|Submitting Comments)\s*[-–—]\s*',
        '', text, flags=re.IGNORECASE,
    )
    parts = re.split(r'\s*[-–—]\s*', text, maxsplit=2)
    if len(parts) >= 2:
        text = parts[-1]
    text = re.sub(r'_Redacted(?:\(\d+\))?\.\w{2,4}$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\.\w{2,4}$', '', text)
    words = set(re.findall(r'[a-z]{3,}', text.lower()))
    words -= {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was'}
    return words


def _distribute_confirmation_attachments(
    agenda_items: list[AgendaItem],
    attachments: dict[int, list[dict]],
) -> None:
    """Move attachments from 'Confirmation of Agenda' items to matching items."""
    confirmation_ids: list[int] = []
    for item in agenda_items:
        if "confirmation of agenda" in item.title.lower():
            confirmation_ids.append(item.item_id)

    if not confirmation_ids:
        return

    candidates: list[tuple[AgendaItem, set[str]]] = []
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

            best_item = None
            best_score = 0.0
            for item, title_words in candidates:
                overlap = att_words & title_words
                if not overlap:
                    continue
                score = len(overlap) / len(att_words)
                if score > best_score:
                    best_score = score
                    best_item = item

            if best_item is None or best_score < 0.3:
                continue

            target_id = best_item.item_id
            existing = attachments.get(target_id, [])
            att_norm = _normalize_name(att["name"])
            already_exists = any(
                _normalize_name(e["name"]) == att_norm for e in existing
            )
            if already_exists:
                continue

            if target_id not in attachments:
                attachments[target_id] = []
            attachments[target_id].append(att)


# ---------------------------------------------------------------------------
# MeetingSource implementation
# ---------------------------------------------------------------------------

class EscribeMeetingSource:
    """Domain-shaped source backed by an ``EscribeTransport``.

    Owns parsing and the "votes are best-effort" policy: a PostMinutes
    fetch failure yields a ``MeetingDetail`` with empty vote/minutes data
    but a populated agenda.  Other transport failures propagate.
    """

    def __init__(self, transport: EscribeTransport):
        self._transport = transport

    def list_past(self, page: int = 1, meeting_type: str | None = None) -> tuple[list[Meeting], int]:
        from app.meeting_types import MEETING_TYPE  # local import to avoid cycle
        envelope = self._transport.fetch_past_meetings_json(
            page=page, meeting_type=meeting_type or MEETING_TYPE,
        )
        data = envelope.get("d", {})
        total_count = data.get("TotalCount", 0)
        raw_meetings = data.get("Meetings", [])

        meetings: list[Meeting] = []
        for m in raw_meetings:
            meeting_id = m.get("Id", "")
            has_video = m.get("HasVideo", False)
            is_cancelled = m.get("Cancelled", False) or any(
                "cancel" in link.get("Title", "").lower()
                for link in m.get("MeetingLinks", [])
            )
            meetings.append(Meeting(
                meeting_id=meeting_id,
                title=m.get("MeetingType", "City Council Meeting").strip(),
                date=_parse_escribemeetings_date(m.get("Start", "")),
                start_time=m.get("FormattedStart", ""),
                location=m.get("LocationName", ""),
                has_video=has_video,
                has_agenda=m.get("HasAgenda", False),
                video_url=_build_video_url(meeting_id) if has_video else None,
                is_cancelled=is_cancelled,
            ))

        return meetings, total_count

    def load_detail(self, meeting_id: str) -> MeetingDetail:
        html = self._transport.fetch_agenda_html(meeting_id)

        bookmarks = _extract_bookmarks(html)
        agenda_items = _extract_agenda_items(html, bookmarks)
        _propagate_timestamps(agenda_items)
        _mark_brief_items(agenda_items)
        agenda_items = _insert_recesses(agenda_items)
        video_url = _build_video_url(meeting_id) if bookmarks else None

        recs = _extract_recommendations(html)
        descs = _extract_descriptions(html)
        attachments = _extract_attachments(html)
        _distribute_confirmation_attachments(agenda_items, attachments)

        # Votes are best-effort: if PostMinutes fails (page missing, slow,
        # malformed), we still return the agenda.
        try:
            post_html = self._transport.fetch_postminutes_html(meeting_id)
            votes = _extract_votes(post_html)
            minutes = _extract_minutes(post_html)
        except Exception:
            votes, minutes = {}, {}

        for item in agenda_items:
            if item.item_id in recs:
                item.recommendation = recs[item.item_id]
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
                motion = v.get("motion_text", "")
                if motion and motion != item.recommendation:
                    item.recommendation = motion

        return MeetingDetail(agenda_items=agenda_items, video_url=video_url)
