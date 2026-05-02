"""
Flask web application for Saskatoon City Council Meeting Summarizer.

Provides a web UI to browse meetings, view AI-generated summaries of
agenda items, and jump to specific timestamps in the meeting video.
"""

import os
from flask import Flask, current_app, render_template, jsonify, request
from requests.exceptions import ConnectionError, SSLError
from dotenv import load_dotenv
from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.meeting_source import MeetingSource
from app.meeting_types import MEETING_TABS, _SLUG_TO_TYPE
from app.summarizer import summarize_agenda_items, extract_meeting_topics, extract_badges

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

        # Summarize if requested
        if request.args.get("summarize", "false").lower() == "true":
            title = request.args.get("title", "City Council Meeting")
            items = summarize_agenda_items(items, title)

        return jsonify({
            "agenda_items": items,
            "video_url": detail.video_url,
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
        topics = extract_meeting_topics(items, title, max_topics=8)
        return jsonify({"meeting_id": meeting_id, "topics": topics})
    except (ConnectionError, SSLError):
        return jsonify({"error": _CONNECTION_ERROR_MSG}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
