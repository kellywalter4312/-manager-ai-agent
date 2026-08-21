"""A specialist that owns a brain and a memory.

    CognitiveAgent
      |- brain   - reasoning about its own work
      |- memory  - what it has retained about its own work

The same pairing the Manager has, and deliberately *not* the same objects. A
specialist receives a Task and nothing else when it is delegated to; its
cognitive equipment is supplied when it is constructed, by whoever composes the
system. Delegation carries work, never access.

That is the whole boundary, and it is structural rather than a rule anyone has
to remember. There is no manager argument, no registry, no route by which an
agent constructed here could reach the orchestrator's memory even if it tried -
the objects are simply not reachable from a Task.

Opt-in by inheritance. Extending BaseAgent itself would have forced an
``__init__`` on every existing agent and every test double that subclasses it;
a specialist that wants cognition inherits from this instead, and one that does
not is unaffected. BaseAgent remains the contract the Manager depends on, so
nothing about routing or assessment changes either way.

Note what this class does *not* do: it never consults the brain and never
writes to the memory. It establishes ownership. When a specialist should think,
and what it should retain, are decisions belonging to that specialist's own
implementation - the same order the Manager followed, boundary first.
"""

from __future__ import annotations

from typing import Sequence

from src.agents.base_agent import BaseAgent
from src.agents.brain import (
    MEMORY_NOTE_PREFIX,
    Insight,
    NullSpecialistBrain,
    SpecialistBrain,
)
from src.agents.memory_policy import SpecialistMemoryPolicy
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.services.memory.day import Calendar, DayView
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord
from src.services.memory.policy import MemoryPolicy
from src.services.memory.query import distinctive_terms
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# How many of its own records a specialist offers its brain as evidence.
# Bounded for the same reason the Manager's is: unbounded context is the first
# step toward needing a summariser to manage it.
MEMORY_CONTEXT_LIMIT = 5

# MEMORY_NOTE_PREFIX is re-exported from brain.py, where it sits beside the
# contract it belongs to. Imported here because this is the module that applies
# it, and kept importable from here because callers already reach for it.


class CognitiveAgent(BaseAgent):
    """A BaseAgent with its own reasoning and its own retention.

    Still abstract: ``spec`` and ``execute`` remain the subclass's to provide,
    so this adds equipment without weakening the agent contract.
    """

    def __init__(
        self,
        brain: SpecialistBrain | None = None,
        memory: Memory | None = None,
        memory_policy: MemoryPolicy | None = None,
        tools: ToolSet | None = None,
        calendar: Calendar | None = None,
    ) -> None:
        """Both are injected, and both default to something safe.

        The brain defaults to NullSpecialistBrain, which answers honestly that
        it has no view - the null-object pattern used throughout this project.

        The memory defaults to a fresh EphemeralMemory rather than a null one,
        for the reason argued at the Manager: a null memory would have to
        accept remember() and silently discard it, leaving a caller who stored
        something and recalled nothing with no signal. A brain can say it has
        no view; a store has no equivalent way to say so.

        Constructed per instance, never shared. Two specialists get two
        memories, and neither gets the orchestrator's.
        """
        if brain is not None and not isinstance(brain, SpecialistBrain):
            # Validated at the boundary so a mistake surfaces at construction,
            # not much later inside a delegated task. The same reasoning as
            # AgentRegistry.register and EmailAgent's mailbox check.
            raise TypeError(
                f"Expected a SpecialistBrain, got {type(brain).__name__}"
            )
        if memory is not None and not isinstance(memory, Memory):
            raise TypeError(f"Expected a Memory, got {type(memory).__name__}")

        self.brain = brain if brain is not None else NullSpecialistBrain()
        self.memory = memory if memory is not None else EphemeralMemory()

        # What may be kept is a separate question from where it is kept, and a
        # separate object answers it. Stateless, so unlike a memory this one is
        # safe to share between specialists.
        self.memory_policy = (
            memory_policy if memory_policy is not None else SpecialistMemoryPolicy()
        )

        # Capability is what was explicitly granted, and the default grant is
        # nothing. A specialist nobody equipped can do nothing, which is the
        # right starting point for something that will one day be driven by a
        # model. The set cannot grow afterwards - see ToolSet.
        if tools is not None and not isinstance(tools, ToolSet):
            raise TypeError(f"Expected a ToolSet, got {type(tools).__name__}")
        self.tools = tools if tools is not None else ToolSet()

        # The same temporal rules the Manager uses, over this specialist's own
        # store. Same rules, different memory - never a shared one.
        self.calendar = calendar if calendar is not None else Calendar.utc()

    @property
    def can_reason(self) -> bool:
        """Whether a real reasoning capability was supplied."""
        return not isinstance(self.brain, NullSpecialistBrain)

    # ------------------------------------------------------------------
    # The cognitive loop
    # ------------------------------------------------------------------

    def recall_context(
        self, task: Task, limit: int = MEMORY_CONTEXT_LIMIT
    ) -> tuple[str, ...]:
        """This specialist's own past records, relevant to ``task``.

        Its own, and only its own. The store is the one handed to this
        instance at construction, so there is no query that could reach the
        orchestrator's records or another specialist's - not because access is
        forbidden, but because no such object is in scope here.

        Returns () rather than raising. Recall is a read on the way to forming
        a view; if it fails, the honest outcome is a specialist that works
        without history, not a failed task.
        """
        log = task_logger(logger, task.id)

        if limit <= 0:
            return ()

        query = distinctive_terms(task.description)
        if not query:
            # An empty query matches everything, which would hand the brain
            # unrelated history and call it evidence.
            return ()

        try:
            # Today only, exactly as the Manager does it.
            records = DayView(self.memory, self.calendar).today(query, limit=limit)
            return tuple(f"{MEMORY_NOTE_PREFIX}: {record.content}" for record in records)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception("could not recall context: %s", type(exc).__name__)
            return ()

    def reason_about(self, task: Task, notes: Sequence[str] = ()) -> Insight | None:
        """Form a view about this work, or None if no view is available.

        None is not a failure mode dressed up - it is the honest answer to
        "was reasoning done?" when no brain is configured, when the brain
        broke, or when it returned something that is not an Insight. A caller
        can then report that no judgement was made, rather than inventing one.

        The brain is given the task and this specialist's own recalled records,
        as inert strings. It receives no store, no registry and no route to
        anything it could act on.

        Fault-isolated the way the Manager isolates a delegated agent: a brain
        is injected and may be anything, and a failure to think must cost this
        task its judgement, never the task itself.
        """
        if not self.can_reason:
            # No brain configured. Calling the null one would produce an
            # insight saying "no reasoning configured", which is noise dressed
            # as a conclusion - better to say nothing was attempted.
            return None

        log = task_logger(logger, task.id)
        context = tuple(notes) + self.recall_context(task)

        try:
            insight = self.brain.consider(task, context)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception("brain raised %s", type(exc).__name__)
            return None

        if not isinstance(insight, Insight):
            # A malformed answer is a broken contract, not a judgement.
            log.error("brain returned %s, not an Insight", type(insight).__name__)
            return None

        log.debug("considered: %s", insight.describe())
        return insight

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def propose_memory(
        self, task: Task, insight: Insight | None, outcome: object = None
    ) -> MemoryRecord | None:
        """What, if anything, is worth keeping from this piece of work.

        **Returns None by default, and that is the intended behaviour**, not a
        stub. A specialist remembers nothing until it can say specifically what
        is worth remembering, because the alternative - a generic "record the
        run" - produces exactly the execution history the policy exists to
        refuse. Opting in is a per-specialist decision made with knowledge of
        the domain.

        A subclass returns a MemoryRecord it would *like* kept. It is a
        proposal, not a write: ``remember_experience`` still asks the policy,
        and the policy refuses plenty.

        No separate candidate type is introduced. A MemoryRecord already
        carries content, a category and context, which is all an admission
        decision needs; a parallel model would duplicate it and require a
        second policy interface to judge.
        """
        return None

    def remember_experience(
        self, task: Task, insight: Insight | None, outcome: object = None
    ) -> MemoryRecord | None:
        """Offer this work to the policy, and keep it only if permitted.

        Returns the stored record, or None when nothing was proposed, nothing
        was admitted, or something went wrong. The three are distinguished in
        the log rather than in the return value, because a caller has the same
        thing to do about all of them: nothing.

        Everything is wrapped. **A memory failure is not a task failure** - the
        domain work has already finished by the time this runs, and a store
        that raises must not turn a completed task into a failed one. Interrupts
        still propagate: ``except Exception`` and not BaseException.
        """
        log = task_logger(logger, task.id)
        try:
            candidate = self.propose_memory(task, insight, outcome)
            if candidate is None:
                return None

            if not isinstance(candidate, MemoryRecord):
                log.error(
                    "proposed %s, not a MemoryRecord", type(candidate).__name__
                )
                return None

            # The policy is the authority. A specialist does not get to
            # overrule its own admission rules, and there is no path from here
            # to the store that skips this call.
            verdict = self.memory_policy.evaluate(candidate)
            if not verdict.allowed:
                log.debug("not remembered: %s", verdict.reason)
                return None

            self.memory.remember(candidate)
            log.debug("remembered %s", candidate.describe())
            return candidate

        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            log.exception(
                "could not record this experience: %s", type(exc).__name__
            )
            return None
