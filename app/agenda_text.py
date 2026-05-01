"""Text-shape helpers for agenda-derived strings.

Pure string operations: HTML entity decoding, plain-language title
cleanup, dollar-amount formatting, and length-bounded chip trimming.
No domain interpretation lives here — see ``app.agenda_items`` for that.
"""

from __future__ import annotations

import html as html_mod
import re


def clean_entities(text: str) -> str:
    """Decode common HTML entities and collapse whitespace."""
    text = text.replace("&#58;", ":").replace("&#160;", " ")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def format_money(raw: str) -> str:
    """Convert a raw dollar match like '$1,500,000' into '$1.5M'."""
    if re.search(r'(million|billion)', raw, re.IGNORECASE):
        return raw.strip()
    numeric = raw.replace('$', '').replace(',', '')
    try:
        val = float(numeric)
    except ValueError:
        return raw.strip()
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.1f}B".replace('.0B', 'B')
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M".replace('.0M', 'M')
    if val >= 100_000:
        return f"${val / 1_000:.0f}K"
    return raw.strip()


# Patterns stripped from titles to produce plain-language summaries
PLAIN_REPLACEMENTS = [
    # "Bylaw No. 9876 - The Foo Bylaw, 2025 (No. 3)" → "Foo"
    (re.compile(r'^Bylaw\s+No\.\s*\d+\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\bBylaw\b,?\s*', re.IGNORECASE), ''),
    (re.compile(r'\(No\.\s*\d+\)', re.IGNORECASE), ''),
    # Reference codes like [CC2025-0402], [TS2026-0203], [FI2026-0205], [CK 225-4-3]
    (re.compile(r'\s*\[[\w\s-]+\]\s*$'), ''),
    # "Award of Contract - Foo (Contract No. 25-0456)" → "Foo"
    (re.compile(r'^Award\s+of\s+Contract\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\(Contract\s+No\.\s*[\w-]+\)', re.IGNORECASE), ''),
    # "Request for Expressions of Interest - Foo" → "Foo"
    (re.compile(r'^Request\s+for\s+Expressions?\s+of\s+Interest\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Request for Proposals - Foo" → "Foo"
    (re.compile(r'^Request\s+for\s+Proposals?\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Enquiry - Councillor Name (Date) - Topic" → "Topic"
    (re.compile(r'^Enquiry\s*[-–—]\s*Councillor\s+\S+(?:\s+\S+)?\s*\([^)]*\)\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'^Enquiry\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Councillor X. Name - Notice of Motion - Topic" → "Topic"
    (re.compile(r'^Councillor\s+\S+(?:\s+\S+)?\s*[-–—]\s*Notice\s+of\s+Motion\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Councillor B. Dubois - Topic" → "Topic"
    (re.compile(r'^Councillor\s+\S+\.?\s+\S+\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Report of the City Clerk - Foo" → "Foo"
    (re.compile(r'^Report\s+of\s+the\s+\w[\w\s]{0,30}?[-–—]\s*', re.IGNORECASE), ''),
    # "Appointments - Foo" / "Appointments – Foo"
    (re.compile(r'^Appointments?\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Standing Policy Committee on Foo" → strip
    (re.compile(r'^Standing\s+Policy\s+Committee\s+(?:on\s+)?', re.IGNORECASE), ''),
    # Strip leading "The " after other cleanup
    (re.compile(r'^The\s+', re.IGNORECASE), ''),
    # Strip reference codes like "[FI2026-0204]" or "[CC2025-0802]"
    (re.compile(r'\s*\[\w{2,4}\d{4}-\d{3,5}\]\s*'), ''),
    # Strip year suffixes like ", 2025" or standalone " 2025" at end
    (re.compile(r'[,\s]+\d{4}\s*$'), ''),
    # Collapse extra whitespace / dashes
    (re.compile(r'\s*[-–—]\s*$'), ''),
    (re.compile(r'\s{2,}'), ' '),
]


def plainify(text: str) -> str:
    """Convert a bureaucratic agenda title into plain language."""
    result = html_mod.unescape(text.strip())
    for pattern, repl in PLAIN_REPLACEMENTS:
        result = pattern.sub(repl, result)
    result = result.strip(' -–—,.')
    if result:
        result = result[0].upper() + result[1:]
    return result or text.strip()


def trim_to_chip(text: str, limit: int = 100) -> str:
    """Fit a string into a chip at a natural break, or return ''.

    Pure length/boundary trimming. Caller is responsible for any prior
    cleaning (HTML entities, transcript filler, etc.). Overflow is only
    accepted when there is a sentence or clause boundary that fits within
    ``limit`` characters; otherwise returns ''.
    """
    text = text.strip().strip(",;:")
    if not text:
        return ""
    if len(text) <= limit:
        return text
    for sep in (". ", "! ", "? ", "; ", ", "):
        idx = text.rfind(sep, 0, limit)
        if idx > 20:
            return text[:idx].rstrip(",;:")
    return ""
