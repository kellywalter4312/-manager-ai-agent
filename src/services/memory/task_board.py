"""Every agent's own task board - add, list, complete, reopen and rename - over
that agent's own memory, admitted by that agent's own policy.

The Manager's task capabilities (``src/manager/capabilities.py``) and every
specialist's Workspace board speak the identical grammar and matching
(``tasks.py``); this module is the board for any agent that is not the Manager,
so a specialist added later has a working board without a line changing.
Nothing here names an agent or a domain.

Honest limits, kept deliberately:

- a reminder at a time is not delivered by a specialist's board - the proactive
  check reads the Manager's store - so it is declined plainly, never stored as
  if it would ring;
- a loose remark that merely contains "I finished ..." is not taken as a board
  request unless a pending task actually matches or the word "task" was used.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

from src.services.memory.tasks import (
    COMPLETE,
    PENDING,
    TASK_KIND,
    build_task_record,
    complete_task_intent,
    create_task_intent,
    describe_task,
    extract_completion_topic,
    extract_reminder,
    extract_reopen_topic,
    extract_task_query,
    extract_task_update,
    list_tasks_intent,
    match_tasks_by_topic,
    reminder_intent,
    reopen_task_intent,
    update_task_intent,
)

_PLAIN_CREATE = ("add a task", "add task", "create a task", "new task", "add this task", "remind me to")


@dataclass(frozen=True)
class BoardReply:
    ok: bool
    text: str
    payload: Mapping[str, Any] = field(default_factory=dict)


def _lowered(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _names_a_task(text: str) -> bool:
    return "task" in _lowered(text).split() or "task" in _lowered(text)


def _replace_in_place(target, updated, *, retain, forget, verb: str) -> "str | None":
    """The governed replace-in-place the Manager's own task capabilities use:
    same id, new version admitted first-class, the old one restored if refused."""
    if not forget(target.id):
        return f"I couldn't {verb} that task: it was already gone."
    verdict = retain(updated)
    if not getattr(verdict, "allowed", False):
        retain(target)
        return f"I couldn't {verb} that task: {getattr(verdict, 'reason', 'it was refused')}"
    return None


def _one_match(pool, topic: str, *, status_word: str) -> "tuple[Any, str | None]":
    matches = match_tasks_by_topic(tuple(pool), topic)
    if not matches:
        return None, f"I don't have a {status_word} task matching '{topic}'."
    if len(matches) > 1:
        listing = "; ".join(record.content for record in matches)
        return None, f"I found {len(matches)} {status_word} tasks matching '{topic}' - be more specific: {listing}"
    return matches[0], None


def task_board_reply(
    text: str,
    *,
    agent_name: str,
    recall: Callable[..., Any],
    retain: Callable[[Any], Any],
    forget: Callable[[str], bool],
    now: Any = None,
) -> "BoardReply | None":
    """The board's answer to ``text``, or None when it is not a board request -
    the caller then does its own work, unchanged."""
    words = str(text or "")

    if update_task_intent(words):
        parsed = extract_task_update(words)
        if parsed is None:
            return BoardReply(False, "Rename which task, and to what? Say \"rename the task X to Y\".")
        old_topic, new_title = parsed
        target, problem = _one_match(recall(kind=TASK_KIND, tags=set(), limit=200), old_topic, status_word="")
        if problem:
            return BoardReply(False, problem.replace("  ", " "))
        updated = replace(target, content=new_title)
        problem = _replace_in_place(target, updated, retain=retain, forget=forget, verb="rename")
        if problem:
            return BoardReply(False, problem)
        return BoardReply(True, f"Renamed: {target.content} -> {new_title}", {"handled_by": "task_board", "task_id": target.id})

    if reopen_task_intent(words):
        topic = extract_reopen_topic(words)
        if not topic:
            return BoardReply(False, "Reopen which task, exactly?")
        target, problem = _one_match(recall(kind=TASK_KIND, tags={f"task:{COMPLETE}"}, limit=200), topic, status_word="completed")
        if problem:
            return BoardReply(False, problem)
        updated = replace(target, tags={"task", f"task:{PENDING}"}, context={**(target.context or {}), "status": PENDING})
        problem = _replace_in_place(target, updated, retain=retain, forget=forget, verb="reopen")
        if problem:
            return BoardReply(False, problem)
        return BoardReply(True, f"Reopened: {target.content}", {"handled_by": "task_board", "task_id": target.id})

    if complete_task_intent(words):
        topic = extract_completion_topic(words)
        pool = recall(kind=TASK_KIND, tags={f"task:{PENDING}"}, limit=200)
        target, problem = _one_match(pool, topic, status_word="pending") if topic else (None, "Complete which task, exactly?")
        if problem:
            # "I finished reading my emails" is conversation, not a board request.
            return BoardReply(False, problem) if _names_a_task(words) else None
        updated = replace(target, tags={"task", f"task:{COMPLETE}"}, context={**(target.context or {}), "status": COMPLETE})
        problem = _replace_in_place(target, updated, retain=retain, forget=forget, verb="complete")
        if problem:
            return BoardReply(False, problem)
        return BoardReply(True, f"Completed: {target.content}", {"handled_by": "task_board", "task_id": target.id})

    if list_tasks_intent(words):
        topic = extract_task_query(words)
        pool = tuple(recall(kind=TASK_KIND, tags=set(), limit=200))
        records = match_tasks_by_topic(pool, topic) if topic else pool
        if not records:
            empty = f"I don't have a task matching '{topic}'." if topic else "Nothing on my board yet."
            return BoardReply(True, empty, {"handled_by": "task_board", "count": 0})
        return BoardReply(True, "\n".join(describe_task(r) for r in records), {"handled_by": "task_board", "count": len(records)})

    if create_task_intent(words):
        plain = any(marker in _lowered(words) for marker in _PLAIN_CREATE)
        timed = extract_reminder(words, now=now) if now is not None and reminder_intent(words) else None
        if timed is not None or not plain:
            return BoardReply(
                False,
                "I keep a task board, but I can't ring a reminder at a time myself - "
                "ask the Manager for a timed reminder and it will be delivered then.",
            )
        record = build_task_record(words, agent_name=agent_name)
        if record is None:
            return BoardReply(False, "Add a task to do what, exactly?")
        # An explicit owner request, never a tentative observation - carried as
        # full confidence, exactly as an explicit capture already is.
        record = replace(record, context={**(record.context or {}), "confidence": 1.0})
        verdict = retain(record)
        if not getattr(verdict, "allowed", False):
            return BoardReply(False, f"I couldn't add that task: {getattr(verdict, 'reason', 'it was refused')}")
        return BoardReply(True, f"Added: {record.content}", {"handled_by": "task_board", "task_id": record.id})

    return None
