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
_LIST_MARKERS: tuple[str, ...] = (
    "what are my tasks", "show my tasks", "list my tasks",
    "what tasks do i have", "my task list", "what do i need to do",
    "what's on my task list",
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


def create_task_intent(text: str) -> bool:
    """The ``claims=`` predicate for creating a task."""
    return _marker_present(text, _CREATE_MARKERS)


def extract_task_title(text: str) -> str:
    """The task's own title - whatever follows the creation marker."""
    return _after_marker(text, _CREATE_MARKERS)


def build_task_record(text: str, *, agent_name: str) -> MemoryRecord | None:
    """One candidate pending task from an explicit creation request, or
    None when nothing followed the marker (an honest "add a task" with
    no content named)."""
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
    return tuple(
        r for r in candidates
        if topic_lower in r.content.lower() or r.content.lower() in topic_lower
    )


def describe_task(record: MemoryRecord) -> str:
    """One task, rendered for a person to read - the identical shape a
    Control Center listing and a conversational answer both use, so the
    two surfaces never describe the same task two different ways."""
    status = str((record.context or {}).get("status") or PENDING)
    marker = "x" if status == COMPLETE else " "
    return f"[{marker}] {record.content} ({record.id})"
