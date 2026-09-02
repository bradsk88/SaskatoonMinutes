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

    def test_a_shouted_meeting_type_arrives_readable(self):
        """The list is where card titles come from. eSCRIBE writes the
        type in full caps and the detail page already titleizes it, so a
        card that shouts is the same name twice in two voices."""
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        meetings, _ = src.list_past(
            page=1, meeting_type="MUNICIPAL HERITAGE ADVISORY COMMITTEE",
        )
        assert meetings[0].title == "Municipal Heritage Advisory Committee"


class TestEscribeMeetingSourceListRecorded:
    def test_list_recorded_returns_only_the_gap(self):
        """The gap is a recording that is up but the upstream still marks
        not-passed.  Passed meetings (list_past owns those), meetings with
        no video, and meetings outside the window are all dropped."""
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        recorded = src.list_recorded("2026-09-01", "2026-12-31")
        # cal-002 is passed, cal-003 has no video, cal-004 is out of range.
        assert [m.meeting_id for m in recorded] == ["cal-001"]
        m = recorded[0]
        assert m.has_video is True
        assert m.video_url is not None
        assert m.date == "2026-09-15"

    def test_list_recorded_inmemory(self):
        m = Meeting(
            meeting_id="rec-001", title="t", date="2026-09-15", start_time="9:00",
            location="hall", has_video=True, has_agenda=True,
        )
        src = InMemoryMeetingSource(recorded=[m])
        assert src.list_recorded("2026-09-01", "2026-12-31") == [m]
        # Outside the window is dropped.
        assert src.list_recorded("2027-01-01", "2027-12-31") == []

    def test_list_recorded_filters_by_meeting_type(self):
        """The type scope lands each recorded meeting on its own body's
        past tab; a body with no recorded-but-unpassed meeting gets
        nothing, and a passed body's recording is not the gap."""
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        trans = src.list_recorded(
            "2026-09-01", "2026-12-31", meeting_type="SPC-TRANSPORTATION - PUBLIC",
        )
        assert [m.meeting_id for m in trans] == ["cal-001"]
        # Finance's recording is passed, so it is not the gap here.
        finance = src.list_recorded(
            "2026-09-01", "2026-12-31", meeting_type="SPC-FINANCE - PUBLIC",
        )
        assert finance == []


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


class TestMeetingDetailCarriesItsIdentity:
    """A detail page has to name the meeting it is showing.

    It is reachable from a bookmark or a search result, with no card
    behind it to say what was clicked.  The identity is read from the
    agenda HTML the source already fetches, so it costs no extra request.
    """

    def _detail(self):
        src = EscribeMeetingSource(FixtureEscribeTransport(FIXTURES))
        return src.load_detail("abc-001")

    def test_detail_carries_the_body_that_met(self):
        assert self._detail().title == (
            "Standing Policy Committee on Transportation"
        )

    def test_detail_carries_the_date_and_start_time(self):
        detail = self._detail()
        assert detail.date == "2025-06-17"
        assert detail.start_time == "09:30"

    def test_identity_survives_serialization(self):
        """The page reads these off the JSON, not the dataclass."""
        d = self._detail().to_dict()
        assert d["title"] == "Standing Policy Committee on Transportation"
        assert d["date"] == "2025-06-17"
        assert d["start_time"] == "09:30"

    def test_an_unidentifiable_meeting_has_empty_identity(self):
        """Empty means unknown. It never falls back to naming council."""
        detail = MeetingDetail()
        assert (detail.title, detail.date, detail.start_time) == ("", "", "")
