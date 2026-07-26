#!/usr/bin/env python3
"""
Build a static version of the YXEMinutes for GitHub Pages.

Fetches meeting data from eSCRIBE (via app.escribe.EscribeMeetingSource), generates topic
summaries (reusing app.summarizer), and produces a self-contained static
site in _site/.

Usage:
    python scripts/build_site.py
"""

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

# Ensure the project root is on the path so we can import app.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS
from app.agenda_items import count_agenda_items
from app.agenda_text import titleize
from app.summarizer import extract_meeting_topics, extract_badges
from app.transcriber import correct_timestamps
from app.transcript_cache import TranscriptCache
from app.item_summaries_cache import ItemSummariesCache

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "_site")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "app", "templates")
STATIC_DIR = os.path.join(PROJECT_ROOT, "app", "static")

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

            topics = extract_meeting_topics(items, m.title, max_topics=8)
            topics_data[mid] = {
                "topics": topics,
                "total_items": count_agenda_items(items),
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
            }
    return topics_data, details_data


def fetch_all_data():
    """Fetch meetings list and per-meeting topics from eSCRIBE for all tabs."""
    source: MeetingSource = EscribeMeetingSource(LiveEscribeTransport())
    # Per-tab meeting lists keyed by slug
    all_tabs_meetings: dict[str, dict] = {}
    # Shared across all tabs (meeting IDs are globally unique)
    all_topics: dict[str, list] = {}
    all_details: dict[str, dict] = {}

    with TranscriptCache.open() as transcript_cache, \
            ItemSummariesCache.open() as summaries_cache:
        for tab in MEETING_TABS:
            slug = tab["slug"]
            meeting_type = tab["type"]
            print(f"\nFetching '{tab['label']}' meetings ({slug})...")
            meetings, total_count = fetch_with_retry(
                source.list_past, page=1, meeting_type=meeting_type,
            )
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

    return all_tabs_meetings, all_topics, all_details


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

    # Expand Jinja2 {% for tab in meeting_tabs %} loop into static HTML
    tab_buttons = []
    for i, tab in enumerate(MEETING_TABS):
        active = " meeting-tab-active" if i == 0 else ""
        tab_buttons.append(
            f'<button class="meeting-tab{active}" '
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
    # Default tab is the first one ("council")
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
        "style.css",
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


def _readable_date(iso: str) -> str:
    """"2025-06-17" -> "June 17, 2025"; anything unparseable comes back as-is."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        return iso or ""


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
    date = _readable_date(detail_data.get("date") or "")
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
        "../style.css",
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


def main():
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)

    all_tabs_meetings, topics_data, details_data = fetch_all_data()

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

    shutil.copy2(
        os.path.join(STATIC_DIR, "style.css"),
        os.path.join(OUTPUT_DIR, "style.css"),
    )

    # Copy CNAME for custom domain (GitHub Pages requires it in the deploy root)
    cname_path = os.path.join(PROJECT_ROOT, "CNAME")
    if os.path.exists(cname_path):
        shutil.copy2(cname_path, os.path.join(OUTPUT_DIR, "CNAME"))

    print(f"Static site built in {OUTPUT_DIR}/")
    print(f"  index.html ({len(html):,} bytes)")
    print(f"  {len(details_data)} meeting detail pages")
    print(f"  style.css")


if __name__ == "__main__":
    main()
