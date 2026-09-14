"""Experience history - which real visual/animation experience was
selected for one genuinely due event, and whether it has actually
reached a live frontend yet.

**What this closes.** ``CelebrationProvider.select()`` already picks a
real ``CelebrationReaction`` (an avatar action, an environment action,
an overlay, an intensity, a duration) for a genuinely due event - but
nothing before this module remembered which one was actually chosen, so
a repeated occurrence of the same kind of event had no way to know what
had already been shown and could only vary by coincidence (a stable hash
of an ``occurrence`` counter, never guaranteed different). This module
is the missing memory: a real, governed, persisted record of what was
selected, for what, and whether a real frontend has genuinely presented
it - read back by the selector itself (see
``src/manager/proactive_experience.py``) to make variety a genuine
guarantee rather than a coincidence, and read back by Control Center/the
frontend to know what is pending and what has already been shown.

**One record per dedup_key, latest-wins - the identical mechanism
``personal_context.py``/``calendar_items.py`` already established and
proved.** ``MemoryRecord`` is frozen, so "this event's experience is now
delivered" is a new record sharing the same ``dedup_key`` tag, never an
edit in place; ``recall(tags={that key}, limit=1)`` always answers this
one occurrence's current state (selected-but-pending, or delivered)
honestly, with the full history still sitting behind it, never deleted.

**Not a duplicate store.** Every event this module describes an
experience *for* is a ``dedup_key`` computed by
``src/manager/proactive_milestones.py``'s own ``ProactiveEvent`` -
already governed, already deduplicated at the event-detection layer.
This module only ever answers "what was shown for that", never
re-detects or re-decides whether an event is due.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from src.services.memory.memory import MemoryRecord

EXPERIENCE_KIND = "experience"
EXPERIENCE_TAG = "experience"

_Recall = Callable[..., "Iterable[MemoryRecord]"]


def _event_tag(dedup_key: str) -> str:
    return "experience_event:" + "_".join(str(dedup_key).lower().split())


def _context_tag(agent_key: str, event_type: str) -> str:
    return "experience_context:" + "_".join(f"{agent_key}_{event_type}".lower().split())


@dataclass(frozen=True)
class ExperienceCandidate:
    """One experience, not yet written - the neutral shape the selector
    builds and offers to governed memory, the identical "candidates, not
    memories" stance every sibling module in this package already holds
    to."""

    event_type: str
    dedup_key: str
    agent_key: str
    reaction_id: str
    avatar_action: str = ""
    environment_action: str = ""
    overlay: str = ""
    intensity: str = "subtle"
    duration_ms: int = 0
    delivered: bool = False
    # What the experience says, when it says something ("call Mum") - a
    # reminder's own words. Empty for a purely visual celebration.
    message: str = ""

    def __post_init__(self) -> None:
        if not str(self.event_type).strip():
            raise ValueError("ExperienceCandidate.event_type must not be blank")
        if not str(self.dedup_key).strip():
            raise ValueError("ExperienceCandidate.dedup_key must not be blank")
        if not str(self.agent_key).strip():
            raise ValueError("ExperienceCandidate.agent_key must not be blank")
        if not str(self.reaction_id).strip():
            raise ValueError("ExperienceCandidate.reaction_id must not be blank")


@dataclass(frozen=True)
class ExperienceRecord:
    """One resolved, current experience, read back from Memory - the
    shape a caller (the selector, Control Center, the frontend route)
    actually wants, rather than a raw ``MemoryRecord`` whose ``context``
    dict it would otherwise have to unpack itself."""

    record_id: str
    event_type: str
    dedup_key: str
    agent_key: str
    reaction_id: str
    avatar_action: str
    environment_action: str
    overlay: str
    intensity: str
    duration_ms: int
    delivered: bool
    noted_by: str
    created_at: "object"
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload - the boundary format
        snapshot.py reads, mirroring every other view-shaped ``to_dict``
        in this project."""
        return {
            "record_id": self.record_id,
            "event_type": self.event_type,
            "dedup_key": self.dedup_key,
            "agent_key": self.agent_key,
            "reaction_id": self.reaction_id,
            "avatar_action": self.avatar_action,
            "environment_action": self.environment_action,
            "overlay": self.overlay,
            "intensity": self.intensity,
            "duration_ms": self.duration_ms,
            "delivered": self.delivered,
            "message": self.message,
        }


def build_experience_record(candidate: ExperienceCandidate, *, noted_by: str) -> MemoryRecord:
    """The MemoryRecord a governed write path retains for one candidate -
    constructed here, never written here, the identical boundary every
    sibling extractor in this package already crosses only through its
    caller."""
    return MemoryRecord(
        content=f"Experience for {candidate.event_type}: {candidate.reaction_id}",
        kind=EXPERIENCE_KIND,
        tags={
            EXPERIENCE_TAG,
            _event_tag(candidate.dedup_key),
            _context_tag(candidate.agent_key, candidate.event_type),
        },
        context={
            "event_type": candidate.event_type,
            "dedup_key": candidate.dedup_key,
            "agent_key": candidate.agent_key,
            "reaction_id": candidate.reaction_id,
            "avatar_action": candidate.avatar_action,
            "environment_action": candidate.environment_action,
            "overlay": candidate.overlay,
            "intensity": candidate.intensity,
            "duration_ms": candidate.duration_ms,
            "delivered": candidate.delivered,
            "message": candidate.message,
            "source": "proactive",
            "noted_by": noted_by,
        },
    )


def delivered_candidate(record: ExperienceRecord) -> ExperienceCandidate:
    """The same experience, marked delivered - the candidate a caller
    offers to the governed write path once a real frontend has actually
    presented it. A new record sharing the identical ``dedup_key``,
    never an edit of the one already stored."""
    return ExperienceCandidate(
        event_type=record.event_type,
        dedup_key=record.dedup_key,
        agent_key=record.agent_key,
        reaction_id=record.reaction_id,
        avatar_action=record.avatar_action,
        environment_action=record.environment_action,
        overlay=record.overlay,
        intensity=record.intensity,
        duration_ms=record.duration_ms,
        delivered=True,
        message=record.message,
    )


def _to_record(raw: MemoryRecord) -> "ExperienceRecord | None":
    ctx = raw.context or {}
    dedup_key = ctx.get("dedup_key")
    if not dedup_key:
        return None
    return ExperienceRecord(
        record_id=raw.id,
        event_type=str(ctx.get("event_type") or ""),
        dedup_key=str(dedup_key),
        agent_key=str(ctx.get("agent_key") or ""),
        reaction_id=str(ctx.get("reaction_id") or ""),
        avatar_action=str(ctx.get("avatar_action") or ""),
        environment_action=str(ctx.get("environment_action") or ""),
        overlay=str(ctx.get("overlay") or ""),
        intensity=str(ctx.get("intensity") or ""),
        duration_ms=int(ctx.get("duration_ms") or 0),
        delivered=bool(ctx.get("delivered", False)),
        noted_by=str(ctx.get("noted_by") or ""),
        created_at=raw.created_at,
        message=str(ctx.get("message") or ""),
    )


def current_experience_for_event(
    dedup_key: str, *, recall: "_Recall | None"
) -> "ExperienceRecord | None":
    """The single, latest state of the experience selected for one due-
    event occurrence - honestly None when nothing has ever been
    selected for it, or no ``recall`` reaches this call. Idempotency
    check for the selector: a non-None result here means "do not select
    again for this occurrence"."""
    if recall is None:
        return None
    for raw in recall(kind=EXPERIENCE_KIND, tags={_event_tag(dedup_key)}, limit=1):
        return _to_record(raw)
    return None


def experience_history_for(
    agent_key: str, event_type: str, *, recall: "_Recall | None", limit: int = 200
) -> "tuple[ExperienceRecord, ...]":
    """Every real, past occurrence's own current experience for this
    (agent, event type) pair, most recent first - each occurrence
    resolved to its own latest state (selected or delivered), never a
    raw, unresolved history dump. What the selector reads to compute a
    genuine occurrence count and to know which reaction was used most
    recently, so it can actively avoid repeating it."""
    if recall is None:
        return ()
    seen: set[str] = set()
    resolved: list[ExperienceRecord] = []
    for raw in recall(kind=EXPERIENCE_KIND, tags={_context_tag(agent_key, event_type)}, limit=limit):
        ctx = raw.context or {}
        dedup_key = str(ctx.get("dedup_key") or "")
        if not dedup_key or dedup_key in seen:
            continue
        seen.add(dedup_key)
        current = current_experience_for_event(dedup_key, recall=recall)
        if current is not None:
            resolved.append(current)
    return tuple(resolved)


def all_experiences(*, recall: "_Recall | None", limit: int = 200) -> "tuple[ExperienceRecord, ...]":
    """Every real occurrence's own current experience, across every
    agent and event type - each resolved to its own latest state, never
    a raw history dump. What Control Center/the snapshot reads to show
    genuine live state."""
    if recall is None:
        return ()
    seen: set[str] = set()
    resolved: list[ExperienceRecord] = []
    for raw in recall(kind=EXPERIENCE_KIND, tags={EXPERIENCE_TAG}, limit=limit):
        ctx = raw.context or {}
        dedup_key = str(ctx.get("dedup_key") or "")
        if not dedup_key or dedup_key in seen:
            continue
        seen.add(dedup_key)
        current = current_experience_for_event(dedup_key, recall=recall)
        if current is not None:
            resolved.append(current)
    return tuple(resolved)


def pending_experiences(*, recall: "_Recall | None", limit: int = 200) -> "tuple[ExperienceRecord, ...]":
    """Every currently-selected experience that has not yet reached a
    live frontend - what a served UI reads to know what to actually
    present, and what stays honestly pending when no session has opened
    since it was selected."""
    return tuple(e for e in all_experiences(recall=recall, limit=limit) if not e.delivered)
