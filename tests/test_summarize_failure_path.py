"""Tests for what the summarize run does when Gemini does not answer.

A run once wrote 35 meetings of empty summaries and exited 0.  Every
layer treated a failed call as "the model had nothing to say", which is a
different fact with the same shape: ``description = None``.

These tests pin the distinction.  A model that answers and declines is a
real outcome and gets saved; a call that fails is an unknown and must
not be.
"""

import json

import pytest

from app.item_categorizer import (
    ExtractionFailed,
    GeminiExtractor,
    QuotaExhausted,
    _retry_delay_seconds,
)


class FakeAPIError(Exception):
    """Stands in for google.genai.errors.ClientError.

    Only the attributes the classifier reads are reproduced — ``code``
    and ``details`` — so the tests do not need the API client installed.
    """

    def __init__(self, code=429, quota_id=None, retry_delay=None, wrapped=True):
        super().__init__(f"{code}")
        self.code = code
        violations = [{"quotaId": quota_id}] if quota_id else []
        details = []
        if violations:
            details.append({
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": violations,
            })
        if retry_delay is not None:
            details.append({
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            })
        body = {"code": code, "status": "RESOURCE_EXHAUSTED", "details": details}
        # The real client sends both shapes depending on the endpoint.
        self.details = {"error": body} if wrapped else body


PER_DAY = "GenerateRequestsPerDayPerProjectPerModel"
PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel"

ITEM = {
    "item_id": 7,
    "title": "Transit Fine Increase",
    "recommendation": "That the fine be raised to $250.",
    "content": "Report on transit fare enforcement.",
}

TRANSCRIPT = "Councillor moved to raise the fine to two hundred and fifty dollars."


def _answer(description="Council raised the transit fine to $250.", chips=None):
    return json.dumps({"description": description, "chips": chips or []})


def _extractor(responses, calls=None):
    """An extractor whose calls replay *responses* in order.

    An entry that is an exception is raised; anything else is returned as
    the response body.
    """
    queue = list(responses)

    def generate(prompt, allowed):
        if calls is not None:
            calls.append(prompt)
        result = queue.pop(0) if queue else responses[-1]
        if isinstance(result, Exception):
            raise result
        return result

    return GeminiExtractor(api_key=None, generate=generate)


@pytest.fixture
def no_waiting(monkeypatch):
    """Records what the retry would have slept instead of sleeping."""
    slept = []
    monkeypatch.setattr("app.item_categorizer.time.sleep", slept.append)
    return slept


# ── The daily quota is not retried ─────────────────────────────────────────


class TestDailyQuotaStopsImmediately:
    def test_a_per_day_quota_raises_quota_exhausted(self, no_waiting):
        ex = _extractor([FakeAPIError(quota_id=PER_DAY)])
        with pytest.raises(QuotaExhausted):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())

    def test_it_is_not_retried_even_once(self, no_waiting):
        """The run is unattended.  Nobody is there to raise the limit, and
        the next scheduled run redoes the meetings for free."""
        calls = []
        ex = _extractor([FakeAPIError(quota_id=PER_DAY)], calls=calls)
        with pytest.raises(QuotaExhausted):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert len(calls) == 1
        assert no_waiting == []

    def test_quota_exhausted_is_not_an_extraction_failure(self):
        """They are caught in different places: one skips a meeting, the
        other stops the run.  Making one a subclass of the other would
        let the meeting-level handler swallow the run-level stop."""
        assert not issubclass(QuotaExhausted, ExtractionFailed)
        assert not issubclass(ExtractionFailed, QuotaExhausted)


# ── A rate limit is waited out ─────────────────────────────────────────────


class TestRateLimitIsRetried:
    def test_a_per_minute_limit_retries_and_succeeds(self, no_waiting):
        ex = _extractor([
            FakeAPIError(quota_id=PER_MINUTE, retry_delay="5s"),
            _answer(),
        ])
        description, _ = ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert description == "Council raised the transit fine to $250."

    def test_it_waits_the_delay_the_server_asked_for(self, no_waiting):
        ex = _extractor([
            FakeAPIError(quota_id=PER_MINUTE, retry_delay="7s"),
            _answer(),
        ])
        ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert no_waiting == [7.0]

    def test_a_long_delay_is_capped(self, no_waiting):
        """A bad retryDelay must not park an unattended run for hours."""
        ex = _extractor([
            FakeAPIError(quota_id=PER_MINUTE, retry_delay="8000s"),
            _answer(),
        ])
        ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert no_waiting == [60]

    def test_a_persistent_rate_limit_fails_the_item(self, no_waiting):
        """Three attempts, then the item is unknown — not empty."""
        calls = []
        ex = _extractor([FakeAPIError(quota_id=PER_MINUTE, retry_delay="1s")], calls=calls)
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert len(calls) == 3

    def test_the_unwrapped_error_shape_is_read_too(self, no_waiting):
        ex = _extractor([
            FakeAPIError(quota_id=PER_MINUTE, retry_delay="2s", wrapped=False),
            _answer(),
        ])
        ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert no_waiting == [2.0]

    def test_a_missing_retry_delay_falls_back_to_the_cap(self, no_waiting):
        ex = _extractor([FakeAPIError(quota_id=PER_MINUTE), _answer()])
        ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert no_waiting == [60]

    def test_an_unparseable_retry_delay_is_not_trusted(self):
        assert _retry_delay_seconds(
            FakeAPIError(quota_id=PER_MINUTE, retry_delay="soon")
        ) is None


class TestUnrecognised429:
    def test_it_is_given_two_attempts_then_treated_as_quota(self, no_waiting):
        """Assuming quota outright would abandon a run over a rate limit
        we could have waited out.  Two attempts costs a minute."""
        calls = []
        ex = _extractor([FakeAPIError(quota_id=None)], calls=calls)
        with pytest.raises(QuotaExhausted):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert len(calls) == 2


# ── Everything else that is not an answer ──────────────────────────────────


class TestOtherFailuresAreNotSilent:
    def test_a_network_error_fails_the_item(self, no_waiting):
        ex = _extractor([ConnectionError("connection reset")])
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())

    def test_an_auth_error_fails_the_item(self, no_waiting):
        ex = _extractor([FakeAPIError(code=403)])
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())

    def test_a_non_429_is_not_retried(self, no_waiting):
        calls = []
        ex = _extractor([FakeAPIError(code=500)], calls=calls)
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert len(calls) == 1

    def test_unparseable_json_fails_the_item(self, no_waiting):
        ex = _extractor(["not json at all"])
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())

    def test_a_json_array_fails_the_item(self, no_waiting):
        """Valid JSON, wrong shape — still not an answer about the item."""
        ex = _extractor(["[]"])
        with pytest.raises(ExtractionFailed):
            ex.extract(ITEM, TRANSCRIPT, exclude=set())


# ── The model declining is a real outcome ──────────────────────────────────


class TestADeclinedAnswerIsKept:
    def test_no_description_returns_rather_than_raises(self, no_waiting):
        """The model answered.  Having nothing to say about a routine item
        is a fact about the item, and the card shows its raw fallback."""
        ex = _extractor([_answer(description=None)])
        description, chips = ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert description is None
        assert chips == []

    def test_chips_without_a_description_are_kept(self, no_waiting):
        ex = _extractor([
            _answer(
                description=None,
                chips=[{
                    "category": "Promise Made",
                    "text": "Staff will report back in June.",
                    "usefulness": "high",
                }],
            )
        ])
        description, chips = ex.extract(ITEM, TRANSCRIPT, exclude=set())
        assert description is None
        assert [c["category"] for c in chips] == ["Promise Made"]

    def test_an_item_with_nothing_to_read_never_calls_the_model(self, no_waiting):
        calls = []
        ex = _extractor([_answer()], calls=calls)
        assert ex.extract({"title": "X"}, "   ", exclude=set()) == (None, [])
        assert calls == []


# ── A meeting is saved whole or not at all ─────────────────────────────────


class _StubItem:
    """One agenda item, in the shape summarize_meeting consumes."""

    def __init__(self, item_id, title, start_ms, end_ms):
        self._data = {
            "item_id": item_id,
            "title": title,
            "recommendation": f"That {title.lower()} be approved.",
            "content": f"Report on {title.lower()}.",
            "section_number": f"{item_id}.",
            "time_start_ms": start_ms,
            "time_end_ms": end_ms,
        }

    def to_dict(self):
        return dict(self._data)


class _StubSource:
    def __init__(self, items):
        self._items = items

    def load_detail(self, meeting_id):
        return type("Detail", (), {"agenda_items": self._items})()


class _StubTranscriptCache:
    def __init__(self, transcript):
        self._transcript = transcript

    def load(self, meeting_id):
        return self._transcript


def _meeting(item_count=2):
    from app.models import Segment, Transcript
    from scripts.summarize_meetings import summarize_meeting

    items = [
        _StubItem(i + 1, f"Item {i + 1} Funding Decision", i * 600_000, (i + 1) * 600_000)
        for i in range(item_count)
    ]
    segments = [
        Segment(
            start_ms=i * 600_000,
            end_ms=(i + 1) * 600_000,
            text="Council debated the funding at length and moved to approve it.",
        )
        for i in range(item_count)
    ]
    return summarize_meeting, _StubSource(items), _StubTranscriptCache(
        Transcript(segments=segments)
    )


class TestAFailedItemSinksTheMeeting:
    """The 35-meeting bug in one rule.

    A meeting holding an item nobody could summarize is not a summarized
    meeting.  Returning it lets the caller save it, and a saved summary is
    indistinguishable from a considered one — so nothing ever retries it.
    """

    def test_one_failed_item_raises_rather_than_returning_partial(self, no_waiting):
        run, source, cache = _meeting(item_count=2)
        ex = _extractor([_answer(), ConnectionError("connection reset")])
        with pytest.raises(ExtractionFailed):
            run(source, "m1", ex, cache)

    def test_the_failure_names_the_item(self, no_waiting):
        """"One item failed" is not actionable without knowing which."""
        run, source, cache = _meeting(item_count=1)
        ex = _extractor([ConnectionError("connection reset")])
        with pytest.raises(ExtractionFailed, match="Funding Decision"):
            run(source, "m1", ex, cache)

    def test_a_spent_quota_travels_past_the_meeting(self, no_waiting):
        """It must not arrive as an ExtractionFailed — that would only skip
        this meeting and the run would carry on burning its budget."""
        run, source, cache = _meeting(item_count=2)
        ex = _extractor([FakeAPIError(quota_id=PER_DAY)])
        with pytest.raises(QuotaExhausted):
            run(source, "m1", ex, cache)

    def test_a_meeting_the_model_merely_declined_is_returned(self, no_waiting):
        """Every call succeeded and the model had nothing to say.  That is
        a summarized meeting, and it is saved."""
        run, source, cache = _meeting(item_count=2)
        ex = _extractor([_answer(description=None)])
        summaries = run(source, "m1", ex, cache)
        assert len(summaries) == 2
        assert all(s.description is None for s in summaries.values())
