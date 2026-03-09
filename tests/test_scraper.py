import pytest

from app.scraper import (
    AgendaItem,
    _parse_escribemeetings_date,
    _clean_html,
    _extract_bookmarks,
    _extract_votes,
    _extract_recommendations,
    _propagate_timestamps,
    _mark_brief_items,
)


# ── _parse_escribemeetings_date ──────────────────────────────────────


class TestParseEscribemeetingsDate:
    def test_valid_date(self):
        assert _parse_escribemeetings_date("/Date(1719457800000)/") == "2024-06-27"

    def test_epoch(self):
        assert _parse_escribemeetings_date("/Date(0)/") == "1970-01-01"

    def test_non_matching(self):
        assert _parse_escribemeetings_date("not-a-date") == "not-a-date"


# ── _clean_html ──────────────────────────────────────────────────────


class TestCleanHtml:
    def test_strips_tags(self):
        assert _clean_html("<b>Hello</b> <i>world</i>") == "Hello world"

    def test_no_tags(self):
        assert _clean_html("No tags here") == "No tags here"

    def test_div_with_class(self):
        assert _clean_html("<DIV class='foo'>text</DIV>") == "text"


# ── _extract_bookmarks ──────────────────────────────────────────────


class TestExtractBookmarks:
    def test_valid_bookmarks(self):
        html = (
            'Bookmarks : [{"AgendaItemId":101,"TimeStart":275697,"TimeEnd":650293},'
            '{"AgendaItemId":102,"TimeStart":650294,"TimeEnd":900000}]'
        )
        result = _extract_bookmarks(html)
        assert result[101] == {"TimeStart": 275697, "TimeEnd": 650293}
        assert result[102] == {"TimeStart": 650294, "TimeEnd": 900000}

    def test_no_bookmarks(self):
        assert _extract_bookmarks("<html>no bookmarks</html>") == {}

    def test_unquoted_keys_fallback(self):
        html = "Bookmarks : [{AgendaItemId:101,TimeStart:1000,TimeEnd:2000}]"
        result = _extract_bookmarks(html)
        assert result[101] == {"TimeStart": 1000, "TimeEnd": 2000}


# ── _extract_votes (deferral bug — motion_text capture) ─────────────


class TestExtractVotes:
    def test_basic_carried(self):
        html = (
            '<div>SelectItem(200);'
            '<div class="MotionResult"><div>CARRIED UNANIMOUSLY</div></div>'
            '<div class="VoterVote"><div>Cllr A: Yes, Cllr B: Yes</div></div>'
            '<div class="MotionText RichText"><div>That the item be approved.</div></div>'
            '</div>'
        )
        result = _extract_votes(html)
        assert 200 in result
        assert result[200]["result"] == "CARRIED UNANIMOUSLY"
        assert result[200]["motion_text"] == "That the item be approved."
        assert result[200]["is_contested"] is False

    def test_carried_with_deferral_motion(self):
        html = (
            '<div>SelectItem(201);'
            '<div class="MotionResult"><div>CARRIED (8 to 3)</div></div>'
            '<div class="VoterVote"><div>...</div></div>'
            '<div class="MotionText RichText"><div>That the item be deferred to the next meeting.</div></div>'
            '</div>'
        )
        result = _extract_votes(html)
        assert "deferred" in result[201]["motion_text"]
        assert result[201]["is_contested"] is True

    def test_defeated(self):
        html = (
            '<div>SelectItem(202);'
            '<div class="MotionResult"><div>DEFEATED (4 to 7)</div></div>'
            '<div class="MotionText RichText"><div>That X be approved.</div></div>'
            '</div>'
        )
        result = _extract_votes(html)
        assert "DEFEATED" in result[202]["result"]
        assert result[202]["is_contested"] is True

    def test_no_motion_text(self):
        html = (
            '<div>SelectItem(203);'
            '<div class="MotionResult"><div>CARRIED</div></div>'
            '</div>'
        )
        result = _extract_votes(html)
        assert result[203]["motion_text"] == ""

    def test_no_votes_in_html(self):
        assert _extract_votes("<html>nothing</html>") == {}


# ── _extract_recommendations ─────────────────────────────────────────


class TestExtractRecommendations:
    def test_with_recommendation(self):
        html = (
            '<div>SelectItem(300);'
            '<div class="MotionText RichText"><div>That the report be <b>received</b>.</div></div>'
            '</div>'
        )
        result = _extract_recommendations(html)
        assert result[300] == "That the report be received."

    def test_no_recommendation(self):
        html = '<div>SelectItem(301);<div class="other">stuff</div></div>'
        result = _extract_recommendations(html)
        assert 301 not in result


# ── _propagate_timestamps ────────────────────────────────────────────


class TestPropagateTimestamps:
    def test_child_inherits_parent(self):
        items = [
            AgendaItem(item_id=1, title="Consent", content="", section_number="8.",
                       time_start_ms=50000, time_end_ms=60000),
            AgendaItem(item_id=2, title="Sub A", content="", section_number="8.1."),
            AgendaItem(item_id=3, title="Sub B", content="", section_number="8.2.1."),
        ]
        _propagate_timestamps(items)
        assert items[1].time_start_ms == 50000
        assert items[1].timestamp_inherited is True
        assert items[2].time_start_ms == 50000
        assert items[2].timestamp_inherited is True

    def test_own_timestamp_preserved(self):
        items = [
            AgendaItem(item_id=1, title="Parent", content="", section_number="8.",
                       time_start_ms=50000, time_end_ms=60000),
            AgendaItem(item_id=2, title="Child", content="", section_number="8.1.",
                       time_start_ms=99000),
        ]
        _propagate_timestamps(items)
        assert items[1].time_start_ms == 99000
        assert items[1].timestamp_inherited is False

    def test_no_ancestor(self):
        items = [
            AgendaItem(item_id=1, title="Orphan", content="", section_number="5.1."),
        ]
        _propagate_timestamps(items)
        assert items[0].time_start_ms is None
        assert items[0].timestamp_inherited is False


# ── AgendaItem.time_start_formatted ──────────────────────────────────


# ── _mark_brief_items ───────────────────────────────────────────────


class TestMarkBriefItems:
    """Items whose video bookmark covers only a trivial duration (e.g. ≤1 s)
    were never actually discussed and should be flagged the same as consent
    items so the UI shows "Not discussed".

    Reproduces the Mar 3 2026 SPC-Transportation meeting where several items
    had their own bookmark but the gap to the next item was ~1 second.
    """

    def test_brief_item_marked_as_inherited(self):
        """An item with a 1-second bookmark should be treated as not-discussed."""
        items = [
            AgendaItem(item_id=1, title="Brief item", content="",
                       section_number="3.1.",
                       time_start_ms=100000, time_end_ms=101000),  # 1 s
            AgendaItem(item_id=2, title="Next item", content="",
                       section_number="3.2.",
                       time_start_ms=101000, time_end_ms=500000),
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is True
        assert items[1].timestamp_inherited is False

    def test_real_discussion_not_marked(self):
        """An item spanning several minutes should remain as discussed."""
        items = [
            AgendaItem(item_id=1, title="Long item", content="",
                       section_number="4.1.",
                       time_start_ms=100000, time_end_ms=400000),  # 5 min
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is False

    def test_already_inherited_stays_inherited(self):
        """Items already marked inherited should stay that way."""
        items = [
            AgendaItem(item_id=1, title="Consent sub", content="",
                       section_number="8.1.",
                       time_start_ms=50000, time_end_ms=60000,
                       timestamp_inherited=True),
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is True

    def test_no_end_timestamp_not_marked(self):
        """Items without time_end_ms cannot be evaluated; leave them alone."""
        items = [
            AgendaItem(item_id=1, title="No end", content="",
                       section_number="5.1.",
                       time_start_ms=100000, time_end_ms=None),
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is False

    def test_borderline_duration(self):
        """An item just at the threshold should still be marked as brief."""
        items = [
            AgendaItem(item_id=1, title="Edge case", content="",
                       section_number="6.1.",
                       time_start_ms=100000, time_end_ms=160000),  # exactly 60 s
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is True

    def test_just_above_threshold(self):
        """An item just over the threshold should NOT be marked."""
        items = [
            AgendaItem(item_id=1, title="Just over", content="",
                       section_number="6.2.",
                       time_start_ms=100000, time_end_ms=160001),  # 60.001 s
        ]
        _mark_brief_items(items)
        assert items[0].timestamp_inherited is False


# ── AgendaItem.time_start_formatted ──────────────────────────────────


class TestTimeStartFormatted:
    def test_none(self):
        item = AgendaItem(item_id=1, title="", content="", section_number="1.")
        assert item.time_start_formatted is None

    def test_minutes_seconds(self):
        item = AgendaItem(item_id=1, title="", content="", section_number="1.",
                          time_start_ms=275697)
        assert item.time_start_formatted == "4:35"

    def test_hours_minutes_seconds(self):
        item = AgendaItem(item_id=1, title="", content="", section_number="1.",
                          time_start_ms=3661000)
        assert item.time_start_formatted == "1:01:01"
