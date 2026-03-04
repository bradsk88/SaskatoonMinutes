"""
Summarizer module supporting multiple backends.

Backends (set via SUMMARIZER_BACKEND env var):
  - "extractive" (default) — Zero-dependency extractive summarization using
    sentence scoring. No cloud calls, no ML frameworks needed.
  - "local" — Local abstractive summarization using Hugging Face Transformers.
    Requires: pip install transformers torch
"""

import html as html_mod
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
    agenda_items: list[dict], meeting_title: str, max_topics: int = 8
) -> list[dict]:
    """Extract the most interesting non-procedural topics from a meeting.

    Filters out procedural items, ranks by an interest heuristic that
    prioritises contested votes, dollar amounts, and substantive
    recommendations, then returns the top *max_topics* as topic/outcome pairs.
    """
    substantive = [
        item for item in agenda_items
        if not _is_procedural(item.get("title", ""))
    ]

    scored = []
    for item in substantive:
        title = item.get("title", "")
        rec = item.get("recommendation", "")
        vote = item.get("vote_result", "")
        section = item.get("section_number", "")
        contested = item.get("is_contested", False)

        # Contested votes are always interesting
        contested_score = 0.5 if contested else 0.0

        # Dollar amounts or percentages in rec/title
        has_money = bool(re.search(r'\$[\d,.]+', rec + " " + title))
        money_score = 0.3 if has_money else 0.0

        # Items with explicit vote results
        vote_score = 0.2 if vote else 0.0

        # Items with recommendations (not just section headers)
        rec_score = 0.2 if rec else 0.0

        # Mid-level sections more interesting than top-level or deeply nested
        dot_count = section.count(".")
        depth_score = 0.15 if 1 <= dot_count <= 2 else 0.05

        # Penalise generic committee headers and appointment sub-items
        title_lower = title.lower()
        if "standing policy committee" in title_lower:
            depth_score -= 0.2
        if "appointments" in title_lower and dot_count >= 3:
            depth_score -= 0.1

        score = contested_score + money_score + vote_score + rec_score + depth_score
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_items = [item for _, item in scored[:max_topics]]

    # Return in original agenda order
    top_set = {id(item) for item in top_items}
    ordered = [item for item in substantive if id(item) in top_set]

    return [_format_topic(item) for item in ordered]


def _format_topic(item: dict) -> dict:
    """Format a single agenda item as a topic/outcome pair for the index page."""
    title = item.get("title", "")
    rec = item.get("recommendation", "")
    vote = item.get("vote_result", "")
    contested = item.get("is_contested", False)

    outcome = _format_outcome(vote, rec)
    is_major = _is_major_decision(title, rec, contested)

    return {
        "topic": _plainify(title),
        "outcome": outcome,
        "outcome_detail": _clean_entities(rec) if rec else "",
        "vote_result": vote,
        "is_major": is_major,
        "is_contested": contested,
        "badges": _extract_badges(item),
    }


def _extract_badges(item: dict) -> list[dict]:
    """Extract contextual badges (money, people, locations) from an agenda item."""
    badges = []
    title = item.get("title", "")
    rec = item.get("recommendation", "")

    # Dollar amounts with surrounding context for a verb and purpose
    combined = title + " " + rec
    money_count = 0
    for m in re.finditer(r'\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?', combined):
        raw = m.group()
        verb = _money_verb(combined, m.start())
        purpose = _money_purpose(combined, m.end())
        formatted = _format_money(raw)
        if purpose:
            label = f"{formatted} \u2192 {purpose}"
        elif verb:
            label = f"{formatted} {verb}"
        else:
            label = formatted
        badges.append({"type": "money", "label": label})
        money_count += 1
        if money_count >= 4:
            break

    # Councillor names from title
    councillor_matches = re.findall(
        r'Councillor\s+([A-Z]\.?\s*[A-Za-z]+)', title
    )
    for name in councillor_matches[:2]:
        badges.append({"type": "person", "label": name.strip()})

    # Street addresses from title only (recommendation has too many false positives)
    addr_matches = re.findall(
        r'(\d+\s+(?:[A-Z][\w]+\s+)*?'
        r'(?:Street|Avenue|Drive|Road|Crescent|Boulevard|Place|Way|Circle)'
        r'(?:\s+(?:North|South|East|West))?)',
        title
    )
    for addr in addr_matches[:1]:
        badges.append({"type": "location", "label": addr.strip()})

    # Saskatoon neighbourhood names (only if no street address found)
    if not addr_matches:
        title_lower = title.lower()
        for hood in _SASKATOON_NEIGHBOURHOODS:
            if hood.lower() in title_lower:
                badges.append({"type": "location", "label": hood})
                break

    return badges


# Maps context keywords (found near a dollar amount) to short badge verbs.
_MONEY_CONTEXT = [
    # Multi-word / specific patterns first
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
    # Look at ~80 chars before and ~30 chars after the dollar sign
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
    # Look at text after the dollar amount, but only within the same clause
    window = text[match_end:match_end + 200]
    # Stop at clause boundaries (semicolons, periods, "and That")
    for sep in (';', '. ', ' and That', ' That '):
        cut = window.find(sep)
        if cut != -1:
            window = window[:cut]
    # Match "to the [X] Reserve/Fund/Plan/Program" or "for [X]"
    m = re.search(
        r'(?:to\s+the|for\s+the|for)\s+((?:[A-Z][\w]*\s*){1,5}?)'
        r'\s*(?:Reserve|Fund|Plan|Program|Account|Initiative)',
        window,
    )
    if m:
        words = m.group(1).strip().split()
        # Pick the most descriptive word (skip generic ones)
        skip = {'the', 'a', 'an', 'of', 'and', 'for', 'in', 'city',
                'neighbourhood', 'land', 'development', 'municipal'}
        meaningful = [w for w in words if w.lower() not in skip]
        if meaningful:
            # Return up to 2 words for brevity
            return ' '.join(meaningful[:2])
    return ""


def _format_money(raw: str) -> str:
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


def _format_outcome(vote_result: str, recommendation: str) -> str:
    """Convert raw vote result + recommendation into a short outcome label."""
    if not vote_result and not recommendation:
        return "Discussed"

    if not vote_result:
        return "Recommended"

    upper = vote_result.upper()
    # Extract vote counts like (7 to 4)
    counts = re.search(r"\((\d+)\s+to\s+(\d+)\)", vote_result)
    tally = f" ({counts.group(1)}-{counts.group(2)})" if counts else ""

    if "DEFEATED" in upper:
        return f"Defeated{tally}"
    if "UNANIMOUSLY" in upper:
        return "Approved"
    if "CARRIED" in upper:
        return f"Approved{tally}"
    return vote_result


def _is_major_decision(title: str, recommendation: str, is_contested: bool) -> bool:
    """Determine whether a decision warrants visual highlighting."""
    if is_contested:
        return True
    combined = title + " " + recommendation
    if re.search(r"\$[\d,.]+", combined):
        return True
    title_lower = title.lower()
    for keyword in ("bylaw", "budget", "acquisition", "contract", "tax"):
        if keyword in title_lower:
            return True
    return False


def _clean_entities(text: str) -> str:
    """Decode common HTML entities."""
    text = text.replace("&#58;", ":").replace("&#160;", " ")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Extractive backend (zero dependencies)
# ---------------------------------------------------------------------------

# Common procedural agenda item keywords
_PROCEDURAL_KEYWORDS = {
    "call to order", "adjournment", "roll call", "adoption of agenda",
    "confirmation of agenda", "confirmation of minutes", "adoption of minutes",
    "declarations of conflict", "communications to council", "o canada",
    "consent agenda", "public acknowledgments", "public acknowledgements",
    "question period", "inquiries", "in camera session", "urgent business",
    "committee reports (not on consent",
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


def _plainify(text: str) -> str:
    """Convert a bureaucratic agenda title into plain language."""
    result = html_mod.unescape(text.strip())
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
