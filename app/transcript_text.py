"""Text-shape helpers for transcript-derived strings.

Operations whose rules come from transcript shape: filler-lead removal,
sentence-window slicing around a regex match, and sentence splitting.
"""

from __future__ import annotations

import re


FILLER_LEADS = re.compile(
    r"^(?:um+,?\s+|uh+,?\s+|well,?\s+|so,?\s+|i think\s+|you know,?\s+|"
    r"and\s+|but\s+|okay,?\s+|ok,?\s+|right,?\s+|yeah,?\s+|yep,?\s+|"
    r"actually,?\s+|basically,?\s+)+",
    re.IGNORECASE,
)


def strip_filler_leads(text: str) -> str:
    """Remove conversational filler from the start of a transcript snippet."""
    return FILLER_LEADS.sub("", text)


def sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence containing text[start:end], using . ! ? as boundaries."""
    sent_start = 0
    for p in ".!?":
        pos = text.rfind(p, 0, start)
        if pos + 1 > sent_start:
            sent_start = pos + 1
    sent_end = len(text)
    for p in ".!?":
        pos = text.find(p, end)
        if pos != -1 and pos < sent_end:
            sent_end = pos + 1
    return text[sent_start:sent_end].strip()


def split_sentences(
    text: str,
    *,
    min_len: int | None = None,
    max_len: int | None = None,
) -> list[str]:
    """Split text into sentence-like chunks on . ! ? boundaries.

    Returns every chunk by default. Optional ``min_len`` / ``max_len``
    bound each kept sentence's character length inclusively. Defaults are
    None / None — no filtering — because length thresholds are caller
    domain logic ('what's a usable transcript sentence vs. an agenda
    fragment'), not splitter behavior.
    """
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    cleaned = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if min_len is not None and len(p) < min_len:
            continue
        if max_len is not None and len(p) > max_len:
            continue
        cleaned.append(p)
    return cleaned
