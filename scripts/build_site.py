#!/usr/bin/env python3
"""
Build a static version of the Saskatoon Council Summarizer for GitHub Pages.

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

from app.scraper import fetch_past_meetings, fetch_meeting_detail
from app.summarizer import extract_meeting_topics

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


def fetch_all_data():
    """Fetch meetings list and per-meeting topics from eSCRIBE."""
    print("Fetching page-1 meetings...")
    meetings, total_count = fetch_with_retry(fetch_past_meetings, page=1)
    meetings_data = [m.to_dict() for m in meetings]
    print(f"  Got {len(meetings_data)} meetings (total: {total_count})")

    topics_data: dict[str, list] = {}
    for i, m in enumerate(meetings):
        mid = m.meeting_id
        print(f"  [{i + 1}/{len(meetings)}] Fetching topics for {mid[:8]}...")
        try:
            detail = fetch_with_retry(
                fetch_meeting_detail, mid, include_votes=True,
            )
            items = [item.to_dict() for item in detail["agenda_items"]]
            topics = extract_meeting_topics(items, m.title, max_topics=8)
            topics_data[mid] = topics
        except Exception as exc:
            print(f"    WARNING: Failed to get topics: {exc}")
            topics_data[mid] = []

    return meetings_data, total_count, topics_data


def render_index_html(meetings_data, total_count, topics_data):
    """Read the Jinja2 templates and produce a flat static HTML file.

    Manually flattens base.html + index.html by replacing the Jinja2 block
    tags with concrete content.  This avoids needing a Flask app context.
    """
    with open(os.path.join(TEMPLATE_DIR, "base.html")) as f:
        base = f.read()
    with open(os.path.join(TEMPLATE_DIR, "index.html")) as f:
        index = f.read()

    # --- Extract blocks from index.html ---
    def extract_block(name, text):
        open_tag = f"{{% block {name} %}}"
        # Find the LAST occurrence of {% endblock %} that closes this block
        start = text.find(open_tag)
        if start == -1:
            return ""
        after_open = start + len(open_tag)
        end = text.find("{% endblock %}", after_open)
        return text[after_open:end].strip()

    title_block = extract_block("title", index) or "Saskatoon Council Summarizer"
    content_block = extract_block("content", index)

    # Scripts block: need to be more careful since there may be multiple endblocks
    scripts_start = index.find("{% block scripts %}")
    scripts_end = index.rfind("{% endblock %}")
    scripts_block = index[scripts_start + len("{% block scripts %}"):scripts_end].strip() if scripts_start != -1 else ""

    # --- Build preloaded data script tags ---
    preloaded_meetings = json.dumps({
        "meetings": meetings_data,
        "total_count": total_count,
        "page": 1,
    }, separators=(",", ":"))

    preloaded_topics = json.dumps(topics_data, separators=(",", ":"))

    data_scripts = (
        f'<script type="application/json" id="preloaded-meetings">'
        f"{preloaded_meetings}</script>\n"
        f'<script type="application/json" id="preloaded-topics">'
        f"{preloaded_topics}</script>"
    )

    # --- Assemble final HTML from base.html ---
    output = base
    output = output.replace(
        "{% block title %}Saskatoon Council Summarizer{% endblock %}",
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


def main():
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)

    meetings_data, total_count, topics_data = fetch_all_data()

    print("Rendering static index.html...")
    html = render_index_html(meetings_data, total_count, topics_data)

    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(html)

    shutil.copy2(
        os.path.join(STATIC_DIR, "style.css"),
        os.path.join(OUTPUT_DIR, "style.css"),
    )

    print(f"Static site built in {OUTPUT_DIR}/")
    print(f"  index.html ({len(html):,} bytes)")
    print(f"  style.css")


if __name__ == "__main__":
    main()
