"""Domain types for meetings, transcripts, and item summaries.

These are the typed shapes the cache layer (de)serializes around.  Keeping
segment-shape knowledge in ``Transcript`` means callers don't reach into
segment dicts, so changing the segment representation is a one-file edit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

# Saskatchewan keeps central standard time all year and never moves for
# daylight saving, so one fixed offset is the whole rule -- no zone
# database, and no date on which the answer changes.
SASKATOON_TZ = timezone(timedelta(hours=-6))

# The City posts a recording soon after a meeting: 12 hours without
# one is the line. A no-video meeting settles in the feeds, and the
# site calls it not recorded on the same clock, so the two surfaces
# agree (ADR ``0027``).
RECORDING_GRACE_HOURS = 12


def meeting_start(date_iso: str, start_time: str) -> datetime | None:
    """The meeting's start as a Saskatchewan datetime, or ``None``.

    ``start_time`` is the 24-hour "HH:MM" the build normalizes from
    eSCRIBE. A missing or malformed time means midnight on the meeting
    date, so a meeting that has a date still settles 12 hours after
    that rather than never.
    """
    try:
        day = date.fromisoformat(date_iso or "")
    except ValueError:
        return None
    hour = minute = 0
    hhmm = (start_time or "").strip()
    if len(hhmm) == 5 and hhmm[2:3] == ":":
        try:
            hour, minute = int(hhmm[:2]), int(hhmm[3:5])
        except ValueError:
            pass
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            hour = minute = 0
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=SASKATOON_TZ
    )


def meeting_recording_state(has_video: bool, is_cancelled: bool,
                            start: datetime | None, now: datetime) -> str | None:
    """The state to show where the video would be, or ``None``.

    ``None`` says nothing: the meeting has a video, was cancelled, has
    not happened yet, or its start is unknown.  A cancelled meeting has
    no recording by definition, and a future start says nothing about
    the video.  Within the grace period (12 hours after the meeting's
    start) the state is ``"pending"`` (recording not yet available);
    at or past it, ``"not_recorded"``.
    """
    if has_video or is_cancelled or start is None:
        return None
    if start >= now:
        return None
    if (now - start) >= timedelta(hours=RECORDING_GRACE_HOURS):
        return "not_recorded"
    return "pending"


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
    # A recording that is up means the meeting has happened, so it is no
    # longer "future": the site drops it from the Future tab and lands it
    # on its body's past tab. The upstream MeetingPassed flag is not the
    # truth here (see build_site) — this flag is.
    has_video: bool = False

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
class ItemSegment:
    """One topic a long agenda item moved through, as a mini item summary.

    A long item — a budget debate, a public hearing — plays as a series
    of distinct topics, and each topic draws as a card of its own: title,
    the moment it began, description bullets and chips, the same aggregate
    an item earns, because a 15-minute topic holds as many facts as a
    typical item (ADR ``0029``).  ``start_ms`` is snapped to a real
    transcript segment start, so a deep link always lands on audio that
    is this topic's.

    ``chips`` keeps the three-state discipline the segment pass uses
    (``ItemSummary.segments`` at the item level): ``None`` is the
    on-disk shape before topics carried chips — or a pass that failed,
    which retries as never — and ``[]`` is an honest finding that the
    topic earned none.  Only the backfill cares about the difference;
    the page reads both the same.
    """

    title: str
    start_ms: int
    description: list[str] = field(default_factory=list)
    chips: list[Chip] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ItemSegment":
        # The pre-chips on-disk shape is {title, start_ms, takeaway}.
        # A takeaway loads as one bullet so the archive renders until
        # the backfill re-asks it, rather than reading as empty.
        raw_desc = data.get("description")
        if raw_desc is None and data.get("takeaway"):
            raw_desc = [data["takeaway"]]
        bullets = [
            b.strip() for b in (raw_desc or [])
            if isinstance(b, str) and b.strip()
        ]
        raw_chips = data.get("chips")
        return cls(
            title=data.get("title") or "",
            start_ms=int(data.get("start_ms") or 0),
            description=bullets,
            chips=(
                [Chip.from_dict(c) for c in raw_chips]
                if raw_chips is not None
                else None
            ),
        )

    def to_dict(self) -> dict:
        payload = {
            "title": self.title,
            "start_ms": self.start_ms,
            "description": list(self.description),
        }
        # Written whenever the chip pass has run: an empty list is the
        # "earned none" finding the backfill relies on, while None stays
        # absent so pre-chips entries keep their old shape until re-asked.
        if self.chips is not None:
            payload["chips"] = [c.to_dict() for c in self.chips]
        return payload


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
    # The topics a long item moved through, in the order the council did
    # (ADR ``0029``).  Three states, because the skip rule must tell
    # "never had the segment pass" apart from "had it, and the model
    # found nothing to split": the first is backfill work, the second is
    # done.  ``None`` (the key absent on disk) is never — or the last
    # attempt failed, which retries as never; ``[]`` is attempted and
    # empty; a populated list is the topics themselves.  Only an item
    # the gate admits earns the pass, so most entries stay ``None``.
    segments: list[ItemSegment] | None = None

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
            # Three states (ADR 0029): the key absent loads as None
            # (never had the segment pass, or the last attempt failed);
            # an explicit empty list is "attempted, nothing to split".
            segments=(
                None
                if data.get("segments") is None
                else [ItemSegment.from_dict(s) for s in data["segments"]]
            ),
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
        # Written whenever the item has had the segment pass: an empty
        # list is a real state ("the model read the span and found
        # nothing to split") that the skip rule relies on, while None
        # stays absent so ordinary short items never gain the key.
        if self.segments is not None:
            payload["segments"] = [s.to_dict() for s in self.segments]
        # Same reasoning as speakers: most entries are post-meeting, so
        # the flag is written only when it distinguishes this one.
        if self.provisional:
            payload["provisional"] = True
        return payload


def segments_fully_current(segs) -> bool:
    """A topic list the segment pass is done with (ADR 0029).

    ``None`` is never had the pass (or the last one failed, which
    retries as never).  An empty list is the model declining to split —
    done, nothing to chip.  A populated list is done only when every
    topic carries chips: a missing chips key is the pre-chips on-disk
    shape, or a pass that fell over, and it re-asks; an explicit list,
    even empty, is a finding.

    The walk's skip rule and the one-off backfill both call this, so
    both agree on what "done" means — the same contract as
    ``has_current_summaries``.
    """
    if segs is None:
        return False
    if not segs:
        return True
    return all(s.chips is not None for s in segs)


def has_current_summaries(cached: dict[str, ItemSummary] | None) -> bool:
    """True when the cached summaries include a real, post-meeting one.

    The summarize job's skip rule and the feeds' "has summaries" signal
    share it.  A provisional summary was written before the meeting,
    from official text alone, and is disposable: the flip to Meeting
    regenerates everything with the transcript (ADR ``0021``).  A feed
    entry settled on provisional coverage would say what the agenda
    promised, not what happened.

    ``any``, not ``all``: an *ineligible* item is stored as an empty
    summary with no description, so a meeting is judged by whether
    anything in it cleared the current bar.  A meeting summarized
    without a Gemini key has no descriptions anywhere and is correctly
    treated as not current: that run produced degraded output and should
    be redone.
    """
    if not cached:
        return False
    return any(
        not summary.is_legacy and not summary.provisional
        for summary in cached.values()
    )


@dataclass(frozen=True)
class AttachmentGist:
    """The "5 Ws" gist of one agenda-item attachment PDF.

    Written for a citizen skimming a Scheduled Meeting to decide whether
    they care enough to register to speak — one terse line per W, "—"
    where a W does not apply.  Always provisional: generated from the
    pre-meeting PDF, never revised, and discarded when the Scheduled
    Meeting flips to a Meeting.
    """

    what: str = "—"
    who: str = "—"
    when: str = "—"
    where: str = "—"
    why: str = "—"

    @classmethod
    def from_dict(cls, data: dict) -> "AttachmentGist":
        return cls(
            what=data.get("what") or "—",
            who=data.get("who") or "—",
            when=data.get("when") or "—",
            where=data.get("where") or "—",
            why=data.get("why") or "—",
        )

    def to_dict(self) -> dict:
        return {
            "what": self.what,
            "who": self.who,
            "when": self.when,
            "where": self.where,
            "why": self.why,
        }
