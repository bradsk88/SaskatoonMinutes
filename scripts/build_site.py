#!/usr/bin/env python3
"""
Build a static version of the YXEMinutes for GitHub Pages.

Fetches meeting data from eSCRIBE (reusing app.scraper), generates topic
summaries (reusing app.summarizer), and produces a self-contained static
site in _site/.

Usage:
    python scripts/build_site.py
"""

import json
import os
import shutil
import sys
import time

# Ensure the project root is on the path so we can import app.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.scraper import fetch_past_meetings, fetch_meeting_detail, MEETING_TABS
from app.summarizer import extract_meeting_topics, extract_badges
from app.transcriber import load_cached_transcript, correct_timestamps
from app.item_summaries_store import load_cached_summaries

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "_site")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "app", "templates")
STATIC_DIR = os.path.join(PROJECT_ROOT, "app", "static")

ESCRIBEMEETINGS_BASE = "https://pub-saskatoon.escribemeetings.com"


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


def _fetch_topics_and_details(meetings):
    """Fetch per-meeting topics and detail data for a list of Meeting objects."""
    topics_data: dict[str, list] = {}
    details_data: dict[str, dict] = {}
    for i, m in enumerate(meetings):
        mid = m.meeting_id
        print(f"  [{i + 1}/{len(meetings)}] Fetching topics for {mid[:8]}...")
        try:
            detail = fetch_with_retry(
                fetch_meeting_detail, mid, include_votes=True,
            )
            items = [item.to_dict() for item in detail["agenda_items"]]

            # Correct timestamps using cached transcript if available
            transcript = load_cached_transcript(mid)
            if transcript:
                print(f"    Applying transcript timestamps ({len(transcript)} segments)")
                items = correct_timestamps(items, transcript)

            # Attach badges to each item (same as /api/meeting/<id>)
            for item in items:
                item["badges"] = extract_badges(item)

            # Attach categorized chip summaries if available
            chip_summaries = load_cached_summaries(mid)
            if chip_summaries:
                print(f"    Applying chip summaries ({len(chip_summaries)} items)")
                for item in items:
                    item["chip_summaries"] = chip_summaries.get(
                        str(item.get("item_id")), []
                    )

            topics = extract_meeting_topics(items, m.title, max_topics=8)
            topics_data[mid] = topics
            details_data[mid] = {
                "agenda_items": items,
                "video_url": detail["video_url"],
            }
        except Exception as exc:
            print(f"    WARNING: Failed to get topics: {exc}")
            topics_data[mid] = []
            details_data[mid] = {"agenda_items": [], "video_url": None}
    return topics_data, details_data


def fetch_all_data():
    """Fetch meetings list and per-meeting topics from eSCRIBE for all tabs."""
    # Per-tab meeting lists keyed by slug
    all_tabs_meetings: dict[str, dict] = {}
    # Shared across all tabs (meeting IDs are globally unique)
    all_topics: dict[str, list] = {}
    all_details: dict[str, dict] = {}

    for tab in MEETING_TABS:
        slug = tab["slug"]
        meeting_type = tab["type"]
        print(f"\nFetching '{tab['label']}' meetings ({slug})...")
        meetings, total_count = fetch_with_retry(
            fetch_past_meetings, page=1, meeting_type=meeting_type,
        )
        meetings_data = [m.to_dict() for m in meetings]
        print(f"  Got {len(meetings_data)} meetings (total: {total_count})")

        all_tabs_meetings[slug] = {
            "meetings": meetings_data,
            "total_count": total_count,
        }

        topics, details = _fetch_topics_and_details(meetings)
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


def render_meeting_html(meeting_id, detail_data):
    """Render a static meeting detail page with preloaded agenda data."""
    with open(os.path.join(TEMPLATE_DIR, "base.html")) as f:
        base = f.read()
    with open(os.path.join(TEMPLATE_DIR, "meeting.html")) as f:
        meeting = f.read()

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
