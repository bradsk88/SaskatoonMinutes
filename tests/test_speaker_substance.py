"""What a guest speaker argued, read off the transcript.

The roster — who registered or was narrated — is established
deterministically in ``app.speakers``.  This is the second half:
a Gemini pass that says what those people actually argued, so a
speaker can carry substance instead of a name and a filename.
"""

import json

from app.item_categorizer import (
    GeminiExtractor,
    _build_remarks_prompt,
    _sanitize_speakers,
    extract_item_summaries,
)
from app.models import ItemSummary, Speaker


def _extractor(speakers=None, description=None, captured=None):
    """A stub answering both calls: the summary pass and the speaker pass.

    Each parser reads the keys it wants, so one payload serves both.
    """
    def _generate(prompt, allowed):
        if captured is not None and "registered to address" in prompt:
            captured["prompt"] = prompt
            captured["speakers"] = list(allowed)
        return json.dumps({
            "description": description or ["Rezones the site."],
            "chips": [],
            "speakers": speakers or [],
        })

    return GeminiExtractor(api_key=None, generate=_generate)


def _item(**kw):
    item = {
        "item_id": 41,
        "title": "Proposed Rezoning – 1401 11th Street West",
        "recommendation": "That the application be approved.",
        "content": "",
        "time_start_ms": 0,
        "time_end_ms": 600_000,
    }
    item.update(kw)
    return item


_ROSTER = [
    {"name": "Randy Pshebylo", "organization": "", "stance": "",
     "summary": "Registered to speak on: Rezoning", "source": "registered"},
    {"name": "Cary Tarasoff", "organization": "", "stance": "",
     "summary": "Registered to speak on: Rezoning", "source": "registered"},
]


class TestSanitizeSpeakers:
    def test_keeps_a_rostered_speaker_with_remarks(self):
        out = _sanitize_speakers(
            [{"name": "Randy Pshebylo", "said": ["Says parking already overflows."],
              "stance": "concern"}],
            ["Randy Pshebylo"],
        )
        assert out == [{
            "name": "Randy Pshebylo",
            "organization": "",
            "said": ["Says parking already overflows."],
            "stance": "concern",
        }]

    def test_drops_a_speaker_who_is_not_on_the_roster(self):
        """The roster is established from the agenda; this call cannot add to it."""
        out = _sanitize_speakers(
            [{"name": "Someone Else", "said": ["Spoke at length."], "stance": ""}],
            ["Randy Pshebylo"],
        )
        assert out == []

    def test_drops_a_registered_speaker_with_no_remarks(self):
        """Registering is not speaking — people withdraw or never take the podium."""
        out = _sanitize_speakers(
            [{"name": "Randy Pshebylo", "said": [], "stance": "support"}],
            ["Randy Pshebylo"],
        )
        assert out == []

    def test_neutral_is_stored_as_no_stance(self):
        out = _sanitize_speakers(
            [{"name": "A B", "said": ["Explained the funding timeline."],
              "stance": "neutral"}],
            ["A B"],
        )
        assert out[0]["stance"] == ""

    def test_a_repeated_speaker_is_kept_once(self):
        out = _sanitize_speakers(
            [
                {"name": "A B", "said": ["First point."], "stance": ""},
                {"name": "A B", "said": ["Second point."], "stance": ""},
            ],
            ["A B"],
        )
        assert len(out) == 1

    def test_name_matching_ignores_case(self):
        out = _sanitize_speakers(
            [{"name": "randy pshebylo", "said": ["Objected to the setback."],
              "stance": ""}],
            ["Randy Pshebylo"],
        )
        assert out[0]["name"] == "Randy Pshebylo"

    def test_bullets_are_capped(self):
        out = _sanitize_speakers(
            [{"name": "A B", "said": [f"Point {i}." for i in range(9)], "stance": ""}],
            ["A B"],
        )
        assert len(out[0]["said"]) == 3

    def test_junk_returns_nothing(self):
        assert _sanitize_speakers(None, ["A B"]) == []
        assert _sanitize_speakers(["not an object"], ["A B"]) == []


class TestExtractRemarks:
    def test_no_roster_means_no_call(self):
        called = []

        def _generate(prompt, allowed):
            called.append(prompt)
            return "{}"

        ex = GeminiExtractor(api_key=None, generate=_generate)
        assert ex.extract_remarks(_item(), "some transcript", []) == []
        assert called == []

    def test_no_transcript_means_no_call(self):
        called = []

        def _generate(prompt, allowed):
            called.append(prompt)
            return "{}"

        ex = GeminiExtractor(api_key=None, generate=_generate)
        assert ex.extract_remarks(_item(), "   ", ["Randy Pshebylo"]) == []
        assert called == []

    def test_the_prompt_names_the_roster(self):
        captured = {}
        ex = _extractor(speakers=[], captured=captured)
        ex.extract_remarks(_item(), "transcript text", ["Randy Pshebylo"])
        assert "Randy Pshebylo" in captured["prompt"]
        assert captured["speakers"] == ["Randy Pshebylo"]


class TestSummaryPassMergesSubstanceIntoTheRoster:
    def test_a_speaker_who_spoke_gains_bullets_and_a_stance(self):
        summaries = extract_item_summaries(
            _item(speakers=_ROSTER),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Randy Pshebylo",
                "said": ["Says rear-lane traffic backs up at school pickup."],
                "stance": "concern",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert summaries["speakers"] == [{
            "name": "Randy Pshebylo",
            "organization": "",
            "stance": "concern",
            "summary": "Registered to speak on: Rezoning",
            "source": "registered",
            "said": ["Says rear-lane traffic backs up at school pickup."],
        }]

    def test_a_registered_no_show_is_left_out(self):
        """Two registered, one spoke: the other is not invented into the record."""
        summaries = extract_item_summaries(
            _item(speakers=_ROSTER),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Cary Tarasoff",
                "said": ["Asked council to defer the decision."],
                "stance": "concern",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert [p["name"] for p in summaries["speakers"]] == ["Cary Tarasoff"]

    def test_an_item_with_no_roster_has_no_speakers(self):
        summaries = extract_item_summaries(
            _item(),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Randy Pshebylo", "said": ["Something."], "stance": "",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert summaries["speakers"] == []

    def test_the_transcript_stance_wins_over_the_minutes_verb(self):
        """"expressed concerns" is the minutes' summary; the transcript heard it."""
        roster = [{
            "name": "Karen Kobussen", "organization": "", "stance": "concern",
            "summary": "Karen Kobussen expressed concerns.", "source": "minutes",
        }]
        summaries = extract_item_summaries(
            _item(speakers=roster),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Karen Kobussen",
                "said": ["Backed the plan once the shelter funding was confirmed."],
                "stance": "support",
            }]),
            transcript_text="a transcript",
        )
        assert summaries["speakers"][0]["stance"] == "support"

    def test_without_gemini_there_are_no_speakers(self):
        """A degraded run must not publish a roster as if it had substance."""
        summaries = extract_item_summaries(
            _item(speakers=_ROSTER),
            [],
            gemini_extractor=GeminiExtractor(api_key=None),
            transcript_text="a transcript",
        )
        assert summaries["speakers"] == []


class TestItemSummaryCarriesSpeakers:
    def test_round_trip(self):
        raw = {
            "description": ["Rezones the site."],
            "chips": [],
            "speakers": [{
                "name": "Randy Pshebylo", "organization": "Riversdale BID",
                "stance": "concern", "summary": "Registered to speak.",
                "source": "registered", "said": ["Says parking already overflows."],
            }],
        }
        summary = ItemSummary.from_dict(raw)
        assert summary.speakers[0].said == ["Says parking already overflows."]
        assert summary.to_dict() == raw

    def test_an_entry_cached_before_speakers_loads_empty(self):
        summary = ItemSummary.from_dict({"description": ["x"], "chips": []})
        assert summary.speakers == []

    def test_an_empty_list_is_not_written_to_disk(self):
        """Six items in seven have no speaker; the key would bloat all of them."""
        summary = ItemSummary(description=["x"], chips=[])
        assert "speakers" not in summary.to_dict()


class TestHasSubstance:
    def test_a_speaker_with_remarks_has_substance(self):
        p = Speaker(name="A B", said=["Asked for a crosswalk."])
        assert p.has_substance is True

    def test_a_bare_registration_does_not(self):
        p = Speaker(name="A B", summary="Registered to speak on: Rezoning")
        assert p.has_substance is False


class TestMergeSubstanceIntoTheRoster:
    def test_cached_remarks_are_folded_into_the_roster(self):
        from app.speakers import merge_remarks
        item = {
            "speakers": [
                {"name": "Jason Aebig", "organization": "", "stance": "",
                 "summary": "Registered to speak on: DEED", "source": "registered"},
            ],
            "summary": {"speakers": [
                {"name": "Jason Aebig", "said": ["Cited a $1.37 billion study."],
                 "stance": "support"},
            ]},
        }
        merged = merge_remarks(item)
        assert merged[0]["said"] == ["Cited a $1.37 billion study."]
        assert merged[0]["stance"] == "support"

    def test_a_meeting_not_yet_summarized_keeps_its_roster(self):
        from app.speakers import merge_remarks
        item = {"speakers": [
            {"name": "Jason Aebig", "summary": "Registered to speak on: DEED"},
        ]}
        merged = merge_remarks(item)
        assert merged[0]["name"] == "Jason Aebig"
        assert merged[0].get("said") in (None, [])

    def test_a_speaker_with_no_cached_remarks_is_not_dropped(self):
        from app.speakers import merge_remarks
        item = {
            "speakers": [
                {"name": "Timothy Cain", "summary": "Registered to speak."},
                {"name": "Randy Pshebylo", "summary": "Registered to speak."},
            ],
            "summary": {"speakers": [
                {"name": "Randy Pshebylo", "said": ["Businesses are leaving."],
                 "stance": "concern"},
            ]},
        }
        assert [p["name"] for p in merge_remarks(item)] == [
            "Timothy Cain", "Randy Pshebylo",
        ]


class TestSpeakersGetTheirOwnCardRow:
    """Brad's brief: a delegation competes with the topics, not a count."""

    def _items(self, said=None):
        speaker = {"name": "Jason Aebig", "organization": "Chamber of Commerce",
                   "summary": "Registered to speak.", "source": "registered"}
        return [{
            "item_id": 1,
            "title": "Downtown Event and Entertainment District",
            "section_number": "10.1.2",
            "recommendation": "That the partnership be approved.",
            "vote_result": "CARRIED",
            "time_start_ms": 0,
            "time_end_ms": 1_200_000,
            "speakers": [speaker],
            "summary": {
                "description": ["Approves an Indigenous partnership."],
                "speakers": (
                    [{"name": "Jason Aebig", "said": said, "stance": "support"}]
                    if said else []
                ),
            },
        }]

    def _rows(self, said=None):
        from app.summarizer import extract_meeting_topics
        return extract_meeting_topics(self._items(said), "City Council")

    def test_a_speaker_with_remarks_gets_a_row(self):
        rows = self._rows(["Downtown district could inject $1.37 billion."])
        speaker_rows = [r for r in rows if r.get("kind") == "speaker"]
        assert len(speaker_rows) == 1
        assert speaker_rows[0]["topic"] == "Jason Aebig"
        assert speaker_rows[0]["organization"] == "Chamber of Commerce"

    def test_the_card_row_carries_no_remarks(self):
        """The card says who had a voice and how they came down on it.

        What they argued is three or four more lines, and with the
        archive populated those lines were what stopped the index being
        scannable. The detail page is where the words live.
        """
        rows = self._rows(["Downtown district could inject $1.37 billion."])
        speaker = [r for r in rows if r.get("kind") == "speaker"][0]
        assert speaker["summary"] == []

    def test_a_bare_registration_gets_no_row(self):
        """A name and a filename is not worth a major topic's place."""
        assert [r for r in self._rows() if r.get("kind") == "speaker"] == []

    def test_the_row_follows_the_item_it_answers(self):
        rows = self._rows(["Cited an economic impact study."])
        kinds = [r.get("kind", "topic") for r in rows]
        assert kinds == ["topic", "speaker"]

    def test_the_stance_replaces_the_outcome_badge(self):
        rows = self._rows(["Cited an economic impact study."])
        speaker = [r for r in rows if r.get("kind") == "speaker"][0]
        assert speaker["outcome"] == "In support"
        # Never a verdict: council's decision is on the row above.
        assert speaker["vote_result"] == ""
        assert speaker["is_major"] is False

    def test_the_payload_carries_every_speaker_for_the_card_to_choose_from(self):
        """The card picks three; the payload must offer it the candidates."""
        from app.summarizer import extract_meeting_topics
        items = self._items(["Cited a study."])
        items[0]["speakers"] = [
            {"name": f"Speaker {i}", "organization": "", "summary": "Registered."}
            for i in range(5)
        ]
        items[0]["summary"]["speakers"] = [
            {"name": f"Speaker {i}", "said": [f"Point {i}."], "stance": ""}
            for i in range(5)
        ]
        rows = extract_meeting_topics(items, "City Council")
        assert len([r for r in rows if r.get("kind") == "speaker"]) == 5

    def test_a_speaker_row_keeps_the_items_categories_for_the_filter(self):
        rows = self._rows(["Cited an economic impact study."])
        topic = [r for r in rows if r.get("kind") != "speaker"][0]
        speaker = [r for r in rows if r.get("kind") == "speaker"][0]
        cats = {b["type"] for b in topic["badges"] if b["type"].startswith("cat-")}
        assert {b["type"] for b in speaker["badges"]} == cats


class TestTheCardBudget:
    """Five rows for what council did, plus up to three for who spoke.

    Brad's rule: a speaker never displaces an agenda item, so a
    well-attended meeting still reports as much of council's business as
    a quiet one. Eight rows on a heavy meeting is acceptable.

    The selection itself runs in the browser, so these pin the constants
    the template declares — the same approach as
    ``tests/test_summary_render_contract.py``.
    """

    import os
    TEMPLATE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "templates", "index.html",
    )

    def _template(self) -> str:
        return open(self.TEMPLATE, encoding="utf-8").read()

    def test_council_keeps_five_slots(self):
        assert "const CARD_TOPICS = 5;" in self._template()

    def test_speakers_get_three_slots_of_their_own(self):
        assert "const CARD_SPEAKERS = 3;" in self._template()

    def test_council_rows_are_chosen_without_the_speakers(self):
        """The filter that stops a delegate taking an agenda item's place."""
        assert "topics.filter(t => t.kind !== 'speaker')" in self._template()

    def test_a_speaker_whose_item_is_absent_names_it(self):
        """Otherwise the row is a person reacting to nothing."""
        assert "Spoke on ${escapeAttr(t.spoke_to)}" in self._template()

    def test_the_payload_offers_more_speakers_than_the_card_shows(self):
        from app.summarizer import MAX_SPEAKER_ROWS
        assert MAX_SPEAKER_ROWS > 3


class TestOrganizationIsCarried:
    """Which organizations had a voice is the thing a resident scans for."""

    def test_the_speaker_pass_reports_an_organization(self):
        out = _sanitize_speakers(
            [{"name": "Jason Aebig",
              "organization": "Greater Saskatoon Chamber of Commerce",
              "said": ["Cited a $1.37 billion impact study."], "stance": "support"}],
            ["Jason Aebig"],
        )
        assert out[0]["organization"] == "Greater Saskatoon Chamber of Commerce"

    def test_a_resident_speaking_for_themselves_has_none(self):
        out = _sanitize_speakers(
            [{"name": "A B", "organization": "", "said": ["Objected."], "stance": ""}],
            ["A B"],
        )
        assert out[0]["organization"] == ""

    def test_the_transcript_fills_the_gap_a_filing_leaves(self):
        """An RTS filing is a name and a filename — it never names the org."""
        from app.speakers import merge_remarks
        merged = merge_remarks({
            "speakers": [{"name": "Randy Pshebylo", "organization": "",
                               "summary": "Registered to speak."}],
            "summary": {"speakers": [{
                "name": "Randy Pshebylo",
                "organization": "Riversdale Business Improvement District",
                "said": ["Businesses are leaving."], "stance": "concern",
            }]},
        })
        assert merged[0]["organization"] == "Riversdale Business Improvement District"

    def test_the_minutes_win_when_they_name_it(self):
        """Official text beats a speech-to-text self-introduction."""
        from app.speakers import merge_remarks
        merged = merge_remarks({
            "speakers": [{"name": "Karen Kobussen",
                               "organization": "Saskatoon West Business Association",
                               "summary": "Karen Kobussen expressed concerns."}],
            "summary": {"speakers": [{
                "name": "Karen Kobussen", "organization": "Saskatoon West Business",
                "said": ["Past plans produced no measurable results."],
                "stance": "concern",
            }]},
        })
        assert merged[0]["organization"] == "Saskatoon West Business Association"

    def _row(self, organization):
        from app.summarizer import _format_speaker_row
        return _format_speaker_row(
            {"name": "Jason Aebig", "organization": organization,
             "said": ["Cited a study."], "stance": "support"},
            {"badges": [], "time_start_ms": 0, "rank": 0},
        )

    def test_the_organization_is_a_chip_beside_the_name(self):
        """The name is the row; the organization is a chip of its own."""
        row = self._row("Greater Saskatoon Chamber of Commerce")
        assert row["topic"] == "Jason Aebig"
        assert row["organization"] == "Greater Saskatoon Chamber of Commerce"

    def test_a_speaker_who_came_for_nobody_says_so(self):
        """A blank chip where every other speaker has one reads as a bug."""
        row = self._row("")
        assert row["organization"] == "Resident"
        assert row["org_color"] is None

    def test_an_organization_carries_its_colour(self):
        row = self._row("Saskatoon Police Service")
        assert isinstance(row["org_color"], int)


# ── The organization the prompt asks for ─────────────────────────────


class TestThePromptAsksForTheBodyNotTheJob:
    """It asked who they spoke FOR and never said to drop the title.

    So the model answered with the whole self-introduction, and the
    archive filled with job titles rendered as organization chips:
    "Executive Director, The Salvation Army", "Director of Planning and
    Development", "Board Chair", "CEO of Nutrien Wonderhub".
    """

    def _prompt(self):
        return _build_remarks_prompt(
            {"title": "Rezoning 123 Main Street"},
            "a transcript of the discussion",
            ["Randy Pshebylo"],
        )

    def test_it_says_to_cut_the_title_off_the_front(self):
        prompt = self._prompt()
        assert "The body, never the job" in prompt
        assert "The Salvation Army" in prompt

    def test_it_names_the_role_only_values_it_produced(self):
        """The real ones, so the model recognises the shape it emitted."""
        prompt = self._prompt()
        for role in ("Board Chair", "Property Owner",
                     "Director of Planning and Development"):
            assert role in prompt

    def test_a_role_with_no_body_behind_it_comes_back_empty(self):
        assert "leave" in self._prompt().lower()
        assert "not an organization" in self._prompt()

    def test_it_no_longer_claims_most_delegates_are_unaffiliated(self):
        """Sixty-three of seventy-two came for an organization."""
        assert "Most delegates are residents" not in self._prompt()

    def test_inventing_an_affiliation_is_still_forbidden(self):
        prompt = self._prompt()
        assert "I live in Nutana" in prompt
