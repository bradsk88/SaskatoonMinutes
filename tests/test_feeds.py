"""The Atom feeds: what qualifies, when a day publishes, and valid XML."""

import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from app import feeds
from app.summarizer import TAKEAWAY_ORDER

ATOM = "{http://www.w3.org/2005/Atom}"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def item(item_id, title, *, description=None, chips=None, minutes=None,
         vote="", recommendation="", speakers=None, consent=False):
    """One agenda item in the shape ``build_site`` hands to the feed."""
    summary = {}
    if description is not None:
        summary["description"] = description
    if chips is not None:
        summary["chips"] = chips
    built = {
        "item_id": item_id,
        "title": title,
        "vote_result": vote,
        "recommendation": recommendation,
        "speakers": speakers or [],
        "timestamp_inherited": consent,
    }
    if summary:
        built["summary"] = summary
    if minutes is not None:
        built["time_start_ms"] = 0
        built["time_end_ms"] = int(minutes * 60_000)
    return built


def meeting(meeting_id="m1", *, day="2026-07-20", body="City Council",
            slug="council", items=None, has_summaries=True):
    return {
        "meeting_id": meeting_id,
        "title": "Regular Business Meeting of City Council",
        "body": body,
        "body_slug": slug,
        "date": day,
        "has_summaries": has_summaries,
        "agenda_items": items or [],
    }


CHIP_DEBATE = {"category": "Debate Highlight", "text": "Councillor Ford argued the study is not neutral"}
CHIP_AFFECTED = {"category": "Who's Affected", "text": "Ward 8 residents"}


class SubstanceGate(unittest.TestCase):
    def test_an_item_with_a_description_qualifies(self):
        self.assertTrue(feeds.qualifies(item(1, "A", description=["It does a thing."])))

    def test_an_item_with_an_interpretive_chip_qualifies(self):
        self.assertTrue(feeds.qualifies(item(1, "A", chips=[CHIP_DEBATE])))

    def test_a_bare_item_does_not_qualify(self):
        self.assertFalse(feeds.qualifies(item(1, "A")))

    def test_a_long_debate_with_nothing_written_does_not_qualify(self):
        """Duration is not substance -- that is the whole of item 12's gate."""
        self.assertFalse(feeds.qualifies(item(1, "A", minutes=90)))

    def test_an_outcome_chip_alone_does_not_qualify(self):
        """Outcome is extracted deterministically and says what, not why."""
        chips = [{"category": "Outcome", "text": "Approved"}]
        self.assertFalse(feeds.qualifies(item(1, "A", chips=chips)))

    def test_a_legacy_summary_without_a_description_still_qualifies_on_chips(self):
        self.assertTrue(feeds.qualifies(item(1, "A", chips=[CHIP_AFFECTED])))


class Ranking(unittest.TestCase):
    def test_longest_discussion_first(self):
        items = [
            item(1, "Short", description=["x"], minutes=5),
            item(2, "Long", description=["x"], minutes=155),
            item(3, "Middling", description=["x"], minutes=40),
        ]
        got = [i["item_id"] for i in feeds.qualifying_items(items)]
        self.assertEqual([2, 3, 1], got)

    def test_a_broken_span_does_not_rank_first(self):
        """9,876 minutes is a broken end bookmark, not the item of the year."""
        items = [
            item(1, "Broken", description=["x"], minutes=9876),
            item(2, "Real", description=["x"], minutes=44),
        ]
        got = [i["item_id"] for i in feeds.qualifying_items(items)]
        self.assertEqual([2, 1], got)

    def test_a_consent_item_does_not_borrow_its_parents_time(self):
        items = [
            item(1, "Consent", description=["x"], minutes=60, consent=True),
            item(2, "Debated", description=["x"], minutes=10),
        ]
        got = [i["item_id"] for i in feeds.qualifying_items(items)]
        self.assertEqual([2, 1], got)

    def test_untimed_items_keep_agenda_order(self):
        items = [item(n, f"Item {n}", description=["x"]) for n in (1, 2, 3)]
        got = [i["item_id"] for i in feeds.qualifying_items(items)]
        self.assertEqual([1, 2, 3], got)

    def test_capped_per_meeting(self):
        items = [item(n, f"Item {n}", description=["x"], minutes=n)
                 for n in range(1, 20)]
        self.assertEqual(
            feeds.MAX_ITEMS_PER_MEETING, len(feeds.qualifying_items(items)),
        )


class Takeaway(unittest.TestCase):
    def test_the_more_telling_category_wins(self):
        chosen = feeds.takeaway(item(1, "A", chips=[CHIP_AFFECTED, CHIP_DEBATE]))
        self.assertEqual("Debate Highlight", chosen["category"])

    def test_the_card_and_the_feed_rank_takeaways_identically(self):
        """``index.html`` cannot import Python, so this holds the copy honest.

        A card leading with the debate and a feed entry leading with who
        it affects, for the same item, is drift nobody would notice.
        """
        path = os.path.join(PROJECT_ROOT, "app", "templates", "index.html")
        with open(path) as handle:
            source = handle.read()
        match = re.search(r"TAKEAWAY_ORDER\s*=\s*\[(.*?)\]", source, re.DOTALL)
        self.assertIsNotNone(match, "TAKEAWAY_ORDER not found in index.html")
        in_template = re.findall(r"'([^']+)'|\"([^\"]+)\"", match.group(1))
        flat = tuple(a or b for a, b in in_template)
        self.assertEqual(TAKEAWAY_ORDER, flat)


class Settling(unittest.TestCase):
    def test_a_day_waits_for_its_summaries(self):
        day = [meeting("a", has_summaries=True), meeting("b", has_summaries=False)]
        self.assertFalse(feeds.is_settled(day, date(2026, 7, 21)))

    def test_a_day_publishes_once_every_meeting_is_summarized(self):
        day = [meeting("a"), meeting("b")]
        self.assertTrue(feeds.is_settled(day, date(2026, 7, 21)))

    def test_a_meeting_with_no_video_stops_holding_the_day_after_a_week(self):
        day = [meeting("a", day="2026-07-01", has_summaries=False)]
        self.assertTrue(feeds.is_settled(day, date(2026, 7, 20)))

    def test_an_unsettled_day_publishes_nothing(self):
        meetings = [meeting("a", has_summaries=False,
                            items=[item(1, "A", description=["x"])])]
        xml = feeds.build_day_feed(meetings, date(2026, 7, 21))
        self.assertEqual(0, len(ET.fromstring(xml).findall(f"{ATOM}entry")))


class DayEntries(unittest.TestCase):
    def setUp(self):
        self.meetings = [
            meeting("council-1", day="2026-07-20", body="City Council",
                    slug="council",
                    items=[item(11, "Wildwood Golf Course",
                                description=["City to study relocating it."],
                                chips=[CHIP_DEBATE], minutes=44,
                                vote="Carried (6 to 5)",
                                recommendation="That the report be approved")]),
            meeting("planning-1", day="2026-07-20", body="Planning & Dev",
                    slug="planning",
                    items=[item(21, "Rezoning 33rd Street",
                                description=["Rezones a lot."], minutes=9)]),
            meeting("council-2", day="2026-07-13", body="City Council",
                    slug="council",
                    items=[item(31, "Transit fares",
                                description=["Raises fares."], minutes=58)]),
        ]

    def feed(self):
        return ET.fromstring(feeds.build_day_feed(self.meetings, date(2026, 7, 27)))

    def test_a_day_entry_carries_the_union_of_its_items_topics(self):
        entry = self.feed().findall(f"{ATOM}entry")[0]
        terms = [c.get("term") for c in entry.findall(f"{ATOM}category")]
        self.assertEqual(["council", "planning", "Debate Highlight"], terms)

    def test_two_bodies_on_one_day_are_one_entry(self):
        entries = self.feed().findall(f"{ATOM}entry")
        self.assertEqual(2, len(entries))

    def test_the_title_names_the_day_and_its_bodies(self):
        first = self.feed().findall(f"{ATOM}entry")[0]
        self.assertEqual(
            "July 20, 2026 · City Council, Planning & Dev",
            first.find(f"{ATOM}title").text,
        )

    def test_newest_day_first(self):
        titles = [e.find(f"{ATOM}title").text
                  for e in self.feed().findall(f"{ATOM}entry")]
        self.assertTrue(titles[0].startswith("July 20"))
        self.assertTrue(titles[1].startswith("July 13"))

    def test_a_day_entry_carries_every_body_as_a_heading(self):
        content = self.feed().findall(f"{ATOM}entry")[0].find(f"{ATOM}content").text
        self.assertIn("<h3>City Council</h3>", content)
        self.assertIn("<h3>Planning &amp; Dev</h3>", content)

    def test_items_link_to_their_own_anchor(self):
        content = self.feed().findall(f"{ATOM}entry")[0].find(f"{ATOM}content").text
        self.assertIn(
            "https://yxeminutes.ca/meeting/council-1.html#item-11", content,
        )

    def test_a_multi_body_day_points_home_rather_than_at_one_meeting(self):
        link = self.feed().findall(f"{ATOM}entry")[0].find(f"{ATOM}link")
        self.assertEqual("https://yxeminutes.ca/", link.get("href"))

    def test_a_single_meeting_day_points_at_that_meeting(self):
        link = self.feed().findall(f"{ATOM}entry")[1].find(f"{ATOM}link")
        self.assertEqual(
            "https://yxeminutes.ca/meeting/council-2.html", link.get("href"),
        )

    def test_every_day_entry_carries_the_ai_disclosure(self):
        for entry in self.feed().findall(f"{ATOM}entry"):
            self.assertIn("AI-generated", entry.find(f"{ATOM}content").text)

    def test_timestamps_come_from_the_meeting_date_not_the_build(self):
        first = self.feed().findall(f"{ATOM}entry")[0]
        self.assertEqual(
            "2026-07-20T12:00:00-06:00", first.find(f"{ATOM}updated").text,
        )

    def test_an_entry_lands_on_its_own_day_in_saskatoon(self):
        """Midnight UTC is the evening before here, and read a day early."""
        stamp = datetime.fromisoformat(
            self.feed().findall(f"{ATOM}entry")[0].find(f"{ATOM}updated").text
        )
        local = stamp.astimezone(timezone(timedelta(hours=-6)))
        self.assertEqual(date(2026, 7, 20), local.date())

    def test_rebuilding_on_a_later_day_changes_nothing(self):
        """The deploy runs six times a day; subscribers must see one entry."""
        first = feeds.build_day_feed(self.meetings, date(2026, 7, 27))
        later = feeds.build_day_feed(self.meetings, date(2026, 8, 3))
        self.assertEqual(first, later)


class ItemEntries(unittest.TestCase):
    def setUp(self):
        self.meetings = [
            meeting("council-1", day="2026-07-20",
                    items=[
                        item(11, "WILDWOOD GOLF COURSE RELOCATION",
                             description=["City to study relocating it."],
                             chips=[CHIP_DEBATE], minutes=44,
                             vote="Carried (6 to 5)",
                             recommendation="That the report be approved",
                             speakers=[{"name": "Jane Doe",
                                        "organization": "Nutrien Wonderhub"},
                                       {"name": "Bob Smith",
                                        "organization": ""}]),
                        item(12, "Routine", minutes=3),
                    ]),
        ]

    def entry(self):
        xml = feeds.build_item_feed(self.meetings, date(2026, 7, 27))
        return ET.fromstring(xml).findall(f"{ATOM}entry")[0]

    def test_one_entry_per_qualifying_item(self):
        xml = feeds.build_item_feed(self.meetings, date(2026, 7, 27))
        self.assertEqual(1, len(ET.fromstring(xml).findall(f"{ATOM}entry")))

    def test_a_shouting_agenda_title_is_set_in_title_case(self):
        self.assertEqual(
            "Wildwood Golf Course Relocation", self.entry().find(f"{ATOM}title").text,
        )

    def test_the_id_is_the_permalink(self):
        self.assertEqual(
            "https://yxeminutes.ca/meeting/council-1.html#item-11",
            self.entry().find(f"{ATOM}id").text,
        )

    def test_the_context_line_leads_with_the_outcome_body_and_date(self):
        content = self.entry().find(f"{ATOM}content").text
        self.assertIn("Approved (6-5) · City Council · July 20, 2026", content)

    def test_the_takeaway_travels(self):
        content = self.entry().find(f"{ATOM}content").text
        self.assertIn("Councillor Ford argued the study is not neutral", content)

    def test_speakers_are_one_line_with_their_organizations(self):
        content = self.entry().find(f"{ATOM}content").text
        self.assertIn("Spoke: Jane Doe (Nutrien Wonderhub), Bob Smith (Resident)",
                      content)

    def test_remarks_stay_on_the_detail_page(self):
        content = self.entry().find(f"{ATOM}content").text
        self.assertNotIn("<li>Jane Doe", content)

    def test_the_body_is_a_category_readers_can_filter_on(self):
        self.assertEqual(
            "council", self.entry().find(f"{ATOM}category").get("term"),
        )

    def test_chip_categories_are_tags_a_reader_can_filter_on(self):
        terms = [c.get("term") for c in self.entry().findall(f"{ATOM}category")]
        self.assertEqual(["council", "Debate Highlight"], terms)


class Retention(unittest.TestCase):
    def test_day_feed_is_capped(self):
        meetings = [
            meeting(f"m{n}", day=f"2026-0{1 + n // 28}-{1 + n % 28:02d}",
                    items=[item(1, "A", description=["x"])])
            for n in range(50)
        ]
        xml = feeds.build_day_feed(meetings, date(2026, 12, 1))
        self.assertEqual(
            feeds.MAX_DAY_ENTRIES,
            len(ET.fromstring(xml).findall(f"{ATOM}entry")),
        )

    def test_item_feed_is_capped(self):
        meetings = [
            meeting(f"m{n}", day=f"2026-0{1 + n // 28}-{1 + n % 28:02d}",
                    items=[item(i, f"Item {i}", description=["x"])
                           for i in range(1, 9)])
            for n in range(50)
        ]
        xml = feeds.build_item_feed(meetings, date(2026, 12, 1))
        self.assertEqual(
            feeds.MAX_ITEM_ENTRIES,
            len(ET.fromstring(xml).findall(f"{ATOM}entry")),
        )


class Escaping(unittest.TestCase):
    """One unescaped ``&`` makes the file invalid, and readers reject it."""

    HOSTILE = 'Parks & Rec <script>alert("x")</script> \'quoted\' > done'

    def meetings(self):
        return [meeting("m1", body=self.HOSTILE, items=[
            item(1, self.HOSTILE, description=[self.HOSTILE],
                 chips=[{"category": "Debate Highlight", "text": self.HOSTILE}],
                 speakers=[{"name": self.HOSTILE, "organization": self.HOSTILE}]),
        ])]

    def test_both_feeds_parse_with_hostile_text_everywhere(self):
        for xml in feeds.build_feeds(self.meetings(), date(2026, 7, 27)).values():
            ET.fromstring(xml)

    def test_the_ampersand_survives_the_round_trip(self):
        xml = feeds.build_item_feed(self.meetings(), date(2026, 7, 27))
        entry = ET.fromstring(xml).findall(f"{ATOM}entry")[0]
        self.assertIn("Parks & Rec", entry.find(f"{ATOM}title").text)

    def test_upstream_markup_does_not_become_markup_in_the_entry(self):
        xml = feeds.build_item_feed(self.meetings(), date(2026, 7, 27))
        entry = ET.fromstring(xml).findall(f"{ATOM}entry")[0]
        content = entry.find(f"{ATOM}content").text
        self.assertNotIn("<script>", content)
        self.assertIn("&lt;script&gt;", content)


class FeedDocument(unittest.TestCase):
    def test_both_feeds_declare_themselves(self):
        built = feeds.build_feeds([meeting(items=[item(1, "A", description=["x"])])],
                                  date(2026, 7, 27))
        self.assertEqual({"feed.xml", "feed-items.xml"}, set(built))
        for path, xml in built.items():
            root = ET.fromstring(xml)
            self_link = [l for l in root.findall(f"{ATOM}link")
                         if l.get("rel") == "self"]
            self.assertEqual(
                f"https://yxeminutes.ca/{path}", self_link[0].get("href"),
            )

    def test_an_empty_archive_still_produces_a_valid_feed(self):
        for xml in feeds.build_feeds([], date(2026, 7, 27)).values():
            root = ET.fromstring(xml)
            self.assertEqual(0, len(root.findall(f"{ATOM}entry")))


if __name__ == "__main__":
    unittest.main()
