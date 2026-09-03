"""
Flask web application for Saskatoon City Council Meeting Summarizer.

Provides a web UI to browse meetings, view AI-generated summaries of
agenda items, and jump to specific timestamps in the meeting video.
"""

import os
from datetime import date
from flask import Flask, current_app, render_template, jsonify, request
from requests.exceptions import ConnectionError, SSLError
from dotenv import load_dotenv
from app.agenda_items import (
    count_agenda_items,
    count_consent_items,
    count_discussed_items,
    mark_row_weights,
)
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS, _SLUG_TO_TYPE
from app.models import meeting_recording_state
from app.summarizer import (
    summarize_agenda_items, extract_meeting_topics, extract_badges,
    speaker_roster,
)

_CONNECTION_ERROR_MSG = (
    "Could not connect to the City of Saskatoon eSCRIBE server. "
    "Check your internet connection and try again."
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-key-change-me")
app.config["meeting_source"] = EscribeMeetingSource(LiveEscribeTransport())


def _source() -> MeetingSource:
    return current_app.config["meeting_source"]


@app.route("/")
def index():
    """Landing page showing recent City Council meetings."""
    return render_template("index.html", meeting_tabs=MEETING_TABS)


@app.route("/about")
def about():
    """About, disclaimer, and contact page."""
    return render_template("about.html")


@app.route("/api/meetings")
def api_meetings():
    """API endpoint to fetch paginated list of past meetings.

    Query params:
        page  – page number (default 1)
        type  – meeting-type slug (e.g. "council", "budget").  When omitted
                the default regular-business type is used.
    """
    page = request.args.get("page", 1, type=int)
    slug = request.args.get("type", "")
    meeting_type = _SLUG_TO_TYPE.get(slug)  # None → default
    try:
        meetings, total_count = _source().list_past(page, meeting_type=meeting_type)
        return jsonify({
            "meetings": [m.to_dict() for m in meetings],
            "total_count": total_count,
            "page": page,
        })
    except (ConnectionError, SSLError):
        return jsonify({"error": _CONNECTION_ERROR_MSG}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meeting/<meeting_id>")
def meeting_detail(meeting_id: str):
    """Page showing a specific meeting with summarized agenda items."""
    return render_template("meeting.html", meeting_id=meeting_id)


@app.route("/api/meeting/<meeting_id>")
def api_meeting_detail(meeting_id: str):
    """API endpoint to fetch meeting details with agenda items and timestamps."""
    try:
        detail = _source().load_detail(meeting_id)
        items = [item.to_dict() for item in detail.agenda_items]

        for item in items:
            item["badges"] = extract_badges(item)

        mark_row_weights(items)

        # Summarize if requested
        if request.args.get("summarize", "false").lower() == "true":
            title = request.args.get("title", "City Council Meeting")
            items = summarize_agenda_items(items, title)

        return jsonify({
            "agenda_items": items,
            "video_url": detail.video_url,
            # Where the video would be says something when there is no
            # video: the recording is pending, or the meeting was not
            # recorded.  The list's HasVideo is not in hand here, so the
            # detail's own video stands in for it.
            "recording_state": meeting_recording_state(
                bool(detail.video_url), False, detail.date, date.today(),
            ),
            "title": detail.title,
            "date": detail.date,
            "start_time": detail.start_time,
            # The same count the index card subtracts from, so "38 other
            # items" and the header agree. Counting the rendered rows
            # instead would include recesses and section headers and make
            # the card look like it had lost thirty items.
            "item_count": count_agenda_items(items),
            # What the header says, and what the page draws. The header
            # used to report 43 above 73 rendered cards.
            "discussed_count": count_discussed_items(items),
            "consent_count": count_consent_items(items),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/meeting/<meeting_id>/topics")
def api_meeting_topics(meeting_id: str):
    """Compact key-topic summaries for a meeting (used by the index page).

    Fetches both the Agenda page (recommendations) and PostMinutes page
    (vote results) to produce topic/outcome pairs.
    """
    try:
        title = request.args.get("title", "City Council Meeting")
        detail = _source().load_detail(meeting_id)
        items = [item.to_dict() for item in detail.agenda_items]
        # Twelve, not the card's ten: the card spends at most ten council
        # rows (five detailed, five title-only), and the payload holds a
        # margin so demotion never runs out of candidates.
        topics = extract_meeting_topics(items, title, max_topics=12)
        roster = speaker_roster(items)
        return jsonify({
            "meeting_id": meeting_id,
            "topics": topics,
            # The card shows a few topics and says how many items it is
            # not showing. That count has to come from the agenda, since
            # topics are the ranked few rather than the whole meeting.
            "total_items": count_agenda_items(items),
            # People, not filings — see build_site.
            "speaker_count": roster["speaker_count"],
            # Consent items roll up into one row on the card — approved
            # without debate, they earn no slot of their own.
            "consent_count": count_consent_items(items),
            # Every organization that spoke, so a packed meeting can
            # collapse speaker rows into an org digest without hiding
            # who had a voice.
            "roster": roster,
        })
    except (ConnectionError, SSLError):
        return jsonify({"error": _CONNECTION_ERROR_MSG}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
