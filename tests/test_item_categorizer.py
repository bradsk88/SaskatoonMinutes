"""Tests for app.item_categorizer — the chip-summary extractor."""

import pytest

from app.item_categorizer import (
    CATEGORIES,
    CATEGORY_GROUP,
    MAX_DESCRIPTION_BULLETS,
    MAX_DESCRIPTION_CHARS,
    MAX_DESCRIPTION_WORDS,
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_WORDS,
    SEMANTIC_CATEGORIES,
    SEMANTIC_DEFINITIONS,
    USEFULNESS_LEVELS,
    GeminiExtractor,
    _build_prompt,
    _summary_schema,
    _extract_amendment,
    _extract_cost_funding,
    _extract_data_cited,
    _extract_declared_conflict,
    _extract_delegation,
    _extract_next_step,
    _extract_outcome,
    _extract_procedural_note,
    _extract_related_deferred,
    _is_unanimous_tally,
    _extract_vote_breakdown,
    _sanitize_chips,
    _slice_transcript,
    _transcript_chip,
    extract_item_summaries,
    is_eligible_for_summary,
    item_transcript_text,
)


def _seg(start_ms: int, text: str, length: int = 5000) -> dict:
    return {"start_ms": start_ms, "end_ms": start_ms + length, "text": text}


def _summary_item(item_id: int, title: str) -> dict:
    """A minimal agenda item that clears is_eligible_for_summary."""
    return {
        "item_id": item_id,
        "title": title,
        "recommendation": "",
        "motion_text": "",
        "vote_result": "",
        "vote_detail": "",
        "content": "",
        "section_number": "8.",
        "time_start_ms": 0,
        "time_end_ms": 600_000,
    }


# ── Category metadata ───────────────────────────────────────────────────────


class TestCategoryMetadata:
    def test_every_category_has_a_group(self):
        missing = [c for c in CATEGORIES if c not in CATEGORY_GROUP]
        assert missing == []

    def test_groups_are_from_fixed_palette(self):
        allowed = {"decision", "money", "context", "voices", "impact", "future"}
        assert set(CATEGORY_GROUP.values()) <= allowed

    def test_count_is_22(self):
        """In Plain Terms was retired; the Description replaced it."""
        assert len(CATEGORIES) == 22

    def test_in_plain_terms_is_retired(self):
        assert "In Plain Terms" not in CATEGORIES
        assert "In Plain Terms" not in SEMANTIC_CATEGORIES


# ── Trimming ────────────────────────────────────────────────────────────────


class TestTranscriptChip:
    """The categorizer's transcript-chip helper composes
    clean_entities + strip_filler_leads + trim_to_chip."""

    def test_short_passthrough(self):
        assert _transcript_chip("Approved (8-3)") == "Approved (8-3)"

    def test_overflow_with_natural_break_trimmed_at_break(self):
        text = (
            "Council debated the proposal at length. "
            "Residents raised concerns about parking and traffic on the side streets."
        )
        result = _transcript_chip(text)
        assert len(result) <= MAX_SUMMARY_CHARS
        assert not result.endswith("…")
        assert result == "Council debated the proposal at length"

    def test_overflow_without_natural_break_dropped(self):
        text = "x" * (MAX_SUMMARY_CHARS + 20)
        assert _transcript_chip(text) == ""

    def test_strips_filler_leads(self):
        assert _transcript_chip("I think the budget is fine") == "the budget is fine"
        assert _transcript_chip("Um, we need to vote") == "we need to vote"
        assert _transcript_chip("Yeah, the city will also act") == "the city will also act"
        assert _transcript_chip("Yep, staff confirmed") == "staff confirmed"

    def test_strips_html_entities(self):
        assert "&amp;" not in _transcript_chip("Parks &amp; Recreation funded")


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


# ── Deterministic extractors ────────────────────────────────────────────────


class TestOutcome:
    def test_carried_no_title(self):
        out = _extract_outcome({"vote_result": "CARRIED (8 to 3)", "recommendation": "x"})
        assert out == [{"category": "Outcome", "text": "Approved (8-3)"}]

    def test_title_is_not_repeated_in_the_outcome(self):
        """The Description carries the context; repeating the title here
        made the Outcome chip a title echo under a sentence that said it
        better.  It was 100% of the remaining echo after U3."""
        out = _extract_outcome({
            "vote_result": "CARRIED (8 to 3)",
            "recommendation": "x",
            "title": "Ruth Street Active Transportation Plan [CC2026-0303]",
        })
        assert out == [{"category": "Outcome", "text": "Approved (8-3)"}]

    def test_unanimous_carries_no_title_either(self):
        out = _extract_outcome({
            "vote_result": "CARRIED UNANIMOUSLY",
            "recommendation": "That the report be received.",
            "title": "Bylaw No. 9876 - Downtown Zoning Amendment [CC2026-0100]",
        })
        assert "Zoning" not in out[0]["text"]
        assert "[CC2026-0100]" not in out[0]["text"]

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

    def test_unanimous_vote_has_no_against_section(self):
        """eSCRIBE omits sides with no members, so a unanimous carry has no
        "Against:" at all.  Requiring both sides dropped the chip entirely."""
        item = {
            "vote_detail": (
                "In Favour: (5) Councillor Davies, Councillor Loewen, "
                "Councillor Gough, Councillor Block, and Councillor Gersher "
                "Absent: (1) Mayor C. Clark CARRIED UNANIMOUSLY"
            ),
            "vote_result": "CARRIED UNANIMOUSLY",
        }
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "5 for, 0 against"}]

    def test_absent_members_are_not_counted_as_against(self):
        item = {"vote_detail": "In Favour: (9) A, B Absent: (2) C, D"}
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "9 for, 0 against"}]

    def test_defeated_vote_with_no_in_favour_section(self):
        item = {"vote_detail": "Against: (7) A, B, C"}
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "0 for, 7 against"}]

    def test_vote_detail_wins_over_vote_result(self):
        item = {
            "vote_detail": "In Favour: (5) A Against: (2) B",
            "vote_result": "CARRIED (8 to 3)",
        }
        out = _extract_vote_breakdown(item)
        assert out == [{"category": "Vote Breakdown", "text": "5 for, 2 against"}]

    def test_zero_zero_tally_is_not_a_chip(self):
        assert _extract_vote_breakdown({"vote_detail": "In Favour: (0)"}) == []


class TestUnanimousDetection:
    """Unanimity suppresses the Dissenting View category — there is no dissent."""

    def test_unanimous_without_an_against_section(self):
        assert _is_unanimous_tally(
            {"vote_detail": "In Favour: (5) A, B Absent: (1) C"}
        )

    def test_split_vote_is_not_unanimous(self):
        assert not _is_unanimous_tally(
            {"vote_detail": "In Favour: (5) A Against: (2) B"}
        )

    def test_split_vote_from_vote_result_is_not_unanimous(self):
        assert not _is_unanimous_tally({"vote_result": "CARRIED (8 to 3)"})

    def test_no_tally_at_all_is_not_unanimous(self):
        assert not _is_unanimous_tally({"vote_detail": "", "vote_result": ""})


class TestAmendment:
    def test_detects_amendment_in_motion(self):
        item = {"motion_text": "That the motion be amended to include parks."}
        out = _extract_amendment(item)
        assert len(out) == 1
        assert out[0]["category"] == "Amendment Made"

    def test_carried_as_amended_is_an_amendment(self):
        """"as amended" is eSCRIBE recording that the motion was changed."""
        item = {"motion_text": "", "vote_result": "Carried as amended.", "recommendation": ""}
        out = _extract_amendment(item)
        assert out and out[0]["category"] == "Amendment Made"

    def test_no_amendment(self):
        item = {"motion_text": "That the report be received."}
        assert _extract_amendment(item) == []

    def test_the_word_amended_describing_a_future_policy_is_not_an_amendment(self):
        """The real false positive from the 2022-03 committee meeting.

        "until such time as a new or amended Naming of City Property and
        Development Areas Policy ... is developed" describes a policy
        nobody amended, but produced an Amendment Made chip.
        """
        item = {
            "motion_text": "", "vote_result": "CARRIED UNANIMOUSLY",
            "recommendation": (
                "That a temporary pause of the Civic Naming Program, with "
                "respect to receiving new submissions, until such time as a "
                "new or amended Naming of City Property and Development "
                "Areas Policy, or related policy is developed."
            ),
        }
        assert _extract_amendment(item) == []

    def test_an_amendment_bylaw_title_is_not_an_amendment(self):
        item = {
            "motion_text": "That Council consider The Capital Reserve "
                           "Amendment Bylaw, 2026.",
            "vote_result": "CARRIED UNANIMOUSLY", "recommendation": "",
        }
        assert _extract_amendment(item) == []


class TestCostFunding:
    def test_single_amount(self):
        item = {"title": "", "recommendation": "Approve $2,500,000 for cycling", "content": ""}
        out = _extract_cost_funding(item)
        assert len(out) == 1
        assert "$2.5M" in out[0]["text"]

    def test_transcript_money_is_not_a_hard_chip(self):
        """Cost & Funding reads official text only.

        Agenda-item boundaries in the transcript come from eSCRIBE
        bookmarks that lag what was said, so a transcript-derived money
        chip lands on the wrong item.  A hard chip has to cite a source
        that can be checked.
        """
        item = {"title": "Report", "recommendation": "", "content": ""}
        assert _extract_cost_funding(item) == []

    def test_purpose_preposition_is_preserved(self):
        item = {
            "title": "", "content": "",
            "recommendation": "Approve an additional $187,000 to complete the project.",
        }
        out = _extract_cost_funding(item)
        assert out and out[0]["text"] == "$187K to complete the project"

    def test_money_from_content_is_found(self):
        item = {
            "title": "", "recommendation": "",
            "content": "A loan of $3,800,000 for the Lorne Avenue purchase.",
        }
        out = _extract_cost_funding(item)
        assert any("$3.8M" in o["text"] for o in out)


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

    def test_recusal_not_treated_as_related(self):
        text = "I'd like to recuse myself from item 10.1.1 for reasons stated earlier."
        out = _extract_related_deferred({"section_number": "9.2"}, text)
        cats = [o["category"] for o in out]
        assert "Related Item" not in cats


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


STUB_DESCRIPTION = ["Council approved a specific, concrete thing this item does."]


def _stub_extractor(
    response: list[dict],
    captured: dict | None = None,
    description: str | list[str] | None = STUB_DESCRIPTION,
):
    """Build a GeminiExtractor whose generate() returns an ItemSummary as JSON.

    *response* is the chip list.  Any chip without an explicit
    ``usefulness`` field is treated as ``"high"`` for convenience.
    ``description`` becomes the required description field -- a list of
    bullets, or a bare string to stand in for the paragraph shape the
    archive still holds.  Pass ``None`` to simulate a model that failed
    to supply one.

    If ``captured`` is provided, the extractor records the allowed_cats
    and the prompt it was called with.
    """
    filled = [
        {"usefulness": "high", **r} if "usefulness" not in r else r
        for r in response
    ]

    def _generate(prompt, allowed_cats):
        if captured is not None:
            captured["allowed_cats"] = list(allowed_cats)
            captured["prompt"] = prompt
        return json.dumps({"description": description or [], "chips": filled})

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
        cats = [o["category"] for o in out["chips"]]
        assert "Environmental Impact" in cats
        assert out["description"] == STUB_DESCRIPTION

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
        # Only the 11 semantic categories are exposed to Gemini.
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
        order_idx = [CATEGORIES.index(o["category"]) for o in out["chips"]]
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

    def test_keyword_trimmed_away_drops_chip(self):
        text = (
            "the city will also have, though, any lead on the cost "
            "estimates and everything, plus report back to council "
            "about the current status of the full redesign plans"
        )
        out = _extract_next_step(text)
        if out:
            assert "report back" in out[0]["text"]

    def test_question_dropped(self):
        text = "For example, could it possibly in your mind be done by the end of 2026?"
        out = _extract_next_step(text)
        assert out == []

    def test_rambling_speech_dropped(self):
        text = "this is, again, I'll bring it back to the, the diligence and pre-opening stages."
        out = _extract_next_step(text)
        assert out == []

    def test_clean_two_comma_chip_kept(self):
        text = "Administration will report back, with a full update, by Q2."
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
        assert ex.extract({"title": "X"}, "   ", exclude=set()) == (None, [])


class TestTranscriptReachesThePrompt:
    def test_the_prompt_sees_the_raw_slice(self):
        """No cleanup pass stands between the transcript and the chip call.

        The A/B that deleted cleanup scored exactly this text (ADR
        `0005`), so anything rewriting it here would invalidate that
        result silently.
        """
        captured: dict = {}
        item = {
            "item_id": 99,
            "title": "Funding Decision",
            "recommendation": "",
            "motion_text": "",
            "vote_result": "",
            "vote_detail": "",
            "content": "",
            "section_number": "1.",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        raw = "um yeah so we, you know, talked about funding stuff."
        stub = _stub_extractor([], captured=captured)
        extract_item_summaries(item, [_seg(0, raw)], gemini_extractor=stub)
        assert raw in captured["prompt"]

    def test_segments_are_joined_with_one_space(self):
        item = {
            "item_id": 1, "title": "", "section_number": "1.",
            "time_start_ms": 0, "time_end_ms": 600_000,
        }
        segments = [_seg(0, "First part."), _seg(5000, "Second part.")]
        assert item_transcript_text(item, segments) == "First part. Second part."

    def test_an_item_with_no_slice_gets_empty_text(self):
        item = {"item_id": 1, "title": "", "timestamp_inherited": True}
        assert item_transcript_text(item, [_seg(0, "anything")]) == ""


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
        out = _extract_cost_funding(item)
        texts = [o["text"] for o in out]
        assert not any("$3" in t and "million" not in t.lower() for t in texts)

    def test_hundreds_with_purpose_kept(self):
        item = {"title": "", "recommendation": "Set aside $500 for permit admin fees.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item)
        assert out and any("500" in o["text"] for o in out)

    def test_bare_amount_without_purpose_dropped(self):
        item = {"title": "", "recommendation": "The budget is $500,000.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item)
        assert out == []

    def test_suffix_keeps_small_value(self):
        item = {"title": "", "recommendation": "Allocate $2 million for snow removal.", "content": "", "motion_text": ""}
        out = _extract_cost_funding(item)
        assert out and any("2M" in o["text"] or "million" in o["text"].lower() for o in out)


class TestRelatedItemSliceQuality:
    def test_no_midword_start(self):
        text = "She noted item 10.1.1 is closely related to the cycling plan."
        item = {"section_number": "9.2"}
        out = _extract_related_deferred(item, text)
        rel = [o for o in out if o["category"] == "Related Item"]
        assert rel
        assert not rel[0]["text"].startswith(("'d", "d ")), rel[0]["text"]


class TestGeminiPromptIncludesMetadata:
    def test_includes_recommendation(self):
        item = {
            "title": "Bus Route 42 Changes",
            "recommendation": "That the administration implement route changes.",
        }
        prompt = _build_prompt(item, "transcript text", ["Who's Affected"])
        assert "implement route changes" in prompt

    def test_includes_vote_result(self):
        item = {
            "title": "Zoning",
            "vote_result": "CARRIED (8 to 3)",
        }
        prompt = _build_prompt(item, "text", ["Who's Affected"])
        assert "CARRIED (8 to 3)" in prompt

    def test_works_without_transcript(self):
        item = {
            "title": "Budget Item",
            "recommendation": "Approve the capital budget of $5M.",
        }
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "capital budget" in prompt
        assert "Transcript" not in prompt

    def test_includes_content(self):
        item = {
            "title": "Policy Update",
            "content": "This policy addresses snow removal standards.",
        }
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "snow removal" in prompt


class TestGeminiRunsWithoutTranscript:
    def test_extract_with_metadata_only(self):
        captured: dict = {}
        item = {
            "item_id": 50,
            "title": "Capital Budget Allocation",
            "recommendation": "That Council approve $5M for road repair.",
            "vote_result": "CARRIED UNANIMOUSLY",
            "vote_detail": "",
            "content": "",
            "motion_text": "",
            "section_number": "7.1",
            "time_start_ms": 0,
            "time_end_ms": 600_000,
        }
        stub = _stub_extractor(
            [{"category": "Who's Affected", "text": "Drivers on arterial roads"}],
            captured=captured,
            description=["Puts $5M toward repaving arterial roads across the city."],
        )
        out = extract_item_summaries(item, [], gemini_extractor=stub)
        # No transcript at all, but the metadata alone still yields a summary.
        assert out["description"] == [
            "Puts $5M toward repaving arterial roads across the city."
        ]
        assert any(o["category"] == "Who's Affected" for o in out["chips"])


class TestDescriptionIsMandatoryNotFallback:
    """The Description replaced the "In Plain Terms" chip + metadata fallback.

    The old design let the model decline the category, then substituted a
    chip built from the item's title -- which is why 2,567 cached chips
    were title echoes.  There is deliberately no replacement fallback.
    """

    def test_description_comes_from_the_model(self):
        item = _summary_item(100, "Transit Route Changes")
        segments = [_seg(0, "Discussion of transit route 42.")]
        stub = _stub_extractor(
            [], description=["Reroutes bus 42 off Broadway and adds two stops."],
        )
        out = extract_item_summaries(item, segments, gemini_extractor=stub)
        assert out["description"] == [
            "Reroutes bus 42 off Broadway and adds two stops."
        ]

    def test_no_description_without_gemini(self):
        """Deterministic-only runs produce a Legacy ItemSummary, not filler."""
        item = _summary_item(99, "Downtown Event and Entertainment District Plan")
        item["vote_result"] = "CARRIED UNANIMOUSLY"
        segments = [_seg(0, "We talked about the entertainment district plan.")]
        out = extract_item_summaries(
            item, segments, gemini_extractor=GeminiExtractor(api_key=None),
        )
        assert out["description"] is None
        # Deterministic chips still work; only the description is absent.
        assert "Outcome" in [c["category"] for c in out["chips"]]

    def test_title_is_never_substituted_for_a_missing_description(self):
        item = _summary_item(101, "Downtown Event and Entertainment District Plan")
        segments = [_seg(0, "Some discussion.")]
        stub = _stub_extractor([], description=None)
        out = extract_item_summaries(item, segments, gemini_extractor=stub)
        assert out["description"] is None

    def test_blank_description_is_none_not_empty_string(self):
        item = _summary_item(102, "Transit Route Changes")
        stub = _stub_extractor([], description="   ")
        out = extract_item_summaries(
            item, [_seg(0, "Talk.")], gemini_extractor=stub,
        )
        assert out["description"] is None

    def test_description_has_html_entities_cleaned(self):
        item = _summary_item(103, "Parks Funding")
        stub = _stub_extractor(
            [], description=["Funds Parks &amp; Recreation upgrades citywide."],
        )
        out = extract_item_summaries(
            item, [_seg(0, "Talk.")], gemini_extractor=stub,
        )
        assert out["description"] == ["Funds Parks & Recreation upgrades citywide."]


class TestDescriptionBullets:
    """The Description is bullets: a card row is scanned, not read."""

    def test_a_paragraph_from_the_archive_loads_as_one_bullet(self):
        item = _summary_item(110, "Transit Route Changes")
        stub = _stub_extractor([], description="Reroutes bus 42 off Broadway.")
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert out["description"] == ["Reroutes bus 42 off Broadway."]

    def test_a_bullet_glyph_the_model_typed_is_stripped(self):
        """Asked for a list, the model still writes the dash into the
        string — which would render beside the marker the page draws."""
        item = _summary_item(111, "Transit Route Changes")
        stub = _stub_extractor(
            [], description=["- Reroutes bus 42", "• Adds two stops"],
        )
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert out["description"] == ["Reroutes bus 42", "Adds two stops"]

    def test_blank_bullets_are_dropped(self):
        item = _summary_item(112, "Transit Route Changes")
        stub = _stub_extractor([], description=["Reroutes bus 42", "   ", ""])
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert out["description"] == ["Reroutes bus 42"]

    def test_a_list_of_only_blanks_is_a_missing_description(self):
        item = _summary_item(113, "Transit Route Changes")
        stub = _stub_extractor([], description=["  ", ""])
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert out["description"] is None

    def test_more_bullets_than_the_ceiling_are_cut(self):
        """The card has a height budget; five bullets is padding."""
        item = _summary_item(114, "Transit Route Changes")
        stub = _stub_extractor([], description=[f"Fact {i}" for i in range(6)])
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert len(out["description"]) == MAX_DESCRIPTION_BULLETS

    def test_a_continuation_bullet_is_left_as_the_model_wrote_it(self):
        """Joining it to the bullet above was tried and reverted: a
        caveat welded onto an unrelated fact asserts something neither
        bullet said. The eval counts these instead."""
        item = _summary_item(115, "Garden and Garage Suites")
        bullets = [
            "Supports Housing Accelerator Fund conditions.",
            "This is a public hearing, not final approval",
        ]
        stub = _stub_extractor([], description=list(bullets))
        out = extract_item_summaries(item, [_seg(0, "Talk.")], gemini_extractor=stub)
        assert out["description"] == bullets

    def test_the_prompt_asks_for_bullets_driven_by_the_facts(self):
        prompt = _build_prompt(
            _summary_item(1, "Transit Bylaw"), "text", ["Who's Affected"],
        )
        assert "One bullet per DISTINCT fact" in prompt
        assert f"1 to {MAX_DESCRIPTION_BULLETS} short bullets" in prompt

    def test_the_prompt_forbids_a_bullet_that_continues_the_one_above(self):
        """One sentence chopped into four is longer than the paragraph
        it replaced and says less."""
        prompt = _build_prompt(
            _summary_item(1, "Transit Bylaw"), "text", ["Who's Affected"],
        )
        assert "Never split one fact across bullets" in prompt


class TestSummarySchema:
    def test_description_is_required(self):
        schema = _summary_schema(["Who's Affected"])
        assert "description" in schema["required"]

    def test_description_is_a_list_of_bullets(self):
        """A string here is what made the model write a paragraph."""
        schema = _summary_schema(["Who's Affected"])
        desc = schema["properties"]["description"]
        assert desc["type"] == "array"
        assert desc["items"]["type"] == "string"

    def test_chips_are_constrained_to_allowed_categories(self):
        schema = _summary_schema(["Who's Affected"])
        chip = schema["properties"]["chips"]["items"]
        assert chip["properties"]["category"]["enum"] == ["Who's Affected"]

    def test_prompt_forbids_restating_the_title(self):
        prompt = _build_prompt(
            _summary_item(1, "Transit Bylaw"), "text", ["Who's Affected"],
        )
        assert "Do NOT restate the agenda item's title" in prompt


# ── Prompt rules that keep regressing ──────────────────────────────


def _rules_prompt(cats=None):
    return _build_prompt(
        _summary_item(1, "Transit Bylaw"),
        "text",
        cats if cats is not None else SEMANTIC_CATEGORIES,
    )


class TestBudgetsAreStatedInWords:
    """A model cannot count characters, so it cannot obey a character budget.

    Seven of eleven fixture descriptions overran the 220-character bound.
    The budgets the model is *asked* for are in words; the character
    constants remain the unit the eval measures in.
    """

    def test_description_budget_is_words(self):
        prompt = _rules_prompt()
        assert f"{MAX_DESCRIPTION_WORDS} words" in prompt
        assert f"{MAX_DESCRIPTION_CHARS} characters" not in prompt

    def test_chip_budget_is_words(self):
        prompt = _rules_prompt()
        assert f"{MAX_SUMMARY_WORDS} words" in prompt
        assert f"{MAX_SUMMARY_CHARS} characters" not in prompt


class TestDescriptionOpeningBans:
    def test_bans_making_the_document_the_subject(self):
        """"The item approves funding..." slipped past a ban that listed
        only "The report ..." openings, and cost real specificity."""
        prompt = _rules_prompt()
        assert "The item approves" in prompt
        assert "The report highlights" in prompt
        assert "never make the agenda item" in prompt.lower()

    def test_still_bans_opening_with_process(self):
        assert "Council received the report as information" in _rules_prompt()


class TestProperNounSpelling:
    """We published a delegate's name as the ASR heard it.

    The minutes said "Kobussen"; the transcript said "Colbison"; the chip
    published "Colbison" with the correct spelling sitting in the same
    prompt.
    """

    def test_official_text_wins_over_the_transcript(self):
        prompt = _rules_prompt()
        assert "Spell proper nouns as the official recommendation" in prompt

    def test_a_transcript_only_garbled_name_is_dropped_not_guessed(self):
        assert "leave the name out rather than publish a guess" in _rules_prompt()


class TestUsefulnessIsGatedOnlyInCode:
    """The model rates; the code cuts.  Two gates made borderline chips flap."""

    def test_prompt_does_not_ask_the_model_to_withhold_low_chips(self):
        prompt = _rules_prompt()
        assert "Omit anything you would rate" not in prompt
        assert "skip the category rather than emit a weak chip" not in prompt

    def test_prompt_still_asks_for_a_rating(self):
        prompt = _rules_prompt()
        for level in USEFULNESS_LEVELS:
            assert f'"{level}"' in prompt

    def test_accuracy_gate_survives(self):
        """Only *usefulness* moved to code — the model still drops inventions."""
        assert "worse than no chip" in _rules_prompt()

    def test_code_still_drops_low_chips(self):
        chips = _sanitize_chips(
            [
                {"category": "Who's Affected", "text": "Riversdale residents",
                 "usefulness": "low"},
            ],
            ["Who's Affected"],
        )
        assert chips == []


class TestChipsDoNotRestateTheDescription:
    def test_prompt_requires_checking_the_chip_against_the_description(self):
        assert "if the description already states that fact, drop the chip" \
            in _rules_prompt()


class TestCategoryDefinitionsDisambiguate:
    """Overlapping definitions yielded the same fact under two labels."""

    def test_whos_affected_is_replaced_by_the_narrower_categories(self):
        """Worded as "use this only when..." it read as a deterrent.

        The first attempt lost the category on six of eleven items while
        only three moved to Equity Impact -- the tie-break has to hand
        the fact to a neighbour, not talk the model out of the fact.
        """
        definition = SEMANTIC_DEFINITIONS["Who's Affected"]
        assert "REPLACE this chip rather than suppress it" in definition
        assert "Equity Impact" in definition
        assert "Environmental Impact" in definition
        assert "Emit it whenever a concrete group is identifiable" in definition

    def test_public_sentiment_and_debate_highlight_split_on_the_speaker(self):
        assert "use Public Sentiment" in SEMANTIC_DEFINITIONS["Debate Highlight"]
        assert "Debate Highlight" in SEMANTIC_DEFINITIONS["Public Sentiment"]

    def test_the_tie_breaks_reach_the_prompt(self):
        assert "REPLACE this chip rather than suppress it" in _rules_prompt()


class TestMoneyPurposeParsing:
    """Official agenda text is full of en dashes and bracketed file numbers."""

    def test_en_dash_terminates_the_purpose(self):
        item = {
            "title": "", "content": "",
            "recommendation": (
                "That City Council approve an increase of $187,000 to Shaw "
                "Centre – Score Clock and Timing Equipment Capital Project."
            ),
        }
        out = _extract_cost_funding(item)
        assert out == [{"category": "Cost & Funding", "text": "$187K to Shaw Centre"}]

    def test_for_purposes_still_work(self):
        item = {
            "title": "", "content": "",
            "recommendation": "Approve $2,500,000 for cycling infrastructure.",
        }
        out = _extract_cost_funding(item)
        assert out[0]["text"] == "$2.5M for cycling infrastructure"

    def test_bare_amount_with_no_purpose_is_dropped(self):
        item = {"title": "", "content": "", "recommendation": "The cost is $500,000."}
        assert _extract_cost_funding(item) == []


class TestPromptInputHygiene:
    """The prompt calls the official text "clean and reliable" — so it must be."""

    def test_html_entities_are_decoded_before_the_model_sees_them(self):
        item = _summary_item(1, "Homelessness Action Plan")
        item["recommendation"] = (
            "That the Governance and Priorities Committee recommend to City "
            "Council&#58; That the plan be received."
        )
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "&#58;" not in prompt
        assert "recommend to City Council:" in prompt

    def test_truncation_is_marked_not_silent(self):
        """A silent mid-clause cut reads as though the motion ended there."""
        item = _summary_item(2, "Long Item")
        item["recommendation"] = "That Council approve " + ("x" * 3000)
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "[truncated]" in prompt

    def test_short_fields_are_not_marked(self):
        item = _summary_item(3, "Short Item")
        item["recommendation"] = "That Council approve the rezoning."
        assert "[truncated]" not in _build_prompt(item, "", ["Who's Affected"])

    def test_the_transcript_is_fenced_and_named(self):
        item = _summary_item(4, "Debated Item")
        prompt = _build_prompt(item, "some spoken words", ["Who's Affected"])
        assert "<<<TRANSCRIPT" in prompt
        assert "<<<END TRANSCRIPT>>>" in prompt
        assert "some spoken words" in prompt

    def test_traceability_rule_names_the_transcript_fences(self):
        """It used to say "the material above" — the transcript is below."""
        prompt = _build_prompt(_summary_item(5, "X"), "words", ["Who's Affected"])
        assert "material above" not in prompt
        assert "TRANSCRIPT fences" in prompt


class TestCommitteeAttributionGuard:
    """A committee recommends to Council; Council has not decided."""

    def _committee_item(self) -> dict:
        item = _summary_item(10, "Saskatoon Homelessness Action Plan 2026")
        item["recommendation"] = (
            "That the Governance and Priorities Committee recommend to City "
            "Council: That City Council reaffirm the City's leadership role."
        )
        item["vote_result"] = "CARRIED UNANIMOUSLY"
        return item

    def test_guard_fires_even_though_the_committee_vote_carried(self):
        """The old guard keyed off the outcome label, which read "Approved",
        so it never fired and the summary asserted Council had acted."""
        prompt = _build_prompt(self._committee_item(), "", ["Who's Affected"])
        assert "this is a COMMITTEE item" in prompt
        assert "City Council has NOT decided it" in prompt

    def test_guard_warns_about_copying_the_subjunctive_clause(self):
        prompt = _build_prompt(self._committee_item(), "", ["Who's Affected"])
        assert "ASKING Council to do" in prompt

    def test_a_real_council_item_gets_the_decision_line_instead(self):
        item = _summary_item(11, "Shaw Centre Score Clock")
        item["recommendation"] = "That City Council approve an increase of $187,000."
        item["vote_result"] = "CARRIED UNANIMOUSLY"
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "this is a COMMITTEE item" not in prompt
        assert "This body's decision: Approved." in prompt

    def test_the_decision_line_tells_the_model_not_to_restate_it(self):
        item = _summary_item(12, "Shaw Centre")
        item["recommendation"] = "That City Council approve the purchase."
        item["vote_result"] = "CARRIED UNANIMOUSLY"
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "do NOT restate it in the description" in prompt


class TestPublicHearingGuard:
    """First reading puts the bylaw before council; it decides nothing."""

    def _hearing_item(self) -> dict:
        item = _summary_item(20, "Proposed Rezoning - 1401 11th Street West")
        item["recommendation"] = "That City Council consider Bylaw No. 10169."
        item["vote_result"] = "CARRIED UNANIMOUSLY (10 to 0)"
        return item

    def test_the_outcome_chip_does_not_claim_approval(self):
        chips = _extract_outcome(self._hearing_item())
        assert chips == [
            {"category": "Outcome", "text": "First reading passed (10-0)"}
        ]

    def test_the_prompt_forbids_asserting_a_decision(self):
        """The model wrote "City Council denied a rezoning request" under an
        Outcome chip reading "Approved" — a summary contradicting itself."""
        prompt = _build_prompt(self._hearing_item(), "", ["Who's Affected"])
        assert "this is a PUBLIC HEARING item" in prompt
        assert "Council has NOT decided the application here" in prompt

    def test_the_hearing_guard_replaces_the_decision_line(self):
        prompt = _build_prompt(self._hearing_item(), "", ["Who's Affected"])
        assert "This body's decision:" not in prompt

    def test_an_ordinary_council_item_is_unaffected(self):
        item = _summary_item(21, "Shaw Centre Score Clock")
        item["recommendation"] = "That City Council approve an increase of $187,000."
        item["vote_result"] = "CARRIED UNANIMOUSLY"
        prompt = _build_prompt(item, "", ["Who's Affected"])
        assert "PUBLIC HEARING item" not in prompt


class TestOneChipPerCategory:
    def test_a_second_chip_in_the_same_category_is_dropped(self):
        parsed = [
            {"category": "Who's Affected", "text": "Residents of Nutana",
             "usefulness": "high"},
            {"category": "Who's Affected", "text": "Businesses on Broadway",
             "usefulness": "high"},
        ]
        out = _sanitize_chips(parsed, ["Who's Affected"])
        assert len(out) == 1
        assert out[0]["text"] == "Residents of Nutana"

    def test_different_categories_both_survive(self):
        parsed = [
            {"category": "Who's Affected", "text": "Residents of Nutana",
             "usefulness": "high"},
            {"category": "Equity Impact", "text": "Helps low-income riders",
             "usefulness": "high"},
        ]
        out = _sanitize_chips(parsed, ["Who's Affected", "Equity Impact"])
        assert len(out) == 2
