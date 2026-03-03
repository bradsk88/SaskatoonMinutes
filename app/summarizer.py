"""
Summarizer module supporting multiple backends.

Backends (set via SUMMARIZER_BACKEND env var):
  - "extractive" (default) — Zero-dependency extractive summarization using
    sentence scoring. No cloud calls, no ML frameworks needed.
  - "local" — Local abstractive summarization using Hugging Face Transformers.
    Requires: pip install transformers torch
"""

import os
import re
import math
from collections import Counter


def get_backend() -> str:
    """Determine which summarization backend to use."""
    backend = os.environ.get("SUMMARIZER_BACKEND", "").lower()
    if backend in ("extractive", "local"):
        return backend
    return "extractive"


def summarize_agenda_items(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize agenda items using the configured backend."""
    backend = get_backend()
    if backend == "local":
        return _summarize_local(agenda_items, meeting_title)
    else:
        return _summarize_extractive(agenda_items, meeting_title)


def extract_meeting_topics(
    agenda_items: list[dict], meeting_title: str, max_topics: int = 5
) -> list[dict]:
    """Extract the most interesting non-procedural topics from a meeting.

    Runs the configured summarizer on the items, filters out procedural ones,
    ranks by a simple interest heuristic, and returns the top N.
    """
    items = summarize_agenda_items(agenda_items, meeting_title)

    substantive = [
        item for item in items
        if not _is_procedural(item.get("title", ""))
        and item.get("summary", "") != "Procedural item."
    ]

    scored = []
    for item in substantive:
        title = item.get("title", "")
        summary = item.get("summary", "")
        section = item.get("section_number", "")

        # Longer summaries suggest more substance
        summary_len_score = min(len(summary.split()) / 20.0, 1.0)

        # Titles with numbers/dollar amounts tend to be about specific decisions
        specificity_score = 0.3 if re.search(r'\$|%|\d{4,}', title) else 0.0

        # Mid-level sections (e.g. "7.1") are often more interesting than
        # top-level ("7") or deeply nested ("7.3.2.1")
        dot_count = section.count(".")
        depth_score = 0.3 if 1 <= dot_count <= 2 else 0.1

        # Penalise items where summary == title (no real content to extract)
        identity_penalty = 0.0 if summary.strip() == title.strip() else 0.3

        score = (
            summary_len_score * 0.35
            + specificity_score
            + depth_score
            + identity_penalty
        )
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_items = [item for _, item in scored[:max_topics]]

    # Return in original agenda order
    top_set = {id(item) for item in top_items}
    ordered = [item for item in substantive if id(item) in top_set]

    return [
        {
            "title": item.get("title", ""),
            "summary": _plainify(item.get("title", "")),
            "section_number": item.get("section_number", ""),
        }
        for item in ordered
    ]


# ---------------------------------------------------------------------------
# Extractive backend (zero dependencies)
# ---------------------------------------------------------------------------

# Common procedural agenda item keywords
_PROCEDURAL_KEYWORDS = {
    "call to order", "adjournment", "roll call", "adoption of agenda",
    "confirmation of minutes", "declarations of conflict",
    "communications to council", "o canada",
}


def _summarize_extractive(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize items using extractive sentence scoring.

    For each item, scores sentences by word frequency, position, and
    overlap with the title, then picks the top sentences as a summary.
    """
    for item in agenda_items:
        title = item.get("title", "")
        content = item.get("content", "")

        # Check if procedural
        if _is_procedural(title):
            item["summary"] = "Procedural item."
            continue

        text = f"{title}. {content}".strip() if content else title
        item["summary"] = _extract_summary(text, title, max_sentences=2)

    return agenda_items


def _is_procedural(title: str) -> bool:
    title_lower = title.lower().strip()
    return any(kw in title_lower for kw in _PROCEDURAL_KEYWORDS)


# Patterns stripped from titles to produce plain-language summaries
_PLAIN_REPLACEMENTS = [
    # "Bylaw No. 9876 - The Foo Bylaw, 2025 (No. 3)" → "Foo"
    (re.compile(r'^Bylaw\s+No\.\s*\d+\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\bBylaw\b,?\s*', re.IGNORECASE), ''),
    (re.compile(r'\(No\.\s*\d+\)', re.IGNORECASE), ''),
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
    # "Councillor X - Notice of Motion - Topic" → "Topic"
    (re.compile(r'^Councillor\s+\S+(?:\s+\S+)?\s*[-–—]\s*Notice\s+of\s+Motion\s*[-–—]\s*', re.IGNORECASE), ''),
    # "Report of the City Clerk - Foo" → "Foo"
    (re.compile(r'^Report\s+of\s+the\s+\w[\w\s]{0,30}?[-–—]\s*', re.IGNORECASE), ''),
    # Strip leading "The " after other cleanup
    (re.compile(r'^The\s+', re.IGNORECASE), ''),
    # Strip year suffixes like ", 2025" or standalone " 2025" at end
    (re.compile(r'[,\s]+\d{4}\s*$'), ''),
    # Collapse extra whitespace / dashes
    (re.compile(r'\s*[-–—]\s*$'), ''),
    (re.compile(r'\s{2,}'), ' '),
]


def _plainify(text: str) -> str:
    """Convert a bureaucratic agenda title into plain language."""
    result = text.strip()
    for pattern, repl in _PLAIN_REPLACEMENTS:
        result = pattern.sub(repl, result)
    result = result.strip(' -–—,.')
    # Ensure first letter is capitalised after stripping
    if result:
        result = result[0].upper() + result[1:]
    return result or text.strip()


def _extract_summary(text: str, title: str, max_sentences: int = 2) -> str:
    """Score and select the best sentences from the text."""
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    # Build word frequency from the full text
    words = _tokenize(text)
    word_freq = Counter(words)
    # Normalize by max frequency
    max_freq = max(word_freq.values()) if word_freq else 1

    title_words = set(_tokenize(title))

    scored = []
    for i, sentence in enumerate(sentences):
        sent_words = _tokenize(sentence)
        if not sent_words:
            continue

        # Frequency score: average normalized frequency of words in sentence
        freq_score = sum(word_freq[w] / max_freq for w in sent_words) / len(sent_words)

        # Position score: earlier sentences get higher scores
        position_score = 1.0 / (1.0 + math.log1p(i))

        # Title overlap: fraction of title words present in sentence
        if title_words:
            overlap_score = len(title_words & set(sent_words)) / len(title_words)
        else:
            overlap_score = 0.0

        # Length penalty: prefer sentences that aren't too short or too long
        length = len(sent_words)
        length_score = 1.0 if 8 <= length <= 30 else 0.5

        score = (freq_score * 0.3) + (position_score * 0.3) + (overlap_score * 0.25) + (length_score * 0.15)
        scored.append((score, i, sentence))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Pick top sentences, but return them in original order
    selected = sorted(scored[:max_sentences], key=lambda x: x[1])
    return " ".join(s[2] for s in selected)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using basic rules."""
    # Split on period, exclamation, question mark followed by space or end
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in parts if s.strip() and len(s.strip()) > 10]


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words, filtering stop words."""
    words = re.findall(r'[a-z]+', text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


_STOP_WORDS = {
    "the", "and", "for", "that", "this", "with", "are", "was", "were",
    "been", "have", "has", "had", "not", "but", "from", "they", "which",
    "their", "will", "would", "could", "should", "can", "may", "its",
    "than", "other", "into", "all", "also", "any", "each", "our",
    "about", "more", "some", "such", "when", "what", "there", "these",
    "those", "then", "how", "who", "where", "being", "does", "did",
    "very", "just", "over", "after", "before",
}


# ---------------------------------------------------------------------------
# Local Transformers backend
# ---------------------------------------------------------------------------

_local_pipeline = None


def _get_local_pipeline():
    global _local_pipeline
    if _local_pipeline is None:
        from transformers import pipeline
        model_name = os.environ.get(
            "SUMMARIZER_MODEL", "sshleifer/distilbart-cnn-12-6"
        )
        _local_pipeline = pipeline("summarization", model=model_name)
    return _local_pipeline


def _summarize_local(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize items using a local Hugging Face Transformers model."""
    pipe = _get_local_pipeline()

    for item in agenda_items:
        title = item.get("title", "")
        content = item.get("content", "")

        if _is_procedural(title):
            item["summary"] = "Procedural item."
            continue

        text = f"{title}. {content}".strip() if content else title

        # Model needs reasonable input length
        if len(text.split()) < 15:
            item["summary"] = text
            continue

        # Truncate to model max input (~1024 tokens for distilbart)
        truncated = " ".join(text.split()[:900])

        try:
            result = pipe(
                truncated,
                max_length=80,
                min_length=20,
                do_sample=False,
            )
            item["summary"] = result[0]["summary_text"]
        except Exception:
            item["summary"] = title

    return agenda_items

