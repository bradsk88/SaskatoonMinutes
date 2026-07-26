"""The on-disk / on-page contract for an ItemSummary.

``app/templates/meeting.html`` reads ``item.summary.description`` and
``item.summary.chips[].category|text``.  ``scripts/build_site.py`` builds
that object from ``ItemSummary.to_dict()``.  Nothing type-checks across
that boundary, so the keys are pinned here.
"""

import re
import os

from app.models import Chip, ItemSummary

TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "templates", "meeting.html",
)


class TestSerializedKeys:
    def test_to_dict_exposes_exactly_what_the_page_reads(self):
        summary = ItemSummary(
            description="Raises transit fines to $250.",
            chips=[Chip(category="Outcome", text="Approved")],
        )
        assert summary.to_dict() == {
            "description": "Raises transit fines to $250.",
            "chips": [{"category": "Outcome", "text": "Approved"}],
        }

    def test_legacy_summary_serializes_a_null_description(self):
        """The page uses a null description to mark a summary as older."""
        legacy = ItemSummary.from_dict([{"category": "Outcome", "text": "Approved"}])
        assert legacy.to_dict()["description"] is None


class TestTemplateReadsTheseKeys:
    def test_template_reads_summary_description(self):
        assert "summary.description" in open(TEMPLATE, encoding="utf-8").read()

    def test_template_reads_summary_chips(self):
        assert "summary.chips" in open(TEMPLATE, encoding="utf-8").read()

    def test_template_no_longer_reads_the_old_key(self):
        """chip_summaries was the pre-aggregate shape."""
        assert "chip_summaries" not in open(TEMPLATE, encoding="utf-8").read()

    def test_template_never_interpolates_the_summary_object_as_text(self):
        """``${item.summary}`` renders "[object Object]" — the object has
        no text form.  It must always be read field by field."""
        source = open(TEMPLATE, encoding="utf-8").read()
        assert "${item.summary}" not in source

    def test_template_reads_the_extractive_string_from_its_own_key(self):
        source = open(TEMPLATE, encoding="utf-8").read()
        assert "item.extractive_summary" in source

    def test_template_chip_groups_match_the_category_list(self):
        """CHIP_GROUP in the template mirrors CATEGORY_GROUP in Python."""
        from app.item_categorizer import CATEGORY_GROUP

        source = open(TEMPLATE, encoding="utf-8").read()
        block = source[source.index("const CHIP_GROUP"):]
        block = block[: block.index("};")]
        in_template = set(re.findall(r'"([^"]+)":\s*"(?:decision|money|context|voices|impact|future)"', block))
        assert in_template == set(CATEGORY_GROUP), (
            f"only in template: {in_template - set(CATEGORY_GROUP)}; "
            f"only in Python: {set(CATEGORY_GROUP) - in_template}"
        )


BUILD_SITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "build_site.py",
)


class TestTheTwoProducersUseDistinctKeys:
    """Two things write a per-item summary: the written ItemSummary (an
    object) and the extractive backend (a string).  They shared the
    ``summary`` key once, and the page printed "[object Object]"."""

    def test_extractive_backend_writes_its_own_key(self):
        from app.summarizer import summarize_agenda_items

        items = summarize_agenda_items(
            [{"title": "Transit Fines", "content": "A long enough sentence about transit fines in the city."}],
            "City Council Meeting",
        )
        assert isinstance(items[0]["extractive_summary"], str)
        assert "summary" not in items[0]

    def test_extractive_backend_leaves_an_item_summary_alone(self):
        from app.summarizer import summarize_agenda_items

        written = ItemSummary(
            description="Raises transit fines to $250.",
            chips=[Chip(category="Outcome", text="Approved")],
        ).to_dict()
        items = summarize_agenda_items(
            [{"title": "Transit Fines", "content": "Some agenda text.", "summary": written}],
            "City Council Meeting",
        )
        assert items[0]["summary"] == written

    def test_procedural_items_also_use_the_extractive_key(self):
        from app.summarizer import summarize_agenda_items

        items = summarize_agenda_items([{"title": "ADOPTION OF MINUTES"}], "Meeting")
        assert items[0]["extractive_summary"] == "Procedural item."
        assert "summary" not in items[0]

    def test_static_build_writes_the_object_to_the_summary_key(self):
        """The static path is the other producer; it owns ``summary``."""
        source = open(BUILD_SITE, encoding="utf-8").read()
        assert 'item["summary"] = summary.to_dict()' in source
        assert 'item["extractive_summary"]' not in source


INDEX_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "templates", "index.html",
)


class TestIndexCardReadsTheTopicKeys:
    """The index cards are built in JavaScript from ``_format_topic``
    output, so those keys are pinned here too."""

    def _index(self) -> str:
        return open(INDEX_TEMPLATE, encoding="utf-8").read()

    def test_card_marks_a_non_description_summary(self):
        assert "summary_is_description" in self._index()

    def test_card_colours_chip_badges_by_group(self):
        assert "chip_group" in self._index()

    def test_chip_group_classes_exist_in_the_stylesheet(self):
        from app.item_categorizer import CATEGORY_GROUP

        css_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "static", "style.css",
        )
        css = open(css_path, encoding="utf-8").read()
        for group in set(CATEGORY_GROUP.values()):
            assert f".chip-{group}" in css

    def test_raw_summary_class_is_styled(self):
        css_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app", "static", "style.css",
        )
        assert ".topic-summary-raw" in open(css_path, encoding="utf-8").read()
