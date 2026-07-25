"""Tests for Consent Item coverage (U4).

A Consent Item is approved in the consent block, in one motion, with no
individual debate.  It has no transcript but substantial official text,
and it used to be rejected before the LLM ever saw it -- 18 of 73 items
on a typical council meeting.
"""

from app.agenda_items import (
    is_boilerplate_recommendation,
    is_consent_item,
    is_procedural,
    is_section_header,
)
from app.item_categorizer import (
    DISCUSSION_ONLY_CATEGORIES,
    GeminiExtractor,
    _build_prompt,
    _slice_transcript,
    extract_item_summaries,
    is_eligible_for_summary,
)


def consent(**extra) -> dict:
    """An item approved on consent: inherited timestamp, real recommendation."""
    base = {
        "item_id": 1,
        "title": "Carthy Foundation Funding – Urban Green Infrastructure Research",
        "recommendation": "That Council accept the $250,000 Carthy Foundation grant.",
        "content": "",
        "section_number": "8.1.2",
        "timestamp_inherited": True,
        "time_start_ms": 100_000,
        "time_end_ms": 154_000,
    }
    base.update(extra)
    return base


def header(**extra) -> dict:
    """A structural container: inherited or absent timestamp, no substance."""
    base = {
        "item_id": 2,
        "title": "Standing Policy Committee on Finance",
        "recommendation": "",
        "content": "",
        "section_number": "8.4",
        "timestamp_inherited": True,
        "time_start_ms": 100_000,
        "time_end_ms": 154_000,
    }
    base.update(extra)
    return base


def discussed(**extra) -> dict:
    base = {
        "item_id": 3,
        "title": "New Saskatoon Transit Bylaw",
        "recommendation": "That Council adopt the bylaw.",
        "content": "",
        "section_number": "8.2.1",
        "time_start_ms": 0,
        "time_end_ms": 600_000,
    }
    base.update(extra)
    return base


class TestConsentItemDetection:
    def test_inherited_timestamp_plus_substance_is_consent(self):
        assert is_consent_item(consent())

    def test_a_header_is_not_a_consent_item(self):
        """Both inherit a timestamp; only one says something of its own."""
        assert not is_consent_item(header())
        assert is_section_header(header())

    def test_an_independently_timed_item_is_not_consent(self):
        assert not is_consent_item(discussed())

    def test_the_consent_agenda_container_itself_is_not_an_item(self):
        """'CONSENT AGENDA' has a recommendation but is a heading."""
        block = consent(
            title="CONSENT AGENDA",
            recommendation="That the items listed be adopted.",
            section_number="8.",
        )
        assert is_procedural("CONSENT AGENDA")
        assert not is_consent_item(block)

    def test_a_recess_is_not_a_consent_item(self):
        assert not is_consent_item(consent(is_recess=True))


class TestSectionHeaders:
    def test_no_timestamp_and_no_substance_is_a_header(self):
        assert is_section_header(header(
            timestamp_inherited=False, time_start_ms=None, time_end_ms=None,
        ))

    def test_an_item_with_substance_is_never_a_header(self):
        assert not is_section_header(consent())

    def test_headers_stay_ineligible(self):
        assert not is_eligible_for_summary(header())


class TestEligibility:
    def test_consent_items_are_now_eligible(self):
        assert is_eligible_for_summary(consent())

    def test_consent_items_bypass_the_duration_floor(self):
        """Their span is the parent's 54 seconds, which says nothing."""
        assert is_eligible_for_summary(consent(
            time_start_ms=0, time_end_ms=54_000,
        ))

    def test_a_brief_non_consent_item_is_still_ineligible(self):
        assert not is_eligible_for_summary(discussed(
            time_start_ms=0, time_end_ms=30_000,
        ))

    def test_procedural_items_stay_ineligible(self):
        assert not is_eligible_for_summary(consent(title="ADOPTION OF MINUTES"))

    def test_discussed_items_are_still_eligible(self):
        assert is_eligible_for_summary(discussed())


class TestConsentItemsGetNoTranscript:
    """An inherited timestamp identifies the parent's audio, not the item's."""

    def test_slicing_on_an_inherited_timestamp_returns_nothing(self):
        segments = [{"start_ms": 100_000, "end_ms": 154_000, "text": "clerk reads the block"}]
        assert _slice_transcript(segments, consent()) == []

    def test_a_real_timestamp_still_slices(self):
        segments = [{"start_ms": 0, "end_ms": 600_000, "text": "real debate"}]
        assert len(_slice_transcript(segments, discussed())) == 1

    def test_consent_items_never_share_the_blocks_audio(self):
        """Two consent items under one parent must not both claim its audio."""
        segments = [{"start_ms": 100_000, "end_ms": 154_000, "text": "block adopted"}]
        a = _slice_transcript(segments, consent(item_id=1))
        b = _slice_transcript(segments, consent(item_id=2))
        assert a == b == []


class TestConsentPrompt:
    def test_prompt_states_there_was_no_discussion(self):
        prompt = _build_prompt(consent(), "", ["Who's Affected"])
        assert "NO individual discussion" in prompt

    def test_prompt_forbids_inventing_debate(self):
        prompt = _build_prompt(consent(), "", ["Who's Affected"])
        assert "Do NOT describe, infer, or imply any debate" in prompt

    def test_discussed_items_get_the_normal_prompt(self):
        prompt = _build_prompt(discussed(), "transcript", ["Who's Affected"])
        assert "NO individual discussion" not in prompt

    def test_discussion_only_categories_are_withheld_by_construction(self):
        captured: dict = {}

        def generate(prompt, allowed_cats):
            captured["allowed"] = list(allowed_cats)
            return '{"description": "Accepts a $250,000 research grant.", "chips": []}'

        ex = GeminiExtractor(api_key="k", generate=generate)
        extract_item_summaries(consent(), [], gemini_extractor=ex)
        assert DISCUSSION_ONLY_CATEGORIES.isdisjoint(captured["allowed"])
        # The categories that survive are the ones derivable from official text.
        assert "Who's Affected" in captured["allowed"]

    def test_discussed_items_keep_the_discussion_categories(self):
        captured: dict = {}

        def generate(prompt, allowed_cats):
            captured["allowed"] = list(allowed_cats)
            return '{"description": "Adopts the transit bylaw.", "chips": []}'

        ex = GeminiExtractor(api_key="k", generate=generate)
        extract_item_summaries(
            discussed(), [{"start_ms": 0, "end_ms": 600_000, "text": "debate"}],
            gemini_extractor=ex,
        )
        assert "Debate Highlight" in captured["allowed"]


class TestConsentItemsProduceSummaries:
    def test_metadata_only_item_still_gets_a_description(self):
        ex = GeminiExtractor(
            api_key="k",
            generate=lambda p, c: (
                '{"description": "Accepts a $250,000 Carthy Foundation grant for '
                'urban green infrastructure research.", "chips": []}'
            ),
        )
        out = extract_item_summaries(consent(), [], gemini_extractor=ex)
        assert out["description"].startswith("Accepts a $250,000")

    def test_deterministic_chips_still_run_on_consent_items(self):
        ex = GeminiExtractor(
            api_key="k",
            generate=lambda p, c: '{"description": "Does a thing.", "chips": []}',
        )
        out = extract_item_summaries(
            consent(vote_result="CARRIED UNANIMOUSLY",
                    vote_detail="In Favour: (11) All"),
            [], gemini_extractor=ex,
        )
        cats = [c["category"] for c in out["chips"]]
        assert "Outcome" in cats
        assert "Vote Breakdown" in cats


class TestBoilerplateRecommendations:
    """An item that resolved nothing has nothing to summarize.

    8.1.3 on the 2026-06-24 council meeting is the case: its whole source
    is "That the report be received as information." plus a note that a
    letter of support exists.  Any description is the title restated, so
    the honest outcome is no summary at all.
    """

    def test_boilerplate_recommendation_is_not_a_consent_item(self):
        assert not is_consent_item(consent(
            recommendation="That the report be received as information.",
            content="A letter of support from the Chair is provided.",
        ))

    def test_a_short_but_real_recommendation_qualifies(self):
        """Length is not the signal -- substance is."""
        assert is_consent_item(consent(
            recommendation=(
                "That Councillor MacDonald be appointed to the Meewasin "
                "Valley Authority to the end of 2026."
            ),
        ))

    def test_content_alone_does_not_qualify(self):
        assert not is_consent_item(consent(
            recommendation="", content="Background notes are attached.",
        ))

    def test_boilerplate_variants(self):
        for rec in [
            "That the report be received as information.",
            "That the information be received.",
            "That the presentation be noted.",
            "That the correspondence be filed.",
        ]:
            assert is_boilerplate_recommendation(rec), rec

    def test_substantive_recommendations_are_not_boilerplate(self):
        for rec in [
            "That Council approve the $250,000 grant application.",
            "That the City Clerk be directed to readvertise for vacancies.",
            "That City Council receive the 2026 edition of the Municipal Manual.",
        ]:
            assert not is_boilerplate_recommendation(rec), rec
