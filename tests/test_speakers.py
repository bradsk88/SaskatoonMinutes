from app.models import AgendaItem
from app.speakers import (
    ORGANIZATION_COLOURS,
    _is_city_unit,
    clean_organization,
    extract_speakers,
    organization_color,
    organization_label,
)

_HOMELESSNESS_CONTENT = (
    "Director of Planning and Development Anderson presented the report and "
    "responded to a question of Committee. Karen Kobussen, Saskatoon West "
    "Business Association, expressed concerns with the effectiveness of "
    "existing approaches to addressing homelessness and encouraged "
    "consideration of alternative strategies. She responded to questions of "
    "Committee. Rob Wilgenhof expressed support for the City's efforts to "
    "address homelessness and addictions. Mathieu Gaudet, Métis "
    "Nation–Saskatchewan, expressed support of the Plan highlighting the "
    "collaborative development of the plan. He responded to questions of "
    "Committee. Gordon Taylor, Executive Director, The Salvation Army, "
    "expressed support for the Plan. He responded to questions of "
    "Committee. Jodie Semkiw, Executive Director, Saskatoon Crisis "
    "Intervention Service, spoke in support of the Plan. She responded to "
    "questions along with Tammy MacFarlane.&#160; Discussion continued. "
    "Robert Lafontaine, Saskatoon Housing Initiatives Partnership, was in "
    "the gallery and was called forward to respond to questions of "
    "Committee regarding homelessness data collection."
)


def _homelessness_item() -> AgendaItem:
    return AgendaItem(
        item_id=21,
        title="Saskatoon Homelessness Action Plan 2026 [CC2026-0301]",
        content=_HOMELESSNESS_CONTENT,
        section_number="6.2.1",
        attachments=[
            {
                "name": "6.2.1 RTS - Karen Kobussen - Saskatoon Homelessness Action Plan 2026_Redacted.pdf",
                "url": "https://example.com/1",
            },
            {
                "name": "6.2.1 RTS - Tammy MacFarlane - Saskatoon Homelessness Action Plan 2026_Redacted.pdf",
                "url": "https://example.com/2",
            },
            {
                "name": "6.2.1 Comments - Landon Field - Saskatoon Homelessness Action Plan 2026_Redacted.pdf",
                "url": "https://example.com/3",
            },
            {
                "name": "Admin Report - Saskatoon Homelessness Action Plan 2026.pdf",
                "url": "https://example.com/4",
            },
        ],
    )


class TestExtractSpeakers:
    def test_finds_multiple_named_delegates(self):
        names = {p.name for p in extract_speakers(_homelessness_item())}
        assert {
            "Karen Kobussen", "Rob Wilgenhof", "Mathieu Gaudet",
            "Gordon Taylor", "Jodie Semkiw", "Robert Lafontaine",
        } <= names

    def test_excludes_staff_presenting_the_report(self):
        names = {p.name for p in extract_speakers(_homelessness_item())}
        assert not any("Director" in n or "Anderson" in n for n in names)

    def test_excludes_pronoun_only_sentences(self):
        # "She responded to questions of Committee." must not become a
        # speaker of their own.
        names = [p.name for p in extract_speakers(_homelessness_item())]
        assert "She" not in names

    def test_captures_organization(self):
        speakers = extract_speakers(_homelessness_item())
        karen = next(p for p in speakers if p.name == "Karen Kobussen")
        assert karen.organization == "Saskatoon West Business Association"

    def test_captures_multi_part_title_and_organization(self):
        speakers = extract_speakers(_homelessness_item())
        gordon = next(p for p in speakers if p.name == "Gordon Taylor")
        assert gordon.organization == "Executive Director, The Salvation Army"

    def test_classifies_concern_stance(self):
        speakers = extract_speakers(_homelessness_item())
        karen = next(p for p in speakers if p.name == "Karen Kobussen")
        assert karen.stance == "concern"

    def test_classifies_support_stance(self):
        speakers = extract_speakers(_homelessness_item())
        rob = next(p for p in speakers if p.name == "Rob Wilgenhof")
        assert rob.stance == "support"

    def test_source_is_minutes_for_narrated_delegates(self):
        speakers = extract_speakers(_homelessness_item())
        karen = next(p for p in speakers if p.name == "Karen Kobussen")
        assert karen.source == "minutes"

    def test_registered_to_speak_but_not_narrated_is_still_found(self):
        # Tammy MacFarlane is only ever named as a companion in the
        # prose ("along with Tammy MacFarlane") but filed a Request to
        # Speak, so the attachment pass should surface her.
        speakers = extract_speakers(_homelessness_item())
        tammy = next(p for p in speakers if p.name == "Tammy MacFarlane")
        assert tammy.source == "registered"

    def test_written_comments_are_not_speakers(self):
        # Landon Field submitted written comments, and never took the podium.
        names = {p.name for p in extract_speakers(_homelessness_item())}
        assert "Landon Field" not in names

    def test_no_duplicate_when_narrated_and_registered(self):
        speakers = extract_speakers(_homelessness_item())
        karen_entries = [p for p in speakers if p.name == "Karen Kobussen"]
        assert len(karen_entries) == 1

    def test_hyphenated_surname_in_rts_filename_not_split(self):
        # "RTS - Colleen Christopherson-Cote - <topic>.pdf": the mid-name
        # hyphen must not be read as the name/topic separator, or the RTS
        # pass invents a second, truncated "Colleen Christopherson" entry
        # for someone already captured (correctly) from the prose.
        content = (
            "Colleen Christopherson-Cote, Saskatoon Poverty Reduction "
            "Partnership, spoke in support of the Plan."
        )
        item = AgendaItem(
            item_id=1, title="t", content=content, section_number="1",
            attachments=[{
                "name": "6.2.1 RTS - Colleen Christopherson-Cote - "
                        "Saskatoon Homelessness Action Plan 2026_Redacted.pdf",
                "url": "https://example.com/x",
            }],
        )
        speakers = extract_speakers(item)
        names = [p.name for p in speakers]
        assert names == ["Colleen Christopherson-Cote"]

    def test_empty_content_and_attachments_yields_nothing(self):
        item = AgendaItem(item_id=1, title="t", content="", section_number="1")
        assert extract_speakers(item) == []

    def test_at_least_two_speakers_for_homelessness_meeting(self):
        assert len(extract_speakers(_homelessness_item())) >= 2


class TestItemsThatCannotHostADelegation:
    """eSCRIBE hangs the meeting's whole document package off ADJOURNMENT.

    125 attachments on the June 24 council meeting, so every Request to
    Speak in the meeting was found there a second time: the published
    count was 22 filings from 11 people, and the detail page grew a
    "Guest speakers" block under Adjournment.
    """

    def _rts(self) -> list[dict]:
        return [{
            "name": "8.1.4 RTS - Sherry Tarasoff - MRC Expansion.pdf",
            "url": "https://example.com/1",
        }]

    def test_adjournment_hosts_no_speakers(self):
        item = AgendaItem(
            item_id=18, title="ADJOURNMENT", content="",
            section_number="18.", attachments=self._rts(),
        )
        assert extract_speakers(item) == []

    def test_call_to_order_hosts_no_speakers(self):
        item = AgendaItem(
            item_id=1, title="CALL TO ORDER", content="",
            section_number="1.", attachments=self._rts(),
        )
        assert extract_speakers(item) == []

    def test_a_recess_hosts_no_speakers(self):
        item = AgendaItem(
            item_id=9, title="Recess", content="", section_number="9.",
            is_recess=True, attachments=self._rts(),
        )
        assert extract_speakers(item) == []

    def test_a_real_item_still_does(self):
        item = AgendaItem(
            item_id=41,
            title="Material Recovery Centre Expansion [CC2026-0402]",
            content="", section_number="8.1.4", attachments=self._rts(),
        )
        assert [p.name for p in extract_speakers(item)] == ["Sherry Tarasoff"]


class TestStaffAndCouncilAreNotGuests:
    """The point of the feature is who came to address council."""

    def _item(self, content: str) -> AgendaItem:
        return AgendaItem(
            item_id=1, title="East Side Leisure Centre",
            content=content, section_number="10.3.4",
        )

    def test_a_general_manager_is_not_a_guest(self):
        # Published as name="General Manager", organization="Community
        # Services Anger" — both words capitalized, so the name shape test
        # could not see it was a job.
        item = self._item(
            "General Manager, Community Services Anger presented the report "
            "with a PowerPoint."
        )
        assert extract_speakers(item) == []

    def test_a_councillor_is_not_a_guest(self):
        item = self._item("Councillor Jeffries expressed support for the report.")
        assert extract_speakers(item) == []

    def test_a_chief_is_still_a_guest(self):
        """"Chief Kelly Wolfe" addressed the Downtown Event District item."""
        item = self._item(
            "Chief Kelly Wolfe, Whitecap Dakota Nation, expressed support "
            "for the partnership."
        )
        assert [p.name for p in extract_speakers(item)] == ["Chief Kelly Wolfe"]

    def test_a_plain_guest_is_unaffected(self):
        item = self._item("Rob Wilgenhof expressed support for the City's efforts.")
        assert [p.name for p in extract_speakers(item)] == ["Rob Wilgenhof"]

    # ── Staff working through their own item ──
    #
    # ``_VERB_RE`` matches "presented", which published thirty-two staff
    # appearances as guest speakers across the archive: the police chief,
    # the city auditor, the fire chief, the Development Review Manager
    # six times over. Their names are ordinary, so the name test could
    # not see them.

    def test_the_clerks_staff_formula_is_not_a_guest(self):
        item = self._item(
            "Chief McBride presented the report and responded to questions "
            "of the Board."
        )
        assert extract_speakers(item) == []

    def test_the_formula_covers_answered_as_well_as_responded(self):
        item = self._item(
            "Sergeant Aaron Moser presented the report and answered "
            "questions of the Board, along with Chief McBride."
        )
        assert extract_speakers(item) == []

    def test_a_bare_presentation_needs_a_rank_to_be_staff(self):
        item = self._item("City Auditor Thomson presented the report.")
        assert extract_speakers(item) == []

    def test_a_bare_presentation_by_a_guest_is_kept(self):
        """The Office of the Matriarchs presenting its own work.

        Dropping her would silence the guest this feature exists to
        surface. A staff row is clutter; a missing delegate is a person
        the page says was not there, so the doubt goes her way.
        """
        item = self._item(
            "Auntie Advocate Swiftwolfe presented the report with a PowerPoint."
        )
        assert [p.name for p in extract_speakers(item)] == [
            "Auntie Advocate Swiftwolfe"
        ]

    def test_a_chief_presenting_for_their_nation_is_still_a_guest(self):
        """"Chief" is not a staff rank here, and must never become one."""
        item = self._item(
            "Chief Kelly Wolfe presented the report with a PowerPoint."
        )
        assert [p.name for p in extract_speakers(item)] == ["Chief Kelly Wolfe"]

    def test_presenting_something_that_is_not_the_report_is_not_the_formula(self):
        item = self._item(
            "Sherry Tarasoff, via teleconference, presented a video "
            "expressing opposition."
        )
        assert [p.name for p in extract_speakers(item)] == ["Sherry Tarasoff"]

    # ── The employer test ──

    def test_a_city_division_is_not_a_guest(self):
        """His name is ordinary and his sentence is not the formula."""
        item = self._item(
            "Darryl Dawson, Development Review Manager, Community Services "
            "Division, presented the proposed amendment to the Zoning Bylaw."
        )
        assert extract_speakers(item) == []

    def test_the_city_itself_is_not_a_guest(self):
        item = self._item(
            "Jane Doe, City of Saskatoon, spoke in support of the proposal."
        )
        assert extract_speakers(item) == []

    def test_a_director_of_a_real_organization_is_still_a_guest(self):
        """"Director" in the title must not condemn the body behind it."""
        item = self._item(
            "Gordon Taylor, Executive Director, The Salvation Army, "
            "expressed support for the Plan."
        )
        assert [p.name for p in extract_speakers(item)] == ["Gordon Taylor"]


class TestRegisteredTopicIsReadable:
    def _with(self, filename: str) -> AgendaItem:
        return AgendaItem(
            item_id=1, title="TCU Place Loan [FI2026-0601]", content="",
            section_number="8.4.1",
            attachments=[{"name": filename, "url": "https://example.com/1"}],
        )

    def test_redacted_marker_is_not_published(self):
        item = self._with("8.4.1 RTS - Lisa Mulvaney - Fixed-term Loan_Redacted.pdf")
        assert extract_speakers(item)[0].summary == (
            "Registered to speak on: Fixed-term Loan"
        )

    def test_a_reupload_suffix_is_not_published(self):
        # Published verbatim as "...TCU Place_Redacted(1)".
        item = self._with(
            "8.4.1 RTS - Lisa Mulvaney - Fixed-term Loan to TCU Place_Redacted(1).pdf"
        )
        assert extract_speakers(item)[0].summary == (
            "Registered to speak on: Fixed-term Loan to TCU Place"
        )


# ── Organization chips ───────────────────────────────────────────────


class TestOrganizationLabel:
    def test_an_organization_speaks_for_itself(self):
        assert organization_label("Wild About Saskatoon") == "Wild About Saskatoon"

    def test_nobody_behind_the_speaker_is_still_something_to_say(self):
        """Sixty-three of seventy-two speakers came for an organization.

        A blank chip on the other nine reads as data we failed to fetch.
        """
        assert organization_label("") == "Resident"
        assert organization_label("   ") == "Resident"
        assert organization_label(None) == "Resident"


class TestOrganizationColor:
    def test_the_same_organization_is_always_the_same_colour(self):
        """The whole point: recognised across cards before it is read."""
        assert (organization_color("Saskatoon Police Service")
                == organization_color("Saskatoon Police Service"))

    def test_the_colour_does_not_move_between_processes(self):
        """``hash()`` is salted per interpreter, so this must not use it.

        The Flask app and the static build would otherwise disagree, and
        one build would disagree with the next.
        """
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, "-c",
             "from app.speakers import organization_color;"
             "print(organization_color('Saskatoon Police Service'))"],
            capture_output=True, text=True, env={"PYTHONHASHSEED": "random"},
        )
        assert out.stdout.strip() == str(organization_color("Saskatoon Police Service"))

    def test_spelling_noise_does_not_change_the_colour(self):
        assert (organization_color("  wild   about saskatoon ")
                == organization_color("Wild About Saskatoon"))

    def test_no_organization_has_no_colour(self):
        assert organization_color("") is None

    def test_the_colour_is_a_palette_slot(self):
        for name in ("Discover Saskatoon", "Strong Towns YXC", "Swale Watchers"):
            assert 0 <= organization_color(name) < ORGANIZATION_COLOURS

    def test_the_palette_is_actually_used(self):
        """One colour for forty-one organizations would be no signal."""
        names = [
            "Saskatoon Police Service", "Wild About Saskatoon",
            "Discover Saskatoon", "Bus Riders of Saskatoon",
            "Strong Towns YXC", "Riversdale Business Improvement District",
            "Downtown Saskatoon Business Improvement District",
            "Saskatoon Fire Department", "Office of the Matriarchs",
            "Métis Nation–Saskatchewan", "Swale Watchers",
            "Meridian Development", "Police Commission",
        ]
        assert len({organization_color(n) for n in names}) >= 5


class TestCleanOrganization:
    """The model put the whole self-introduction in the field.

    The prompt asks for the body alone now, but the archive was written
    before it did, so this runs every build over what is already cached.
    """

    def test_a_title_in_front_of_a_comma_is_dropped(self):
        assert (clean_organization("Executive Director, The Salvation Army")
                == "The Salvation Army")

    def test_a_long_staff_title_is_dropped_too(self):
        assert (clean_organization(
            "Development Review Section Manager, Community Services Division")
            == "Community Services Division")

    def test_an_organization_with_no_title_is_untouched(self):
        for name in ("Saskatoon Police Service", "Métis Nation–Saskatchewan",
                     "Saskatoon & Region Home Builders' Association",
                     "Riversdale Business Improvement District"):
            assert clean_organization(name) == name

    def test_a_comma_that_is_part_of_the_name_survives(self):
        """Only a role noun in front of the comma triggers the cut."""
        assert (clean_organization("Kindrachuk Agrey Architecture, Saskatoon")
                == "Kindrachuk Agrey Architecture, Saskatoon")

    def test_a_title_with_nothing_behind_it_is_left_alone(self):
        """Emptying it would render "Resident", which is a different lie."""
        assert clean_organization("Board Chair") == "Board Chair"
        assert clean_organization("Executive Director,") == "Executive Director,"

    def test_the_shape_no_rule_can_read_is_left_alone(self):
        """One is a job at a named body, one is a job at no body at all."""
        assert (clean_organization("CEO of Nutrien Wonderhub")
                == "CEO of Nutrien Wonderhub")
        assert (clean_organization("Director of Planning and Development")
                == "Director of Planning and Development")

    def test_whitespace_is_normalized(self):
        assert clean_organization("  Wild   About  Saskatoon ") == "Wild About Saskatoon"

    def test_nothing_stays_nothing(self):
        assert clean_organization("") == ""
        assert clean_organization(None) == ""

    def test_the_chip_and_the_colour_both_read_the_cleaned_name(self):
        """Otherwise the title would still pick the colour, and the same
        organization would be two colours depending on who represented it."""
        assert (organization_label("Executive Director, The Salvation Army")
                == "The Salvation Army")
        assert (organization_color("Executive Director, The Salvation Army")
                == organization_color("The Salvation Army"))


class TestCityUnits:
    """The employer test, on its own."""

    def test_a_division_is_the_city(self):
        assert _is_city_unit("Community Services Division") is True

    def test_the_corporation_is_the_city_however_it_is_written(self):
        assert _is_city_unit("City of Saskatoon") is True
        assert _is_city_unit("City of Saskatoon Administration") is True

    def test_a_title_in_front_does_not_hide_the_division(self):
        """Tested after the job title comes off, or it would be missed."""
        assert _is_city_unit(
            "Development Review Manager, Community Services Division"
        ) is True

    def test_a_guest_organization_is_not_the_city(self):
        for name in ("The Salvation Army", "Saskatoon West Business Association",
                     "Downtown Saskatoon Business Improvement District",
                     "Muskeg Lake Cree Nation", "Métis Nation–Saskatchewan",
                     "Wild About Saskatoon", ""):
            assert _is_city_unit(name) is False, name
