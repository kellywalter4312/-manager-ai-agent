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

import re
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
from src.core.contracts import Task, TaskResult, tokenize
from src.services.conversation_floor import (
    GREETING_WORDS,
    THANKS_WORDS,
    conversational_intent,
)
from src.services.identity.introduction import (
    greeting_from_identity,
    identity_question,
    self_introduction,
)
from src.services.memory.day import Calendar, DayView
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord
from src.services.memory.policy import MemoryPolicy, PolicyVerdict
from src.services.memory.query import distinctive_terms
from src.services.memory.task_board import task_board_reply
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# How many of its own records a specialist offers its brain as evidence.
# Bounded for the same reason the Manager's is: unbounded context is the first
# step toward needing a summariser to manage it.
MEMORY_CONTEXT_LIMIT = 5

# MEMORY_NOTE_PREFIX is re-exported from brain.py, where it sits beside the
# contract it belongs to. Imported here because this is the module that applies
# it, and kept importable from here because callers already reach for it.


_ABOUT_ME = re.compile(
    r"\bwhat\s+(?:do|else\s+do)\s+you\s+(?:know|remember)\s+about\s+me\b|\btell\s+me\s+what\s+you\s+know\s+about\s+me\b",
    re.IGNORECASE,
)


class CognitiveAgent(BaseAgent):
    """A BaseAgent with its own reasoning and its own retention.

    Still abstract: ``spec`` and ``execute`` remain the subclass's to provide,
    so this adds equipment without weakening the agent contract.
    """

    # ---- the conversational floor ----------------------------------------
    #
    # Stage 17.22. A specialist had none, and the consequence was not
    # subtle: every message that reached one was treated as domain work,
    # so "hello Arthur" returned six email subjects and "who are you"
    # returned the same six. The specialist was not misbehaving - it was
    # being handed conversation and had only one thing it knew how to do
    # with a Task.
    #
    # This is that floor, and it is the SAME one the orchestrator stands
    # on: the identical vocabulary and the identical whole-message rule
    # from src/services/conversation_floor.py, and the identical
    # identity rendering from src/services/identity/introduction.py.
    # Not a second conversation system, not a parallel router, and not a
    # response library - it recognises two shapes of message and answers
    # them from this agent's own already-loaded identity document.
    #
    # Deliberately returns None rather than a TaskResult for anything
    # else, so a subclass calls it first and then carries on exactly as
    # it did before for real work. Nothing about domain routing, memory,
    # tools or the Brain changes, and no model is consulted: naming
    # yourself from a document that names you needs no reasoning, and
    # claiming it did was the original defect.
    def conversational_reply(self, task: Task) -> "TaskResult | None":
        """An answer to a greeting or a "who are you", or None.

        None means "this is not conversation" - the caller does its own
        work, unchanged. A specialist whose identity document declares no
        name gets None for the identity case too, so it falls through to
        its ordinary behaviour rather than answering with a blank.
        """
        identity = getattr(self, "identity", None)

        # "Arthur, choose the voice that suits you" / "speak with a British
        # accent": how this agent sounds, decided by the one voice capability
        # every agent shares - never a second voice system per specialist.
        voiced = self.voice_reply(task)
        if voiced is not None:
            return voiced

        # "Arthur, start recording for field notes" / "stop recording":
        # found live, driving exactly this phrasing, this fell through to
        # this agent's own domain fallback instead - the frontend's
        # Start/Stop buttons and SpecialistMemoryCategory both already
        # treat recording as agent-generic (T-REC-03), but nothing routed
        # the natural-language request there. The one governed recording
        # capability every agent shares, never a second recording system.
        recorded = self.recording_reply(task)
        if recorded is not None:
            return recorded

        # What the person has taught, asked of any specialist: answered from
        # the one canonical view of the person this agent is granted
        # read-only - never a copy, never its own store - and with no model.
        known = self.personal_context_reply(task)
        if known is not None:
            return known

        # "Nova, build a 3D model of a lighthouse" / "read this file aloud":
        # any registered capability this agent is authorised for - the
        # every-agent ones, and whatever the owner granted it - run with this
        # agent's own name, memory and Brain. The same capability the Manager
        # runs, never a copy, and granting it here takes nothing from anyone.
        capable = self.capability_reply(task)
        if capable is not None:
            return capable

        if identity_question(task.description):
            text = self_introduction(identity, self.spec.capabilities)
            if text is not None:
                return TaskResult.success(task, self.name, text)
            return None

        # Its own name is discounted, so "hello Arthur" is recognised as
        # the greeting it is rather than failing on the name token.
        if conversational_intent(task, participants=(self.name,)):
            tokens = tokenize(task.description)
            if tokens & THANKS_WORDS:
                return TaskResult.success(task, self.name, "You're welcome!")
            if tokens & GREETING_WORDS:
                text = greeting_from_identity(identity)
                if text is None:
                    return None
                return TaskResult.success(task, self.name, text)
            return TaskResult.success(task, self.name, "Got it.")

        return None

    def bind_voice(self, handler) -> None:
        """How this agent chooses and configures its own voice: a callable
        ``handler(agent, task) -> TaskResult | None`` given by the composition
        root, which runs the one governed voice capability with this agent's
        own name and own memory against the active speech provider. Held as a
        plain callable so this module never reaches the orchestrator's code -
        the layering the specialist package keeps."""
        self._voice_handler = handler

    def voice_reply(self, task: Task) -> "TaskResult | None":
        """This agent choosing, reselecting, releasing or configuring its own
        voice, or None for anything else (and when no handler was given). The
        person's explicit choice (Control Center ``voice <agent> <voice>``)
        still wins over the agent's own, exactly as it does for the Manager."""
        handler = getattr(self, "_voice_handler", None)
        return handler(self, task) if callable(handler) else None

    def bind_recording(self, handler) -> None:
        """This agent's own real recording capability: a callable
        ``handler(agent, task) -> TaskResult | None`` given by the
        composition root, which runs the same governed
        start/stop/cancel/list_recordings capabilities the Manager has,
        with this agent's own name, own memory and the one shared
        ``RecordingSessionRegistry`` every agent's session already lives
        in - never a second recording system. Held as a plain callable
        for the identical layering reason ``bind_voice`` already is."""
        self._recording_handler = handler

    def recording_reply(self, task: Task) -> "TaskResult | None":
        """This agent starting, stopping, cancelling or listing its own
        recordings, or None for anything else (and when no handler was
        given) - the browser's Start/Stop buttons already reach this
        agent's own session regardless; this is what lets the identical
        request work when simply said or typed instead."""
        handler = getattr(self, "_recording_handler", None)
        return handler(self, task) if callable(handler) else None

    def bind_capabilities(self, handler) -> None:
        """This agent's authorised capabilities: a callable
        ``handler(agent, task) -> TaskResult | None`` given by the
        composition root, which reads the owner's governed grants at the
        moment of asking and runs the one registered capability that claims
        the request - with this agent's own name, memory and Brain. Held as
        a plain callable for the identical layering reason ``bind_voice``
        is: this package never reaches the orchestrator's code."""
        self._capability_handler = handler

    def capability_reply(self, task: Task) -> "TaskResult | None":
        """The answer of an authorised capability that claims this request,
        or None (nothing claimed it, nothing is granted, or no handler was
        given) - the caller then does its own work, unchanged."""
        handler = getattr(self, "_capability_handler", None)
        return handler(self, task) if callable(handler) else None

    def authorised_capabilities(self) -> "tuple[str, ...]":
        """The registered capabilities this agent may use right now, by name
        - empty when no handler was given."""
        handler = getattr(self, "_capability_handler", None)
        names = getattr(handler, "authorised", None)
        return tuple(names(self)) if callable(names) else ()

    def selected_capability_for(self, task_id: str) -> "str | None":
        """Which authorised capability actually ran for ``task_id``, or None
        (nothing claimed it, or no handler was given).

        The same question - and the same name - ``ManagerSkills`` already
        answers for the Manager, so one reader (the Control Center's trail)
        asks every agent alike rather than knowing which kind it has. Read
        through the bound handler for the identical layering reason
        ``authorised_capabilities`` above is: this package never reaches the
        orchestrator's code.
        """
        handler = getattr(self, "_capability_handler", None)
        selected = getattr(handler, "selected_capability_for", None)
        return selected(self.name, task_id) if callable(selected) else None

    def bind_user_knowledge(self, user_knowledge) -> None:
        """Grant this agent the canonical, read-only view of the person
        (``UserKnowledge``) - the same one the Manager reads."""
        self._user_knowledge = user_knowledge

    def personal_context_reply(self, task: Task) -> "TaskResult | None":
        """"What do you know about me?", "where do I work?" - answered from the
        granted canonical view, or None when this is not such a question or
        no view was granted."""
        from src.services.memory.personal_context import (
            STATUS_CURRENT, describe_for_user, extract_personal_context_query, match_personal_context,
        )

        knowledge = getattr(self, "_user_knowledge", None)
        if knowledge is None:
            return None
        text = str(task.description or "")
        about_me = _ABOUT_ME.search(text) is not None
        focus = extract_personal_context_query(text)
        if focus is None and not about_me:
            return None
        items = tuple(knowledge.current())
        matched = items if about_me and focus is None else match_personal_context(items, focus or "")
        current = [item for item in matched if item.status == STATUS_CURRENT][:8]
        if not current:
            return TaskResult.success(
                task, self.name, "You haven't told us anything about that yet.",
                payload={"handled_by": "personal_context", "source": "user_knowledge", "count": 0},
            )
        lines = [describe_for_user(item) for item in current]
        said = lines[0] if len(lines) == 1 else "Here's what I know:\n" + "\n".join(f"- {line}" for line in lines)
        return TaskResult.success(
            task, self.name, said,
            payload={"handled_by": "personal_context", "source": "user_knowledge", "count": len(current)},
        )

    # ---- this agent's own Workspace task board ----------------------------
    #
    # Post-Pre-Stage-9. Every agent has a Workspace, and every Workspace has a
    # task board. Found in a real browser: "add a task to sort the inbox by
    # client", addressed to a specialist (exactly what the page's board input
    # sends), was treated as domain work and listed the inbox. The board is
    # shared by every specialist and lives here once; nothing about any domain
    # is known to it.
    def workspace_task_reply(self, task: Task) -> "TaskResult | None":
        """This agent's own board's answer - add, list, complete, reopen,
        rename - over its own memory and policy, or None when the request is
        not about the board (the caller then does its own work)."""
        reply = task_board_reply(
            task.description,
            agent_name=self.name,
            recall=self.memory.recall,
            retain=self.retain,
            forget=self.memory.forget,
            now=self.calendar.now(),
        )
        if reply is None:
            return None
        if reply.ok:
            return TaskResult.success(task, self.name, reply.text, payload=dict(reply.payload))
        return TaskResult.failure(task, self.name, reply.text)

    # ---- genuinely open-ended conversation --------------------------------
    #
    # 0.2 Final Surgical Pass. conversational_reply above recognises two
    # narrow shapes (a greeting, a "who are you") and answers them without
    # a model - correct for those two, and never enough on its own: a
    # specialist asked something with real content that names none of its
    # own domain vocabulary ("tell me about Napoleon", a joke, a follow-up
    # in an ongoing conversation) has nowhere else to go, and before this
    # existed it fell straight into whatever this agent's own domain
    # default is - EmailAgent's own execute() shows the consequence: such
    # a remark used to fetch and summarise the mailbox, because that
    # method's fallback for "not obviously domain work" was "assume it is
    # anyway."
    #
    # Reusable and undecided about *when* it applies on purpose: only the
    # concrete specialist knows what its own domain vocabulary looks like
    # (EmailAgent's is "email", "inbox", "send", its own analyzer words -
    # this base class has never known any of that and must not start
    # now), so the concrete specialist decides when nothing named its
    # domain and calls this; what happens once that decision is made -
    # consult this specialist's own brain, honestly, exactly the way the
    # orchestrator's own open_conversation already does - is the one
    # thing every specialist shares, and lives here exactly once.
    def general_conversation(self, task: Task, notes: Sequence[str] = ()) -> TaskResult:
        """Answer a genuinely open-ended remark by consulting this
        specialist's own brain directly - the specialist's own
        counterpart to the orchestrator's ``open_conversation`` capability
        (``src/manager/capabilities.py``), mirroring its exact shape so
        natural conversation is one reusable capability of the canonical
        agent architecture rather than reimplemented per specialist.

        Honest either way, the identical rule ``open_conversation``
        itself lives under: a usable brain composes a genuine answer;
        without one, this says so plainly rather than guessing or
        silently falling back to unrelated domain work.
        """
        insight = self.brain.converse(task, notes)
        if not insight.is_useful or not insight.summary.strip():
            # When the request is really a registered capability this agent
            # is not authorised for, that is the true reason - said instead of
            # blaming a missing model, and naming who can do it.
            handler = getattr(self, "_capability_handler", None)
            unauthorised = getattr(handler, "unauthorised_reason", None)
            reason = unauthorised(self, task) if callable(unauthorised) else None
            if reason:
                return TaskResult.failure(task, self.name, reason)
            return TaskResult.failure(
                task,
                self.name,
                "I don't have a way to reason about that yet - no language "
                "model is configured for me right now, so I would rather "
                "say so than guess.",
            )
        return TaskResult.success(
            task,
            self.name,
            insight.summary,
            payload={"handled_by": "general_conversation", "brain": insight.source},
        )

    def __init__(
        self,
        brain: SpecialistBrain | None = None,
        memory: Memory | None = None,
        memory_policy: MemoryPolicy | None = None,
        tools: ToolSet | None = None,
        calendar: Calendar | None = None,
        # Untyped deliberately: this shared base class must import
        # nothing from src.providers (the identical purity
        # test_specialist_cognition.py's own TestNothingLeaksOut already
        # holds src/agents/brain.py to - "no provider is imported"), so
        # it cannot even name CelebrationProvider as a type. Any object
        # exposing a duck-typed ``select(...)`` matching that ABC's
        # contract works; a concrete specialist that wants the project's
        # own default constructs one at its own construction site (see
        # EmailAgent.__init__) and passes it in here.
        celebration=None,
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

        # Stage 12 follow-up: how this specialist's own genuine milestones
        # are presented, kept entirely separate from what they mean - see
        # src/providers/celebration.py's own docstring for the boundary.
        # Stored exactly as given - None is a real, honest state (no
        # celebration provider reaches this agent), the same shape
        # ``converse``/``retain`` already have elsewhere in this project.
        # Never defaulted here: this base class does not construct one,
        # for the identical "no provider import" reason ``celebration``'s
        # own parameter above is untyped. A concrete specialist that
        # wants the project's own default supplies it at its own
        # construction site (see EmailAgent.__init__).
        self.celebration_provider = celebration

    def celebration_reaction(self, milestone_type: str, *, occurrence: int = 0, intensity: str = "full"):
        """A presentation-level reaction for one of this specialist's own
        genuine milestones - never reasoning, never a write to Memory.

        Style comes from this agent's own declared Personality
        (``personality_for(self)``, duck-typed exactly as
        ``snapshot.py``'s own reader already is), never from a name
        checked here - a future specialist gets its own distinct
        celebration identity by declaring its own Personality, with
        nothing in this method changing.

        Returns None when no celebration provider was supplied to this
        agent - an honest absence, never a fabricated reaction.
        """
        if self.celebration_provider is None:
            return None
        from src.services.identity.personality import personality_for

        return self.celebration_provider.select(
            milestone_type=milestone_type,
            agent_key=self.name,
            personality=personality_for(self),
            intensity=intensity,
            occurrence=occurrence,
        )

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

    def retain(self, record: MemoryRecord) -> PolicyVerdict:
        """Offer one record to this specialist's own memory. The policy
        decides.

        The specialist's own counterpart to ``ManagerAgent.retain`` -
        **the one governed write path** for anything asked of this
        agent explicitly (Stage 12: "Arthur, record this for later"),
        as opposed to ``remember_experience``, which is this agent's own
        proposal about its own completed work. Both end at the same
        rule: the policy is the authority, and there is no path from
        here to the store that skips it.

        Writes into **this agent's own** store and no other - the same
        guarantee ``ManagerAgent.retain`` gives, restated here because a
        specialist's memory is a different instance with the identical
        rule, never a shared one.
        """
        verdict = self.memory_policy.evaluate(record)
        if verdict.allowed:
            self.memory.remember(record)
        return verdict

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
