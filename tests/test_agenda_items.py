"""Tests for app.agenda_items — domain interpretation of agenda items."""

from app.agenda_items import (
    categorize_topic,
    count_agenda_items,
    count_consent_items,
    count_discussed_items,
    format_outcome,
    is_major_decision,
    is_procedural,
    mark_routine_rows,
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

    # ── Public-hearing first readings ──

    def test_consider_bylaw_is_not_an_approval(self):
        """The vote is on first reading; the application is not decided."""
        result = format_outcome(
            "CARRIED UNANIMOUSLY (10 to 0)",
            "That City Council consider Bylaw No. 10169.",
        )
        assert result == "First reading passed (10-0)"

    def test_spelled_out_first_reading_motion(self):
        result = format_outcome(
            "CARRIED UNANIMOUSLY",
            "That Bylaw No. 10169 be given first reading.",
        )
        assert result == "First reading passed"

    def test_a_defeated_first_reading_is_still_defeated(self):
        """The vote outcome outranks what the motion was for."""
        result = format_outcome(
            "DEFEATED (4 to 7)",
            "That City Council consider Bylaw No. 10169.",
        )
        assert result == "Defeated (4-7)"

    def test_an_ordinary_bylaw_approval_is_unaffected(self):
        result = format_outcome(
            "CARRIED UNANIMOUSLY",
            "That Bylaw No. 9999 be approved.",
        )
        assert result == "Approved"

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


class TestOutcomeReadsTheMotionNotJustTheVote:
    """What kind of action was moved is in the recommendation.

    format_outcome checked the vote for CARRIED/UNANIMOUSLY before looking
    at what the motion actually did, so every carried motion came out as
    "Approved" -- including committee recommendations and motions to
    merely receive a report.  Seven of eleven eval-fixture items were
    mislabelled, and Outcome is a hard chip: the one the design promises
    is auditable.
    """

    def test_committee_recommendation_is_not_an_approval(self):
        """A committee recommends to Council; Council has not acted."""
        out = format_outcome(
            "CARRIED UNANIMOUSLY",
            "That the Standing Policy Committee on Planning, Development and "
            "Community Services recommend to City Council that a temporary "
            "pause of the Civic Naming Program be approved.",
        )
        assert out == "Recommended to Council"

    def test_recommendation_keeps_a_split_tally(self):
        out = format_outcome(
            "CARRIED (7 to 4)",
            "That the Committee recommend to City Council that the plan be adopted.",
        )
        assert out == "Recommended to Council (7-4)"

    def test_governance_committee_phrasing(self):
        out = format_outcome(
            "CARRIED UNANIMOUSLY",
            "That the Governance and Priorities Committee recommend to City "
            "Council: That City Council reaffirm the City's leadership role.",
        )
        assert out == "Recommended to Council"

    def test_receiving_information_is_not_an_approval(self):
        assert format_outcome(
            "CARRIED UNANIMOUSLY", "That the information be received.",
        ) == "Received as information"

    def test_report_be_received(self):
        assert format_outcome(
            "CARRIED UNANIMOUSLY", "That the report be received as information.",
        ) == "Received as information"

    def test_noted_and_filed_are_also_non_decisions(self):
        for rec in ("That the minutes be noted.", "That the letter be filed."):
            assert format_outcome("CARRIED", rec) == "Received as information"

    def test_a_real_council_approval_is_still_approved(self):
        assert format_outcome(
            "CARRIED UNANIMOUSLY",
            "That City Council approve an increase of $187,000 to the Shaw "
            "Centre Score Clock project.",
        ) == "Approved"

    def test_a_split_council_approval_keeps_its_tally(self):
        assert format_outcome(
            "CARRIED (8 to 3)", "That Council approve the rezoning.",
        ) == "Approved (8-3)"

    def test_deferral_still_wins_over_recommendation(self):
        assert format_outcome(
            "CARRIED", "That the Committee recommend the matter be deferred.",
        ) == "Deferred"

    def test_defeated_still_wins(self):
        assert format_outcome(
            "DEFEATED (2 to 9)",
            "That the Committee recommend to City Council that it be approved.",
        ) == "Defeated (2-9)"


# ── count_agenda_items ───────────────────────────────────────────────


class TestCountAgendaItems:
    """What "N other items" on an index card counts."""

    def _item(self, **kw):
        base = {
            "title": "Report on Snow Routes",
            "content": "Some substance.",
            "recommendation": "That the report be approved.",
            "time_start_ms": 1000,
            "timestamp_inherited": False,
            "is_recess": False,
        }
        base.update(kw)
        return base

    def test_counts_ordinary_items(self):
        assert count_agenda_items([self._item(), self._item()]) == 2

    def test_excludes_recesses(self):
        items = [self._item(), self._item(title="Recess", is_recess=True)]
        assert count_agenda_items(items) == 1

    def test_excludes_section_headers(self):
        """A heading groups the business; it is not business."""
        header = self._item(
            title="COMMITTEE REPORTS",
            content="",
            recommendation="",
            time_start_ms=None,
        )
        assert count_agenda_items([self._item(), header]) == 1

    def test_counts_consent_items(self):
        """They are on the agenda and on the detail page, so they count."""
        consent = self._item(timestamp_inherited=True)
        assert count_agenda_items([consent]) == 1

    def test_counts_procedural_items(self):
        """The number has to agree with the page it points at."""
        assert count_agenda_items([self._item(title="CALL TO ORDER")]) == 1

    def test_empty_meeting(self):
        assert count_agenda_items([]) == 0


# ── mark_routine_rows ────────────────────────────────────────────────


def _row(number, title, **kw):
    base = {
        "section_number": number,
        "title": title,
        "content": "",
        "recommendation": "",
        "time_start_ms": 1000,
        "timestamp_inherited": False,
        "is_recess": False,
    }
    base.update(kw)
    return base


class TestMarkRoutineRows:
    """Which rows are meeting furniture rather than meeting business."""

    def _routine(self, items):
        mark_routine_rows(items)
        return [i["title"] for i in items if i["is_routine"]]

    def test_procedural_rows_are_routine(self):
        items = [_row("1.", "CALL TO ORDER"), _row("18.", "ADJOURNMENT")]
        assert self._routine(items) == ["CALL TO ORDER", "ADJOURNMENT"]

    def test_business_is_not_routine(self):
        items = [_row("10.3.4", "East Side Leisure Centre", content="A report.")]
        assert self._routine(items) == []

    def test_procedural_row_with_its_own_account_stays(self):
        """Question Period is where residents get answered. It is not furniture."""
        items = [_row("6.", "QUESTION PERIOD", content="x" * 2089)]
        assert self._routine(items) == []

    def test_boilerplate_length_is_not_enough_to_stay(self):
        """A row can be long and still only restate the agenda template."""
        items = [_row("1.", "CALL TO ORDER", content="x" * 140)]
        assert self._routine(items) == ["CALL TO ORDER"]

    def test_heading_over_nothing_is_routine(self):
        items = [
            _row("9.2", "Standing Policy Committee Transportation",
                 time_start_ms=None),
            _row("10.1.1", "Right-of-Way Dedication", content="A report."),
        ]
        assert self._routine(items) == ["Standing Policy Committee Transportation"]

    def test_heading_over_items_is_kept(self):
        items = [
            _row("10.3", "Community Services", time_start_ms=None),
            _row("10.3.4", "East Side Leisure Centre", content="A report."),
        ]
        assert self._routine(items) == []

    def test_prefix_match_is_by_section_not_by_digits(self):
        """``1.` must not adopt ``10.1`` as its child."""
        items = [
            _row("1.", "UNFINISHED BUSINESS", time_start_ms=None),
            _row("10.1", "Transportation", time_start_ms=None),
            _row("10.1.1", "Right-of-Way Dedication", content="A report."),
        ]
        assert self._routine(items) == ["UNFINISHED BUSINESS"]

    def test_recess_is_never_routine(self):
        """It has its own row and its own duration; the strip would hide it."""
        items = [_row("", "Recess", is_recess=True)]
        assert self._routine(items) == []


# ── count_discussed_items / count_consent_items ──────────────────────


class TestHeaderCounts:
    """The header's numbers have to be what the page draws."""

    def _counted(self, items):
        mark_routine_rows(items)
        return count_discussed_items(items), count_consent_items(items)

    def test_counts_only_what_is_drawn_at_full_weight(self):
        items = [
            _row("1.", "CALL TO ORDER"),
            _row("9.2", "Transportation", time_start_ms=None),
            _row("10.3.4", "East Side Leisure Centre", content="A report."),
            _row("8.4.1", "TCU Place Loan", timestamp_inherited=True,
                 recommendation="That a loan of $1.2M be approved."),
        ]
        assert self._counted(items) == (1, 1)

    def test_empty_meeting(self):
        assert self._counted([]) == (0, 0)
