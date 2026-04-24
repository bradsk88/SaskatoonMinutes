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

# Whisper initial_prompt — biases the decoder toward domain vocabulary so
# proper nouns and local jargon are transcribed correctly instead of
# phonetic gibberish ("Me was in" → "Meewasin").
WHISPER_INITIAL_PROMPT = (
    "Saskatoon City Council meeting. "
    # Current council (2024-2028)
    "Mayor Cynthia Block. "
    "Councillor Kathryn MacDonald, Councillor Senos Timon, "
    "Councillor Robert Pearce, Councillor Troy Davies, "
    "Councillor Randy Donauer, Councillor Jasmin Parker, "
    "Councillor Holly Kelleher, Councillor Scott Ford, "
    "Councillor Bev Dubois, Councillor Zach Jeffries. "
    # Previous council (2020-2024)
    "Mayor Charlie Clark. "
    "Councillor Darren Hill, Councillor Hilary Gough, "
    "Councillor David Kirton, Councillor Mairin Loewen, "
    "Councillor Sarina Gersher. "
    # City administration
    "City Manager Jeff Jorgenson. City Clerk Joanne Chicken. "
    "City Solicitor. "
    # Indigenous and cultural terms
    "Treaty 6 territory. Métis homeland. "
    "Cree. Dakota. Nakota. Dene. Saulteaux. "
    # Local organizations and landmarks
    "Meewasin Valley Authority. Swale Watchers. "
    "Remai Modern. TCU Place. SaskTel Centre. Midtown Plaza. "
    "Nutrien Wonderhub. "
    # Neighbourhoods and geography
    "Nutana. Riversdale. Caswell Hill. City Park. Sutherland. "
    "Broadway. Varsity View. Buena Vista. Haultain. Fairhaven. "
    "Lawson Heights. Stonebridge. Willowgrove. Brighton. "
    "Confederation. Lakeview. Pleasant Hill. Meadowgreen. Mayfair. "
    "Blairmore. Rosewood. Evergreen. Kensington. Montgomery. "
    "Hampton Village. College Park. Silverspring. "
    # Major roads and infrastructure
    "Idylwyld Drive. Circle Drive. College Drive. Preston Avenue. "
    "8th Street. 25th Street. 22nd Street. Attridge Drive. "
    "Chief Mistawasis Bridge. University Bridge. Broadway Bridge. "
    # Acronyms and committees
    "SPC. GPC. BRT. Bus Rapid Transit. "
    "Standing Policy Committee. "
    "Governance and Priorities Committee. "
    "Municipal Planning Commission. "
    "Board of Police Commissioners."
)


def _extract_video_mp4_url(meeting_id: str) -> str | None:
    """Fetch the eSCRIBE player page and extract the direct MP4 URL.

    The standalone player page uses ``data-file_name`` while the embedded
    player on Meeting.aspx uses ``data-stream_name``.  We try both.

    The full URL is ``https://video.isilive.ca/{client_id}/{file_name}``.
    """
    url = (
        f"{BASE_URL}/Players/ISIStandAlonePlayer.aspx?Id={meeting_id}"
    )
    resp = requests.get(url, headers=_PAGE_HEADERS, timeout=30, verify=False)
    resp.raise_for_status()

    client_match = re.search(
        r'data-client_id="([^"]+)"', resp.text, re.IGNORECASE,
    )
    # Try data-file_name first (standalone player), then data-stream_name
    file_match = re.search(
        r'data-file_name="([^"]+)"', resp.text, re.IGNORECASE,
    ) or re.search(
        r'data-stream_name="([^"]+)"', resp.text, re.IGNORECASE,
    )
    if not client_match or not file_match:
        return None

    client_id = client_match.group(1)
    file_name = file_match.group(1)
    # URL-encode spaces in the file name
    file_name_encoded = file_name.replace(" ", "%20")
    return f"https://video.isilive.ca/{client_id}/{file_name_encoded}"


def _extract_audio(video_url: str, output_path: str) -> None:
    """Download and extract mono 16kHz OGG/Opus audio from a video URL.

    Uses OGG/Opus instead of WAV to keep file size manageable (~30MB vs
    ~900MB for a full council meeting).  faster-whisper can read OGG
    directly via ffmpeg/libav.

    The ``-reconnect`` flags tell ffmpeg to retry when the HTTP connection
    drops mid-stream, which prevents truncated downloads on long council
    meetings (3-5 hours).
    """
    cmd = [
        "ffmpeg", "-y",
        # HTTP reconnect options — essential for long streams
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "30",
        "-i", video_url,
        "-vn",                  # no video
        "-ac", "1",             # mono
        "-ar", "16000",         # 16kHz (what Whisper expects)
        "-c:a", "libopus",     # Opus codec in OGG container
        "-b:a", "32k",         # 32kbps is plenty for speech
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def _probe_duration(audio_path: str) -> str:
    """Return a human-readable duration string via ffprobe, or 'unknown'."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        secs = float(result.stdout.strip())
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    except Exception:
        return "duration unknown"


def _split_audio(input_path: str, output_dir: str, chunk_minutes: int = 30) -> list[str]:
    """Split an audio file into fixed-length chunks.

    Returns list of chunk file paths in order.  Each chunk is named
    chunk_0000.ogg, chunk_0001.ogg, etc.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-f", "segment",
        "-segment_time", str(chunk_minutes * 60),
        "-c:a", "libopus",
        "-b:a", "32k",
        "-ar", "16000",
        "-ac", "1",
        os.path.join(output_dir, "chunk_%04d.ogg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg split failed: {result.stderr[-500:]}")

    chunks = sorted(
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith("chunk_") and f.endswith(".ogg")
    )
    return chunks


def _transcribe_audio(audio_path: str, model: "WhisperModel") -> list[dict]:
    """Transcribe a single audio file using an already-loaded model.

    Returns a list of segment dicts with keys:
        start_ms, end_ms, text
    """
    segments, _info = model.transcribe(
        audio_path,
        language="en",
        initial_prompt=WHISPER_INITIAL_PROMPT,
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


def transcribe_meeting(meeting_id: str, model_size: str = "tiny") -> list[dict]:
    """Full pipeline: extract video URL, download audio, transcribe in chunks.

    Splits audio into 30-minute chunks to keep memory usage bounded.
    Returns list of transcript segments [{start_ms, end_ms, text}, ...].
    """
    from faster_whisper import WhisperModel

    mp4_url = _extract_video_mp4_url(meeting_id)
    if not mp4_url:
        raise ValueError(f"Could not find MP4 URL for meeting {meeting_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "full_audio.ogg")
        print(f"  Extracting audio from {mp4_url}...", flush=True)
        _extract_audio(mp4_url, audio_path)

        audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        duration_info = _probe_duration(audio_path)
        print(f"  Audio extracted: {audio_size_mb:.0f} MB, {duration_info}", flush=True)

        # Split into chunks
        chunks_dir = os.path.join(tmpdir, "chunks")
        os.makedirs(chunks_dir)
        chunks = _split_audio(audio_path, chunks_dir, chunk_minutes=30)
        print(f"  Split into {len(chunks)} chunks", flush=True)

        # Delete full audio to free disk
        os.remove(audio_path)

        # Load model once
        print(f"  Loading faster-whisper ({model_size})...", flush=True)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

        # Transcribe each chunk
        all_segments: list[dict] = []
        chunk_duration_ms = 30 * 60 * 1000  # 30 minutes

        for i, chunk_path in enumerate(chunks):
            offset_ms = i * chunk_duration_ms
            print(f"  Transcribing chunk {i + 1}/{len(chunks)}...", flush=True)
            chunk_segments = _transcribe_audio(chunk_path, model)

            # Adjust timestamps by chunk offset
            for seg in chunk_segments:
                seg["start_ms"] += offset_ms
                seg["end_ms"] += offset_ms
            all_segments.extend(chunk_segments)

            # Delete chunk after processing
            os.remove(chunk_path)

        print(f"  Got {len(all_segments)} transcript segments", flush=True)

    return all_segments


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

    Uses `git show` to read from the branch directly.  Tries the local
    branch first, then falls back to origin/ (for CI where the branch
    may not be checked out locally).
    """
    for ref in [TRANSCRIPT_BRANCH, f"origin/{TRANSCRIPT_BRANCH}"]:
        blob_path = f"{ref}:{TRANSCRIPT_DIR}/{meeting_id}.json"
        try:
            raw = _git("show", blob_path)
            return json.loads(raw)
        except (RuntimeError, json.JSONDecodeError):
            continue
    return None


def save_transcript(meeting_id: str, segments: list[dict]) -> None:
    """Save a transcript JSON to the orphan branch.

    Creates the orphan branch if it doesn't exist yet.  All operations
    use a temporary worktree so the main working tree is never touched.
    """
    repo_root = _git("rev-parse", "--show-toplevel")

    # Check if the branch exists locally
    branch_exists = True
    try:
        _git("rev-parse", "--verify", TRANSCRIPT_BRANCH)
    except RuntimeError:
        branch_exists = False

    with tempfile.TemporaryDirectory() as tmpdir:
        worktree_path = os.path.join(tmpdir, "wt")

        if branch_exists:
            _git("worktree", "add", worktree_path, TRANSCRIPT_BRANCH)
        else:
            # Create orphan branch directly via worktree
            _git(
                "worktree", "add", "--detach", worktree_path,
            )
            # Inside the worktree, create the orphan branch
            _git("checkout", "--orphan", TRANSCRIPT_BRANCH, cwd=worktree_path)
            _git("rm", "-rf", ".", cwd=worktree_path)

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
            _git("worktree", "remove", "--force", worktree_path)


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


# ---------------------------------------------------------------------------
# Transcript-based timestamp correction
# ---------------------------------------------------------------------------

# Words that are too common to be useful for matching
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that "
    "the to was were will with this their them they these those which who "
    "city council committee public report recommendation standing policy "
    "agenda item section update overview".split()
)


def _extract_keywords(title: str) -> list[str]:
    """Pull distinctive keywords from an agenda item title.

    Strips reference codes like [GPC2024-0603] and common filler words,
    returning lowercase keywords sorted longest-first (more specific first).
    """
    # Remove bracketed reference codes
    cleaned = re.sub(r"\[.*?\]", "", title)
    # Remove punctuation except hyphens within words
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
    words = []
    for w in cleaned.lower().split():
        # Keep words that are distinctive (not stop words, not too short)
        if len(w) >= 3 and w not in _STOP_WORDS:
            words.append(w)
    # Longer words are more distinctive
    words.sort(key=len, reverse=True)
    return words


def _section_number_patterns(section_number: str) -> list[re.Pattern]:
    """Build regex patterns that match how a chair might say a section number.

    "9.2.1" could be spoken as:
      - "9.2.1" (verbatim in transcript)
      - "nine two one" or "nine point two point one"
      - "item 9.2.1"
    """
    clean = section_number.rstrip(".")
    if not clean:
        return []

    # Exact digits with optional separators: "9.2.1", "9 2 1"
    digits = clean.split(".")
    sep = r"[\s.,]+"
    digit_pattern = sep.join(re.escape(d) for d in digits)

    patterns = [
        # "item 9.2.1" or "item nine two one"
        re.compile(r"\bitem\s+" + digit_pattern, re.IGNORECASE),
        # Just the number at a word boundary
        re.compile(r"\b" + digit_pattern + r"\b", re.IGNORECASE),
    ]

    # Also try matching spoken numbers for single-digit parts
    _SPOKEN = {
        "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
        "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
        "11": "eleven", "12": "twelve", "13": "thirteen", "14": "fourteen",
        "15": "fifteen",
    }
    spoken_parts = [_SPOKEN.get(d, d) for d in digits]
    spoken_pattern = r"\s+".join(spoken_parts)
    patterns.append(re.compile(r"\b" + spoken_pattern + r"\b", re.IGNORECASE))

    return patterns


def _find_in_transcript(
    segments: list[dict],
    section_number: str,
    title: str,
    escribemeetings_start_ms: int | None,
) -> int | None:
    """Find the best transcript timestamp for an agenda item.

    Strategy (in priority order):
    1. Look for the section number being spoken near the eSCRIBE timestamp
    2. Look for distinctive title keywords near the eSCRIBE timestamp
    3. Fall back to the eSCRIBE timestamp if no transcript match found

    Returns the corrected start time in ms, or None if no match.
    """
    if not segments:
        return None

    # Build a combined text index for searching: list of (start_ms, text)
    # Combine nearby segments into larger windows for better matching
    WINDOW_MS = 30_000  # 30-second windows
    windows: list[tuple[int, str]] = []
    current_start = segments[0]["start_ms"]
    current_texts: list[str] = []

    for seg in segments:
        if seg["start_ms"] - current_start > WINDOW_MS and current_texts:
            windows.append((current_start, " ".join(current_texts)))
            current_start = seg["start_ms"]
            current_texts = []
        current_texts.append(seg["text"])
    if current_texts:
        windows.append((current_start, " ".join(current_texts)))

    # If we have an eSCRIBE timestamp, search in a window around it first,
    # then expand. If no eSCRIBE timestamp, search everywhere.
    NEARBY_MS = 300_000  # 5 minutes

    def _score_window(win_start_ms: int, text: str) -> tuple[int, int]:
        """Score a window for matching. Returns (priority, -distance).

        Higher priority = better match. Among equal priorities, prefer
        the one closest to the eSCRIBE timestamp.
        """
        score = 0

        # Check section number patterns
        for pat in _section_number_patterns(section_number):
            if pat.search(text):
                score = max(score, 3)
                break

        # Check title keywords (need at least 2 matches for confidence)
        keywords = _extract_keywords(title)
        if keywords:
            matched = sum(1 for kw in keywords if kw in text.lower())
            if matched >= 2:
                score = max(score, 2)
            elif matched == 1 and len(keywords) == 1:
                score = max(score, 1)

        if score == 0:
            return (0, 0)

        # Distance penalty (prefer matches near the eSCRIBE timestamp)
        if escribemeetings_start_ms is not None:
            distance = abs(win_start_ms - escribemeetings_start_ms)
        else:
            distance = 0

        return (score, -distance)

    best_score = (0, 0)
    best_start_ms: int | None = None

    for win_start, text in windows:
        # If we have an eSCRIBE hint, skip windows that are very far away
        # unless we haven't found anything nearby
        score = _score_window(win_start, text)
        if score[0] > best_score[0] or (
            score[0] == best_score[0] and score[0] > 0 and score[1] > best_score[1]
        ):
            best_score = score
            best_start_ms = win_start

    return best_start_ms


def correct_timestamps(
    agenda_items: list[dict],
    transcript: list[dict],
) -> list[dict]:
    """Apply transcript-based timestamp corrections to agenda items.

    Takes agenda items (as dicts from AgendaItem.to_dict()) and a transcript
    (list of {start_ms, end_ms, text} segments). Returns the same items list
    with corrected timestamps where transcript matches were found.

    Only corrects items that have their own bookmark (not inherited).
    """
    if not transcript:
        return agenda_items

    for item in agenda_items:
        # Skip items without their own timestamp or consent-agenda items
        if item.get("timestamp_inherited", False):
            continue
        if item.get("time_start_ms") is None:
            continue

        match_ms = _find_in_transcript(
            transcript,
            item.get("section_number", ""),
            item.get("title", ""),
            item.get("time_start_ms"),
        )

        if match_ms is not None:
            item["time_start_ms"] = match_ms
            # Recalculate formatted time
            total_seconds = match_ms // 1000
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            if hours > 0:
                item["time_start_formatted"] = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                item["time_start_formatted"] = f"{minutes}:{seconds:02d}"

    return agenda_items
