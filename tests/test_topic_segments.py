"""The topics a long item moved through, each anchored to a moment.

A 155-minute budget debate is one agenda item, but it plays as a series
of distinct topics (ADR ``0029``).  These tests pin the gate (which item
is worth the LLM call), the sanitizer (what the model's answer survives
meeting the record with), the model round-trip, and what the page must
draw.
"""

import json
import os

from app.item_categorizer import (
    SEGMENT_MAX_TOPICS,
    GeminiExtractor,
    _build_segments_prompt,
    _parse_ts,
    _sanitize_segments,
    has_segment_content,
    needs_topic_segments,
)
from app.models import ItemSegment, ItemSummary

MIN = 60 * 1000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEETING = os.path.join(ROOT, "app", "templates", "meeting.html")
CSS = os.path.join(ROOT, "app", "static", "style.css")


def _item(**overrides):
    base = {
        "item_id": 7,
        "title": "2027 Operating and Capital Budget",
        "time_start_ms": 0,
        "time_end_ms": 45 * MIN,
    }
    base.update(overrides)
    return base


class TestGate:
    """The bar is 30+ minutes of the item's own span."""

    def test_long_item_qualifies(self):
        assert needs_topic_segments(_item())

    def test_exact_threshold_qualifies(self):
        assert needs_topic_segments(_item(time_end_ms=30 * MIN))

    def test_just_under_does_not(self):
        assert not needs_topic_segments(_item(time_end_ms=30 * MIN - 1))

    def test_no_timestamps_does_not(self):
        assert not needs_topic_segments(
            _item(time_start_ms=None, time_end_ms=None),
        )

    def test_inherited_span_does_not(self):
        # A borrowed span identifies no audio of its own.
        assert not needs_topic_segments(_item(timestamp_inherited=True))

    def test_recess_does_not(self):
        assert not needs_topic_segments(_item(is_recess=True))

    def test_procedural_does_not(self):
        assert not needs_topic_segments(
            _item(title="Giving notice of upcoming meetings"),
        )

    def test_jointly_heard_partner_does_not(self):
        # The discussion is recorded on the primary's card.
        assert not needs_topic_segments(_item(heard_with={
            "primary_item_id": 9,
            "primary_section": "9.1",
            "partners": ["7.1"],
        }))

    def test_jointly_heard_primary_qualifies(self):
        assert needs_topic_segments(_item(heard_with={
            "primary_item_id": 7,
            "primary_section": "7.1",
            "partners": ["9.1"],
        }))


class TestHasSegmentContent:
    """The word floor the skip rule and the call site share.

    The duration gate runs on the eSCRIBE bookmark, which lies: a
    "Recess" span that inherits five hours, a 235-minute placeholder of
    silence.  The transcript is the only witness to whether there is
    anything to split, and the walk's backfill check must not flag a
    meeting whose long items are like that, or it would re-do that
    meeting on every dispatch.
    """

    def _rich(self):
        # 100 segments of 6 words: over the 500-word floor.
        return [
            {
                "start_ms": i * 27_000,
                "end_ms": i * 27_000 + 26_000,
                "text": (
                    "the council discussed the next part of the "
                    "budget this way today"
                ),
            }
            for i in range(100)
        ]

    def test_a_rich_span_has_content(self):
        assert has_segment_content(_item(), self._rich())

    def test_an_empty_span_does_not(self):
        assert not has_segment_content(_item(), [])

    def test_a_single_segment_does_not(self):
        assert not has_segment_content(
            _item(), [{"start_ms": 0, "end_ms": 1000, "text": "hi."}],
        )

    def test_a_thin_span_does_not(self):
        assert not has_segment_content(
            _item(), [{"start_ms": 0, "end_ms": 1000, "text": "hello there"}],
        )

    def test_the_window_is_the_span_when_given(self):
        # The item's bookmark is empty, but the jointly-heared group's
        # union window carries the discussion.
        item = _item(time_start_ms=None, time_end_ms=None)
        assert not has_segment_content(item, self._rich())
        assert has_segment_content(item, self._rich(), window=(0, 45 * MIN))


class TestSanitize:
    """What the model's topic list survives meeting the record with."""

    KNOWN = [0, 60_000, 120_000, 180_000]

    def _seg(self, title, start, takeaway="It did a thing."):
        return {"title": title, "start": start, "takeaway": takeaway}

    def test_copied_timestamp_passes_through(self):
        out = _sanitize_segments(
            [self._seg("Staff presentation", "1:00")], self.KNOWN,
        )
        assert [s["start_ms"] for s in out] == [60_000]
        assert out[0]["title"] == "Staff presentation"
        assert out[0]["takeaway"] == "It did a thing."

    def test_off_by_seconds_snaps_to_nearest(self):
        # 61,000 ms is 1 s from 60,000 and 59 s from 120,000.
        out = _sanitize_segments(
            [self._seg("Police budget", "1:01")], self.KNOWN,
        )
        assert out[0]["start_ms"] == 60_000

    def test_invented_time_snaps_to_nearest_real_one(self):
        # 1:30:00 is nowhere near the item; it snaps to the last line
        # the model was shown, not to a silent invention.
        out = _sanitize_segments(
            [self._seg("The vote", "1:30:00")], self.KNOWN,
        )
        assert out[0]["start_ms"] == 180_000

    def test_unparseable_start_is_dropped(self):
        out = _sanitize_segments(
            [self._seg("No time", "somewhere around noon")], self.KNOWN,
        )
        assert out == []

    def test_empty_takeaway_is_dropped(self):
        out = _sanitize_segments(
            [self._seg("No words", "1:00:00", takeaway="  ")], self.KNOWN,
        )
        assert out == []

    def test_order_restored_and_overlaps_collapsed(self):
        # The model answered out of order, and two topics snapped onto
        # the same real start.
        out = _sanitize_segments([
            self._seg("Later", "3:00:00"),
            self._seg("First", "0:00:00"),
            self._seg("Also first", "0:00:02"),
        ], self.KNOWN)
        assert [s["start_ms"] for s in out] == [0, 180_000]
        assert [s["title"] for s in out] == ["First", "Later"]

    def test_cap_keeps_first_topics_and_the_last(self):
        # The last topic is where the motion lands, so the cap trims
        # the middle rather than the end.
        known = [i * MIN for i in range(14)]
        entries = [
            self._seg(f"Topic {i}", f"{i // 60}:{i % 60:02d}:00")
            for i in range(14)
        ]
        out = _sanitize_segments(entries, known)
        assert len(out) == SEGMENT_MAX_TOPICS
        assert out[0]["title"] == "Topic 0"
        assert out[-1]["title"] == "Topic 13"

    def test_no_known_starts_is_nothing(self):
        assert _sanitize_segments([self._seg("X", "1:00:00")], []) == []

    def test_not_a_list_is_nothing(self):
        assert _sanitize_segments("nope", self.KNOWN) == []


class TestTimestamps:
    def test_parses_hms(self):
        assert _parse_ts("1:02:03") == 3_723_000

    def test_parses_ms(self):
        assert _parse_ts("2:05") == 125_000

    def test_parses_bare_milliseconds(self):
        assert _parse_ts(123_456) == 123_456

    def test_rejects_garbage(self):
        assert _parse_ts("noonish") is None
        assert _parse_ts("") is None


class TestExtractorSegments:
    """The third call, with the transcript timestamped line by line."""

    def _long_transcript(self):
        # 100 segments of 6 words: enough to clear the word floor, and
        # each line carries a start the model can copy back.
        segs = []
        for i in range(100):
            start = i * 27_000  # 45 minutes total
            segs.append({
                "start_ms": start,
                "end_ms": start + 26_000,
                "text": (
                    "the council discussed the next part of the "
                    "budget this way today"
                ),
            })
        return segs

    def _stub(self, payload, captured):
        def _generate(prompt, allowed):
            if captured is not None:
                captured["prompt"] = prompt
            return json.dumps(payload)

        return GeminiExtractor(api_key=None, generate=_generate)

    def test_returns_topics_anchored_to_real_starts(self):
        captured = {}
        ex = self._stub({
            "segments": [
                {"title": "Staff presentation", "start": "0:00:00",
                 "takeaway": "Staff laid out the budget."},
                {"title": "Police numbers", "start": "26:59",
                 "takeaway": "A 9% increase was proposed."},
            ],
        }, captured)
        out = ex.extract_segments(_item(), self._long_transcript())
        # "26:59" is 1 s off segment 60's start (27:00) and 26 s off
        # segment 59's (26:33), so it snaps to 27:00.
        assert [s["start_ms"] for s in out] == [0, 27 * MIN]
        assert out[0]["title"] == "Staff presentation"

    def test_prompt_carries_title_and_timestamped_lines(self):
        captured = {}
        ex = self._stub({"segments": []}, captured)
        ex.extract_segments(_item(), self._long_transcript())
        assert "2027 Operating and Capital Budget" in captured["prompt"]
        assert "[0:00] the council discussed" in captured["prompt"]
        assert "[27:27] the council discussed" in captured["prompt"]

    def test_empty_answer_is_a_valid_answer(self):
        ex = self._stub({"segments": []}, None)
        assert ex.extract_segments(_item(), self._long_transcript()) == []

    def test_empty_span_never_calls_the_model(self):
        # The bookmark said 45 minutes; the transcript says nothing was
        # said in that span. No call, no topics.
        def _fail(prompt, allowed):
            raise AssertionError("the model must not be called")

        ex = GeminiExtractor(api_key=None, generate=_fail)
        assert ex.extract_segments(_item(), []) == []

    def test_single_segment_never_calls_the_model(self):
        def _fail(prompt, allowed):
            raise AssertionError("the model must not be called")

        ex = GeminiExtractor(api_key=None, generate=_fail)
        assert ex.extract_segments(
            _item(), [{"start_ms": 0, "end_ms": 1000, "text": "hi."}],
        ) == []

    def test_thin_span_never_calls_the_model(self):
        # Under the word floor: the duration gate ran on a bookmark
        # that lied.
        def _fail(prompt, allowed):
            raise AssertionError("the model must not be called")

        ex = GeminiExtractor(api_key=None, generate=_fail)
        thin = [{"start_ms": 0, "end_ms": 1000, "text": "hello there"}]
        assert ex.extract_segments(_item(), thin) == []

    def test_prompt_asks_for_an_empty_list_when_unsplit(self):
        prompt = _build_segments_prompt(_item(), "[0:00:00] x")
        assert "return an empty list" in prompt
        assert "Do not force" in prompt


class TestModelRoundTrip:
    def test_segment_round_trips(self):
        s = ItemSegment(title="Police numbers", start_ms=60_000,
                        takeaway="A 9% increase.")
        assert ItemSegment.from_dict(s.to_dict()) == s

    def test_absent_segments_load_as_never(self):
        # Three states (ADR 0029): no key is "never had the segment
        # pass" — the backfill's work queue, distinct from "attempted,
        # nothing to split" below.
        s = ItemSummary.from_dict({"description": ["A thing."]})
        assert s.segments is None

    def test_explicit_empty_segments_load_as_attempted(self):
        s = ItemSummary.from_dict({"description": ["A thing."], "segments": []})
        assert s.segments == []

    def test_to_dict_omits_never(self):
        s = ItemSummary(description=["A thing."])
        assert s.segments is None
        assert "segments" not in s.to_dict()

    def test_to_dict_writes_attempted_empty(self):
        s = ItemSummary(description=["A thing."], segments=[])
        assert s.to_dict()["segments"] == []

    def test_to_dict_writes_segments_when_present(self):
        s = ItemSummary(
            description=["A thing."],
            segments=[ItemSegment("Police numbers", 60_000, "A 9% increase.")],
        )
        assert s.to_dict()["segments"] == [
            {"title": "Police numbers", "start_ms": 60_000,
             "takeaway": "A 9% increase."},
        ]

    def test_segments_load_from_cache_shape(self):
        s = ItemSummary.from_dict({
            "description": ["A thing."],
            "segments": [
                {"title": "Police numbers", "start_ms": 60_000,
                 "takeaway": "A 9% increase."},
            ],
        })
        assert s.segments[0].title == "Police numbers"
        assert s.segments[0].start_ms == 60_000


class TestPageContract:
    """Source-level pins: what the details page must and must not draw.

    The page is plain JavaScript with no test harness, so the
    guarantees that matter — upstream text is escaped, and every topic
    is reachable — are pinned here rather than left to review.
    """

    def _read(self, path):
        return open(path, encoding="utf-8").read()

    def test_topics_are_escaped(self):
        src = self._read(MEETING)
        assert "${escapeHtml(s.title)}" in src
        assert "${escapeHtml(s.takeaway)}" in src

    def test_every_topic_seeks_the_video(self):
        src = self._read(MEETING)
        assert "onclick=\"seekVideo(${s.start_ms})\"" in src

    def test_topics_share_a_spine(self):
        css = self._read(CSS)
        assert ".item-segments" in css
        assert "border-left" in css
