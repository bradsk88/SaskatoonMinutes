#!/usr/bin/env python3
"""
Compare two attachment-gist styles on real agenda PDFs.

Pulls attachments from upcoming Scheduled Meetings (or a given meeting),
extracts text from each PDF, and generates both styles with Gemini:

- **hook** — one sentence, ~25 words max, written to catch a skimming
  citizen's interest ("Proposes raising 2025 property tax 4.38%...").
- **abstract** — a compressed 2–3 sentence summary.

Output is a markdown report with both gists side by side per attachment,
for eyeball review.  Nothing is cached or committed — this is a
one-shot experiment to pick the style before building the feature.

Usage:
    venv/bin/python scripts/gist_experiment.py [--limit 15]
    venv/bin/python scripts/gist_experiment.py --meeting-id <id>
    venv/bin/python scripts/gist_experiment.py --pdf path/or/url.pdf ...

Requires GEMINI_API_KEY.
"""

import argparse
import io
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import requests
from pypdf import PdfReader

from app.escribe import EscribeMeetingSource, LiveEscribeTransport
from app.item_categorizer import GEMINI_MODEL

MAX_PAGES = 15
MAX_CHARS = 30_000
TIMEOUT = 30

HOOK_PROMPT = """\
You are helping citizens of Saskatoon decide whether they care about a \
city council agenda item enough to register to speak.

Below is the text of one PDF attachment to an agenda item titled \
"{title}".

Write 1–2 sentences, 40 words maximum.  Sentence one: what the document \
proposes or discusses, leading with the action and the key number or \
name if there is one.  Then name WHO should care — the group, \
neighbourhood, or kind of person this touches ("affects anyone who \
drives downtown", "matters to Transit riders").  No preamble, no "This \
document...".  If the text is unreadable (scanned images, pure tables, \
no substance), answer exactly: SKIP

Document text:
{text}"""

FIVE_WS_PROMPT = """\
You are helping citizens of Saskatoon decide whether they care about a \
city council agenda item enough to register to speak.

Below is the text of one PDF attachment to an agenda item titled \
"{title}".

Summarize it as exactly five lines, one per W, in this order and \
format:

What: <what the document proposes or discusses, one phrase or sentence>
Who: <who should care — the group, neighbourhood, or kind of person \
affected>
When: <dates, deadlines, or timelines mentioned>
Where: <places affected — neighbourhoods, streets, facilities>
Why: <why this is being proposed — the problem or goal>

Rules: 10 words max per line — terse phrases, not sentences.  If a W \
genuinely does not apply, write "—" for that line instead of stretching.  Never invent dates, amounts, \
or places that are not in the text.  If the text is unreadable (scanned \
images, pure tables, no substance), answer exactly: SKIP

Document text:
{text}"""

ITEM_WS_PROMPT = """\
You are helping citizens of Saskatoon decide whether they care about a \
city council agenda item enough to register to speak.

Below are the texts of ALL PDF attachments to one agenda item titled \
"{title}".  Synthesize them together — do not describe the documents \
themselves.

Summarize the item as exactly five lines, one per W, in this order and \
format:

What: <what is being proposed or discussed, one terse phrase>
Who: <who should care — the group, neighbourhood, or kind of person \
affected>
When: <dates, deadlines, or timelines>
Where: <places affected — neighbourhoods, streets, facilities>
Why: <the problem or goal driving this>

Rules: 10 words max per line — terse phrases, not sentences.  If a W \
genuinely does not apply, write "—" for that line instead of stretching. \
 Never invent dates, amounts, or places that are not in the texts.  If \
the texts are unreadable, answer exactly: SKIP

Attachment texts:
{text}"""

ABSTRACT_PROMPT = """\
You are helping citizens of Saskatoon decide whether they care about a \
city council agenda item enough to register to speak.

Below is the text of one PDF attachment to an agenda item titled \
"{title}".

Write a compressed abstract of 2–3 sentences: what the document is, what \
it proposes or concludes, and anything a citizen would want to know \
before deciding to dig in.  Plain language.  If the text is unreadable \
(scanned images, pure tables, no substance), answer exactly: SKIP

Document text:
{text}"""


def extract_text(pdf_bytes: bytes) -> str:
    """First MAX_PAGES pages of text, capped at MAX_CHARS.  '' on failure."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages[:MAX_PAGES]:
            parts.append(page.extract_text() or "")
        return "".join(parts)[:MAX_CHARS].strip()
    except Exception:
        return ""


def generate(client, prompt: str) -> str:
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


def collect_attachments(source, meeting_id: str | None, limit: int):
    """(item_title, attachment) pairs from scheduled meeting agendas."""
    from datetime import date, timedelta

    if meeting_id:
        meetings = [source.load_detail(meeting_id)]
    else:
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=60)).isoformat()
        scheduled = [s for s in source.list_scheduled(start, end) if s.has_agenda]
        meetings = [source.load_detail(s.meeting_id) for s in scheduled]

    pairs = []
    for m in meetings:
        for item in m.agenda_items:
            for att in (item.attachments or []):
                if att.get("url", "").lower().endswith(".pdf") or "DocumentId" in att.get("url", ""):
                    pairs.append((m, item.title or "(untitled)", att))
    return pairs[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=15,
                        help="Max attachments to process (default: 15).")
    parser.add_argument("--meeting-id", default=None,
                        help="Use this meeting instead of upcoming ones.")
    parser.add_argument("--pdf", nargs="*", default=None,
                        help="Local paths or URLs; skips agenda scraping.")
    parser.add_argument("--out", default="gist_report.md",
                        help="Report path (default: gist_report.md).")
    args = parser.parse_args()

    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    if args.pdf:
        pairs = [(None, "(ad-hoc)", {"name": p, "url": p}) for p in args.pdf]
    else:
        source = EscribeMeetingSource(LiveEscribeTransport())
        pairs = collect_attachments(source, args.meeting_id, args.limit)
        print(f"Collected {len(pairs)} attachments")

    # Group by item so we can also synthesize an item-level 5 Ws.
    items: dict[str, tuple] = {}
    for meeting, title, att in pairs:
        key = f"{meeting.date if meeting else ''}|{title}"
        items.setdefault(key, (meeting, title, []))[2].append(att)

    lines = ["# Attachment Gist Experiment\n"]
    i = 0
    for meeting, title, atts in items.values():
        item_texts = []
        for att in atts:
            i += 1
            text = _attachment_section(
                lines, i, len(pairs), meeting, title, att, client,
            )
            if text:
                item_texts.append(text)
        combined = "\n\n---\n\n".join(item_texts)[:60_000]
        if combined.strip():
            ws = generate(client, ITEM_WS_PROMPT.format(title=title, text=combined))
            lines.append(f"\n### Item-level 5 Ws — {title}\n\n{ws}\n")

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nWrote {args.out}")


def _attachment_section(lines, i, total, meeting, title, att, client) -> str:
    """One report section per attachment.  Returns extracted text ('' on failure)."""
    name, url = att["name"], att["url"]
    where = f"[{meeting.date} {meeting.title}] " if meeting else ""
    print(f"[{i}/{total}] {name[:70]}", flush=True)

    if url.startswith("http"):
        try:
            resp = requests.get(url, timeout=TIMEOUT, verify=False)
            resp.raise_for_status()
            pdf_bytes = resp.content
        except Exception as exc:
            lines.append(f"\n## {i}. {name}\n\n{where}{title}\n\n"
                         f"**Download failed:** {exc}\n")
            return ""
    else:
        with open(url, "rb") as fh:
            pdf_bytes = fh.read()

    text = extract_text(pdf_bytes)
    if not text:
        lines.append(f"\n## {i}. {name}\n\n{where}{title}\n\n"
                     f"**No extractable text** (scanned?)\n")
        return ""

    hook = generate(client, HOOK_PROMPT.format(title=title, text=text))
    ws = generate(client, FIVE_WS_PROMPT.format(title=title, text=text))

    lines.append(
        f"\n## {i}. {name}\n\n"
        f"{where}{title}\n\n"
        f"{url}\n\n"
        f"**Hook:** {hook}\n\n"
        f"**5 Ws:**\n\n{ws}\n"
    )
    return text


if __name__ == "__main__":
    main()
