"""Things that happened, and whether any of them is worth telling a person.

A system that speaks every time something happens is not attentive, it is
noisy - and a person learns to ignore it, which costs more than saying nothing
would have. So an event carries its own significance, and the difference
between the levels is the difference between a log line and an interruption:

    ROUTINE     it happened, and it is nobody's business. A memory written, a
                capability selected, a tick that found nothing.

    NOTABLE     worth finding when you look. A report written, a specialist
                finishing a piece of work, a heartbeat waking.

    IMPORTANT   worth being told without asking. A new capability becoming
                available, a tool going missing, a run failing, something
                found that the person was waiting for.

Only IMPORTANT is allowed to reach a person unprompted. That single rule is
what keeps proactive announcements from becoming notification spam, and it
lives on the event rather than in whatever is listening - so a new source of
events cannot accidentally become chatty by forgetting to check.

**Events carry facts, never sentences.** An event says what happened and what
was involved; it does not say how to phrase it. Phrasing belongs to the
narrator, and keeping it out of here is what stops the system becoming a
dictionary of things Alfred can say.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping


class Significance(str, Enum):
    """How far an event deserves to travel."""

    ROUTINE = "routine"
    NOTABLE = "notable"
    IMPORTANT = "important"

    @property
    def reaches_the_user(self) -> bool:
        """Whether this may be announced without being asked for."""
        return self is Significance.IMPORTANT

    @property
    def worth_recording(self) -> bool:
        """Whether this belongs in history at all."""
        return self is not Significance.ROUTINE


@dataclass(frozen=True)
class SystemEvent:
    """One thing that happened, described by its facts.

    ``facts`` is the payload a narrator phrases from - a capability name, a
    tool name, a count, an agent. Deliberately a mapping of real values rather
    than a prepared string, because the moment an event carries its own wording
    the system has a fixed vocabulary again.
    """

    kind: str
    actor: str
    significance: Significance = Significance.ROUTINE
    facts: Mapping[str, Any] = field(default_factory=dict)
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("SystemEvent.kind must not be empty")
        if not self.actor.strip():
            raise ValueError("SystemEvent.actor must not be empty")
        object.__setattr__(self, "facts", dict(self.facts))

    @property
    def announceable(self) -> bool:
        return self.significance.reaches_the_user

    def fact(self, name: str, default: Any = None) -> Any:
        return self.facts.get(name, default)

    def describe(self) -> str:
        """A flat line for a log. Not what a person hears - see the narrator."""
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.facts.items()))
        return f"[{self.significance.value}] {self.actor}: {self.kind}" + (
            f" ({detail})" if detail else ""
        )


class EventLog:
    """What has happened, in order, bounded.

    In memory and bounded on purpose. This is an observability aid, not a
    second history: the system already records what it retains in each agent's
    Memory and what it produced in its reports, and a log that grew without
    limit would become a third store nobody maintains.
    """

    def __init__(self, limit: int = 200) -> None:
        if limit <= 0:
            raise ValueError("EventLog limit must be positive")
        self._limit = limit
        self._events: list[SystemEvent] = []

    def record(self, event: SystemEvent) -> SystemEvent:
        """Append one event, discarding the oldest when full."""
        self._events.append(event)
        if len(self._events) > self._limit:
            del self._events[: len(self._events) - self._limit]
        return event

    def all(self) -> tuple[SystemEvent, ...]:
        return tuple(self._events)

    def since(self, moment: datetime) -> tuple[SystemEvent, ...]:
        return tuple(event for event in self._events if event.when > moment)

    def announceable(self) -> tuple[SystemEvent, ...]:
        """Only what may be said without being asked."""
        return tuple(event for event in self._events if event.announceable)

    def of_kind(self, kind: str) -> tuple[SystemEvent, ...]:
        return tuple(event for event in self._events if event.kind == kind)

    def by(self, actor: str) -> tuple[SystemEvent, ...]:
        return tuple(
            event for event in self._events if event.actor.lower() == actor.lower()
        )

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------------------
# Events the architecture can genuinely observe
# ---------------------------------------------------------------------------
#
# Each of these is built from something the system actually knows. There is no
# constructor here for an event the architecture cannot detect - a
# "capability learned" event that nothing could raise truthfully would be a
# fabrication with a helper function in front of it.


def capability_became_available(name: str, tool: str | None, actor: str) -> SystemEvent:
    """A capability the system can now genuinely perform.

    Important because it changes what a person can ask for. Note that this is
    about a capability becoming *performable* - a declared capability whose
    tool is still missing has not become available and must not raise this.
    """
    return SystemEvent(
        kind="capability.available",
        actor=actor,
        significance=Significance.IMPORTANT,
        facts={"capability": name, "tool": tool},
    )


def tool_became_unavailable(tool: str, actor: str, reason: str = "") -> SystemEvent:
    """Something the system could do, it can no longer do."""
    return SystemEvent(
        kind="tool.unavailable",
        actor=actor,
        significance=Significance.IMPORTANT,
        facts={"tool": tool, "reason": reason},
    )


def work_completed(actor: str, summary: str, findings: int = 0) -> SystemEvent:
    """A piece of work finished.

    Important only when it found something. Work that completed and turned up
    nothing is exactly the "quiet run" the heartbeat already declines to report
    on, and announcing it would undo that.
    """
    return SystemEvent(
        kind="work.completed",
        actor=actor,
        significance=(
            Significance.IMPORTANT if findings else Significance.NOTABLE
        ),
        facts={"summary": summary, "findings": findings},
    )


def delegation(manager: str, specialist: str, reason: str) -> SystemEvent:
    """The Manager handed work to a specialist. Ordinary, and worth finding."""
    return SystemEvent(
        kind="delegation",
        actor=manager,
        significance=Significance.NOTABLE,
        facts={"specialist": specialist, "reason": reason},
    )


def brain_mode_changed(actor: str, mode: str, reason: str) -> SystemEvent:
    """Reasoning became available, or stopped being.

    Important in both directions: losing a model changes what the system can
    do, and a person who is not told will read a thinner answer as a worse one.
    """
    return SystemEvent(
        kind="brain.mode",
        actor=actor,
        significance=Significance.IMPORTANT,
        facts={"mode": mode, "reason": reason},
    )


def run_failed(actor: str, error: str) -> SystemEvent:
    """Something that should have worked did not."""
    return SystemEvent(
        kind="run.failed",
        actor=actor,
        significance=Significance.IMPORTANT,
        facts={"error": error},
    )


def routine(kind: str, actor: str, **facts: Any) -> SystemEvent:
    """Anything the system does constantly and nobody needs told about."""
    return SystemEvent(
        kind=kind, actor=actor, significance=Significance.ROUTINE, facts=facts
    )


def announceable_only(events: Iterable[SystemEvent]) -> tuple[SystemEvent, ...]:
    """Filter to what may be said unprompted. One place, so it is one rule."""
    return tuple(event for event in events if event.announceable)
