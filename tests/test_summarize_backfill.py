"""Tests for the backfill's skip rule in scripts/summarize_meetings.py.

The rule decides what a repeated dispatch does.  Get it wrong in one
direction and the backfill never advances past the first batch; wrong in
the other and it declares 226 meetings done without summarizing any of
them.
"""

from app.models import Chip, ItemSummary
from scripts.summarize_meetings import is_current


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
