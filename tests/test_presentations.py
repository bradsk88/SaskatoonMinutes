from app.models import AgendaItem
from app.presentations import extract_presentations

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


class TestExtractPresentations:
    def test_finds_multiple_named_delegates(self):
        names = {p.name for p in extract_presentations(_homelessness_item())}
        assert {
            "Karen Kobussen", "Rob Wilgenhof", "Mathieu Gaudet",
            "Gordon Taylor", "Jodie Semkiw", "Robert Lafontaine",
        } <= names

    def test_excludes_staff_presenting_the_report(self):
        names = {p.name for p in extract_presentations(_homelessness_item())}
        assert not any("Director" in n or "Anderson" in n for n in names)

    def test_excludes_pronoun_only_sentences(self):
        # "She responded to questions of Committee." must not become a
        # presentation of its own.
        names = [p.name for p in extract_presentations(_homelessness_item())]
        assert "She" not in names

    def test_captures_organization(self):
        presentations = extract_presentations(_homelessness_item())
        karen = next(p for p in presentations if p.name == "Karen Kobussen")
        assert karen.organization == "Saskatoon West Business Association"

    def test_captures_multi_part_title_and_organization(self):
        presentations = extract_presentations(_homelessness_item())
        gordon = next(p for p in presentations if p.name == "Gordon Taylor")
        assert gordon.organization == "Executive Director, The Salvation Army"

    def test_classifies_concern_stance(self):
        presentations = extract_presentations(_homelessness_item())
        karen = next(p for p in presentations if p.name == "Karen Kobussen")
        assert karen.stance == "concern"

    def test_classifies_support_stance(self):
        presentations = extract_presentations(_homelessness_item())
        rob = next(p for p in presentations if p.name == "Rob Wilgenhof")
        assert rob.stance == "support"

    def test_source_is_minutes_for_narrated_delegates(self):
        presentations = extract_presentations(_homelessness_item())
        karen = next(p for p in presentations if p.name == "Karen Kobussen")
        assert karen.source == "minutes"

    def test_registered_to_speak_but_not_narrated_is_still_found(self):
        # Tammy MacFarlane is only ever named as a companion in the
        # prose ("along with Tammy MacFarlane") but filed a Request to
        # Speak, so the attachment pass should surface her.
        presentations = extract_presentations(_homelessness_item())
        tammy = next(p for p in presentations if p.name == "Tammy MacFarlane")
        assert tammy.source == "registered"

    def test_written_comments_are_not_presentations(self):
        # Landon Field submitted written comments, not a presentation.
        names = {p.name for p in extract_presentations(_homelessness_item())}
        assert "Landon Field" not in names

    def test_no_duplicate_when_narrated_and_registered(self):
        presentations = extract_presentations(_homelessness_item())
        karen_entries = [p for p in presentations if p.name == "Karen Kobussen"]
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
        presentations = extract_presentations(item)
        names = [p.name for p in presentations]
        assert names == ["Colleen Christopherson-Cote"]

    def test_empty_content_and_attachments_yields_nothing(self):
        item = AgendaItem(item_id=1, title="t", content="", section_number="1")
        assert extract_presentations(item) == []

    def test_at_least_two_presentations_for_homelessness_meeting(self):
        assert len(extract_presentations(_homelessness_item())) >= 2
