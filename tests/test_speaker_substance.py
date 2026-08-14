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
    """A speaker sits beneath the item they answered, as a row."""

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

    def test_the_stance_travels_as_a_value_the_card_can_colour_on(self):
        """The card colours the badge; matching on "In support" would
        break the moment the wording is reconsidered -- and it has been
        once already, "Raised concerns" over "Opposed"."""
        rows = self._rows(["Cited an economic impact study."])
        speaker = [r for r in rows if r.get("kind") == "speaker"][0]
        assert speaker["stance"] == "support"

    def test_the_payload_caps_the_rows_but_carries_the_full_count(self):
        """The card shows at most three speakers per item and says
        \"+N more\" for the rest -- so the payload hands it three and
        tells it how many there were."""
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
        speaker_rows = [r for r in rows if r.get("kind") == "speaker"]
        assert len(speaker_rows) == 3
        assert all(r["item_speaker_count"] == 5 for r in speaker_rows)

    def test_a_speaker_row_keeps_the_items_categories_for_the_filter(self):
        rows = self._rows(["Cited an economic impact study."])
        topic = [r for r in rows if r.get("kind") != "speaker"][0]
        speaker = [r for r in rows if r.get("kind") == "speaker"][0]
        cats = {b["type"] for b in topic["badges"] if b["type"].startswith("cat-")}
        assert {b["type"] for b in speaker["badges"]} == cats


class TestTheCardBudget:
    """A card spends vertical space, not rows: fifteen units, roughly
    one mobile screen.

    A quiet meeting looks like the old card — five detailed council
    rows, each with its speakers beneath it. A packed meeting spends
    down (ADR 0022): the least-attended items demote to title-only
    first, then the most-attended item gives up its speaker rows one
    at a time, then it demotes too, and dropping a title-only row is
    the last resort. The digest names whatever organizations are not
    shown inline and is never cut: hiding which orgs had a voice is
    not an acceptable saving.

    Brad's rule stands: a speaker never displaces an agenda item.

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

    def test_the_budget_is_fifteen_units(self):
        assert "const CARD_SPACE_BUDGET = 15;" in self._template()

    def test_council_keeps_five_detailed_slots(self):
        assert "const CARD_DETAILED = 5;" in self._template()

    def test_ranked_items_past_the_five_earn_a_title(self):
        assert "const CARD_TITLE_ONLY = 5;" in self._template()

    def test_speakers_get_up_to_three_rows_per_item(self):
        assert "const CARD_SPEAKERS_PER_ITEM = 3;" in self._template()

    def test_engagement_is_the_last_thing_cut(self):
        """An item that drew a crowd keeps its detail and its speakers,
        paid for by the other items: the least-attended detailed rows
        demote first, then the most-attended item's speaker rows trim
        one at a time (only where a trim actually saves space), then
        title-only rows drop. Demoting the most-attended item itself is
        the last resort, and the digest is never cut (ADR 0022)."""
        source = self._template()
        demote = source.index("demote(demotable[0]);")
        trim = source.index("shownSpeakers.get(protectedItem).pop();")
        drop = source.index("dropped.push(titleOnly.pop());")
        last = source.index("demote(detailed[detailed.length - 1]);")
        assert demote < trim < drop < last

    def test_a_crowded_item_earns_detail_even_past_the_rank_cut(self):
        """June 24: the DEED item drew five speakers but ranked sixth,
        so it was title-only and its speakers never appeared inline.
        Engagement now promotes an item into the detailed five,
        displacing the least-attended one (ADR 0022)."""
        assert "const drawCard = byRank.slice().sort(" in self._template()

    def test_a_resident_shown_inline_leaves_the_residents_rollup(self):
        """The payload labels an unaffiliated speaker \"Resident\", which
        is not an organization: a resident shown inline must shrink the
        residents roll-up, not leave it counting them twice."""
        from app.speakers import UNAFFILIATED_LABEL
        assert UNAFFILIATED_LABEL == "Resident"
        assert "s.organization !== 'Resident'" in self._template()

    def test_the_digest_orgs_carry_their_items_categories(self):
        """\"Discover Saskatoon\" alone does not say whether it came
        about transit or the entertainment district; the digest row
        carries the item's category slugs so it can say."""
        from app.summarizer import speaker_roster
        out = speaker_roster([
            {"item_id": 1, "badges": [{"type": "cat-transit"}],
             "speakers": [{"name": "Robert Clipperton",
                           "organization": "Bus Riders of Saskatoon"}]},
        ])
        assert out["organizations"][0]["categories"] == ["cat-transit"]

    def test_a_digest_row_names_the_category(self):
        """One word from the filter's own category set -- an org in the
        digest is a name with no item beside it, so the row says what
        they came about. An inline speaker row has its item right above
        it and carries no chip."""
        source = self._template()
        digest = source[source.index("function digestHtml"):]
        assert "catChipHtml(cats[0])" in digest

    def test_a_consent_item_never_becomes_a_row(self):
        """Approved in one block vote without debate: nothing to
        summarize, nothing to name. The roll-up accounts for them."""
        assert "t.kind !== 'speaker' && !t.is_consent" in self._template()
        assert "consentRollupHtml" in self._template()

    def test_the_rollup_explains_itself_without_navigating(self):
        """\"Approved in consent\" is council's jargon, so the row carries
        a help icon — and the icon sits outside the link, because tapping
        it must explain, not open the meeting."""
        source = self._template()
        assert "consent-help-text" in source
        help_fn = source[source.index("function consentRollupHtml"):]
        assert help_fn.index("</a>") < help_fn.index("+ help")

    def test_the_digest_names_every_organization_not_shown_inline(self):
        """Inline speaker rows name the orgs they show; the digest names
        the rest. The union is always the full roster -- a representative
        few would hide who had a voice."""
        assert ".filter(o => !shownOrgs.has(o.label))" in self._template()

    def test_the_payload_carries_the_full_roster(self):
        """The card cannot digest what it is never told about."""
        from app.summarizer import speaker_roster
        out = speaker_roster([
            {"item_id": 1, "speakers": [
                {"name": "Robert Clipperton",
                 "organization": "Bus Riders of Saskatoon"},
                {"name": "A B", "organization": ""},
                {"name": "C D"},
            ]},
            {"item_id": 2, "speakers": [
                {"name": "Robert Clipperton",
                 "organization": "Bus Riders of Saskatoon"},
            ]},
        ])
        assert [o["label"] for o in out["organizations"]] == [
            "Bus Riders of Saskatoon"]
        assert out["resident_count"] == 2
        assert out["speaker_count"] == 3

    def test_a_filing_without_a_podium_is_not_a_voice(self):
        """An RTS filing proves intent, not attendance. The filing
        survives only if the transcript captured the remarks."""
        from app.summarizer import speaker_roster
        out = speaker_roster([
            {"item_id": 1, "speakers": [
                {"name": "Was There", "organization": "",
                 "source": "minutes"},
                {"name": "No Show", "organization": "Tron Group",
                 "source": "registered"},
                {"name": "Missed By Minutes", "organization": "",
                 "source": "registered", "said": ["Spoke."]},
            ]},
        ])
        assert out["speaker_count"] == 2
        assert [o["label"] for o in out["organizations"]] == []

    def test_the_chair_introducing_a_filing_speaker_vouches_for_them(self):
        """mark_heard: a registered name the chair said during the item
        did speak, whatever the substance pass produced."""
        from app.speakers import mark_heard
        item = {
            "time_start_ms": 2_760_000, "time_end_ms": 3_600_000,
            "speakers": [
                {"name": "Robert Clipperton", "source": "registered",
                 "organization": "Bus Riders of Saskatoon"},
                {"name": "No Show", "source": "registered",
                 "organization": ""},
            ],
        }
        segments = [
            {"start_ms": 2_800_000, "end_ms": 2_900_000,
             "text": "We'll go now to the first speaker Robert Clipperton "
                     "with Bus Riders of Saskatoon."},
            {"start_ms": 100, "end_ms": 200, "text": "outside the item"},
        ]
        mark_heard(item, segments)
        assert item["speakers"][0]["heard"] is True
        assert item["speakers"][0]["organization"] == "Bus Riders of Saskatoon"
        assert "heard" not in item["speakers"][1]

    def test_a_speaker_is_stamped_where_the_chair_named_them(self):
        """mark_timestamps: the first in-window segment naming the speaker
        is where their link seeks the video."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 2_760_000, "time_end_ms": 3_600_000,
            "speakers": [
                {"name": "Robert Clipperton", "source": "registered"},
                {"name": "No Show", "source": "registered"},
            ],
        }
        segments = [
            {"start_ms": 100, "end_ms": 200, "text": "outside the item"},
            {"start_ms": 2_800_000, "end_ms": 2_820_000,
             "text": "We'll go now to the first speaker Robert Clipperton."},
            {"start_ms": 2_820_000, "end_ms": 2_900_000,
             "text": "Thank you for having me."},
        ]
        mark_timestamps(item, segments)
        assert item["speakers"][0]["time_start_ms"] == 2_800_000
        assert "time_start_ms" not in item["speakers"][1]

    def test_speakers_are_ordered_by_when_they_spoke(self):
        """The roster follows the agenda; the podium does not."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 0, "time_end_ms": 10_000,
            "speakers": [
                {"name": "Karen Kobussen"},
                {"name": "Robert Clipperton"},
                {"name": "Never Introduced"},
            ],
        }
        segments = [
            {"start_ms": 1000, "end_ms": 2000,
             "text": "robert clipperton, welcome."},
            {"start_ms": 5000, "end_ms": 6000,
             "text": "karen kobussen, welcome."},
        ]
        mark_timestamps(item, segments)
        assert [s["name"] for s in item["speakers"]] == [
            "Robert Clipperton", "Karen Kobussen", "Never Introduced",
        ]

    def test_a_whisper_garbled_surname_still_stamps(self):
        """Wilgenhof came out of Whisper as Wilgunhof; a close rendering
        of a long surname is still its owner at the podium."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 0, "time_end_ms": 1000,
            "speakers": [{"name": "Rob Wilgenhof"}],
        }
        mark_timestamps(item, [{"start_ms": 400, "end_ms": 900,
                                "text": "the next speaker is robert wilgunhof."}])
        assert item["speakers"][0]["time_start_ms"] == 400

    def test_a_fuzzy_match_without_an_introduction_cue_does_not_stamp(self):
        """\"Gather\" sits at 0.86 from \"Gauthier\": a fuzzy hit in the
        middle of someone's remarks is a word, not a speaker."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 0, "time_end_ms": 1000,
            "speakers": [{"name": "Jean-Sébastien Gauthier"}],
        }
        mark_timestamps(item, [{"start_ms": 400, "end_ms": 900,
                                "text": "it can encourage people to stop, "
                                        "gather and interact with space."}])
        assert "time_start_ms" not in item["speakers"][0]

    def test_a_distinctive_first_name_with_a_garbled_surname_stamps(self):
        """Naytowhow came out as nitaohau -- past the surname rule, but
        a rare first name beside a surname-shaped word is still her."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 0, "time_end_ms": 1000,
            "speakers": [{"name": "Melissa Naytowhow"}],
        }
        mark_timestamps(item, [{"start_ms": 400, "end_ms": 900,
                                "text": "my name is melissa nitaohau."}])
        assert item["speakers"][0]["time_start_ms"] == 400

    def test_a_common_first_name_alone_does_not_stamp(self):
        """Every Robert in the transcript is not Robert Clipperton."""
        from app.speakers import mark_timestamps
        item = {
            "time_start_ms": 0, "time_end_ms": 1000,
            "speakers": [{"name": "Robert Clipperton"}],
        }
        mark_timestamps(item, [{"start_ms": 400, "end_ms": 900,
                                "text": "councillor robertson moved the motion."}])
        assert "time_start_ms" not in item["speakers"][0]

    def test_a_short_surname_needs_the_full_name(self):
        """Otherwise Taylor Street attends every meeting on 25th."""
        from app.speakers import mark_heard
        item = {
            "time_start_ms": 0, "time_end_ms": 1000,
            "speakers": [
                {"name": "Gordon Taylor", "source": "registered",
                 "organization": ""},
            ],
        }
        mark_heard(item, [{"start_ms": 0, "end_ms": 1000,
                           "text": "the brt station at taylor street"}])
        assert "heard" not in item["speakers"][0]

    def test_council_rows_are_chosen_without_the_speakers(self):
        """The filter that stops a delegate taking an agenda item's place."""
        assert "topics.filter(t => t.kind !== 'speaker' && !t.is_consent)" in self._template()

    def test_a_speaker_appears_only_beneath_their_item(self):
        """Speakers sit with the item they answered (ADR 0022). A
        speaker whose item is not on the card has no floating row --
        their organization is still named in the digest."""
        assert "spoke_to" not in self._template()

    def test_the_card_shows_up_to_three_speakers_per_item(self):
        from app.summarizer import CARD_SPEAKERS_PER_ITEM
        assert CARD_SPEAKERS_PER_ITEM == 3
        assert "const CARD_SPEAKERS_PER_ITEM = 3;" in self._template()


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
            1,
        )

    def test_the_organization_travels_on_the_row(self):
        """The name is the row's text; the organization rides with it
        and the row's colour carries who they came for. Speakers are
        rows, never chips (ADR 0022)."""
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
