"""Tests for app.models — Transcript, Segment, ItemSummary, Meeting, AgendaItem."""

from app.models import AgendaItem, ItemSummary, Meeting, Segment, Transcript


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


class TestItemSummary:
    def test_round_trip(self):
        raw = {"category": "Outcome", "text": "Approved (8-3)"}
        s = ItemSummary.from_dict(raw)
        assert s.to_dict() == raw
        assert ItemSummary.from_dict(s.to_dict()) == s

    def test_extra_fields_dropped(self):
        # Forward-compat: extra fields silently dropped on load.
        raw = {"category": "Outcome", "text": "ok", "usefulness": "high"}
        s = ItemSummary.from_dict(raw)
        assert s.to_dict() == {"category": "Outcome", "text": "ok"}


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
