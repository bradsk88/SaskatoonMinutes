import json
import pytest
from unittest.mock import patch, MagicMock

from app.transcriber import (
    _extract_video_mp4_url,
    load_cached_transcript,
)


class TestExtractVideoMp4Url:
    """Test MP4 URL extraction from the eSCRIBE player page."""

    PLAYER_HTML = """
    <div id="isi_player"
         data-client_id="saskatoon"
         data-stream_name="Council Chambers_CITY COUNCIL_2026-03-25.mp4"
         data-auto_play="false">
    </div>
    """

    @patch("app.transcriber.requests.get")
    def test_extracts_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = self.PLAYER_HTML
        mock_get.return_value = mock_resp

        url = _extract_video_mp4_url("abc-123")
        assert url == (
            "https://video.isilive.ca/saskatoon/"
            "Council%20Chambers_CITY%20COUNCIL_2026-03-25.mp4"
        )

    @patch("app.transcriber.requests.get")
    def test_returns_none_when_no_player(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>No player here</body></html>"
        mock_get.return_value = mock_resp

        assert _extract_video_mp4_url("abc-123") is None


class TestLoadCachedTranscript:
    """Test loading transcripts from the orphan branch."""

    @patch("app.transcriber._git")
    def test_loads_cached(self, mock_git):
        segments = [{"start_ms": 0, "end_ms": 1000, "text": "Hello"}]
        mock_git.return_value = json.dumps(segments)

        result = load_cached_transcript("abc-123")
        assert result == segments
        mock_git.assert_called_once_with(
            "show", "transcripts:transcripts/abc-123.json",
        )

    @patch("app.transcriber._git")
    def test_returns_none_when_missing(self, mock_git):
        mock_git.side_effect = RuntimeError("not found")

        result = load_cached_transcript("abc-123")
        assert result is None
