"""What the two pages must and must not put on screen.

These are source-level assertions over the templates.  The pages are
plain JavaScript with no test harness, so the guarantees that matter --
that upstream text is escaped, and that a page names the meeting it is
showing -- are pinned here rather than left to review.
"""

import os

from app.speakers import ORGANIZATION_COLOURS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEETING = os.path.join(ROOT, "app", "templates", "meeting.html")
INDEX = os.path.join(ROOT, "app", "templates", "index.html")
CSS = os.path.join(ROOT, "app", "static", "style.css")


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


class TestEveryPaletteSlotIsStyled:
    """``organization_color`` can return any slot, and an unstyled one
    renders as unreadable default text on the card and the detail page.
    Both themes, because the site follows the reader's."""

    def _blocks(self):
        css = _read(CSS)
        dark = css.index("prefers-color-scheme: dark")
        return css[:dark], css[dark:]

    def test_light(self):
        light, _ = self._blocks()
        for slot in range(ORGANIZATION_COLOURS):
            assert f".org-chip-{slot} " in light, slot

    def test_dark(self):
        _, dark = self._blocks()
        for slot in range(ORGANIZATION_COLOURS):
            assert f".org-chip-{slot} " in dark, slot

    def test_a_speaker_with_no_organization_has_a_chip_too(self):
        assert ".org-chip-none" in _read(CSS)

    def test_a_stance_is_coloured_in_both_themes(self):
        """Same reason as the palette: the site follows the reader's theme."""
        light, dark = self._blocks()
        for stance in ("support", "concern"):
            assert f".badge-stance-{stance} " in light, stance
            assert f".badge-stance-{stance} " in dark, stance

    def test_a_stance_is_outlined_and_an_outcome_is_filled(self):
        """The shape is what stops green reading as "council passed it"."""
        css = _read(CSS)
        stance = css[css.index(".badge-stance-support,"):]
        assert "background: transparent;" in stance[:400]
        assert "border: 1px solid currentColor;" in stance[:400]

    def test_every_stance_badge_is_the_same_width(self):
        """"Spoke" is five characters and "Raised concerns" is fifteen.

        Ragged badge edges down a column of speaker rows read as a layout
        fault rather than as three different words.
        """
        css = _read(CSS)
        block = css[css.index(".badge-stance-support,"):]
        block = block[: block.index("}")]
        assert "min-width:" in block
        assert "justify-content: center;" in block

    def test_both_pages_read_the_same_two_fields(self):
        """One place decides the label and the colour; neither page recomputes."""
        for page in (INDEX, MEETING):
            source = _read(page)
            assert "org_color" in source


class TestASpeakerRowIsOneLine:
    """The card answers who had a voice and how they came down on it.

    A name, an organization, a stance. What they argued is on the detail
    page -- on the card it was three or four more lines per speaker, and
    once the archive was populated it was those lines that stopped the
    index being scannable.
    """

    def test_the_row_does_not_announce_a_speaker_it_sits_beneath(self):
        """The phrase under the item they spoke to said nothing new.

        Matched as a rendered string, so the comment explaining why it
        went does not keep the test passing.
        """
        source = _read(INDEX)
        assert "'Spoke to council'" not in source
        assert "`Spoke to council`" not in source

    def test_a_speaker_row_only_ever_sits_beneath_its_item(self):
        """There is no orphan case: a speaker whose item did not make
        the card has no floating row to explain. Their organization is
        still named -- in the digest (ADR 0022)."""
        source = _read(INDEX)
        assert "spoke_to" not in source

    def test_a_speaker_row_does_not_repeat_the_items_takeaway(self):
        """Speaker rows and the \"+N more\" row carry the item's badges
        for the filter, and the takeaway logic read them too -- the
        DEED item's Debate line rendered once under the item and again
        under \"+3 more speakers\"."""
        source = _read(INDEX)
        assert "(t.title_only || t.kind) ? '' : takeawayHtml(t)" in source


class TestIndexCardStaysThin:
    """The index skims; the detail page proves."""

    def test_card_carries_no_category_abbreviations(self):
        """Two-letter codes were unreadable, and hover-only besides."""
        assert "CATEGORY_ICONS" not in _read(INDEX)

    def test_card_shows_a_bounded_number_of_topics(self):
        """Bounded by vertical space now, not a row count."""
        assert "CARD_SPACE_BUDGET" in _read(INDEX)

    def test_a_row_earns_its_slot_with_a_recorded_outcome(self):
        """Standing business does not fill a card. The floor keeps a thin
        agenda from rendering a card with one row on it."""
        source = _read(INDEX)
        assert "CARD_DETAILED_MIN" in source
        assert "'Discussed'" in source
        # The selection needs the ranking the server did, or padding
        # would fall back to agenda order and pick the earliest rows.
        assert "t.rank" in source

    def test_raw_agenda_text_says_it_is_raw_agenda_text(self):
        """Not hover-only, and not a sentence of apology either: seven
        rows in ten are on this path, so the mark is a source label
        inside the clamped block."""
        source = _read(INDEX)
        assert "topic-summary-note" in source
        assert "From the agenda:" in source
        assert "no plain-language description available" not in source

    def test_a_row_offers_the_takeaway_as_text(self):
        """The chip was a badge whose claim only appeared on hover. It is
        the closest thing the card has to "why this is worth opening", so
        it is prose now -- and the RSS feed reads the same rows."""
        source = _read(INDEX)
        assert "TAKEAWAY_ORDER" in source
        assert "topic-takeaway" in source
        # The sentence itself, not the category label alone.
        block = source[source.index("function takeawayHtml"):]
        block = block[: block.index("\n    function ")]
        assert "best.tooltip" in block

    def test_the_takeaway_is_bounded_and_the_summary_pays_for_it(self):
        """Chip text runs to 122 characters, which is three lines at card
        width -- so the takeaway gets three and the description drops to
        two beneath it. The row's height is unchanged."""
        css = _read(CSS)
        block = css[css.index(".topic-takeaway {"):]
        assert "-webkit-line-clamp: 3" in block[: block.index("}")]
        block = css[css.index(".topic-summary-short {"):]
        assert "-webkit-line-clamp: 2" in block[: block.index("}")]
        assert "topic-summary-short" in _read(INDEX)

    def test_a_card_row_is_bounded_in_height(self):
        """A card is read to choose a meeting, not to read the item."""
        css = _read(CSS)
        block = css[css.index(".topic-summary {"):]
        assert "-webkit-line-clamp: 3" in block[: block.index("}")]
        block = css[css.index(".topic-name > span:first-child {"):]
        assert "-webkit-line-clamp: 2" in block[: block.index("}")]

    def test_the_card_does_not_offer_the_same_link_twice(self):
        """The play button pointed at the row's own href."""
        assert "topic-play-link" not in _read(INDEX)
        assert "topic-play-link" not in _read(CSS)

    def test_a_card_outcome_does_not_shout(self):
        css = _read(CSS)
        block = css[css.index(".topics-table .topic-badge {"):]
        assert "text-transform: none" in block[: block.index("}")]

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


class TestTheHeaderCountMatchesThePage:
    """The header used to say "43 agenda items" above 73 rendered cards.

    One number could not be honest about a page with three weights on
    it, so the header names each: what was discussed, and what passed in
    the consent block.  Both are counted once, in Python, from the same
    list the page renders.
    """

    def test_header_reads_the_python_side_counts(self):
        source = _read(MEETING)
        assert "data.discussed_count" in source
        assert "data.consent_count" in source

    def test_an_older_build_without_the_counts_still_reports_a_size(self):
        assert "data.item_count" in _read(MEETING)

    def test_card_reads_a_python_side_count(self):
        assert "total_items" in _read(INDEX)


class TestRoutineRowsAreDemoted:
    """Every meeting carries the same scaffolding, and it says nothing.

    Call to order, conflict declarations, adjournment, and the headings
    that stand over nothing were 26 of the 73 rows on June 24, each drawn
    at the same weight as a $95M decision.  They keep their place and
    their play buttons, in one closed strip.
    """

    def test_the_page_splits_routine_rows_out_of_the_cards(self):
        source = _read(MEETING)
        assert "i.is_routine" in source
        assert "el.className = 'routine-strip'" in source

    def test_the_strip_starts_closed(self):
        """It is a <details> with no open attribute."""
        source = _read(MEETING)
        assert "createElement('details')" in source
        assert "el.open = true" not in source

    def test_a_routine_row_keeps_its_jump_to_the_video(self):
        source = _read(MEETING)
        block = source[source.index("function buildRoutineStripEl"):]
        block = block[:block.index("function renderAgendaItems")]
        assert "seekVideo(" in block

    def test_a_routine_row_escapes_its_upstream_text(self):
        source = _read(MEETING)
        block = source[source.index("function buildRoutineStripEl"):]
        block = block[:block.index("function renderAgendaItems")]
        assert "${escapeHtml(item.title)}" in block
        assert "${escapeHtml(item.section_number)}" in block

    def test_the_strip_is_styled(self):
        css = _read(CSS)
        assert ".routine-strip" in css
        assert ".routine-row" in css


class TestHeadingsAreRulesNotCards:
    """"Standing Policy Committee on Finance" is a name for the group
    below it. Drawn as a card it wore a NOT DISCUSSED badge and a topic
    chip, and read like a decision nobody had examined."""

    def test_a_heading_gets_its_own_element(self):
        source = _read(MEETING)
        assert "if (item.is_heading) return buildHeadingEl(item);" in source

    def test_a_heading_escapes_its_upstream_text(self):
        source = _read(MEETING)
        block = source[source.index("function buildHeadingEl"):]
        block = block[:block.index("function buildItemEl")]
        assert "${escapeHtml(item.title)}" in block

    def test_a_heading_is_styled_as_a_rule(self):
        assert ".agenda-heading" in _read(CSS)

    def test_a_rule_over_nothing_is_dropped(self):
        """Video order can separate a heading from the items it names."""
        source = _read(MEETING)
        assert "return !!next && !next.is_heading;" in source


class TestConsentItemsShowWhatTheyDecided:
    """A consent item's summary was written, cached, and then dropped.

    The card showed a list of PDFs where the index card showed the
    sentence -- for a $1.2M loan to TCU Place, among sixteen others.
    Passing in one motion says how council decided, not whether what
    they decided matters.
    """

    def test_a_consent_item_is_not_denied_its_summary(self):
        source = _read(MEETING)
        assert "!isConsent && !!item.summary" not in source
        assert "const hasChips = !!item.summary" in source

    def test_a_consent_item_still_says_it_was_not_debated(self):
        assert "badge-consent" in _read(MEETING)


class TestConsentItemsAreNotLinkedToTheWrongAudio:
    """A consent item's timestamp is its parent's.

    The detail page already refuses to offer a jump for one; the card
    used to offer both a play link and a ``?t=`` deep link, which sent a
    reader to the clerk reading the consent block into the record.  The
    play link is gone entirely now -- it pointed at the row's own href
    -- so the row link is the only path left to guard.
    """

    def test_no_timestamp_deep_link_for_a_consent_topic(self):
        source = _read(INDEX)
        block = _href_block(source)
        assert "!t.is_consent" in block


def _href_block(source):
    """The card's link-building block, as source text."""
    start = source.index("let detailHref = openHref")
    return source[start : source.index("// A guest speaker's row", start)]


class TestACardRowLinksToItsOwnItem:
    """The card used to send every row to the top of the meeting page.

    On a 70-row agenda that is a reader hunting for the thing they
    clicked.  ``#item-<id>`` does not depend on video position, so it
    works for the consent rows that ``?t=`` never could -- the ones the
    card is most likely to be the only mention of.
    """

    def test_the_row_carries_an_anchor(self):
        assert "#item-${t.item_id}" in _href_block(_read(INDEX))

    def test_a_recess_is_not_linked(self):
        """Every recess shares item_id -1, so it has no address."""
        assert "t.item_id >= 0" in _href_block(_read(INDEX))

    def test_a_topic_row_carries_the_id_the_link_needs(self):
        from app.summarizer import extract_meeting_topics
        items = [{
            "item_id": 58, "title": "Wildwood Golf Course", "content": "",
            "section_number": "8.1", "recommendation": "That it be approved",
            "vote_result": "Carried (6 to 5)",
        }]
        rows = extract_meeting_topics(items, "City Council")
        assert rows[0]["item_id"] == 58

    def test_the_page_does_not_scroll_twice_when_both_arrive(self):
        """A row sends ?t= and #item- together; the anchor already scrolled."""
        source = _read(MEETING)
        block = source[source.index("loadMeeting().then("):]
        assert "window.location.hash.startsWith('#item-')" in block


class TestACardRowIsClickableForItsWholeHeight:
    """A card exists to be opened.

    The row's vertical space used to sit on the table cell, outside the
    link, so most of a row's height did nothing when clicked and only the
    text lit up on hover. The space belongs to the link instead; the cell
    keeps a small gutter so two rows' hover states do not touch.
    """

    def _rule(self, css, selector):
        """The rule for *selector* exactly.

        Anchored to the start of a line: ``.topic-speaker-row
        .topic-content`` also contains ``.topic-content``, and matching it
        would test the wrong rule.
        """
        start = css.index("\n" + selector)
        return css[start : css.index("}", start)]

    def test_the_cell_no_longer_owns_the_rows_height(self):
        rule = self._rule(_read(CSS), ".topics-table td {")
        assert "padding: 0.9rem" not in rule

    def test_the_link_owns_it_instead(self):
        rule = self._rule(_read(CSS), ".topic-content {")
        assert "padding:" in rule

    def test_the_link_reaches_the_cells_edges(self):
        """Negative horizontal margin, so the text does not move."""
        rule = self._rule(_read(CSS), ".topic-content {")
        assert "margin: 0 -" in rule

