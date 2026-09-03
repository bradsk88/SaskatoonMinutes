"""The Atom feeds: what qualifies, when a meeting publishes, and valid XML."""

import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from app import feeds
from app.feeds import SASKATOON_TZ
from app.summarizer import TAKEAWAY_ORDER

ATOM = "{http://www.w3.org/2005/Atom}"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now(day: str, hour: int = 12, minute: int = 0) -> datetime:
    """A Saskatoon datetime on an ISO day, for the settled feeds' clock."""
    y, m, d = (int(part) for part in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=SASKATOON_TZ)


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
            slug="council", items=None, has_summaries=True, has_video=True,
            start_time=""):
    return {
        "meeting_id": meeting_id,
        "title": "Regular Business Meeting of City Council",
        "body": body,
        "body_slug": slug,
        "date": day,
        "start_time": start_time,
        "has_summaries": has_summaries,
        "has_video": has_video,
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
    """When a meeting stops waiting and publishes.

    A meeting settles the instant it has real summaries, or 12 hours
    after it sat with no recording (the site's not-recorded line), or
    seven days after it sat with a recording but no summary (the
    pipeline-broke escape).
    """

    def test_a_no_video_meeting_waits_twelve_hours(self):
        m = meeting("a", day="2026-07-20", start_time="09:00",
                    has_summaries=False, has_video=False)
        self.assertFalse(feeds.is_settled(m, _now("2026-07-20", 20, 29)))
        self.assertTrue(feeds.is_settled(m, _now("2026-07-20", 21, 0)))

    def test_a_no_video_meeting_without_a_time_settles_at_noon(self):
        m = meeting("a", day="2026-07-20", start_time="",
                    has_summaries=False, has_video=False)
        self.assertFalse(feeds.is_settled(m, _now("2026-07-20", 11, 59)))
        self.assertTrue(feeds.is_settled(m, _now("2026-07-20", 12, 0)))

    def test_a_meeting_publishes_once_it_has_real_summaries(self):
        self.assertTrue(
            feeds.is_settled(
                meeting("a", has_summaries=True), _now("2026-07-20", 10)))

    def test_a_meeting_waits_without_real_summaries(self):
        # Provisional coverage is not real summaries: the meeting keeps
        # waiting for the video and the transcript.
        m = meeting("a", day="2026-07-20", start_time="09:00",
                    has_summaries=False, has_video=True)
        self.assertFalse(feeds.is_settled(m, _now("2026-07-20", 20)))

    def test_a_video_meeting_without_summaries_escapes_after_a_week(self):
        m = meeting("a", day="2026-07-20", start_time="09:00",
                    has_summaries=False, has_video=True)
        # Seven days after the start (2026-07-27 09:00), not the date.
        self.assertFalse(
            feeds.is_settled(m, _now("2026-07-27", 8, 0)))
        self.assertTrue(
            feeds.is_settled(m, _now("2026-07-27", 9, 0)))

    def test_a_meeting_does_not_wait_on_a_same_day_sibling(self):
        """One summarized meeting publishes even if a sibling on the same
        day lacks summaries: a meeting settles on its own, not with the
        day.
        """
        ready = meeting("a", day="2026-07-20", start_time="09:00",
                        has_summaries=True,
                        items=[item(1, "A", description=["x"])])
        waiting = meeting("b", day="2026-07-20", start_time="09:00",
                          body="Planning & Dev", slug="planning",
                          has_summaries=False, has_video=False,
                          items=[item(1, "B", description=["x"])])
        # 11h30m after the meeting: the no-video sibling is still
        # waiting, so only the summarized one publishes.
        xml = feeds.build_meeting_feed([ready, waiting], _now("2026-07-20", 20, 30))
        titles = [e.find(f"{ATOM}title").text
                  for e in ET.fromstring(xml).findall(f"{ATOM}entry")]
        self.assertEqual(["July 20, 2026 · City Council"], titles)

    def test_an_unsettled_meeting_publishes_nothing(self):
        meetings = [meeting("a", day="2026-07-20", start_time="09:00",
                            has_summaries=False, has_video=False,
                            items=[item(1, "A", description=["x"])])]
        xml = feeds.build_meeting_feed(meetings, _now("2026-07-20", 19))
        self.assertEqual(0, len(ET.fromstring(xml).findall(f"{ATOM}entry")))


class NotRecorded(unittest.TestCase):
    """A meeting that settles without a recording says so in the feed.

    Real summaries need a transcript and a transcript needs a video, so
    a meeting that settles by the clock (12 hours after it sat) is one
    the City had not posted a video for. Its entry carries provisional,
    agenda-derived content, and the note keeps it from reading as an
    account of the discussion.
    """

    def _no_video(self, day="2026-07-01", start_time="09:00", **kw):
        return meeting(
            "nv", day=day, start_time=start_time, has_video=False,
            has_summaries=False,
            items=[item(1, "A", description=["From the agenda."])], **kw)

    def test_no_video_settled_by_time_carries_the_note(self):
        xml = feeds.build_meeting_feed(
            [self._no_video()], _now("2026-07-01", 21, 0))
        content = ET.fromstring(xml).find(f"{ATOM}entry")\
            .find(f"{ATOM}content").text
        self.assertIn("has been posted", content)

    def test_no_video_within_twelve_hours_publishes_nothing(self):
        """The incident: it should have waited, not published thin."""
        xml = feeds.build_meeting_feed(
            [self._no_video(start_time="09:00")], _now("2026-07-01", 20, 30))
        self.assertEqual(
            0, len(ET.fromstring(xml).findall(f"{ATOM}entry")))

    def test_a_video_meeting_carries_no_note(self):
        xml = feeds.build_meeting_feed(
            [meeting("v", day="2026-07-01", has_video=True,
                     has_summaries=True,
                     items=[item(1, "A", description=["x"])])],
            _now("2026-07-01", 21, 0))
        content = ET.fromstring(xml).find(f"{ATOM}entry")\
            .find(f"{ATOM}content").text
        self.assertNotIn("has been posted", content)

    def test_item_feed_carries_the_note(self):
        xml = feeds.build_item_feed(
            [self._no_video()], _now("2026-07-01", 21, 0))
        content = ET.fromstring(xml).find(f"{ATOM}entry")\
            .find(f"{ATOM}content").text
        self.assertIn("has been posted", content)


class MeetingEntries(unittest.TestCase):
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
        return ET.fromstring(
            feeds.build_meeting_feed(self.meetings, _now("2026-07-27")))

    def entries(self):
        return self.feed().findall(f"{ATOM}entry")

    def test_each_meeting_is_its_own_entry(self):
        """Two bodies on one day are two entries, not one.

        A busy Tuesday is three entries, not a single day entry.
        """
        self.assertEqual(3, len(self.entries()))

    def test_a_meeting_entry_carries_its_items_topics(self):
        first = self.entries()[0]
        terms = [c.get("term") for c in first.findall(f"{ATOM}category")]
        self.assertEqual(["council", "Debate Highlight"], terms)

    def test_the_title_names_the_meeting_and_its_body(self):
        first = self.entries()[0]
        self.assertEqual(
            "July 20, 2026 · City Council",
            first.find(f"{ATOM}title").text,
        )

    def test_newest_meeting_first(self):
        titles = [e.find(f"{ATOM}title").text for e in self.entries()]
        self.assertTrue(titles[0].startswith("July 20"))
        self.assertTrue(titles[-1].startswith("July 13"))

    def test_same_day_meetings_order_by_body(self):
        """Within a day the meetings run in body order, not as one entry."""
        titles = [e.find(f"{ATOM}title").text for e in self.entries()]
        self.assertTrue(titles[0].startswith("July 20, 2026 · City Council"))
        self.assertTrue(titles[1].startswith("July 20, 2026 · Planning & Dev"))

    def test_no_entry_carries_a_body_heading(self):
        """The body is in the title, so no entry repeats it as a heading.

        A heading that repeats it spends a line a preview cannot spare,
        and cuts off the item's gist beneath it; a meeting leads straight
        to its item.
        """
        for entry in self.entries():
            content = entry.find(f"{ATOM}content").text
            self.assertNotIn("<h3>", content)
            self.assertTrue(content.lstrip().startswith("<p><a"))

    def test_items_link_to_their_own_anchor(self):
        content = self.entries()[0].find(f"{ATOM}content").text
        self.assertIn(
            "https://yxeminutes.ca/meeting/council-1.html#item-11", content,
        )

    def test_each_meeting_points_at_its_own_page(self):
        links = [e.find(f"{ATOM}link").get("href") for e in self.entries()]
        self.assertEqual(
            ["https://yxeminutes.ca/meeting/council-1.html",
             "https://yxeminutes.ca/meeting/planning-1.html",
             "https://yxeminutes.ca/meeting/council-2.html"],
            links,
        )

    def test_every_meeting_entry_carries_the_ai_disclosure(self):
        for entry in self.entries():
            self.assertIn("AI-generated", entry.find(f"{ATOM}content").text)

    def test_timestamps_come_from_the_meeting_date_not_the_build(self):
        first = self.entries()[0]
        self.assertEqual(
            "2026-07-20T12:00:00-06:00", first.find(f"{ATOM}updated").text,
        )

    def test_an_entry_lands_on_its_own_day_in_saskatoon(self):
        """Midnight UTC is the evening before here, and read a day early."""
        stamp = datetime.fromisoformat(
            self.entries()[0].find(f"{ATOM}updated").text
        )
        local = stamp.astimezone(timezone(timedelta(hours=-6)))
        self.assertEqual(date(2026, 7, 20), local.date())

    def test_rebuilding_on_a_later_day_changes_nothing(self):
        """The deploy runs six times a day; subscribers must see one entry."""
        first = feeds.build_meeting_feed(self.meetings, _now("2026-07-27"))
        later = feeds.build_meeting_feed(self.meetings, _now("2026-08-03"))
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
        xml = feeds.build_item_feed(self.meetings, _now("2026-07-27"))
        return ET.fromstring(xml).findall(f"{ATOM}entry")[0]

    def test_one_entry_per_qualifying_item(self):
        xml = feeds.build_item_feed(self.meetings, _now("2026-07-27"))
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
    def test_meeting_feed_is_capped(self):
        meetings = [
            meeting(f"m{n}", day=f"2026-0{1 + n // 28}-{1 + n % 28:02d}",
                    items=[item(1, "A", description=["x"])])
            for n in range(50)
        ]
        xml = feeds.build_meeting_feed(meetings, _now("2026-12-01"))
        self.assertEqual(
            feeds.MAX_MEETING_ENTRIES,
            len(ET.fromstring(xml).findall(f"{ATOM}entry")),
        )

    def test_item_feed_is_capped(self):
        meetings = [
            meeting(f"m{n}", day=f"2026-0{1 + n // 28}-{1 + n % 28:02d}",
                    items=[item(i, f"Item {i}", description=["x"])
                           for i in range(1, 9)])
            for n in range(50)
        ]
        xml = feeds.build_item_feed(meetings, _now("2026-12-01"))
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
        for xml in feeds.build_feeds(self.meetings(), _now("2026-07-27")).values():
            ET.fromstring(xml)

    def test_the_ampersand_survives_the_round_trip(self):
        xml = feeds.build_item_feed(self.meetings(), _now("2026-07-27"))
        entry = ET.fromstring(xml).findall(f"{ATOM}entry")[0]
        self.assertIn("Parks & Rec", entry.find(f"{ATOM}title").text)

    def test_upstream_markup_does_not_become_markup_in_the_entry(self):
        xml = feeds.build_item_feed(self.meetings(), _now("2026-07-27"))
        entry = ET.fromstring(xml).findall(f"{ATOM}entry")[0]
        content = entry.find(f"{ATOM}content").text
        self.assertNotIn("<script>", content)
        self.assertIn("&lt;script&gt;", content)


class FeedDocument(unittest.TestCase):
    def test_both_feeds_declare_themselves(self):
        built = feeds.build_feeds([meeting(items=[item(1, "A", description=["x"])])],
                                  _now("2026-07-27"))
        self.assertEqual({"feed.xml", "feed-items.xml"}, set(built))
        for path, xml in built.items():
            root = ET.fromstring(xml)
            self_link = [l for l in root.findall(f"{ATOM}link")
                         if l.get("rel") == "self"]
            self.assertEqual(
                f"https://yxeminutes.ca/{path}", self_link[0].get("href"),
            )

    def test_an_empty_archive_still_produces_a_valid_feed(self):
        for xml in feeds.build_feeds([], _now("2026-07-27")).values():
            root = ET.fromstring(xml)
            self.assertEqual(0, len(root.findall(f"{ATOM}entry")))


def scheduled(meeting_id="s1", *, day="2026-08-19", deadline="2026-08-17",
              body="City Council", slug="council", has_agenda=True, items=None):
    """One Scheduled Meeting in the shape ``build_site`` hands the Future Feed."""
    return {
        "meeting_id": meeting_id,
        "body": body,
        "body_slug": slug,
        "date": day,
        "has_agenda": has_agenda,
        "request_to_speak_deadline": deadline,
        "agenda_items": items or [],
    }


TODAY = date(2026, 8, 14)  # the Friday before the Monday deadline


class FutureFeed(unittest.TestCase):
    def entries(self, xml):
        return ET.fromstring(xml).findall(f"{ATOM}entry")

    def test_a_meeting_with_an_agenda_publishes(self):
        xml = feeds.build_future_feed([scheduled()], TODAY)
        self.assertEqual(1, len(self.entries(xml)))

    def test_a_meeting_without_an_agenda_waits_until_the_deadline_nears(self):
        far = scheduled(has_agenda=False)
        self.assertEqual(
            0, len(self.entries(feeds.build_future_feed(
                [far], date(2026, 8, 10)))))
        near = feeds.build_future_feed([far], TODAY)  # 3 days out
        self.assertEqual(1, len(self.entries(near)))

    def test_the_bare_entry_says_the_agenda_is_not_posted(self):
        xml = feeds.build_future_feed([scheduled(has_agenda=False)], TODAY)
        content = self.entries(xml)[0].find(f"{ATOM}content").text
        self.assertIn("agenda has not been posted", content)

    def test_a_meeting_that_has_happened_is_dropped(self):
        xml = feeds.build_future_feed(
            [scheduled(day="2026-08-12", deadline="2026-08-10")], TODAY)
        self.assertEqual(0, len(self.entries(xml)))

    def test_entries_run_soonest_first(self):
        xml = feeds.build_future_feed([
            scheduled("later", day="2026-08-26", deadline="2026-08-24"),
            scheduled("sooner", day="2026-08-19", deadline="2026-08-17"),
        ], TODAY)
        ids = [e.find(f"{ATOM}id").text for e in self.entries(xml)]
        self.assertEqual(
            ["https://yxeminutes.ca/feed/future/sooner",
             "https://yxeminutes.ca/feed/future/later"], ids)

    def test_the_guid_is_stable_per_meeting(self):
        entry = self.entries(feeds.build_future_feed([scheduled("abc")], TODAY))[0]
        self.assertEqual("https://yxeminutes.ca/feed/future/abc",
                         entry.find(f"{ATOM}id").text)

    def test_the_title_names_the_body_and_the_date(self):
        entry = self.entries(feeds.build_future_feed([scheduled()], TODAY))[0]
        self.assertIn("City Council", entry.find(f"{ATOM}title").text)
        self.assertIn("August 19, 2026", entry.find(f"{ATOM}title").text)

    def test_the_deadline_leads_the_entry(self):
        xml = feeds.build_future_feed([scheduled()], TODAY)
        content = self.entries(xml)[0].find(f"{ATOM}content").text
        self.assertTrue(content.startswith(
            "<p><strong>Request to speak by August 17, 2026"))

    def test_the_agenda_runs_in_agenda_order_with_descriptions(self):
        xml = feeds.build_future_feed([scheduled(items=[
            item(2, "Second item", description=["Does the second thing."]),
            item(1, "Adoption of Agenda"),
            item(3, "First real item", description=["Does the first thing."]),
        ])], TODAY)
        content = self.entries(xml)[0].find(f"{ATOM}content").text
        self.assertNotIn("Adoption of Agenda", content)
        self.assertLess(content.index("Second item"),
                        content.index("First real item"))
        self.assertIn("Does the second thing.", content)

    def test_an_empty_future_still_produces_a_valid_feed(self):
        root = ET.fromstring(feeds.build_future_feed([], TODAY))
        self.assertEqual(0, len(root.findall(f"{ATOM}entry")))


if __name__ == "__main__":
    unittest.main()
