"""Scheduled Meetings: transport parsing, deadline, provisional summaries."""

import json

import pytest

from app.escribe import EscribeMeetingSource, FixtureEscribeTransport
from app.agenda_items import is_scheduled_item
from app.item_categorizer import is_eligible_for_summary
from app.models import ItemSummary, ScheduledMeeting
from scripts.summarize_meetings import is_current


def _calendar_payload(meetings):
    return {"d": meetings}


def _entry(**overrides):
    base = {
        "ID": "sched-001",
        "MeetingName": "SPC-TRANSPORTATION - PUBLIC",
        "MeetingType": "SPC-TRANSPORTATION - PUBLIC",
        "StartDate": "2026/08/04 14:00:00",
        "FormattedStart": "Tuesday, 4 August 2026 @ 2:00 PM",
        "Location": "Council Chamber, City Hall",
        "MeetingPassed": False,
        "HasAgenda": True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def source(tmp_path):
    payload = _calendar_payload([
        _entry(),
        # Passed meetings are Meetings, not Scheduled Meetings.
        _entry(ID="sched-past", MeetingPassed=True, StartDate="2026/08/01 09:30:00"),
        # Bodies the app does not cover are dropped.
        _entry(ID="sched-other", MeetingType="BOARD OF REVISION",
               MeetingName="BOARD OF REVISION"),
        # Sort order: soonest first.
        _entry(ID="sched-later", StartDate="2026/08/06 09:30:00"),
    ])
    (tmp_path / "calendar_meetings_2026-07-31_2026-09-29.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return EscribeMeetingSource(FixtureEscribeTransport(tmp_path))


class TestListScheduled:
    def test_filters_and_sorts(self, source):
        scheduled = source.list_scheduled("2026-07-31", "2026-09-29")
        assert [s.meeting_id for s in scheduled] == ["sched-001", "sched-later"]

    def test_maps_body_from_tabs(self, source):
        (s,) = source.list_scheduled("2026-07-31", "2026-09-29")[:1]
        assert s.body == "Transportation"
        assert s.date == "2026-08-04"
        assert s.has_agenda is True


class TestDeadline:
    @pytest.mark.parametrize("meeting_day,expected_monday", [
        ("2026-08-04", "2026-08-03"),  # Tuesday → day before
        ("2026-08-03", "2026-08-03"),  # Monday → same day
        ("2026-08-09", "2026-08-03"),  # Sunday → same week's Monday
    ])
    def test_monday_of_meeting_week(self, meeting_day, expected_monday):
        s = ScheduledMeeting(
            meeting_id="x", title="t", body="b", date=meeting_day,
            start_time="", location="", has_agenda=True,
        )
        assert s.request_to_speak_deadline == expected_monday

    def test_to_dict_marks_scheduled(self):
        s = ScheduledMeeting(
            meeting_id="x", title="t", body="b", date="2026-08-04",
            start_time="", location="", has_agenda=True,
        )
        d = s.to_dict()
        assert d["scheduled"] is True
        assert d["has_video"] is False
        assert d["request_to_speak_deadline"] == "2026-08-03"


class TestScheduledItemEligibility:
    def test_substantive_rec_is_eligible(self):
        item = {"scheduled": True, "title": "BRT Bylaw Updates",
                "recommendation": "That the bylaw be updated"}
        assert is_scheduled_item(item)
        assert is_eligible_for_summary(item)

    def test_boilerplate_rec_is_not_eligible(self):
        item = {"scheduled": True, "title": "Report",
                "recommendation": "That the information be received"}
        assert not is_scheduled_item(item)
        assert not is_eligible_for_summary(item)

    def test_unmarked_item_is_never_scheduled(self):
        item = {"title": "X", "recommendation": "That X happen"}
        assert not is_scheduled_item(item)


class TestProvisionalSummaries:
    def test_round_trip(self):
        s = ItemSummary(description=["One fact."], provisional=True)
        loaded = ItemSummary.from_dict(s.to_dict())
        assert loaded.provisional is True
        assert loaded.description == ["One fact."]

    def test_default_is_not_provisional(self):
        s = ItemSummary(description=["One fact."])
        assert "provisional" not in s.to_dict()
        assert ItemSummary.from_dict(s.to_dict()).provisional is False

    def test_provisional_is_not_current(self):
        # The flip: a meeting covered only provisionally must be
        # regenerated once the transcript exists (ADR 0021).
        cached = {"1": ItemSummary(description=["x"], provisional=True)}
        assert not is_current(cached)

    def test_real_summary_is_current(self):
        cached = {"1": ItemSummary(description=["x"])}
        assert is_current(cached)


def test_body_shorthand_covers_every_tab_label():
    """The Future tab's date squares name their body with a short code
    (<=6 chars, fits the square when written vertically). Every tab
    label needs one; an unknown body falls back to the full label and
    overflows."""
    import re
    index = open("app/templates/index.html", encoding="utf-8").read()
    m = re.search(r"BODY_SHORTHAND = \{(.+?)\};", index, re.S)
    assert m, "BODY_SHORTHAND map missing from index.html"
    keys = set(re.findall(r"'([^']+)':\s*'([^']+)'", m.group(1)))
    codes = {k: v for k, v in keys}
    from app.meeting_types import MEETING_TABS
    for tab in MEETING_TABS:
        assert tab["label"] in codes, f"no shorthand for {tab['label']}"
    for label, code in codes.items():
        assert len(code) <= 6, f"{code} too long for the square's edge"
