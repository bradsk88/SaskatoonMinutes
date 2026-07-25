"""Tests for scripts/add_eval_fixture.py — mechanical fixture selection.

The point of ranking items by name density is that nobody chooses them by
hand.  So the ranking has to be reproducible and has to count names rather
than sentences: a heuristic that ranked by sentence count would quietly
put the selection back in the author's hands.
"""

from scripts.add_eval_fixture import (
    name_candidates,
    rank_items,
    trim_transcript,
)


def _seg(start_ms: int, text: str) -> dict:
    return {"start_ms": start_ms, "end_ms": start_ms + 5000, "text": text}


def _item(item_id: int, start: int = 0, end: int = 100000, **kw) -> dict:
    return {
        "item_id": item_id,
        "title": f"Item {item_id}",
        "section_number": "1.1",
        "recommendation": "That the report be received as information.",
        "time_start_ms": start,
        "time_end_ms": end,
        **kw,
    }


class TestNameCandidates:
    def test_finds_a_name_the_roster_does_not_cover(self):
        assert "Kobussen" in name_candidates("The delegate was Karen Kobussen.")

    def test_ignores_names_the_roster_already_has(self):
        """A transcript that already says "Meewasin" gives cleanup nothing to do."""
        found = name_candidates("She thanked the Meewasin Valley Authority.")
        assert "Meewasin" not in found

    def test_ignores_words_capitalized_only_by_the_sentence_boundary(self):
        """ASR capitalizes every sentence; counting those ranks by verbosity."""
        assert name_candidates("Right. Okay. Thank you. Next. Moving on.") == set()

    def test_counts_the_same_name_once(self):
        text = "Rumely spoke. The Rumely position is that Rumely objects."
        assert name_candidates(text) == {"Rumely"}

    def test_first_word_of_the_text_is_not_a_candidate(self):
        assert name_candidates("Rumely spoke to the item.") == set()


class TestRankItems:
    def test_orders_by_distinct_names_not_by_length(self):
        segments = [
            _seg(0, "We heard from Kobussen and from Wanuskewin about this."),
            _seg(200000, "It was a long discussion. " * 40),
        ]
        items = [_item(1, 0, 120000), _item(2, 200000, 400000)]
        ranked = rank_items(items, segments)
        assert [r["item"]["item_id"] for r in ranked] == [1, 2]
        assert ranked[1]["chars"] > ranked[0]["chars"]

    def test_ties_break_on_item_id_so_reruns_pick_the_same_items(self):
        segments = [_seg(0, "Nothing named here at all."), _seg(50000, "Nor here.")]
        items = [_item(9, 200000, 400000), _item(3, 0, 120000)]
        assert [r["item"]["item_id"] for r in rank_items(items, segments)] == [3, 9]

    def test_an_item_with_no_slice_scores_nothing(self):
        """Consent Items would hand both A/B arms the same empty text."""
        segments = [_seg(0, "Speaking about Kobussen.")]
        consent = _item(
            1, 0, 10000, timestamp_inherited=True,
            recommendation="That Councillor MacDonald be appointed to the "
                           "Meewasin Valley Authority board.",
        )
        ranked = rank_items([consent], segments)
        assert ranked[0]["chars"] == 0
        assert ranked[0]["names"] == []

    def test_ineligible_items_are_not_ranked(self):
        segments = [_seg(0, "Speaking about Kobussen.")]
        recess = _item(1, 0, 120000, is_recess=True)
        assert rank_items([recess], segments) == []


class TestTrimTranscript:
    def test_keeps_only_what_the_chosen_items_reach(self):
        segments = [_seg(0, "first"), _seg(500000, "far away")]
        kept = trim_transcript(segments, [_item(1, 0, 10000)])
        assert [s["text"] for s in kept] == ["first"]

    def test_a_segment_shared_by_two_items_is_kept_once(self):
        segments = [_seg(0, "shared")]
        kept = trim_transcript(segments, [_item(1, 0, 3000), _item(2, 2000, 9000)])
        assert len(kept) == 1

    def test_segments_stay_in_time_order(self):
        segments = [_seg(0, "a"), _seg(10000, "b"), _seg(20000, "c")]
        kept = trim_transcript(segments, [_item(2, 20000, 30000), _item(1, 0, 5000)])
        assert [s["start_ms"] for s in kept] == [0, 20000]
