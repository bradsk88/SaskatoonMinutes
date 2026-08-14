"""Generate 5-Ws gists for agenda-item attachment PDFs.

One gist per attachment, written for a citizen skimming a Scheduled
Meeting to decide whether they care enough to register to speak.  The
PDF is fetched once, at provisional-summary time; the gist is never
revised pre-meeting and is discarded when the Scheduled Meeting flips
to a Meeting (the same lifecycle as provisional ItemSummaries).

Failure is silent by design: an unparseable or unreadable PDF simply
produces no gist, and the attachment renders as a bare link.  A wrong
gist is worse than none.
"""

from __future__ import annotations

import io
import os
import re

import requests
from pypdf import PdfReader

from app.item_categorizer import GEMINI_MODEL
from app.models import AttachmentGist

# Beyond this the text is usually appendices and tables; the gist lives
# in the report's front matter.
MAX_PAGES = 15
MAX_CHARS = 30_000
TIMEOUT = 30

PROMPT = """\
You are helping citizens of Saskatoon decide whether they care about a \
city council agenda item enough to register to speak.

Below is the text of one PDF attachment to an agenda item titled \
"{title}".

Summarize it as exactly five lines, one per W, in this order and \
format:

What: <what the document proposes or discusses, one terse phrase>
Who: <who should care — the group, neighbourhood, or kind of person \
affected>
When: <dates, deadlines, or timelines mentioned>
Where: <places affected — neighbourhoods, streets, facilities>
Why: <why this is being proposed — the problem or goal>

Rules: 10 words max per line — terse phrases, not sentences.  If a W \
genuinely does not apply, write "—" for that line instead of stretching. \
 Never invent dates, amounts, or places that are not in the text.  If \
the text is unreadable (scanned images, pure tables, no substance), \
answer exactly: SKIP

Document text:
{text}"""

_LINE_RE = re.compile(r"^\s*(What|Who|When|Where|Why)\s*:\s*(.*)$")


def extract_text(pdf_bytes: bytes) -> str:
    """First MAX_PAGES pages of text, capped.  '' on any failure."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = [page.extract_text() or "" for page in reader.pages[:MAX_PAGES]]
        return "".join(parts)[:MAX_CHARS].strip()
    except Exception:
        return ""


# The eSCRIBE host rejects the default python-requests UA from
# datacenter IPs; the transport in app/escribe.py sends a browser UA
# for the same reason.
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def fetch_text(url: str) -> str:
    """Download the PDF and extract its text.  '' on any failure."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
    except Exception as exc:
        print(f"      gist: download failed — {exc}", flush=True)
        return ""
    text = extract_text(resp.content)
    if not text:
        print(
            f"      gist: no text in {len(resp.content)} bytes "
            f"(content-type: {resp.headers.get('Content-Type', '?')})",
            flush=True,
        )
    return text


def parse_gist(answer: str) -> AttachmentGist | None:
    """Parse the model's five lines.  None on SKIP or unparseable output."""
    if not answer or answer.strip().startswith("SKIP"):
        return None
    fields: dict[str, str] = {}
    for line in answer.splitlines():
        m = _LINE_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip() or "—"
    if len(fields) < 3:
        return None
    return AttachmentGist(
        what=fields.get("what", "—"),
        who=fields.get("who", "—"),
        when=fields.get("when", "—"),
        where=fields.get("where", "—"),
        why=fields.get("why", "—"),
    )


class GistGenerator:
    """Gemini-backed gist generator.  ``enabled`` is False without a key."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self._client = None
        if self._api_key:
            from google import genai  # lazy import
            self._client = genai.Client(api_key=self._api_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def gist(self, title: str, url: str) -> AttachmentGist | None:
        """Fetch *url*, extract text, and gist it.  None on any failure.

        Failures are silent in the UI but logged here — a CI run that
        produced zero gists must say why.
        """
        if not self._client:
            print("      gist: skipped (no GEMINI_API_KEY)", flush=True)
            return None
        text = fetch_text(url)
        if not text:
            print(f"      gist: no extractable text — {url[:80]}", flush=True)
            return None
        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=PROMPT.format(title=title, text=text),
            )
        except Exception as exc:
            print(f"      gist: model call failed — {exc}", flush=True)
            return None
        gist = parse_gist(response.text or "")
        if gist is None:
            print(
                f"      gist: model declined/unparseable — "
                f"{(response.text or '')[:80]!r}",
                flush=True,
            )
        return gist
