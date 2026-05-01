"""Tests for app.models — Transcript, Segment, ItemSummary."""

from app.models import ItemSummary, Segment, Transcript


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
