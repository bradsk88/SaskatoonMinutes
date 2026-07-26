"""What the two pages must and must not put on screen.

These are source-level assertions over the templates.  The pages are
plain JavaScript with no test harness, so the guarantees that matter --
that upstream text is escaped, and that a page names the meeting it is
showing -- are pinned here rather than left to review.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEETING = os.path.join(ROOT, "app", "templates", "meeting.html")
INDEX = os.path.join(ROOT, "app", "templates", "index.html")


def _read(path: str) -> str:
    return open(path, encoding="utf-8").read()


class TestUpstreamTextIsEscaped:
    """Agenda titles and error strings come from eSCRIBE.

    They were interpolated into ``innerHTML`` raw while every other field
    on the page went through an escaper.
    """

    def test_item_title_is_escaped(self):
        assert "${escapeHtml(item.title)}" in _read(MEETING)
        assert "${item.title}" not in _read(MEETING)

    def test_item_section_number_is_escaped(self):
        assert "${escapeHtml(item.section_number)}" in _read(MEETING)
        assert "${item.section_number}" not in _read(MEETING)

    def test_error_text_is_escaped(self):
        assert "${escapeHtml(data.error)}" in _read(MEETING)
        assert "${data.error}" not in _read(MEETING)

    def test_card_title_is_escaped(self):
        assert "${escapeAttr(cardTitle)}" in _read(INDEX)
        assert "${cardTitle}" not in _read(INDEX)

    def test_index_escaper_covers_angle_brackets(self):
        """escapeAttr is used for text nodes too, not only attributes."""
        source = _read(INDEX)
        block = source[source.index("function escapeAttr"):]
        block = block[: block.index("}")]
        for char in ("&", '"', "'", "<", ">"):
            assert f"/{char}/g" in block


class TestDetailPageNamesTheMeeting:
    """Every detail page used to read "City Council Meeting"."""

    def test_no_hardcoded_body_name(self):
        assert "City Council Meeting</h1>" not in _read(MEETING)
        assert "<h1>City Council Meeting" not in _read(MEETING)

    def test_header_reads_the_meeting_title(self):
        assert "data.title" in _read(MEETING)

    def test_header_reads_the_date_and_start_time(self):
        source = _read(MEETING)
        assert "data.date" in source
        assert "data.start_time" in source

    def test_tab_title_is_set_from_the_meeting(self):
        """A bookmark and a shared link show the tab title, not the page."""
        assert "document.title" in _read(MEETING)

    def test_unnamed_meeting_does_not_borrow_a_body_name(self):
        source = _read(MEETING)
        assert "|| 'Meeting'" in source


class TestDetailPageExplainsItselfWithoutHover:
    def test_consent_items_are_explained_in_text(self):
        """The badge's tooltip cannot be opened on a touch screen."""
        source = _read(MEETING)
        assert "agenda-legend" in source
        assert "without individual debate" in source


class TestIndexCardStaysThin:
    """The index skims; the detail page proves."""

    def test_card_carries_no_category_abbreviations(self):
        """Two-letter codes were unreadable, and hover-only besides."""
        assert "CATEGORY_ICONS" not in _read(INDEX)

    def test_card_shows_a_bounded_number_of_topics(self):
        assert "CARD_TOPICS" in _read(INDEX)

    def test_a_row_earns_its_slot_with_a_recorded_outcome(self):
        """Standing business does not fill a card. The floor keeps a thin
        agenda from rendering a card with one row on it."""
        source = _read(INDEX)
        assert "CARD_TOPICS_MIN" in source
        assert "'Discussed'" in source
        # The selection needs the ranking the server did, or padding
        # would fall back to agenda order and pick the earliest rows.
        assert "t.rank" in source

    def test_legacy_summary_caveat_is_not_hover_only(self):
        """A tooltip does not exist on a touch screen."""
        source = _read(INDEX)
        assert "topic-summary-note" in source
        assert "Older summary" in source

    def test_filter_states_its_real_range(self):
        """It filters loaded meetings, not the archive."""
        assert "filter-scope-note" in _read(INDEX)

    def test_a_card_whose_topics_failed_can_still_be_opened(self):
        """The footer moved inside the topics container, which is where a
        load failure also renders.  Every branch appends the footer, so a
        missing summary never costs the reader the meeting itself."""
        source = _read(INDEX)
        # Both message branches go through the shared renderer, and that
        # renderer is the one that appends the footer.
        assert source.count("renderTopicsMessage(") >= 4
        block = source[source.index("function renderTopicsMessage"):]
        block = block[: block.index("function renderTopicsInContainer")]
        assert "cardFooterHtml(" in block


class TestTheTwoPagesAgreeOnMeetingSize:
    """The card says "N other items" and the header says "N agenda items".

    Both must be the same count, produced once in Python.  Counting the
    detail page's rendered rows instead includes recesses and section
    headers, which made a 43-item meeting report 73.
    """

    def test_header_prefers_the_shared_count(self):
        source = _read(MEETING)
        assert "data.item_count" in source

    def test_card_and_header_both_read_a_python_side_count(self):
        assert "total_items" in _read(INDEX)


class TestConsentItemsAreNotLinkedToTheWrongAudio:
    """A consent item's timestamp is its parent's.

    The detail page already refuses to offer a jump for one; the card
    used to offer both a play link and a ``?t=`` deep link, which sent a
    reader to the clerk reading the consent block into the record.
    """

    def test_no_play_link_for_a_consent_topic(self):
        source = _read(INDEX)
        block = source[source.index("let playHtml = '';"):]
        block = block[: block.index("}")]
        assert "!t.is_consent" in block

    def test_no_timestamp_deep_link_for_a_consent_topic(self):
        source = _read(INDEX)
        block = source[source.index("let detailHref = openHref"):]
        block = block[: block.index("}")]
        assert "!t.is_consent" in block
