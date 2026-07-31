"""Tests for app.summary_judge — the LLM-as-judge quality gate."""

import json
import ssl

import pytest

from app.summary_judge import (
    FAITHFULNESS_CONCERN,
    FAITHFULNESS_FLOOR,
    MIN_MEAN_FAITHFULNESS,
    SummaryJudge,
    _sanitize_verdict,
    build_judge_prompt,
)


@pytest.fixture
def no_waiting(monkeypatch):
    """Records what the retry would have slept instead of sleeping."""
    slept = []
    monkeypatch.setattr("app.summary_judge.time.sleep", slept.append)
    return slept


def verdict(**over) -> dict:
    base = {
        "faithfulness": 5,
        "specificity": 4,
        "non_redundancy": 5,
        "supporting_quote": "That Council approve $250,000.",
        "unsupported_claims": [],
    }
    base.update(over)
    return base


class TestJudgePrompt:
    def test_source_is_not_truncated(self):
        """A judge denied part of the source calls those claims unsupported.

        Truncating turned the faithfulness score into a measure of how
        long the transcript was: the 117k-char homelessness item scored 1
        because the judge never saw the text its chips came from.
        """
        source = "x" * 60_000
        prompt = build_judge_prompt("Title", source, "A description.", [])
        assert source in prompt

    def test_prompt_requires_a_verbatim_supporting_quote(self):
        prompt = build_judge_prompt("T", "src", "desc", [])
        assert "Copy it verbatim" in prompt

    def test_prompt_allows_an_empty_quote_rather_than_invention(self):
        prompt = build_judge_prompt("T", "src", "desc", [])
        assert "return an empty string" in prompt

    def test_bullets_are_shown_as_bullets(self):
        """One bullet per line, so the judge scores each fact on its own
        instead of blaming a whole block for one bad clause."""
        prompt = build_judge_prompt(
            "T", "src", ["Rezones the lots", "Allows 83 units"], [],
        )
        assert "- Rezones the lots\n- Allows 83 units" in prompt

    def test_a_stored_paragraph_is_still_judged(self):
        """Most of the archive holds the pre-bullet string shape."""
        prompt = build_judge_prompt("T", "src", "Rezones the lots.", [])
        assert "- Rezones the lots." in prompt

    def test_chips_are_listed_for_audit(self):
        prompt = build_judge_prompt(
            "T", "src", "desc",
            [{"category": "Cost & Funding", "text": "$250K for research"}],
        )
        assert "Cost & Funding: $250K for research" in prompt

    def test_no_chips_renders_as_none(self):
        assert "(none)" in build_judge_prompt("T", "src", "desc", [])

    def test_the_title_counts_as_source(self):
        """The extractor is given the title, so a claim drawn from it is
        grounded.  Left outside the source block it read as fabrication."""
        prompt = build_judge_prompt("Green Infrastructure Research", "rec", "d", [])
        body = prompt.split("--- SOURCE MATERIAL ---")[1].split("--- END SOURCE ---")[0]
        assert "Green Infrastructure Research" in body

    def test_the_title_is_still_shown_on_its_own(self):
        """non_redundancy scores the summary against the title."""
        prompt = build_judge_prompt("Green Infrastructure Research", "rec", "d", [])
        assert prompt.split("--- SOURCE MATERIAL ---")[0].count(
            "Green Infrastructure Research"
        ) == 1

    def test_the_generating_prompt_is_not_shown_to_the_judge(self):
        """The judge grades truthfulness, not compliance with instructions."""
        prompt = build_judge_prompt("T", "src", "desc", [])
        assert "Do NOT restate the agenda item's title" not in prompt


class TestSanitizeVerdict:
    def test_valid_verdict_passes_through(self):
        assert _sanitize_verdict(verdict())["faithfulness"] == 5

    def test_scores_are_clamped_to_the_scale(self):
        out = _sanitize_verdict(verdict(faithfulness=99, specificity=-4))
        assert out["faithfulness"] == 5
        assert out["specificity"] == 1

    def test_non_integer_score_invalidates_the_verdict(self):
        assert _sanitize_verdict(verdict(faithfulness="high")) is None

    def test_missing_score_invalidates_the_verdict(self):
        bad = verdict()
        del bad["specificity"]
        assert _sanitize_verdict(bad) is None

    def test_booleans_are_not_accepted_as_scores(self):
        assert _sanitize_verdict(verdict(faithfulness=True)) is None

    def test_blank_claims_are_dropped(self):
        out = _sanitize_verdict(verdict(unsupported_claims=["real", "  ", ""]))
        assert out["unsupported_claims"] == ["real"]

    def test_missing_quote_becomes_empty_string(self):
        out = _sanitize_verdict(verdict(supporting_quote=None))
        assert out["supporting_quote"] == ""


class TestJudgeCall:
    def test_returns_the_parsed_verdict(self):
        judge = SummaryJudge(
            api_key="k", generate=lambda p: json.dumps(verdict(faithfulness=4)),
        )
        assert judge.judge("T", "src", "desc", [])["faithfulness"] == 4

    def test_malformed_json_returns_none_not_a_pass(self):
        judge = SummaryJudge(api_key="k", generate=lambda p: "not json")
        assert judge.judge("T", "src", "desc", []) is None

    def test_a_list_response_returns_none(self):
        judge = SummaryJudge(api_key="k", generate=lambda p: "[]")
        assert judge.judge("T", "src", "desc", []) is None

    def test_disabled_without_a_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert SummaryJudge().enabled is False

    def test_a_missing_description_is_still_judged(self):
        """A summary with no description is exactly what we want scored."""
        captured = {}

        def generate(prompt):
            captured["prompt"] = prompt
            return json.dumps(verdict(faithfulness=1))

        judge = SummaryJudge(api_key="k", generate=generate)
        assert judge.judge("T", "src", None, [])["faithfulness"] == 1
        assert "(none)" in captured["prompt"]


class TestTransientFailuresAreRetried:
    """A judge that does not return fails the check, so a dropped TLS
    record was enough to turn CI red on otherwise fine summaries."""

    def _replaying(self, responses, calls):
        def generate(prompt):
            calls.append(prompt)
            result = responses.pop(0) if responses else None
            if isinstance(result, Exception):
                raise result
            return result

        return SummaryJudge(api_key="k", generate=generate)

    def test_an_ssl_error_is_retried(self, no_waiting):
        calls = []
        judge = self._replaying(
            [ssl.SSLError("bad record mac"), json.dumps(verdict())], calls,
        )
        assert judge.judge("T", "src", "desc", [])["faithfulness"] == 5
        assert len(calls) == 2

    def test_an_overloaded_model_is_retried(self, no_waiting):
        exc = Exception("overloaded")
        exc.code = 503
        calls = []
        judge = self._replaying([exc, json.dumps(verdict())], calls)
        assert judge.judge("T", "src", "desc", []) is not None

    def test_it_gives_up_rather_than_grinding(self, no_waiting):
        calls = []
        judge = self._replaying([ssl.SSLError("bad record mac")] * 9, calls)
        assert judge.judge("T", "src", "desc", []) is None
        assert len(calls) == 3

    def test_a_bad_request_is_not_retried(self, no_waiting):
        exc = Exception("invalid argument")
        exc.code = 400
        calls = []
        judge = self._replaying([exc], calls)
        assert judge.judge("T", "src", "desc", []) is None
        assert len(calls) == 1

    def test_a_malformed_answer_is_not_retried(self):
        """The model answered.  Asking again is a different question."""
        calls = []
        judge = self._replaying(["not json"], calls)
        assert judge.judge("T", "src", "desc", []) is None
        assert len(calls) == 1


class TestGateThresholds:
    def test_the_floor_catches_fabrication_not_overstatement(self):
        """Rubric semantics: 1-2 asserts unsupported facts, 3 overstates."""
        assert FAITHFULNESS_FLOOR == 2
        assert FAITHFULNESS_CONCERN == 3
        assert FAITHFULNESS_FLOOR < FAITHFULNESS_CONCERN

    def test_the_mean_gate_sits_above_the_floor(self):
        assert MIN_MEAN_FAITHFULNESS > FAITHFULNESS_FLOOR
