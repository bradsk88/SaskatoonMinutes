"""Tests for the CleanTranscript phase split in app.item_categorizer.

Covers the seam that makes prompt iteration affordable: cleanup can be
lifted out of ``extract_item_summaries``, cached, and fed back in.
"""

from app.item_categorizer import (
    GeminiExtractor,
    _cleanup_chunks,
    _cleanup_looks_truncated,
    clean_item_transcript,
    clean_meeting_transcripts,
    cleanup_fingerprint,
    extract_item_summaries,
)


class FakeResponse:
    """Stands in for a google-genai response object."""

    def __init__(self, finish_reason=None):
        if finish_reason is None:
            self.candidates = []
        else:
            self.candidates = [type("C", (), {"finish_reason": finish_reason})()]


def item(item_id: int, start: int = 0, end: int = 600_000, **extra) -> dict:
    base = {
        "item_id": item_id,
        "title": f"Item {item_id} about a rezoning application",
        "section_number": f"1.{item_id}",
        "time_start_ms": start,
        "time_end_ms": end,
    }
    base.update(extra)
    return base


def segments(text: str, start: int = 0, end: int = 600_000) -> list[dict]:
    return [{"start_ms": start, "end_ms": end, "text": text}]


class RecordingExtractor(GeminiExtractor):
    """Counts cleanup calls so we can assert the cache actually saved work."""

    def __init__(self):
        super().__init__(
            api_key="test-key",
            generate=lambda prompt, cats: "[]",
            clean_generate=self._clean,
        )
        self.clean_calls: list[str] = []

    def _clean(self, text: str) -> str:
        self.clean_calls.append(text)
        return f"CLEANED({text})"


class TestCleanupFingerprint:
    def test_is_stable_across_calls(self):
        assert cleanup_fingerprint() == cleanup_fingerprint()

    def test_changes_when_the_cleanup_prompt_changes(self, monkeypatch):
        before = cleanup_fingerprint()
        monkeypatch.setattr(
            "app.item_categorizer._build_cleanup_prompt",
            lambda text: f"a different instruction set\n{text}",
        )
        assert cleanup_fingerprint() != before

    def test_changes_when_the_model_changes(self, monkeypatch):
        before = cleanup_fingerprint()
        monkeypatch.setattr("app.item_categorizer.GEMINI_MODEL", "gemini-9.9-ultra")
        assert cleanup_fingerprint() != before

    def test_does_not_depend_on_any_transcript(self, monkeypatch):
        """The fingerprint identifies the prompt, not the content it cleans."""
        calls = []
        real = __import__(
            "app.item_categorizer", fromlist=["_build_cleanup_prompt"]
        )._build_cleanup_prompt

        def spy(text):
            calls.append(text)
            return real(text)

        monkeypatch.setattr("app.item_categorizer._build_cleanup_prompt", spy)
        cleanup_fingerprint()
        assert calls == [""]


class TestCleanupTruncationGuard:
    """A truncated CleanTranscript reads as clean prose, so it must be caught here."""

    def test_max_tokens_finish_reason_is_truncation(self):
        raw = "word " * 100
        assert _cleanup_looks_truncated(FakeResponse("MAX_TOKENS"), raw, raw)

    def test_finish_reason_is_matched_case_insensitively(self):
        raw = "word " * 100
        assert _cleanup_looks_truncated(FakeResponse("max_tokens"), raw, raw)

    def test_output_far_shorter_than_input_is_truncation(self):
        assert _cleanup_looks_truncated(FakeResponse("STOP"), "x" * 1000, "x" * 200)

    def test_modest_shrinkage_from_filler_removal_is_fine(self):
        assert not _cleanup_looks_truncated(FakeResponse("STOP"), "x" * 1000, "x" * 800)

    def test_missing_candidates_falls_back_to_the_length_check(self):
        assert not _cleanup_looks_truncated(FakeResponse(), "x" * 1000, "x" * 900)
        assert _cleanup_looks_truncated(FakeResponse(), "x" * 1000, "x" * 100)


class TestCleanItemTranscript:
    def test_cleans_the_slice_for_the_item(self):
        ex = RecordingExtractor()
        out = clean_item_transcript(item(1), segments("um so the uh rezoning"), ex)
        assert out == "CLEANED(um so the uh rezoning)"

    def test_returns_raw_text_when_the_extractor_is_disabled(self):
        ex = GeminiExtractor(api_key="")
        assert clean_item_transcript(item(1), segments("raw text"), ex) == "raw text"

    def test_empty_slice_makes_no_call(self):
        ex = RecordingExtractor()
        assert clean_item_transcript(item(1), [], ex) == ""
        assert ex.clean_calls == []


def many_segments(count: int, text: str) -> list[dict]:
    """*count* segments spread evenly across the item's 0-600s window."""
    step = 600_000 // count
    return [
        {"start_ms": i * step, "end_ms": (i + 1) * step, "text": text}
        for i in range(count)
    ]


class TestCleanupChunking:
    """One call per item does not scale to a 100-minute agenda item."""

    def test_a_short_slice_is_one_chunk(self):
        assert len(_cleanup_chunks(item(1), segments("short"))) == 1

    def test_a_long_slice_splits(self):
        segs = many_segments(40, "x" * 500)  # 20k chars
        chunks = _cleanup_chunks(item(1), segs, max_chars=8000)
        assert len(chunks) > 1

    def test_chunks_respect_the_size_limit(self):
        segs = many_segments(40, "x" * 500)
        for chunk in _cleanup_chunks(item(1), segs, max_chars=8000):
            assert len(chunk) <= 8000

    def test_no_segment_text_is_lost_or_reordered(self):
        segs = many_segments(40, "x" * 500)
        chunks = _cleanup_chunks(item(1), segs, max_chars=8000)
        rejoined = " ".join(chunks)
        assert rejoined == " ".join(s["text"] for s in segs)

    def test_a_single_oversized_segment_is_not_cut(self):
        segs = [{"start_ms": 0, "end_ms": 600_000, "text": "y" * 20000}]
        chunks = _cleanup_chunks(item(1), segs, max_chars=8000)
        assert chunks == ["y" * 20000]

    def test_blank_segments_are_dropped(self):
        segs = [
            {"start_ms": 0, "end_ms": 300_000, "text": "real"},
            {"start_ms": 300_000, "end_ms": 600_000, "text": "   "},
        ]
        assert _cleanup_chunks(item(1), segs) == ["real"]

    def test_chunks_are_cleaned_and_rejoined_in_order(self):
        ex = RecordingExtractor()
        segs = many_segments(4, "z" * 3000)  # 12k chars -> 2 chunks
        out = clean_item_transcript(item(1), segs, ex)
        assert len(ex.clean_calls) == 2
        assert out == f"CLEANED({ex.clean_calls[0]}) CLEANED({ex.clean_calls[1]})"

    def test_meeting_level_chunks_are_reassembled_per_item(self):
        ex = RecordingExtractor()
        segs = many_segments(4, "z" * 3000)
        out = clean_meeting_transcripts([item(1), item(2)], segs, ex)
        # Two items x two chunks each, fanned out through one pool.
        assert len(ex.clean_calls) == 4
        assert out["1"] == out["2"]
        assert out["1"].count("CLEANED(") == 2


class TestCleanMeetingTranscripts:
    def test_cleans_every_item(self):
        ex = RecordingExtractor()
        out = clean_meeting_transcripts([item(1), item(2)], segments("hello"), ex)
        assert set(out) == {"1", "2"}
        assert len(ex.clean_calls) == 2

    def test_cached_entries_are_reused_and_cost_nothing(self):
        ex = RecordingExtractor()
        out = clean_meeting_transcripts(
            [item(1), item(2)], segments("hello"), ex,
            cached={"1": "already cleaned"},
        )
        assert out["1"] == "already cleaned"
        assert out["2"] == "CLEANED(hello)"
        assert len(ex.clean_calls) == 1

    def test_fully_cached_meeting_makes_no_calls(self):
        ex = RecordingExtractor()
        out = clean_meeting_transcripts(
            [item(1), item(2)], segments("hello"), ex,
            cached={"1": "a", "2": "b"},
        )
        assert out == {"1": "a", "2": "b"}
        assert ex.clean_calls == []

    def test_result_covers_only_the_items_passed_in(self):
        ex = RecordingExtractor()
        out = clean_meeting_transcripts(
            [item(1)], segments("hello"), ex, cached={"1": "a", "99": "stale"},
        )
        assert out == {"1": "a"}


class TestExtractAcceptsPreCleanedText:
    def test_supplied_text_skips_the_cleanup_call(self):
        ex = RecordingExtractor()
        extract_item_summaries(
            item(1), segments("raw"), gemini_extractor=ex,
            cleaned_transcript_text="already cleaned",
        )
        assert ex.clean_calls == []

    def test_omitting_it_still_cleans(self):
        ex = RecordingExtractor()
        extract_item_summaries(item(1), segments("raw"), gemini_extractor=ex)
        assert ex.clean_calls == ["raw"]

    def test_supplied_text_is_what_reaches_the_extractors(self):
        """Money in the pre-cleaned text is found; money in the raw text is not."""
        ex = RecordingExtractor()
        chips = extract_item_summaries(
            item(1), segments("nothing here"), gemini_extractor=ex,
            cleaned_transcript_text="Council approved $250,000 for the new pathway.",
        )
        assert any(c["category"] == "Cost & Funding" for c in chips)

    def test_empty_string_is_honoured_rather_than_treated_as_absent(self):
        ex = RecordingExtractor()
        extract_item_summaries(
            item(1), segments("raw"), gemini_extractor=ex,
            cleaned_transcript_text="",
        )
        assert ex.clean_calls == []
