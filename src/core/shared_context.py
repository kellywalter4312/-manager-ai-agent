"""Context deliberately shared with a participant for one piece of work.

When several agents work on one thing - a discussion, a task handed from one
to another - each needs to see what the others contributed. None may reach into
another's memory or brain to get it. So shared context travels the one way
work already travels between agents: on the Task itself.

**Explicit, attributed, and never private.** A note here exists because the
orchestrator put it on the task, and every note says who produced it, what
kind of thing it is, and where it was shared. Nothing in this module reads an
agent's memory, a brain's state or a transcript on its own - it carries what it
is handed. Private memory stays private because nothing ever copies it here.

**Marked as shared.** Every line a brain is given starts with ``SHARED_PREFIX``,
the same discipline ``MEMORY_NOTE_PREFIX`` ("recalled") already gives memory
notes, so a reasoning model can always tell what another participant said from
what it remembers itself and from what its caller instructed.

**No schema of anybody's.** ``kind`` and ``scope`` are the caller's words,
carried and never interpreted - the same contract ``MemoryRecord.kind`` and
``Contribution.kind`` have - so a future kind of shared work needs nothing here
changed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from src.core.contracts import Task

SHARED_CONTEXT_KEY = "shared_context"
SHARED_PREFIX = "shared"


@dataclass(frozen=True)
class SharedNote:
    """One attributed piece of shared context."""

    source: str
    content: str
    kind: str = ""
    scope: str = ""

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise ValueError("a shared note must name who produced it")
        if not str(self.content).strip():
            raise ValueError("a shared note must say something")
        object.__setattr__(self, "source", " ".join(str(self.source).split()))
        object.__setattr__(self, "content", str(self.content).strip())
        object.__setattr__(self, "kind", " ".join(str(self.kind).split()).lower())
        object.__setattr__(self, "scope", " ".join(str(self.scope).split()))

    def line(self) -> str:
        """The note as a brain is given it: marked shared, attributed, scoped."""
        where = f" in {self.scope}" if self.scope else ""
        kind = f" ({self.kind})" if self.kind else ""
        return f"[{SHARED_PREFIX}{where}] {self.source}{kind}: {self.content}"

    def to_dict(self) -> dict:
        return {"source": self.source, "content": self.content, "kind": self.kind, "scope": self.scope}


def with_shared_context(task: Task, notes: Iterable[SharedNote]) -> Task:
    """``task`` carrying ``notes`` as its shared context. A new Task - a Task
    is frozen, and its id stays the same so the work is still one piece of
    work. Notes already on the task are kept, then these are added."""
    added = tuple(n for n in notes if isinstance(n, SharedNote))
    if not added:
        return task
    payload = dict(task.payload)
    payload[SHARED_CONTEXT_KEY] = shared_context_of(task) + added
    return replace(task, payload=payload)


def shared_context_of(task: Task) -> tuple[SharedNote, ...]:
    """The shared notes a task carries, and nothing else found on it."""
    carried = (task.payload or {}).get(SHARED_CONTEXT_KEY, ())
    return tuple(n for n in carried if isinstance(n, SharedNote))


def shared_notes(task: Task) -> tuple[str, ...]:
    """The shared context as the lines a brain is given."""
    return tuple(note.line() for note in shared_context_of(task))
