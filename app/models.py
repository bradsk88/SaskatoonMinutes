"""Domain types for meetings, transcripts, and item summaries.

These are the typed shapes the cache layer (de)serializes around.  Keeping
segment-shape knowledge in ``Transcript`` means callers don't reach into
segment dicts, so changing the segment representation is a one-file edit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Speaker:
    """One member of the public who addressed council on an agenda item.

    Extracted deterministically from PostMinutes prose — council's minutes
    narrate each delegate in their own sentence ("Karen Kobussen, Saskatoon
    West Business Association, expressed concerns...") — the same
    regex-over-official-text approach as the other hard chips.  When the
    prose only mentions someone in passing (e.g. "along with Tammy
    MacFarlane"), a submitted Request-to-Speak attachment fills the gap.
    ``source`` records which so the UI can show a narrated speaker
    with more confidence than a bare RTS filing.

    Those two sources establish **who spoke**, and neither says **what
    they said** — the minutes give one narrated sentence and an RTS filing
    gives a filename.  ``said`` carries that, in bullets, read off the
    meeting transcript by the same Gemini pass that writes Descriptions.
    It is empty until a summarize run has reached the meeting, so a
    speaker always has a name and only sometimes has substance.
    """

    name: str
    organization: str = ""
    stance: str = ""  # "support" | "concern" | "" (informational)
    summary: str = ""
    source: str = "minutes"  # "minutes" | "registered"
    said: list[str] = field(default_factory=list)

    @property
    def has_substance(self) -> bool:
        """True when the transcript told us what this speaker argued.

        The index only ranks a speaker against the meeting's topics
        when this holds: a row carrying a name and a filename is not worth
        a major topic's place on the card.
        """
        return bool(self.said)

    @classmethod
    def from_dict(cls, data: dict) -> "Speaker":
        return cls(
            name=data.get("name") or "",
            organization=data.get("organization") or "",
            stance=data.get("stance") or "",
            summary=data.get("summary") or "",
            source=data.get("source") or "minutes",
            said=list(data.get("said") or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgendaItem:
    item_id: int
    title: str
    content: str
    section_number: str  # e.g. "4.1.2"
    time_start_ms: int | None = None
    time_end_ms: int | None = None
    recommendation: str = ""
    vote_result: str = ""
    vote_detail: str = ""
    is_contested: bool = False
    timestamp_inherited: bool = False
    is_recess: bool = False
    attachments: list = field(default_factory=list)
    speakers: list[Speaker] = field(default_factory=list)

    @property
    def time_start_formatted(self) -> str | None:
        if self.time_start_ms is None:
            return None
        total_seconds = self.time_start_ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time_start_formatted"] = self.time_start_formatted
        d["is_contested"] = self.is_contested
        d["timestamp_inherited"] = self.timestamp_inherited
        d["is_recess"] = self.is_recess
        return d


@dataclass
class Meeting:
    meeting_id: str
    title: str
    date: str  # ISO date string
    start_time: str
    location: str
    has_video: bool
    has_agenda: bool
    video_url: str | None = None
    is_cancelled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScheduledMeeting:
    """A Meeting that has not happened yet (see CONTEXT.md).

    Announced on the upstream calendar with a ``meeting_id`` already
    assigned, but no video, minutes, or votes.  Shown only on the
    Future tab.  ``body`` is the tab label for the meeting's body
    (e.g. "Transportation") — the Future tab mixes bodies, so each
    row has to name its own.
    """

    meeting_id: str
    title: str  # the body, e.g. "SPC-Transportation - Public", titleized
    body: str  # tab label, e.g. "Transportation"
    date: str  # ISO date string
    start_time: str  # upstream formatted start, e.g. "Tuesday, 4 August 2026 @ 2:00 PM"
    location: str
    has_agenda: bool

    @property
    def request_to_speak_deadline(self) -> str:
        """5:00 p.m. on the Monday of the meeting week, ISO date.

        The City's deadline for submissions about an item already on the
        agenda — unchanged even when the Monday is a holiday.
        """
        from datetime import date as _date, timedelta

        d = _date.fromisoformat(self.date)
        monday = d - timedelta(days=d.weekday())
        return monday.isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["request_to_speak_deadline"] = self.request_to_speak_deadline
        d["scheduled"] = True
        # Card renderer compatibility with Meeting.to_dict() output.
        d["has_video"] = False
        d["is_cancelled"] = False
        return d


@dataclass
class MeetingDetail:
    """The full per-meeting payload: agenda items, video URL, and identity.

    ``title``/``date``/``start_time`` are the meeting's own identity — the
    body that met and when.  They live here because a detail page has to
    be readable on its own: arriving from a search result or a bookmark,
    a reader has no card to tell them what they are looking at.

    They are empty when the upstream page does not carry them.  Empty
    means unknown and is rendered as such; the page does not fall back to
    naming a body that may not have met.
    """

    agenda_items: list[AgendaItem] = field(default_factory=list)
    video_url: str | None = None
    title: str = ""
    date: str = ""  # ISO date string
    start_time: str = ""  # 24-hour "HH:MM"

    def to_dict(self) -> dict:
        return {
            "agenda_items": [i.to_dict() for i in self.agenda_items],
            "video_url": self.video_url,
            "title": self.title,
            "date": self.date,
            "start_time": self.start_time,
        }


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str

    @classmethod
    def from_dict(cls, data: dict) -> "Segment":
        return cls(
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            text=data["text"],
        )

    def to_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }


@dataclass(frozen=True)
class Transcript:
    segments: list[Segment] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: list[dict]) -> "Transcript":
        return cls(segments=[Segment.from_dict(s) for s in data])

    def to_dict(self) -> list[dict]:
        return [s.to_dict() for s in self.segments]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def slice(self, start_s: float, end_s: float) -> "Transcript":
        """Return segments overlapping the half-open interval [start_s, end_s).

        Times are seconds; segments store milliseconds internally.
        """
        start_ms = int(start_s * 1000)
        end_ms = int(end_s * 1000)
        kept = [
            s for s in self.segments
            if s.start_ms < end_ms and s.end_ms > start_ms
        ]
        return Transcript(segments=kept)

    def slice_ms(self, start_ms: int, end_ms: int) -> "Transcript":
        """Millisecond variant of :meth:`slice` for callers that already work in ms."""
        kept = [
            s for s in self.segments
            if s.start_ms < end_ms and s.end_ms > start_ms
        ]
        return Transcript(segments=kept)


@dataclass(frozen=True)
class Chip:
    """One ``(category, text)`` fact about an agenda item."""

    category: str
    text: str

    @classmethod
    def from_dict(cls, data: dict) -> "Chip":
        # Extra fields (e.g. the model's ``usefulness`` rating) are dropped;
        # only ``category`` and ``text`` are part of the on-disk contract.
        return cls(category=data["category"], text=data["text"])

    def to_dict(self) -> dict:
        return {"category": self.category, "text": self.text}


def normalize_description(value) -> list[str] | None:
    """Coerce a stored or model-supplied description to bullets, or ``None``.

    A plain string is one bullet.  Descriptions were paragraphs until the
    bullet change and the archive still holds thousands of them on disk;
    they are not all regenerated at once, so the old shape has to keep
    loading rather than reading as a Legacy ItemSummary and putting an
    "older summary" apology under a perfectly good sentence.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    bullets = [b.strip() for b in value if isinstance(b, str) and b.strip()]
    return bullets or None


@dataclass(frozen=True)
class ItemSummary:
    """The summary of one agenda item: a Description plus its Chips.

    ``description`` is a plain-language explanation of what the item does,
    written for a busy resident.  It is a required field of the LLM
    response schema rather than a chip category the model may decline —
    a declinable description is what produced 2,567 title-echo summaries
    across the cached corpus.  See ``docs/adr/0003-item-summary-aggregate.md``.

    It is a **list of bullets**, one per distinct fact, because a card row
    is scanned rather than read: a paragraph makes the reader parse a
    sentence to find out whether the item concerns them.  One bullet is a
    valid Description — the count follows the facts, and a thin item gets
    one bullet rather than four padded ones.

    ``description`` is ``None`` only for a **Legacy ItemSummary**: one
    cached before the aggregate existed, or produced by a run with no
    Gemini key.  Both are degraded artifacts, and the UI marks them as
    such rather than presenting them as meeting the current bar.
    """

    description: list[str] | None
    chips: list[Chip] = field(default_factory=list)
    # True for a **provisional** summary: written before the meeting, from
    # official text alone, and disposable — the flip to Meeting regenerates
    # everything with the transcript (ADR ``0021``).  Absent from every
    # entry cached before Scheduled Meetings existed; loads as False.
    provisional: bool = False
    # What each guest speaker argued, keyed by the roster the agenda
    # yields deterministically.  Absent from every entry cached before
    # speakers existed, which loads as an empty list — the roster
    # roster still renders, just without substance.
    speakers: list[Speaker] = field(default_factory=list)

    @property
    def is_legacy(self) -> bool:
        """True when this summary carries no Description."""
        return self.description is None

    @classmethod
    def from_dict(cls, data: dict | list) -> "ItemSummary":
        # A bare list is the pre-aggregate on-disk shape, so old cache
        # entries load as Legacy rather than needing a migration.
        if isinstance(data, list):
            return cls(description=None, chips=[Chip.from_dict(c) for c in data])
        return cls(
            description=normalize_description(data.get("description")),
            chips=[Chip.from_dict(c) for c in data.get("chips") or []],
            speakers=[
                Speaker.from_dict(p) for p in data.get("speakers") or []
            ],
            provisional=bool(data.get("provisional")),
        )

    def to_dict(self) -> dict:
        payload = {
            "description": self.description,
            "chips": [c.to_dict() for c in self.chips],
        }
        # Written only when there is one.  Six items in seven have no
        # speaker, so an always-present empty list would add the key to
        # all 16,210 cached items and rewrite every file on the branch to
        # record that nothing happened.
        if self.speakers:
            payload["speakers"] = [p.to_dict() for p in self.speakers]
        # Same reasoning as speakers: most entries are post-meeting, so
        # the flag is written only when it distinguishes this one.
        if self.provisional:
            payload["provisional"] = True
        return payload
