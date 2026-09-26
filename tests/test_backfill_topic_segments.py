"""Tests for scripts/backfill_topic_segments.py.

The one-off that gives the archive's long items their topics without
re-summarizing the meetings that already have descriptions and chips:
one Gemini call per long item, everything else untouched.
"""

import pytest

from app.item_categorizer import ExtractionFailed, QuotaExhausted
from app.meeting_source import InMemoryMeetingSource
from app.models import (
    AgendaItem,
    Chip,
    ItemSegment,
    ItemSummary,
    MeetingDetail,
    Segment,
    Transcript,
)
from scripts.backfill_topic_segments import backfill_meeting

MIN = 60 * 1000


def _long_item(item_id=1):
    return AgendaItem(
        item_id=item_id,
        title="2027 Operating and Capital Budget",
        content="Report on the budget.",
        section_number="1.",
        time_start_ms=0,
        time_end_ms=45 * MIN,
    )


def _short_item(item_id=2):
    return AgendaItem(
        item_id=item_id,
        title="Funding Decision",
        content="Report on the funding allocation.",
        section_number="2.",
        time_start_ms=0,
        time_end_ms=10 * MIN,
    )


def _rich_transcript():
    return Transcript(segments=[
        Segment(
            start_ms=i * 27_000,
            end_ms=i * 27_000 + 26_000,
            text=(
                "the council discussed the next part of the "
                "budget this way today"
            ),
        )
        for i in range(100)
    ])


def _cached():
    return {
        "1": ItemSummary(
            description=["A thing."],
            chips=[Chip("Outcome", "Approved")],
        ),
        "2": ItemSummary(description=None, chips=[]),
    }


class _Extractor:
    """Records what it was called for; answers *result* or raises."""

    def __init__(self, result=None, raise_exc=None):
        self.calls = []
        self._result = result
        self._raise_exc = raise_exc

    def extract_segments(self, item, transcript_segments, window=None):
        self.calls.append(item["item_id"])
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result or []


def _run(result=None, raise_exc=None, items=None, cached=None):
    source = InMemoryMeetingSource(
        details={"m1": MeetingDetail(agenda_items=items or [
            _long_item(), _short_item(),
        ])},
    )
    extractor = _Extractor(result=result, raise_exc=raise_exc)
    out = backfill_meeting(
        source, extractor, "m1",
        cached if cached is not None else _cached(),
        _rich_transcript(),
    )
    return out, extractor


class TestCandidateSelection:
    def test_only_the_long_item_is_asked(self):
        topics = [{"title": "Staff", "start_ms": 0, "takeaway": "T."}]
        out, extractor = _run(result=topics)
        assert extractor.calls == [1]
        assert out["1"].segments[0].title == "Staff"

    def test_the_existing_summary_is_preserved(self):
        topics = [{"title": "Staff", "start_ms": 0, "takeaway": "T."}]
        out, _ = _run(result=topics)
        assert out["1"].description == ["A thing."]
        assert [c.category for c in out["1"].chips] == ["Outcome"]
        # The short item is byte-identical to what was cached.
        assert out["2"].to_dict() == ItemSummary(
            description=None, chips=[],
        ).to_dict()

    def test_a_meeting_with_nothing_to_add_returns_none(self):
        # No long items at all: no call, no change, nothing to save.
        out, extractor = _run(items=[_short_item()])
        assert out is None
        assert extractor.calls == []

    def test_an_item_that_has_topics_is_skipped(self):
        cached = _cached()
        cached["1"] = ItemSummary(
            description=["A thing."], chips=[],
            segments=[ItemSegment("Staff", 0, "Laid out the budget.")],
        )
        out, extractor = _run(items=[_long_item(), _short_item()],
                              cached=cached)
        assert out is None
        assert extractor.calls == []

    def test_an_item_with_no_cached_entry_gets_one(self):
        topics = [{"title": "Staff", "start_ms": 0, "takeaway": "T."}]
        out, _ = _run(result=topics, cached={"2": ItemSummary(None, [])})
        assert out["1"].segments[0].title == "Staff"
        assert out["1"].description is None


class TestFailureBehaviour:
    def test_a_quota_rejection_stops_the_run(self):
        with pytest.raises(QuotaExhausted):
            _run(raise_exc=QuotaExhausted("daily quota used"))

    def test_a_failed_item_costs_its_topics_not_the_run(self):
        # Item 1 fails; the meeting is otherwise untouched, and the
        # item has no segments — the next dispatch retries it.
        out, _ = _run(raise_exc=ExtractionFailed("boom"))
        assert out is None
        assert _cached()["1"].segments is None

    def test_an_empty_answer_records_the_attempt(self):
        """The model read the span and found nothing to split.

        The empty list is persisted so the walk's backfill check does
        not re-flag this meeting on every dispatch.
        """
        out, extractor = _run(result=[])
        assert extractor.calls == [1]
        assert out["1"].segments == []

    def test_a_repeated_empty_answer_saves_nothing_new(self):
        # An explicit empty list is "done": a re-run does not re-ask
        # the model the question it already answered.
        cached = _cached()
        cached["1"] = ItemSummary(description=["A thing."], chips=[],
                                  segments=[])
        out, extractor = _run(result=[], cached=cached)
        assert out is None
        assert extractor.calls == []
