import pytest

from app.summarizer import (
    _extract_badges,
    _extract_discussion_topics,
    _format_topic,
)


# ── _extract_badges ──────────────────────────────────────────────────


class TestLongDiscussedBadgeCount:
    """Items discussed for 30+ minutes should have at least 2 badges so
    the index page conveys meaningful information beyond just the outcome.

    Reproduces the Mar 3 2026 SPC-Transportation meeting where the active
    transportation improvements item had ~1 hour of discussion but only
    showed 'Approved' and 'Traffic'.
    """

    def test_active_transport_item_gets_enough_badges(self):
        """An active-transportation item should match multiple categories."""
        item = {
            "title": "Active Transportation Plan Improvements",
            "recommendation": "That the report be received.",
            "content": "",
            "time_start_ms": 0,
            "time_end_ms": 3_600_000,  # 1 hour
        }
        badges = _extract_badges(item)
        assert len(badges) >= 2, (
            f"Expected >= 2 badges for a 1-hour discussion item, "
            f"got {len(badges)}: {badges}"
        )

    def test_cycling_infrastructure_item(self):
        """A cycling infrastructure item should get category + detail badges."""
        item = {
            "title": "Cycling Network - Protected Bike Lanes",
            "recommendation": "That $2.5 million be approved for "
                              "cycling infrastructure on 25th Street East.",
            "content": "",
            "time_start_ms": 0,
            "time_end_ms": 2_400_000,  # 40 min
        }
        badges = _extract_badges(item)
        assert len(badges) >= 2, (
            f"Expected >= 2 badges, got {len(badges)}: {badges}"
        )

    def test_minutes_content_generates_extra_badges(self):
        """Rich minutes text should produce badges even when title is sparse."""
        item = {
            "title": "Report on Transportation",
            "recommendation": "That the report be received.",
            "content": (
                "Director of Transportation presented the report and "
                "responded to questions related to cycling infrastructure, "
                "$2.5 million funding strategy, and pedestrian safety."
            ),
            "time_start_ms": 0,
            "time_end_ms": 2_400_000,  # 40 min
        }
        badges = _extract_badges(item)
        types = {b["type"] for b in badges}
        # Minutes content mentions cycling → Active Transport category
        assert "cat-active-transport" in types
        # Minutes content mentions $2.5 million → money badge
        assert "money" in types
        # Discussion topics extracted from "related to ..." clause
        assert "topic" in types
        assert len(badges) >= 4

    def test_real_transportation_minutes(self):
        """Reproduces the Mar 3 2026 SPC-Transportation active transport item."""
        item = {
            "title": "Active Transportation Plan Improvements",
            "recommendation": "That the report be received.",
            "content": (
                "Director of Transportation Magus presented the report "
                "with a PowerPoint and responded to questions of Committee "
                "related to traffic volumes and demand, funding strategy, "
                "snow removal and winter operations, project timing and "
                "consideration of development in area."
            ),
            "time_start_ms": 0,
            "time_end_ms": 3_600_000,  # 1 hour
        }
        badges = _extract_badges(item)
        types = {b["type"] for b in badges}
        topic_labels = [b["label"] for b in badges if b["type"] == "topic"]
        # Should have category badges + topic badges from minutes
        assert "cat-active-transport" in types
        assert "topic" in types
        assert len(topic_labels) >= 2
        assert len(badges) >= 4

    def test_short_item_not_required(self):
        """Short items are not subject to the same expectation."""
        item = {
            "title": "Approval of Minutes",
            "recommendation": "",
            "content": "",
            "time_start_ms": 0,
            "time_end_ms": 30_000,  # 30 seconds
        }
        badges = _extract_badges(item)
        # No assertion on count — short items may have 0 badges


class TestExtractDiscussionTopics:
    def test_related_to_clause(self):
        topics = _extract_discussion_topics(
            "Director X responded to questions related to "
            "traffic volumes and demand, funding strategy, "
            "snow removal and winter operations."
        )
        assert len(topics) >= 2
        assert "Traffic volumes and demand" in topics
        assert "Funding strategy" in topics

    def test_regarding_clause(self):
        topics = _extract_discussion_topics(
            "Staff responded to questions regarding cost recovery, "
            "timing and scope of the review."
        )
        assert "Cost recovery" in topics

    def test_empty_content(self):
        assert _extract_discussion_topics("") == []

    def test_no_clause(self):
        assert _extract_discussion_topics("The meeting adjourned at 3:25 p.m.") == []

    def test_max_three_topics(self):
        topics = _extract_discussion_topics(
            "Questions related to a, b, c, d, e."
        )
        assert len(topics) <= 3

    def test_skips_tiny_fragments(self):
        topics = _extract_discussion_topics(
            "Questions related to a, real discussion topic."
        )
        # "a" is too short (< 3 chars) and should be skipped
        labels = [t.lower() for t in topics]
        assert "a" not in labels


class TestExtractBadges:
    def test_money_badge(self):
        item = {"title": "Approve $1,500,000 for roads", "recommendation": "", "content": ""}
        badges = _extract_badges(item)
        money = [b for b in badges if b["type"] == "money"]
        assert len(money) >= 1
        assert "$1.5M" in money[0]["label"]

    def test_category_badge(self):
        item = {"title": "Rezoning at 123 Main", "recommendation": "", "content": ""}
        badges = _extract_badges(item)
        cat = [b for b in badges if b["type"].startswith("cat-")]
        assert len(cat) >= 1

    def test_person_badge(self):
        item = {"title": "Councillor B. Dubois - Motion on Parks", "recommendation": "", "content": ""}
        badges = _extract_badges(item)
        people = [b for b in badges if b["type"] == "person"]
        assert len(people) >= 1

    def test_location_badge_address(self):
        item = {"title": "Construction at 456 Broadway Avenue East", "recommendation": "", "content": ""}
        badges = _extract_badges(item)
        locs = [b for b in badges if b["type"] == "location"]
        assert len(locs) >= 1
        assert "Broadway Avenue East" in locs[0]["label"]

    def test_location_badge_neighbourhood(self):
        item = {"title": "Development in Nutana", "recommendation": "", "content": ""}
        badges = _extract_badges(item)
        locs = [b for b in badges if b["type"] == "location"]
        assert len(locs) >= 1
        assert locs[0]["label"] == "Nutana"


# ── End-to-end deferral via _format_topic ────────────────────────────


class TestFormatTopicDeferral:
    def test_deferred_item(self):
        item = {
            "title": "Rezoning Application - 123 Main Street",
            "recommendation": "That the matter be deferred to the next regular meeting.",
            "vote_result": "CARRIED (8 to 3)",
            "is_contested": True,
            "section_number": "7.1.",
            "timestamp_inherited": False,
            "content": "",
        }
        topic = _format_topic(item)
        assert topic["outcome"] == "Deferred"
        assert topic["is_contested"] is True


# ── The card uses the ItemSummary ────────────────────────────────────


def _item(**over) -> dict:
    base = {
        "title": "Transit Fare Bylaw Amendment",
        "recommendation": "That the bylaw be approved.",
        "vote_result": "CARRIED",
        "is_contested": False,
        "section_number": "7.1.",
        "timestamp_inherited": False,
        "content": (
            "The Standing Policy Committee on Transportation considered the "
            "attached report of the General Manager, Community Services "
            "Department dated April 2 2026, and responded to questions."
        ),
    }
    base.update(over)
    return base


class TestTopicSummaryPrefersTheDescription:
    def test_description_is_used_whole(self):
        """220 characters is the Description's own bound — the card does
        not re-truncate a sentence written to be read in full."""
        description = "Raises the fine for fare evasion to $250 and " + "x" * 150
        topic = _format_topic(_item(summary={"description": description, "chips": []}))
        assert topic["summary"] == description
        assert topic["summary_is_description"] is True

    def test_legacy_summary_falls_back_to_clipped_agenda_text(self):
        topic = _format_topic(_item(summary={"description": None, "chips": []}))
        assert topic["summary"].startswith("The Standing Policy Committee")
        assert topic["summary"].endswith("...")
        assert topic["summary_is_description"] is False

    def test_no_summary_at_all_behaves_like_legacy(self):
        topic = _format_topic(_item())
        assert topic["summary_is_description"] is False

    def test_a_string_summary_does_not_crash(self):
        """Payloads cached before the extractive split still hold a string."""
        topic = _format_topic(_item(summary="Procedural item."))
        assert topic["summary_is_description"] is False


class TestChipBadges:
    def test_interpretive_chips_become_badges(self):
        topic = _format_topic(_item(summary={
            "description": "Raises transit fines.",
            "chips": [
                {"category": "Dissenting View", "text": "Councillor X objected."},
                {"category": "Equity Impact", "text": "Low-income riders affected."},
            ],
        }))
        chips = [b for b in topic["badges"] if b["type"] == "chip"]
        assert [b["label"] for b in chips] == ["Dissenting View", "Equity Impact"]
        assert chips[0]["tooltip"] == "Councillor X objected."
        assert chips[0]["chip_group"] == "decision"

    def test_outcome_chips_are_left_to_the_outcome_badge(self):
        topic = _format_topic(_item(summary={
            "description": "Raises transit fines.",
            "chips": [
                {"category": "Outcome", "text": "Approved"},
                {"category": "Vote Breakdown", "text": "11 for, 0 against"},
            ],
        }))
        assert not [b for b in topic["badges"] if b["type"] == "chip"]

    def test_at_most_three_chip_badges(self):
        topic = _format_topic(_item(summary={
            "description": "d",
            "chips": [
                {"category": c, "text": c}
                for c in ("Dissenting View", "Equity Impact", "Public Sentiment",
                          "Promise Made", "Legal Risk Flagged")
            ],
        }))
        assert len([b for b in topic["badges"] if b["type"] == "chip"]) == 3

    def test_a_repeated_category_appears_once(self):
        topic = _format_topic(_item(summary={
            "description": "d",
            "chips": [
                {"category": "Who's Affected", "text": "Riders"},
                {"category": "Who's Affected", "text": "Taxpayers"},
            ],
        }))
        assert len([b for b in topic["badges"] if b["type"] == "chip"]) == 1


class TestChipsRaiseRanking:
    def test_an_item_with_chips_outranks_an_identical_item_without(self):
        from app.summarizer import extract_meeting_topics

        plain = _item(title="Rezoning Application - 123 Main Street")
        chipped = _item(
            title="Rezoning Application - 456 Side Street",
            summary={"description": "d", "chips": [
                {"category": "Dissenting View", "text": "Councillor X objected."},
                {"category": "Legal Risk Flagged", "text": "Liability raised."},
            ]},
        )
        topics = extract_meeting_topics([plain, chipped], "Council", max_topics=1)
        assert topics[0]["topic"].endswith("456 Side Street")


class TestChipsDisplaceRegexMoneyBadges:
    def test_one_money_badge_survives_when_chips_are_present(self):
        money_item = _item(
            recommendation="That $4,700,000 be approved and $700,000 be spent.",
            summary={"description": "d", "chips": [
                {"category": "Cost & Funding", "text": "$4.7M for the centre."},
            ]},
        )
        badges = _format_topic(money_item)["badges"]
        assert len([b for b in badges if b["type"] == "money"]) == 1

    def test_all_money_badges_survive_without_chips(self):
        money_item = _item(
            recommendation="That $4,700,000 be approved and $700,000 be spent.",
        )
        badges = _format_topic(money_item)["badges"]
        assert len([b for b in badges if b["type"] == "money"]) == 2
