#!/usr/bin/env python3
"""
Build a static version of the YXEMinutes for GitHub Pages.

Fetches meeting data from eSCRIBE (via app.escribe.EscribeMeetingSource), generates topic
summaries (reusing app.summarizer), and produces a self-contained static
site in _site/.

Usage:
    python scripts/build_site.py
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import date, timedelta

# Ensure the project root is on the path so we can import app.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS
from app.models import (
    ScheduledMeeting,
    has_current_summaries,
    meeting_recording_state,
)
from app.agenda_items import (
    count_agenda_items,
    count_consent_items,
    count_discussed_items,
    is_procedural,
    is_section_header,
    mark_row_weights,
)
from app.agenda_text import plainify, readable_date, titleize
from app.feeds import build_feeds, build_future_feed
from app.speakers import (
    group_window,
    mark_jointly_heard,
    mark_heard,
    mark_questions,
    mark_timestamps,
    merge_remarks,
)
from app.summarizer import (
    extract_meeting_topics, extract_badges, speaker_roster,
)
from app.transcriber import adjust_timestamps_for_recesses, correct_timestamps
from app.transcript_cache import TranscriptCache
from app.attachment_gists_cache import AttachmentGistsCache
from app.item_summaries_cache import ItemSummariesCache

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "_site")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "app", "templates")
STATIC_DIR = os.path.join(PROJECT_ROOT, "app", "static")


# The stylesheet is linked with a content version (``style.css?v=<hash>``)
# so a deploy with new CSS is not read through a cached old copy — an
# unstyled span renders its tooltip as plain inline text, which is
# exactly the bug that made this necessary.
def _style_href() -> str:
    with open(os.path.join(STATIC_DIR, "style.css"), "rb") as f:
        version = hashlib.sha1(f.read()).hexdigest()[:8]
    return f"style.css?v={version}"

ESCRIBEMEETINGS_BASE = "https://pub-saskatoon.escribemeetings.com"

MEETINGS_PER_TAB = 20


def fetch_with_retry(func, *args, retries=3, delay=3, **kwargs):
    """Call *func* with retry logic and exponential backoff."""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"    Retry {attempt + 1}/{retries} after error: {exc}")
                time.sleep(wait)
            else:
                raise


def _extract_block(name, text):
    """Extract the content of a Jinja2 {% block name %}...{% endblock %} tag."""
    open_tag = f"{{% block {name} %}}"
    start = text.find(open_tag)
    if start == -1:
        return ""
    after_open = start + len(open_tag)
    end = text.find("{% endblock %}", after_open)
    return text[after_open:end].strip()


_START_TIME_RE = re.compile(r"@\s*(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]")


def _start_time_24h(formatted: str) -> str:
    """"Wednesday, 24 June 2026 @ 9:30 AM" -> "09:30"; "" when absent.

    A council agenda page's ``<time>`` element carries a date and no
    clock time, while a committee page carries both.  The meetings list
    has the time for all of them, so it fills the gap rather than
    leaving council meetings the only ones that cannot say when they sat.
    """
    match = _START_TIME_RE.search(formatted or "")
    if not match:
        return ""
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "p":
        hour += 12
    return f"{hour:02d}:{match.group(2)}"


def _fetch_topics_and_details(source: MeetingSource, meetings, transcript_cache, summaries_cache):
    """Fetch per-meeting topics and detail data for a list of Meeting objects."""
    topics_data: dict[str, list] = {}
    details_data: dict[str, dict] = {}
    for i, m in enumerate(meetings):
        mid = m.meeting_id
        print(f"  [{i + 1}/{len(meetings)}] Fetching topics for {mid[:8]}...")
        try:
            detail = fetch_with_retry(source.load_detail, mid)
            items = [item.to_dict() for item in detail.agenda_items]

            # Correct timestamps using cached transcript if available
            transcript = transcript_cache.load(mid)
            if transcript and transcript.segments:
                print(
                    f"    Applying transcript timestamps "
                    f"({len(transcript.segments)} segments)"
                )
                items = correct_timestamps(items, transcript.to_dict())
                items = adjust_timestamps_for_recesses(items, transcript.to_dict())

            # Attach badges to each item (same as /api/meeting/<id>)
            for item in items:
                item["badges"] = extract_badges(item)

            # Attach the ItemSummary (description + chips) if available
            item_summaries = summaries_cache.load(mid)
            if item_summaries:
                print(f"    Applying item summaries ({len(item_summaries)} items)")
                for item in items:
                    summary = item_summaries.get(str(item.get("item_id")))
                    if summary is None:
                        continue
                    item["summary"] = summary.to_dict()
                    # Legacy summaries predate the mandatory description, so
                    # the page marks them rather than passing them off as
                    # meeting the current bar.
                    item["summary"]["is_legacy"] = summary.is_legacy

            # The roster is rebuilt from the agenda every build; what each
            # speaker argued is cached with the summary.  Merged here so
            # both pages read one list. Then the transcript vouches for
            # registered speakers the chair introduced by name — a filing
            # alone never puts anyone in the digest.
            for item in items:
                item["speakers"] = merge_remarks(item)

            # Items taken as one discussion (6.3.2 with 7.1) would
            # otherwise repeat the same speaker, same stamp, on two
            # cards — one speech that answered both, reading as a leak.
            # Annotated before the transcript pass so speaker matching
            # scans the group's union window: the later item's bookmark
            # starts long after its first delegate spoke (G&P 2026-08-12
            # bookmarked 7.1 at 1:10:51; its first delegate spoke at
            # 42:40, inside 6.3.2's window).
            mark_jointly_heard(items)

            if transcript and transcript.segments:
                segments = transcript.to_dict()
                for item in items:
                    window = group_window(item, items)
                    mark_heard(item, segments, window=window)
                    # Where each speaker's name first sounds in the
                    # transcript is where their card links the video.
                    mark_timestamps(item, segments, window=window)
                    # And where the chair turns from speakers to the
                    # committee's questions is where the item's own
                    # play button earns its keep -- the one moment in
                    # the window no speaker card can point at.
                    mark_questions(item, segments, window=window)

            mark_row_weights(items)

            # Twelve, not the card's ten — see api_meeting_topics.
            topics = extract_meeting_topics(items, m.title, max_topics=12)
            roster = speaker_roster(items)
            topics_data[mid] = {
                "topics": topics,
                "total_items": count_agenda_items(items),
                # People, not filings: one delegate who spoke to two
                # items is one guest speaker, and counting rows said 11
                # where 10 came.
                "speaker_count": roster["speaker_count"],
                # Consent items roll up into one row on the card —
                # approved without debate, they earn no slot of their own.
                "consent_count": count_consent_items(items),
                # Every organization that spoke, so a packed meeting can
                # collapse speaker rows into an org digest without hiding
                # who had a voice.
                "roster": roster,
            }
            details_data[mid] = {
                "agenda_items": items,
                "video_url": detail.video_url,
                # Identity comes from the agenda page, but the meetings
                # list is the better source when it has one: it is where
                # the tab label and the card date already come from.
                # titleize the fallback too: the meetings list writes
                # names in full caps, and a page heading that shouts on
                # some meetings and not others looks broken.
                "title": detail.title or titleize(m.title),
                "date": detail.date or m.date,
                "start_time": detail.start_time or _start_time_24h(m.start_time),
                # Shared with the index card's "N other items" so the two
                # pages report the same meeting size.
                "item_count": count_agenda_items(items),
                # What the header says, and what the page draws. The
                # header used to report 43 above 73 rendered cards.
                "discussed_count": count_discussed_items(items),
                "consent_count": count_consent_items(items),
                # The feed's wait flag: whether a real, post-meeting
                # summarize run has reached this meeting.  Provisional
                # coverage does not count -- it is written before the
                # meeting from the agenda alone, and an entry settled on
                # it would say what the agenda promised, not what
                # happened (ADR ``0021``).  A no-video meeting so kept
                # waiting settles on the clock and publishes with the
                # not-recorded note.
                "has_summaries": has_current_summaries(item_summaries),
                # Where the video would be, on this page, says something
                # when there is no video: the recording is pending, or
                # the meeting was not recorded.
                "recording_state": meeting_recording_state(
                    m.has_video, m.is_cancelled, detail.date or m.date,
                    date.today(),
                ),
            }
        except Exception as exc:
            print(f"    WARNING: Failed to get topics: {exc}")
            topics_data[mid] = {"topics": [], "total_items": 0}
            # A meeting whose agenda could not be fetched still gets its
            # name and date from the list, so the page says which meeting
            # failed rather than failing anonymously.
            details_data[mid] = {
                "agenda_items": [],
                "video_url": None,
                "title": titleize(m.title),
                "date": m.date,
                "start_time": _start_time_24h(m.start_time),
                "item_count": 0,
                "discussed_count": 0,
                "consent_count": 0,
                # No agenda means nothing to publish, and the day it sits
                # on must not wait seven days for a meeting that will
                # never arrive -- it has no qualifying items either way.
                "has_summaries": True,
                "recording_state": meeting_recording_state(
                    m.has_video, m.is_cancelled, m.date, date.today(),
                ),
            }
    return topics_data, details_data


# The Future tab: Scheduled Meetings, always the left-most tab.
FUTURE_TAB = {"slug": "future", "label": "Future"}
SCHEDULED_DAYS_AHEAD = 60

# One static blurb for every Scheduled Meeting: the City runs a single
# form for written comments and requests to speak, and the deadline is
# always 5:00 p.m. on the Monday of the meeting week.
REGISTER_TO_SPEAK_URL = (
    "https://www.saskatoon.ca/submit-letterrequest-speak-council-and-committees"
)


def _attach_gists(items, gists):
    """Hang each attachment's 5-Ws gist on its dict, keyed by DocumentId."""
    if not gists:
        return
    for item in items:
        for att in (item.get("attachments") or []):
            m = re.search(r"DocumentId=(\d+)", att.get("url", ""))
            gist = gists.get(m.group(1)) if m else None
            if gist is not None:
                att["gist"] = gist.to_dict()


def _fetch_scheduled_details(source, scheduled, summaries_cache, gists_cache):
    """Detail pages and index topics for Scheduled Meetings.

    Unlike past meetings: no transcript correction, no badges, no speaker
    roster, no ranking — every agenda item in agenda order, each carrying
    its provisional Description when a summarize run has produced one.
    """
    topics_data: dict[str, dict] = {}
    details_data: dict[str, dict] = {}
    for i, s in enumerate(scheduled):
        mid = s.meeting_id
        print(f"  [{i + 1}/{len(scheduled)}] Fetching agenda for {mid[:8]}...")
        try:
            detail = fetch_with_retry(source.load_detail, mid)
            items = [item.to_dict() for item in detail.agenda_items]
            for item in items:
                item["scheduled"] = True

            _attach_gists(items, gists_cache.load(mid))

            item_summaries = summaries_cache.load(mid)
            if item_summaries:
                for item in items:
                    summary = item_summaries.get(str(item.get("item_id")))
                    if summary is None:
                        continue
                    item["summary"] = summary.to_dict()

            topics = []
            for item in items:
                if is_procedural(item.get("title", "")) \
                        or is_section_header(item) or item.get("is_recess"):
                    continue
                summary = item.get("summary") or {}
                description = summary.get("description")
                attachments = [
                    {"name": a.get("name"), "url": a.get("url"), "gist": a.get("gist")}
                    for a in (item.get("attachments") or [])
                ]
                topics.append({
                    "topic": titleize(plainify(item.get("title", ""))),
                    # No outcome exists yet; the row draws no badge.
                    "outcome": "",
                    "summary": description or [],
                    "summary_is_description": bool(description),
                    "badges": [],
                    "kind": "item",
                    "item_id": item.get("item_id"),
                    "time_start_ms": None,
                    "attachments": attachments,
                })

            topics_data[mid] = {
                "scheduled": True,
                "topics": topics,
                "total_items": count_agenda_items(items),
                "speaker_count": 0,
                "roster": None,
                "request_to_speak_deadline": s.request_to_speak_deadline,
            }
            details_data[mid] = {
                "scheduled": True,
                "agenda_items": items,
                "video_url": None,
                "title": detail.title or titleize(s.title),
                "date": detail.date or s.date,
                "start_time": detail.start_time or _start_time_24h(s.start_time),
                "item_count": count_agenda_items(items),
                "discussed_count": 0,
                "consent_count": 0,
                "has_summaries": True,  # irrelevant; never feeds the feeds
                "request_to_speak_deadline": s.request_to_speak_deadline,
                "register_to_speak_url": REGISTER_TO_SPEAK_URL,
            }
        except Exception as exc:
            print(f"    WARNING: Failed to get agenda: {exc}")
            topics_data[mid] = {
                "scheduled": True, "topics": [], "total_items": 0,
                "speaker_count": 0, "roster": None,
                "request_to_speak_deadline": s.request_to_speak_deadline,
            }
            details_data[mid] = {
                "scheduled": True,
                "agenda_items": [],
                "video_url": None,
                "title": titleize(s.title),
                "date": s.date,
                "start_time": _start_time_24h(s.start_time),
                "item_count": 0,
                "discussed_count": 0,
                "consent_count": 0,
                "has_summaries": True,
                "request_to_speak_deadline": s.request_to_speak_deadline,
                "register_to_speak_url": REGISTER_TO_SPEAK_URL,
            }
    return topics_data, details_data


def _merge_recorded(past, recorded):
    """Land a body's recorded-but-unpassed meetings on its past tab.

    A recording that is up but the upstream still marks not-passed has
    happened, so the meeting belongs on its body's past tab rather than
    the Future tab. ``list_past`` returns only passed meetings, so the
    calendar pass (``list_recorded``) supplies these. They are deduped
    against the passed set (in case one slips into both) and ordered
    most-recent first alongside the passed meetings.
    """
    seen = {m.meeting_id for m in past}
    merged = list(past)
    for m in recorded:
        if m.meeting_id in seen:
            continue
        merged.append(m)
        seen.add(m.meeting_id)
    merged.sort(key=lambda m: (m.date, m.start_time), reverse=True)
    return merged


def fetch_all_data():
    """Fetch meetings list and per-meeting topics from eSCRIBE for all tabs."""
    source: MeetingSource = EscribeMeetingSource(LiveEscribeTransport())
    # Per-tab meeting lists keyed by slug
    all_tabs_meetings: dict[str, dict] = {}
    # Shared across all tabs (meeting IDs are globally unique)
    all_topics: dict[str, list] = {}
    all_details: dict[str, dict] = {}
    # What the feeds read.  Assembled here because this is the only place
    # that knows which body a meeting belongs to -- a feed entry read in
    # a reader has no tab to say where it came from.
    feed_meetings: list[dict] = []
    future_feed_meetings: list[dict] = []

    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache, \
            AttachmentGistsCache.open() as gists_cache:
        # The Future tab first: Scheduled Meetings ride the same caches
        # but never the feeds.
        print("\nFetching scheduled meetings (future tab)...")
        try:
            today = date.today()
            scheduled = fetch_with_retry(
                source.list_scheduled,
                today.isoformat(),
                (today + timedelta(days=SCHEDULED_DAYS_AHEAD)).isoformat(),
            )
        except Exception as exc:
            print(f"  WARNING: scheduled meetings unavailable: {exc}")
            scheduled = []
        # A meeting with a recording has happened, so it is no longer
        # "future": the Future tab (and its feed) must not carry it.
        # Its body's past tab gets it below, via list_recorded.
        scheduled = [s for s in scheduled if not s.has_video]
        print(f"  Got {len(scheduled)} scheduled meetings")
        all_tabs_meetings[FUTURE_TAB["slug"]] = {
            "meetings": [s.to_dict() for s in scheduled],
            "total_count": len(scheduled),
        }
        sched_topics, sched_details = _fetch_scheduled_details(
            source, [s for s in scheduled if s.has_agenda], summaries_cache,
            gists_cache,
        )
        all_topics.update(sched_topics)
        all_details.update(sched_details)

        # The Future Feed (ADR 0024).  Scheduled Meetings ride the same
        # caches as past meetings above, so the agenda items are already
        # in hand; the feed stays pure and only reads dicts.
        slug_by_label = {t["label"]: t["slug"] for t in MEETING_TABS}
        for s in scheduled:
            d = s.to_dict()
            detail = sched_details.get(s.meeting_id) or {}
            d["body_slug"] = slug_by_label.get(s.body, "")
            d["agenda_items"] = detail.get("agenda_items") or []
            future_feed_meetings.append(d)

        # The recorded-but-unpassed gap for the past tabs: a meeting
        # with a recording has happened, so its date is at or before
        # today; the lookback covers the days eSCRIBE can sit on the
        # passed flag before it flips, and the forward slack matches the
        # transcribe job in case eSCRIBE lists a recording a day late.
        recorded_since = (today - timedelta(days=45)).isoformat()
        recorded_until = (today + timedelta(days=15)).isoformat()

        for tab in MEETING_TABS:
            slug = tab["slug"]
            meeting_type = tab["type"]
            print(f"\nFetching '{tab['label']}' meetings ({slug})...")
            meetings, total_count = fetch_with_retry(
                source.list_past, page=1, meeting_type=meeting_type,
            )
            # A meeting whose recording is up but the upstream still
            # marks not-passed has happened, so it belongs on its body's
            # past tab, not the Future tab. list_past returns only
            # passed meetings, so the calendar pass (list_recorded)
            # fills the gap; the past feeds pick it up from here, and
            # the future feed already dropped it above.
            try:
                recorded = fetch_with_retry(
                    source.list_recorded, recorded_since, recorded_until,
                    meeting_type=meeting_type,
                )
            except Exception as exc:
                print(f"  WARNING: recorded meetings unavailable: {exc}")
                recorded = []
            past_count = len(meetings)
            meetings = _merge_recorded(meetings, recorded)
            total_count += len(meetings) - past_count
            meetings = meetings[:MEETINGS_PER_TAB]
            meetings_data = [m.to_dict() for m in meetings]
            print(f"  Got {len(meetings_data)} meetings (total: {total_count})")

            all_tabs_meetings[slug] = {
                "meetings": meetings_data,
                "total_count": total_count,
            }

            topics, details = _fetch_topics_and_details(
                source, meetings, transcript_cache, summaries_cache,
            )
            all_topics.update(topics)
            all_details.update(details)

            for m in meetings:
                detail = details.get(m.meeting_id) or {}
                feed_meetings.append({
                    "meeting_id": m.meeting_id,
                    "title": detail.get("title") or titleize(m.title),
                    "body": tab["label"],
                    "body_slug": slug,
                    "date": detail.get("date") or m.date,
                    "has_summaries": detail.get("has_summaries", False),
                    # The feed's not-recorded note keys off this: an
                    # entry for a video-less meeting must say its
                    # descriptions come from the agenda.
                    "has_video": m.has_video,
                    "agenda_items": detail.get("agenda_items") or [],
                })

    return (all_tabs_meetings, all_topics, all_details, feed_meetings,
            future_feed_meetings)


def render_index_html(all_tabs_meetings, topics_data):
    """Read the Jinja2 templates and produce a flat static HTML file.

    Manually flattens base.html + index.html by replacing the Jinja2 block
    tags with concrete content.  This avoids needing a Flask app context.
    """
    with open(os.path.join(TEMPLATE_DIR, "base.html")) as f:
        base = f.read()
    with open(os.path.join(TEMPLATE_DIR, "index.html")) as f:
        index = f.read()

    # --- Extract blocks from index.html ---
    title_block = _extract_block("title", index) or "YXEMinutes"
    content_block = _extract_block("content", index)

    # Expand Jinja2 {% for tab in meeting_tabs %} loop into static HTML.
    # The Future tab is always left-most; JS keeps it pinned there.
    tab_buttons = [
        f'<button class="meeting-tab meeting-tab-active" data-slug="future">Future</button>'
    ]
    for tab in MEETING_TABS:
        tab_buttons.append(
            f'<button class="meeting-tab" '
            f'data-slug="{tab["slug"]}">{tab["label"]}</button>'
        )
    tab_html = "\n        ".join(tab_buttons)

    # Replace the Jinja for-loop block with rendered HTML
    import re as _re
    content_block = _re.sub(
        r'\{%\s*for tab in meeting_tabs\s*%\}.*?\{%\s*endfor\s*%\}',
        tab_html,
        content_block,
        flags=_re.DOTALL,
    )

    # Scripts block: need to be more careful since there may be multiple endblocks
    scripts_start = index.find("{% block scripts %}")
    scripts_end = index.rfind("{% endblock %}")
    scripts_block = index[scripts_start + len("{% block scripts %}"):scripts_end].strip() if scripts_start != -1 else ""

    # --- Build preloaded data script tags ---
    # Default tab is the Future tab when it has meetings, else council.
    future_data = all_tabs_meetings.get("future", {"meetings": [], "total_count": 0})
    if future_data["meetings"]:
        default_slug = "future"
        default_data = future_data
    else:
        default_slug = MEETING_TABS[0]["slug"]
        default_data = all_tabs_meetings.get(default_slug, {"meetings": [], "total_count": 0})

    preloaded_meetings = json.dumps({
        "meetings": default_data["meetings"],
        "total_count": default_data["total_count"],
        "active_type": default_slug,
        "page": 1,
    }, separators=(",", ":"))

    # Preload all tabs' meeting lists so tab switching works without API
    preloaded_all_tabs = json.dumps(all_tabs_meetings, separators=(",", ":"))

    preloaded_topics = json.dumps(topics_data, separators=(",", ":"))

    data_scripts = (
        f'<script type="application/json" id="preloaded-meetings">'
        f"{preloaded_meetings}</script>\n"
        f'<script type="application/json" id="preloaded-all-tabs">'
        f"{preloaded_all_tabs}</script>\n"
        f'<script type="application/json" id="preloaded-topics">'
        f"{preloaded_topics}</script>"
    )

    # --- Assemble final HTML from base.html ---
    output = base
    output = output.replace(
        "{% block title %}YXEMinutes{% endblock %}",
        title_block,
    )
    output = output.replace(
        "{{ url_for('static', filename='style.css') }}",
        _style_href(),
    )
    output = output.replace(
        "{% block content %}{% endblock %}",
        content_block,
    )
    output = output.replace(
        "{% block scripts %}{% endblock %}",
        data_scripts + "\n" + scripts_block,
    )

    return output


def render_meeting_html(meeting_id, detail_data):
    """Render a static meeting detail page with preloaded agenda data."""
    with open(os.path.join(TEMPLATE_DIR, "base.html")) as f:
        base = f.read()
    with open(os.path.join(TEMPLATE_DIR, "meeting.html")) as f:
        meeting = f.read()

    # A static page's <title> is what a bookmark, a browser tab and a
    # shared link show, and none of those have the card that was clicked.
    # It names the meeting when the meeting is known.
    name = (detail_data.get("title") or "").strip()
    date = readable_date(detail_data.get("date") or "")
    if name and date:
        title_block = f"{name}, {date} - YXEMinutes"
    elif name:
        title_block = f"{name} - YXEMinutes"
    else:
        title_block = (
            _extract_block("title", meeting)
            or "Meeting Details - YXEMinutes"
        )
    content_block = _extract_block("content", meeting)

    # Scripts block
    scripts_start = meeting.find("{% block scripts %}")
    scripts_end = meeting.rfind("{% endblock %}")
    scripts_block = (
        meeting[scripts_start + len("{% block scripts %}") : scripts_end].strip()
        if scripts_start != -1
        else ""
    )

    # Replace Jinja variable with actual meeting ID
    scripts_block = scripts_block.replace("{{ meeting_id }}", meeting_id)

    # Build preloaded data script tag (escape </ to prevent script breakage)
    preloaded = json.dumps(detail_data, separators=(",", ":"))
    preloaded = preloaded.replace("</", "<\\/")
    data_script = (
        f'<script type="application/json" id="preloaded-meeting">'
        f"{preloaded}</script>"
    )

    # Fix relative paths for meeting sub-pages (one directory deep)
    content_block = content_block.replace('href="/"', 'href="../"')

    # Assemble from base.html
    output = base
    output = output.replace(
        "{% block title %}YXEMinutes{% endblock %}",
        title_block,
    )
    output = output.replace(
        "{{ url_for('static', filename='style.css') }}",
        f"../{_style_href()}",
    )
    # Fix logo link for sub-page
    output = output.replace('href="/" class="logo"', 'href="../" class="logo"')
    output = output.replace(
        "{% block content %}{% endblock %}",
        content_block,
    )
    output = output.replace(
        "{% block scripts %}{% endblock %}",
        data_script + "\n" + scripts_block,
    )

    return output


def render_about_html():
    """Render the static about/disclaimer page."""
    with open(os.path.join(TEMPLATE_DIR, "base.html")) as f:
        base = f.read()
    with open(os.path.join(TEMPLATE_DIR, "about.html")) as f:
        about = f.read()

    title_block = _extract_block("title", about) or "About - YXEMinutes"
    content_block = _extract_block("content", about)

    output = base
    output = output.replace(
        "{% block title %}YXEMinutes{% endblock %}",
        title_block,
    )
    output = output.replace(
        "{{ url_for('static', filename='style.css') }}",
        _style_href(),
    )
    output = output.replace(
        "{% block content %}{% endblock %}",
        content_block,
    )
    output = output.replace("{% block scripts %}{% endblock %}", "")
    return output


def main():
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)

    (all_tabs_meetings, topics_data, details_data, feed_meetings,
     future_feed_meetings) = fetch_all_data()

    print("Rendering static index.html...")
    html = render_index_html(all_tabs_meetings, topics_data)

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(html)

    # Render per-meeting detail pages
    meeting_dir = os.path.join(OUTPUT_DIR, "meeting")
    os.makedirs(meeting_dir, exist_ok=True)

    for mid, detail in details_data.items():
        meeting_html = render_meeting_html(mid, detail)
        meeting_path = os.path.join(meeting_dir, f"{mid}.html")
        with open(meeting_path, "w") as f:
            f.write(meeting_html)

    # /about/ is a directory so the deployed URL matches the Flask route
    about_dir = os.path.join(OUTPUT_DIR, "about")
    os.makedirs(about_dir, exist_ok=True)
    with open(os.path.join(about_dir, "index.html"), "w") as f:
        f.write(render_about_html())

    shutil.copy2(
        os.path.join(STATIC_DIR, "style.css"),
        os.path.join(OUTPUT_DIR, "style.css"),
    )

    # Copy CNAME for custom domain (GitHub Pages requires it in the deploy root)
    cname_path = os.path.join(PROJECT_ROOT, "CNAME")
    if os.path.exists(cname_path):
        shutil.copy2(cname_path, os.path.join(OUTPUT_DIR, "CNAME"))

    # The feeds are regenerated from scratch on every build and keep no
    # record of what they published.  That is safe because entry ids come
    # from the meeting id and timestamps from the meeting date, neither
    # from build time, so a rebuild whose data has not moved is
    # byte-identical and no subscriber sees anything (ADR 0019).
    feeds = build_feeds(feed_meetings, date.today())
    # The Future Feed is built separately: its input is Scheduled
    # Meetings, not the settled-meeting dicts the other two read
    # (ADR 0024).
    feeds["feed-future.xml"] = build_future_feed(future_feed_meetings,
                                                 date.today())
    for filename, xml in feeds.items():
        with open(os.path.join(OUTPUT_DIR, filename), "w") as f:
            f.write(xml)

    print(f"Static site built in {OUTPUT_DIR}/")
    print(f"  index.html ({len(html):,} bytes)")
    print(f"  {len(details_data)} meeting detail pages")
    print(f"  style.css")
    for filename, xml in feeds.items():
        entries = xml.count("<entry>")
        print(f"  {filename} ({entries} entries)")


if __name__ == "__main__":
    main()
