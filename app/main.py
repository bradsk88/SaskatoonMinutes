"""
Flask web application for Saskatoon City Council Meeting Summarizer.

Provides a web UI to browse meetings, view AI-generated summaries of
agenda items, and jump to specific timestamps in the meeting video.
"""

import os
from flask import Flask, render_template, jsonify, request
from requests.exceptions import ConnectionError, SSLError
from dotenv import load_dotenv
from app.scraper import fetch_past_meetings, fetch_meeting_detail, fetch_video_player_config
from app.summarizer import summarize_agenda_items, get_backend

_CONNECTION_ERROR_MSG = (
    "Could not connect to the City of Saskatoon eSCRIBE server. "
    "Check your internet connection and try again."
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-key-change-me")


@app.route("/")
def index():
    """Landing page showing recent City Council meetings."""
    return render_template("index.html")


@app.route("/api/meetings")
def api_meetings():
    """API endpoint to fetch paginated list of past meetings."""
    page = request.args.get("page", 1, type=int)
    try:
        meetings, total_count = fetch_past_meetings(page)
        return jsonify({
            "meetings": [m.to_dict() for m in meetings],
            "total_count": total_count,
            "page": page,
        })
    except (ConnectionError, SSLError):
        return jsonify({"error": _CONNECTION_ERROR_MSG}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/player")
def player():
    """Serve the ISI video player with a configurable start time."""
    return render_template(
        "player.html",
        seek=request.args.get("seek", "0"),
        client_id=request.args.get("client_id", ""),
        file_name=request.args.get("file_name", ""),
    )


@app.route("/meeting/<meeting_id>")
def meeting_detail(meeting_id: str):
    """Page showing a specific meeting with summarized agenda items."""
    return render_template("meeting.html", meeting_id=meeting_id)


@app.route("/api/meeting/<meeting_id>")
def api_meeting_detail(meeting_id: str):
    """API endpoint to fetch meeting details with agenda items and timestamps."""
    try:
        detail = fetch_meeting_detail(meeting_id)
        items = [item.to_dict() for item in detail["agenda_items"]]

        backend = get_backend()

        # Summarize if requested
        if request.args.get("summarize", "false").lower() == "true":
            title = request.args.get("title", "City Council Meeting")
            items = summarize_agenda_items(items, title)

        video_player_config = None
        if detail["video_url"]:
            video_player_config = fetch_video_player_config(meeting_id)

        return jsonify({
            "agenda_items": items,
            "video_url": detail["video_url"],
            "video_player_config": video_player_config,
            "summarizer_backend": backend,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
