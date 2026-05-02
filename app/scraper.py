"""Back-compat shim for the legacy ``app.scraper`` module.

The eSCRIBE seam now lives in :mod:`app.escribe` (transport + parsers +
``EscribeMeetingSource``).  This module preserves the public surface
(``fetch_past_meetings``, ``fetch_meeting_detail``, ``fetch_post_minutes``,
``fetch_meeting_votes``) until the Flask app and scripts are migrated.
It is deleted in U7.

It also re-exports the private parser helpers so that
``tests/test_scraper.py`` keeps importing them from here until that file
is renamed to ``tests/test_escribe.py`` in U7.
"""

from app.models import AgendaItem, Meeting, MeetingDetail  # noqa: F401
from app.meeting_types import MEETING_TABS, MEETING_TYPE, _SLUG_TO_TYPE  # noqa: F401
from app.escribe import (  # noqa: F401
    BASE_URL,
    EscribeMeetingSource,
    LiveEscribeTransport,
    _build_video_url,
    _clean_html,
    _distribute_confirmation_attachments,
    _extract_agenda_items,
    _extract_attachments,
    _extract_bookmarks,
    _extract_descriptions,
    _extract_minutes,
    _extract_recommendations,
    _extract_votes,
    _insert_recesses,
    _item_blocks,
    _mark_brief_items,
    _normalize_name,
    _parse_escribemeetings_date,
    _propagate_timestamps,
    _tokenize_for_match,
    MIN_DISCUSSION_MS,
    MIN_RECESS_MS,
)

_default_transport = LiveEscribeTransport()
_default_source = EscribeMeetingSource(_default_transport)


def fetch_past_meetings(page: int = 1, meeting_type: str | None = None) -> tuple[list[Meeting], int]:
    """Fetch a page of past meetings from eSCRIBE."""
    return _default_source.list_past(page=page, meeting_type=meeting_type)


def fetch_meeting_detail(meeting_id: str, include_votes: bool = False) -> dict:
    """Fetch the full meeting detail.

    The ``include_votes`` flag is retained for back-compat but ignored —
    the source always fetches PostMinutes (best-effort).  Returns a dict
    with ``agenda_items`` (list of :class:`AgendaItem`) and ``video_url``
    matching the pre-refactor shape.
    """
    detail = _default_source.load_detail(meeting_id)
    return {
        "agenda_items": detail.agenda_items,
        "video_url": detail.video_url,
    }


def fetch_post_minutes(meeting_id: str) -> dict:
    """Fetch the PostMinutes page and extract votes + meeting minutes.

    Silent on failure — preserves legacy behavior.  The
    ``EscribeMeetingSource`` owns the equivalent policy for ``load_detail``.
    """
    try:
        html = _default_transport.fetch_postminutes_html(meeting_id)
    except Exception:
        return {"votes": {}, "minutes": {}}
    return {
        "votes": _extract_votes(html),
        "minutes": _extract_minutes(html),
    }


def fetch_meeting_votes(meeting_id: str) -> dict[int, dict]:
    """Fetch PostMinutes page and extract vote results per item."""
    return fetch_post_minutes(meeting_id)["votes"]
