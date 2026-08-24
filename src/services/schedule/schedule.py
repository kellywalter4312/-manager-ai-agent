"""The user's schedule: when the person Alfred serves is around.

The fourth document in the set, and the first that is about *time*:

    IDENTITY.md   who the Manager is        -> services/identity/identity.py
    SOUL.md       how the Manager behaves   -> services/identity/soul.py
    USER.md       who the agent serves      -> services/profile/
    SCHEDULE.md   when they are around      -> here

**Why this is not the Calendar.** ``services/memory/day.py`` decides which
human day a stored memory fell on. That is arithmetic over a timestamp, and
its own docstring forbids it becoming anything more. "When is this person
free" is a different question entirely - it is a fact the user wrote down, not
something derivable from a clock - and answering it there would turn a read
lens over memory into a model of a person. Two questions, two homes.

**Why this is not the profile.** USER.md says what the user can do; this says
when they are around. Both are user context, both belong to the same person,
and they are kept apart because one changes when someone learns something and
the other changes when someone takes a holiday.

**What it deliberately cannot do.**

  - No time arithmetic. There is no ``is_quiet_now()``, because answering that
    needs a clock, and a clock is exactly what day.py owns. A schedule that
    started resolving instants would be a second temporal system.
  - No timer, no scheduler, no cron. This project schedules nothing, and a
    document describing a person's week must never become the thing that
    starts a run.
  - No authority. There is no field here in which a capability, permission,
    tool, specialist or brain mode could be expressed. When someone is free is
    a fact about them, never a right granted to the software.

A missing SCHEDULE.md is not an error. It means no schedule is configured, and
Alfred says so rather than assuming one.
"""

from __future__ import annotations

from dataclasses import dataclass


class ScheduleError(Exception):
    """Raised when a schedule document exists but cannot be read.

    Missing is fine and yields an unconfigured schedule; unreadable is not. A
    document that is present is meant to take effect, and quietly ignoring one
    would leave Alfred reasoning about a week nobody described.
    """


@dataclass(frozen=True)
class UserSchedule:
    """When the user is around, as they wrote it down.

    Every field holds the document's own lines, trimmed and nothing more.
    Times stay as text on purpose: turning "09:00-17:00" into instants needs a
    timezone and a clock, and both belong to the Calendar. What Alfred needs
    at this stage is to know what the person said about their week, which is
    exactly what is stored.

    Note the fields that are absent: no capability, permission, tool,
    specialist, scope or brain mode. There is nowhere here to put authority.
    """

    working_hours: tuple[str, ...] = ()
    quiet_hours: tuple[str, ...] = ()
    commitments: tuple[str, ...] = ()
    # False when no document was found. Distinct from "found but empty", which
    # is a document the user wrote and left blank - worth telling apart,
    # because one is an absence and the other is a statement.
    configured: bool = False

    @property
    def is_empty(self) -> bool:
        """Whether anything was actually stated."""
        return not (self.working_hours or self.quiet_hours or self.commitments)

    def describe(self) -> str:
        """A short line safe for logs and payloads.

        Counts only, for the same reason UserProfile.describe() and
        Recognition.describe() carry counts: a schedule says where somebody is
        on a Tuesday, and that is theirs rather than the system's to display.
        """
        if not self.configured:
            return "no schedule configured"
        if self.is_empty:
            return "schedule configured but empty"
        return (
            f"{len(self.working_hours)} working-hours line(s), "
            f"{len(self.quiet_hours)} quiet-hours line(s), "
            f"{len(self.commitments)} commitment(s)"
        )

    def notes(self) -> tuple[str, ...]:
        """Inert context for a brain, phrased as description rather than rule.

        "the user works ..." and never "do not contact the user before ...".
        A schedule tells Alfred about someone's week; it does not instruct him,
        and wording it as an instruction would be the first step towards a
        markdown file deciding what the system may do.

        Strings, and only strings. A brain receives no object it could act
        through.
        """
        if not self.configured:
            return (
                "no schedule is configured, so nothing is known about the user's week",
            )
        if self.is_empty:
            return ("a schedule is configured but states nothing",)

        notes: list[str] = []
        if self.working_hours:
            notes.append(f"the user works {'; '.join(self.working_hours)}")
        if self.quiet_hours:
            notes.append(
                f"the user is usually unavailable {'; '.join(self.quiet_hours)}"
            )
        if self.commitments:
            notes.append(f"the user has commitments: {'; '.join(self.commitments)}")
        return tuple(notes)
