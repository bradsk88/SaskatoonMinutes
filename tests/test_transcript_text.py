"""Tests for app.transcript_text — text-shape helpers for transcript strings."""

from app.transcript_text import (
    sentence_around,
    split_sentences,
    strip_filler_leads,
)


# ── strip_filler_leads ───────────────────────────────────────────────


class TestStripFillerLeads:
    def test_i_think(self):
        assert strip_filler_leads("I think the budget is fine") == "the budget is fine"

    def test_um(self):
        assert strip_filler_leads("Um, we need to vote") == "we need to vote"

    def test_yeah(self):
        assert strip_filler_leads("Yeah, the city will also act") == "the city will also act"

    def test_yep(self):
        assert strip_filler_leads("Yep, staff confirmed") == "staff confirmed"

    def test_no_filler_passthrough(self):
        assert strip_filler_leads("The budget is fine") == "The budget is fine"

    def test_only_strips_at_start(self):
        # Filler in the middle should be preserved
        result = strip_filler_leads("Council voted, and I think it passed")
        assert "I think" in result


# ── split_sentences ──────────────────────────────────────────────────


class TestSplitSentences:
    def test_basic_split(self):
        text = "This is sentence one. Here is another one! And a third?"
        sentences = split_sentences(text)
        assert len(sentences) == 3

    def test_no_filter_keeps_short(self):
        # Default: no min/max — keeps everything non-empty.
        assert split_sentences("Ok. This sentence is long enough.") == [
            "Ok.",
            "This sentence is long enough.",
        ]

    def test_min_len_drops_short(self):
        result = split_sentences(
            "Ok. This sentence is long enough to keep.",
            min_len=12,
        )
        assert result == ["This sentence is long enough to keep."]

    def test_max_len_drops_long(self):
        long_sentence = "x" * 50 + "."
        result = split_sentences(
            f"Short one. {long_sentence}",
            max_len=20,
        )
        assert result == ["Short one."]

    def test_min_and_max(self):
        result = split_sentences(
            "Ok. Medium length here. " + "x" * 200 + ".",
            min_len=5,
            max_len=100,
        )
        assert "Medium length here." in result
        assert "Ok." not in result  # below min_len

    def test_empty(self):
        assert split_sentences("") == []

    def test_collapses_whitespace(self):
        result = split_sentences("a   b. c   d.")
        assert result == ["a b.", "c d."]


# ── sentence_around ──────────────────────────────────────────────────


class TestSentenceAround:
    def test_middle_sentence(self):
        text = "First. Middle sentence here. Last one."
        # "Middle" starts at index 7
        result = sentence_around(text, 7, 13)
        assert result == "Middle sentence here."

    def test_first_sentence(self):
        text = "First sentence. Second one."
        result = sentence_around(text, 0, 5)
        assert result == "First sentence."

    def test_no_terminators(self):
        text = "no terminators here at all"
        result = sentence_around(text, 3, 14)
        assert result == "no terminators here at all"
