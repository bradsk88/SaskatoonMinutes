"""Domain types for meetings, transcripts, and item summaries.

These are the typed shapes the cache layer (de)serializes around.  Keeping
segment-shape knowledge in ``Transcript`` means callers don't reach into
segment dicts, so changing the segment representation is a one-file edit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


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
class MeetingDetail:
    """The full per-meeting payload: agenda items + the bookmarked video URL."""

    agenda_items: list[AgendaItem] = field(default_factory=list)
    video_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "agenda_items": [i.to_dict() for i in self.agenda_items],
            "video_url": self.video_url,
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
class ItemSummary:
    """One category-chip summary for a single agenda item."""

    category: str
    text: str

    @classmethod
    def from_dict(cls, data: dict) -> "ItemSummary":
        # Extra fields are dropped; only ``category`` and ``text`` are part
        # of the on-disk contract today.
        return cls(category=data["category"], text=data["text"])

    def to_dict(self) -> dict:
        return {"category": self.category, "text": self.text}
