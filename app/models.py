"""Domain types for transcripts and item summaries.

These are the typed shapes the cache layer (de)serializes around.  Keeping
segment-shape knowledge in ``Transcript`` means callers don't reach into
segment dicts, so changing the segment representation is a one-file edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
