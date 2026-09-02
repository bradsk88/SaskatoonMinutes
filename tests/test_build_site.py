"""Static-build glue: the parts that shape what the pages read."""

from scripts.build_site import _start_time_24h, _merge_recorded
from app.models import Meeting


class TestMergeRecorded:
    """A recorded-but-unpassed meeting is landed on its body's past tab.

    The gap is a recording that is up but the upstream still marks
    not-passed: it has happened, so it belongs on the past tab rather
    than the Future tab. The merge dedupes against the passed set and
    orders most-recent first, alongside the passed meetings.
    """

    def _m(self, meeting_id, date, **kw):
        return Meeting(
            meeting_id=meeting_id, title="t", date=date, start_time="09:00",
            location="h", has_video=True, has_agenda=True, **kw,
        )

    def test_recorded_meeting_is_added(self):
        past = [self._m("p1", "2026-08-01")]
        recorded = [self._m("r1", "2026-09-01")]
        merged = _merge_recorded(past, recorded)
        assert [m.meeting_id for m in merged] == ["r1", "p1"]

    def test_duplicate_is_deduped(self):
        past = [self._m("p1", "2026-08-01")]
        recorded = [self._m("p1", "2026-09-01")]
        merged = _merge_recorded(past, recorded)
        assert [m.meeting_id for m in merged] == ["p1"]

    def test_ordering_is_most_recent_first(self):
        past = [self._m("p1", "2026-08-01"), self._m("p2", "2026-07-01")]
        recorded = [self._m("r1", "2026-09-01")]
        merged = _merge_recorded(past, recorded)
        assert [m.meeting_id for m in merged] == ["r1", "p1", "p2"]

    def test_empty_recorded_is_a_noop(self):
        past = [self._m("p1", "2026-08-01")]
        merged = _merge_recorded(past, [])
        assert [m.meeting_id for m in merged] == ["p1"]


class TestStartTime24h:
    """A council agenda page carries a date with no clock time.

    The meetings list has it, in eSCRIBE's formatted form, so the build
    falls back to that rather than leaving council meetings the only
    ones unable to say when they sat.
    """

    def test_morning(self):
        assert _start_time_24h("Wednesday, 24 June 2026 @ 9:30 AM") == "09:30"

    def test_afternoon(self):
        assert _start_time_24h("Tuesday, 5 May 2026 @ 1:05 PM") == "13:05"

    def test_noon(self):
        assert _start_time_24h("Monday @ 12:00 PM") == "12:00"

    def test_midnight(self):
        assert _start_time_24h("Monday @ 12:15 AM") == "00:15"

    def test_dotted_meridiem(self):
        assert _start_time_24h("Monday @ 9:30 a.m.") == "09:30"

    def test_no_time_present(self):
        assert _start_time_24h("Wednesday, 24 June 2026") == ""

    def test_empty(self):
        assert _start_time_24h("") == ""
