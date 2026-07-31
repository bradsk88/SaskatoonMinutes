"""Domain-level seam for fetching meetings.

``MeetingSource`` is the typed boundary the Flask app and scripts depend
on.  Production wires :class:`app.escribe.EscribeMeetingSource`; tests
wire :class:`InMemoryMeetingSource`.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from app.models import Meeting, MeetingDetail, ScheduledMeeting


class MeetingSource(Protocol):
    """Domain seam over upstream meeting data.

    Implementations decide their own failure policy. Callers receive
    fully-parsed domain types — no HTML, no envelope shapes.
    """

    def list_past(self, page: int = 1, meeting_type: str | None = None) -> tuple[list[Meeting], int]:
        """Return ``(meetings, total_count)`` for the given page+filter."""

    def load_detail(self, meeting_id: str) -> MeetingDetail:
        """Return the full ``MeetingDetail`` for ``meeting_id``."""

    def list_scheduled(self, start_date: str, end_date: str) -> list[ScheduledMeeting]:
        """Scheduled Meetings in the date range, soonest first."""


class InMemoryMeetingSource:
    """Test double backed by passive in-memory data.

    Construction is the test fixture: tests build the dicts directly,
    matching :class:`app.cache.InMemoryCache`'s passive style.
    """

    def __init__(
        self,
        details: dict[str, MeetingDetail] | None = None,
        past: Sequence[Meeting] = (),
        scheduled: Sequence[ScheduledMeeting] = (),
    ):
        self.details: dict[str, MeetingDetail] = dict(details or {})
        self.past: list[Meeting] = list(past)
        self.scheduled: list[ScheduledMeeting] = list(scheduled)

    def list_past(self, page: int = 1, meeting_type: str | None = None) -> tuple[list[Meeting], int]:
        return list(self.past), len(self.past)

    def load_detail(self, meeting_id: str) -> MeetingDetail:
        return self.details[meeting_id]

    def list_scheduled(self, start_date: str, end_date: str) -> list[ScheduledMeeting]:
        return [
            s for s in self.scheduled
            if start_date <= s.date <= end_date
        ]
