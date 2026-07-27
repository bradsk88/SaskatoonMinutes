"""What a guest speaker argued, read off the transcript.

The roster — who registered or was narrated — is established
deterministically in ``app.presentations``.  This is the second half:
a Gemini pass that says what those people actually argued, so a
presentation can carry substance instead of a name and a filename.
"""

import json

from app.item_categorizer import (
    GeminiExtractor,
    _sanitize_speakers,
    extract_item_summaries,
)
from app.models import ItemSummary, Presentation


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


class TestExtractSpeakers:
    def test_no_roster_means_no_call(self):
        called = []

        def _generate(prompt, allowed):
            called.append(prompt)
            return "{}"

        ex = GeminiExtractor(api_key=None, generate=_generate)
        assert ex.extract_speakers(_item(), "some transcript", []) == []
        assert called == []

    def test_no_transcript_means_no_call(self):
        called = []

        def _generate(prompt, allowed):
            called.append(prompt)
            return "{}"

        ex = GeminiExtractor(api_key=None, generate=_generate)
        assert ex.extract_speakers(_item(), "   ", ["Randy Pshebylo"]) == []
        assert called == []

    def test_the_prompt_names_the_roster(self):
        captured = {}
        ex = _extractor(speakers=[], captured=captured)
        ex.extract_speakers(_item(), "transcript text", ["Randy Pshebylo"])
        assert "Randy Pshebylo" in captured["prompt"]
        assert captured["speakers"] == ["Randy Pshebylo"]


class TestSummaryPassMergesSubstanceIntoTheRoster:
    def test_a_speaker_who_spoke_gains_bullets_and_a_stance(self):
        summaries = extract_item_summaries(
            _item(presentations=_ROSTER),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Randy Pshebylo",
                "said": ["Says rear-lane traffic backs up at school pickup."],
                "stance": "concern",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert summaries["presentations"] == [{
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
            _item(presentations=_ROSTER),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Cary Tarasoff",
                "said": ["Asked council to defer the decision."],
                "stance": "concern",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert [p["name"] for p in summaries["presentations"]] == ["Cary Tarasoff"]

    def test_an_item_with_no_roster_has_no_presentations(self):
        summaries = extract_item_summaries(
            _item(),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Randy Pshebylo", "said": ["Something."], "stance": "",
            }]),
            transcript_text="a transcript of the discussion",
        )
        assert summaries["presentations"] == []

    def test_the_transcript_stance_wins_over_the_minutes_verb(self):
        """"expressed concerns" is the minutes' summary; the transcript heard it."""
        roster = [{
            "name": "Karen Kobussen", "organization": "", "stance": "concern",
            "summary": "Karen Kobussen expressed concerns.", "source": "minutes",
        }]
        summaries = extract_item_summaries(
            _item(presentations=roster),
            [],
            gemini_extractor=_extractor(speakers=[{
                "name": "Karen Kobussen",
                "said": ["Backed the plan once the shelter funding was confirmed."],
                "stance": "support",
            }]),
            transcript_text="a transcript",
        )
        assert summaries["presentations"][0]["stance"] == "support"

    def test_without_gemini_there_are_no_presentations(self):
        """A degraded run must not publish a roster as if it had substance."""
        summaries = extract_item_summaries(
            _item(presentations=_ROSTER),
            [],
            gemini_extractor=GeminiExtractor(api_key=None),
            transcript_text="a transcript",
        )
        assert summaries["presentations"] == []


class TestItemSummaryCarriesPresentations:
    def test_round_trip(self):
        raw = {
            "description": ["Rezones the site."],
            "chips": [],
            "presentations": [{
                "name": "Randy Pshebylo", "organization": "Riversdale BID",
                "stance": "concern", "summary": "Registered to speak.",
                "source": "registered", "said": ["Says parking already overflows."],
            }],
        }
        summary = ItemSummary.from_dict(raw)
        assert summary.presentations[0].said == ["Says parking already overflows."]
        assert summary.to_dict() == raw

    def test_an_entry_cached_before_presentations_loads_empty(self):
        summary = ItemSummary.from_dict({"description": ["x"], "chips": []})
        assert summary.presentations == []

    def test_an_empty_list_is_not_written_to_disk(self):
        """Six items in seven have no speaker; the key would bloat all of them."""
        summary = ItemSummary(description=["x"], chips=[])
        assert "presentations" not in summary.to_dict()


class TestHasSubstance:
    def test_a_speaker_with_remarks_has_substance(self):
        p = Presentation(name="A B", said=["Asked for a crosswalk."])
        assert p.has_substance is True

    def test_a_bare_registration_does_not(self):
        p = Presentation(name="A B", summary="Registered to speak on: Rezoning")
        assert p.has_substance is False


class TestMergeSubstanceIntoTheRoster:
    def test_cached_remarks_are_folded_into_the_roster(self):
        from app.presentations import merge_substance
        item = {
            "presentations": [
                {"name": "Jason Aebig", "organization": "", "stance": "",
                 "summary": "Registered to speak on: DEED", "source": "registered"},
            ],
            "summary": {"presentations": [
                {"name": "Jason Aebig", "said": ["Cited a $1.37 billion study."],
                 "stance": "support"},
            ]},
        }
        merged = merge_substance(item)
        assert merged[0]["said"] == ["Cited a $1.37 billion study."]
        assert merged[0]["stance"] == "support"

    def test_a_meeting_not_yet_summarized_keeps_its_roster(self):
        from app.presentations import merge_substance
        item = {"presentations": [
            {"name": "Jason Aebig", "summary": "Registered to speak on: DEED"},
        ]}
        merged = merge_substance(item)
        assert merged[0]["name"] == "Jason Aebig"
        assert merged[0].get("said") in (None, [])

    def test_a_speaker_with_no_cached_remarks_is_not_dropped(self):
        from app.presentations import merge_substance
        item = {
            "presentations": [
                {"name": "Timothy Cain", "summary": "Registered to speak."},
                {"name": "Randy Pshebylo", "summary": "Registered to speak."},
            ],
            "summary": {"presentations": [
                {"name": "Randy Pshebylo", "said": ["Businesses are leaving."],
                 "stance": "concern"},
            ]},
        }
        assert [p["name"] for p in merge_substance(item)] == [
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
            "presentations": [speaker],
            "summary": {
                "description": ["Approves an Indigenous partnership."],
                "presentations": (
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
        speaker_rows = [r for r in rows if r.get("kind") == "presentation"]
        assert len(speaker_rows) == 1
        assert speaker_rows[0]["topic"] == "Jason Aebig, Chamber of Commerce"
        assert speaker_rows[0]["summary"] == [
            "Downtown district could inject $1.37 billion."
        ]

    def test_a_bare_registration_gets_no_row(self):
        """A name and a filename is not worth a major topic's place."""
        assert [r for r in self._rows() if r.get("kind") == "presentation"] == []

    def test_the_row_follows_the_item_it_answers(self):
        rows = self._rows(["Cited an economic impact study."])
        kinds = [r.get("kind", "topic") for r in rows]
        assert kinds == ["topic", "presentation"]

    def test_the_stance_replaces_the_outcome_badge(self):
        rows = self._rows(["Cited an economic impact study."])
        speaker = [r for r in rows if r.get("kind") == "presentation"][0]
        assert speaker["outcome"] == "In support"
        # Never a verdict: council's decision is on the row above.
        assert speaker["vote_result"] == ""
        assert speaker["is_major"] is False

    def test_a_card_carries_at_most_two_speaker_rows(self):
        """Five delegations on one item would otherwise be the whole card."""
        from app.summarizer import extract_meeting_topics
        items = self._items(["Cited a study."])
        items[0]["presentations"] = [
            {"name": f"Speaker {i}", "organization": "", "summary": "Registered."}
            for i in range(5)
        ]
        items[0]["summary"]["presentations"] = [
            {"name": f"Speaker {i}", "said": [f"Point {i}."], "stance": ""}
            for i in range(5)
        ]
        rows = extract_meeting_topics(items, "City Council")
        assert len([r for r in rows if r.get("kind") == "presentation"]) == 2

    def test_a_speaker_row_keeps_the_items_categories_for_the_filter(self):
        rows = self._rows(["Cited an economic impact study."])
        topic = [r for r in rows if r.get("kind") != "presentation"][0]
        speaker = [r for r in rows if r.get("kind") == "presentation"][0]
        cats = {b["type"] for b in topic["badges"] if b["type"].startswith("cat-")}
        assert {b["type"] for b in speaker["badges"]} == cats
