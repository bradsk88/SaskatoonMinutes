"""Items a committee heard as one discussion.

eSCRIBE bookmark windows lag when two items are taken together, so
each speaker is stamped on both cards (G&P 2026-08-12: 6.3.2 with
7.1). ``mark_jointly_heard`` detects the pairing from the windows and
rosters alone — overlap plus a shared witness — and annotates the
items so the page draws one speaker list.
"""

from app.speakers import mark_jointly_heard


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
