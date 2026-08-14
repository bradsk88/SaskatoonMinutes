"""Items a committee heard as one discussion.

eSCRIBE bookmark windows lag when two items are taken together, so
each speaker is stamped on both cards (G&P 2026-08-12: 6.3.2 with
7.1). ``mark_jointly_heard`` detects the pairing from the windows and
rosters alone — overlap plus a shared witness — and annotates the
items so the page draws one speaker list.
"""

import json

from app.item_categorizer import GeminiExtractor, item_transcript_text
from app.models import AgendaItem, Speaker, Transcript
from app.speakers import group_window, mark_jointly_heard
from scripts.summarize_meetings import summarize_meeting


def _item(item_id, section, start_ms, end_ms, speakers=()):
    return {
        "item_id": item_id,
        "section_number": section,
        "time_start_ms": start_ms,
        "time_end_ms": end_ms,
        "speakers": [{"name": n} for n in speakers],
    }


def test_gpc_2026_08_12_pairs_632_with_71():
    """The ground truth: windows 7:08–3:36:29 vs 1:10:51–3:08:50,
    rosters sharing Kelsey Ford."""
    items = [
        _item(24, "6.3.2", 428_270, 12_988_978,
              ["Godfred Yeboah", "Tristan Surtees", "Kelsey Ford"]),
        _item(25, "7.1", 4_251_000, 11_330_000,
              ["Kelsey Ford", "Em Ironstar", "Daniel Macdonald"]),
    ]
    mark_jointly_heard(items)
    # 6.3.2's window starts first: the discussion began there, so it
    # keeps the list.
    assert items[0]["heard_with"] == {
        "primary_item_id": 24,
        "primary_section": "6.3.2",
        "partners": ["7.1"],
    }
    assert items[1]["heard_with"]["primary_item_id"] == 24
    assert items[1]["heard_with"]["partners"] == ["6.3.2"]


def test_overlap_without_a_shared_speaker_is_just_lag():
    """Council 2026-06-24: four items brush windows, none share a
    speaker — sloppy bookmarks, not one discussion."""
    items = [
        _item(1, "8.1.4", 1_441_000, 1_931_000, ["Alice"]),
        _item(2, "8.4.1", 1_430_000, 1_483_000, ["Bob"]),
    ]
    mark_jointly_heard(items)
    assert "heard_with" not in items[0]
    assert "heard_with" not in items[1]


def test_parent_section_spanning_children_is_not_a_joint_hearing():
    """6.'s window contains 6.1's because hierarchy, not because they
    were debated together."""
    items = [
        _item(1, "6.", 0, 3_600_000, ["Alice"]),
        _item(2, "6.1", 60_000, 600_000, ["Alice"]),
    ]
    mark_jointly_heard(items)
    assert all("heard_with" not in i for i in items)


def test_brushing_windows_under_half_the_shorter_is_not_joint():
    items = [
        _item(1, "8.1", 0, 3_600_000, ["Alice"]),
        _item(2, "8.2", 3_300_000, 4_200_000, ["Alice"]),
    ]
    mark_jointly_heard(items)
    assert all("heard_with" not in i for i in items)


def test_no_roster_no_group():
    items = [
        _item(1, "8.1", 0, 3_600_000),
        _item(2, "8.2", 60_000, 3_000_000),
    ]
    mark_jointly_heard(items)
    assert all("heard_with" not in i for i in items)


def test_three_items_heard_together_group_as_one():
    items = [
        _item(1, "6.1", 0, 7_200_000, ["Alice"]),
        _item(2, "6.2", 300_000, 7_000_000, ["Alice"]),
        _item(3, "7.1", 600_000, 6_900_000, ["Alice"]),
    ]
    mark_jointly_heard(items)
    assert all(i["heard_with"]["primary_item_id"] == 1 for i in items)
    assert items[1]["heard_with"]["partners"] == ["6.1", "7.1"]


# ── The summarize pipeline slices grouped items on the union window ──────────
#
# G&P 2026-08-12 ground truth, stamped permanently (see CLAUDE.md): 7.1's
# bookmark opens at 1:10:51 but Kelsey Ford spoke at 35:20 and Em Ironstar
# at 42:20, inside 6.3.2's window.  A summary sliced on 7.1's raw bookmark
# never sees them speak; the remarks pass comes back empty and their cards
# show a stamp with no substance.


def _gpc_items():
    return [
        _item(24, "6.3.2", 428_270, 12_988_978,
              ["Godfred Yeboah", "Tristan Surtees", "Kelsey Ford"]),
        _item(25, "7.1", 4_251_000, 11_330_000,
              ["Kelsey Ford", "Em Ironstar", "Daniel Macdonald"]),
    ]


# One segment where each delegate was introduced, on the real clocks.
_GPC_SEGMENTS = [
    {"start_ms": 2_100_000, "end_ms": 2_140_000,
     "text": "As we move to our next speaker Kelsey Ford."},
    {"start_ms": 2_520_000, "end_ms": 2_560_000,
     "text": "The next speaker is Em Ironstar."},
    {"start_ms": 8_000_000, "end_ms": 8_030_000,
     "text": "Committee discussion resumed on the recommendation."},
]


def test_group_window_is_the_union_of_the_groups_windows():
    items = _gpc_items()
    mark_jointly_heard(items)
    assert group_window(items[0], items) == (428_270, 12_988_978)
    assert group_window(items[1], items) == (428_270, 12_988_978)


def test_group_window_is_none_for_a_standalone_item():
    items = [_item(1, "8.1", 0, 3_600_000, ["Alice"])]
    mark_jointly_heard(items)
    assert group_window(items[0], items) is None


def test_union_slice_catches_speakers_the_raw_bookmark_missed():
    items = _gpc_items()
    mark_jointly_heard(items)
    own = item_transcript_text(items[1], _GPC_SEGMENTS)
    assert "Kelsey Ford" not in own
    assert "Em Ironstar" not in own
    window = group_window(items[1], items)
    union = item_transcript_text(items[1], _GPC_SEGMENTS, window=window)
    assert "Kelsey Ford" in union
    assert "Em Ironstar" in union


def test_window_override_does_not_mutate_the_item():
    """The item's own bookmarks still belong to the page's video links."""
    items = _gpc_items()
    mark_jointly_heard(items)
    item_transcript_text(
        items[1], _GPC_SEGMENTS, window=group_window(items[1], items),
    )
    assert items[1]["time_start_ms"] == 4_251_000
    assert items[1]["time_end_ms"] == 11_330_000


class _FakeSource:
    def __init__(self, agenda_items):
        self._detail = type(
            "Detail", (),
            {"agenda_items": agenda_items, "video_url": ""},
        )()

    def load_detail(self, meeting_id):
        return self._detail


class _FakeTranscriptCache:
    def __init__(self, segments):
        self._transcript = Transcript.from_dict(segments)

    def load(self, meeting_id):
        return self._transcript


def _capturing_extractor(captured):
    """Answers both Gemini calls and records every prompt it is shown."""

    def _generate(prompt, allowed):
        captured.append(prompt)
        if "registered to address" in prompt:
            # The remarks pass: report Kelsey Ford only when the slice
            # actually contains her introduction.
            said = ["Argued the corridor study ignored growth."] \
                if "As we move to our next speaker Kelsey Ford" in prompt \
                else []
            return json.dumps({"speakers": [{
                "name": "Kelsey Ford", "organization": "",
                "said": said, "stance": "concern",
            }]})
        return json.dumps({"description": ["Advanced the corridor study."],
                           "chips": []})

    return GeminiExtractor(api_key=None, generate=_generate)


def test_summarize_meeting_summarizes_grouped_items_on_the_union_window():
    """End to end: 7.1's summary finds the speakers its bookmark hid."""
    agenda = [
        AgendaItem(
            item_id=24, title="Growth Plan Corridor Study", content="report",
            section_number="6.3.2",
            time_start_ms=428_270, time_end_ms=12_988_978,
            speakers=[Speaker(name=n) for n in (
                "Godfred Yeboah", "Tristan Surtees", "Kelsey Ford")],
        ),
        AgendaItem(
            item_id=25, title="Sector Plan Amendment", content="report",
            section_number="7.1",
            time_start_ms=4_251_000, time_end_ms=11_330_000,
            speakers=[Speaker(name=n) for n in (
                "Kelsey Ford", "Em Ironstar", "Daniel Macdonald")],
        ),
    ]
    captured = []
    summaries = summarize_meeting(
        _FakeSource(agenda), "m", _capturing_extractor(captured),
        _FakeTranscriptCache(_GPC_SEGMENTS),
    )

    seventy_one_prompts = [
        p for p in captured if "Agenda item title: Sector Plan Amendment" in p
    ]
    assert seventy_one_prompts, "7.1 was never summarized"
    assert any(
        "As we move to our next speaker Kelsey Ford" in p
        for p in seventy_one_prompts
    ), "7.1's slice still starts at its own bookmark"

    # Her remarks landed on the partner item's cached summary — the card on
    # the merged list reads its substance from there, per item.
    ford = next(
        s for s in summaries["25"].speakers if s.name == "Kelsey Ford"
    )
    assert ford.said == ["Argued the corridor study ignored growth."]


# ── Speaker matching scans the union window too ──────────────────────────────
#
# Same meeting, same lag, one layer up: mark_heard / mark_timestamps used
# each item's own bookmark, so every delegate who spoke before 7.1's
# 1:10:51 bookmark was invisible on 7.1's card.  User-verified ground
# truth, stamped permanently (see CLAUDE.md): Deneh'Cho Thompson was
# introduced at ~49:30, John Penner at 55:05, Jake Moore at 59:00, and
# Betsy Rosenwald at 1:06:00 — all four missed on the published page.
# Whisper's renderings below are verbatim from the meeting's transcript.

from app.speakers import mark_heard, mark_timestamps


def _delegate_item(item_id, section, start_ms, end_ms, names):
    return {
        "item_id": item_id,
        "section_number": section,
        "time_start_ms": start_ms,
        "time_end_ms": end_ms,
        "speakers": [
            {"name": n, "source": "registered", "organization": ""}
            for n in names
        ],
    }


def _gpc_delegate_items():
    return [
        _delegate_item(24, "6.3.2", 428_270, 12_988_978,
                       ["Kelsey Ford", "Daniel Macdonald"]),
        _delegate_item(25, "7.1", 4_251_000, 11_330_000, [
            "Em Ironstar", "Deneh’Cho Thompson", "John Penner",
            "Jake Moore", "Betsy Rosenwald", "Daniel Macdonald",
        ]),
    ]


def _seg(minutes, text):
    start = int(minutes * 60_000)
    return {"start_ms": start, "end_ms": start + 3_000, "text": text}


_DELEGATE_SEGMENTS = [
    _seg(42.39, "And I own staff for five minutes, introduce yourself "
                "when you're ready."),
    _seg(42.68, "So my name is Emma Armstrong. I'm the executive "
                "director of the Saskatchewan Arts Alliance."),
    _seg(49.40, "Our next speaker is Denika Short Thompson."),
    _seg(49.81, "I'm an associate professor in the School for the Arts at the University of Saskatchewan."),
    _seg(49.89, "I'm also the chair of the Board of the Saskatchewan Association of Theatre Professionals."),
    _seg(49.99, "I say those things to establish my expertise, not to speak on behalf of those organizations. I am choosing to speak as a private citizen today."),
    _seg(55.08, "Our next speaker is John Pan."),
    _seg(55.45, "My name is John Paner."),
    _seg(58.74, "On next speaker is Jake Moore."),
    _seg(65.90, "I'm going to go and next speaker is Beth's tea for "
                "Rosenwall."),
    _seg(66.51, "Yes, I'm Betsy Rosenwalled. I'm an artist and a member "
                "of Saskatoon's art community and a resident of Ward 1."),
    # A councillor referring to Jake Moore during questions, half an
    # hour after he spoke — a reference, not an introduction.
    _seg(90.92, "What am I calling Jake Moore? I think knows more about "
                "this. But yes, I think that there could be more "
                "consultation."),
]


def _stamps(item):
    return {s["name"]: s.get("time_start_ms") for s in item["speakers"]}


def test_delegates_before_the_bookmark_are_stamped_on_the_union_window():
    items = _gpc_delegate_items()
    mark_jointly_heard(items)
    motion = items[1]
    window = group_window(motion, items)
    mark_timestamps(motion, _DELEGATE_SEGMENTS, window=window)
    stamps = _stamps(motion)

    # User-stamped facts: ~49:30, 55:05, 59:00, 1:06:00.  The stamp
    # lands on the sentence that names them, seconds from the fact.
    assert 49.0 * 60_000 <= stamps["Deneh’Cho Thompson"] <= 50.0 * 60_000
    assert 55.0 * 60_000 <= stamps["John Penner"] <= 56.0 * 60_000
    assert 58.5 * 60_000 <= stamps["Jake Moore"] <= 59.5 * 60_000
    assert 65.5 * 60_000 <= stamps["Betsy Rosenwald"] <= 66.5 * 60_000


def test_an_introduction_beats_a_later_reference():
    """Jake Moore's card linked to 90:56 — a councillor saying his name
    during questions — because his real introduction at 58:44 sat
    outside 7.1's bookmark."""
    items = _gpc_delegate_items()
    mark_jointly_heard(items)
    motion = items[1]
    mark_timestamps(motion, _DELEGATE_SEGMENTS,
                    window=group_window(motion, items))
    jake = _stamps(motion)["Jake Moore"]
    assert jake < 60.0 * 60_000


def test_garbled_delegates_are_heard_on_the_union_window():
    items = _gpc_delegate_items()
    mark_jointly_heard(items)
    motion = items[1]
    mark_heard(motion, _DELEGATE_SEGMENTS,
               window=group_window(motion, items))
    heard = {s["name"]: s.get("heard") for s in motion["speakers"]}
    assert heard["Em Ironstar"] is True          # "Emma Armstrong"
    assert heard["Deneh’Cho Thompson"] is True   # "Denika Short Thompson"
    assert heard["John Penner"] is True          # "John Paner"
    assert heard["Betsy Rosenwald"] is True      # "Betsy Rosenwalled"


def test_org_is_read_beside_the_garbled_name():
    """Em Ironstar filed as herself but spoke as the Saskatchewan Arts
    Alliance's executive director — audible only through Whisper's
    "Emma Armstrong", which an exact-name org search can never find."""
    items = _gpc_delegate_items()
    mark_jointly_heard(items)
    motion = items[1]
    mark_heard(motion, _DELEGATE_SEGMENTS,
               window=group_window(motion, items))
    orgs = {s["name"]: s["organization"] for s in motion["speakers"]}
    assert orgs["Em Ironstar"] == "Saskatchewan Arts Alliance"
    # "A member of Saskatoon's art community" is a neighbour, not an
    # organization — the chip stays "Resident".
    assert orgs["Betsy Rosenwald"] == ""
    # Deneh'Cho named her boards and then said she was *not* speaking
    # for them — a disclaimed org is worse than none.
    assert orgs["Deneh’Cho Thompson"] == ""
