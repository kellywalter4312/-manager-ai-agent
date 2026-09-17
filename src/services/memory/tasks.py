"""Tasks - an outstanding, actionable item with real state, distinct from
an ordinary durable fact (``MemoryCategory.USER_CONTEXT``) and from an
ad-hoc "record this for later" capture (``capture.py``).

**Why a task is not a recording and not a fact.** A recording
(``capture.py``) is a note captured for later retrieval - it has no
state, and "finishing" one is meaningless. A durable fact
(``session.py``/``user_facts.py``) describes who the person *is* - a
preference, a birthday, a goal. A task is neither: it is something to
*do*, it has a lifecycle (pending -> complete), and completing one is
itself the point. Collapsing any of these would make "forget that task"
indistinguishable from "forget who I am", or would let a to-do item's
free text quietly become a durable fact about the person's identity.

**Generic across agents, not one implementation per agent.** Exactly
like ``capture.py``, this module knows nothing about Alfred, Arthur,
ManagerCapability or CapabilityRequest - it turns text into a candidate
``MemoryRecord``, or reasons about an existing one, and the caller (a
Manager capability today; a future specialist's own request handling
tomorrow, with no change needed here) decides whose memory a candidate
is offered to and which agent's own governed policy applies.

**Deliberately minimal.** No due date, no reminder, no priority, no
recurrence - a title and a status, nothing else. Widening this shape is
a future decision, not an oversight; see ``MemoryCategory.TASK``'s own
comment for the identical restraint applied to the memory-policy side.

**Identity and "updating" a frozen record.** ``MemoryRecord`` is frozen
by design (see its own docstring: a correction is a new record, never
an edit in place). A task keeps a stable identity across an update or a
completion the same way an avatar/environment preference already does
(``src/services/identity/presence.py``'s own forget-old, remember-new
pattern) - except a task's replacement goes through the *governed*
write path (``request.retain``, i.e. ``ManagerMemoryPolicy``), not the
ungated ``remember`` a cosmetic preference uses, because arbitrary task
text deserves the same content-admission checks (length, secrets) every
other durable Manager write already gets. ``MemoryRecord.id`` *is* the
task's identity across its whole lifecycle - never regenerated on
update, so a task can always be found again by the same id even though
each update is, underneath, a new frozen object.
"""

from __future__ import annotations

import re
import uuid

from src.services.memory.memory import MemoryRecord

# The literal string a task record's own MemoryRecord.kind carries.
# Deliberately not "task" - src.manager.memory_policy.EXCLUDED_KINDS
# already reserves that exact string for a different concept entirely
# (the internal Task/TaskResult execution-bookkeeping objects every
# capability handler already receives one of - "every routine execution
# is history, not knowledge"). Kept as this module's own local constant,
# matching capture.py's CAPTURED_KIND, rather than imported from
# memory_policy - this module stays independent of the Manager's own
# policy layer, exactly as capture.py's own docstring states.
TASK_KIND = "user_task"

PENDING = "pending"
COMPLETE = "complete"

_CREATE_MARKERS: tuple[str, ...] = (
    "add a task", "add task", "create a task", "new task",
    "add this task", "remind me to",
)
# Pre-Stage-9 Campaign 4 of time: asking to be reminded or woken at a time -
# grammar only. "remind me what/of/who/how ..." is a question about what is
# already known, never a new reminder.
_REMINDER_MARKERS: tuple[str, ...] = (
    "remind me", "set a reminder", "set an alarm", "set alarm", "wake me up", "wake me",
)
_NOT_A_REMINDER = re.compile(r"\bremind\s+me\s+(?:what|of|who|how|why|where|which)\b", re.IGNORECASE)
_LIST_MARKERS: tuple[str, ...] = (
    "what are my tasks", "show my tasks", "list my tasks",
    "what tasks do i have", "my task list", "what do i need to do",
    "what's on my task list",
    # A timed reminder is a task with a due moment (build_task_record below),
    # so the person who set one asks for it back in reminder words - and got
    # "no reasoning capability is configured", because nothing claimed them.
    # The same listing answers both; describe_task names the due moment.
    "what reminders do i have", "what are my reminders", "list my reminders",
    "show my reminders", "my reminders", "what alarms do i have", "list my alarms",
    "show my alarms", "what's due today", "what is due today", "what's due",
    "what is due", "what have i got coming up", "what's coming up", "what is coming up",
)
_QUERY_MARKERS: tuple[str, ...] = (
    "do i have a task about", "what's my task about", "is there a task about",
    "what's the status of my task about",
)
_COMPLETE_MARKERS: tuple[str, ...] = (
    "complete the task", "complete task", "mark the task", "mark task",
    "i finished", "i completed", "done with the task", "finished the task",
)
_UPDATE_MARKERS: tuple[str, ...] = (
    "change the task", "change task", "update the task", "update task",
    "rename the task", "rename task",
)
# Deliberately non-overlapping with _COMPLETE_MARKERS above ("make the
# task"/"make task", never "mark the task"/"mark task") - a phrase must
# not be able to satisfy both complete_task_intent and reopen_task_intent,
# which would leave the router choosing between two capabilities that
# both claimed it. "uncomplete the task" is deliberately NOT a marker
# here even though it reads naturally: it contains "complete the task"
# as a literal substring, so it would satisfy complete_task_intent too -
# exactly the collision this set exists to avoid.
_REOPEN_MARKERS: tuple[str, ...] = (
    "reopen the task", "reopen task", "reopen this task",
    "make the task", "make task",
)
_TRAILING_INCOMPLETE = re.compile(r"\s*\bincomplete\b\.?\s*$", re.IGNORECASE)

_LEADING_JOINER = re.compile(r"^(?:to|that|:|-)\s*", re.IGNORECASE)
_TRAILING_STATUS = re.compile(
    r"\s*\bas\s+(?:done|complete|completed|finished)\b\.?\s*$", re.IGNORECASE
)


def _marker_present(text: str, markers: tuple[str, ...]) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in markers)


def _after_marker(text: str, markers: tuple[str, ...]) -> str:
    """The part of an utterance that follows the first marker found, with
    a leading joiner word ("to"/"that") and trailing punctuation
    stripped - the identical extraction shape every marker-based
    capability in this project already uses (see
    src/manager/capabilities.py's own ``_after_marker``), reimplemented
    here rather than imported so this module stays independent of the
    Manager layer.
    """
    lowered = str(text or "").lower()
    for marker in markers:
        position = lowered.find(marker)
        if position == -1:
            continue
        remainder = text[position + len(marker) :].strip(" ,.:;?\t\n")
        remainder = _LEADING_JOINER.sub("", remainder).strip(" ,.:;?\t\n")
        if remainder:
            return remainder
    return ""


# Closure campaign (2026-09-16, REQUIREMENT_LEDGER.md N-06): _CREATE_MARKERS
# above only ever matches the trigger phrase immediately preceding the task's
# own content ("add a task: X"). "I have a task: finish the report." - found
# live, not by reading code - states the same thing in the equally natural
# opposite order and matched nothing at all, silently falling through to
# open_conversation instead of creating a task or even refusing honestly.
#
# This is the shared fallback _after_marker's own callers reach for only
# when the primary marker-based extraction finds nothing - never a
# replacement for it, and deliberately narrow: the noun ("task") must
# still be named, so "I have a meeting at 3pm" (an unrelated declarative
# statement) is never mistaken for a task the same way _CREATE_MARKERS'
# own precision already refuses "that was quite a task".
_REORDERED_TASK_PATTERN = re.compile(
    r"^(?:i\s+(?:have|need|want|would\s+love|would\s+like)|there'?s)\s+an?\s+task\b"
    r"(?:\s*:|\s+(?:to|for|about|of))\s*(?P<subject>.+?)"
    r"(?:,\s*(?:can|could|would)\s+you\s+\w+.*)?$",
    re.IGNORECASE,
)


def _reordered_task_subject(text: str) -> "str | None":
    """The task's own content, from the reordered "I have a task: X" shape -
    see ``_REORDERED_TASK_PATTERN``'s own comment. ``None`` when the text
    does not have this shape at all, so a caller always tries the ordinary
    marker-based extraction first and only reaches this as a fallback."""
    match = _REORDERED_TASK_PATTERN.match(str(text or "").strip())
    if match is None:
        return None
    subject = match.group("subject").strip(" ,.:;!?\t\n")
    return subject or None


def create_task_intent(text: str) -> bool:
    """The ``claims=`` predicate for creating a task - including a reminder
    or alarm set for a time."""
    if _marker_present(text, _CREATE_MARKERS):
        return True
    if _reordered_task_subject(text) is not None:
        return True
    return reminder_intent(text)


def reminder_intent(text: str) -> bool:
    return _marker_present(text, _REMINDER_MARKERS) and not _NOT_A_REMINDER.search(str(text or ""))


# "in 10 minutes", "in 2 hours" - a relative moment. Grammar only.
_RELATIVE_MOMENT = re.compile(r"\bin\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_DATE_WORDS = re.compile(
    r"\b(?:today|tonight|tomorrow|(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|\d{4}-\d{2}-\d{2}|(?:on\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"|(?:on\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)
_TIME_PHRASE = re.compile(
    r"\b(?:at\s+|for\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b(?:at\s+|for\s+)?\d{1,2}:\d{2}\b"
    r"|\b(?:at\s+|for\s+)?(?:noon|midnight)\b|\bat\s+\d{1,2}\b(?!\s*(?:-\s*\d|to\s+\d|st\b|nd\b|rd\b|th\b))",
    re.IGNORECASE,
)


def extract_reminder(text: str, *, now) -> "tuple[str, object] | None":
    """``(title, due_at)`` for a request to be reminded or woken at a time,
    or None when the request names no time. ``now`` is a timezone-aware
    datetime in the person's own timezone - "5pm" means their 5pm.

    A time with no date is today if it is still ahead, otherwise tomorrow;
    "tomorrow"/a weekday/a date moves it to that day; "in 20 minutes" is
    relative to now. The title is what is left once the marker, the moment
    and joining words are removed ("to call Mum" -> "call Mum"); an alarm
    with nothing else named is simply an alarm."""
    from datetime import datetime, timedelta

    from src.services.memory.calendar_items import find_time_range, parse_calendar_date

    working = str(text or "")
    if not reminder_intent(working):
        return None

    relative = _RELATIVE_MOMENT.search(working)
    if relative is not None:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith("h") else timedelta(minutes=amount)
        due_at = (now + delta).replace(microsecond=0)
        remainder = working.replace(relative.group(0), " ")
    else:
        # The moment is a real time phrase ("at 5pm", "at 7", "17:30",
        # "noon") - never the first number anywhere, so "buy 2 apples at 5pm"
        # is due at 5pm, not 2am.
        phrase_match = _TIME_PHRASE.search(working)
        if phrase_match is None:
            return None
        spoken = phrase_match.group(0).lower()
        if "noon" in spoken:
            from datetime import time as _time
            clock = _time(12, 0)
        elif "midnight" in spoken:
            from datetime import time as _time
            clock = _time(0, 0)
        else:
            found = find_time_range(re.sub(r"^(?:at|for)\s+", "", spoken))
            if found is None:
                return None
            clock = found[0]
        date_match = _DATE_WORDS.search(working)
        day = None
        if date_match is not None:
            phrase = re.sub(r"^on\s+", "", date_match.group(0), flags=re.IGNORECASE)
            if phrase.lower() == "tonight":
                phrase = "today"
            phrase = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", phrase, flags=re.IGNORECASE)
            # "3 October" is read the way "October 3" already is.
            phrase = re.sub(r"^(\d{1,2})\s+([a-z]+)$", r"\2 \1", phrase.strip(), flags=re.IGNORECASE)
            day = parse_calendar_date(phrase, today=now.date())
            if day is None:
                # A date was named but could not be read - never guess a day.
                return None
        if day is None:
            day = now.date()
            candidate = datetime.combine(day, clock, tzinfo=now.tzinfo)
            if candidate <= now:
                day = day + timedelta(days=1)
        due_at = datetime.combine(day, clock, tzinfo=now.tzinfo)
        remainder = _TIME_PHRASE.sub(" ", working)
        if date_match is not None:
            remainder = remainder.replace(date_match.group(0), " ")

    lowered = remainder.lower()
    for marker in _REMINDER_MARKERS:
        position = lowered.find(marker)
        if position != -1:
            remainder = remainder[position + len(marker):]
            break
    title = re.sub(r"\b(?:for|at|on|in|me)\b\s*$", "", " ".join(remainder.split()), flags=re.IGNORECASE)
    title = _LEADING_JOINER.sub("", title.strip(" ,.:;?!-")).strip(" ,.:;?!-")
    title = re.sub(r"^(?:for|at|on)\s+", "", title, flags=re.IGNORECASE).strip()
    if not title:
        title = "Alarm" if re.search(r"\b(?:alarm|wake)\b", working, re.IGNORECASE) else "Reminder"
    return title, due_at


def extract_task_title(text: str) -> str:
    """The task's own title - whatever follows the creation marker, or,
    when nothing follows one (including the reordered "I have a task: X"
    shape - see ``_reordered_task_subject``), the reordered subject."""
    title = _after_marker(text, _CREATE_MARKERS)
    if title:
        return title
    return _reordered_task_subject(text) or ""


def build_task_record(text: str, *, agent_name: str, reminder: "tuple[str, object] | None" = None) -> MemoryRecord | None:
    """One candidate pending task from an explicit creation request, or
    None when nothing followed the marker (an honest "add a task" with
    no content named). ``reminder`` - ``(title, due_at)`` from
    ``extract_reminder`` - makes it a timed reminder: the due moment is
    stored with the task, which is what the proactive check reads."""
    if reminder is not None:
        title, due_at = reminder
        return MemoryRecord(
            id=uuid.uuid4().hex[:12],
            content=title,
            kind=TASK_KIND,
            tags={"task", f"task:{PENDING}", "reminder"},
            context={"status": PENDING, "created_by": agent_name, "due_at": due_at.isoformat()},
        )
    title = extract_task_title(text)
    if not title:
        return None
    return MemoryRecord(
        id=uuid.uuid4().hex[:12],
        content=title,
        kind=TASK_KIND,
        tags={"task", f"task:{PENDING}"},
        context={"status": PENDING, "created_by": agent_name},
    )


def list_tasks_intent(text: str) -> bool:
    """The ``claims=`` predicate for listing/querying tasks - either a
    bare listing or "do I have a task about X", both handled by the same
    capability (see ``extract_task_query``)."""
    return _marker_present(text, _LIST_MARKERS) or _marker_present(text, _QUERY_MARKERS)


def extract_task_query(text: str) -> str:
    """The topic to filter by, or "" for "list everything" - the same
    "" -> most-recent-first shape ``Memory.recall`` itself already
    documents for an empty query."""
    return _after_marker(text, _QUERY_MARKERS)


def complete_task_intent(text: str) -> bool:
    """The ``claims=`` predicate for completing a task."""
    return _marker_present(text, _COMPLETE_MARKERS)


def extract_completion_topic(text: str) -> str:
    """Which task the person means, with a trailing "as done"-style
    clause stripped - "mark the task buy milk as done" names the task
    "buy milk", not "buy milk as done"."""
    topic = _after_marker(text, _COMPLETE_MARKERS)
    return _TRAILING_STATUS.sub("", topic).strip()


def reopen_task_intent(text: str) -> bool:
    """The ``claims=`` predicate for reopening a completed task - the
    lifecycle's missing fourth verb (add/complete/reopen), so a task
    accidentally marked done, or genuinely not finished after all, is
    not a dead end."""
    return _marker_present(text, _REOPEN_MARKERS)


def extract_reopen_topic(text: str) -> str:
    """Which completed task the person means. Mirrors
    ``extract_completion_topic``'s own shape: ``_after_marker`` first,
    then a trailing status clause stripped - "make the task buy milk
    incomplete" names the task "buy milk", not "buy milk incomplete" -
    the identical reasoning ``_TRAILING_STATUS`` already applies for
    "... as done"."""
    topic = _after_marker(text, _REOPEN_MARKERS)
    return _TRAILING_INCOMPLETE.sub("", topic).strip()


def update_task_intent(text: str) -> bool:
    """The ``claims=`` predicate for renaming/updating a task."""
    return _marker_present(text, _UPDATE_MARKERS)


def extract_task_update(text: str) -> tuple[str, str] | None:
    """``(old_topic, new_title)`` for an explicit rename request, or
    None when the " to " separator naming the new title is missing.

    A known, honest limitation: this splits on the first " to " after
    the marker, so a task title that itself contains " to " cannot be
    named as the *old* half unambiguously. The same class of limitation
    ``capture.py``'s own extraction already accepts for "for" - a real,
    useful deterministic mechanism, not a claim of complete natural-
    language understanding.
    """
    remainder = _after_marker(text, _UPDATE_MARKERS)
    if not remainder:
        return None
    match = re.search(r"\bto\b", remainder, re.IGNORECASE)
    if match is None:
        return None
    old_topic = remainder[: match.start()].strip(" ,.:;?\t\n")
    new_title = remainder[match.end() :].strip(" ,.:;?\t\n")
    if not old_topic or not new_title:
        return None
    return old_topic, new_title


def match_tasks_by_topic(
    candidates: "tuple[MemoryRecord, ...]", topic: str
) -> "tuple[MemoryRecord, ...]":
    """Which of ``candidates`` a person naming ``topic`` most plausibly
    means - exact content match first, then a substring either
    direction, never ``Memory.recall``'s own literal-token-overlap
    scoring.

    Token overlap is the wrong instrument here, and provably so: a task
    titled "water the plants" and one titled "write the quarterly
    report" share the token "the" and nothing else, and
    ``recall("water the plants", ...)`` genuinely returns both - correct
    behaviour for ``recall``'s own documented contract (literal token
    overlap against arbitrary prose), and exactly wrong for naming one
    short, precise task title among a person's own list. This is a
    known, honest limitation in the other direction too: "buy milk"
    will not match a task titled "buy oat milk" (not a substring either
    way) - a real, useful deterministic mechanism, not a claim of
    complete natural-language understanding, the same restraint
    ``extract_task_update``'s own docstring already states.
    """
    topic_lower = " ".join(topic.strip().lower().split())
    if not topic_lower:
        return ()
    exact = tuple(r for r in candidates if r.content.strip().lower() == topic_lower)
    if exact:
        return exact
    contained = tuple(
        r for r in candidates
        if topic_lower in r.content.lower() or r.content.lower() in topic_lower
    )
    if contained:
        return contained
    # "I finished renewing my car insurance" names "renew my car insurance"
    # in another form of the same words. Every word of one, compared by stem,
    # must be in the other - never a shared word or two - so "water the
    # plants" and "write the report" still never match each other.
    wanted = _task_stems(topic_lower)
    if not wanted:
        return ()
    return tuple(
        r for r in candidates
        if (have := _task_stems(r.content)) and (wanted <= have or have <= wanted)
    )


_TASK_FILLER = frozenset({
    "a", "an", "the", "my", "to", "of", "for", "and", "on", "in", "at", "with", "up", "it", "this", "that", "task",
})


def _task_stems(text: str) -> "frozenset[str]":
    """Content words of a task title, reduced to a shared stem - "renewing"
    and "renew", "reports" and "report", "studies" and "study"."""
    stems = set()
    for word in re.findall(r"[a-z0-9']+", str(text or "").lower()):
        if word in _TASK_FILLER:
            continue
        for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)] + keep
                break
        if len(word) > 3 and word[-1] == "e":
            word = word[:-1]
        stems.add(word)
    return frozenset(stems)


def describe_task(record: MemoryRecord) -> str:
    """One task, rendered for a person to read - the identical shape a
    Control Center listing and a conversational answer both use, so the
    two surfaces never describe the same task two different ways.

    A timed reminder names its moment, in the identical wording the answer
    that created it used ("at 21:00 on Tuesday 15 September 2026"): a list
    of reminders with no times in it does not answer "what reminders do I
    have", and the due moment is already stored on the record.
    """
    context = record.context or {}
    status = str(context.get("status") or PENDING)
    marker = "x" if status == COMPLETE else " "
    return f"[{marker}] {record.content}{_due_suffix(context)} ({record.id})"


def _due_suffix(context: "dict") -> str:
    """" - at <moment>" for a timed reminder, "" for an ordinary task."""
    raw = str(context.get("due_at") or "").strip()
    if not raw:
        return ""
    from datetime import datetime

    try:
        due_at = datetime.fromisoformat(raw)
    except ValueError:
        return f" - due {raw}"
    return f" - at {due_at.strftime('%H:%M on %A %d %B %Y')}"
