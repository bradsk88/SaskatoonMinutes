"""Tests for the backfill's skip rule in scripts/summarize_meetings.py.

The rule decides what a repeated dispatch does.  Get it wrong in one
direction and the backfill never advances past the first batch; wrong in
the other and it declares 226 meetings done without summarizing any of
them.
"""

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
from scripts.summarize_meetings import is_current, needs_segment_backfill


def _current(text: str = "Council approved the thing.") -> ItemSummary:
    return ItemSummary(description=text, chips=[Chip("Outcome", "Approved")])


def _legacy() -> ItemSummary:
    """What the cache held before the aggregate existed: chips, no description."""
    return ItemSummary(description=None, chips=[Chip("Outcome", "Approved")])


def _ineligible() -> ItemSummary:
    """How an item that cannot be summarized is stored."""
    return ItemSummary(description=None, chips=[])


class TestIsCurrent:
    def test_a_meeting_never_summarized_is_not_current(self):
        assert is_current(None) is False

    def test_pre_aggregate_summaries_are_not_current(self):
        """Every in-term meeting looks like this before the backfill runs."""
        assert is_current({"1": _legacy(), "2": _legacy()}) is False

    def test_summaries_from_this_backfill_are_current(self):
        assert is_current({"1": _current(), "2": _current()}) is True

    def test_ineligible_items_do_not_drag_a_meeting_back_to_legacy(self):
        """Most items in a meeting are ineligible and stored empty.

        Requiring every summary to carry a description would mark every
        real meeting legacy, so the backfill would redo all of them on
        every dispatch — the loop this rule exists to break.
        """
        cached = {"1": _current(), "2": _ineligible(), "3": _ineligible()}
        assert is_current(cached) is True

    def test_a_run_with_no_gemini_key_is_not_current(self):
        """No key means no descriptions. That output is degraded, not done."""
        cached = {"1": _legacy(), "2": _ineligible()}
        assert is_current(cached) is False

    def test_an_empty_cache_entry_is_not_current(self):
        assert is_current({}) is False


class TestBackfillConverges:
    def test_a_second_dispatch_skips_what_the_first_one_finished(self):
        """The bug this replaced: --force redid the same meetings forever."""
        cache: dict[str, dict[str, ItemSummary]] = {
            "m1": {"1": _legacy()},
            "m2": {"1": _legacy()},
        }
        summarized = []

        def dispatch(limit: int) -> None:
            """The shape of the real loop: walk the list, stop at --limit."""
            processed = 0
            for mid in sorted(cache):
                if processed >= limit:
                    break
                if is_current(cache[mid]):
                    continue
                cache[mid] = {"1": _current()}
                summarized.append(mid)
                processed += 1

        dispatch(limit=1)
        dispatch(limit=1)
        assert summarized == ["m1", "m2"]


# ── The segments exception (ADR 0029) ─────────────────────────────


def _long_item(item_id=1):
    """A 45-minute item: over the 30-minute segment bar."""
    return AgendaItem(
        item_id=item_id,
        title="2027 Operating and Capital Budget",
        content="Report on the budget.",
        section_number="1.",
        time_start_ms=0,
        time_end_ms=45 * 60 * 1000,
    )


def _short_item(item_id=2):
    return AgendaItem(
        item_id=item_id,
        title="Funding Decision",
        content="Report on the funding allocation.",
        section_number="2.",
        time_start_ms=0,
        time_end_ms=10 * 60 * 1000,
    )


def _rich_transcript():
    """100 x 6-word segments filling 45 minutes: over the word floor."""
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


def _thin_transcript():
    return Transcript(segments=[
        Segment(start_ms=0, end_ms=1000, text="hello there"),
    ])


def _source(items):
    return InMemoryMeetingSource(
        details={"m1": MeetingDetail(agenda_items=items)},
    )


class TestNeedsSegmentBackfill:
    """The one exception to the skip rule, and why it converges."""

    def test_a_long_item_without_segments_is_pending(self):
        cached = {"1": ItemSummary(description=["A thing."], chips=[])}
        assert needs_segment_backfill(
            _source([_long_item(), _short_item()]), "m1", cached,
            _rich_transcript(),
        ) is True

    def test_topics_present_means_done(self):
        cached = {"1": ItemSummary(
            description=["A thing."], chips=[],
            segments=[ItemSegment(
                title="Staff", start_ms=0,
                description=["Laid out the budget."], chips=[],
            )],
        )}
        assert needs_segment_backfill(
            _source([_long_item(), _short_item()]), "m1", cached,
            _rich_transcript(),
        ) is False

    def test_pre_chips_topics_are_pending_again(self):
        """The archive shape before topics carried chips: the walk keeps
        flagging the meeting until the backfill re-asks the item, and
        the re-asked topics carry chips."""
        cached = {"1": ItemSummary(
            description=["A thing."], chips=[],
            segments=[ItemSegment(
                title="Staff", start_ms=0,
                description=["Laid out the budget."], chips=None,
            )],
        )}
        assert needs_segment_backfill(
            _source([_long_item(), _short_item()]), "m1", cached,
            _rich_transcript(),
        ) is True

    def test_attempted_and_empty_means_done(self):
        """The model read the span and found nothing to split.

        Treating this as pending would re-do the meeting on every
        dispatch forever — the exact loop the skip rule exists to
        break.
        """
        cached = {"1": ItemSummary(description=["A thing."], chips=[],
                                   segments=[])}
        assert needs_segment_backfill(
            _source([_long_item(), _short_item()]), "m1", cached,
            _rich_transcript(),
        ) is False

    def test_a_short_item_never_flags(self):
        cached = {"2": ItemSummary(description=["A thing."], chips=[])}
        assert needs_segment_backfill(
            _source([_short_item()]), "m1", cached,
            _rich_transcript(),
        ) is False

    def test_a_thin_span_never_flags(self):
        """A 45-minute bookmark with 3 words is a broken bookmark, not
        a meeting missing its topics."""
        cached = {"1": ItemSummary(description=["A thing."], chips=[])}
        assert needs_segment_backfill(
            _source([_long_item()]), "m1", cached,
            _thin_transcript(),
        ) is False

    def test_no_transcript_never_flags(self):
        cached = {"1": ItemSummary(description=["A thing."], chips=[])}
        assert needs_segment_backfill(
            _source([_long_item()]), "m1", cached, None,
        ) is False

    def test_a_not_current_meeting_never_flags(self):
        """The exception applies to current summaries only; everything
        else the walk already re-does for its own reasons."""
        legacy = {"1": ItemSummary(description=None, chips=[])}
        assert not is_current(legacy)
        assert needs_segment_backfill(
            _source([_long_item()]), "m1", legacy,
            _rich_transcript(),
        ) is False

    def test_an_uncached_long_item_flags(self):
        """The item has no cached entry at all (the agenda changed after
        the summary was written): its topics were never asked for."""
        cached = {"2": ItemSummary(description=["A thing."], chips=[])}
        assert needs_segment_backfill(
            _source([_long_item(), _short_item()]), "m1", cached,
            _rich_transcript(),
        ) is True
