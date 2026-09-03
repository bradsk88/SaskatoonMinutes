"""Tests for app.models — Transcript, Segment, ItemSummary, Meeting, AgendaItem."""

from app.models import (
    AgendaItem,
    Chip,
    ItemSummary,
    Meeting,
    RECORDING_GRACE_HOURS,
    SASKATOON_TZ,
    Segment,
    Transcript,
    has_current_summaries,
    meeting_recording_state,
    meeting_start,
)

from datetime import datetime, timedelta


def _seg(start_ms: int, end_ms: int, text: str = "x") -> Segment:
    return Segment(start_ms=start_ms, end_ms=end_ms, text=text)


class TestTranscriptRoundTrip:
    def test_round_trip(self):
        raw = [
            {"start_ms": 0, "end_ms": 1000, "text": "Hello"},
            {"start_ms": 1000, "end_ms": 2500, "text": "world"},
        ]
        t = Transcript.from_dict(raw)
        assert t.to_dict() == raw
        assert Transcript.from_dict(t.to_dict()) == t

    def test_empty_round_trip(self):
        assert Transcript.from_dict([]).to_dict() == []
        assert Transcript().text == ""


class TestTranscriptSlice:
    def test_slice_returns_overlapping_segments(self):
        t = Transcript(segments=[
            _seg(0, 30_000, "a"),
            _seg(60_000, 90_000, "b"),
            _seg(110_000, 130_000, "c"),
            _seg(200_000, 210_000, "d"),
        ])
        result = t.slice(60.0, 120.0)
        texts = [s.text for s in result.segments]
        assert texts == ["b", "c"]

    def test_slice_empty_range(self):
        t = Transcript(segments=[_seg(0, 1000, "a")])
        result = t.slice(60.0, 120.0)
        assert result.segments == []
        assert isinstance(result, Transcript)

    def test_slice_segment_straddling_start_boundary_included(self):
        t = Transcript(segments=[_seg(50_000, 70_000, "boundary")])
        result = t.slice(60.0, 120.0)
        assert [s.text for s in result.segments] == ["boundary"]

    def test_slice_segment_straddling_end_boundary_included(self):
        t = Transcript(segments=[_seg(110_000, 130_000, "boundary")])
        result = t.slice(60.0, 120.0)
        assert [s.text for s in result.segments] == ["boundary"]

    def test_slice_empty_transcript(self):
        assert Transcript().slice(0, 100).segments == []

    def test_slice_ms_matches_slice(self):
        t = Transcript(segments=[
            _seg(0, 30_000, "a"),
            _seg(60_000, 90_000, "b"),
        ])
        assert t.slice_ms(60_000, 120_000) == t.slice(60.0, 120.0)


class TestTranscriptText:
    def test_text_joins_with_spaces(self):
        t = Transcript(segments=[
            _seg(0, 1000, "hello"),
            _seg(1000, 2000, "world"),
        ])
        assert t.text == "hello world"

    def test_empty_text(self):
        assert Transcript().text == ""


class TestChip:
    def test_round_trip(self):
        raw = {"category": "Outcome", "text": "Approved (8-3)"}
        c = Chip.from_dict(raw)
        assert c.to_dict() == raw
        assert Chip.from_dict(c.to_dict()) == c

    def test_extra_fields_dropped(self):
        # Forward-compat: the model's usefulness rating is not persisted.
        raw = {"category": "Outcome", "text": "ok", "usefulness": "high"}
        assert Chip.from_dict(raw).to_dict() == {"category": "Outcome", "text": "ok"}


class TestItemSummary:
    def test_round_trip(self):
        raw = {
            "description": ["Raises transit fines to $250."],
            "chips": [{"category": "Outcome", "text": "Approved (8-3)"}],
        }
        s = ItemSummary.from_dict(raw)
        assert s.to_dict() == raw
        assert ItemSummary.from_dict(s.to_dict()) == s

    def test_description_is_required_to_not_be_legacy(self):
        s = ItemSummary.from_dict({
            "description": ["Does a concrete thing."], "chips": [],
        })
        assert s.is_legacy is False

    def test_blank_description_normalizes_to_none(self):
        s = ItemSummary.from_dict({"description": "   ", "chips": []})
        assert s.description is None
        assert s.is_legacy is True

    def test_missing_description_key_is_legacy(self):
        s = ItemSummary.from_dict({"chips": []})
        assert s.is_legacy is True

    def test_missing_chips_key_is_an_empty_list(self):
        assert ItemSummary.from_dict({"description": "x"}).chips == []


class TestDescriptionBullets:
    """The Description is a list of bullets, one per distinct fact."""

    def test_bullets_are_kept_in_order(self):
        s = ItemSummary.from_dict({
            "description": ["Rezones 902-938 3rd Avenue North", "Allows 83 units"],
            "chips": [],
        })
        assert s.description == [
            "Rezones 902-938 3rd Avenue North", "Allows 83 units",
        ]

    def test_a_stored_paragraph_loads_as_one_bullet(self):
        """The archive holds thousands of string descriptions and they are
        not all regenerated at once. A string is one bullet, not Legacy."""
        s = ItemSummary.from_dict({
            "description": "Raises transit fines to $250.", "chips": [],
        })
        assert s.description == ["Raises transit fines to $250."]
        assert s.is_legacy is False

    def test_blank_bullets_are_dropped(self):
        s = ItemSummary.from_dict({"description": ["Real fact", "  ", ""]})
        assert s.description == ["Real fact"]

    def test_an_empty_bullet_list_is_legacy(self):
        s = ItemSummary.from_dict({"description": [], "chips": []})
        assert s.description is None
        assert s.is_legacy is True

    def test_a_list_of_only_blanks_is_legacy(self):
        s = ItemSummary.from_dict({"description": ["   "], "chips": []})
        assert s.is_legacy is True


class TestLegacyItemSummary:
    """Entries cached before the aggregate load without a migration."""

    def test_a_bare_chip_list_loads_as_legacy(self):
        raw = [
            {"category": "Outcome", "text": "Approved"},
            {"category": "In Plain Terms", "text": "Subcommittee report"},
        ]
        s = ItemSummary.from_dict(raw)
        assert s.is_legacy is True
        assert s.description is None
        assert [c.category for c in s.chips] == ["Outcome", "In Plain Terms"]

    def test_a_retired_category_still_loads(self):
        """In Plain Terms is retired, but 2,567 cached chips still use it."""
        s = ItemSummary.from_dict([{"category": "In Plain Terms", "text": "x"}])
        assert s.chips[0].category == "In Plain Terms"

    def test_an_empty_legacy_list_loads(self):
        s = ItemSummary.from_dict([])
        assert s.is_legacy is True
        assert s.chips == []

    def test_legacy_round_trips_into_the_new_shape(self):
        s = ItemSummary.from_dict([{"category": "Outcome", "text": "Approved"}])
        assert s.to_dict() == {
            "description": None,
            "chips": [{"category": "Outcome", "text": "Approved"}],
        }


class TestAgendaItem:
    def test_to_dict_includes_derived_fields(self):
        item = AgendaItem(
            item_id=42,
            title="Funding request",
            content="body",
            section_number="4.1.2",
            time_start_ms=3_661_000,
        )
        d = item.to_dict()
        assert d["item_id"] == 42
        assert d["section_number"] == "4.1.2"
        assert d["time_start_formatted"] == "1:01:01"
        assert d["is_contested"] is False
        assert d["timestamp_inherited"] is False
        assert d["is_recess"] is False
        assert d["attachments"] == []

    def test_time_start_formatted_under_one_hour(self):
        item = AgendaItem(item_id=1, title="t", content="c", section_number="1", time_start_ms=125_000)
        assert item.time_start_formatted == "2:05"

    def test_time_start_formatted_none(self):
        item = AgendaItem(item_id=1, title="t", content="c", section_number="1")
        assert item.time_start_formatted is None
        assert item.to_dict()["time_start_formatted"] is None

    def test_attachments_default_is_independent(self):
        a = AgendaItem(item_id=1, title="a", content="", section_number="1")
        b = AgendaItem(item_id=2, title="b", content="", section_number="2")
        a.attachments.append("x")
        assert b.attachments == []


class TestMeeting:
    def test_to_dict_round_trip_shape(self):
        m = Meeting(
            meeting_id="abc",
            title="Council",
            date="2026-05-01",
            start_time="6:00 PM",
            location="Council Chambers",
            has_video=True,
            has_agenda=True,
            video_url="https://example.com/v",
        )
        d = m.to_dict()
        assert d == {
            "meeting_id": "abc",
            "title": "Council",
            "date": "2026-05-01",
            "start_time": "6:00 PM",
            "location": "Council Chambers",
            "has_video": True,
            "has_agenda": True,
            "video_url": "https://example.com/v",
            "is_cancelled": False,
        }


class TestRecordingState:
    """Where the video would be, says something when there is no video."""

    def _s(self, has_video, is_cancelled, start, now):
        return meeting_recording_state(
            has_video, is_cancelled, start, now)

    def _at(self, day, hour=0, minute=0):
        y, m, d = (int(part) for part in day.split("-"))
        return datetime(y, m, d, hour, minute, tzinfo=SASKATOON_TZ)

    def test_a_video_says_nothing(self):
        start = meeting_start("2026-08-01", "09:00")
        assert self._s(True, False, start, self._at("2026-09-02")) is None

    def test_a_cancelled_meeting_says_nothing(self):
        start = meeting_start("2026-08-01", "09:00")
        assert self._s(False, True, start, self._at("2026-09-02")) is None

    def test_a_future_meeting_says_nothing(self):
        start = meeting_start("2026-09-05", "09:00")
        assert self._s(False, False, start, self._at("2026-09-02")) is None

    def test_an_unknown_start_says_nothing(self):
        assert self._s(False, False, None, self._at("2026-09-02")) is None

    def test_within_twelve_hours_is_pending(self):
        start = meeting_start("2026-08-01", "09:00")  # 09:00 that day
        # 20:29 the same day is 11h29m after the start: still pending.
        assert self._s(False, False, start, self._at("2026-08-01", 20, 29)) == "pending"

    def test_at_twelve_hours_it_is_not_recorded(self):
        start = meeting_start("2026-08-01", "09:00")
        assert self._s(False, False, start, self._at("2026-08-01", 21, 0)) == "not_recorded"

    def test_well_past_twelve_hours_is_not_recorded(self):
        start = meeting_start("2026-08-01", "09:00")
        assert self._s(False, False, start, self._at("2026-08-02")) == "not_recorded"


class TestMeetingStart:
    """The meeting's start as a Saskatchewan datetime."""

    def _at(self, day, hour=0, minute=0):
        y, m, d = (int(part) for part in day.split("-"))
        return datetime(y, m, d, hour, minute, tzinfo=SASKATOON_TZ)

    def test_date_and_time(self):
        assert meeting_start("2026-08-01", "09:30") == self._at("2026-08-01", 9, 30)

    def test_missing_time_is_midnight(self):
        assert meeting_start("2026-08-01", "") == self._at("2026-08-01")

    def test_none_time_is_midnight(self):
        assert meeting_start("2026-08-01", None) == self._at("2026-08-01")

    def test_garbage_time_is_midnight(self):
        assert meeting_start("2026-08-01", "xx:yy") == self._at("2026-08-01")

    def test_out_of_range_time_is_midnight(self):
        assert meeting_start("2026-08-01", "99:99") == self._at("2026-08-01")

    def test_missing_date_is_none(self):
        assert meeting_start("", "09:00") is None

    def test_garbage_date_is_none(self):
        assert meeting_start("not-a-date", "09:00") is None

    def test_timezone_is_saskatoon(self):
        start = meeting_start("2026-08-01", "09:00")
        assert start.utcoffset() == timedelta(hours=-6)


class TestHasCurrentSummaries:
    """What 'summarized' means, shared by the skip rule and the feeds."""

    def _real(self):
        return ItemSummary(description=["x"], chips=[])

    def _provisional(self):
        return ItemSummary(description=["x"], chips=[], provisional=True)

    def _legacy(self):
        return ItemSummary.from_dict({"chips": []})

    def test_absent_cache_is_not_current(self):
        assert has_current_summaries(None) is False

    def test_empty_cache_is_not_current(self):
        assert has_current_summaries({}) is False

    def test_a_real_summary_is_current(self):
        assert has_current_summaries({"1": self._real()}) is True

    def test_provisional_only_is_not_current(self):
        # Written before the meeting, from the agenda alone: it does not
        # settle a feed entry or skip a backfill.
        assert has_current_summaries({"1": self._provisional()}) is False

    def test_legacy_only_is_not_current(self):
        assert has_current_summaries({"1": self._legacy()}) is False

    def test_any_real_summary_among_degraded_ones_counts(self):
        cached = {"1": self._provisional(), "2": self._real()}
        assert has_current_summaries(cached) is True
