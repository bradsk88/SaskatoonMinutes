import pytest

from app.summarizer import (
    _format_outcome,
    _format_money,
    _plainify,
    _is_procedural,
    _categorize_topic,
    _is_major_decision,
    _extract_badges,
    _extract_discussion_topics,
    _format_topic,
    _clean_entities,
)


# ── _format_outcome (deferral bug regression) ────────────────────────


class TestFormatOutcome:
    def test_no_vote_no_rec(self):
        assert _format_outcome("", "") == "Discussed"

    def test_rec_only(self):
        assert _format_outcome("", "That the report be received.") == "Recommended"

    def test_carried_unanimously(self):
        assert _format_outcome("CARRIED UNANIMOUSLY", "That X be approved.") == "Approved"

    def test_carried_with_tally(self):
        assert _format_outcome("CARRIED (7 to 4)", "That X be approved.") == "Approved (7-4)"

    def test_defeated_with_tally(self):
        assert _format_outcome("DEFEATED (4 to 7)", "...") == "Defeated (4-7)"

    def test_defeated_no_tally(self):
        assert _format_outcome("DEFEATED", "") == "Defeated"

    def test_explicit_deferred_in_vote(self):
        assert _format_outcome("DEFERRED", "") == "Deferred"

    def test_explicit_tabled_in_vote(self):
        assert _format_outcome("TABLED", "") == "Deferred"

    def test_withdrawn(self):
        assert _format_outcome("WITHDRAWN", "") == "Withdrawn"

    def test_received(self):
        assert _format_outcome("RECEIVED", "") == "Received"

    def test_noted(self):
        assert _format_outcome("NOTED", "") == "Received"

    def test_unknown_passthrough(self):
        assert _format_outcome("SOME OTHER THING", "") == "SOME OTHER THING"

    # ── Deferral regression tests ──

    def test_carried_but_motion_to_defer(self):
        result = _format_outcome(
            "CARRIED UNANIMOUSLY",
            "That the item be DEFERRED to the next meeting.",
        )
        assert result == "Deferred"

    def test_carried_but_motion_to_table(self):
        result = _format_outcome(
            "CARRIED (8 to 3)",
            "That the item be TABLED.",
        )
        assert result == "Deferred"

    def test_carried_with_defer_verb(self):
        result = _format_outcome(
            "CARRIED",
            "That Council defer consideration of this matter.",
        )
        assert result == "Deferred"

    def test_defeated_takes_precedence_over_defer_in_rec(self):
        result = _format_outcome(
            "DEFEATED (3 to 8)",
            "That the item be deferred.",
        )
        assert result == "Defeated (3-8)"


# ── _format_money ────────────────────────────────────────────────────


class TestFormatMoney:
    def test_millions(self):
        assert _format_money("$1,500,000") == "$1.5M"

    def test_round_millions(self):
        assert _format_money("$2,000,000") == "$2M"

    def test_billions(self):
        assert _format_money("$1,000,000,000") == "$1B"

    def test_hundreds_of_thousands(self):
        assert _format_money("$250,000") == "$250K"

    def test_below_threshold(self):
        assert _format_money("$99,999") == "$99,999"

    def test_word_million_passthrough(self):
        assert _format_money("$5 million") == "$5 million"

    def test_zero(self):
        assert _format_money("$0") == "$0"


# ── _plainify ────────────────────────────────────────────────────────


class TestPlainify:
    def test_bylaw_prefix(self):
        result = _plainify("Bylaw No. 9876 - The Zoning Bylaw, 2025 (No. 3)")
        assert "Bylaw No." not in result
        assert "Zoning" in result

    def test_contract_prefix(self):
        result = _plainify("Award of Contract - Road Resurfacing (Contract No. 25-0456)")
        assert "Award of Contract" not in result
        assert "Road Resurfacing" in result

    def test_enquiry_prefix(self):
        result = _plainify("Enquiry - Councillor Smith (March 2025) - Transit Funding")
        assert "Transit Funding" in result

    def test_standing_committee(self):
        result = _plainify("Standing Policy Committee on Planning")
        assert result == "Planning"

    def test_reference_code(self):
        result = _plainify("Simple Title [CC2025-0402]")
        assert result == "Simple Title"

    def test_empty(self):
        assert _plainify("") == ""


# ── _is_procedural ───────────────────────────────────────────────────


class TestIsProcedural:
    def test_call_to_order(self):
        assert _is_procedural("Call to Order") is True

    def test_adoption_of_agenda(self):
        assert _is_procedural("Adoption of Agenda") is True

    def test_consent_agenda(self):
        assert _is_procedural("Consent Agenda") is True

    def test_case_insensitive(self):
        assert _is_procedural("CALL TO ORDER") is True

    def test_non_procedural(self):
        assert _is_procedural("Rezoning Application - 123 Main Street") is False

    def test_declaration_of_conflict(self):
        assert _is_procedural("DECLARATION OF CONFLICT OF INTEREST") is True

    def test_unfinished_business(self):
        assert _is_procedural("UNFINISHED BUSINESS") is True

    def test_giving_notice(self):
        assert _is_procedural("GIVING NOTICE") is True

    def test_motions_notice(self):
        assert _is_procedural("MOTIONS (NOTICE PREVIOUSLY GIVEN)") is True

    def test_legislative_reports_header(self):
        assert _is_procedural("LEGISLATIVE REPORTS") is True

    def test_administrative_reports_header(self):
        assert _is_procedural("ADMINISTRATIVE REPORTS") is True

    def test_other_reports_header(self):
        assert _is_procedural("OTHER REPORTS") is True

    def test_committee_reports_header(self):
        assert _is_procedural("COMMITTEE REPORTS (not on Consent Agenda)") is True

    def test_in_remembrance(self):
        assert _is_procedural("In Remembrance - Hal Lam") is True

    def test_council_members(self):
        assert _is_procedural("Council Members") is True


# ── _categorize_topic ────────────────────────────────────────────────


class TestCategorizeTopic:
    def test_zoning(self):
        cats = _categorize_topic("Rezoning Application", "")
        assert "Zoning & Dev" in cats

    def test_transit(self):
        cats = _categorize_topic("Bus Rapid Transit Update", "")
        assert "Transit" in cats

    def test_two_categories(self):
        cats = _categorize_topic("Homeless Shelter Funding", "affordable housing")
        assert len(cats) == 2

    def test_no_match(self):
        assert _categorize_topic("Approval of Minutes", "") == []

    def test_max_two(self):
        cats = _categorize_topic("homeless shelter rezoning transit", "")
        assert len(cats) <= 2

    def test_active_transport_cycling(self):
        cats = _categorize_topic("Protected Bike Lane Network", "")
        assert "Active Transport" in cats

    def test_active_transport_plan(self):
        cats = _categorize_topic("Active Transportation Plan Improvements", "")
        assert "Active Transport" in cats

    def test_active_transport_pedestrian(self):
        cats = _categorize_topic("Pedestrian Crosswalk Upgrades", "")
        assert "Active Transport" in cats

    def test_traffic_still_matches_roads(self):
        """Traffic category should still match road/intersection items."""
        cats = _categorize_topic("Traffic Signal at Main Intersection", "")
        assert "Traffic" in cats


# ── _is_major_decision ──────────────────────────────────────────────


class TestIsMajorDecision:
    def test_contested(self):
        assert _is_major_decision("Something", "", True) is True

    def test_budget_keyword(self):
        assert _is_major_decision("Budget Amendment", "", False) is True

    def test_dollar_amount(self):
        assert _is_major_decision("Approve $500,000 contract", "", False) is True

    def test_routine(self):
        assert _is_major_decision("Routine Report", "", False) is False


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


# ── _clean_entities ──────────────────────────────────────────────────


class TestCleanEntities:
    def test_html_entities(self):
        assert _clean_entities("foo&#58;bar") == "foo:bar"
        assert _clean_entities("a&amp;b") == "a&b"

    def test_collapses_whitespace(self):
        assert _clean_entities("a   b") == "a b"


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
