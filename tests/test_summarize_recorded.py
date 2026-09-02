"""Tests for the recorded-but-unpassed pass in scripts/summarize_meetings.py.

A meeting that happened, was recorded, and is now past-dated but the
upstream still marks not passed reaches neither the full pass (list_past,
passed only) nor the provisional pass (future window only). Without a
list_recorded pass it never gets a full summary, and the provisional cache
written for it while it was in the future window is not current (is_current
rejects provisional), so no pass ever regenerates it.

The transcribe job fixed the same gap with a calendar pass
(scripts/transcribe_meetings.py); the summarize job never got the
equivalent. These tests pin that the new pass closes it.
"""

import json

import pytest

from app.item_categorizer import (
    ExtractionFailed,
    GeminiExtractor,
    QuotaExhausted,
)
from app.meeting_source import InMemoryMeetingSource
from app.models import (
    AgendaItem,
    Chip,
    ItemSummary,
    Meeting,
    MeetingDetail,
    Segment,
    Transcript,
)
from scripts.summarize_meetings import is_current, summarize_recorded_meetings


SINCE, UNTIL = "2026-08-01", "2026-10-01"


# ── Fixtures ─────────────────────────────────────────────────────────


def _valid_answer():
    return json.dumps({"description": "Council approved the funding.", "chips": []})


def _extractor(*, raise_exc=None):
    """An extractor whose call returns a valid answer or raises *raise_exc*.

    ``generate`` is the seam ``GeminiExtractor`` calls, so a raise here
    surfaces the same way it would in the real run.
    """
    if raise_exc is None:
        def generate(prompt, allowed):
            return _valid_answer()
    else:
        def generate(prompt, allowed):
            raise raise_exc
    return GeminiExtractor(api_key=None, generate=generate)


def _item(item_id=1):
    """One discussed item, wide enough to clear the eligibility bar."""
    return AgendaItem(
        item_id=item_id,
        title="Funding Decision",
        content="Report on the funding allocation.",
        section_number=f"{item_id}.",
        time_start_ms=0,
        time_end_ms=600_000,  # 10 minutes: above MIN_DISCUSSED_MS
        recommendation="That the funding be approved.",
    )


def _transcript():
    """A segment over the item's window, so the model is actually called."""
    return Transcript(segments=[
        Segment(
            start_ms=0,
            end_ms=600_000,
            text="Council debated the funding at length and moved to approve it.",
        ),
    ])


def _meeting(meeting_id, date="2026-09-01"):
    return Meeting(
        meeting_id=meeting_id,
        title="Transportation",
        date=date,
        start_time="",
        location="",
        has_video=True,
        has_agenda=True,
    )


def _detail(items):
    return MeetingDetail(agenda_items=items)


class _TranscriptCache:
    def __init__(self, transcript):
        self._transcript = transcript

    def load(self, meeting_id):
        return self._transcript


class _SummariesCache:
    def __init__(self, existing=None):
        self._data: dict[str, dict[str, ItemSummary]] = dict(existing or {})

    def load(self, meeting_id):
        return self._data.get(meeting_id)

    def save(self, meeting_id, summaries):
        self._data[meeting_id] = summaries


def _source(recorded, cached=None):
    """A source that returns *recorded* meetings and their detail."""
    details = {
        m.meeting_id: _detail([_item()]) for m in recorded
    }
    return (
        InMemoryMeetingSource(
            details=details,
            recorded=recorded,
            past=(),
            scheduled=(),
        ),
        _SummariesCache(cached),
    )


def _run(recorded, cached=None, *, limit=5, force=False, raise_exc=None):
    source, summaries_cache = _source(recorded, cached)
    transcript_cache = _TranscriptCache(_transcript())
    extractor = _extractor(raise_exc=raise_exc)
    return summarize_recorded_meetings(
        source, extractor, transcript_cache, summaries_cache,
        since=SINCE, until=UNTIL, limit=limit, force=force,
    ), summaries_cache


# ── The gap closes ───────────────────────────────────────────────────


class TestRecordedMeetingGetsASummary:
    def test_a_recorded_unpassed_meeting_is_summarized(self):
        """The gap: recorded, past-dated, not passed, empty cache."""
        result, _ = _run([_meeting("m1")])
        summarized, skipped, errors, quota_gone = result
        assert (summarized, skipped, errors, quota_gone) == (1, 0, 0, False)

    def test_the_regenerated_summary_is_full_not_provisional(self):
        """The pass replaces the provisional cache with a real one."""
        provisional = {
            "m1": {
                "1": ItemSummary(
                    description=None, chips=[], provisional=True,
                ),
            },
        }
        _, summaries_cache = _run([_meeting("m1")], cached=provisional)
        regenerated = summaries_cache.load("m1")
        assert is_current(regenerated) is True
        assert not any(s.provisional for s in regenerated.values())

    def test_the_exact_gap_meeting_scenario(self):
        """c046eacd's shape: a provisional/empty cache, now past-dated."""
        # Provisional cache written while the meeting was in the future
        # window: every item provisional and empty.
        provisional = {
            "m1": {
                "1": ItemSummary(description=None, chips=[], provisional=True),
                "2": ItemSummary(description=None, chips=[], provisional=True),
            },
        }
        (summarized, _, _, _), summaries_cache = _run(
            [_meeting("m1")], cached=provisional,
        )
        assert summarized == 1
        assert is_current(summaries_cache.load("m1")) is True


# ── What the pass skips ──────────────────────────────────────────────


class TestSkipRules:
    def test_a_current_cache_is_skipped_not_redone(self):
        """is_current is the skip rule, mirroring the full pass."""
        current = {"m1": {"1": ItemSummary(
            description="Council approved.", chips=[Chip("Outcome", "Approved")],
        )}}
        result, _ = _run([_meeting("m1")], cached=current)
        summarized, skipped, errors, quota_gone = result
        assert (summarized, skipped, errors, quota_gone) == (0, 1, 0, False)

    def test_force_overrides_the_skip(self):
        """--force redoes an already-current meeting, as the full pass does."""
        current = {"m1": {"1": ItemSummary(
            description="Council approved.", chips=[Chip("Outcome", "Approved")],
        )}}
        result, _ = _run([_meeting("m1")], cached=current, force=True)
        summarized, skipped, _, _ = result
        assert summarized == 1
        assert skipped == 0

    def test_a_meeting_with_no_transcript_is_not_summarized(self):
        """No transcript: nothing to summarize, so it is not saved empty."""
        source, summaries_cache = _source([_meeting("m1")])
        transcript_cache = _TranscriptCache(None)
        summarized, skipped, errors, quota_gone = summarize_recorded_meetings(
            source, _extractor(), transcript_cache, summaries_cache,
            since=SINCE, until=UNTIL, limit=5, force=False,
        )
        assert (summarized, skipped, errors, quota_gone) == (0, 0, 0, False)
        assert summaries_cache.load("m1") is None


# ── The cap and the quota ────────────────────────────────────────────


class TestBounds:
    def test_limit_caps_the_pass(self):
        """The cap is on work done, so one meeting per run as asked."""
        (summarized, skipped, errors, quota_gone), _ = _run(
            [_meeting("m1"), _meeting("m2")], limit=1,
        )
        assert (summarized, skipped, errors, quota_gone) == (1, 0, 0, False)

    def test_a_quota_rejection_stops_the_pass(self):
        """One quota rejection stops the run; the rest are left to retry."""
        (summarized, skipped, errors, quota_gone), _ = _run(
            [_meeting("m1"), _meeting("m2")],
            limit=5,
            raise_exc=QuotaExhausted("daily quota gone"),
        )
        assert quota_gone is True
        assert errors == 1
        # Nothing was saved: a failed meeting is not a summarized meeting.

    def test_an_extraction_failure_is_one_error_not_a_stop(self):
        """A single failed item skips the meeting; the run carries on."""
        (summarized, skipped, errors, quota_gone), _ = _run(
            [_meeting("m1")],
            limit=5,
            raise_exc=ExtractionFailed("model did not answer"),
        )
        assert quota_gone is False
        assert errors == 1
        assert summarized == 0


# ── A fetch failure is not a crash ───────────────────────────────────


class TestFetchFailure:
    def test_a_recorded_fetch_error_returns_zero(self):
        class _BoomSource:
            def list_recorded(self, since, until):
                raise ConnectionError("calendar unreachable")

            def load_detail(self, meeting_id):
                raise AssertionError("not reached")

        summaries_cache = _SummariesCache()
        summarized, skipped, errors, quota_gone = summarize_recorded_meetings(
            _BoomSource(), _extractor(), _TranscriptCache(_transcript()),
            summaries_cache,
            since=SINCE, until=UNTIL, limit=5, force=False,
        )
        assert (summarized, skipped, errors, quota_gone) == (0, 0, 0, False)
