"""The Manager's own capabilities, expressed as an ordinary agent.

This is what makes "can I handle this myself?" a real question rather than a
hardcoded branch. ManagerSkills implements BaseAgent and is registered in the
same registry as every specialist, so the existing capability router decides
self-vs-delegate by the same scoring it already uses. No parallel mechanism,
no hidden self-execution path, and no change to Router, Registry or Assessor.

Its area of responsibility is coordination: knowing what it and its specialists
can do, and reporting on that. It does not attempt a specialist's work - and it
is not obliged to delegate merely because a specialist exists.

**What it offers is a declaration and a handler, together.** IDENTITY.md says
what the author intends the Manager to take on; src/manager/capabilities.py
holds the handlers that can actually answer. The spec below offers only their
intersection, which cuts both ways: a word added to the document with nothing
behind it grants nothing, and a handler registered in code does not widen the
Manager until the document declares it too.

So editing IDENTITY.md still changes what the Manager takes on - within what
the code can do. That last clause is the part this class exists to enforce,
and it is the same rule specialist identity documents already live under.

Note what stays out of ManagerAgent: everything. Choosing a handler and running
it happens here, in an ordinary registered agent. The orchestrator still holds
no algorithm and grows no branch when a capability is added.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from src.agents.base_agent import BaseAgent
from src.agents.tools.toolset import ToolSet
from src.services.identity.personality import AgentPersonality, default_personality
from src.services.identity.voice import VoiceActivation, VoiceProfile, default_activation, default_voice
from src.core.contracts import AgentSpec, Task, TaskResult
from src.manager.activity_session import ActivityBrainOps, ActivitySessions
from src.manager.brain import BrainDecision, GovernanceBrainVerdict
from src.manager.capabilities import (
    CORRECT_MEMORY,
    MANAGER_CAPABILITIES,
    is_new_memory_statement,
    OPEN_CONVERSATION,
    CapabilityRequest,
    ManagerCapability,
    offered_capabilities,
    select_capability,
)
from src.manager.governance import GovernanceLog
from src.manager.recognition import Recognition, recognise
from src.providers.celebration import CelebrationProvider, CelebrationReaction
from src.providers.connection_resolver import ConnectionResolver
from src.providers.default_celebration_provider import DefaultCelebrationProvider
from src.control_center.interfaces import InterfaceDescriptor
from src.services.identity.identity import AgentIdentity
from src.services.identity.recording_session import RecordingSessionRegistry
from src.services.memory.day import Calendar
from src.services.memory.memory import MemoryRecord
from src.services.profile.profile import UserProfile
from src.services.profile.user_knowledge import UserKnowledge
from src.services.schedule.schedule import UserSchedule
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class ManagerSkills(BaseAgent):
    """Work the Manager performs directly."""

    def __init__(
        self,
        identity: AgentIdentity,
        roster: Callable[[], Sequence[BaseAgent]] | None = None,
        capabilities: dict[str, ManagerCapability] | None = None,
        user: UserProfile | None = None,
        user_knowledge: "UserKnowledge | None" = None,
        schedule: UserSchedule | None = None,
        tools: ToolSet | None = None,
        converse: "Callable[[Task, Sequence[str]], BrainDecision] | None" = None,
        analyze_image: "Callable[[bytes, str], BrainDecision] | None" = None,
        recall: "Callable[..., tuple[MemoryRecord, ...]] | None" = None,
        forget: "Callable[[str], bool] | None" = None,
        retain: "Callable[[MemoryRecord], object] | None" = None,
        celebration: "CelebrationProvider | None" = None,
        connections: "ConnectionResolver | None" = None,
        calendar: "Calendar | None" = None,
        history: "Callable[[], Sequence[str]] | None" = None,
        shared_files: "Callable[[], Sequence[str]] | None" = None,
        activity_sessions: "ActivitySessions | None" = None,
        activity_brain: "ActivityBrainOps | None" = None,
        discover_voices: "Callable[[], tuple[Any, ...]] | None" = None,
        interface: "InterfaceDescriptor | None" = None,
        recording_sessions: "RecordingSessionRegistry | None" = None,
        governance_log: "GovernanceLog | None" = None,
        assess_governance: "Callable[[Task, Sequence[str]], GovernanceBrainVerdict] | None" = None,
    ) -> None:
        """``roster`` supplies the currently registered agents.

        A callable rather than a registry reference: the Manager registers this
        object *into* the registry, so holding the registry directly would be a
        cycle. A late-bound callable keeps the dependency one-way and lets the
        roster answer reflect agents registered after construction.

        ``capabilities`` is the handler registry, defaulting to the one every
        deployment ships. Injectable for the same reason every other
        collaborator in this project is: so a test can register a capability
        without mutating global state that another test would then inherit.

        ``user`` is who the Manager is serving. Until now the profile passed
        straight through the composition root into the email specialist's
        analyzer and the Manager never saw it - so the orchestrator accountable
        for a mission had no idea who the mission was for. It arrives here at
        construction, which is per run and per heartbeat tick, so an edit to
        USER.md is picked up on the next tick exactly as it always has been.

        It is context, never authority. A UserProfile has no field that could
        be read as a permission, a tool, or a right to reach a specialist. What
        the user can do is a fact about the user; what the software can do is
        decided by handlers and wiring.
        """
        self._identity = identity
        self._roster = roster or (lambda: ())
        self._capabilities = (
            MANAGER_CAPABILITIES if capabilities is None else dict(capabilities)
        )
        self._user = user
        # Pre-Stage-9 Campaign 1: the canonical view of the person - USER.md
        # with everything taught in conversation layered over it, read live.
        # When present it is what ``user`` answers, so recognition and every
        # handler reason from what the person has actually said, not only
        # from what the document said at startup.
        self._user_knowledge = user_knowledge
        # When the user is around. A second document about the same person,
        # kept apart from the profile because one changes when someone learns
        # something and the other when someone takes a holiday. Per run, like
        # the profile beside it - a week is not a fact about the agent.
        #
        # Defaults to an unconfigured schedule rather than None, so a handler
        # can always ask and always get an honest "nothing is known" instead of
        # having to guard.
        self._schedule = schedule if schedule is not None else UserSchedule()
        # What the Manager is equipped to do. A separate axis from the brain
        # beside it: capability answers "what can I do", the brain answers
        # "how deeply can I reason about it", and neither implies the other.
        #
        # Empty by default, so a Manager nobody equipped can do nothing - the
        # same rule every specialist's ToolSet already lives under, and the
        # reason a missing tool is reported rather than improvised around.
        self._tools = tools if tools is not None else ToolSet()
        # A bound way to reach the Brain for an open-ended answer, never
        # the brain object itself - the same narrowing every other
        # collaborator here already lives under. None by default, so a
        # ManagerSkills built without one (most existing tests, and any
        # caller predating this parameter) behaves exactly as before:
        # open_conversation reports honestly that no reasoning capability
        # is available, rather than raising.
        self._converse = converse
        # Files/Documents completion: the identical bound-callable
        # narrowing ``converse`` already has, for real visual reasoning
        # over an image rather than an open-ended text answer. None by
        # default for the identical reason ``converse`` defaults to
        # None: image_analysis reports honestly that no vision-capable
        # reasoning is available, rather than raising.
        self._analyze_image = analyze_image
        # Stage 12: bound, narrow access to reading/removing this agent's
        # own governed memory - never the Memory object itself, the same
        # narrowing as everything above. None by default, for the same
        # reason converse defaults to None: an older caller or a bare
        # test construction gets honest refusals from the memory
        # capabilities, never a crash. Writing has no equivalent field
        # here - see CapabilityRequest's own docstring for why.
        self._recall = recall
        self._forget = forget
        # Stage 12 addition: bound to the real ManagerAgent.retain once
        # one exists - see bind_retain below. None at construction is
        # normal, not a defect: ManagerSkills is built as part of
        # constructing the ManagerAgent that owns retain, so the two
        # cannot both exist before each other. A handler that runs
        # before binding gets the same honest refusal a missing Brain
        # already produces.
        self._retain = retain
        # Stage 12 follow-up: how Alfred's own genuine milestones are
        # presented, entirely separate from what they mean - see
        # src/providers/celebration.py's own docstring for the boundary.
        # Defaults to the one real, generic implementation this project
        # ships; replacing it is a construction-time argument here, never
        # a change to milestone_recognition or to this class's own logic.
        self._celebration_provider = (
            celebration if celebration is not None else DefaultCelebrationProvider()
        )
        # Stage 16 / Phase 7 (Universal Connection Layer): read-only
        # access to every governed Connection this agent was equipped
        # with, handed to capability handlers as-is (never executed
        # here) - see CapabilityRequest's own docstring for why this
        # stays a read-mostly reference rather than a bound callable
        # like celebration above. Was a single Connection; is now a
        # ConnectionResolver (an open-ended, per-agent connection
        # context - see that module's own docstring), constructed once
        # per ManagerSkills instance exactly like governance_log
        # already is - what keeps two agents' own connections genuinely
        # independent rather than accidentally shared. None by default,
        # the same honest-refusal shape converse/recall/forget/retain
        # already have - a Manager nobody equipped with a connection can
        # attempt none.
        self._connections = connections
        # Foundation Repair #1: the same Calendar ManagerAgent itself
        # holds - how this Manager reckons days - threaded down so
        # current_datetime can answer "what day/date/month/year is it"
        # from the real, timezone-configured clock. None by default, the
        # same honest-fallback shape every other optional collaborator
        # here already has: the handler itself falls back to Calendar.utc()
        # when this is None, exactly as ManagerAgent falls back for itself.
        self._calendar = calendar
        # Natural Conversation: a bound way to read the recent
        # conversation, never the Conversation object itself - the same
        # narrowing every other collaborator here already lives under.
        # None at construction (the same reason retain is): the real
        # Conversation is built from the Manager this ManagerSkills
        # belongs to, so it cannot exist yet at this point - see
        # bind_conversation_history below, the same closing-the-loop step
        # bind_retain already performs. A handler that reads this before
        # it is bound gets an honest empty history, never a crash.
        self._history = history
        # Pre-Stage-9 Campaign 3: the ids of files shared in this conversation,
        # bound once the room exists - see bind_shared_files.
        self._shared_files = shared_files
        # Final Pre-Phase Interaction Completion: where a pending
        # clarification question or an in-progress guided activity lives
        # while this Manager is running - see activity_session.py's own
        # docstring for why this is neither Memory nor a second Brain.
        # Handed through whole, the same "already narrow enough" shape
        # ConnectionResolver above and ToolSet already have - constructed once
        # here (never per turn) so state genuinely survives from one
        # turn to the next, the identical lifetime PresenceRegistry
        # already has at the Runtime level.
        self._activity_sessions = (
            activity_sessions if activity_sessions is not None else ActivitySessions()
        )
        # None by default, the same honest-refusal shape converse/recall/
        # forget/retain already have: a Manager with no Brain wired for
        # this cannot judge ambiguity or run a guided activity, and
        # open_conversation treats that exactly like "no reasoning
        # capability available" rather than crashing.
        self._activity_brain = activity_brain
        # Agent-autonomous voice selection checkpoint: a bound, live
        # discovery call - never the speech adapter itself, the same
        # narrowing every other collaborator here already lives under.
        # None by default, the same honest-refusal shape every sibling
        # collaborator already has: select_voice reports honestly that
        # no voice options are available, rather than crashing.
        self._discover_voices = discover_voices
        # What this agent offers as a screen, asked the same way
        # EmailAgent already answers it (see src/agents/email_agent.py's
        # own ``interface`` property) - a declaration, not a grant, per
        # InterfaceDescriptor's own docstring. None by default, the same
        # honest-fallback shape every collaborator above already has:
        # describe_interface() (src/control_center/interfaces.py) treats
        # a ManagerSkills with no descriptor exactly like any other agent
        # that declares nothing, and falls back to the conversational
        # default rather than crashing. This class stays generic - it is
        # composition.py's build_manager() that actually decides what
        # Alfred declares, the same place EmailAgent's own hardcoded
        # descriptor lives, one call site per agent rather than a branch
        # in here.
        self._interface = interface
        # Real Audio Recording/Capture checkpoint: the one shared,
        # live "is this agent recording right now" signal - never a
        # second one this class tracks itself, the identical narrowing
        # every other Runtime-level collaborator here already has. None
        # by default, the same honest-refusal shape converse/recall/
        # forget/retain already have: start_recording/stop_recording
        # report honestly that no recording capability is available,
        # rather than crashing.
        self._recording_sessions = recording_sessions
        # Context-Aware Agent Capability & Safety Governance: the real,
        # per-agent record of recent governance decisions - the
        # identical "constructed once here, never per turn, genuinely
        # in-process" lifetime ``ActivitySessions`` already has just
        # above, and for the same reason: two agents' own governance
        # history must stay genuinely independent, never a shared
        # module-level singleton. Control Center reads this same object
        # (ManagerSkills.governance_log below) - never a second,
        # disconnected registry.
        self._governance_log = governance_log if governance_log is not None else GovernanceLog()
        # A bound way to reach the Brain's own narrow governance
        # judgement - never the brain object itself, the identical
        # narrowing ``converse``/``activity_brain`` already live under.
        # None by default, the same honest-refusal shape every sibling
        # collaborator already has: governance.assess() itself already
        # degrades honestly (CLARIFY, never a guess) when this is None.
        self._assess_governance = assess_governance
        # Pre-Stage-9 Campaign 2: which capability execute() actually chose
        # for the most recent task - what the Control Center trail reports,
        # rather than re-deriving a choice after the session state that
        # decided it has already changed.
        self._last_selected: "tuple[str, str] | None" = None

    @property
    def interface(self) -> "InterfaceDescriptor | None":
        return self._interface

    @property
    def governance_log(self) -> "GovernanceLog":
        """The real, per-agent record of recent governance decisions -
        read by Control Center, written by every capability that calls
        through ``src/manager/governance.py::assess`` for this agent.
        Never empty-by-construction-elsewhere: always the same
        ``GovernanceLog`` instance this agent's own capability handlers
        write to."""
        return self._governance_log

    def bind_conversation_history(self, history: "Callable[[], Sequence[str]]") -> None:
        """Wire the real conversation's recent history in, once the room
        that holds it exists.

        Construction order makes this necessary rather than a choice,
        exactly as it does for bind_retain: the real Conversation is
        built from the Manager this ManagerSkills belongs to
        (scripts/control_center.py's build(), right after build_manager
        returns), so it does not exist yet when ManagerSkills itself is
        constructed. Called at most once, with a real bound accessor,
        never a second implementation of "what was recently said."
        """
        self._history = history

    def bind_shared_files(self, shared_files: "Callable[[], Sequence[str]]") -> None:
        """Wire in the room's own record of which files were shared, once the
        room exists - the identical construction-order reason
        bind_conversation_history has."""
        self._shared_files = shared_files

    def bind_discover_voices(self, discover_voices: "Callable[[], tuple[Any, ...]]") -> None:
        """Wire the real, live voice-discovery call in, once the speech
        channel that can answer it exists.

        Construction order makes this necessary rather than a choice,
        exactly as it does for bind_retain/bind_conversation_history: the
        real Channel/speech adapter is built in scripts/control_center.py's
        build(), after build_manager() returns, so it does not exist yet
        when ManagerSkills itself is constructed. Called at most once,
        with a real bound accessor - never a persisted catalog, and
        never a second discovery mechanism.
        """
        self._discover_voices = discover_voices

    def celebration_reaction(
        self, milestone_type: str, *, occurrence: int = 0, intensity: str = "full"
    ) -> "CelebrationReaction":
        """A presentation-level reaction for one of Alfred's own genuine
        milestones - never reasoning, never a write to Memory. Style
        comes from Alfred's own declared Personality
        (``self.personality``, already read from IDENTITY.md above),
        never from a name checked here.
        """
        from src.services.identity.personality import personality_for

        return self._celebration_provider.select(
            milestone_type=milestone_type,
            agent_key=self.name,
            personality=personality_for(self),
            intensity=intensity,
            occurrence=occurrence,
        )

    @property
    def spec(self) -> AgentSpec:
        """Declared and handled, never merely declared.

        AgentSpec is what the router scores against, so this is where the
        intersection has to be applied - offering a capability nothing can
        answer would route work here that would then be answered by the
        fallback, which is the silent-wrong-behaviour outcome this project
        refuses everywhere else.
        """
        return AgentSpec(
            name=self._identity.name,
            description=(
                self._identity.description
                or self._identity.role
                or f"{self._identity.name} agent"
            ),
            capabilities=offered_capabilities(
                self._identity.capabilities, self._capabilities
            ),
        )

    @property
    def capabilities(self) -> dict[str, ManagerCapability]:
        """The handler registry this instance answers from."""
        return dict(self._capabilities)

    def claims(self, task: Task) -> bool:
        """Whether one of this Manager's own capabilities recognises
        ``task``'s *shape* - an explicit ``claims=`` match, never mere
        word-overlap scoring (see ``ManagerCapability``'s own docstring
        for that distinction, and ``select_capability``'s own "asked
        first" pass, which this mirrors).

        Stage 15's own routing audit found the defect this closes: the
        *outer* router and ``collaboration_candidates`` both decide by
        raw word overlap against a specialist's declared capabilities,
        which has no way to know that Alfred's own capability system
        already recognised a request confidently by its shape ("Read my
        recent messages" is a communication request, however incidental
        its overlap with Arthur's own, legitimate "messages" - email -
        capability word happens to be). Every ``claims=``-bearing
        capability today (forget, recall, milestones, current
        information, participant invitation, communication, small talk)
        is a self-contained, deterministic operation of Alfred's own -
        none of them benefits from specialist collaboration - so when
        one matches, the answer is unambiguous and generic: this request
        is Alfred's, regardless of what word a specialist also happens
        to declare. No word, name or specialist is named here or in
        either caller (``ManagerAgent.decide``,
        ``collaboration_entry.collaboration_candidates``); a future
        claims-bearing capability is covered automatically.

        A pending clarification or an active guided activity claims
        every remark unconditionally, for the identical reason
        ``execute()`` gives its own matching check: a specialist's
        incidental word overlap with an in-progress session's own reply
        must never be able to route the whole task away from Alfred and
        straight to a specialist, silently abandoning the session before
        it ever reaches ``open_conversation``'s own handling of it.
        """
        if (
            self._activity_sessions.pending_clarification(self.name) is not None
            or self._activity_sessions.activity(self.name) is not None
            or self._activity_sessions.pending_confirmation(self.name) is not None
        ):
            return True
        # Pre-Stage-9 Campaign 2: a reply to an open "which one did you
        # mean?" correction question is this Manager's, whatever a specialist
        # also happens to declare.
        pending_correction = self._activity_sessions.pending_correction(self.name)
        if pending_correction is not None and CORRECT_MEMORY.name in self._capabilities and not is_new_memory_statement(
            task.description
        ) and (
            pending_correction.select_option(task.description) is not None
            or pending_correction.is_cancel(task.description)
        ):
            return True
        return any(
            capability.claims is not None
            and capability.claims(task, tuple(a.name for a in self._roster()))
            for capability in self._capabilities.values()
        )

    @property
    def voice(self) -> VoiceProfile:
        """How the Manager sounds, from the Manager's own identity document.

        Published rather than configured elsewhere, so a channel about to speak
        asks *this agent* what it sounds like. Falls back to a voice keyed on
        this agent's own name when the document declares none - never to a
        shared default, because two agents that both declared nothing would
        then be given one voice, which is the failure the whole layer exists to
        prevent.
        """
        declared = self._identity.voice
        return declared if declared is not None else default_voice(self._identity.name)

    @property
    def activation(self) -> VoiceActivation:
        """How the Manager is addressed by voice, from the Manager's own
        identity document.

        Published for the same reason voice above is: a channel deciding
        whether a heard utterance is meant for this agent asks *this
        agent*, not a hardcoded phrase list. Falls back to a phrase list
        derived from this agent's own name when the document declares none
        - never to a shared default, for the same reason voice does not.
        """
        declared = self._identity.activation
        return declared if declared is not None else default_activation(self._identity.name)

    @property
    def personality(self) -> AgentPersonality:
        """How the Manager tends to communicate, from the Manager's own
        identity document.

        Published for the same reason voice/activation above are: whatever
        composes a reply on this agent's behalf asks *this agent*, not a
        hardcoded style. Falls back to an unconfigured, name-keyed
        personality when the document declares none - never to a shared
        default, for the same reason voice/activation do not.
        """
        declared = self._identity.personality
        return declared if declared is not None else default_personality(self._identity.name)

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    def bind_retain(self, retain: "Callable[[MemoryRecord], object]") -> None:
        """Wire this Manager's real, governed write path in, once it
        exists.

        Construction order makes this necessary rather than a choice:
        composition.py builds a ManagerSkills to hand to ManagerAgent's
        own constructor, so ``manager.retain`` does not exist yet at the
        point ManagerSkills is built. This is the one, explicit,
        post-construction step that closes the loop - called exactly
        once, with the real bound method, never a second
        implementation of it (see CapabilityRequest.retain's own
        docstring).
        """
        self._retain = retain

    def recognise(self, task: Task) -> Recognition:
        """Which of the user's declared abilities this work names.

        Alfred's own faculty, not a specialist's and not a second brain. It
        reasons about nothing: it reads the profile he was built with and
        reports what the task named, deterministically and identically in
        Full Brain and Half Brain.

        The result is evidence. ``Recognition.notes()`` renders it as inert
        strings for the ``deliberate(task, notes=...)`` channel Alfred's Brain
        has always accepted, so nothing about how he reasons changes - he
        simply now has something true to reason from.
        """
        return recognise(task, self.user)

    @property
    def tools(self) -> ToolSet:
        """What this Manager may actually invoke.

        Immutable and un-growable by construction: a ToolSet has no add(), so
        nothing the Manager does can widen what it may do next.
        """
        return self._tools

    @property
    def schedule(self) -> UserSchedule:
        """The user's week, or an unconfigured schedule if none was supplied.

        Deliberately not merged into ``user``: what someone can do and when
        they are around are different facts, arrive from different documents,
        and change for different reasons.
        """
        return self._schedule

    @property
    def user(self) -> UserProfile | None:
        """The profile this Manager was built with, or None if there is none.

        Deliberately not merged into ``identity``: one says who the agent is,
        the other says who it serves, and they have different lifetimes -
        identity is read once with the runtime, the profile is re-read every
        run because the person keeps changing.
        """
        if self._user_knowledge is not None:
            return self._user_knowledge.profile()
        return self._user

    @property
    def user_knowledge(self) -> "UserKnowledge | None":
        """The canonical view of the person this Manager serves, or None."""
        return self._user_knowledge

    def selected_capability_for(self, task_id: str) -> "str | None":
        """The capability this Manager actually chose for ``task_id``, or None."""
        if self._last_selected is not None and self._last_selected[0] == task_id:
            return self._last_selected[1]
        return None

    def _answers_pending_correction(self, text: str) -> bool:
        """Whether ``text`` answers this agent's open "which one did you
        mean?" correction question. A new statement drops the question and is
        handled on its own; any other non-answer drops it too."""
        if self._activity_sessions.pending_correction(self.name) is None:
            return False
        if CORRECT_MEMORY.name not in self._capabilities or is_new_memory_statement(text):
            self._activity_sessions.clear_correction(self.name)
            return False
        return self._activity_sessions.correction_reply_is_for_pending(self.name, text)

    def execute(self, task: Task) -> TaskResult:
        """Pick the handler this task is asking for, and run it.

        Selection is the same scoring the router used one level up: match the
        task's words against what each handler declares, strongest wins. With
        a single handler registered - which is what this project ships today -
        the answer is always that handler, and the behaviour is exactly what
        it was before a registry existed.

        No branch here grows when a capability is added. That is the point:
        this method reads the same with one handler or with twenty.
        """
        log = task_logger(logger, task.id)
        # A pending clarification or an active guided activity outranks
        # ordinary claims/scoring entirely - found during a full-ecosystem
        # audit: an in-progress session's own reply ("that's enough for
        # now" to end a quiz, a bare candidate value to resolve a
        # clarification) can incidentally match an unrelated capability's
        # own shape-based claim (live_information's "for now" among
        # them), which would silently derail the session by answering as
        # though nothing were in progress. Only open_conversation's own
        # guided_interaction_response - checked first, before any
        # ordinary reply is composed - knows how to continue, grade or
        # honestly end a session, so while one is active for this agent,
        # every remark goes there, unconditionally, exactly the same
        # "this settles it outright" priority `addressed` and `claims`
        # already have one level up in `ManagerAgent.decide`.
        if (
            self._activity_sessions.pending_clarification(self.name) is not None
            or self._activity_sessions.activity(self.name) is not None
            or self._activity_sessions.pending_confirmation(self.name) is not None
        ):
            capability = OPEN_CONVERSATION
        elif self._answers_pending_correction(task.description):
            # Pre-Stage-9 Campaign 2: the person is answering "which one did
            # you mean?" - resolved deterministically, with or without a
            # Brain. Any other reply has already dropped the question.
            capability = self._capabilities[CORRECT_MEMORY.name]
        else:
            # Who is registered right now, so a name used as the object of
            # an invitation is read as an identity rather than as a kind of
            # work. Read per call: an agent registered after construction
            # counts.
            capability = select_capability(
                task,
                self._capabilities,
                participants=[agent.name for agent in self._roster()],
            )
        log.debug(
            "manager handling '%s' itself via '%s'",
            task.description,
            capability.name,
        )

        self._last_selected = (task.id, capability.name)
        return capability.handle(
            CapabilityRequest(
                task=task,
                identity=self._identity,
                roster=self._roster,
                own_name=self.name,
                # What this Manager actually offers - the intersection, not the
                # raw declaration - so a handler reporting "what can you do"
                # cannot advertise a word nothing is able to answer.
                own_capabilities=tuple(sorted(self.spec.capabilities)),
                # Handed to every handler as read-only context. A handler that
                # has no use for it simply does not look.
                user=self.user,
                # Computed once per execution, before any handler runs, so two
                # handlers cannot disagree about what this task named.
                recognition=self.recognise(task),
                schedule=self._schedule,
                tools=self._tools,
                converse=self._converse,
                analyze_image=self._analyze_image,
                recall=self._recall,
                forget=self._forget,
                retain=self._retain,
                celebration=self.celebration_reaction,
                connections=self._connections,
                calendar=self._calendar,
                history=self._history,
                shared_files=self._shared_files,
                activity_sessions=self._activity_sessions,
                activity_brain=self._activity_brain,
                discover_voices=self._discover_voices,
                recording_sessions=self._recording_sessions,
                governance_log=self._governance_log,
                assess_governance=self._assess_governance,
            )
        )
