"""The dedicated heavy-breakdown job's cost controls.

Pinned here rather than in the model's ADR so a cost-affecting edit to
the trigger, the one-shot dedup, or the fire decision does not land
unseen.  The heavy work is off the daily loop and only fires for a
meeting the light pass is summarizing now, so the gate, the dedup, and
the fire decision are what actually keep spend where it should be: only
long items, forward only, once each.
"""

from unittest import mock

from app.item_categorizer import HEAVY_SPAN_MS, heavily_discussed_item_ids
from app.models import ItemSegment, ItemSummary
from scripts.heavy_breakdown import pick_breakdown_targets
from scripts.summarize_meetings import _maybe_fire_heavy_breakdown

MIN = 60 * 1000
HR = 60 * MIN
TWO_HR = 2 * HR


def _item(item_id, start_ms=None, end_ms=None, **overrides):
    item = {
        "item_id": item_id,
        "title": f"Item {item_id}",
    }
    if start_ms is not None:
        item["time_start_ms"] = start_ms
    if end_ms is not None:
        item["time_end_ms"] = end_ms
    item.update(overrides)
    return item


def _segment():
    return ItemSegment(title="T", start_ms=0, description=["A thing."])


def _summary(segments):
    return ItemSummary(description=["A thing."], chips=[], segments=segments)


class TestFireGate:
    """Two hours on the floor: the trigger the heavy job re-checks."""

    def test_just_over_two_hours_qualifies(self):
        assert heavily_discussed_item_ids([_item(1, 0, 125 * MIN)]) == [1]

    def test_just_under_does_not(self):
        assert heavily_discussed_item_ids([_item(1, 0, 119 * MIN)]) == []

    def test_exact_two_hours_does_not(self):
        assert heavily_discussed_item_ids([_item(1, 0, TWO_HR)]) == []

    def test_last_item_uses_its_own_end(self):
        # A 15-minute item that is last (no next) runs to its own end, 15
        # minutes not two hours: not a candidate.  With the same start but
        # a three-hour end, it is.
        assert heavily_discussed_item_ids(
            [_item(9, 0, 15 * MIN)]
        ) == []
        assert heavily_discussed_item_ids(
            [_item(9, 0, 3 * HR)]
        ) == [9]

    def test_recess_never_qualifies(self):
        assert heavily_discussed_item_ids([
            _item(1, 0, 4 * HR, is_recess=True),
        ]) == []

    def test_procedural_never_qualifies(self):
        # "Giving notice" is the ADR's example of a long procedural
        # placeholder.
        assert heavily_discussed_item_ids([
            _item(1, 0, 4 * HR, title="Giving notice of upcoming meetings"),
        ]) == []

    def test_partner_side_never_qualifies(self):
        # ADR 0025: the discussion is broken down on the primary's card.
        assert heavily_discussed_item_ids([
            _item(
                2, 0, 4 * HR,
                heard_with={"primary_item_id": 7},
            ),
        ]) == []

    def test_primary_side_still_qualifies(self):
        assert heavily_discussed_item_ids([
            _item(
                7, 0, 4 * HR,
                heard_with={"primary_item_id": 7, "primary_section": "7.1"},
            ),
        ]) == [7]

    def test_next_item_starts_measuring_a_short_items_span(self):
        # Item 1 runs to item 2's start (20 min), not to its own end
        # (three hours); item 2 is last, its span is its own end (10 min).
        # Neither reaches the floor.
        items = [
            _item(1, 0, 3 * HR),
            _item(2, 20 * MIN, 20 * MIN + 10 * MIN),
        ]
        assert heavily_discussed_item_ids(items) == []


class TestOneShotDedup:
    """An item with a breakdown is never re-asked."""

    def test_done_item_is_not_a_target(self):
        cached = {"7": _summary(segments=[_segment()])}
        items = [_item(7, 0, 4 * HR)]
        assert pick_breakdown_targets(cached, items) == []

    def test_declined_split_is_done(self):
        # Empty segments = the model read and declined to split. Same
        # state the chip pass uses when it did that: done, not pending.
        cached = {"7": _summary(segments=[])}
        assert pick_breakdown_targets(cached, [_item(7, 0, 4 * HR)]) == []

    def test_failed_attempt_is_not_done(self):
        # None = the pass has never run (or the last attempt failed).
        cached = {"7": _summary(segments=None)}
        assert [
            i["item_id"]
            for i in pick_breakdown_targets(cached, [_item(7, 0, 4 * HR)])
        ] == [7]

    def test_absent_entry_is_a_target(self):
        # An item the light pass summarized is in the cache; a meeting
        # the light pass has not summarized yet is not.  Both are valid
        # breakdown targets: the job writes the segments next to the
        # summary it holds (or creates one).
        assert [
            i["item_id"]
            for i in pick_breakdown_targets({}, [_item(7, 0, 4 * HR)])
        ] == [7]

    def test_only_long_items(self):
        # Distinct starts: each item's span runs to the next item's start.
        # Items 1 and 2 run three hours each and qualify; item 3 is last,
        # its span is its own ten minutes, and it does not.  Item 1 already
        # has its breakdown, so only item 2 is a target.
        cached = {"1": _summary(segments=[_segment()])}
        items = [
            _item(1, 0, 3 * HR),
            _item(2, 3 * HR, 6 * HR),
            _item(3, 6 * HR, 6 * HR + 10 * MIN),
        ]
        assert [
            i["item_id"]
            for i in pick_breakdown_targets(cached, items)
        ] == [2]


class TestDispatch:
    """The light pass fires the job once per meeting, and only when
    there is actually an item without a breakdown to do."""

    def _patch(self):
        return mock.patch("scripts.summarize_meetings._fire_heavy_breakdown")

    def test_no_candidates_no_fire(self):
        # All candidates are short: no fire.
        with self._patch() as fire:
            _maybe_fire_heavy_breakdown(
                "abc123",
                [_item(1, 0, 30 * MIN)],
                {"1": _summary(segments=None)},
            )
            fire.assert_not_called()

    def test_long_item_fires_once_for_the_meeting(self):
        with self._patch() as fire:
            _maybe_fire_heavy_breakdown(
                "abc123",
                [_item(1, 0, 4 * HR)],
                {"1": _summary(segments=None)},
            )
            fire.assert_called_once_with("abc123")

    def test_all_done_no_re_fire(self):
        # Every item that held the floor over two hours already has its
        # breakdown: the pass fires nothing.  This is the recurring-cost
        # bug that moved off the light loop.
        summaries = {
            "1": _summary(segments=[_segment()]),
            "2": _summary(segments=[_segment()]),
        }
        items = [_item(1, 0, 4 * HR), _item(2, 0, 4 * HR)]
        with self._patch() as fire:
            _maybe_fire_heavy_breakdown("abc123", items, summaries)
            fire.assert_not_called()

    def test_mixed_fires_once_for_the_meeting(self):
        # One done, one not: still one dispatch for the meeting, because
        # the heavy job re-derives its candidates and skips the done one
        # itself (one-shot).
        summaries = {
            "1": _summary(segments=[_segment()]),
            "2": _summary(segments=None),
        }
        items = [_item(1, 0, 4 * HR), _item(2, 0, 4 * HR)]
        with self._patch() as fire:
            _maybe_fire_heavy_breakdown("abc123", items, summaries)
            fire.assert_called_once_with("abc123")
