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
