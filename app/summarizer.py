"""
Summarizer module using the Anthropic Claude API.

Takes agenda items from a council meeting and produces concise,
plain-language summaries for each topic.
"""

import os


def summarize_agenda_items(agenda_items: list[dict], meeting_title: str) -> list[dict]:
    """Summarize a list of agenda items using Claude.

    Each agenda item dict should have: title, section_number, content.
    Returns the same list with a 'summary' field added to each item.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Return items without summaries if no API key
        for item in agenda_items:
            item["summary"] = item.get("title", "No summary available")
        return agenda_items

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # Build a single prompt with all agenda items for efficiency
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
            # Truncate very long content
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
        # Match patterns like "ITEM 1:" or "ITEM 1."
        for prefix_pattern in ["ITEM ", "Item "]:
            if line.startswith(prefix_pattern):
                rest = line[len(prefix_pattern):]
                # Extract number and summary
                parts = rest.split(":", 1)
                if len(parts) == 2:
                    try:
                        num = int(parts[0].strip().rstrip("."))
                        summaries[num] = parts[1].strip()
                    except ValueError:
                        pass
                break
    return summaries
