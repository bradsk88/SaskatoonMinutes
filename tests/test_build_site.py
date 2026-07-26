"""Static-build glue: the parts that shape what the pages read."""

from scripts.build_site import _readable_date, _start_time_24h


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


class TestReadableDate:
    def test_iso_becomes_readable(self):
        assert _readable_date("2025-06-17") == "June 17, 2025"

    def test_unparseable_comes_back_unchanged(self):
        assert _readable_date("not-a-date") == "not-a-date"

    def test_empty(self):
        assert _readable_date("") == ""
