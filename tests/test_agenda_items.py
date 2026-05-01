"""Tests for app.agenda_items — domain interpretation of agenda items."""

from app.agenda_items import (
    categorize_topic,
    format_outcome,
    is_major_decision,
    is_procedural,
)


# ── format_outcome ───────────────────────────────────────────────────


class TestFormatOutcome:
    def test_no_vote_no_rec(self):
        assert format_outcome("", "") == "Discussed"

    def test_rec_only(self):
        assert format_outcome("", "That the report be received.") == "Recommended"

    def test_carried_unanimously(self):
        assert format_outcome("CARRIED UNANIMOUSLY", "That X be approved.") == "Approved"

    def test_carried_with_tally(self):
        assert format_outcome("CARRIED (7 to 4)", "That X be approved.") == "Approved (7-4)"

    def test_defeated_with_tally(self):
        assert format_outcome("DEFEATED (4 to 7)", "...") == "Defeated (4-7)"

    def test_defeated_no_tally(self):
        assert format_outcome("DEFEATED", "") == "Defeated"

    def test_explicit_deferred_in_vote(self):
        assert format_outcome("DEFERRED", "") == "Deferred"

    def test_explicit_tabled_in_vote(self):
        assert format_outcome("TABLED", "") == "Deferred"

    def test_withdrawn(self):
        assert format_outcome("WITHDRAWN", "") == "Withdrawn"

    def test_received(self):
        assert format_outcome("RECEIVED", "") == "Received"

    def test_noted(self):
        assert format_outcome("NOTED", "") == "Received"

    def test_unknown_passthrough(self):
        assert format_outcome("SOME OTHER THING", "") == "SOME OTHER THING"

    # ── Deferral regression tests ──

    def test_carried_but_motion_to_defer(self):
        result = format_outcome(
            "CARRIED UNANIMOUSLY",
            "That the item be DEFERRED to the next meeting.",
        )
        assert result == "Deferred"

    def test_carried_but_motion_to_table(self):
        result = format_outcome(
            "CARRIED (8 to 3)",
            "That the item be TABLED.",
        )
        assert result == "Deferred"

    def test_carried_with_defer_verb(self):
        result = format_outcome(
            "CARRIED",
            "That Council defer consideration of this matter.",
        )
        assert result == "Deferred"

    def test_defeated_takes_precedence_over_defer_in_rec(self):
        result = format_outcome(
            "DEFEATED (3 to 8)",
            "That the item be deferred.",
        )
        assert result == "Defeated (3-8)"


# ── is_procedural ────────────────────────────────────────────────────


class TestIsProcedural:
    def test_call_to_order(self):
        assert is_procedural("Call to Order") is True

    def test_adoption_of_agenda(self):
        assert is_procedural("Adoption of Agenda") is True

    def test_consent_agenda(self):
        assert is_procedural("Consent Agenda") is True

    def test_case_insensitive(self):
        assert is_procedural("CALL TO ORDER") is True

    def test_non_procedural(self):
        assert is_procedural("Rezoning Application - 123 Main Street") is False

    def test_declaration_of_conflict(self):
        assert is_procedural("DECLARATION OF CONFLICT OF INTEREST") is True

    def test_unfinished_business(self):
        assert is_procedural("UNFINISHED BUSINESS") is True

    def test_giving_notice(self):
        assert is_procedural("GIVING NOTICE") is True

    def test_motions_notice(self):
        assert is_procedural("MOTIONS (NOTICE PREVIOUSLY GIVEN)") is True

    def test_legislative_reports_header(self):
        assert is_procedural("LEGISLATIVE REPORTS") is True

    def test_administrative_reports_header(self):
        assert is_procedural("ADMINISTRATIVE REPORTS") is True

    def test_other_reports_header(self):
        assert is_procedural("OTHER REPORTS") is True

    def test_committee_reports_header(self):
        assert is_procedural("COMMITTEE REPORTS (not on Consent Agenda)") is True

    def test_in_remembrance(self):
        assert is_procedural("In Remembrance - Hal Lam") is True

    def test_council_members(self):
        assert is_procedural("Council Members") is True


# ── categorize_topic ─────────────────────────────────────────────────


class TestCategorizeTopic:
    def test_zoning(self):
        cats = categorize_topic("Rezoning Application", "")
        assert "Zoning & Dev" in cats

    def test_transit(self):
        cats = categorize_topic("Bus Rapid Transit Update", "")
        assert "Transit" in cats

    def test_two_categories(self):
        cats = categorize_topic("Homeless Shelter Funding", "affordable housing")
        assert len(cats) == 2

    def test_no_match(self):
        assert categorize_topic("Approval of Minutes", "") == []

    def test_max_two(self):
        cats = categorize_topic("homeless shelter rezoning transit", "")
        assert len(cats) <= 2

    def test_active_transport_cycling(self):
        cats = categorize_topic("Protected Bike Lane Network", "")
        assert "Active Transport" in cats

    def test_active_transport_plan(self):
        cats = categorize_topic("Active Transportation Plan Improvements", "")
        assert "Active Transport" in cats

    def test_active_transport_pedestrian(self):
        cats = categorize_topic("Pedestrian Crosswalk Upgrades", "")
        assert "Active Transport" in cats

    def test_traffic_still_matches_roads(self):
        """Traffic category should still match road/intersection items."""
        cats = categorize_topic("Traffic Signal at Main Intersection", "")
        assert "Traffic" in cats


# ── is_major_decision ───────────────────────────────────────────────


class TestIsMajorDecision:
    def test_contested(self):
        assert is_major_decision("Something", "", True) is True

    def test_budget_keyword(self):
        assert is_major_decision("Budget Amendment", "", False) is True

    def test_dollar_amount(self):
        assert is_major_decision("Approve $500,000 contract", "", False) is True

    def test_routine(self):
        assert is_major_decision("Routine Report", "", False) is False
