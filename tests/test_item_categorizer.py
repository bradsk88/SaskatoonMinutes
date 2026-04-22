"""Tests for app.item_categorizer — the chip-summary extractor."""

import pytest

from app.item_categorizer import (
    CATEGORIES,
    CATEGORY_GROUP,
    MAX_SUMMARY_CHARS,
    SEMANTIC_CATEGORIES,
    GeminiExtractor,
    _extract_amendment,
    _extract_cost_funding,
    _extract_data_cited,
    _extract_declared_conflict,
    _extract_delegation,
    _extract_next_step,
    _extract_outcome,
    _extract_procedural_note,
    _extract_related_deferred,
    _extract_vote_breakdown,
    _sanitize_chips,
    _slice_transcript,
    _split_sentences,
    _trim_to_chip,
    extract_item_summaries,
    is_eligible_for_summary,
)


def _seg(start_ms: int, text: str, length: int = 5000) -> dict:
    return {"start_ms": start_ms, "end_ms": start_ms + length, "text": text}


# ── Category metadata ───────────────────────────────────────────────────────


class TestCategoryMetadata:
    def test_every_category_has_a_group(self):
        missing = [c for c in CATEGORIES if c not in CATEGORY_GROUP]
        assert missing == []

    def test_groups_are_from_fixed_palette(self):
        allowed = {"decision", "money", "context", "voices", "impact", "future"}
        assert set(CATEGORY_GROUP.values()) <= allowed

    def test_count_is_23(self):
        assert len(CATEGORIES) == 23


# ── Trimming ────────────────────────────────────────────────────────────────


class TestTrimToChip:
    def test_short_passthrough(self):
        assert _trim_to_chip("Approved (8-3)") == "Approved (8-3)"

    def test_overflow_with_natural_break_trimmed_at_break(self):
        text = (
            "Council debated the proposal at length. "
            "Residents raised concerns about parking and traffic on the side streets."
        )
        result = _trim_to_chip(text)
        assert len(result) <= MAX_SUMMARY_CHARS
        assert not result.endswith("…")
        assert result == "Council debated the proposal at length"

    def test_overflow_without_natural_break_dropped(self):
        text = "x" * (MAX_SUMMARY_CHARS + 20)
        assert _trim_to_chip(text) == ""

    def test_strips_filler_leads(self):
        assert _trim_to_chip("I think the budget is fine") == "the budget is fine"
        assert _trim_to_chip("Um, we need to vote") == "we need to vote"
        assert _trim_to_chip("Yeah, the city will also act") == "the city will also act"
        assert _trim_to_chip("Yep, staff confirmed") == "staff confirmed"

    def test_strips_html_entities(self):
        assert "&amp;" not in _trim_to_chip("Parks &amp; Recreation funded")


# ── Sentence splitting / slicing ────────────────────────────────────────────


class TestSliceTranscript:
    def test_keeps_overlapping_segments(self):
        segments = [_seg(0, "before"), _seg(100_000, "inside"), _seg(500_000, "after")]
        item = {"time_start_ms": 90_000, "time_end_ms": 200_000}
        sliced = _slice_transcript(segments, item)
        assert len(sliced) == 1
        assert sliced[0]["text"] == "inside"

    def test_no_timestamps_returns_empty(self):
        assert _slice_transcript([_seg(0, "x")], {"time_start_ms": None, "time_end_ms": None}) == []


class TestSplitSentences:
    def test_basic_split(self):
        text = "This is sentence one. Here is another one! And a third?"
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_drops_very_short(self):
        assert _split_sentences("Ok. This sentence is long enough to keep.") == [
            "This sentence is long enough to keep."
        ]

    def test_empty(self):
        assert _split_sentences("") == []


# ── Deterministic extractors ────────────────────────────────────────────────


class TestOutcome:
    def test_carried(self):
        out = _extract_outcome({"vote_result": "CARRIED (8 to 3)", "recommendation": "x"})
        assert out == [{"category": "Outcome", "text": "Approved (8-3)"}]

    def test_no_vote(self):
        assert _extract_outcome({"vote_result": "", "recommendation": ""}) == []


class TestVoteBreakdown:
    def test_from_vote_detail(self):
        item = {"vote_detail": "In Favour: (5) Cllr A, B Against: (2) Cllr C, D"}
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "5 for, 2 against"}]

    def test_from_vote_result_fallback(self):
        item = {"vote_detail": "", "vote_result": "CARRIED (8 to 3)"}
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "8 for, 3 against"}]

    def test_no_vote(self):
        assert _extract_vote_breakdown({"vote_detail": "", "vote_result": ""}) == []


class TestAmendment:
    def test_detects_amendment_in_motion(self):
        item = {"motion_text": "That the motion be amended to include parks."}
        out = _extract_amendment(item)
        assert len(out) == 1
        assert out[0]["category"] == "Amendment Made"

    def test_bare_amended_word_dropped(self):
        item = {"motion_text": "", "vote_result": "Carried as amended.", "recommendation": ""}
        assert _extract_amendment(item) == []

    def test_no_amendment(self):
        item = {"motion_text": "That the report be received."}
        assert _extract_amendment(item) == []


class TestCostFunding:
    def test_single_amount(self):
        item = {"title": "", "recommendation": "Approve $2,500,000 for cycling", "content": ""}
        out = _extract_cost_funding(item, "")
        assert len(out) == 1
        assert "$2.5M" in out[0]["text"]

    def test_transcript_mention(self):
        item = {"title": "Report", "recommendation": "", "content": ""}
        out = _extract_cost_funding(item, "We allocated $750,000 for snow removal.")
        assert any("$750K" in o["text"] for o in out)


class TestDeclaredConflict:
    def test_match(self):
        out = _extract_declared_conflict("Councillor Smith declared a conflict of interest on this item.")
        assert out and out[0]["category"] == "Declared Conflict"

    def test_no_match(self):
        assert _extract_declared_conflict("We discussed the budget") == []


class TestDelegation:
    def test_director_presented(self):
        text = "Director Magus presented the report and responded to questions."
        out = _extract_delegation(text)
        assert out and out[0]["category"] == "Delegation"

    def test_no_delegation(self):
        assert _extract_delegation("No one spoke to this item.") == []


class TestNextStep:
    def test_report_back(self):
        text = "Administration will report back at the next meeting."
        out = _extract_next_step(text)
        assert out and out[0]["category"] == "Next Step"

    def test_by_year(self):
        out = _extract_next_step("This must be completed by 2027.")
        assert out and "2027" in out[0]["text"]


class TestRelatedDeferred:
    def test_deferred_from(self):
        out = _extract_related_deferred({"section_number": "9.2.1"}, "This was previously deferred from the March meeting.")
        cats = [o["category"] for o in out]
        assert "Deferred From" in cats

    def test_related_item_excludes_self(self):
        out = _extract_related_deferred({"section_number": "9.2.1"}, "See item 9.2.1 and item 10.3.2 for context.")
        cats = [o["category"] for o in out]
        assert "Related Item" in cats
        assert all("10.3.2" in o["text"] or o["category"] != "Related Item" for o in out)


class TestProceduralNote:
    def test_procedural_title(self):
        out = _extract_procedural_note({"title": "Call to Order"})
        assert out and out[0]["category"] == "Procedural Note"

    def test_non_procedural(self):
        assert _extract_procedural_note({"title": "Rezoning Application"}) == []


class TestDataCited:
    def test_percent(self):
        out = _extract_data_cited("Cycling trips increased by 15% last year.")
        assert out and out[0]["category"] == "Data Cited"

    def test_units(self):
        out = _extract_data_cited("The pilot reached 2,400 residents over three months.")
        assert out and "residents" in out[0]["text"]

    def test_no_number(self):
        assert _extract_data_cited("Many people attended the meeting.") == []


# ── LLM pass with stub Gemini extractor ────────────────────────────────────


import json


def _stub_extractor(response: list[dict], captured: dict | None = None):
    """Build a GeminiExtractor whose generate() returns *response* as JSON.

    Any chip in ``response`` without an explicit ``usefulness`` field is
    treated as ``"high"`` for convenience.  If ``captured`` is provided, the
    extractor records the allowed_cats and prompt passed to generate().
    """
    filled = [
        {"usefulness": "high", **r} if "usefulness" not in r else r
        for r in response
    ]

    def _generate(prompt, allowed_cats):
        if captured is not None:
            captured["allowed_cats"] = list(allowed_cats)
            captured["prompt"] = prompt
        return json.dumps(filled)
    return GeminiExtractor(api_key=None, generate=_generate)


class TestExtractItemSummariesSemantic:
    def test_stub_chips_merged(self):
        item = {
            "item_id": 1,
            "title": "Cycling Network Update",
            "recommendation": "That the report be received.",
            "motion_text": "",
            "vote_result": "",
            "vote_detail": "",
            "content": "",
            "section_number": "9.2.1",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        segments = [_seg(0, "Cycling commuting reduces city emissions significantly.")]
        stub = _stub_extractor([
            {"category": "Environmental Impact", "text": "Cuts emissions"},
        ])
        out = extract_item_summaries(item, segments, gemini_extractor=stub)
        cats = [o["category"] for o in out]
        assert "Environmental Impact" in cats

    def test_deterministic_category_not_requested_from_gemini(self):
        """Outcome is deterministic; Gemini's allowed_cats must not include it."""
        captured: dict = {}
        item = {
            "item_id": 2,
            "title": "Something",
            "recommendation": "That it be approved.",
            "motion_text": "",
            "vote_result": "CARRIED UNANIMOUSLY",
            "vote_detail": "",
            "content": "",
            "section_number": "1.",
            "time_start_ms": 0,
            "time_end_ms": 300_000,
        }
        segments = [_seg(0, "Plenty of text to reach the min length threshold.")]
        stub = _stub_extractor([], captured=captured)
        extract_item_summaries(item, segments, gemini_extractor=stub)
        # Outcome is always deterministic → must be excluded.
        assert "Outcome" not in captured["allowed_cats"]
        # Only the 12 semantic categories are exposed to Gemini.
        assert set(captured["allowed_cats"]).issubset(set(SEMANTIC_CATEGORIES))


# ── Ordering / eligibility ──────────────────────────────────────────────────


class TestSortOrder:
    def test_results_sorted_by_canonical_order(self):
        item = {
            "item_id": 3,
            "title": "Cycling Project",
            "recommendation": "Approve $2,000,000 for cycling paths.",
            "motion_text": "",
            "vote_result": "CARRIED (8 to 3)",
            "vote_detail": "",
            "content": "",
            "section_number": "9.",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        out = extract_item_summaries(item, [])
        order_idx = [CATEGORIES.index(o["category"]) for o in out]
        assert order_idx == sorted(order_idx)


class TestIsEligible:
    def test_consent_skipped(self):
        item = {"timestamp_inherited": True, "title": "Sub",
                "time_start_ms": 0, "time_end_ms": 100_000}
        assert is_eligible_for_summary(item) is False

    def test_procedural_skipped(self):
        item = {"title": "Call to Order", "time_start_ms": 0, "time_end_ms": 120_000}
        assert is_eligible_for_summary(item) is False

    def test_brief_skipped(self):
        item = {"title": "Normal item", "time_start_ms": 0, "time_end_ms": 30_000}
        assert is_eligible_for_summary(item) is False

    def test_substantive_eligible(self):
        item = {"title": "Rezoning Application",
                "time_start_ms": 0, "time_end_ms": 600_000}
        assert is_eligible_for_summary(item) is True

    def test_recess_skipped(self):
        item = {"title": "Recess", "is_recess": True,
                "time_start_ms": 0, "time_end_ms": 600_000}
        assert is_eligible_for_summary(item) is False


# ── Next Step conditional filter ───────────────────────────────────────────


class TestNextStepConditional:
    def test_if_clause_skipped(self):
        text = "if Council wanted to see a report back on this item. Administration will report back next year."
        out = _extract_next_step(text)
        assert out and "next year" in out[0]["text"]

    def test_if_mid_sentence_skipped(self):
        text = "I wonder if Council wanted to see a report back on this."
        out = _extract_next_step(text)
        assert out == []

    def test_previously_skipped(self):
        text = "We previously landed the world juniors and they want to come back next year."
        out = _extract_next_step(text)
        assert out == []

    def test_regular_next_step_kept(self):
        text = "Administration will report back at the next meeting."
        out = _extract_next_step(text)
        assert out and out[0]["category"] == "Next Step"

    def test_keyword_in_chip_text(self):
        text = "Some long preamble about various topics discussed at length. Staff committed to report back by Q2."
        out = _extract_next_step(text)
        assert out and "report back" in out[0]["text"]


# ── Unanimous vote suppresses Dissenting View ──────────────────────────────


class TestUnanimousSuppression:
    def test_unanimous_text_suppresses_dissent(self):
        captured: dict = {}
        item = {
            "item_id": 10,
            "title": "Policy Update",
            "recommendation": "",
            "motion_text": "",
            "vote_result": "CARRIED UNANIMOUSLY",
            "vote_detail": "",
            "content": "",
            "section_number": "5.",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        segments = [_seg(0, "Plenty of discussion happened across the room here.")]
        stub = _stub_extractor([], captured=captured)
        extract_item_summaries(item, segments, gemini_extractor=stub)
        assert "Dissenting View" not in captured["allowed_cats"]

    def test_zero_against_suppresses_dissent(self):
        captured: dict = {}
        item = {
            "item_id": 11,
            "title": "Policy Update",
            "recommendation": "",
            "motion_text": "",
            "vote_result": "CARRIED (10 to 0)",
            "vote_detail": "",
            "content": "",
            "section_number": "5.",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        segments = [_seg(0, "Plenty of discussion happened across the room here.")]
        stub = _stub_extractor([], captured=captured)
        extract_item_summaries(item, segments, gemini_extractor=stub)
        assert "Dissenting View" not in captured["allowed_cats"]

    def test_zero_for_defeat_suppresses_dissent(self):
        captured: dict = {}
        item = {
            "item_id": 12,
            "title": "Policy Update",
            "recommendation": "",
            "motion_text": "",
            "vote_result": "DEFEATED (0 to 9)",
            "vote_detail": "",
            "content": "",
            "section_number": "5.",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        segments = [_seg(0, "Plenty of discussion happened across the room here.")]
        stub = _stub_extractor([], captured=captured)
        extract_item_summaries(item, segments, gemini_extractor=stub)
        assert "Dissenting View" not in captured["allowed_cats"]


# ── Chip sanitization ──────────────────────────────────────────────────────


class TestSanitizeChips:
    def test_filters_invalid_category(self):
        out = _sanitize_chips(
            [{"category": "Not Real", "text": "hello", "usefulness": "high"}],
            allowed_cats=["Promise Made"],
        )
        assert out == []

    def test_drops_unnaturally_long_text(self):
        long = "x" * (MAX_SUMMARY_CHARS + 20)
        out = _sanitize_chips(
            [{"category": "Promise Made", "text": long, "usefulness": "high"}],
            allowed_cats=["Promise Made"],
        )
        assert out == []

    def test_dedupes_same_text(self):
        out = _sanitize_chips(
            [
                {"category": "Promise Made", "text": "Commit to this work", "usefulness": "high"},
                {"category": "Equity Impact", "text": "Commit to this work", "usefulness": "high"},
            ],
            allowed_cats=["Promise Made", "Equity Impact"],
        )
        assert len(out) == 1

    def test_not_a_list(self):
        assert _sanitize_chips({"oops": 1}, allowed_cats=["Promise Made"]) == []

    def test_missing_fields(self):
        out = _sanitize_chips(
            [{"category": "Promise Made"}, {"text": "hello"}, {}],
            allowed_cats=["Promise Made"],
        )
        assert out == []

    def test_keeps_medium_usefulness(self):
        out = _sanitize_chips(
            [{"category": "Promise Made", "text": "Decent", "usefulness": "medium"}],
            allowed_cats=["Promise Made"],
        )
        assert len(out) == 1

    def test_drops_low_usefulness(self):
        out = _sanitize_chips(
            [{"category": "Promise Made", "text": "Vague filler", "usefulness": "low"}],
            allowed_cats=["Promise Made"],
        )
        assert out == []

    def test_drops_missing_usefulness(self):
        out = _sanitize_chips(
            [{"category": "Promise Made", "text": "Missing rating"}],
            allowed_cats=["Promise Made"],
        )
        assert out == []

    def test_keeps_high_usefulness(self):
        out = _sanitize_chips(
            [{"category": "Promise Made", "text": "Staff will publish Q2 plan", "usefulness": "high"}],
            allowed_cats=["Promise Made"],
        )
        assert len(out) == 1


# ── GeminiExtractor state ──────────────────────────────────────────────────


class TestGeminiExtractorState:
    def test_disabled_without_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert GeminiExtractor().enabled is False

    def test_enabled_with_generate_hook(self):
        ex = GeminiExtractor(api_key=None, generate=lambda p, c: "[]")
        assert ex.enabled is True

    def test_enabled_with_api_key(self):
        ex = GeminiExtractor(api_key="fake")
        assert ex.enabled is True

    def test_empty_transcript_returns_empty(self):
        ex = _stub_extractor([{"category": "Promise Made", "text": "hi"}])
        assert ex.extract({"title": "X"}, "   ", exclude=set()) == []


class TestNextStepSliceQuality:
    def test_no_midword_start(self):
        # The "r" of "report back" must not be cut off.
        text = "If Council wanted to see a report about this. Administration will report back by Q2 next year on the findings."
        out = _extract_next_step(text)
        assert out
        assert not out[0]["text"].startswith(("eport", "eturn", "ext ")), out[0]["text"]

    def test_previously_mid_sentence_skipped(self):
        text = "We previously landed the world juniors and Curling Canada, hey they want to come back next year."
        out = _extract_next_step(text)
        assert out == []


class TestMoneyMinimum:
    def test_bare_tiny_amount_skipped(self):
        item = {"title": "", "recommendation": "It costs $3 to do this. Also $4 million overall.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item, "")
        texts = [o["text"] for o in out]
        assert not any("$3" in t and "million" not in t.lower() for t in texts)

    def test_hundreds_with_purpose_kept(self):
        item = {"title": "", "recommendation": "Set aside $500 for permit admin fees.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item, "")
        assert out and any("500" in o["text"] for o in out)

    def test_bare_amount_without_purpose_dropped(self):
        item = {"title": "", "recommendation": "The budget is $500,000.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item, "")
        assert out == []

    def test_suffix_keeps_small_value(self):
        item = {"title": "", "recommendation": "Allocate $2 million for snow removal.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item, "")
        assert out and any("2M" in o["text"] or "million" in o["text"].lower() for o in out)


class TestRelatedItemSliceQuality:
    def test_no_midword_start(self):
        text = "I'd like to recuse myself from item 10.1.1 for reasons stated earlier."
        item = {"section_number": "9.2"}
        out = _extract_related_deferred(item, text)
        rel = [o for o in out if o["category"] == "Related Item"]
        assert rel
        assert not rel[0]["text"].startswith(("'d", "d ")), rel[0]["text"]


