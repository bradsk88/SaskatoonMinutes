"""
Summarizer module for extractive summarization of council agenda items.

Zero-dependency extractive summarization using sentence scoring.
No cloud calls, no ML frameworks needed.
"""

import re
import math
from collections import Counter

from app.agenda_items import (
    categorize_topic,
    format_outcome,
    is_major_decision,
    is_procedural,
)
from app.agenda_text import clean_entities, format_money, plainify
from app.transcript_text import split_sentences


def summarize_agenda_items(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize agenda items using extractive summarization."""
    return _summarize_extractive(agenda_items, meeting_title)


def extract_meeting_topics(
    agenda_items: list[dict], meeting_title: str, max_topics: int = 8
) -> list[dict]:
    """Extract the most interesting non-procedural topics from a meeting.

    Filters out procedural items, ranks by an interest heuristic that
    prioritises contested votes, dollar amounts, and substantive
    recommendations, then returns the top *max_topics* as topic/outcome pairs.
    """
    substantive = [
        item for item in agenda_items
        if not is_procedural(item.get("title", ""))
    ]

    scored = []
    for item in substantive:
        title = item.get("title", "")
        rec = item.get("recommendation", "")
        vote = item.get("vote_result", "")
        section = item.get("section_number", "")
        contested = item.get("is_contested", False)

        contested_score = 0.5 if contested else 0.0

        has_money = bool(re.search(r'\$[\d,.]+', rec + " " + title))
        money_score = 0.3 if has_money else 0.0

        vote_score = 0.2 if vote else 0.0

        rec_score = 0.2 if rec else 0.0

        dot_count = section.count(".")
        depth_score = 0.15 if 1 <= dot_count <= 2 else 0.05

        title_lower = title.lower()
        if "standing policy committee" in title_lower:
            depth_score -= 0.2
        if "appointments" in title_lower and dot_count >= 3:
            depth_score -= 0.1

        score = contested_score + money_score + vote_score + rec_score + depth_score
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_items = [item for _, item in scored[:max_topics]]

    top_set = {id(item) for item in top_items}
    ordered = [item for item in substantive if id(item) in top_set]

    return [_format_topic(item) for item in ordered]


def _format_topic(item: dict) -> dict:
    """Format a single agenda item as a topic/outcome pair for the index page."""
    title = item.get("title", "")
    rec = item.get("recommendation", "")
    vote = item.get("vote_result", "")
    contested = item.get("is_contested", False)

    outcome = format_outcome(vote, rec)
    is_major = is_major_decision(title, rec, contested)

    content = item.get("content", "")
    summary = ""
    if content:
        summary = clean_entities(content)
        if len(summary) > 120:
            summary = summary[:117].rsplit(" ", 1)[0] + "..."

    return {
        "topic": plainify(title),
        "outcome": outcome,
        "outcome_detail": clean_entities(rec) if rec else "",
        "summary": summary,
        "vote_result": vote,
        "is_major": is_major,
        "is_contested": contested,
        "is_consent": item.get("timestamp_inherited", False),
        "badges": _extract_badges(item),
        "time_start_ms": item.get("time_start_ms"),
    }


def _extract_badges(item: dict) -> list[dict]:
    """Extract contextual badges (category, money, people, locations) from an agenda item."""
    badges = []
    title = item.get("title", "")
    rec = item.get("recommendation", "")

    content = item.get("content", "")
    for cat in categorize_topic(title, rec, content):
        slug = cat.lower().replace(" & ", "-").replace(" ", "-")
        badges.append({"type": f"cat-{slug}", "label": cat})

    combined = title + " " + rec + " " + content
    money_count = 0
    for m in re.finditer(r'\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?', combined):
        raw = m.group()
        verb = _money_verb(combined, m.start())
        purpose = _money_purpose(combined, m.end())
        formatted = format_money(raw)
        if purpose:
            label = f"{formatted} → {purpose}"
        elif verb:
            label = f"{formatted} {verb}"
        else:
            label = formatted
        badges.append({"type": "money", "label": label})
        money_count += 1
        if money_count >= 4:
            break

    councillor_matches = re.findall(
        r'Councillor\s+([A-Z]\.?\s*[A-Za-z]+)', title
    )
    for name in councillor_matches[:2]:
        badges.append({"type": "person", "label": name.strip()})

    addr_matches = re.findall(
        r'(\d+\s+(?:[A-Z][\w]+\s+)*?'
        r'(?:Street|Avenue|Drive|Road|Crescent|Boulevard|Place|Way|Circle)'
        r'(?:\s+(?:North|South|East|West))?)',
        title
    )
    for addr in addr_matches[:1]:
        badges.append({"type": "location", "label": addr.strip()})

    if not addr_matches:
        title_lower = title.lower()
        for hood in _SASKATOON_NEIGHBOURHOODS:
            if hood.lower() in title_lower:
                badges.append({"type": "location", "label": hood})
                break

    for topic in _extract_discussion_topics(content):
        badges.append({"type": "topic", "label": topic})

    return badges


def _extract_discussion_topics(content: str) -> list[str]:
    """Extract key discussion topics from minutes text.

    Minutes typically follow the pattern "responded to questions … related to
    traffic volumes and demand, funding strategy, snow removal and winter
    operations, project timing and consideration of development in area."

    Returns up to 3 short topic labels.
    """
    if not content:
        return []

    m = re.search(
        r"(?:related to|regarding|concerning)\s+(.+?)(?:\.|$)",
        content, re.IGNORECASE,
    )
    if not m:
        return []

    raw = m.group(1).strip()

    parts = re.split(r",\s*", raw)

    topics: list[str] = []
    for part in parts:
        part = part.strip()
        part = re.sub(r"^and\s+", "", part, flags=re.IGNORECASE)
        if not part:
            continue
        label = part[0].upper() + part[1:] if len(part) > 1 else part.upper()
        if 3 <= len(label) <= 50:
            topics.append(label)
        if len(topics) >= 3:
            break

    return topics


def extract_badges(item: dict) -> list[dict]:
    """Extract contextual badges for a single agenda item (public API)."""
    return _extract_badges(item)


# Maps context keywords (found near a dollar amount) to short badge verbs.
_MONEY_CONTEXT = [
    (r'not.{0,10}exceed', 'max'),
    (r'award', 'awarded'),
    (r'approv', 'approved'),
    (r'budget', 'budgeted'),
    (r'allocat', 'allocated'),
    (r'fund(?:ing|ed)', 'funded'),
    (r'grant', 'granted'),
    (r'contract', 'contract'),
    (r'spend|expenditure|expend', 'spent'),
    (r'invest', 'invested'),
    (r'lev(?:y|ied)', 'levied'),
    (r'increas', 'increase'),
    (r'reduc|decreas|sav', 'savings'),
    (r'revenue', 'revenue'),
    (r'fee|charge', 'fee'),
    (r'salar|compensat|pay', 'salary'),
    (r'donat', 'donated'),
    (r'cost|estimat', 'estimated'),
]


def _money_verb(text: str, match_pos: int) -> str:
    """Find a contextual verb for a dollar amount by scanning nearby text."""
    start = max(0, match_pos - 80)
    end = min(len(text), match_pos + 30)
    window = text[start:end].lower()
    for pattern, verb in _MONEY_CONTEXT:
        if re.search(pattern, window):
            return verb
    return ""


def _money_purpose(text: str, match_end: int) -> str:
    """Extract a short purpose/destination label after a dollar amount.

    Looks for patterns like "allocated to the Affordable Housing Reserve"
    and returns a short label like "Housing".
    """
    window = text[match_end:match_end + 200]
    for sep in (';', '. ', ' and That', ' That '):
        cut = window.find(sep)
        if cut != -1:
            window = window[:cut]
    m = re.search(
        r'(?:to\s+the|for\s+the|for)\s+((?:[A-Z][\w]*\s*){1,5}?)'
        r'\s*(?:Reserve|Fund|Plan|Program|Account|Initiative)',
        window,
    )
    if m:
        words = m.group(1).strip().split()
        skip = {'the', 'a', 'an', 'of', 'and', 'for', 'in', 'city',
                'neighbourhood', 'land', 'development', 'municipal'}
        meaningful = [w for w in words if w.lower() not in skip]
        if meaningful:
            return ' '.join(meaningful[:2])
    return ""


_SASKATOON_NEIGHBOURHOODS = [
    "Nutana", "Riversdale", "Broadway", "Sutherland", "City Park",
    "Caswell Hill", "Westmount", "Fairhaven", "Haultain", "Varsity View",
    "Buena Vista", "Exhibition", "Confederation", "Lakeview",
    "Lawson Heights", "Silverspring", "Stonebridge", "Willowgrove",
    "Brighton", "Evergreen", "Rosewood", "Kensington", "Montgomery",
    "Dundonald", "Pleasant Hill", "King George", "Meadowgreen", "Mayfair",
    "Massey Place", "Pacific Heights", "Hampton Village", "Blairmore",
    "Holiday Park", "Forest Grove", "College Park", "Greystone Heights",
    "Kelsey-Woodlawn", "Adelaide", "Churchill", "Aspen Ridge",
    "Cumberland", "Downtown",
]


# ---------------------------------------------------------------------------
# Extractive backend (zero dependencies)
# ---------------------------------------------------------------------------


def _summarize_extractive(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize items using extractive sentence scoring.

    For each item, scores sentences by word frequency, position, and
    overlap with the title, then picks the top sentences as a summary.
    """
    for item in agenda_items:
        title = item.get("title", "")
        content = item.get("content", "")

        if is_procedural(title):
            item["summary"] = "Procedural item."
            continue

        text = f"{title}. {content}".strip() if content else title
        item["summary"] = _extract_summary(text, title, max_sentences=2)

    return agenda_items


def _extract_summary(text: str, title: str, max_sentences: int = 2) -> str:
    """Score and select the best sentences from the text."""
    sentences = split_sentences(text, min_len=11)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    words = _tokenize(text)
    word_freq = Counter(words)
    max_freq = max(word_freq.values()) if word_freq else 1

    title_words = set(_tokenize(title))

    scored = []
    for i, sentence in enumerate(sentences):
        sent_words = _tokenize(sentence)
        if not sent_words:
            continue

        freq_score = sum(word_freq[w] / max_freq for w in sent_words) / len(sent_words)

        position_score = 1.0 / (1.0 + math.log1p(i))

        if title_words:
            overlap_score = len(title_words & set(sent_words)) / len(title_words)
        else:
            overlap_score = 0.0

        length = len(sent_words)
        length_score = 1.0 if 8 <= length <= 30 else 0.5

        score = (freq_score * 0.3) + (position_score * 0.3) + (overlap_score * 0.25) + (length_score * 0.15)
        scored.append((score, i, sentence))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = sorted(scored[:max_sentences], key=lambda x: x[1])
    return " ".join(s[2] for s in selected)


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
