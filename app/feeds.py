"""Atom feeds for the static site.

Two settled feeds, differing only in granularity and never in what
qualifies (ADR ``0019``):

- ``/feed.xml`` — one entry per meeting the city sat, that meeting's
  qualifying items as its body.  The default.
- ``/feed-items.xml`` — one entry per qualifying agenda item.

An item qualifies by having something to say — a Description or an
interpretive chip — and qualifying items rank by discussion time, capped
per meeting.  Deliberately not the index card's ranking; see ADR
``0018`` for the measurements.

Pure: everything here takes dicts and returns strings.  ``build_site``
holds the data already, so the feeds cost no extra fetch, and the tests
can build a real feed from fixtures with no network and no site build.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from app.agenda_items import format_outcome, is_procedural
from app.agenda_text import plainify, readable_date, titleize
from app.models import RECORDING_GRACE_DAYS, normalize_description
from app.speakers import merge_remarks, organization_label
from app.summarizer import (
    CARD_CHIP_CATEGORIES,
    TAKEAWAY_ORDER,
    discussion_minutes,
    item_summary,
)
from app.models import normalize_description

SITE_URL = "https://yxeminutes.ca"
FEED_TITLE = "YXEMinutes"
FEED_SUBTITLE = "What Saskatoon city council actually decided"

MEETING_FEED_PATH = "feed.xml"
ITEM_FEED_PATH = "feed-items.xml"
FUTURE_FEED_PATH = "feed-future.xml"

# How close to the Request-to-Speak Deadline an agenda-less Scheduled
# Meeting gets before it publishes a bare entry anyway (ADR ``0024``).
# The deadline is the user's decision point; an entry that waits for an
# agenda that never posts must still arrive before it.
NO_AGENDA_FALLBACK_DAYS = 3

# Retention.  The build holds far more than this -- 20 meetings across
# each of 16 tabs -- but most readers import every entry in the file when
# someone subscribes, and a new subscriber's first experience should not
# be three hundred unread items dating back a year.  The archive is on
# the site; the feed is a notification channel, not a mirror.
MAX_MEETING_ENTRIES = 30
MAX_ITEM_ENTRIES = 100

# Per meeting, not per day: a day on which four committees sat has more
# genuinely happening in it than a day on which one did.
MAX_ITEMS_PER_MEETING = 8

# How long a meeting waits for the pipeline before publishing anyway.  A
# meeting whose video never arrives must not wait forever, and a meeting
# missing from the feed is a worse error than one that later improves.
# The same count the site's recording state uses: a no-video meeting
# stops waiting for the feed in the week the site calls it not recorded.
SETTLE_DAYS = RECORDING_GRACE_DAYS

ATOM_NS = "http://www.w3.org/2005/Atom"

# Saskatchewan keeps central standard time all year and never moves for
# daylight saving, so one fixed offset is the whole rule -- no zone
# database, and no date on which the answer changes.
SASKATOON_TZ = timezone(timedelta(hours=-6))

AI_DISCLOSURE = (
    "Summaries are AI-generated from the meeting transcript and may "
    "contain errors."
)

# A feed entry arrives with no header, no footer and no second page, so
# the disclosure that the site keeps in its footer has to travel in the
# entry.  Same premise as ADR 0017.
_SOURCE_NOTE = "Source: City of Saskatoon eSCRIBE."


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def interpretive_chips(item: dict) -> list[dict]:
    """The item's chips the model only fills when it found something to say.

    Outcome and Vote Breakdown are excluded upstream in
    ``CARD_CHIP_CATEGORIES``: they are extracted deterministically and
    say what happened, not why it was worth reading.
    """
    chips = item_summary(item).get("chips") or []
    return [
        chip for chip in chips
        if isinstance(chip, dict)
        and chip.get("category") in CARD_CHIP_CATEGORIES
        and (chip.get("text") or "").strip()
    ]


def item_description(item: dict) -> list[str]:
    """The item's Description as bullets, or ``[]``.

    Only the written Description counts.  The card falls back to clipped
    agenda prose under an "older summary" note, which is fine on a page
    that says so and is not something to mail to a subscriber.
    """
    return normalize_description(item_summary(item).get("description")) or []


def qualifies(item: dict) -> bool:
    """Whether the item has earned an entry.

    Substance, not size: an item council spent forty minutes on with
    nothing written about it publishes "council spent forty minutes on
    this" and no account of what came of it.
    """
    return bool(item_description(item)) or bool(interpretive_chips(item))


def qualifying_items(agenda_items: list[dict]) -> list[dict]:
    """The items from one meeting that earn entries, best first.

    Substance gates and discussion time ranks -- not the card's score,
    which saturates at twenty minutes and so cannot tell a 155-minute
    budget debate from a 21-minute one (ADR ``0018``).  Ties keep agenda
    order, so a meeting with no timings at all still publishes the items
    in the order they were reached.
    """
    gated = [item for item in agenda_items or [] if qualifies(item)]
    ranked = sorted(
        enumerate(gated),
        key=lambda pair: (-discussion_minutes(pair[1]), pair[0]),
    )
    return [item for _, item in ranked[:MAX_ITEMS_PER_MEETING]]


def item_topics(item: dict) -> list[str]:
    """The item's interpretive chip categories, deduped, as feed tags.

    A reader that filters by category turns these into topic
    subscriptions -- every item carrying a ``Cost & Funding`` chip is an
    item about money.  Keyword subscribers need nothing: readers match
    keywords against title and content, which the Description already
    fills.
    """
    topics = []
    for chip in interpretive_chips(item):
        category = chip["category"]
        if category not in topics:
            topics.append(category)
    return topics


def takeaway(item: dict) -> dict | None:
    """The one chip worth a line, or ``None``.

    Same order as the card, from the same list, so the feed and the card
    never lead with different things about the same item.
    """
    chips = interpretive_chips(item)
    by_category = {}
    for chip in chips:
        by_category.setdefault(chip["category"], chip)
    for category in TAKEAWAY_ORDER:
        if category in by_category:
            return by_category[category]
    return chips[0] if chips else None


def is_settled(meeting: dict, today: date) -> bool:
    """Whether one meeting is done being summarized.

    Publishing on first qualification would send a thin entry that fills
    in later, and a reader who saw the thin one is never shown the full
    one -- most readers render a changed entry once and never resurface
    it.  So a meeting waits until it has summaries, or until
    ``SETTLE_DAYS`` have passed and it is published as it stands.  A
    meeting settles on its own and does not wait on a sibling that sat
    the same day.

    "Has summaries" means real, post-meeting summaries: a provisional
    cache written before the meeting is not a summary of what happened,
    and an entry settled on it would lead with the agenda (ADR ``0021``).
    Such a meeting keeps waiting, and when the week runs out it
    publishes with the note in ``not_recorded_note``.
    """
    if meeting.get("has_summaries"):
        return True
    day = _parse_date(meeting.get("date"))
    if day is None:
        return False
    return (today - day) >= timedelta(days=SETTLE_DAYS)


def _parse_date(iso: str | None) -> date | None:
    try:
        return datetime.strptime(iso or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def meetings_to_publish(meetings: list[dict], today: date) -> list[dict]:
    """Settled meetings that earn entries, newest first.

    Each meeting settles on its own -- it publishes once it has
    summaries or ``SETTLE_DAYS`` have passed, without waiting on a
    sibling that sat the same day. Both settled feeds build from this
    same list, so they publish the same meetings at the same time and
    differ only in granularity (ADR ``0019``): the default one entry
    per meeting, the item feed one entry per qualifying item.
    """
    rows = [
        meeting
        for meeting in meetings
        if (meeting.get("date") or "").strip()
        and _parse_date(meeting.get("date")) is not None
        and qualifying_items(meeting.get("agenda_items") or [])
        and is_settled(meeting, today)
    ]
    rows.sort(key=lambda m: (m.get("body") or ""))
    rows.sort(key=lambda m: (m.get("date") or ""), reverse=True)
    return rows


# --------------------------------------------------------------------------
# Entry content
# --------------------------------------------------------------------------

def item_title(item: dict) -> str:
    """The item's title as the card writes it.

    Some agenda titles arrive in full caps.  A feed whose entries shout
    on some days and not others reads as two feeds.
    """
    return titleize(plainify(item.get("title") or "")) or "Agenda item"


def item_url(meeting_id: str, item: dict) -> str:
    """The item's permalink, which is also its feed id.

    An anchor rather than ``?t=<ms>``: a Consent Item has no timestamp of
    its own, and those are the items most likely to be missed
    (``TODO.md`` item 7).  Synthetic Recess rows all share ``item_id``
    ``-1``, so they can never be addressed -- they never qualify either.
    """
    return f"{SITE_URL}/meeting/{meeting_id}.html#item-{item.get('item_id')}"


def speaker_line(item: dict) -> str:
    """Who came to speak to this item, on one line.

    Names and organizations only.  What each of them argued is three or
    four more lines and it is on the detail page, which is the same call
    the card makes.
    """
    named = []
    seen = set()
    for speaker in merge_remarks(item):
        name = (speaker.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        org = organization_label(speaker.get("organization") or "")
        named.append(f"{name} ({org})" if org else name)
    return "Spoke: " + ", ".join(named) if named else ""


def item_outcome(item: dict) -> str:
    """What kind of action was taken, in the site's outcome vocabulary.

    "Discussed" is what ``format_outcome`` returns when there was no vote
    and no recommendation -- it reports nothing, so the entry says
    nothing rather than leading with a non-answer.
    """
    outcome = format_outcome(
        item.get("vote_result") or "", item.get("recommendation") or "",
    )
    return "" if outcome == "Discussed" else outcome


def _context_line(meeting: dict, item: dict) -> str:
    """``Approved (6-5) · City Council · December 17, 2025``.

    The outcome leads because a committee's "Recommended to Council"
    reported as "Approved" tells a resident the opposite of what
    happened, and an entry read in a reader has no tab to say which body
    it came from.
    """
    parts = [
        item_outcome(item),
        (meeting.get("body") or meeting.get("title") or "").strip(),
        readable_date(meeting.get("date") or ""),
    ]
    return " · ".join(part for part in parts if part)


def item_content_html(meeting: dict, item: dict, *, include_context: bool) -> str:
    """One item rendered as the HTML that goes inside an entry.

    Deliberately plain: a feed reader applies its own stylesheet and
    strips most of what it is given, so anything cleverer than headings,
    lists and links is thrown away or, worse, kept and made ugly.
    """
    parts = []
    if include_context:
        context = _context_line(meeting, item)
        if context:
            parts.append(f"<p><strong>{_esc(context)}</strong></p>")
    bullets = item_description(item)
    if bullets:
        lines = "".join(f"<li>{_esc(b)}</li>" for b in bullets)
        parts.append(f"<ul>{lines}</ul>")
    hook = takeaway(item)
    if hook:
        parts.append(
            f"<p><em>{_esc(hook['category'])}:</em> {_esc(hook['text'])}</p>"
        )
    spoke = speaker_line(item)
    if spoke:
        parts.append(f"<p>{_esc(spoke)}</p>")
    return "".join(parts)


def _esc(text: str) -> str:
    """Escape for embedding in the HTML that goes inside ``<content>``.

    ``ElementTree`` escapes the ``<content>`` element's own text, so this
    is escaping the *inner* HTML before that happens -- otherwise a
    ``<li>`` would be escaped along with the title inside it.
    """
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def not_recorded_note(meeting: dict, *, per_item: bool = False) -> str:
    """A plain note for a published meeting that has no recording.

    Only reachable for a meeting that settled on the clock rather than
    on summaries: real summaries need a transcript, and a transcript
    needs a video.  An entry settled without one carries provisional,
    agenda-derived descriptions, and the note says so instead of letting
    the entry read as an account of the discussion.
    """
    if meeting.get("has_video"):
        return ""
    if per_item:
        text = (
            "This meeting was not recorded, so this description was "
            "written from the published agenda rather than from the discussion."
        )
    else:
        text = (
            "This meeting was not recorded, so the item descriptions "
            "were written from the published agenda rather than from the "
            "discussion."
        )
    return f"<p>{_esc(text)}</p>"


def _meeting_content_html(meeting: dict, items: list[dict]) -> str:
    """A meeting's qualifying items, each a linked line plus its body.

    No body heading: the body is already in the entry title, so a heading
    that repeats it spends a line a preview cannot spare -- and a reader
    that shows only the top line or two never reaches the item beneath it.
    """
    parts = []
    note = not_recorded_note(meeting)
    if note:
        parts.append(note)
    for item in items:
        url = item_url(meeting["meeting_id"], item)
        outcome = item_outcome(item)
        line = f'<p><a href="{_esc(url)}">{_esc(item_title(item))}</a>'
        if outcome:
            line += f" — {_esc(outcome)}"
        parts.append(line + "</p>")
        parts.append(item_content_html(meeting, item, include_context=False))
    parts.append(f"<p><small>{_esc(AI_DISCLOSURE)} {_esc(_SOURCE_NOTE)}</small></p>")
    return "".join(parts)


def _meeting_title(meeting: dict) -> str:
    """``December 17, 2025 · Planning & Development``.

    The date leads and the body follows; a meeting with no body falls
    back to the date alone.
    """
    body = (meeting.get("body") or "").strip()
    day = (meeting.get("date") or "").strip()
    return f"{readable_date(day)} · {body}" if body else readable_date(day)


def _meeting_link(meeting: dict) -> str:
    """Where a meeting entry points: that meeting's own detail page."""
    return f"{SITE_URL}/meeting/{meeting['meeting_id']}.html"


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------

def _timestamp(day: str) -> str:
    """A day as an Atom timestamp, at midday in Saskatoon.

    From the meeting date and never from build time: the deploy runs six
    times a day, and an ``<updated>`` that moved with it would republish
    every entry in the file to every subscriber six times a day.

    Midday and not midnight because a reader shows the entry in the
    reader's own timezone.  Midnight UTC on the day council sat is the
    evening *before* in Saskatoon, so every entry read a day early and a
    Tuesday meeting looked like a Monday one.  Noon local has half a day
    of slack on either side, which is enough for every timezone a reader
    of a Saskatoon council feed plausibly sits in.
    """
    parsed = _parse_date(day)
    if parsed is None:
        return datetime(1970, 1, 1, 12, tzinfo=SASKATOON_TZ).isoformat()
    return datetime(
        parsed.year, parsed.month, parsed.day, 12, tzinfo=SASKATOON_TZ
    ).isoformat()


def _feed_root(title: str, self_path: str, updated: str,
               subtitle: str = FEED_SUBTITLE) -> ET.Element:
    feed = ET.Element("feed", {"xmlns": ATOM_NS})
    ET.SubElement(feed, "title").text = title
    ET.SubElement(feed, "subtitle").text = subtitle
    ET.SubElement(feed, "id").text = f"{SITE_URL}/{self_path}"
    ET.SubElement(feed, "updated").text = updated
    ET.SubElement(feed, "link", {"rel": "alternate", "type": "text/html",
                                 "href": f"{SITE_URL}/"})
    ET.SubElement(feed, "link", {"rel": "self", "type": "application/atom+xml",
                                 "href": f"{SITE_URL}/{self_path}"})
    author = ET.SubElement(feed, "author")
    ET.SubElement(author, "name").text = FEED_TITLE
    ET.SubElement(author, "uri").text = SITE_URL
    ET.SubElement(feed, "rights").text = AI_DISCLOSURE
    return feed


def _add_entry(feed: ET.Element, *, entry_id: str, title: str, link: str,
               updated: str, content: str, categories: list[str]) -> None:
    entry = ET.SubElement(feed, "entry")
    ET.SubElement(entry, "title").text = title
    ET.SubElement(entry, "id").text = entry_id
    ET.SubElement(entry, "updated").text = updated
    ET.SubElement(entry, "published").text = updated
    ET.SubElement(entry, "link", {"rel": "alternate", "type": "text/html",
                                  "href": link})
    for term in categories:
        if term:
            ET.SubElement(entry, "category", {"term": term})
    node = ET.SubElement(entry, "content", {"type": "html"})
    node.text = content


def _serialize(feed: ET.Element) -> str:
    ET.indent(feed, space="  ")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        + ET.tostring(feed, encoding="unicode")
        + "\n"
    )


def _meeting_categories(meeting: dict, items: list[dict]) -> list[str]:
    """The meeting's body plus its items' topic tags, a reader's filters.

    A category subscriber turns these into topic subscriptions -- every
    item carrying a ``Cost & Funding`` chip is an item about money.
    """
    return [meeting.get("body_slug") or ""] + sorted({
        topic
        for item in items
        for topic in item_topics(item)
    })


def build_meeting_feed(meetings: list[dict], today: date) -> str:
    """The default feed: one entry per meeting the city sat.

    A meeting is the unit, not a day: committees stack, and a Tuesday on
    which Planning and Transportation both sat is two entries rather
    than one, each naming the body that met (ADR ``0019``). Each
    meeting publishes as soon as it is settled, without waiting on a
    sibling that sat the same day.
    """
    rows = meetings_to_publish(meetings, today)[:MAX_MEETING_ENTRIES]
    updated = _timestamp(rows[0].get("date")) if rows else _timestamp("")
    feed = _feed_root(FEED_TITLE, MEETING_FEED_PATH, updated)
    for meeting in rows:
        items = qualifying_items(meeting.get("agenda_items") or [])
        _add_entry(
            feed,
            entry_id=f"{SITE_URL}/feed/meeting/{meeting['meeting_id']}",
            title=_meeting_title(meeting),
            link=_meeting_link(meeting),
            updated=_timestamp(meeting.get("date")),
            content=_meeting_content_html(meeting, items),
            categories=_meeting_categories(meeting, items),
        )
    return _serialize(feed)


def build_item_feed(meetings: list[dict], today: date) -> str:
    """The second feed: one entry per qualifying item.

    Same gate, same ranking, same cap as the meeting feed -- the two
    differ only in granularity.  Two feeds with two thresholds would
    make one a subset of the other, so anyone subscribed to both would
    see the important items twice.
    """
    rows = meetings_to_publish(meetings, today)
    entries: list[tuple[dict, dict]] = []
    for meeting in rows:
        for item in qualifying_items(meeting.get("agenda_items") or []):
            entries.append((meeting, item))
    entries = entries[:MAX_ITEM_ENTRIES]
    updated = _timestamp(entries[0][0].get("date")) if entries else _timestamp("")
    feed = _feed_root(f"{FEED_TITLE} — every item", ITEM_FEED_PATH, updated)
    for meeting, item in entries:
        url = item_url(meeting["meeting_id"], item)
        content = item_content_html(meeting, item, include_context=True)
        content += not_recorded_note(meeting, per_item=True)
        content += (
            f"<p><small>{_esc(AI_DISCLOSURE)} "
            f'<a href="{_esc(url)}">Read the original agenda item</a>.</small></p>'
        )
        _add_entry(
            feed,
            entry_id=url,
            title=item_title(item),
            link=url,
            updated=_timestamp(meeting.get("date")),
            content=content,
            categories=[meeting.get("body_slug") or ""] + item_topics(item),
        )
    return _serialize(feed)


# --------------------------------------------------------------------------
# The Future Feed (ADR 0024): Scheduled Meetings, dropped when they happen
# --------------------------------------------------------------------------

def future_meeting_publishable(meeting: dict, today: date) -> bool:
    """Whether a Scheduled Meeting earns an entry yet.

    Normally when the agenda first posts -- an entry before that cannot
    answer \"should I sign up?\".  The fallback: if the deadline is
    ``NO_AGENDA_FALLBACK_DAYS`` away (or already past) and there is
    still no agenda, publish a bare entry so the subscriber hears about
    the meeting before its deadline rather than after.
    """
    day = _parse_date(meeting.get("date"))
    if day is None or day < today:
        return False
    if meeting.get("has_agenda"):
        return True
    deadline = _parse_date(meeting.get("request_to_speak_deadline"))
    if deadline is None:
        return False
    return (deadline - today).days <= NO_AGENDA_FALLBACK_DAYS


def _future_agenda_items(meeting: dict) -> list[dict]:
    """The meeting's agenda in agenda order, minus the scaffolding.

    Procedural rows and recesses are how an agenda is laid out, not
    things a resident can speak to.  Section headers are *not* filtered:
    ``is_section_header`` reads timestamps a Scheduled Meeting's items
    never have, so it would call every future item a header.
    """
    return [
        item for item in (meeting.get("agenda_items") or [])
        if not item.get("is_recess")
        and not is_procedural(item.get("title") or "")
    ]


def _future_content_html(meeting: dict) -> str:
    """Deadline up top, then the full agenda with provisional Descriptions.

    The deadline leads because it is the reason the feed exists: the
    subscriber's question is \"do I need to register by Monday?\", not
    \"what is on the agenda?\".
    """
    parts = []
    deadline = _parse_date(meeting.get("request_to_speak_deadline"))
    if deadline is not None:
        parts.append(
            "<p><strong>Request to speak by "
            f"{_esc(readable_date(deadline.isoformat()))}, 5:00 p.m.</strong></p>"
        )
    items = _future_agenda_items(meeting)
    if not items:
        parts.append("<p>The agenda has not been posted yet.</p>")
    for item in items:
        parts.append(f"<p>{_esc(item_title(item))}</p>")
        bullets = item_description(item)
        if bullets:
            lines = "".join(f"<li>{_esc(b)}</li>" for b in bullets)
            parts.append(f"<ul>{lines}</ul>")
    parts.append(
        f"<p><small>{_esc(AI_DISCLOSURE)} {_esc(_SOURCE_NOTE)}</small></p>"
    )
    return "".join(parts)


def build_future_feed(meetings: list[dict], today: date) -> str:
    """The third feed: one entry per Scheduled Meeting, soonest first.

    *meetings* is one dict per Scheduled Meeting: ``meeting_id``,
    ``body``, ``body_slug``, ``date``, ``has_agenda``,
    ``request_to_speak_deadline`` and ``agenda_items``.  Entries are
    dropped, not annotated, once the meeting happens -- the meeting
    flips into the settled feeds on its own, and readers keep what they
    already fetched (ADR ``0024``).
    """
    publishable = [
        m for m in meetings
        if future_meeting_publishable(m, today)
    ]
    publishable.sort(key=lambda m: (m.get("date") or "", m.get("body") or ""))
    updated = _timestamp(publishable[0].get("date")) if publishable else _timestamp("")
    feed = _feed_root(
        f"{FEED_TITLE} — Upcoming Meetings", FUTURE_FEED_PATH, updated,
        subtitle="What Saskatoon city council is about to take up",
    )
    for meeting in publishable:
        day = meeting.get("date") or ""
        body = (meeting.get("body") or meeting.get("title") or "").strip()
        title = f"{body} · {readable_date(day)}" if body else readable_date(day)
        _add_entry(
            feed,
            entry_id=f"{SITE_URL}/feed/future/{meeting['meeting_id']}",
            title=title,
            link=f"{SITE_URL}/meeting/{meeting['meeting_id']}.html",
            updated=_timestamp(day),
            content=_future_content_html(meeting),
            categories=[meeting.get("body_slug") or ""],
        )
    return _serialize(feed)


def build_feeds(meetings: list[dict], today: date) -> dict[str, str]:
    """Both feeds, keyed by the filename they are written to.

    *meetings* is one dict per meeting: ``meeting_id``, ``title``,
    ``body``, ``body_slug``, ``date``, ``has_summaries``,
    ``has_video`` and ``agenda_items``.  ``build_site`` has all of it in
    hand by the time it renders, so the feeds cost no second fetch.
    """
    return {
        MEETING_FEED_PATH: build_meeting_feed(meetings, today),
        ITEM_FEED_PATH: build_item_feed(meetings, today),
    }
