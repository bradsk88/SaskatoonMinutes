"""
Transcribe meeting videos using faster-whisper and cache results.

Extracts audio from eSCRIBE meeting videos (hosted on isilive.ca),
transcribes with faster-whisper, and caches transcript JSON on an
orphan git branch ('transcripts') to avoid re-processing.
"""

import json
import os
import re
import subprocess
import tempfile

import requests

BASE_URL = "https://pub-saskatoon.escribemeetings.com"

_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}


def _extract_video_mp4_url(meeting_id: str) -> str | None:
    """Fetch the eSCRIBE player page and extract the direct MP4 URL.

    The player page contains an element like:
        <div id="isi_player" data-client_id="saskatoon"
             data-stream_name="Council Chambers_TYPE_DATE.mp4" ...>
    The full URL is https://video.isilive.ca/{client_id}/{stream_name}
    """
    url = (
        f"{BASE_URL}/Players/ISIStandAlonePlayer.aspx?Id={meeting_id}"
    )
    resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
    resp.raise_for_status()

    client_match = re.search(
        r'data-client_id="([^"]+)"', resp.text, re.IGNORECASE,
    )
    stream_match = re.search(
        r'data-stream_name="([^"]+)"', resp.text, re.IGNORECASE,
    )
    if not client_match or not stream_match:
        return None

    client_id = client_match.group(1)
    stream_name = stream_match.group(1)
    # URL-encode spaces in the stream name
    stream_name_encoded = stream_name.replace(" ", "%20")
    return f"https://video.isilive.ca/{client_id}/{stream_name_encoded}"


def _extract_audio(video_url: str, output_path: str) -> None:
    """Download and extract mono 16kHz WAV audio from a video URL via ffmpeg.

    Uses ffmpeg's ability to stream from HTTP URLs, so we never download
    the full video file to disk.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_url,
        "-vn",                  # no video
        "-ac", "1",             # mono
        "-ar", "16000",         # 16kHz (what Whisper expects)
        "-c:a", "pcm_s16le",   # 16-bit PCM WAV
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _transcribe_audio(audio_path: str, model_size: str = "base") -> list[dict]:
    """Transcribe audio file using faster-whisper.

    Returns a list of segment dicts with keys:
        start_ms, end_ms, text
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio_path,
        language="en",
        vad_filter=True,           # skip silence automatically
        vad_parameters={
            "min_silence_duration_ms": 1000,
        },
    )

    result = []
    for seg in segments:
        result.append({
            "start_ms": int(seg.start * 1000),
            "end_ms": int(seg.end * 1000),
            "text": seg.text.strip(),
        })
    return result


def transcribe_meeting(meeting_id: str, model_size: str = "base") -> list[dict]:
    """Full pipeline: extract video URL, download audio, transcribe.

    Returns list of transcript segments [{start_ms, end_ms, text}, ...].
    """
    mp4_url = _extract_video_mp4_url(meeting_id)
    if not mp4_url:
        raise ValueError(f"Could not find MP4 URL for meeting {meeting_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.wav")
        print(f"  Extracting audio from {mp4_url}...")
        _extract_audio(mp4_url, audio_path)

        audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        print(f"  Audio extracted: {audio_size_mb:.0f} MB")

        print(f"  Transcribing with faster-whisper ({model_size})...")
        segments = _transcribe_audio(audio_path, model_size)
        print(f"  Got {len(segments)} transcript segments")

    return segments


# ---------------------------------------------------------------------------
# Transcript cache on orphan branch
# ---------------------------------------------------------------------------

TRANSCRIPT_BRANCH = "transcripts"
TRANSCRIPT_DIR = "transcripts"  # path within the branch


def _git(*args: str, cwd: str | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def load_cached_transcript(meeting_id: str) -> list[dict] | None:
    """Try to load a transcript from the orphan branch without checking it out.

    Uses `git show` to read from the branch directly.
    """
    blob_path = f"{TRANSCRIPT_BRANCH}:{TRANSCRIPT_DIR}/{meeting_id}.json"
    try:
        raw = _git("show", blob_path)
        return json.loads(raw)
    except (RuntimeError, json.JSONDecodeError):
        return None


def save_transcript(meeting_id: str, segments: list[dict]) -> None:
    """Save a transcript JSON to the orphan branch and push.

    Creates the orphan branch if it doesn't exist yet.
    """
    repo_root = _git("rev-parse", "--show-toplevel")

    # Ensure the orphan branch exists locally
    try:
        _git("rev-parse", "--verify", TRANSCRIPT_BRANCH)
    except RuntimeError:
        # Create orphan branch with an empty initial commit
        _git("checkout", "--orphan", TRANSCRIPT_BRANCH)
        _git("rm", "-rf", ".", cwd=repo_root)
        os.makedirs(os.path.join(repo_root, TRANSCRIPT_DIR), exist_ok=True)
        readme = os.path.join(repo_root, TRANSCRIPT_DIR, ".gitkeep")
        with open(readme, "w") as f:
            f.write("")
        _git("add", TRANSCRIPT_DIR, cwd=repo_root)
        _git("commit", "-m", "Initialize transcripts branch", cwd=repo_root)
        # Return to previous branch
        _git("checkout", "-", cwd=repo_root)

    # Use a worktree to commit to the orphan branch without switching
    with tempfile.TemporaryDirectory() as tmpdir:
        worktree_path = os.path.join(tmpdir, "wt")
        _git("worktree", "add", worktree_path, TRANSCRIPT_BRANCH)
        try:
            transcript_dir = os.path.join(worktree_path, TRANSCRIPT_DIR)
            os.makedirs(transcript_dir, exist_ok=True)

            filepath = os.path.join(transcript_dir, f"{meeting_id}.json")
            with open(filepath, "w") as f:
                json.dump(segments, f, separators=(",", ":"))

            _git("add", f"{TRANSCRIPT_DIR}/{meeting_id}.json", cwd=worktree_path)
            _git(
                "commit", "-m",
                f"Add transcript for {meeting_id}",
                cwd=worktree_path,
            )
        finally:
            _git("worktree", "remove", worktree_path)


def get_or_transcribe(meeting_id: str, model_size: str = "base") -> list[dict]:
    """Load cached transcript or transcribe and cache."""
    cached = load_cached_transcript(meeting_id)
    if cached is not None:
        print(f"  Using cached transcript for {meeting_id[:8]}")
        return cached

    print(f"  No cached transcript for {meeting_id[:8]}, transcribing...")
    segments = transcribe_meeting(meeting_id, model_size)
    save_transcript(meeting_id, segments)
    return segments
