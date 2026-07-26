"""Tests for app.agenda_text — pure text-shape helpers for agenda strings."""

from app.agenda_text import (
    clean_entities,
    format_money,
    plainify,
    titleize,
    trim_to_chip,
)


# ── format_money ─────────────────────────────────────────────────────


class TestFormatMoney:
    def test_millions(self):
        assert format_money("$1,500,000") == "$1.5M"

    def test_round_millions(self):
        assert format_money("$2,000,000") == "$2M"

    def test_billions(self):
        assert format_money("$1,000,000,000") == "$1B"

    def test_hundreds_of_thousands(self):
        assert format_money("$250,000") == "$250K"

    def test_below_threshold(self):
        assert format_money("$99,999") == "$99,999"

    def test_word_million_passthrough(self):
        assert format_money("$5 million") == "$5 million"

    def test_zero(self):
        assert format_money("$0") == "$0"


# ── plainify ─────────────────────────────────────────────────────────


class TestPlainify:
    def test_bylaw_prefix(self):
        result = plainify("Bylaw No. 9876 - The Zoning Bylaw, 2025 (No. 3)")
        assert "Bylaw No." not in result
        assert "Zoning" in result

    def test_contract_prefix(self):
        result = plainify("Award of Contract - Road Resurfacing (Contract No. 25-0456)")
        assert "Award of Contract" not in result
        assert "Road Resurfacing" in result

    def test_enquiry_prefix(self):
        result = plainify("Enquiry - Councillor Smith (March 2025) - Transit Funding")
        assert "Transit Funding" in result

    def test_standing_committee(self):
        result = plainify("Standing Policy Committee on Planning")
        assert result == "Planning"

    def test_reference_code(self):
        result = plainify("Simple Title [CC2025-0402]")
        assert result == "Simple Title"

    def test_reference_code_with_the_clerks_period(self):
        """The same file number is written both ways upstream, so the
        card kept "[CK. 225-18]" and dropped "[CK 225-18]"."""
        assert plainify("Work Plan [CK. 225-18]") == "Work Plan"
        assert plainify("Report of the Chair [File No. CK 225-83]") \
            == "Report of the Chair"

    def test_two_reference_groups_both_go(self):
        """Budget items carry the book page and the file number, and an
        end-anchored single strip left the first group on the card."""
        assert plainify("Saskatoon Police Service [Budget Book pages 9-18] "
                        "[CCB2025-1110]") == "Saskatoon Police Service"

    def test_a_multi_code_reference_goes(self):
        assert plainify("New Saskatoon Transit [TS2023-0305 & TS2023-0503]") \
            == "New Saskatoon Transit"

    def test_empty(self):
        assert plainify("") == ""


# ── clean_entities ───────────────────────────────────────────────────


class TestCleanEntities:
    def test_html_entities(self):
        assert clean_entities("foo&#58;bar") == "foo:bar"
        assert clean_entities("a&amp;b") == "a&b"

    def test_collapses_whitespace(self):
        assert clean_entities("a   b") == "a b"


# ── trim_to_chip ─────────────────────────────────────────────────────


class TestTrimToChip:
    def test_short_passthrough(self):
        assert trim_to_chip("Approved (8-3)") == "Approved (8-3)"

    def test_overflow_with_natural_break_trimmed_at_break(self):
        text = (
            "Council debated the proposal at length. "
            "Residents raised concerns about parking and traffic on the side streets."
        )
        result = trim_to_chip(text)
        assert len(result) <= 100
        assert not result.endswith("…")
        assert result == "Council debated the proposal at length"

    def test_overflow_without_natural_break_dropped(self):
        text = "x" * 120
        assert trim_to_chip(text) == ""

    def test_does_not_strip_filler(self):
        # trim_to_chip is purely length/boundary; filler-stripping is the
        # caller's responsibility (see transcript_text.strip_filler_leads).
        assert trim_to_chip("I think the budget is fine") == "I think the budget is fine"

    def test_does_not_decode_entities(self):
        # Entity decoding is the caller's responsibility (see clean_entities).
        assert "&amp;" in trim_to_chip("Parks &amp; Recreation funded")

    def test_empty(self):
        assert trim_to_chip("") == ""


# ── titleize ─────────────────────────────────────────────────────────


class TestTitleize:
    def test_shouted_name_becomes_readable(self):
        assert titleize("MUNICIPAL HERITAGE ADVISORY COMMITTEE") == (
            "Municipal Heritage Advisory Committee"
        )

    def test_joining_words_stay_lowercase(self):
        assert titleize("BOARD OF POLICE COMMISSIONERS") == (
            "Board of Police Commissioners"
        )

    def test_a_leading_joining_word_is_still_capitalized(self):
        assert titleize("THE COMMITTEE") == "The Committee"

    def test_mixed_case_is_left_alone(self):
        """Anything not shouted was written that way deliberately."""
        assert titleize("Standing Policy Committee on Transportation") == (
            "Standing Policy Committee on Transportation"
        )

    def test_hyphenated_words_capitalize_both_parts(self):
        assert titleize("PUBLIC-HEARING COMMITTEE") == "Public-Hearing Committee"

    def test_digits_and_punctuation_survive(self):
        assert titleize("WARD 3 COMMITTEE (SPECIAL)") == "Ward 3 Committee (Special)"

    def test_empty_string(self):
        assert titleize("") == ""

    def test_entities_and_whitespace_are_cleaned(self):
        assert titleize("PARKS &amp;  RECREATION") == "Parks & Recreation"
