import json
import pytest
from unittest.mock import patch, MagicMock

from app.transcriber import (
    WHISPER_INITIAL_PROMPT,
    _extract_video_mp4_url,
    _extract_keywords,
    _probe_duration,
    _section_number_patterns,
    _find_in_transcript,
    correct_timestamps,
    load_cached_transcript,
)


class TestExtractVideoMp4Url:
    """Test MP4 URL extraction from the eSCRIBE player page."""

    PLAYER_HTML_FILE_NAME = """
    <div id="isi_player" data-start-time="0" data-size="inherit"
         data-auto_play="false" style="height: 100%;"
         data-client_id="saskatoon"
         data-file_name="Council Chambers_CITY COUNCIL_2026-03-25.mp4">
    </div>
    """

    PLAYER_HTML_STREAM_NAME = """
    <div id="isi_player"
         data-client_id="saskatoon"
         data-stream_name="Council Chambers_CITY COUNCIL_2026-03-25.mp4"
         data-auto_play="false">
    </div>
    """

    @patch("app.transcriber.requests.get")
    def test_extracts_url_from_file_name(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = self.PLAYER_HTML_FILE_NAME
        mock_get.return_value = mock_resp

        url = _extract_video_mp4_url("abc-123")
        assert url == (
            "https://video.isilive.ca/saskatoon/"
            "Council%20Chambers_CITY%20COUNCIL_2026-03-25.mp4"
        )

    @patch("app.transcriber.requests.get")
    def test_extracts_url_from_stream_name(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = self.PLAYER_HTML_STREAM_NAME
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


# ── Keyword extraction ──────────────────────────────────────────────


class TestExtractKeywords:
    def test_basic_title(self):
        kws = _extract_keywords("Downtown Event and Entertainment District")
        assert "entertainment" in kws
        assert "downtown" in kws
        assert "district" in kws
        # "and" is a stop word
        assert "and" not in kws

    def test_strips_reference_codes(self):
        kws = _extract_keywords("Bridge Damage Events [CC2026-0303]")
        assert "cc2026-0303" not in kws
        assert "bridge" in kws
        assert "damage" in kws

    def test_sorted_longest_first(self):
        kws = _extract_keywords("Ruth Street Active Transportation Plan")
        assert kws.index("transportation") < kws.index("ruth")

    def test_empty_title(self):
        assert _extract_keywords("") == []

    def test_stop_words_removed(self):
        kws = _extract_keywords("City Council Report Update")
        # all are stop words or too short
        assert kws == []


# ── Section number patterns ─────────────────────────────────────────


class TestSectionNumberPatterns:
    def test_matches_digits(self):
        patterns = _section_number_patterns("9.2.1")
        text = "now we move to item 9.2.1 which is about"
        assert any(p.search(text) for p in patterns)

    def test_matches_spoken_numbers(self):
        patterns = _section_number_patterns("9.2.1")
        text = "item nine two one is the active transportation"
        assert any(p.search(text) for p in patterns)

    def test_matches_bare_number(self):
        patterns = _section_number_patterns("6.")
        text = "we will now proceed to 6 which is question period"
        assert any(p.search(text) for p in patterns)

    def test_empty_section(self):
        assert _section_number_patterns("") == []


# ── Transcript matching ─────────────────────────────────────────────


def _make_segments(entries: list[tuple[int, str]]) -> list[dict]:
    """Helper: build segment list from (start_ms, text) tuples."""
    result = []
    for start_ms, text in entries:
        result.append({
            "start_ms": start_ms,
            "end_ms": start_ms + 5000,
            "text": text,
        })
    return result


class TestFindInTranscript:
    def test_finds_section_number_in_speech(self):
        segments = _make_segments([
            (60_000, "We will now deal with the consent items"),
            (120_000, "Moving on to item 9.2.1 connecting Victoria Avenue"),
            (180_000, "The administration recommends approval"),
        ])
        result = _find_in_transcript(
            segments, "9.2.1",
            "Connecting Victoria Avenue from Taylor Street to Ruth Street",
            escribemeetings_start_ms=100_000,
        )
        assert result == 120_000

    def test_finds_keywords_when_no_section_number(self):
        segments = _make_segments([
            (60_000, "We will now discuss the bridge situation"),
            (120_000, "The Ruth Street active transportation plan"),
            (180_000, "All in favour say aye"),
        ])
        result = _find_in_transcript(
            segments, "9.2.1",
            "Connecting Victoria Avenue to Ruth Street - Active Transportation",
            escribemeetings_start_ms=100_000,
        )
        assert result == 120_000

    def test_prefers_nearby_match(self):
        segments = _make_segments([
            (60_000, "item 9.2.1 first mention"),
            (600_000, "item 9.2.1 second mention after recess"),
        ])
        # eSCRIBE says ~600k - prefer the nearby match
        result = _find_in_transcript(
            segments, "9.2.1", "Some Title",
            escribemeetings_start_ms=590_000,
        )
        assert result == 600_000

    def test_returns_none_with_no_match(self):
        segments = _make_segments([
            (60_000, "nothing relevant here"),
            (120_000, "still nothing"),
        ])
        result = _find_in_transcript(
            segments, "9.2.1", "Ruth Street",
            escribemeetings_start_ms=100_000,
        )
        assert result is None

    def test_no_escribemeetings_hint(self):
        segments = _make_segments([
            (60_000, "item 9.2.1 discussion begins"),
        ])
        result = _find_in_transcript(
            segments, "9.2.1", "Ruth Street",
            escribemeetings_start_ms=None,
        )
        assert result == 60_000

    def test_empty_transcript(self):
        result = _find_in_transcript(
            [], "9.2.1", "Ruth Street",
            escribemeetings_start_ms=100_000,
        )
        assert result is None


# ── Full correction pipeline ────────────────────────────────────────


class TestCorrectTimestamps:
    def test_corrects_timestamp(self):
        items = [
            {
                "item_id": 43,
                "section_number": "9.2.1",
                "title": "Ruth Street Active Transportation",
                "time_start_ms": 4661440,
                "time_end_ms": 22477784,
                "time_start_formatted": "1:17:41",
                "timestamp_inherited": False,
            },
        ]
        transcript = _make_segments([
            (4660_000, "item 9.2.1 connecting Victoria Avenue to Ruth Street"),
            (21600_000, "returning to item 9.2.1 after the break"),
        ])
        result = correct_timestamps(items, transcript)
        # Should match the first segment (closest to eSCRIBE timestamp)
        assert result[0]["time_start_ms"] == 4660_000

    def test_skips_inherited_items(self):
        items = [
            {
                "item_id": 10,
                "section_number": "8.1",
                "title": "Consent Item",
                "time_start_ms": 50_000,
                "time_end_ms": 60_000,
                "time_start_formatted": "0:50",
                "timestamp_inherited": True,
            },
        ]
        transcript = _make_segments([
            (100_000, "item 8.1 consent"),
        ])
        result = correct_timestamps(items, transcript)
        # Should not be modified
        assert result[0]["time_start_ms"] == 50_000

    def test_skips_items_without_timestamp(self):
        items = [
            {
                "item_id": 5,
                "section_number": "3.",
                "title": "Declarations",
                "time_start_ms": None,
                "time_end_ms": None,
                "time_start_formatted": None,
                "timestamp_inherited": False,
            },
        ]
        result = correct_timestamps(items, _make_segments([(0, "hello")]))
        assert result[0]["time_start_ms"] is None

    def test_empty_transcript_passthrough(self):
        items = [
            {
                "item_id": 1,
                "section_number": "1.",
                "title": "Call to Order",
                "time_start_ms": 39_000,
                "time_end_ms": 100_000,
                "time_start_formatted": "0:39",
                "timestamp_inherited": False,
            },
        ]
        result = correct_timestamps(items, [])
        assert result[0]["time_start_ms"] == 39_000

    def test_updates_formatted_time(self):
        items = [
            {
                "item_id": 43,
                "section_number": "9.2.1",
                "title": "Ruth Street",
                "time_start_ms": 4661440,
                "time_end_ms": 5000000,
                "time_start_formatted": "1:17:41",
                "timestamp_inherited": False,
            },
        ]
        # Transcript says discussion starts at 1:20:00 = 4800000ms
        transcript = _make_segments([
            (4800_000, "item 9.2.1 discussion of Ruth Street"),
        ])
        result = correct_timestamps(items, transcript)
        assert result[0]["time_start_ms"] == 4800_000
        assert result[0]["time_start_formatted"] == "1:20:00"


# ── Whisper initial_prompt ─────────────────────────────────────────


class TestWhisperInitialPrompt:
    def test_contains_current_mayor(self):
        assert "Cynthia Block" in WHISPER_INITIAL_PROMPT

    def test_contains_previous_mayor(self):
        assert "Charlie Clark" in WHISPER_INITIAL_PROMPT

    def test_contains_key_local_vocabulary(self):
        for term in ("Meewasin", "Métis", "Treaty 6", "Dubois", "Idylwyld"):
            assert term in WHISPER_INITIAL_PROMPT, f"Missing: {term}"

    def test_contains_councillor_names(self):
        for name in ("Donauer", "Davies", "Jeffries", "Kelleher", "Timon"):
            assert name in WHISPER_INITIAL_PROMPT, f"Missing: {name}"


# ── Duration probe ─────────────────────────────────────────────────


class TestProbeDuration:
    @patch("app.transcriber.subprocess.run")
    def test_formats_hours(self, mock_run):
        mock_run.return_value = MagicMock(stdout="10800.5\n")
        assert _probe_duration("/fake.ogg") == "3h00m00s"

    @patch("app.transcriber.subprocess.run")
    def test_formats_short(self, mock_run):
        mock_run.return_value = MagicMock(stdout="5432.0\n")
        assert _probe_duration("/fake.ogg") == "1h30m32s"

    @patch("app.transcriber.subprocess.run")
    def test_returns_unknown_on_error(self, mock_run):
        mock_run.side_effect = Exception("no ffprobe")
        assert _probe_duration("/fake.ogg") == "duration unknown"
