"""Tests for scripts/ab_cleanup.py — the cleanup-value A/B harness.

The harness decides whether ~15M tokens of backfill cost survives, so its
own failure modes matter: an unbalanced blind, a leaked arm label, or a
silent re-clean would each produce a confident and wrong answer.
"""

from collections import Counter

from scripts.ab_cleanup import (
    ARMS,
    TRANSCRIPT_WRAP,
    blind_orders,
    raw_slice,
    render_pairs,
    wrap_transcript,
)


def _seg(start_ms: int, text: str) -> dict:
    return {"start_ms": start_ms, "end_ms": start_ms + 5000, "text": text}


class TestBlindOrders:
    def test_split_is_balanced_at_realistic_sample_sizes(self):
        """Eleven independent coin flips land 9-2 far too often."""
        for n in range(2, 30):
            keys = [f"m/{i}" for i in range(n)]
            first = Counter(o[0] for o in blind_orders(keys).values())
            assert abs(first[ARMS[0]] - first[ARMS[1]]) <= 1

    def test_assignment_is_stable_across_runs(self):
        keys = [f"m/{i}" for i in range(11)]
        assert blind_orders(keys) == blind_orders(list(reversed(keys)))

    def test_every_item_gets_both_arms(self):
        for order in blind_orders([f"m/{i}" for i in range(11)]).values():
            assert set(order) == set(ARMS)


class TestRawSlice:
    def test_is_the_transcript_the_cleanup_pass_would_have_received(self):
        item = {"item_id": 1, "time_start_ms": 0, "time_end_ms": 20000}
        segments = [_seg(0, "Um, so we, we move the motion."), _seg(6000, "Carried.")]
        raw = raw_slice(item, segments)
        assert "move the motion" in raw
        assert "Carried" in raw

    def test_an_item_with_no_slice_is_empty(self):
        """Consent Items have no transcript, so both arms would be identical."""
        assert raw_slice({"item_id": 1}, []) == ""


class TestWrapTranscript:
    """A 34k-token single line is evidence the judge cannot reach."""

    def test_long_lines_are_broken_up(self):
        wrapped = wrap_transcript("word " * 400)
        assert max(len(line) for line in wrapped.splitlines()) <= TRANSCRIPT_WRAP

    def test_no_words_are_lost(self):
        text = "The motion carried unanimously. " * 50
        assert wrap_transcript(text).split() == text.split()

    def test_empty_transcript_is_safe(self):
        assert wrap_transcript("") == ""


class TestRenderPairs:
    def _pair(self):
        return {
            "key": "m/1",
            "section_number": "10.1",
            "title": "Transit Bylaw",
            "recommendation": "That the bylaw be approved.",
            "content": "",
            "transcript": "some words",
            "A": {"description": "Raises fines to $250.", "chips": []},
            "B": {
                "description": "Council considered a report.",
                "chips": [{"category": "Outcome", "text": "Approved"}],
            },
            "_key": {"A": "raw", "B": "clean"},
        }

    def test_the_judge_document_never_names_an_arm(self):
        rendered = render_pairs([self._pair()])
        assert "clean" not in rendered.lower().replace("cleanup", "")
        assert "raw" not in rendered.lower()

    def test_both_summaries_and_the_source_are_present(self):
        rendered = render_pairs([self._pair()])
        assert "Raises fines to $250." in rendered
        assert "Council considered a report." in rendered
        assert "That the bylaw be approved." in rendered
        assert "some words" in rendered

    def test_tie_is_offered_as_an_answer(self):
        """Forcing a preference on indistinguishable pairs invents a result."""
        assert "tie" in render_pairs([self._pair()])
