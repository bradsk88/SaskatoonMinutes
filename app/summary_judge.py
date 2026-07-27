"""LLM-as-judge for ItemSummary quality.

The cheap gates in ``scripts/eval_chips.py`` catch structural failure — a
missing description, a chip that restates the title, a category that
vanished.  They cannot see the failure this project cares most about: a
summary that reads well and says something the source never said.

So the judge scores each summary against the source material it was built
from, and is required to **quote the span that supports the claim**.  A
judge that cannot produce a supporting quote has found an unsupported
claim, which is exactly the signal we want.

Deliberately a separate model call from extraction, with the source in
front of it and the generating prompt withheld — a judge shown the
instructions tends to grade compliance with the instructions rather than
truthfulness about the meeting.
"""

from __future__ import annotations

import json
import os

GEMINI_MODEL = "gemini-2.5-flash"

# Scores are 1-5.  The gate is deliberately set below "excellent": the
# judge is here to catch fabrication and vagueness, not to enforce a
# house style.
SCORE_MIN = 1
SCORE_MAX = 5

MIN_MEAN_FAITHFULNESS = 4.0
MIN_MEAN_SPECIFICITY = 3.0

# Any single summary scoring at or below this on faithfulness fails the run
# on its own, however good the mean is.
#
# Set at 2, not 3, to match the rubric's own semantics: 1-2 is "asserts
# facts the source does not contain" (fabrication, a trust failure), while
# 3 is "something is overstated or implied beyond the source" (a quality
# failure).  Fabrication has to stop the build.  Overstatement is reported
# in the Flagged section and caught in aggregate by the mean gate — but a
# single strict judgment on an eleven-item sample should not turn CI red,
# especially when the judge is itself a sampled model.
FAITHFULNESS_FLOOR = 2

# Scores at or below this are surfaced in the report even when they pass.
FAITHFULNESS_CONCERN = 3


def _judge_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "faithfulness": {"type": "integer"},
            "specificity": {"type": "integer"},
            "non_redundancy": {"type": "integer"},
            "supporting_quote": {"type": "string"},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "faithfulness", "specificity", "non_redundancy",
            "supporting_quote", "unsupported_claims",
        ],
    }


def build_judge_prompt(
    title: str,
    source: str,
    description: str | list[str],
    chips: list[dict],
) -> str:
    chip_lines = "\n".join(f"- {c['category']}: {c['text']}" for c in chips) or "(none)"
    # The description is a list of bullets; it is shown as bullets so the
    # judge scores each fact on its own rather than reading four lines as
    # one sentence and blaming the whole block for one bad clause.
    bullets = [description] if isinstance(description, str) else list(description)
    desc_lines = "\n".join(f"- {b}" for b in bullets if b) or "(none)"
    return "\n".join([
        "You are auditing an AI-generated summary of one agenda item from "
        "a Saskatoon city council meeting. You have the source material "
        "the summary was built from. Judge the summary against that "
        "source and nothing else.",
        "",
        "Score each dimension 1-5:",
        "",
        "- faithfulness: does every claim in the summary follow from the "
        "source? 5 = every claim is supported. 3 = something is "
        "overstated or implied beyond the source. 1 = it asserts facts "
        "the source does not contain. Judge truth, not style.",
        "- specificity: does it give a reader something concrete — an "
        "amount, a date, a location, an affected group, a named "
        "commitment? 5 = concrete and useful. 1 = could describe any "
        "agenda item at any meeting.",
        "- non_redundancy: does it say more than the item's title already "
        "says? 5 = substantially more. 1 = the title reworded.",
        "",
        "Then:",
        "- supporting_quote: quote the span of the SOURCE that best "
        "supports the description. Copy it verbatim. If no span in the "
        "source supports the description, return an empty string — do "
        "not paraphrase or invent one.",
        "- unsupported_claims: list any specific claim in the summary you "
        "could not find support for in the source. Empty list if all "
        "claims check out.",
        "",
        f"Agenda item title: {title}",
        "",
        # Not truncated.  A judge that cannot see the whole source reports
        # every claim drawn from the part it was denied as unsupported,
        # which turns the faithfulness score into a measure of how long
        # the transcript is.  Flash takes a 1M-token context; a 117k-char
        # agenda item fits with room to spare.
        "--- SOURCE MATERIAL ---",
        source,
        "--- END SOURCE ---",
        "",
        "Summary under audit:",
        "",
        "Description:",
        desc_lines,
        "",
        "Chips:",
        chip_lines,
        "",
        "Return the JSON object.",
    ])


class SummaryJudge:
    """Scores ItemSummaries against their source material."""

    def __init__(self, api_key: str | None = None, generate=None) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self._generate = generate
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) or self._generate is not None

    def _call(self, prompt: str) -> str:
        if self._generate is not None:
            return self._generate(prompt)
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        response = self._client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _judge_schema(),
                "temperature": 0.0,
            },
        )
        return response.text or "{}"

    def judge(
        self,
        title: str,
        source: str,
        description: str | list[str] | None,
        chips: list[dict],
    ) -> dict | None:
        """Return the verdict dict, or ``None`` when the call fails.

        ``None`` is distinct from a bad score: it means we learned
        nothing, so the caller must not treat it as a pass.
        """
        prompt = build_judge_prompt(title, source, description or [], chips)
        try:
            parsed = json.loads(self._call(prompt))
        except Exception as exc:
            print(f"    judge failed: {exc}", flush=True)
            return None
        if not isinstance(parsed, dict):
            return None
        return _sanitize_verdict(parsed)


def _clamp_score(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(SCORE_MIN, min(SCORE_MAX, value))


def _sanitize_verdict(parsed: dict) -> dict | None:
    scores = {
        key: _clamp_score(parsed.get(key))
        for key in ("faithfulness", "specificity", "non_redundancy")
    }
    if any(v is None for v in scores.values()):
        return None
    claims = parsed.get("unsupported_claims")
    quote = parsed.get("supporting_quote")
    return {
        **scores,
        "supporting_quote": quote.strip() if isinstance(quote, str) else "",
        "unsupported_claims": [
            c for c in (claims or []) if isinstance(c, str) and c.strip()
        ],
    }
