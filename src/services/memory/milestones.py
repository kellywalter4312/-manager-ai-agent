"""Personal milestones - structured, extensible, never a per-type branch.

A milestone is not a new kind of store. It is a ``MemoryRecord`` under
its own category (``MILESTONE``), with its structured fields carried in
``context`` - the same opaque, caller-defined shape ``user_facts.py`` and
``capture.py`` already use for the same reason: ``Memory`` itself must
stay domain-agnostic, so structure belongs beside the record, never
inside the contract.

**Extensible by construction.** ``milestone_type`` is whatever phrase the
person actually used ("first paying client", "graduation", "project
launch"), slugified into a stable key - never matched against a fixed
vocabulary. A birthday and a first SaaS customer are both just a type
string; a genuinely new kind of achievement needs no new code here, the
same claim ``GeneralCapability``'s own docstring already makes about
subjects.

**Recognition is generic and deliberately cautious.** "I just got my
first paying client" is recognised as an achievement *shape* - "I just
<verb> my first/a <noun phrase>" - never as a specific fact about
clients or revenue. And recognising the shape is not the same as
storing it: see ``milestone_confirmation`` below for why every
recognised candidate still passes through an explicit governance step
before anything is written, exactly as the brief asks - "do not
automatically turn every conversational statement into a milestone".
"""

from __future__ import annotations

import re
from datetime import date
from typing import Callable, Iterable

from src.providers.celebration import INTENSITY_LEVELS
from src.services.memory.memory import MemoryRecord
from src.services.memory.temporal_facts import utc_today

MILESTONE_KIND = "milestone"

# The achievement *shape* - "I just <verb> my/a/the (first) <noun
# phrase>" - never a list of achievements. "got", "achieved", "landed",
# "reached", "launched", "completed", "hit", "earned", "graduated" are
# verbs of accomplishment in general, not any one domain's vocabulary.
_ACHIEVEMENT = re.compile(
    r"\bi (?:just )?(?P<verb>got|achieved|landed|reached|launched|completed|hit|earned|graduated)\s+"
    r"(?:my |a |the )?(?P<first>first\s+)?(?P<rest>[^.!?]+)",
    re.IGNORECASE,
)
# Closure campaign (2026-09-16, N-09): "I graduated today!" matched
# _ACHIEVEMENT (the verb "graduated" is in the list), but with nothing
# but a bare time word after it, group(rest) captured "today" - so the
# stored milestone_type became "today" instead of "graduated", silently
# wrong rather than honestly refused. This is a real bug distinct from
# the verb list's own deliberate, documented limits (see this module's
# own docstring on why the verb set stays finite): once the achievement
# *shape* is genuinely recognised, its type must not be a meaningless
# adverb. These bare completions fall back to the verb itself.
_BARE_TIME_WORDS = frozenset({
    "today", "yesterday", "now", "recently", "finally", "just", "already",
})
# The single explicit, always-store trigger - the same "an explicit ask
# is never ambiguous" rule "remember that X" and "record this for X"
# already live by. A bare achievement statement below this is the
# ambiguous case that goes through wants_auto_confirm instead.
_EXPLICIT_MILESTONE_MARKER = re.compile(r"\bmilestone:\s*([^.!?]+)", re.IGNORECASE)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    cleaned = _SLUG_STRIP.sub("_", text.strip().lower()).strip("_")
    return cleaned or "achievement"


def milestone_intent(text: str) -> bool:
    """Whether this text names a recognisable achievement - the
    ``claims=`` predicate for the milestone capability."""
    stripped = str(text or "")
    return bool(_ACHIEVEMENT.search(stripped) or _EXPLICIT_MILESTONE_MARKER.search(stripped))


def is_explicit(text: str) -> bool:
    """Whether this text is the single explicit, always-store trigger
    ("milestone: ...") rather than a bare achievement statement that
    still needs ``wants_auto_confirm`` to decide what happens."""
    return bool(_EXPLICIT_MILESTONE_MARKER.search(str(text or "")))


def extract_milestone(text: str) -> tuple[str, str] | None:
    """``(milestone_type, description)`` for a recognised achievement, or
    None. ``milestone_type`` is derived entirely from what was said -
    never a fixed vocabulary."""
    stripped = str(text or "")

    marker_match = _EXPLICIT_MILESTONE_MARKER.search(stripped)
    if marker_match is not None:
        description = marker_match.group(1).strip()
        if description:
            return _slug(description), description

    match = _ACHIEVEMENT.search(stripped)
    if match is None:
        return None
    is_first = bool(match.group("first"))
    noun_phrase = match.group("rest").strip()
    if not noun_phrase:
        return None
    # A bare time word ("today", "already"...) is not what was achieved -
    # the verb itself is the only real content of the sentence in that
    # case, so it becomes the type instead of a meaningless adverb.
    if noun_phrase.rstrip(" !").lower() in _BARE_TIME_WORDS:
        noun_phrase = match.group("verb")
        is_first = False
    milestone_type = _slug(("first " if is_first else "") + noun_phrase)
    description = stripped.strip()
    return milestone_type, description


def build_milestone_record(
    milestone_type: str,
    description: str,
    *,
    agent_name: str,
    when: date | None = None,
    importance: str = "normal",
    notes: str = "",
    proactive: str = "allow",
) -> MemoryRecord:
    """One structured milestone record.

    ``proactive`` is a plain preference string ("allow", "snooze",
    "silent") carried in context - see ``proactive_status`` below - never
    a new subsystem: it is read the same way any other context value
    already is, by whatever later asks whether to mention it.
    """
    occurred = when if when is not None else utc_today()
    return MemoryRecord(
        content=description,
        kind=MILESTONE_KIND,
        tags={"milestone", milestone_type},
        context={
            "milestone_type": milestone_type,
            "date": occurred.isoformat(),
            "importance": importance,
            "notes": notes,
            "source": "conversation",
            "recognized_by": agent_name,
            "proactive": proactive,
        },
    )


# ---------------------------------------------------------------------------
# Confirm-or-store governance
# ---------------------------------------------------------------------------

AUTO_CONFIRM_FACT_TYPE = "milestone_auto_confirm"


def wants_auto_confirm(preference_records: tuple[MemoryRecord, ...]) -> bool:
    """Whether the user has established a preference to store a
    recognised milestone immediately, rather than being asked first.

    Read the same way any other durable preference already is: a plain
    ``MemoryRecord`` under the user's own governed store, found by its
    ``fact_type`` tag - no new preference subsystem, the existing one
    reused for a new kind of fact.
    """
    for record in preference_records:
        context = record.context or {}
        if context.get("fact_type") == AUTO_CONFIRM_FACT_TYPE:
            # A structured boolean, never parsed back out of prose - the
            # same "context carries structured detail" contract every
            # other fact in this module already relies on.
            return bool(context.get("enabled", False))
    return False


# ---------------------------------------------------------------------------
# Proactive recognition preferences (birthdays, anniversaries)
# ---------------------------------------------------------------------------


def proactive_status(record: MemoryRecord) -> str:
    """This milestone's own stated proactive preference - "allow",
    "silent" (never mention), or "snooze" (not right now). Read from the
    record's own context, defaulting to "allow" for a record that
    predates this field or never set one, which is the least surprising
    default: a milestone worth recording is, by default, worth
    mentioning."""
    return str((record.context or {}).get("proactive", "allow")).strip().lower() or "allow"


def is_mentionable(record: MemoryRecord) -> bool:
    """Whether governed, user-controlled preference currently allows
    proactively mentioning this milestone at all."""
    return proactive_status(record) not in ("silent", "snooze")


# ---------------------------------------------------------------------------
# Celebration intensity - the "Off / Subtle / Full" preference, and
# reduced motion, resolved once here so any caller with read access to
# an agent's Memory (a live capability handler, or Stage 12 closure's
# own heartbeat-driven proactive events) reaches the identical decision.
# ---------------------------------------------------------------------------

CELEBRATION_INTENSITY_FACT_TYPE = "celebration_intensity"
REDUCED_MOTION_FACT_TYPE = "reduced_motion"


def resolve_celebration_intensity(
    recall: "Callable[..., tuple[MemoryRecord, ...]] | None",
) -> str:
    """The intensity to actually use for a reaction - the user's stored
    preference, capped by reduced motion.

    ``recall`` is a bound, narrow read - the same shape
    ``CapabilityRequest.recall``/``ManagerAgent``'s own governed reads
    already are - never a Memory object itself. None (no memory reaches
    this call) is treated the same honest way every other optional
    collaborator in this project already is: the safe, full-intensity
    default, never an exception.

    Defaults to "full": celebration is on unless the user turns it down
    or off, the same "the honest default states the fact plainly"
    stance ``proactive_status``'s own "allow" default already takes.
    Reduced motion never *disables* celebration outright (that is what
    the explicit "off" preference is for) - it caps intensity at
    "subtle".
    """
    level = "full"
    if recall is not None:
        for record in recall(
            kind="user_context", tags={CELEBRATION_INTENSITY_FACT_TYPE}, limit=1
        ):
            stored = (record.context or {}).get("level")
            if stored in INTENSITY_LEVELS:
                level = stored
            break

    if level == "off":
        return "off"

    reduced_motion = False
    if recall is not None:
        for record in recall(
            kind="user_context", tags={REDUCED_MOTION_FACT_TYPE}, limit=1
        ):
            reduced_motion = bool((record.context or {}).get("enabled", False))
            break

    return "subtle" if reduced_motion else level


# ---------------------------------------------------------------------------
# Stage 12 closure: which dated records are due to be proactively
# mentioned today - the consumer proactive_status/is_mentionable never
# had (see src/manager/proactive_milestones.py for the governed caller
# that actually reaches an agent's real Memory with this).
# ---------------------------------------------------------------------------


def due_recurring_dates(
    records: Iterable[MemoryRecord], *, today: date
) -> tuple[MemoryRecord, ...]:
    """Which of these already-recalled, already-governed records name a
    date whose month and day match ``today`` - a birthday, or the
    anniversary of any other dated fact or milestone - filtered by each
    record's own stored proactive preference.

    **Generic over what is recurring, never birthday-specific.** A
    stored ``date_of_birth`` fact (``user_facts.py``) and a stored
    milestone's own occurrence date (``build_milestone_record`` above)
    are read identically, through the same ``context["date"]`` field
    every dated record in this project already carries - there is no
    birthday-only code path, and a future recurring fact (an
    anniversary, a renewal date) needs none either. Governance is
    identical too: ``is_mentionable`` reads the same "silent"/"snooze"/
    "allow" preference this module already defines for milestones, and
    defaults to "allow" for a record - like a bare ``date_of_birth``
    fact - that never set one, the same honest default
    ``proactive_status`` already documents.

    **Never fabricates.** A record with no ``"date"`` in its context, or
    one that cannot be parsed, is silently skipped - never guessed at.

    **Excludes the year a fact was stated or a milestone happened.** A
    record whose own date falls in ``today``'s year or later is not
    "due" - it was either just stated (this year) or has not happened
    yet (a future date). Only a date from a strictly earlier year is a
    genuine anniversary; this is what stops a milestone recorded this
    afternoon from being "proactively" surfaced again this same year.
    """
    due: list[MemoryRecord] = []
    for record in records:
        context = record.context or {}
        raw_date = context.get("date")
        if not raw_date:
            continue
        try:
            occurred = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        if occurred.year >= today.year:
            continue
        if (occurred.month, occurred.day) != (today.month, today.day):
            continue
        if not is_mentionable(record):
            continue
        due.append(record)
    return tuple(due)
