"""eSCRIBE transport seam.

The transport returns raw bytes/JSON.  Higher layers (``EscribeMeetingSource``)
do the parsing and any policy decisions about partial failures.

All transports raise on HTTP failure — silent swallow lives one layer up.
"""

from __future__ import annotations

from typing import Protocol

import urllib3
import requests

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


def _build_video_url(meeting_id: str) -> str:
    return f"{BASE_URL}/Players/ISIStandAlonePlayer.aspx?Id={meeting_id}"


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
