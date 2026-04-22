"""Tests for app.item_categorizer — the chip-summary extractor."""

import pytest

from app.item_categorizer import (
    CATEGORIES,
    CATEGORY_GROUP,
    MAX_SUMMARY_CHARS,
    Encoder,
    _extract_amendment,
    _extract_cost_funding,
    _extract_data_cited,
    _extract_declared_conflict,
    _extract_delegation,
    _extract_next_step,
    _extract_outcome,
    _extract_procedural_note,
    _extract_related_deferred,
    _extract_semantic,
    _extract_vote_breakdown,
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

    def test_long_truncated_at_word(self):
        text = "This is a long sentence that should be trimmed to sixty characters"
        result = _trim_to_chip(text)
        assert len(result) <= MAX_SUMMARY_CHARS
        assert result.endswith("…")
        assert " " in result  # word boundary preserved

    def test_strips_filler_leads(self):
        assert _trim_to_chip("I think the budget is fine") == "the budget is fine"
        assert _trim_to_chip("Um, we need to vote") == "we need to vote"

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


# ── Semantic pass with stub encoder ─────────────────────────────────────────


class StubEncoder(Encoder):
    """Returns deterministic vectors keyed on text content.

    We rig the similarities so the sentence containing ``CYCLING`` scores
    above the 0.50 threshold against the ``Environmental Impact`` query and
    nothing else does.
    """

    def encode(self, texts):
        out = []
        for t in texts:
            lower = t.lower()
            if "environmental" in lower or "emissions" in lower or "ecological" in lower:
                out.append([1.0, 0.0, 0.0])  # environmental query
            elif "cycling" in lower:
                out.append([0.9, 0.1, 0.0])  # good match for environmental
            elif "unrelated" in lower:
                out.append([0.0, 1.0, 0.0])  # orthogonal
            else:
                out.append([0.0, 0.2, 0.9])  # below threshold
        return out


class TestExtractItemSummariesSemantic:
    def test_semantic_pass_with_stub(self):
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
        segments = [
            _seg(0, "Cycling commuting reduces city emissions significantly over time."),
            _seg(60_000, "Some unrelated discussion happened here at length."),
        ]
        out = extract_item_summaries(item, segments, encoder=StubEncoder())
        cats = [o["category"] for o in out]
        assert "Environmental Impact" in cats

    def test_determinstic_excludes_same_semantic_category(self):
        """If Outcome is extracted deterministically, semantic pass must not re-emit it."""
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
        segments = [_seg(0, "Unrelated content here for now.")]
        out = extract_item_summaries(item, segments, encoder=StubEncoder())
        # Outcome should appear exactly once.
        assert sum(1 for o in out if o["category"] == "Outcome") == 1


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
        out = extract_item_summaries(item, [], encoder=StubEncoder())
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
        segments = [_seg(0, "Cycling commuting reduces city emissions significantly over time.")]
        out = extract_item_summaries(item, segments, encoder=StubEncoder())
        cats = [o["category"] for o in out]
        assert "Dissenting View" not in cats

    def test_zero_against_suppresses_dissent(self):
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
        segments = [_seg(0, "Cycling commuting reduces city emissions significantly over time.")]
        out = extract_item_summaries(item, segments, encoder=StubEncoder())
        cats = [o["category"] for o in out]
        assert "Dissenting View" not in cats


# ── Sentence deduplication in semantic pass ────────────────────────────────


class DedupeStubEncoder(Encoder):
    """All queries and all sentences get the same high-similarity vector,
    forcing the same sentence to be the 'best' for every category.
    Deduplication should prevent it from appearing more than once.
    """

    def encode(self, texts):
        return [[1.0, 0.0, 0.0]] * len(texts)


class TestSemanticDedup:
    def test_same_sentence_not_reused(self):
        sentences = ["This single sentence is long enough for a chip summary surely."]
        out = _extract_semantic(sentences, DedupeStubEncoder(), exclude=set())
        assert len(out) == 1

    def test_no_duplicate_texts_in_output(self):
        sentences = [
            "This single sentence is long enough for a chip summary surely.",
            "Another decent sentence that also has enough length here.",
        ]
        out = _extract_semantic(sentences, DedupeStubEncoder(), exclude=set())
        texts = [o["text"] for o in out]
        assert len(texts) == len(set(texts))


# ── Quality gate on semantic results ───────────────────────────────────────


class TestSemanticQualityGate:
    def test_trailing_junk_filtered(self):
        chip = _trim_to_chip(
            "They reduce flood risk, moderate urban heat and support by additional means"
        )
        from app.item_categorizer import _TRAILING_JUNK_RE
        assert _TRAILING_JUNK_RE.search(chip)


class TestSemanticDisqualifiers:
    def test_clarify_disqualifies_unanswered_question(self):
        from app.item_categorizer import _SEMANTIC_DISQUALIFIERS
        pats = _SEMANTIC_DISQUALIFIERS["Unanswered Question"]
        text = "Just to clarify your question, Councillor Park."
        assert any(p.search(text) for p in pats)

    def test_clarify_disqualifies_staff_vs_council(self):
        from app.item_categorizer import _SEMANTIC_DISQUALIFIERS
        pats = _SEMANTIC_DISQUALIFIERS["Staff vs. Council"]
        text = "I want to clarify for the public watching, what city council does."
        assert any(p.search(text) for p in pats)

    def test_we_want_to_disqualifies_env_impact(self):
        from app.item_categorizer import _SEMANTIC_DISQUALIFIERS
        pats = _SEMANTIC_DISQUALIFIERS["Environmental Impact"]
        text = "We want to look at sustainability."
        assert any(p.search(text) for p in pats)

    def test_normal_sentence_not_disqualified(self):
        from app.item_categorizer import _SEMANTIC_DISQUALIFIERS
        pats = _SEMANTIC_DISQUALIFIERS.get("Unanswered Question", [])
        text = "What happens to the funding if the project is delayed?"
        assert not any(p.search(text) for p in pats)
