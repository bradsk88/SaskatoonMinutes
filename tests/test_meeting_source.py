"""Tests for MeetingSource Protocol implementations."""

from pathlib import Path

import pytest

from app.escribe import EscribeMeetingSource, FixtureEscribeTransport
from app.meeting_source import InMemoryMeetingSource
from app.models import AgendaItem, Meeting, MeetingDetail


FIXTURES = Path(__file__).parent / "fixtures" / "escribe"


# ── InMemoryMeetingSource ────────────────────────────────────────────


class TestInMemoryMeetingSource:
    def test_list_past_returns_supplied_data(self):
        m = Meeting(
            meeting_id="x", title="t", date="2026-01-01", start_time="9:00",
            location="hall", has_video=False, has_agenda=True,
        )
        src = InMemoryMeetingSource(past=[m])
        meetings, total = src.list_past()
        assert meetings == [m]
        assert total == 1

    def test_list_past_empty_default(self):
        src = InMemoryMeetingSource()
        assert src.list_past() == ([], 0)

    def test_list_past_returns_independent_copy(self):
        m = Meeting(
            meeting_id="x", title="t", date="2026-01-01", start_time="9:00",
            location="hall", has_video=False, has_agenda=True,
        )
        src = InMemoryMeetingSource(past=[m])
        meetings, _ = src.list_past()
        meetings.append("junk")
        assert src.list_past() == ([m], 1)

    def test_load_detail_returns_supplied_detail(self):
        item = AgendaItem(item_id=1, title="t", content="", section_number="1.")
        detail = MeetingDetail(agenda_items=[item], video_url="https://v")
        src = InMemoryMeetingSource(details={"abc": detail})
        assert src.load_detail("abc") is detail

    def test_load_detail_unknown_id_raises(self):
        src = InMemoryMeetingSource()
        with pytest.raises(KeyError):
            src.load_detail("missing")


# ── EscribeMeetingSource via FixtureEscribeTransport ─────────────────


class TestEscribeMeetingSourceListPast:
    def test_list_past_parses_fixture_envelope(self):
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        meetings, total = src.list_past(
            page=1,
            meeting_type="CITY COUNCIL AGENDA - REGULAR BUSINESS MEETING",
        )
        assert total == 2
        assert [m.meeting_id for m in meetings] == ["abc-001", "abc-002"]
        # Title trimmed of trailing whitespace.
        assert meetings[0].title == "City Council Meeting"
        assert meetings[0].has_video is True
        assert meetings[0].video_url is not None
        # Cancelled detection from MeetingLinks.
        assert meetings[1].is_cancelled is True
        assert meetings[1].video_url is None


class TestEscribeMeetingSourceLoadDetail:
    def test_load_detail_returns_meeting_detail(self):
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        detail = src.load_detail("abc-001")
        assert isinstance(detail, MeetingDetail)
        assert detail.video_url is not None  # bookmarks were present
        assert len(detail.agenda_items) >= 2

    def test_load_detail_merges_postminutes_votes(self):
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        detail = src.load_detail("abc-001")
        bridge_item = next(i for i in detail.agenda_items if i.item_id == 102)
        # Vote text from postminutes fixture.
        assert "CARRIED UNANIMOUSLY" in bridge_item.vote_result
        # Minutes text preferred over agenda description.
        assert "funding strategy" in bridge_item.content
        # Motion text from postminutes overrides agenda recommendation.
        assert bridge_item.recommendation == "That the report be received as information."

    def test_load_detail_attachments_extracted(self):
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        detail = src.load_detail("abc-001")
        bridge_item = next(i for i in detail.agenda_items if i.item_id == 102)
        assert bridge_item.attachments
        assert bridge_item.attachments[0]["name"] == "Bridge Report.pdf"


class _AgendaOnlyTransport:
    """Transport that succeeds on agenda but raises on postminutes."""

    def __init__(self, fixtures_dir: Path):
        self._real = FixtureEscribeTransport(fixtures_dir)

    def fetch_past_meetings_json(self, page, meeting_type):
        return self._real.fetch_past_meetings_json(page, meeting_type)

    def fetch_agenda_html(self, meeting_id):
        return self._real.fetch_agenda_html(meeting_id)

    def fetch_postminutes_html(self, meeting_id):
        raise RuntimeError("postminutes unavailable")


class TestEscribeMeetingSourcePostminutesFailure:
    def test_postminutes_failure_returns_agenda_with_empty_votes(self):
        src = EscribeMeetingSource(_AgendaOnlyTransport(FIXTURES))
        detail = src.load_detail("abc-001")

        # Agenda items still populated.
        assert len(detail.agenda_items) >= 2
        # No item ended up with a vote_result (postminutes failed).
        assert all(i.vote_result == "" for i in detail.agenda_items)
        # Agenda description used since minutes unavailable.
        bridge_item = next(i for i in detail.agenda_items if i.item_id == 102)
        assert "north bridge" in bridge_item.content


class TestFixtureEscribeTransportMissing:
    def test_missing_fixture_raises(self, tmp_path):
        t = FixtureEscribeTransport(tmp_path)
        with pytest.raises(FileNotFoundError):
            t.fetch_agenda_html("nope")
