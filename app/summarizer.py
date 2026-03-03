"""
Summarizer module supporting multiple backends.

Backends (set via SUMMARIZER_BACKEND env var):
  - "extractive" (default) — Zero-dependency extractive summarization using
    sentence scoring. No cloud calls, no ML frameworks needed.
  - "local" — Local abstractive summarization using Hugging Face Transformers.
    Requires: pip install transformers torch
  - "claude" — Cloud summarization via the Anthropic Claude API.
    Requires: pip install anthropic, and ANTHROPIC_API_KEY set.
"""

import os
import re
import math
from collections import Counter


def get_backend() -> str:
    """Determine which summarization backend to use."""
    backend = os.environ.get("SUMMARIZER_BACKEND", "").lower()
    if backend in ("extractive", "local", "claude"):
        return backend
    # Auto-detect: use claude if API key is set, otherwise extractive
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return "extractive"


def summarize_agenda_items(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize agenda items using the configured backend."""
    backend = get_backend()
    if backend == "claude":
        return _summarize_claude(agenda_items, meeting_title)
    elif backend == "local":
        return _summarize_local(agenda_items, meeting_title)
    else:
        return _summarize_extractive(agenda_items, meeting_title)


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


# ---------------------------------------------------------------------------
# Claude API backend
# ---------------------------------------------------------------------------

def _summarize_claude(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize items using the Anthropic Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        for item in agenda_items:
            item["summary"] = item.get("title", "No summary available")
        return agenda_items

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    items_text = _format_items_for_prompt(agenda_items)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"""You are summarizing agenda items from a Saskatoon City Council meeting: "{meeting_title}".

For each numbered agenda item below, write a 1-3 sentence plain-language summary that a regular citizen would understand. Focus on what was discussed or decided and why it matters to residents.

If an item is procedural (e.g. "Call to Order", "Adjournment"), just write "Procedural" as the summary.

Format your response as one summary per line, prefixed with the item number:
ITEM 1: summary here
ITEM 2: summary here
...

Agenda items:
{items_text}""",
            }
        ],
    )

    response_text = message.content[0].text
    summaries = _parse_summaries(response_text, len(agenda_items))

    for i, item in enumerate(agenda_items):
        item["summary"] = summaries.get(i + 1, item.get("title", ""))

    return agenda_items


def _format_items_for_prompt(items: list[dict]) -> str:
    parts = []
    for i, item in enumerate(items, 1):
        number = item.get("section_number", "")
        title = item.get("title", "Untitled")
        content = item.get("content", "")
        entry = f"ITEM {i} [{number}]: {title}"
        if content:
            truncated = content[:1500] + "..." if len(content) > 1500 else content
            entry += f"\n  Details: {truncated}"
        parts.append(entry)
    return "\n\n".join(parts)


def _parse_summaries(response: str, count: int) -> dict[int, str]:
    """Parse the numbered summaries from Claude's response."""
    summaries = {}
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for prefix_pattern in ["ITEM ", "Item "]:
            if line.startswith(prefix_pattern):
                rest = line[len(prefix_pattern):]
                parts = rest.split(":", 1)
                if len(parts) == 2:
                    try:
                        num = int(parts[0].strip().rstrip("."))
                        summaries[num] = parts[1].strip()
                    except ValueError:
                        pass
                break
    return summaries
