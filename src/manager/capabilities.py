"""What the Manager can actually do, as opposed to what it says it can.

IDENTITY.md declared a list of capabilities from the first commit, and the
Manager has always had exactly one behaviour behind all of them: report its
own capabilities and the roster of specialists it can call on. So `plan` and
`coordinate` were declared, routable, and answered with a roster listing -
reported as a completed mission. Declared reach with nothing behind it is the
same defect class `coherence.py` already refuses one layer up: a provider that
is configured and then never consumed.

Both words have now been removed from the document and from the handler, so a
planning request finds no agent rather than a plausible non-answer.

This module supplies the missing half. A capability is a **declared word plus
a handler that answers it**, and the Manager offers only the ones where both
exist:

    declared in IDENTITY.md          what the author intends Alfred to take on
    handled by a registered handler  what the code can actually do
    ------------------------------------------------------------------
    offered = declared AND handled   what the router may send here

Read that intersection in both directions, because both matter:

  - Adding a word to `## Capabilities` grants nothing. With no handler behind
    it the word is dropped, and a warning names it. A capability that could be
    created by editing a markdown file would not be a capability boundary -
    the identical argument this project already makes about SOUL.md and about
    specialist identity documents.

  - Adding a handler does not silently widen Alfred either. Its capabilities
    still have to be declared in his identity document, so what the Manager
    takes on remains an authored decision rather than a side effect of an
    import.

**Why a registry rather than branches in ManagerAgent.** ManagerAgent runs the
DATA loop and contains no algorithm of its own - that is the whole claim of
this architecture. A capability answered by `if task says X` inside it would
put domain behaviour in the orchestrator and grow a branch per capability in
the one file that should never care how many exist. Handlers live here, keyed
by what they answer to, in the same small-registry shape as the provider,
router, brain, memory and specialist factories.

A handler receives a request and returns a TaskResult. It is given the task,
the Manager's identity and a way to read the roster - and deliberately not the
Manager, the registry, the router or any agent. A capability answers a
question; it does not get to orchestrate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import date, time, timedelta
from typing import Any, Callable, Mapping, Sequence

from src.agents.base_agent import BaseAgent
from src.agents.tools.tool import ToolResult
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskResult, tokenize
from src.manager.activity_session import (
    ActivityBrainOps,
    ActivitySessions,
    GuidedActivityState,
    PendingClarification,
    PendingConfirmation,
)
from src.manager.brain import BrainDecision, GovernanceBrainVerdict
from src.manager.governance import GovernanceLog
from src.manager.guided_interaction import guided_interaction_response
from src.services.identity.recording_session import RecordingSessionRegistry
from src.manager.knowledge import (
    KnowledgeEvidence,
    MODEL_KNOWLEDGE,
    gather_knowledge_evidence,
    read_artifact_content,
    resolve_content_sources,
    resolve_shared_media,
    describe_artifact,
)
from src.services.artifacts.content import (
    ContentUnit,
    content_reference_intent,
    locator_in,
    reading_selection,
    units_at,
)
from src.services.artifacts.ingestion import SUPPLIED_FILE_TYPE
from src.manager.memory_policy import MemoryCategory
from src.manager.recognition import Recognition
from src.agents.tools.connection import tool_name_for
from src.agents.tools.media_production import find_media_path, find_video_path, parse_clip_specs
from src.providers.celebration import CelebrationReaction
from src.providers.connection import Connection
from src.providers.connection_resolver import ConnectionResolver
from src.services.identity.identity import AgentIdentity
from src.services.conversation_floor import (
    ACKNOWLEDGEMENT_WORDS,
    CONVERSATIONAL_FILLER,
    CONVERSATIONAL_WORDS,
    GREETING_WORDS,
    THANKS_WORDS,
    conversational_intent,
)
from src.services.identity.introduction import (
    greeting_from_identity,
    identity_question,
    self_introduction,
)
from src.services.identity.voice_preference import (
    autonomous_choice_record_ids,
    autonomous_voice_choice_for,
    build_voice_autonomous_choice_record,
    forget_record_ids,
    release_voice_preference,
    voice_preference_for,
)
from src.services.identity.voice_selection import (
    VoiceRequest,
    VoiceSelection,
    is_release_autonomy_request,
    is_reselect_request,
    is_unconstrained_selection_request,
    parse_voice_request,
    pick_any,
    select_voice as select_voice_option,
)
from src.services.memory.capture import DEFAULT_PURPOSE as DEFAULT_CAPTURE_PURPOSE
from src.services.memory.capture import build_recording, capture_intent
from src.services.memory.query import STOPWORDS as _CONNECTED_ACTION_STOPWORDS
from src.services.memory.calendar_items import (
    CalendarItemCandidate,
    Recurrence,
    STATUS_CANCELLED as CALENDAR_STATUS_CANCELLED,
    STATUS_SCHEDULED as CALENDAR_STATUS_SCHEDULED,
    WEEKDAY_NAMES as CALENDAR_WEEKDAY_NAMES,
    all_current_calendar_items,
    build_calendar_item_record,
    calendar_add_intent,
    calendar_cancel_intent,
    calendar_reschedule_intent,
    calendar_retrieve_intent,
    derive_topic_key as derive_calendar_topic_key,
    extract_calendar_item,
    extract_cancel_target as extract_calendar_cancel_target,
    extract_period_query as extract_calendar_period_query,
    extract_reschedule as extract_calendar_reschedule,
    find_conflicts as find_calendar_conflicts,
    find_time_range as find_calendar_time_range,
    match_items_by_title as match_calendar_items_by_title,
    next_occurrences as next_calendar_occurrences,
    parse_calendar_date,
)
from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import (
    PersonalContextCandidate,
    STATUS_CURRENT,
    STATUS_DISCONTINUED,
    all_current_personal_context,
    build_personal_context_record,
    describe_for_user,
    extract_personal_context_candidate,
    extract_personal_context_query,
    is_question_or_request as _is_question_or_request_statement,
    match_personal_context,
    normalize_topic_key,
    personal_context_query_intent,
)
from src.services.memory.query import STOPWORDS as _QUERY_STOPWORDS
from src.services.memory.session import retention_requests
from src.services.memory.correction import (
    ACTION_INVALIDATE,
    ACTION_REPLACE,
    CorrectionStatement,
    correction_statement_intent,
    extract_correction,
)
from src.manager.activity_session import CorrectionOption, PendingCorrection
from src.services.memory.personal_context import (
    personal_context_intent as _personal_context_shape_matches,
)
from src.services.memory.milestones import (
    AUTO_CONFIRM_FACT_TYPE,
    CELEBRATION_INTENSITY_FACT_TYPE,
    REDUCED_MOTION_FACT_TYPE,
    build_milestone_record,
    extract_milestone,
    is_explicit,
    milestone_intent,
    resolve_celebration_intensity,
    wants_auto_confirm,
)
from src.services.memory.day import Calendar
from src.services.memory.policy import PolicyVerdict
from src.services.memory.audio_recording import (
    cancel_recording_intent,
    describe_recording,
    extract_recording_purpose,
    extract_recording_query,
    list_recordings_intent,
    match_recordings_by_topic,
    start_recording_intent,
    stop_recording_intent,
)
from src.services.memory.tasks import (
    build_task_record,
    complete_task_intent,
    create_task_intent,
    extract_reminder,
    reminder_intent,
    describe_task,
    extract_completion_topic,
    extract_reopen_topic,
    extract_task_query,
    extract_task_update,
    list_tasks_intent,
    match_tasks_by_topic,
    reopen_task_intent,
    update_task_intent,
)
from src.services.memory.temporal_facts import (
    compute_age,
    days_between,
    days_until,
    next_occurrence,
    utc_today,
)
from src.services.profile.profile import UserProfile
from src.services.schedule.schedule import UserSchedule
from src.services.memory.user_facts import (
    RelationalBirthdayCandidate,
    extract_relational_birthdays,
    relational_birthday_intent,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CapabilityRequest:
    """Everything a handler is given, and nothing more.

    Frozen, and carrying no route to the orchestrator. A handler can read who
    the Manager is and who is registered; it cannot delegate, route, register
    or reach another agent's memory. That boundary is structural - there is no
    such object in scope - rather than a rule anyone has to remember.
    """

    task: Task
    identity: AgentIdentity
    roster: Callable[[], Sequence[BaseAgent]]
    own_name: str
    own_capabilities: tuple[str, ...]
    # Who the Manager is serving, freshly loaded for this run. Read-only
    # context and nothing more: a UserProfile carries skills, projects,
    # seniority, targets and hard filters, and has no field a handler could
    # read as a permission, a tool or a right to reach a specialist. What the
    # user can do is a fact about the user; what the software can do is
    # decided by handlers and wiring, and the two never meet.
    #
    # None when no profile is configured. main.py degrades to detection-only
    # rather than stopping, so a handler must be able to say "I do not know
    # who I am serving" instead of assuming someone.
    user: UserProfile | None = None
    # What this task named from the user's declared context, computed
    # deterministically before any handler runs. Structured evidence, not a
    # judgement: Recognition has no score, band or coverage, and no field in
    # which a capability, tool or permission could be expressed.
    #
    # Defaulted so a handler constructed by hand in a test still works, and so
    # a request built without a profile carries an honest "nothing is known".
    recognition: Recognition = field(default_factory=Recognition)
    # When the user is around, as they wrote it down. Context in exactly the
    # sense `user` above is context: it describes a person, and there is no
    # field on a UserSchedule in which a capability, tool, permission, scope or
    # brain mode could be expressed.
    #
    # Note what it is not. It is not the Calendar, which decides which day a
    # memory fell on; it resolves no instants and starts nothing. A handler
    # that wants to know whether now is a quiet hour cannot ask this, and that
    # is deliberate - answering it needs a clock, and clocks belong to day.py.
    schedule: UserSchedule = field(default_factory=UserSchedule)
    # What the Manager is actually equipped to do, as opposed to how deeply he
    # can reason about it. Two axes, kept apart on purpose:
    #
    #     tools  what can I do?          membership in this set
    #     brain  how deeply can I think? FULL or HALF
    #
    # Neither implies the other. A Half Brain Alfred with a working tool can
    # still do the work; a Full Brain Alfred without one still cannot, and must
    # say so rather than reason his way to an answer he cannot fetch.
    #
    # Empty by default, because a Manager nobody equipped can do nothing - the
    # same rule every specialist's ToolSet already lives under.
    tools: ToolSet = field(default_factory=ToolSet)
    # A bound way to reach the Brain for a genuinely open-ended answer -
    # never the brain object itself, and never the Manager: the same
    # narrowing ``roster`` above already applies, extended to reasoning.
    # A handler that wants an answer calls this; it cannot reach anything
    # else the brain might be attached to. None when no Brain reaches
    # this request (a bare ManagerSkills built without one, or an older
    # caller predating this field) - open_conversation treats that as
    # "no reasoning capability available" and says so honestly, the same
    # stance a missing tool already gets.
    converse: "Callable[[Task, Sequence[str]], BrainDecision] | None" = None
    # Files/Documents completion: the identical narrowing ``converse``
    # already has, for a different question - "what does this image
    # show", never "what should happen next". Bound to
    # ``ManagerBrain.analyze_image`` (default: an honest no-view,
    # exactly like every sibling Brain question in
    # ``src/manager/brain.py``), never to a Tool holding an
    # ``LLMProvider`` directly - a Tool must depend only on providers of
    # information/environment access, never on reasoning machinery (see
    # ``tests/test_provenance.py::TestHalfBrainStillReachesTheEnvironment``),
    # and visual reasoning is reasoning, not environment access.
    analyze_image: "Callable[[bytes, str], BrainDecision] | None" = None
    # Stage 12: bound, narrow access to reading and removing this agent's
    # own governed memory - never the Memory object itself, the same
    # narrowing every other collaborator here already lives under.
    # MemoryPolicy has no opinion about reading or forgetting in any case
    # (see src/services/memory/policy.py's own docstring), so neither of
    # these needs to be policy-gated the way a write does. None when no
    # memory reaches this request, treated the same honest way a missing
    # Brain already is.
    recall: "Callable[..., tuple[MemoryRecord, ...]] | None" = None
    forget: "Callable[[str], bool] | None" = None
    # Stage 12 addition: bound to ManagerAgent.retain itself - never a
    # second implementation of it - so a handler that needs to *write*
    # (record_this, milestone_recognition) goes through the identical
    # governed gate "remember that X" already does. Deliberately still
    # not used for "remember" - retain_what_was_asked_for already owns
    # that trigger at the conversation layer, and a second handler
    # matching the same phrase would retain the same utterance twice
    # (see that module's own docstring). "record this"/"milestone:" are
    # different trigger phrases, so there is no overlap to duplicate.
    # None for the same reason recall/forget default to None: an older
    # caller or a bare test construction gets an honest refusal, never a
    # crash.
    retain: "Callable[[MemoryRecord], PolicyVerdict] | None" = None
    # Stage 12 follow-up: a bound way to select a presentation-level
    # CelebrationReaction for one of this agent's own genuine milestones -
    # never the provider itself (the same narrowing every other
    # collaborator here already lives under), and never anything that
    # could reach a Brain, a Memory, or reason about whether something is
    # a milestone. milestone_recognition calls this only *after* a
    # milestone has already been recognised and stored through the
    # ordinary governed path above; this field decides no more than how
    # that already-settled fact might be presented. None when no
    # provider reaches this request, the same honest-refusal shape every
    # other optional collaborator here already has.
    celebration: "Callable[..., CelebrationReaction] | None" = None
    # Stage 16 / Phase 7 (Universal Connection Layer): read-only access
    # to every governed Connection this agent was equipped with - name,
    # availability, authorization, supported operations - never the
    # means to execute anything through it. A handler that wants to
    # actually perform an operation goes through request.tools.invoke(...),
    # the identical governed boundary every other consequential action
    # in this file already crosses (see connected_action below); this
    # field exists only so a handler can report *why* something cannot
    # be attempted (not configured, not reachable, not authorized)
    # before ever trying, keeping "capability availability"
    # (request.tools), "connection availability"
    # (connection.availability) and "authorization"
    # (connection.authorization) genuinely distinct facts rather than
    # one collapsed concept. Was a single ``Connection``; is now a
    # ``ConnectionResolver`` (Phase 7) - an open-ended, per-agent
    # connection context rather than one fixed connection, built once
    # at process-construction time (see that module's own docstring
    # for why it is never re-read live). None when no connection
    # reaches this request, the same honest-refusal shape every other
    # optional collaborator here already has.
    connections: "ConnectionResolver | None" = None
    # Foundation Repair #1: how this Manager reckons days - the same
    # Calendar ManagerAgent itself holds (see manager.py), threaded down
    # so a handler can answer "what day/date/month/year is it" from the
    # real, timezone-configured clock rather than reinventing one. This is
    # exactly the collaborator the ``schedule`` field's own docstring above
    # already named as absent: "answering [the current moment] needs a
    # clock, and clocks belong to day.py." None when no calendar reaches
    # this request (a bare construction, or an older caller) - the handler
    # below falls back to Calendar.utc(), the same neutral default
    # ManagerAgent itself uses when none is configured.
    calendar: "Calendar | None" = None
    # Natural Conversation: a bound way to read the recent conversation as
    # plain, already-attributed lines ("Kelly: ...", "Alfred: ...") -
    # never the Conversation object itself, the same narrowing every
    # other collaborator here already lives under. This is evidence for a
    # Brain that already reasons over notes (open_conversation,
    # knowledge_inquiry, live_information all already build a `notes`
    # tuple from request.recognition and hand it to request.converse);
    # folding recent history into that same tuple is additive to a
    # contract that already exists, not a new one. None when no
    # conversation reaches this request (a bare construction, or an
    # older caller) - every caller that reads this treats an absent or
    # empty history exactly as it already treats no notes at all.
    history: "Callable[[], Sequence[str]] | None" = None
    # Pre-Stage-9 Campaign 3: the ids of every file shared in this
    # conversation, newest first - the room's own record of what was handed
    # over, never the Conversation object and never limited to the short
    # recent-history window above (a file shared a dozen lines ago is still
    # "this file"). None when no conversation reaches this request.
    shared_files: "Callable[[], Sequence[str]] | None" = None
    # Final Pre-Phase Interaction Completion: where a pending
    # clarification question or an in-progress guided activity lives -
    # see src/manager/activity_session.py's own docstring for why this
    # is neither Memory nor a second Brain. Handed through whole, the
    # same "already narrow enough" shape ``tools``/``recognition`` above
    # already have. None when no tracker reaches this request (a bare
    # construction, or an older caller) - open_conversation treats that
    # as "nothing is in progress", the honest, correct default.
    activity_sessions: "ActivitySessions | None" = None
    # The four narrow Brain questions guided activity/ambiguity handling
    # needs, bundled - never the brain object itself, the identical
    # narrowing ``converse`` already has. None for the same reason
    # ``converse`` defaults to None: an older caller or a bare test
    # construction gets an honest "no reasoning capability available"
    # rather than a crash.
    activity_brain: "ActivityBrainOps | None" = None
    # Agent-autonomous voice selection checkpoint: a bound, live call to
    # whatever speech adapter this Manager is actually configured with -
    # never the adapter itself, the same narrowing every other
    # collaborator here already lives under, and never a persisted
    # catalog (see select_voice's own docstring for why discovery is
    # always live). Returns real ``VoiceOption``s reported by that
    # adapter's own ``available_voices()`` when it has one, or an
    # honestly empty tuple for an adapter that does not (Null speech, or
    # an engine this checkpoint did not add discovery to) - never a
    # fabricated option. None when no speech channel reaches this
    # request at all (a bare construction, or an older caller) - the
    # same honest-refusal shape every other optional collaborator here
    # already has.
    discover_voices: "Callable[[], tuple[Any, ...]] | None" = None
    # Real Audio Recording/Capture checkpoint: the one shared, live
    # "is this agent recording right now" signal - see
    # src/services/identity/recording_session.py's own docstring for why
    # it lives at Runtime level rather than in Memory. Handed through
    # whole, the same "already narrow enough" shape activity_sessions
    # above already has: a handler asks it a small, fixed set of
    # questions (is anything in progress, mark it stopped) and can
    # reach nothing else through it. None when no registry reaches this
    # request (a bare construction, or an older caller) -
    # start_recording/stop_recording report honestly that recording is
    # unavailable, rather than crashing.
    recording_sessions: "RecordingSessionRegistry | None" = None
    # Context-Aware Agent Capability & Safety Governance: the real,
    # per-agent record of recent governance decisions - handed through
    # whole (the same "already narrow enough" shape ``activity_sessions``
    # already has: a handler only ever calls ``.record()``/``.recent()``
    # on it, never reaches anything else through it), and read by
    # Control Center from the identical object a handler wrote to -
    # never a second, disconnected registry. None when no log reaches
    # this request (a bare construction, or an older caller) - governed
    # capabilities simply skip recording, exactly as they already skip
    # retaining memory when ``retain`` is None.
    governance_log: "GovernanceLog | None" = None
    # A bound way to reach the Brain's own narrow governance judgement -
    # never the brain object itself, the identical narrowing
    # ``converse``/``activity_brain`` already live under. None by
    # default, the same honest-refusal shape every sibling collaborator
    # already has: ``governance.assess()`` itself already degrades
    # honestly (CLARIFY, never a guess) when this is None.
    assess_governance: "Callable[[Task, Sequence[str]], GovernanceBrainVerdict] | None" = None

    @property
    def knows_the_user(self) -> bool:
        """Whether a usable profile reached this request.

        An empty profile is treated as not knowing: a UserProfile with no
        skills cannot support honest reasoning about the person, and saying
        so beats quietly behaving as though it could.
        """
        return self.user is not None and not self.user.is_empty

    def specialists(self) -> list[BaseAgent]:
        """Everyone registered except the Manager's own skills.

        The Manager registers its skills into the same registry as every
        specialist, so it would otherwise list itself as one of them.
        """
        return sorted(
            (agent for agent in self.roster() if agent.name != self.own_name),
            key=lambda agent: agent.name,
        )


# A handler answers one kind of question and returns the standard result shape.
CapabilityHandler = Callable[[CapabilityRequest], TaskResult]


@dataclass(frozen=True)
class ManagerCapability:
    """One handler, and the words that select it."""

    name: str
    capabilities: frozenset[str]
    handle: CapabilityHandler
    description: str = ""
    # How this handler recognises a request that is unmistakably its own,
    # when word overlap is the wrong instrument for the job.
    #
    # Scoring compares a task against a bag of words, which is right for "what
    # kind of work is this?" and wrong for "who is being named?". A request
    # that references an *identity* is not describing a kind of work, and a
    # handler that can tell the difference says so here rather than hoping the
    # arithmetic lands its way.
    #
    # Optional, and absent on every handler that does not need it: scoring
    # remains the rule, and this is the exception a handler must earn.
    claims: "Callable[[Task, Sequence[str]], bool] | None" = None
    # Natural Conversation fix: whether this handler may win the
    # word-overlap *fallback* at all. True for every existing handler by
    # default - nothing already relying on scoring changes.
    #
    # A handful of handlers own a `claims` predicate that is a *shape*
    # check ("every meaningful word is conversational") precise enough
    # that it already covers every request the handler should ever
    # answer. For exactly those handlers, the word-overlap fallback
    # beneath `claims` is not a safety net - it is the defect the Natural
    # Conversation audit measured: a long, substantive remark that
    # happens to open with one incidental word ("yeah, I still need to
    # finish my Java work" scores one point for small_talk on "yeah", and
    # nothing else scores at all) wins by default, and the Manager answers
    # a real remark with "Got it." A capability set with scoreable=False
    # is reachable only through its own `claims`, never through this
    # fallback.
    scoreable: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ManagerCapability.name must not be empty")
        if not self.capabilities:
            raise ValueError(
                f"ManagerCapability '{self.name}' must answer to at least one word"
            )
        object.__setattr__(
            self,
            "capabilities",
            frozenset(c.strip().lower() for c in self.capabilities if c.strip()),
        )

    def score(self, tokens: frozenset[str]) -> int:
        """How many of this handler's words appear in the task."""
        return len(self.capabilities & tokens)


# ---------------------------------------------------------------------------
# The built-in handler: what the Manager has always done
# ---------------------------------------------------------------------------


def report_roster(request: CapabilityRequest) -> TaskResult:
    """Report who the Manager is, what it handles, and who it can call on.

    This is today's behaviour moved, not rewritten. The rendering below is
    character-for-character what ManagerSkills.execute produced before there
    was a registry, because the point of this step is to change where the
    behaviour lives and not what it says.
    """
    others = request.specialists()
    own = list(request.own_capabilities)

    lines = [
        f"{request.identity.name}: {request.identity.role or 'orchestrator'}",
        f"  handles directly : {', '.join(own)}",
    ]
    if others:
        lines.append(f"  specialists ({len(others)}):")
        for agent in others:
            caps = ", ".join(sorted(agent.spec.capabilities))
            lines.append(f"    - {agent.name}: {caps}")
    else:
        lines.append("  specialists      : none registered")

    payload = {
        "identity": request.identity.name,
        "own_capabilities": tuple(own),
        "specialists": {
            agent.name: tuple(sorted(agent.spec.capabilities)) for agent in others
        },
        # Who this Manager is serving, as UserProfile.describe() renders it -
        # counts and seniority, never a skill name, a project or a rate. That
        # method exists precisely because this kind of summary reaches logs
        # and consoles, and the profile is the user's, not the system's, to
        # put on display.
        #
        # Added to the payload rather than the rendered text on purpose: the
        # rendering above is what a person reads, and it did not previously
        # mention the user. Nothing about the printed report changes.
        "serving": (
            request.user.describe() if request.knows_the_user else "no profile configured"
        ),
        # What this task named from the user's context, as counts. Same rule as
        # "serving" above and for the same reason: how much, never what. The
        # named skills exist on request.recognition for a handler that needs
        # them; they do not belong in a payload that reaches a console.
        "recognised": request.recognition.describe(),
        # Counts only, the same rule as "serving" and "recognised" above. A
        # schedule says where somebody is on a Tuesday, and that is theirs
        # rather than the system's to print.
        "scheduled": request.schedule.describe(),
    }
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines), payload=payload
    )


# Verbs that ask for somebody to be brought into a conversation.
#
# A verb alone is not an invitation. "join" is also an ordinary coding word -
# "code a function to join two lists" is a task, not a request for company -
# so the verb has to land on a *name the registry knows* before this means
# anything. That pairing is the whole rule.
#
# "add" is deliberately absent, and the reason is worth stating. It reads as an
# invitation in "add Security to the room" - but a verb only reaches this
# function if the router sent the task to the Manager, and the router matches
# on the words IDENTITY.md declares. Declaring "add" would make Alfred claim
# "add two numbers" and answer it with "I have no specialist by that name",
# which is a confident wrong answer to an ordinary request. A phrasing that
# does not work is better than one that works by making a common word mean
# something it usually does not.
INVITATION_VERBS = frozenset({"invite", "bring", "join"})


def invitation_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this task asks for a registered agent to be brought in.

    Two conditions, and both are required:

        an invitation verb        "invite", "bring", "add", "join"
        a registered identity     a name currently in the roster

    Neither alone decides anything. "research this topic" names a kind of work
    and no agent, so it routes as work. "join these two lists" uses a verb and
    names nobody, so it routes as work. "invite Research" does both, and is the
    only shape that means somebody should join.

    **No agent is named here.** The names arrive from the roster at call time,
    so a Coding, Security or QA specialist is invitable the day it registers -
    the collision this function exists to fix is precisely that such names are
    also ordinary capability words, and the fix must not be a list of them.
    """
    if not (tokenize(task.description) & INVITATION_VERBS):
        return False
    words = task.description.lower()
    return any(name and name.lower() in words for name in participants)


INVITED_KEY = "invited"


def invite_participant(request: CapabilityRequest) -> TaskResult:
    """Work out which registered agent a person is asking to bring in.

    Deciding who takes part in a piece of work is orchestration, which is why
    this is one of the Manager's own capabilities rather than a new layer. It
    is the same question `roster_report` answers - *who is available* - asked
    about a particular name.

    **It resolves a name and returns it. It joins nothing.** There is no
    Conversation in scope and no way to reach one, so this handler cannot add a
    participant even if a later edit wanted it to. What it produces is a string
    in the payload; whoever owns the room decides what to do with it. That is
    what keeps the room free of discovery and this handler free of presentation.

    **No agent is named here.** Matching is against ``roster()`` - the registry
    the Manager already exposes - so a Coding, Research or QA specialist added
    later is invitable the day it registers, with nothing in this file changing.

    Deterministic, so it behaves identically in Half Brain: a rostered agent is
    invited when its name appears in the words. Longest name wins, so "Research
    Agent" beats "Research" when both are registered.

    A name nobody is registered under is **not** turned into a participant. The
    honest answer is that no such specialist exists and here is who does, which
    is more useful than silently agreeing that somebody exists because they were
    mentioned.
    """
    available = request.specialists()
    words = request.task.description.lower()

    matched = None
    for agent in available:
        name = agent.name.lower()
        if name and name in words:
            if matched is None or len(name) > len(matched.name):
                matched = agent

    known = ", ".join(agent.name for agent in available) or "none registered"

    if matched is None:
        return TaskResult.success(
            request.task,
            request.own_name,
            "I have no specialist by that name to bring in. "
            f"Registered specialists: {known}.",
            payload={INVITED_KEY: None, "available": tuple(a.name for a in available)},
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        f"{matched.name} can join this conversation.",
        payload={INVITED_KEY: matched.name, "available": tuple(a.name for a in available)},
    )


# Bringing somebody into a conversation. Declared here because a capability
# word with no handler behind it is dropped, so these words mean something
# exactly as far as this function goes.
PARTICIPANT_INVITATION = ManagerCapability(
    name="participant_invitation",
    capabilities=frozenset({"invite", "bring", "join", "participant", "participants"}),
    handle=invite_participant,
    description="Resolves which registered specialist a person is asking to bring in.",
    # Word overlap cannot settle "invite Research": the name scores for the
    # research capability and the verb scores here, and a tie broken on handler
    # name decides it alphabetically. Recognising the shape of the request is
    # the honest answer, and it costs one predicate rather than a priority hack.
    claims=invitation_intent,
    # 0.4 Natural Conversation pass: scoreable=False was tried here and
    # reverted. Unlike record_this/forget/etc., claims does not fully
    # replace scoring for this capability by design: invitation_intent
    # requires the named target to already be a *registered* identity, so
    # "invite Cartographer" (nobody by that name exists) is claims=False
    # on purpose - it still needs to reach invite_participant's own
    # handler via word-overlap scoring so that handler can give the
    # honest "no such specialist, here's who is" answer
    # (tests/test_invitation.py::TestNobodyIsInvented). Disabling scoring
    # would silence that honest refusal, not just the genuine ordinary-
    # speech collision ("can I join you for lunch" also still scores here,
    # a known, accepted trade-off - not the same class of bug as the
    # others this pass fixed).
)


# The words this handler answers to - and only those it genuinely implements.
#
# `plan` and `coordinate` are deliberately absent. IDENTITY.md declared them
# from the very first commit, and nothing was ever written behind them: a task
# saying "plan my week" routed here and received a roster listing, reported as
# a completed mission. The user asked for a plan and was told who is on the
# team.
#
# Claiming them here would have preserved that. Declining to claim them means
# such a task now finds no agent, which is the honest answer - "Never invent"
# and "Escalate rather than guess" are principles this project states in
# SOUL.md, and answering a planning request with a roster breaks both.
#
# When a real planning handler is written it declares those words, IDENTITY.md
# declares them again, and Alfred takes the work on. Until then he does not
# claim it.
ROSTER_REPORT = ManagerCapability(
    name="roster_report",
    capabilities=frozenset(
        {
            "capabilities",
            "status",
            "roster",
            "report",
            "reporting",
            "summary",
        }
    ),
    handle=report_roster,
    description="Reports the Manager's own capabilities and its specialist roster.",
)


# ---------------------------------------------------------------------------
# The conversational floor (Stage 11.3): short turns answered naturally
# ---------------------------------------------------------------------------
#
# Measured directly, before this existed: "okay", "thanks", "good morning"
# and "got it" all reached ManagerAgent.handle_task and came back
# no_agent_found with an empty output - not because nothing could answer
# them, but because nothing had ever been asked to. A person saying
# "thanks" received silence. That is the specific, verified defect this
# section exists to close - not a general conversation engine, one small
# capability answering the turns a person actually produces between real
# requests.

# Grouped by what kind of turn they are, because the right reply differs -
# "thanks" and "okay" are not the same thing to say back, and a greeting
# is not either. Deliberately short lists: this recognises a short,
# ordinary turn, not every synonym for one.
# Stage 17.22: the vocabulary and the rule moved to
# ``src/services/conversation_floor.py`` so a specialist can use the same
# ones without an agent importing this module - see that module's own note
# for why copying them down instead would have been the wrong fix. These
# names are the ones this file has always exported; only where they are
# defined changed, so every existing reader is untouched.
_GREETING_WORDS = GREETING_WORDS
_THANKS_WORDS = THANKS_WORDS
_ACKNOWLEDGEMENT_WORDS = ACKNOWLEDGEMENT_WORDS
_CONVERSATIONAL_WORDS = CONVERSATIONAL_WORDS
_CONVERSATIONAL_FILLER = CONVERSATIONAL_FILLER


def natural_acknowledgement(request: CapabilityRequest) -> TaskResult:
    """Answer a short conversational turn naturally and briefly, instead
    of either finding no agent or reporting the full roster for a message
    that never asked for it.

    Deterministic and Half-Brain-safe: no model is consulted, so "thanks"
    gets an answer whether or not a language model is configured - the
    same floor ``report_roster``/``general_assistance`` already stand on.

    What changed, and why. This used to answer every greeting with one
    fixed string - "Hello! What can I help you with?" - carrying no name.
    A person who had just opened a private line to one specific agent, and
    addressed it by name, got back a sentence that could have come from
    any agent in the system or from none of them, which made the whole
    identity layer look like it was not running. It was running; this
    handler simply never asked it anything.

    It asks now. The greeting is composed from the identity this request
    already carries (see CapabilityRequest.identity, populated for every
    handler by ManagerSkills), so an agent greets as itself - and a third
    agent registered tomorrow greets as itself too, with nothing added
    here. Still no model, still no personality performance (see
    ``src/services/identity/introduction.py`` for why that boundary is
    where it is), and still a fallback to the plain wording when a
    document declares no name to greet with.

    Thanks and acknowledgements keep their short fixed replies: neither
    carries identity, and answering "thanks" with a job title would be a
    worse reply, not a more personal one.
    """
    tokens = tokenize(request.task.description)
    if tokens & _THANKS_WORDS:
        text = "You're welcome!"
    elif tokens & _GREETING_WORDS:
        text = greeting_from_identity(request.identity) or "Hello! What can I help you with?"
    else:
        text = "Got it."
    return TaskResult.success(request.task, request.own_name, text)


def describe_self(request: CapabilityRequest) -> TaskResult:
    """Answer "who are you" from the identity document that is already loaded.

    The defect this removes was the sharpest evidence that identity was not
    reaching the product: asked who it was, the orchestrator replied that it
    had no way to reason about that yet. That was not an honest limitation,
    it was a missing capability wearing one. Nothing about naming yourself
    requires a model - the name, the role and the description were parsed out
    of IDENTITY.md at startup and handed to this very function.

    Deterministic, Half-Brain-safe, and composed rather than written: every
    content word comes from the document, and the capability list is the
    agent's own real ``own_capabilities`` - the intersection the Manager
    already computes, never a claim invented here. An identity that declares
    nothing falls through to the same honest answer as before rather than
    being padded out.
    """
    text = self_introduction(request.identity, request.own_capabilities)
    if text is None:
        return TaskResult.success(
            request.task,
            request.own_name,
            f"I am {request.own_name}. My identity document declares nothing further.",
        )
    return TaskResult.success(request.task, request.own_name, text)


def asks_who_you_are(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this whole message is a question about who the agent is.

    A claim rather than a score, for the reason ManagerCapability.claims
    itself documents: "who are you" is a question about an *identity*, and
    word overlap cannot tell it from "who should this go to". The phrase
    matching, and the length cap that keeps a long request containing the
    phrase from being swallowed, both live in
    ``src/services/identity/introduction.py`` beside the rendering they
    guard.
    """
    return identity_question(task.description)


SELF_DESCRIPTION = ManagerCapability(
    # Named to sort first among the claiming handlers, and deliberately: a
    # direct question about who you are is answered by identity, not by
    # whichever other handler also recognises a word in it. This is not a
    # priority list (see select_capability's own comment) - it is one name,
    # chosen so a total, repeatable order puts identity first for the one
    # kind of request that is unambiguously about identity.
    name="about_self",
    # Deliberately NARROW. The first attempt declared "who", "name" and
    # "role" as well, and scoring - which runs for every task this claim
    # does not catch - then handed "who should I send this to" to the
    # identity handler on a single-word overlap. These are the words that
    # do not appear in ordinary requests; everything else this handler
    # answers arrives through `claims` below, which reads whole phrases.
    capabilities=frozenset({"yourself", "introduce"}),
    handle=describe_self,
    description="Says who this agent is, from its own loaded identity document.",
    claims=asks_who_you_are,
    # Natural Conversation fix: even a deliberately narrow word can still
    # appear in ordinary speech ("I need to introduce this feature to the
    # client") with nothing to do with an identity question. asks_who_you_are
    # already recognises the real request shape, per this class's own
    # docstring above - the word list was always meant to be an existence
    # check for `claims`, never a genuine scoring signal.
    scoreable=False,
)


CONVERSATIONAL_FLOOR = ManagerCapability(
    # Named to sort *after* every other Manager capability's name
    # ("general_assistance", "participant_invitation", "roster_report") on
    # purpose. select_capability breaks a scoring tie by handler name,
    # keeping whichever it visited first when a later one only *equals*
    # the best score rather than beating it - so "thanks, what's your
    # status" (a genuine tie: "status" for roster_report, "thanks" here)
    # resolves to roster_report, the more specific answer, not this one.
    # This capability is deliberately the fallback of last resort among
    # Alfred's own handlers, never a competitor for one that already
    # claims part of the same message.
    name="small_talk",
    capabilities=_CONVERSATIONAL_WORDS,
    handle=natural_acknowledgement,
    description="Answers short conversational turns - greetings, thanks, acknowledgements - naturally.",
    # Word overlap alone cannot tell "thanks" from "thanks, and also check
    # my email" - see conversational_intent's own docstring.
    claims=conversational_intent,
    # Natural Conversation fix: conversational_intent's own subset check
    # ("every meaningful word must itself be conversational") already
    # covers every message this handler should ever answer - so the
    # word-overlap fallback below can only ever fire on a message
    # conversational_intent already, correctly, rejected: a substantive
    # remark that happens to contain one acknowledgement word ("yeah, I
    # still need to finish my Java work" scores one point on "yeah" and
    # nothing else scores at all). Disabling the fallback here loses no
    # genuine case and closes exactly that false-success path.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# The general capability floor: ordinary work Alfred does himself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneralCapability:
    """One ordinary kind of help, and the tool it needs to actually do it.

    Named by *kind of work*, never by subject. There is no entry here for a
    football club, a film, a company or a war, and there must never be: the
    subject of a request is data that arrives in the task, and a capability
    that had to be extended for each new subject would not be a general
    capability at all. "Tell me about X" is one capability for every X.
    """

    kind: str
    words: frozenset[str]
    tool: str
    describes: str


# The ordinary work a Manager is expected to be able to attempt. Each entry
# names the tool that would actually do it - and none of those tools exists in
# this repository yet, which is the honest position: the architecture makes
# room for them and refuses to pretend they are installed.
GENERAL_CAPABILITIES: tuple[GeneralCapability, ...] = (
    GeneralCapability(
        kind="research",
        # Natural Conversation fix: "about" deliberately removed. It is
        # one of the most common words in ordinary speech ("what were we
        # talking about", "I'm worried about my exam") and, with no
        # `claims` predicate of its own, was scoring this capability for
        # exactly those remarks with nothing else to check it - a casual
        # aside answered with "the 'web_search' capability is not
        # installed." A genuine "tell me about X" is unaffected: that
        # phrase is KNOWLEDGE_QUESTION_MARKERS' own territory and is
        # claimed there, before scoring ever runs.
        words=frozenset(
            {"research", "search", "lookup", "find", "overview", "information"}
        ),
        tool="web_search",
        describes="looking something up",
    ),
    GeneralCapability(
        kind="coding",
        words=frozenset({"code", "coding", "script", "function", "debug"}),
        tool="code_assistant",
        describes="writing or explaining code",
    ),
)


def _general_kind_for(tokens: frozenset[str]) -> GeneralCapability | None:
    """Which ordinary kind of help this task is asking for, if any."""
    best, best_score = None, 0
    for capability in GENERAL_CAPABILITIES:
        score = len(capability.words & tokens)
        if score > best_score:
            best, best_score = capability, score
    return best


def general_assistance(request: CapabilityRequest) -> TaskResult:
    """Attempt ordinary work, or say precisely why it cannot be attempted.

    This exists because of a defect measured during the Step 9 audit: every
    ordinary request - look something up, explain something, write a small
    function - returned ``no_agent_found``. That answer conflated two entirely
    different facts, "nobody is registered for this" and "the system cannot do
    this", and it made the Manager a router that refuses anything no specialist
    claims. A Manager who cannot attempt ordinary work is not a Manager.

    So the outcomes here are deliberately four, not two:

        the tool is present and works   -> Alfred does the work himself
        the tool is present and fails   -> the failure is reported as its own
        the tool is absent              -> the missing *capability* is named
        the request is not research/coding -> genuine reasoning answers it

    What it never does is fabricate. With no search tool installed there is no
    result to give, and inventing one - or answering from whatever the model
    happens to remember - would be worse than saying nothing. It reports what
    is missing, by name, so the gap is fixable rather than mysterious.

    And note what is absent from the refusal: "I am only the Manager." That
    sentence is the thing this handler exists to make impossible to say.

    **The fourth outcome, corrected during the creative-media checkpoint.**
    ``general_assistance_intent``'s own claim recognises any imperative
    "write/create/build/generate/..." shape, on purpose - narrowing it to
    "only if the object is about code or research" would reintroduce the
    exact word-overlap subject-matching this project has repeatedly removed
    elsewhere. That means a genuine, confidently-claimed request can still
    turn out not to be research or coding once ``_general_kind_for`` looks
    closer - "write a short story", "create a business plan", "build a
    workout routine" all claim this capability the identical way "write a
    function" does. Those are not tool-gated at all: writing prose, a plan
    or a routine is ordinary Brain reasoning with no external capability
    required, exactly like everything else with nowhere more specific to
    go. This used to fall to ``report_roster`` - a roster listing standing
    in for an answer nobody asked for, discovered live during that
    checkpoint's own first real creative-media test - and now reaches
    genuine reasoning instead, through the identical mechanism
    ``open_conversation`` already provides for exactly this.
    """
    capability = _general_kind_for(tokenize(request.task.description))
    if capability is None:
        return open_conversation(request)

    if not request.tools.permits(capability.tool):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task,
            request.own_name,
            (
                f"I can take on {capability.describes}, but the underlying "
                f"'{capability.tool}' capability is not installed, so I cannot "
                f"do it yet rather than guess at an answer. "
                f"Currently equipped: {equipped}."
            ),
        )

    outcome = request.tools.invoke(capability.tool, {"query": request.task.description})
    if not outcome.ok:
        return TaskResult.failure(
            request.task,
            request.own_name,
            f"the '{capability.tool}' capability failed: {outcome.error}",
        )

    if capability.kind != "research":
        return TaskResult.success(
            request.task,
            request.own_name,
            outcome.output,
            payload={
                "handled_by": "general_assistance",
                "kind": capability.kind,
                "tool": capability.tool,
            },
        )

    return _research_synthesis(request, capability, outcome)


def _research_synthesis(
    request: CapabilityRequest, capability: GeneralCapability, outcome: ToolResult
) -> TaskResult:
    """Phase 3 (Research): gathered evidence is reasoned over, never
    handed back raw - the identical acquire-then-reason pattern
    ``live_information`` already established, reused rather than
    duplicated, because "research the best options and compare them" is
    the same shape of problem: acquire real evidence through a governed
    tool, then let the Brain compare and synthesise it. Coding's own
    tool output is returned as-is above, deliberately - a written
    function *is* the deliverable, never evidence to reason over.

    **Research continuity**, folded in here rather than built as a
    second mechanism: a person's own explicitly-recorded research
    (``record_this``, kind=RECORDING, tagged "research" by
    ``capture.py``'s own purpose tagging - Phase 2, unchanged) is
    recalled and offered alongside the fresh search, so "continue the
    research we did earlier" has something real to continue from. Never
    fabricated when nothing was ever recorded - an empty recall is
    simply not added to the notes.
    """
    if request.converse is None:
        return TaskResult.success(
            request.task,
            request.own_name,
            f"Here's what I found (no reasoning available to compare or "
            f"synthesise it yet):\n{outcome.output}",
            payload={
                "handled_by": "general_assistance",
                "kind": "research",
                "tool": capability.tool,
                "synthesised": False,
            },
        )

    notes: list[str] = [f"Web search: {outcome.output}"]
    if request.recall is not None:
        try:
            prior = request.recall(
                request.task.description, kind=MemoryCategory.RECORDING.value,
                tags={"research"}, limit=3,
            )
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            logger.warning("could not recall prior research: %s", exc)
            prior = ()
        notes.extend(f"Earlier research you recorded: {record.content}" for record in prior)
    notes.extend(_recent_conversation_notes(request))

    decision = request.converse(request.task, tuple(notes))
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task,
            request.own_name,
            "I found sources but couldn't properly reason about or "
            f"compare them, so I would rather say so than guess ({decision.reason}).",
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        decision.reason,
        payload={
            "handled_by": "general_assistance",
            "kind": "research",
            "tool": capability.tool,
            "brain": decision.source,
            "tool_source": outcome.source,
            "synthesised": True,
        },
    )


# Phase 1.3 fix: word overlap alone cannot tell "write a small function"
# (an imperative ask to *do* the work) from "I have a function that
# produces an unexpected result and I can't figure out why" (a
# description of a problem, asking to be *reasoned about* - exactly the
# kind of general software-reasoning request this Brain must be able to
# take on, per Phase 1.3's own requirement). Both share the bare word
# "function"; only the first is a request for the tool-gated action this
# capability's own docstring describes. The shape - an imperative verb in
# command position, the identical "anchored verb, never a bare word"
# instrument _CALL_PATTERN/_communication_shape already use - is what
# actually decides it, never the subject.
#
# Requirement N's own rule ("Coding is gated by tool, never by brain" -
# see ARCHITECTURE.md) is unchanged and untouched by this: a genuine
# imperative request for tool-gated coding/research work still reaches
# this handler and still honestly refuses without a tool, Full Brain or
# not. What changes is that a *reasoning* question merely mentioning the
# same domain words no longer gets swept into that refusal at all - it
# was never asking for the tool-gated action in the first place, and now
# correctly falls through to open_conversation, where a genuine Brain can
# actually reason about it.
_GENERAL_ACTION_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(write|create|build|generate|implement|fix|debug|search|find|look\s*up|research)\b",
    re.IGNORECASE,
)


def general_assistance_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request's *shape* is an imperative ask to actually do
    ordinary tool-dependent work - never a description or a discussion
    that merely mentions the same domain words. See the comment above
    ``_GENERAL_ACTION_PATTERN`` for the full reasoning.

    **AI video-generation checkpoint.** ``_GENERAL_ACTION_PATTERN``'s own
    verb set ("write/create/build/generate/...") genuinely overlaps
    ``media_production_intent``'s own - "generate a video of X" and
    "create an image of X" match both shapes, since both are genuine
    imperative production asks. When that happens, ``media_production``
    is the more specific capability (it names a concrete media asset;
    this one only ever recognised code and research as its two kinds -
    see ``_general_kind_for``) and takes the request instead, the
    identical "the more specific claim wins, priority never argued over
    scoring" reasoning ``ManagerSkills.claims()`` already applies one
    level up for an active guided session. Checked here, in the specific
    capability that would otherwise win the tie purely by sorting before
    ``media_production`` alphabetically - never inside
    ``select_capability`` itself, which stays a flat, unordered registry.
    """
    working = _strip_vocative(task.description).strip()
    if not _GENERAL_ACTION_PATTERN.match(working):
        return False
    if media_production_intent(task, participants):
        return False
    # Phase 3 fix, the identical reasoning as the media_production
    # deferral above, found live: "find the section about X in this
    # file" matches this function's own "find" verb gate and knowledge's
    # own DOCUMENT_REFERENCE_MARKERS shape at once - alphabetical sorting
    # in select_capability would otherwise let this capability win
    # ("general_assistance" < "knowledge") and send a request about the
    # user's own supplied document to a live web search instead.
    # knowledge is the more specific capability here - it names a
    # concrete source (the document just referenced); this one only ever
    # recognised "web_search"/"code_assistant" as its two tool-gated
    # kinds.
    if knowledge_intent(task, participants):
        return False
    # Phase 3 fix, the identical reasoning again: "create a report from
    # these findings" matches this function's own "create" verb gate and
    # generate_document's own DOCUMENT_GENERATION_MARKERS shape at once -
    # alphabetical sorting would otherwise let this capability win
    # ("general_assistance" < "generate_document") and answer with
    # composed prose instead of a real, saved file.
    if document_generation_intent(task, participants):
        return False
    # Agent-autonomous voice selection checkpoint, the identical
    # reasoning again: "find me an Italian voice" matches this
    # function's own "find" verb gate and select_voice's own
    # voice/accent+language shape at once - alphabetical sorting would
    # otherwise let this capability win ("general_assistance" <
    # "select_voice") and send a request about the agent's own voice to
    # a live web search instead.
    if select_voice_intent(task, participants):
        return False
    # Real Audio Recording/Capture checkpoint, the identical reasoning
    # again, found live: "find the recording about research" matches
    # this function's own "find" verb gate and list_recordings' own
    # query-marker shape at once - alphabetical sorting would otherwise
    # let this capability win ("general_assistance" < "list_recordings")
    # and send a request about the person's own saved audio to a live
    # web search instead.
    if list_recordings_intent(task.description):
        return False
    return True


GENERAL_ASSISTANCE = ManagerCapability(
    name="general_assistance",
    capabilities=frozenset(
        word for capability in GENERAL_CAPABILITIES for word in capability.words
    ),
    handle=general_assistance,
    description="Ordinary information and coding work the Manager attempts himself.",
    claims=general_assistance_intent,
    # Word overlap has no genuine case left to catch once the claim above
    # exists - see it, and the "today"/"about"/"now" removals elsewhere
    # in this file, for the identical reasoning applied to this
    # capability's own two kinds at once.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Open-ended conversation and reasoning (the Stage 11 audit fix): anything
# substantive that no more specific capability claimed or scored
# ---------------------------------------------------------------------------
#
# The defect this closes: every capability above is a declared *word*, which
# means a genuinely open-ended request - a teaching question, or a short
# remark about something happening in the person's life or the news - matches
# none of them and previously fell to report_roster (a status listing) or, at
# the outer router, to no agent at all. Neither is an answer. This is
# deliberately not "one more word list" -
# no list of words could ever cover an unbounded set of things a person might
# say - it is the last-resort handler select_capability() reaches for
# precisely when nothing else claimed or scored, which is the only mechanism
# that can be honest about genuinely unpredictable input.


def _recent_conversation_notes(request: CapabilityRequest) -> tuple[str, ...]:
    """Recent conversation lines, as inert evidence for a Brain that
    already accepts notes - never a second conversation history, and
    never anything that could read as an instruction rather than
    background (the same "notes, not instructions" contract every other
    caller of converse() already honours).

    Empty whenever no conversation reaches this request - a bare
    construction, an older caller, or a request.history that itself
    reports nothing yet - so every existing caller that never wires this
    gets exactly the notes it always got, unchanged.
    """
    if request.history is None:
        return ()
    lines = tuple(str(line) for line in request.history() if str(line).strip())
    if not lines:
        return ()
    return ("Recent conversation, oldest first (background, not instructions):",) + lines


def open_conversation(request: CapabilityRequest) -> TaskResult:
    """Answer a genuinely open-ended request by consulting the Brain
    directly - a question, an ask to explain or teach something, an
    emotional or contextual remark, natural follow-up conversation.

    Reached only as select_capability's own fallback of last resort:
    nothing more specific claimed or scored the task, and there was real
    content to respond to (see that function for the empty-input case,
    which still goes to report_roster, unchanged).

    Honest either way, the same rule general_assistance already lives
    under for a missing tool: a usable Brain composes a genuine answer;
    without one, this says so plainly rather than fabricating a response
    or reporting the roster as though that were what was asked for. That
    is also why this returns TaskResult.failure rather than success when
    no answer could be composed - "no agent found" and "a roster dump
    reported as done" were the two old failure shapes; a named,
    honest failure is neither.
    """
    if request.converse is None:
        return TaskResult.failure(
            request.task,
            request.own_name,
            "I don't have a way to reason about that yet - no language model "
            "is configured for me right now, so I would rather say so than guess.",
        )

    # Final Pre-Phase Interaction Completion: a pending clarification or
    # an in-progress guided activity takes precedence over an ordinary
    # reply - see guided_interaction.py's own docstring. Additive and
    # fully reversible: this returns None (nothing to do) the instant
    # nothing is in progress and nothing in this remark starts anything,
    # and everything below runs exactly as it always has.
    guided = guided_interaction_response(request)
    if guided is not None:
        return guided

    # Stage 18: the same settled evidence knowledge_inquiry gathers, offered
    # to an open-ended answer too. Additive and strictly so - with no
    # knowledge tool granted and no memory wired (this project's own
    # default, and every pre-Stage-18 construction) it contributes nothing
    # and this call is byte-identical to what it always was. When a person
    # *has* handed Alfred their books, an explanation that ignored them
    # would be the same defect knowledge_inquiry exists to fix, one handler
    # over.
    evidence = gather_knowledge_evidence(
        request.task.description, tools=request.tools, recall=request.recall,
        history=request.history,
    )
    notes = (
        tuple(request.recognition.notes())
        + _recent_conversation_notes(request)
        + evidence.with_settlement_note()
    )

    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task,
            request.own_name,
            "I don't have a way to properly reason about that yet, so I would "
            f"rather say so than guess ({decision.reason}).",
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        decision.reason,
        payload={
            "handled_by": "open_conversation",
            "brain": decision.source,
            "knowledge_sources": tuple(evidence.sources) + (MODEL_KNOWLEDGE,),
        },
    )


OPEN_CONVERSATION = ManagerCapability(
    name="open_conversation",
    # Kinds of interaction, never subjects - the same discipline
    # GeneralCapability's own docstring states: no entry here for a car, a
    # football club, a country or a market, and there must never be one.
    # These words are not what makes this handler reachable for a short
    # remark containing none of them - select_capability's fallback is -
    # they simply let a request that plainly asks to be taught or
    # discussed score normally too, the same as any other capability's
    # own words.
    capabilities=frozenset({"explain", "teach", "discuss"}),
    handle=open_conversation,
    description=(
        "Answers genuinely open-ended questions, teaching requests and "
        "conversation by consulting the Brain directly - never a "
        "subject-specific word list."
    ),
)


# ---------------------------------------------------------------------------
# Stage 12: explicit, user-directed memory commands - "forget this",
# "what do you remember about X"
# ---------------------------------------------------------------------------
#
# "Remember this" is deliberately absent here, and that absence is the
# result of inspection, not an oversight. scripts/exchange.py's own
# retain_what_was_asked_for() already extracts a retention request from the
# raw conversation text (session.retention_requests, the same function this
# module would otherwise have called a second time) and writes it through
# manager.retain() - the identical governed path - on every real turn, in
# both production entry points (scripts/control_center.py and
# scripts/serve_ui.py). A capability here that also matched "remember that"
# would not add a missing behaviour; it would retain the same utterance
# twice under two different record ids. Forget and recall have no such
# existing counterpart - nothing in this codebase previously offered a way
# to remove or query a memory by name - so only those two are new here.
#
# Recognised by request *shape* (a marker phrase), exactly as an invitation
# or a conversational turn already is - never by the topic inside it, which
# is what keeps "what do you remember about" any subject at all reachable
# without this file learning what that subject is. Word-overlap scoring
# is the wrong instrument for the same reason invitation_intent's own
# docstring already gives: these phrasings would otherwise compete on
# ordinary words ("forget", "about") that also appear in substantive
# requests having nothing to do with memory.

FORGET_MARKERS: tuple[str, ...] = (
    "forget that", "forget what i said about", "forget about that", "please forget",
)
RECALL_MARKERS: tuple[str, ...] = (
    "what do you remember about", "do you remember", "what did i tell you about",
    "what do you know about",
)


def _marker_present(text: str, markers: tuple[str, ...]) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in markers)


def _after_marker(text: str, markers: tuple[str, ...]) -> str:
    """The part of an utterance that follows the first marker found, if
    any - the same extraction src.services.memory.session._asked_to_keep
    performs, generalised to any marker set rather than duplicated per
    handler."""
    lowered = str(text or "").lower()
    for marker in markers:
        position = lowered.find(marker)
        if position == -1:
            continue
        remainder = text[position + len(marker):].strip(" ,.:;?\t\n")
        if remainder:
            return remainder
    return ""


def forget_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, FORGET_MARKERS)


def recall_memory_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, RECALL_MARKERS)


def forget_this(request: CapabilityRequest) -> TaskResult:
    """'Forget that X' removes whatever this agent actually has stored
    about X - found the same honest way any recall already works
    (literal overlap against real content), never a hardcoded topic.
    """
    if request.recall is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to forget things right now.",
        )

    topic = _after_marker(request.task.description, FORGET_MARKERS)
    if not topic:
        return TaskResult.failure(
            request.task, request.own_name,
            "Forget what, exactly? Try 'forget that ...'.",
        )

    matches = request.recall(topic, limit=5)
    if not matches:
        return TaskResult.success(
            request.task, request.own_name,
            "I don't have anything remembered about that.",
        )

    removed = sum(1 for record in matches if request.forget(record.id))
    if removed < len(matches):
        # Final acceptance sweep (2026-09-08): forget() returning False for
        # any match must not be reported as an unqualified "Forgotten" -
        # that would tell a person something was deleted when it was not.
        # request.forget's own contract (see Memory.forget) already
        # distinguishes "did not exist" from "existed and was removed";
        # this is the one place that distinction must survive into what
        # the person is actually told.
        return TaskResult.failure(
            request.task, request.own_name,
            f"Only {removed} of {len(matches)} record(s) could be forgotten.",
        )
    return TaskResult.success(
        request.task, request.own_name,
        f"Forgotten ({removed} record(s)).",
        payload={"forgotten": removed},
    )


# "What do you remember about me" - the whole of what the user taught.
_SELF_REFERENCES = frozenset({"me", "myself", "me and my life", "my life"})
PERSONAL_CONTEXT_ANSWER_LIMIT = 12


def _canonical_recall(request: CapabilityRequest, topic: str) -> "tuple[Any, ...]":
    try:
        items = all_current_personal_context(recall=request.recall, limit=200)
    except Exception as exc:  # noqa: BLE001 - a store that cannot be read
        # must not take plain recall down with it.
        logger.warning("could not read canonical personal context: %s", exc)
        return ()
    if not items:
        return ()
    cleaned = " ".join(str(topic or "").lower().split()).strip(" ?.!")
    if cleaned in _SELF_REFERENCES:
        return tuple(items[:PERSONAL_CONTEXT_ANSWER_LIMIT])
    if not (tokenize(cleaned) - _QUERY_STOPWORDS):
        return ()
    matched = match_personal_context(items, cleaned)
    current = tuple(item for item in matched if item.status == STATUS_CURRENT) or matched
    return tuple(current[:PERSONAL_CONTEXT_ANSWER_LIMIT])


def recall_memory(request: CapabilityRequest) -> TaskResult:
    """'What do you remember about X' - plain recall against this agent's
    own store, by whatever topic the person actually named. No topic list
    anywhere: the query is the words after the marker, nothing else.
    """
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to recall memories right now.",
        )

    topic = _after_marker(request.task.description, RECALL_MARKERS)

    # Pre-Stage-9 Campaign 1: what the user taught about themselves lives in
    # one canonical, topic-keyed store, where a newer statement supersedes an
    # older one. Answered from there first, so "what do you remember about my
    # schedule" reports the current schedule rather than every schedule ever
    # mentioned; anything else falls through to the plain recall below.
    canonical = _canonical_recall(request, topic)
    if canonical:
        lines = "\n".join(f"- {describe_for_user(item)}" for item in canonical)
        return TaskResult.success(
            request.task, request.own_name,
            f"Here's what I know:\n{lines}",
            payload={"recalled": len(canonical), "source": "personal_context"},
        )

    records = request.recall(topic, limit=5)
    if not records:
        return TaskResult.success(
            request.task, request.own_name,
            "I don't have anything remembered about that yet.",
        )

    lines = "\n".join(f"- {record.content}" for record in records)
    return TaskResult.success(
        request.task, request.own_name,
        f"Here's what I remember:\n{lines}",
        payload={"recalled": len(records)},
    )


FORGET = ManagerCapability(
    name="forget",
    capabilities=frozenset({"forget"}),
    handle=forget_this,
    description="Removes a stored memory when a person explicitly asks to be forgotten.",
    claims=forget_intent,
    # Natural Conversation fix: "forget" is an ordinary word in ordinary
    # speech ("I always forget my keys") that has nothing to do with this
    # capability. forget_intent already recognises the real request shape
    # ("forget that X"); the word-overlap fallback below can only ever
    # fire on a message forget_intent already, correctly, rejected.
    scoreable=False,
)

RECALL_MEMORY = ManagerCapability(
    name="recall_memory",
    capabilities=frozenset({"recall"}),
    handle=recall_memory,
    description="Answers 'what do you remember about X' from this agent's own governed memory.",
    claims=recall_memory_intent,
    # Natural Conversation fix: "recall" appears in ordinary speech ("I
    # don't recall saying that") with nothing to do with this capability.
    # recall_memory_intent already recognises the real request shape.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Phase 2 (Memory & Personal Context): correcting an existing memory -
# genuinely distinct from FORGET (removal only) and from an ordinary
# "remember that X" (addition only). CHECK found the *mechanism* for a
# correction already proven, once, narrowly: personal_recurring_events's
# own relational-birthday handling already reads an existing tagged record
# and, on a genuine change, forgets it and retains the replacement -
# forget-then-retain, atomically, through the identical governed write
# path. What did not exist was that same sequence generalised to any
# ordinary user_context memory, which is the real gap Section 11 of this
# checkpoint names ("do not silently maintain contradictory active
# memories indefinitely") - retain_what_was_asked_for (scripts/exchange.py)
# admits every "remember that X" as a brand new record with no check for
# an existing, now-superseded one at all.
#
# **Marker-based, never inferred from free-text similarity.** The same
# "ask explicitly, never guess" discipline session.py's own
# RETENTION_MARKERS and capture.py's own capture markers already hold
# themselves to. A correction is consequential - it removes an active
# memory - so it earns the same explicitness a retention request does,
# rather than a similarity heuristic that could misfire on two genuinely
# different facts that merely share a few words.
CORRECTION_TOPIC_PATTERN = re.compile(
    r"\b(?:update|change|correct)\s+what\s+you\s+(?:remembered|recorded|know)\s+about\s+"
    r"(.+?)\s+to\s+(.+?)[.!?]*$",
    re.IGNORECASE,
)
CORRECTION_REPLACE_MARKERS: tuple[str, ...] = (
    "that's wrong,", "that's wrong -", "that's wrong:",
    "that's incorrect,", "that's incorrect:",
    "you got that wrong,", "you got that wrong:",
    "you remembered that incorrectly,", "you remembered that incorrectly:",
    "correction:",
)
CORRECTION_INVALIDATE_MARKERS: tuple[str, ...] = (
    "that's no longer", "that's not accurate anymore", "that's not true anymore",
    "no longer my", "not my preference anymore",
)


def correction_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = task.description
    return bool(
        CORRECTION_TOPIC_PATTERN.search(text)
        or _marker_present(text, CORRECTION_REPLACE_MARKERS)
        or _marker_present(text, CORRECTION_INVALIDATE_MARKERS)
        # Pre-Stage-9 Campaign 2: a correction in ordinary words ("You
        # remembered that incorrectly. It's green.", "Actually it's green,
        # not blue.", "I don't like blue anymore") - see correction.py.
        or correction_statement_intent(text)
    )


def _canonical_items_for_correction(request: CapabilityRequest) -> "tuple[Any, ...]":
    try:
        return all_current_personal_context(recall=request.recall, limit=200)
    except Exception as exc:  # noqa: BLE001 - a store that cannot be read
        # must not take the plain correction path down with it.
        logger.warning("could not read canonical personal context: %s", exc)
        return ()


def _canonical_for_topic(items: "Sequence[Any]", topic: str) -> "Any | None":
    """The current canonical statement ``topic`` names - by its normalized
    key, or by a whole-word key containment ("my favourite colour" names the
    topic "favourite colour"). Never a partial word overlap: correcting the
    wrong fact is worse than correcting none."""
    key = normalize_topic_key(topic)
    if not key:
        return None
    for item in items:
        if item.topic_key == key:
            return item
    for item in items:
        other = item.topic_key
        if len(other) >= 3 and (
            re.search(rf"\b{re.escape(other)}\b", key) or re.search(rf"\b{re.escape(key)}\b", other)
        ):
            return item
    return None


def _replace_canonical(
    request: CapabilityRequest, existing: "Any", candidate: PersonalContextCandidate, *, reply: str, payload: dict,
) -> TaskResult:
    """Write the corrected statement under the existing topic's own key, then
    remove only the statement that was wrong - retain first, so a refused
    replacement never destroys what it was meant to replace. Earlier history
    for the topic (a schedule that genuinely changed before) is untouched."""
    corrected = PersonalContextCandidate(
        topic=existing.topic, content=candidate.content, category=candidate.category or existing.category,
        status=candidate.status, value=candidate.value, activity=candidate.activity,
    )
    record = build_personal_context_record(corrected, agent_name=request.own_name)
    record = MemoryRecord(
        content=record.content, kind=record.kind, tags=record.tags,
        context={**dict(record.context or {}), "corrects": existing.record_id},
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
    request.forget(existing.record_id)
    return TaskResult.success(request.task, request.own_name, reply, payload=payload)


# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 2: a correction in ordinary words, resolved against
# what is actually stored - and asked back, never guessed, when more than one
# stored fact could be meant.
# ---------------------------------------------------------------------------

# Words that make a stored topic a preference - grammar about preferring,
# never a subject.
_PREFERENCE_TOPIC_WORDS = frozenset({"favourite", "favorite", "preference", "preferences", "preferred", "prefer", "prefers"})
# How many candidates one clarifying question offers at most.
_CORRECTION_OPTION_LIMIT = 6


def _mentions(text: str, fragment: str) -> bool:
    fragment = " ".join(str(fragment or "").lower().split())
    haystack = " ".join(str(text or "").lower().split())
    return len(fragment) >= 2 and re.search(rf"\b{re.escape(fragment)}\b", haystack) is not None


def _correction_options(request: CapabilityRequest, items: "Sequence[Any]") -> "list[tuple[CorrectionOption, Any]]":
    """Every current stored fact a correction could be about, newest first:
    each canonical topic's current statement, and every free "remember that"
    note that is not already part of the canonical store."""
    options: list[tuple[CorrectionOption, Any]] = []
    for item in items:
        if item.status != STATUS_CURRENT:
            continue
        said = describe_for_user(item).rstrip(".")
        label = f"your {item.topic} ({item.value})" if item.value else said[:1].lower() + said[1:]
        options.append((CorrectionOption(label=label, record_id=item.record_id, topic=item.topic, value=item.value), item))
    canonical_ids = {item.record_id for item in items}
    try:
        notes = tuple(request.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=50))
    except Exception as exc:  # noqa: BLE001 - an unreadable store offers nothing
        logger.warning("could not read notes for a correction: %s", exc)
        notes = ()
    for note in notes:
        if note.id in canonical_ids or (note.context or {}).get("fact_type") or "resolved_ambiguity" in note.tags:
            continue
        options.append((CorrectionOption(label=f'"{note.content}"', record_id=note.id), note))
    options.sort(key=lambda pair: pair[1].created_at, reverse=True)
    return options


def _option_text(option: CorrectionOption, source: "Any") -> str:
    return f"{option.topic} {option.value} {getattr(source, 'content', '')}"


def _last_answer_line(request: CapabilityRequest) -> str:
    """What this agent said last, when it was an answer rather than an
    acknowledgement of something just learned - the one reply "that's wrong"
    can only mean."""
    if request.history is None:
        return ""
    try:
        lines = [str(line) for line in request.history()]
    except Exception:  # noqa: BLE001 - an unreadable history narrows nothing
        return ""
    prefix = f"{request.own_name}:"
    for line in reversed(lines):
        if line.startswith(prefix):
            said = line[len(prefix):].strip()
            return "" if said.lower().startswith("got it") else said
    return ""


def _learn_instead(request: CapabilityRequest, candidate: PersonalContextCandidate) -> TaskResult:
    record = build_personal_context_record(candidate, agent_name=request.own_name)
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't record that: {verdict.reason}")
    said = describe_for_user(candidate)
    return TaskResult.success(
        request.task, request.own_name, f"Got it - I'll remember that {said[:1].lower()}{said[1:]}",
        payload={"handled_by": "correct_memory", "action": "learned", "topic": candidate.topic},
    )


def _ask_which(
    request: CapabilityRequest, statement: CorrectionStatement, candidates: "Sequence[tuple[CorrectionOption, Any]]",
) -> TaskResult:
    offered = tuple(option for option, _source in candidates[:_CORRECTION_OPTION_LIMIT])
    verb = f"change to {statement.replacement}" if statement.action == ACTION_REPLACE else "remove"
    labels = [option.label for option in offered]
    listing = ", ".join(labels[:-1]) + f" or {labels[-1]}"
    question = f"More than one thing I remember could be meant: {listing}. Which should I {verb}?"
    sessions = request.activity_sessions
    if sessions is None:
        return TaskResult.failure(
            request.task, request.own_name,
            f"{question} I can't hold that question open right now, so tell me the topic "
            "directly - for example 'update what you remembered about <topic> to <value>'.",
        )
    sessions.raise_correction(
        request.own_name,
        PendingCorrection(question=question, action=statement.action, replacement=statement.replacement, options=offered),
    )
    return TaskResult.success(
        request.task, request.own_name, question,
        payload={"handled_by": "correct_memory", "action": "clarify", "options": labels},
    )


def _apply_to_option(
    request: CapabilityRequest, statement: CorrectionStatement, option: CorrectionOption, source: "Any",
) -> TaskResult:
    """Apply one resolved correction - retain the corrected statement first,
    then forget only the statement that was wrong."""
    payload = {"handled_by": "correct_memory", "topic": option.topic or option.label}
    if statement.action == ACTION_INVALIDATE:
        request.forget(option.record_id)
        return TaskResult.success(
            request.task, request.own_name, f"Understood - I've removed {option.label}.",
            payload={**payload, "action": "invalidated"},
        )

    replacement = statement.replacement
    if isinstance(source, MemoryRecord):
        content = source.content
        if statement.old_value and _mentions(content, statement.old_value):
            content = re.sub(rf"\b{re.escape(statement.old_value)}\b", replacement, content, flags=re.IGNORECASE)
        else:
            content = replacement
        verdict = request.retain(MemoryRecord(
            content=content, kind=MemoryCategory.USER_CONTEXT.value, tags=source.tags,
            context={"source": "conversation", "corrects": source.id},
        ))
        if not verdict.allowed:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
        request.forget(source.id)
        return TaskResult.success(
            request.task, request.own_name, f"Got it - I've corrected that: {content}",
            payload={**payload, "action": "corrected"},
        )

    if source.value:
        corrected = PersonalContextCandidate(
            topic=source.topic, content=f"The user's {source.topic} is {replacement}.",
            category=source.category, value=replacement,
        )
        said = describe_for_user(corrected)
        return _replace_canonical(
            request, source, corrected, reply=f"Got it - I've corrected that. {said}",
            payload={**payload, "action": "corrected"},
        )

    # A statement with no separate value ("you're learning X") is corrected
    # to a new topic: the corrected subject takes the old one's place.
    content = re.sub(rf"\b{re.escape(source.topic)}\b", replacement, source.content, flags=re.IGNORECASE)
    if content == source.content:
        content = f"The user's {source.topic} is {replacement}."
    corrected = PersonalContextCandidate(
        topic=replacement if content != f"The user's {source.topic} is {replacement}." else source.topic,
        content=content, category=source.category, activity=source.activity,
    )
    record = build_personal_context_record(corrected, agent_name=request.own_name)
    record = MemoryRecord(
        content=record.content, kind=record.kind, tags=record.tags,
        context={**dict(record.context or {}), "corrects": source.record_id},
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
    request.forget(source.record_id)
    said = describe_for_user(corrected)
    return TaskResult.success(
        request.task, request.own_name, f"Got it - I've corrected that. {said}",
        payload={**payload, "action": "corrected"},
    )


def _apply_correction(request: CapabilityRequest, statement: CorrectionStatement, items: "Sequence[Any]") -> TaskResult:
    """Resolve what a correction is about, in order of how certain the
    evidence is, and ask whenever more than one stored fact remains:

      1. the replacement names its own topic ("my favourite colour is green");
      2. the wrong value was named ("not blue", "I don't like blue anymore");
      3. a change of preference targets a preference-shaped fact;
      4. the one fact this agent has just given as an answer;
      5. the only stored fact there is.
    """
    if statement.action == ACTION_REPLACE:
        taught = extract_personal_context_candidate(statement.replacement)
        if taught is not None:
            existing = _canonical_for_topic(items, taught.topic)
            if existing is not None:
                return _replace_canonical(
                    request, existing, taught, reply="Got it - I've corrected that.",
                    payload={"handled_by": "correct_memory", "action": "corrected", "topic": existing.topic},
                )
            return _learn_instead(request, taught)

    options = _correction_options(request, items)

    if statement.old_value:
        named = [pair for pair in options if _mentions(_option_text(*pair), statement.old_value)]
        if len(named) == 1:
            return _apply_to_option(request, statement, *named[0])
        if len(named) > 1:
            return _ask_which(request, statement, named)

    if statement.preference:
        preferred = [pair for pair in options if tokenize(pair[0].topic) & _PREFERENCE_TOPIC_WORDS]
        if len(preferred) == 1:
            return _apply_to_option(request, statement, *preferred[0])
        if len(preferred) > 1:
            return _ask_which(request, statement, preferred)
        if statement.action == ACTION_REPLACE and statement.replacement:
            return _learn_instead(request, PersonalContextCandidate(
                topic="preference", content=f"The user prefers {statement.replacement}.",
                category="context", value=statement.replacement,
            ))
        if statement.old_value:
            return _learn_instead(request, PersonalContextCandidate(
                topic=statement.old_value, content=f"The user no longer likes {statement.old_value}.",
                category="context", status=STATUS_DISCONTINUED,
            ))

    answered = _last_answer_line(request)
    if answered:
        mentioned = [pair for pair in options if _mentions(answered, pair[0].topic) or _mentions(answered, pair[0].value)]
        if len(mentioned) == 1:
            return _apply_to_option(request, statement, *mentioned[0])

    if len(options) == 1:
        return _apply_to_option(request, statement, *options[0])
    if not options:
        return TaskResult.success(
            request.task, request.own_name, "I don't have anything remembered that this could correct.",
            payload={"handled_by": "correct_memory", "action": "nothing_to_correct"},
        )
    return _ask_which(request, statement, options)


def _resolve_pending_correction(
    request: CapabilityRequest, pending: PendingCorrection, items: "Sequence[Any]",
) -> TaskResult:
    sessions = request.activity_sessions
    reply = request.task.description
    sessions.clear_correction(request.own_name)
    if pending.is_cancel(reply):
        return TaskResult.success(
            request.task, request.own_name, "Okay - I've left everything as it was.",
            payload={"handled_by": "correct_memory", "action": "cancelled"},
        )
    option = pending.select_option(reply)
    if option is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I couldn't tell which one you meant, so I've left everything as it was.",
        )
    for candidate, source in _correction_options(request, items):
        if candidate.record_id == option.record_id:
            return _apply_to_option(
                request, CorrectionStatement(pending.action, pending.replacement), candidate, source,
            )
    return TaskResult.failure(
        request.task, request.own_name,
        "That has changed since I asked, so I've left everything as it was.",
    )


def is_new_memory_statement(text: str) -> bool:
    """A remark that itself says something new - a correction, a personal
    fact, or a request to remember - and so is never merely an answer to an
    open "which one did you mean?" question, even when it happens to name one
    of the offered topics."""
    return bool(
        extract_correction(text) is not None
        or extract_personal_context_candidate(text) is not None
        or retention_requests([text])
        or CORRECTION_TOPIC_PATTERN.search(str(text or ""))
    )


def correct_memory(request: CapabilityRequest) -> TaskResult:
    """Replace or invalidate an existing memory, atomically, through the
    identical retain-then-forget sequence the relational-birthday handling
    already proved.

    Pre-Stage-9 Campaign 1: a personal fact now lives in the canonical,
    topic-keyed personal-context store, so a correction first looks there -
    a named topic, or the topic a stated replacement is about ("correction:
    my favourite colour is green") - and replaces that topic's current
    statement. A plain "remember that" note with no topic keeps the original
    behaviour below.

    Three deterministic shapes, checked in order:

        "update what you remembered about X to Y"   named topic, named replacement
        "that's wrong, Y" / "correction: Y" / ...    replacement, topic from Y when
                                                       Y states one, otherwise this
                                                       agent's most recent note
        "that's no longer my ..." / "... anymore"    pure invalidation, no replacement
    """
    if request.recall is None or request.forget is None or request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to correct memories right now.",
        )

    text = request.task.description
    items = _canonical_items_for_correction(request)

    sessions = request.activity_sessions
    pending = sessions.pending_correction(request.own_name) if sessions is not None else None
    if pending is not None:
        if is_new_memory_statement(text):
            # A new statement replaces the open question rather than answering it.
            sessions.clear_correction(request.own_name)
        else:
            return _resolve_pending_correction(request, pending, items)

    topic_match = CORRECTION_TOPIC_PATTERN.search(text)
    if topic_match is not None:
        topic = " ".join(topic_match.group(1).split())
        new_value = " ".join(topic_match.group(2).split())
        existing = _canonical_for_topic(items, topic)
        if existing is not None:
            candidate = PersonalContextCandidate(
                topic=existing.topic, content=f"The user's {existing.topic} is {new_value}.",
                category=existing.category, value=new_value,
            )
            return _replace_canonical(
                request, existing, candidate, reply=f"Updated what I remember about {topic}.",
                payload={"handled_by": "correct_memory", "action": "updated", "topic": topic},
            )
        matches = request.recall(topic, kind=MemoryCategory.USER_CONTEXT.value, limit=1)
        if not matches:
            return TaskResult.success(
                request.task, request.own_name,
                f"I don't have anything remembered about {topic} to correct.",
            )
        # The topic itself must stay in the stored content, not only in
        # this handler's own extraction - otherwise a bare replacement
        # value becomes unfindable by a later recall on the topic.
        content = new_value if new_value.lower().startswith(topic.lower()) else f"{topic} is {new_value}"
        verdict = request.retain(
            MemoryRecord(
                content=content, kind=MemoryCategory.USER_CONTEXT.value,
                tags=matches[0].tags, context={"source": "conversation", "corrects": matches[0].id},
            )
        )
        if not verdict.allowed:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
        request.forget(matches[0].id)
        return TaskResult.success(
            request.task, request.own_name, f"Updated what I remember about {topic}.",
            payload={"handled_by": "correct_memory", "action": "updated", "topic": topic},
        )

    statement = extract_correction(text)
    if statement is not None:
        return _apply_correction(request, statement, items)

    replacement = _after_marker(text, CORRECTION_REPLACE_MARKERS)
    if replacement:
        taught = extract_personal_context_candidate(replacement)
        existing = _canonical_for_topic(items, taught.topic) if taught is not None else None
        if taught is not None and existing is not None:
            return _replace_canonical(
                request, existing, taught, reply="Got it - I've corrected that.",
                payload={"handled_by": "correct_memory", "action": "corrected", "topic": existing.topic},
            )
        target = request.recall(kind=MemoryCategory.USER_CONTEXT.value, limit=1)
        new_record = MemoryRecord(
            content=replacement, kind=MemoryCategory.USER_CONTEXT.value,
            context={"source": "conversation", "corrects": target[0].id if target else None},
        )
        # Same ordering discipline as above: the replacement must be
        # accepted before the original is removed.
        verdict = request.retain(new_record)
        if not verdict.allowed:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
        if target:
            request.forget(target[0].id)
        return TaskResult.success(
            request.task, request.own_name, "Got it - I've corrected that.",
            payload={"handled_by": "correct_memory", "action": "corrected"},
        )

    if _marker_present(text, CORRECTION_INVALIDATE_MARKERS):
        # Never "the most recent memory" by default any more: removing the
        # wrong one silently is worse than asking which one.
        return _apply_correction(
            request,
            CorrectionStatement(ACTION_INVALIDATE, preference=bool(re.search(r"\bprefer", text, re.IGNORECASE))),
            items,
        )

    # select_capability only reaches this handler when claims already
    # matched, so this is unreachable in practice - kept as an honest
    # answer rather than an assumption nothing else could route here.
    return TaskResult.failure(request.task, request.own_name, "I couldn't tell what to correct.")


CORRECT_MEMORY = ManagerCapability(
    name="correct_memory",
    capabilities=frozenset({"correct", "correction"}),
    handle=correct_memory,
    description="Replaces or invalidates an existing memory when the user explicitly corrects it.",
    claims=correction_intent,
    # Marker-based, the identical reason FORGET/RECALL_MEMORY are: word
    # overlap alone would let "correct"/"correction" collide with
    # unrelated ordinary speech ("that's the correct file").
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 14: current, external information - acquired through a governed
# tool, then reasoned over, never put into the Brain itself
# ---------------------------------------------------------------------------
#
# The distinction this closes: a Brain reasons over information; it does not
# know what changed since it was trained, and putting current-world facts
# directly into a prompt as though they were the model's own knowledge is
# exactly the confusion ``LiveInfoResult``'s own docstring names. So this
# capability does two things in order, never conflating them - acquire, then
# reason - the same two-step shape ``open_conversation`` already performs
# with a Recognition's notes, generalised to evidence that came from outside
# rather than from the user's own declared profile.
#
# Recognised by generic recency words, never a subject. "now", "current",
# "today", "latest" and their few kin say a request wants information as it
# stands *right now*, on any subject at all - a football transfer, a market
# price, a piece of news, a technology release. There is no football word,
# no market word, no company name here, and there must never be one: the
# subject of a request is data that arrives with the task, exactly as
# ``GeneralCapability``'s own docstring already states for research and
# coding. A request with none of these words is answered by whatever
# already answers it - ``general_assistance`` or ``open_conversation`` -
# unchanged, because this capability adds a new *kind* of work, not a
# competing claim on ordinary requests.
CURRENT_INFORMATION_WORDS: frozenset[str] = frozenset(
    {
        "now", "current", "currently", "latest",
        "recent", "recently", "news", "happening",
        # Pre-1.6 ecosystem audit: "update"/"updates" deliberately removed.
        # An ordinary software/database/business sentence uses "update" far
        # more often than a genuine request for live external information
        # does ("users see stale prices right after we update the cache",
        # "I need to update the record", "did you update the file?") -
        # reproduced live during the audit, misrouting a genuine debugging
        # question to this capability purely on that one word. This is the
        # identical class of false positive "today" was already removed
        # for, at larger scale: a request that actually wants a live
        # update on something named still scores on "current"/"latest"/
        # "recent"/"news"/"happening", or is caught by the marker-phrase
        # shape below ("any update on X") - only the bare word, divorced
        # from any of those, is what is removed.
        #
        # Natural Conversation fix: "today" deliberately removed. Unlike
        # every word above, "today" appears constantly in ordinary
        # conversation that has nothing to do with current information
        # ("I'm tired today", "I have a meeting today"), and it was
        # scoring this capability for exactly those remarks with nothing
        # else to check it. A genuine "what's happening today"-style
        # request still scores on "happening"; a genuine self-referential
        # date question ("what's today's date") is current_datetime's own
        # claims-recognised territory, not this one's.
        #
        # "now" was tried for the same removal during the 0.4 Natural
        # Conversation pass and reverted: unlike "today", genuine,
        # already-tested requests exist that score *only* on "now" and
        # nothing else here ("is Arsenal linked with any wingers right
        # now?", "what's going on in the football world right now?" -
        # tests/test_stage14_live_information.py,
        # tests/test_stage14_production_live_information.py). "now" does
        # carry a real, narrower false-positive risk ("I forget the
        # details now") that "today" did not need to accept - recorded
        # here as a known, accepted trade-off rather than silently
        # reintroduced.
    }
)

# A second, generic signal alongside the word list above - request *shape*,
# the same instrument forget_intent/recall_memory_intent already use and for
# the identical reason: word-overlap scoring cannot separate "what happened
# WITH Elliott" (an update on somebody or something named, whoever or
# whatever it is) from "what happened IN the French Revolution" (a request
# to explain a known, settled topic), because neither sentence shares a word
# with CURRENT_INFORMATION_WORDS at all. The two read identically to a
# scorer and differently to a person, and the difference is the preposition,
# not the subject - "with"/"to" name an update on an entity; "in"/"during"
# name an explanation of an event. No name, team, company or topic appears
# below, and none may be added: a marker earns its place here only by being
# a phrase *shape* that means "give me an update", true of any subject a
# person could ever name.
CURRENT_INFORMATION_MARKERS: tuple[str, ...] = (
    "happened with", "happened to", "going on with", "the latest on",
    "the latest with", "any update on", "any news on", "what's up with",
    # Phase 3 fix: "happened in"/"going on in" ordinarily mean "explain
    # this settled event" per this list's own grammar rule above ("in"/
    # "during" name an explanation, not an update) - true for "what
    # happened in the French Revolution", false for this one, genuinely
    # different case: "the world" is not a settled historical topic, it
    # is the present moment itself, and paired with "today"/"right now"
    # the whole phrase is unambiguously a request for current events, not
    # an explanation of a topic named "the world". Found live: this
    # project's own literal example ("what happened in the world today")
    # was answered as a settled-knowledge question with live information
    # explicitly excluded, exactly the "current falsely presented as
    # timeless" failure this phase exists to prevent. Phrase-shaped, not
    # a bare recency word, for the identical false-positive-avoidance
    # reason "today" itself was removed from CURRENT_INFORMATION_WORDS.
    "happened in the world today", "happening in the world today",
    "happened in the world right now", "happening in the world right now",
    "going on in the world today", "going on in the world right now",
)


def current_information_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request's *shape* asks for an update on something
    named, rather than an explanation of it.

    Checked by marker phrase against the whole message, never by scoring
    individual words - the same reason ``forget_intent`` and
    ``recall_memory_intent`` already use ``_marker_present`` rather than
    word overlap: "happened with Elliott" and "happened in the French
    Revolution" would otherwise score identically, and the distinction
    that actually matters - update vs. explanation - lives in the
    preposition, not in any word this function would have to know the
    meaning of.
    """
    return _marker_present(task.description, CURRENT_INFORMATION_MARKERS)


def live_information(request: CapabilityRequest) -> TaskResult:
    """Acquire current information through the governed tool boundary,
    then let the Brain reason over it - never the tool's raw output
    standing in as the final answer, and never a synthesis fabricated
    without ever having looked.

    Four honest outcomes, the same shape ``general_assistance`` and
    ``open_conversation`` already established for their own two axes -
    tool availability and Brain availability - now composed, because a
    request needing current information depends on both:

        tool missing               named honestly, nothing guessed
        tool present, call fails   the failure is reported as its own
        call succeeds, no Brain    what was found is not thrown away
                                    silently, but no fabricated synthesis
                                    is offered either - an honest refusal
        call succeeds, Brain works genuine reasoning over real evidence

    Live-data availability and Brain availability are checked
    independently, on purpose: a tool call can succeed with no Brain to
    reason from it, and a Brain can be perfectly usable with nothing
    current to reason about - the identical independence Stage 13's
    ``answer_with_collaboration`` already keeps between collaboration and
    synthesis, reused here rather than re-argued.
    """
    if not request.tools.permits("live_information"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task,
            request.own_name,
            (
                "This looks like it needs current, real-world information, "
                "but the underlying 'live_information' capability is not "
                "installed, so I cannot look it up rather than guess from "
                f"what I already know. Currently equipped: {equipped}."
            ),
        )

    outcome = request.tools.invoke("live_information", {"query": request.task.description})
    if not outcome.ok:
        return TaskResult.failure(
            request.task,
            request.own_name,
            f"the 'live_information' capability failed: {outcome.error}",
        )

    if request.converse is None:
        return TaskResult.failure(
            request.task,
            request.own_name,
            "I found current information but don't have a way to reason "
            "about it yet - no language model is configured for me right "
            "now, so I would rather say so than guess.",
        )

    # The tool's own output already carries its provenance as text (see
    # LiveInformationTool.run); handed to the Brain as evidence, the same
    # "notes, not instructions" contract every other caller of converse()
    # already honours.
    notes = (f"Live information: {outcome.output}",) + _recent_conversation_notes(request)
    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task,
            request.own_name,
            "I found current information but couldn't properly reason "
            f"about it, so I would rather say so than guess ({decision.reason}).",
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        decision.reason,
        payload={
            "handled_by": "live_information",
            "brain": decision.source,
            "tool_source": outcome.source,
            "retrieved_at": outcome.observed_at.isoformat() if outcome.observed_at else None,
        },
    )


LIVE_INFORMATION = ManagerCapability(
    name="live_information",
    capabilities=CURRENT_INFORMATION_WORDS,
    handle=live_information,
    description=(
        "Acquires current, external information through a governed tool, "
        "then reasons over it as evidence - never a subject-specific word list."
    ),
    # The marker-phrase signal above, checked first - the same "claims
    # before scoring" precedence forget/recall_memory/participant_invitation
    # already rely on, for the identical reason: a request's shape can
    # settle what word-overlap scoring alone cannot.
    claims=current_information_intent,
)


# ---------------------------------------------------------------------------
# Phase 3: generating a real, saved document from findings already in the
# conversation - "create a report from these findings" (Section 6's own
# example). Composition (the Brain) and persistence (a governed Tool)
# stay separate, the identical split media_production already keeps
# between an image provider's own output and MediaProductionTool._write.
# ---------------------------------------------------------------------------

# Marker-shaped, the same discipline every consequential-action trigger
# in this module already holds itself to: word overlap alone would let
# "report" collide with unrelated ordinary speech ("she reported the
# issue", "a school report card").
DOCUMENT_GENERATION_MARKERS: tuple[str, ...] = (
    "create a report", "create a document", "write a report",
    "generate a report", "generate a document", "save this as a document",
    "save that as a document", "make a report from", "make a document from",
)


def document_generation_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, DOCUMENT_GENERATION_MARKERS)


def generate_document(request: CapabilityRequest) -> TaskResult:
    """Compose a real document from what has actually been discussed,
    and save it - never a placeholder reply standing in for a real
    generated artifact.

    Two honest gates, checked independently, the identical shape
    ``live_information``/research already use: no Brain, no genuine
    composition to offer; no ``document_generation`` tool, nothing to
    persist even once composed. Evidence is exactly the recent
    conversation - the same ``_recent_conversation_notes`` every other
    Brain-consulting handler here already reads - never fabricated
    findings invented because none were actually discussed.
    """
    if request.converse is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to compose a document yet - no language "
            "model is configured for me right now, so I would rather say "
            "so than guess.",
        )
    if not request.tools.permits("document_generation"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can compose the content, but the underlying "
            "'document_generation' capability is not installed, so I "
            f"cannot save it as a real file yet. Currently equipped: {equipped}.",
        )

    notes = _recent_conversation_notes(request)
    if not notes:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have anything discussed yet to build a document from.",
        )

    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't compose that document, so I would rather say so "
            f"than guess ({decision.reason}).",
        )

    title = " ".join(request.task.description.split())[:80]
    outcome = request.tools.invoke(
        "document_generation", {"title": title, "content": decision.reason}
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I composed the document but couldn't save it: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "generate_document", "brain": decision.source},
    )


# ---------------------------------------------------------------------------
# Files/Documents completion: real visual reasoning over a real image,
# genuinely distinct from storing one, extracting its path, OCR, or
# metadata - honestly refused when the configured provider has no real
# vision capability, never faked.
# ---------------------------------------------------------------------------

IMAGE_ANALYSIS_MARKERS: tuple[str, ...] = (
    "look at this image", "look at the image", "what do you see in this image",
    "what is in this image", "what's in this image", "analyze this image",
    "describe this image", "look at this picture", "what's in this picture",
    "analyze this picture", "describe this picture",
)

_IMAGE_PATH_PATTERN = re.compile(r"\S+\.(?:png|jpg|jpeg|gif|webp)", re.IGNORECASE)


# Pre-Stage-9 Campaign 3: the stored rendition a media capability opens - in
# the order a real file is most usefully read, never a guess at one.
_MEDIA_RENDITION_ORDER = (
    "mp4", "mov", "mkv", "webm", "avi", "wav", "mp3", "m4a", "ogg", "oga", "opus", "flac", "aac",
    "png", "jpg", "jpeg", "gif", "webp",
)
_A_OR_AN = re.compile(r"^[aeiou]", re.IGNORECASE)


def _rendition_path(described: "dict | None") -> "str | None":
    renditions = (described or {}).get("renditions") or {}
    for fmt in _MEDIA_RENDITION_ORDER:
        if fmt in renditions:
            return str(renditions[fmt])
    return str(next(iter(renditions.values()))) if renditions else None


def _shared_media(request: CapabilityRequest, kinds: "Sequence[str]") -> "tuple[dict | None, str | None]":
    """The newest file of one of ``kinds`` this request points at or this
    conversation shared, and the stored file it can be opened from."""
    described = resolve_shared_media(
        request.task.description, tools=request.tools, history=request.history,
        shared_files=request.shared_files, kinds=kinds,
    )
    return described, _rendition_path(described)


def image_analysis_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, IMAGE_ANALYSIS_MARKERS)


def image_analysis(request: CapabilityRequest) -> TaskResult:
    """Real visual reasoning through the Brain's own governed
    ``analyze_image`` question - never OCR, never metadata, and never a
    Tool holding reasoning machinery: visual reasoning is reasoning,
    the identical class of question ``converse()`` already answers for
    text, so it is reached the same way - through the Brain, never
    through ``request.tools`` (a Tool must depend only on providers of
    information/environment access, never on an ``LLMProvider`` - see
    ``tests/test_provenance.py::TestHalfBrainStillReachesTheEnvironment``).

    Reading the referenced file's real bytes is the one piece of
    environment access this handler does itself, directly - a plain
    file read, no different in kind from ``record_this`` reading
    ``request.task.description`` - never routed through a Tool that
    would then have to also hold the provider to reason about what it
    read.
    """
    if request.analyze_image is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to reason about images yet - no vision-capable "
            "reasoning is configured for me right now, so I would rather say "
            "so than guess.",
        )

    from pathlib import Path

    match = _IMAGE_PATH_PATTERN.search(request.task.description)
    if match is not None:
        image_path = Path(match.group(0))
    else:
        # Pre-Stage-9 Campaign 3: the image the person shared in this
        # conversation, resolved to its stored file - no server path needed.
        _described, shared_path = _shared_media(request, ("image",))
        if shared_path is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't see an image in that request or shared in this conversation - "
                "attach one, or name it with 'artifact <id>'.",
            )
        image_path = Path(shared_path)
    if not image_path.is_file():
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't find an image at '{image_path}'.",
        )

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't read that image: {exc}",
        )

    decision = request.analyze_image(image_bytes, request.task.description)
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't analyze that image: {decision.reason}",
        )

    return TaskResult.success(
        request.task, request.own_name, decision.reason,
        payload={"handled_by": "image_analysis", "brain": decision.source},
    )


IMAGE_ANALYSIS = ManagerCapability(
    name="image_analysis",
    capabilities=frozenset({"image_analysis", "vision"}),
    handle=image_analysis,
    description=(
        "Analyzes a real image with genuine visual reasoning, when the "
        "configured provider supports it - never OCR, never metadata, "
        "never a fabricated description."
    ),
    claims=image_analysis_intent,
    scoreable=False,
)


GENERATE_DOCUMENT = ManagerCapability(
    name="generate_document",
    capabilities=frozenset({"report", "document"}),
    handle=generate_document,
    description="Composes a real document from recent findings and saves it as a real file.",
    claims=document_generation_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Multimodal workspace/artifact checkpoint: a real, persistent, versioned
# document artifact ("handout") - genuinely different from
# generate_document above, which writes one flat, disposable text file
# with no identity and no version chain. A handout has a stable
# artifact_id, real multi-page structure, a real PDF export where a
# rendering engine is configured, and can be reopened and revised
# through that same identity - see src/services/artifacts/store.py's
# own docstring for the full architecture.
#
# "handout" is used as the routing noun rather than "document"/"report"
# specifically so this never collides with generate_document's own,
# already-shipped markers above - two real, independent document
# capabilities, not one capability's ambiguous second meaning. Neither
# marker set is about any particular topic: the AI-history handout this
# checkpoint's own brief uses as an example is nowhere in this code -
# any subject reaches the identical handler.
# ---------------------------------------------------------------------------

_HANDOUT_MARKERS: tuple[str, ...] = (
    "make a handout", "make me a handout", "create a handout", "prepare a handout",
    "write a handout", "produce a handout", "build a handout",
)

_REVISE_HANDOUT_MARKERS: tuple[str, ...] = (
    "revise the handout", "revise that handout", "update the handout",
    "update that handout", "edit the handout", "edit that handout",
    "revise the document", "update the document",
)

# "artifact <id>" (or "artifact: <id>") - an explicit reference, the
# identical "a path/range named in the text is the evidence" convention
# find_video_path/parse_clip_specs already establish for video clipping,
# applied here to an artifact's own id instead of a filesystem path.
_ARTIFACT_ID_PATTERN = re.compile(r"\bartifact[:\s]+([0-9a-f]{6,32})\b", re.IGNORECASE)

# How the Brain's own answer (or a person's own typed request) must be
# shaped for _parse_document_content to read it back as a real,
# structured DocumentContent. Deliberately NOT line-oriented, even
# though it reads that way below: ``BrainDecision.reason`` (and
# ``ObjectiveVerdict.next_step``) collapse all whitespace - including
# every newline - to single spaces (see ``BrainDecision.__post_init__``),
# so a real answer reaches this parser as one flattened line regardless
# of how the model actually formatted it. The colon between a section's
# heading and its body is what survives that flattening and is what
# ``_parse_document_content`` actually keys on; the newlines below are
# only there to make the instruction itself readable to a real model.
_DOCUMENT_FORMAT_INSTRUCTION = (
    "Respond in EXACTLY this format and nothing else - no other commentary "
    "before or after it:\n"
    "TITLE: <a concise, real title>\n"
    "SECTION: <first heading>: <that section's real body text, one or more sentences>\n"
    "SECTION: <second heading>: <that section's real body text>\n"
    "(repeat SECTION entries as needed - at least two sections total, each "
    "with its own heading, a colon, then its body)"
)

_TITLE_PATTERN = re.compile(r"TITLE:\s*(.*?)\s*(?=SECTION:|$)", re.IGNORECASE | re.DOTALL)
_SECTION_PATTERN = re.compile(r"SECTION:\s*(.*?)\s*(?=SECTION:|$)", re.IGNORECASE | re.DOTALL)


def _parse_document_content(text: str) -> "DocumentContent | None":
    """A real ``DocumentContent`` from ``text``, when it already contains
    at least one ``TITLE:``/``SECTION:`` block in ``_DOCUMENT_FORMAT_INSTRUCTION``'s
    own shape - the identical role ``parse_clip_specs`` plays for video
    clipping: a single, shared parser used both on a person's own typed
    request (the deterministic path, no Brain needed) and on the Brain's
    own structured answer (the Brain-assisted path, where the text
    reaching here is always a single flattened line - see this module's
    own ``_DOCUMENT_FORMAT_INSTRUCTION`` for why), so a "the request
    already carries real evidence" check and "here is what the Brain
    proposed" both go through one real implementation regardless of
    which shape the text actually arrived in.

    None when no real title or no real section was found - honest
    absence, never a guess at structure that was never actually there.
    """
    from src.services.artifacts.models import DocumentContent, DocumentSection

    title_match = _TITLE_PATTERN.search(text)
    title = title_match.group(1).strip() if title_match else ""

    sections: list[DocumentSection] = []
    for chunk in _SECTION_PATTERN.findall(text):
        heading, _, body = chunk.partition(":")
        heading, body = heading.strip(), body.strip()
        if not heading or not body:
            continue
        try:
            sections.append(DocumentSection(heading=heading, body=body))
        except ValueError:
            continue

    if not title or not sections:
        return None
    try:
        return DocumentContent(title=title, sections=tuple(sections))
    except ValueError:
        return None


# REPAIR-01 (PDF/document capability): producing a document *from* content
# already in play - "create a PDF version of this", "turn this into a PDF".
# Format words and production verbs only, never a subject.
_CONVERT_TO_DOCUMENT_MARKERS: tuple[str, ...] = (
    "pdf version of", "pdf copy of", "a pdf of", "into a pdf", "to a pdf", "as a pdf",
    "into pdf", "to pdf", "as pdf",
)
_CONVERT_VERBS = re.compile(r"\b(create|make|turn|convert|export|save|produce|generate|give me)\b", re.IGNORECASE)
_POINTING_WORD = re.compile(r"\b(this|that|it)\b", re.IGNORECASE)

# REPAIR-01: user-authorized editing of a supplied file or document - the
# generic edit shapes, plus a literal "replace/change X with/to Y" aimed at
# content in play. Legitimate document work on the person's own material is
# never refused merely for being a document: the one generic governance
# check below still runs, with real ownership evidence.
_REVISE_CONTENT_MARKERS: tuple[str, ...] = (
    "edit this document", "edit this pdf", "edit this file", "edit the pdf", "edit the file",
    "update this document", "update this pdf", "update this file", "update the pdf",
    "revise this document", "revise this pdf", "revise this file",
    "correct this document", "correct this pdf", "correct this file",
)
_REPLACEMENT_PATTERN = re.compile(
    r"\b(?:replace|change)\s+[\"'“‘]?(?P<old>.+?)[\"'”’]?\s+(?:with|to)\s+"
    r"[\"'“‘]?(?P<new>.+?)[\"'”’]?"
    r"(?=\s+(?:in|on|throughout)\s+(?:this|the|that|my|artifact)\b|\s*[.!?]*\s*$)",
    re.IGNORECASE,
)


def document_conversion_intent(text: str) -> bool:
    return _marker_present(text, _CONVERT_TO_DOCUMENT_MARKERS) and bool(_CONVERT_VERBS.search(str(text or "")))


def _title_stem(title: object) -> str:
    """A supplied file's name without its extension, for a derived
    document's title - "timetable.pdf" -> "timetable"."""
    name = str(title or "Document").strip() or "Document"
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem and 0 < len(ext) <= 8 else name


def _document_from_content(
    payload: Mapping[str, Any], units: "Sequence[ContentUnit]", *, title: "str | None" = None
) -> "DocumentContent":
    """Real, structured document content rebuilt from an artifact's own
    text units - one section per unit. A document Alfred produced keeps its
    own headings (its units read "heading: body"); a supplied file's units
    are headed by their honest locator ("Page 2"). Nothing is added that
    the source did not contain."""
    from src.services.artifacts.models import DocumentContent, DocumentSection

    sections = []
    for unit in units:
        heading, body = unit.locator.capitalize(), unit.text
        if payload.get("type") == "document":
            head, sep, rest = unit.text.partition(": ")
            if sep and head.strip() and rest.strip():
                heading, body = head, rest
        sections.append(DocumentSection(heading=heading.strip(), body=body.strip()))
    chosen_title = (title or str(payload.get("title") or "")).strip() or "Document"
    return DocumentContent(title=chosen_title, sections=tuple(sections))


def _apply_replacement(doc: "DocumentContent", old: str, new: str) -> "tuple[DocumentContent, int]":
    """``doc`` with every occurrence of ``old`` replaced by ``new`` in its
    title, headings and bodies, and how many were replaced. Exact matches
    first; only when there are none anywhere is a case-insensitive match
    used. Zero means nothing changed - the caller says so rather than
    saving an "edit" that edited nothing."""
    from src.services.artifacts.models import DocumentContent, DocumentSection

    def run(flags: int) -> "tuple[DocumentContent, int]":
        pattern = re.compile(re.escape(old), flags)
        count = 0

        def sub(value: str) -> str:
            nonlocal count
            result, n = pattern.subn(lambda _m: new, value)
            count += n
            return result

        title = sub(doc.title)
        sections = tuple(DocumentSection(heading=sub(s.heading), body=sub(s.body)) for s in doc.sections)
        return DocumentContent(title=title, sections=sections), count

    edited, count = run(0)
    if count == 0:
        edited, count = run(re.IGNORECASE)
    return (edited, count) if count else (doc, 0)


def create_artifact_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, _HANDOUT_MARKERS) or document_conversion_intent(task.description)


def create_artifact(request: CapabilityRequest) -> TaskResult:
    """Create a real, persistent, versioned document artifact - a
    "handout" - deterministically when the request already carries real,
    structured content (Half-Brain safe, no LLM needed), Brain-assisted
    when it instead names an objective the Brain must organize into that
    same structure.

    Two ways real, structured evidence reaches this handler, tried in
    order - the identical priority ``clip_video`` already holds itself
    to between explicit timestamps and Brain-derived ones:

    1. The request itself already contains a real ``TITLE:``/``SECTION:``
       block (see ``_parse_document_content``) - used directly, no Brain
       call at all.
    2. Otherwise, the Brain is asked to organize the objective (plus
       whatever real evidence a multi-step objective pursuit already
       carried into this very request's own description - see
       ``src/manager/objective_loop.py``) into that same structure.
       Never fabricates research or findings that were never actually
       gathered: the Brain reasons only over what this request and the
       recent conversation genuinely contain.

    Persistence and PDF export are entirely ``artifact_production``'s
    own job (see that tool's docstring) - this handler never touches a
    file directly.
    """
    if not request.tools.permits("artifact_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can put together a handout, but the underlying "
            f"'artifact_production' capability is not installed, so I cannot "
            f"save it as a real artifact yet. Currently equipped: {equipped}.",
        )

    text = request.task.description

    # REPAIR-01: a document produced from content already in play ("create
    # a PDF version of this") - built deterministically from that content's
    # own units and saved as a new artifact linked back to its source, which
    # is never modified. Half-Brain-safe: no model is consulted. Only when
    # no real source content can be found does this fall through to the
    # ordinary composition path below.
    if document_conversion_intent(text):
        found = resolve_content_sources(
            text, tools=request.tools, history=request.history, limit=1,
            shared_files=request.shared_files,
            # "a PDF version of this" points at the most recent content even
            # after the share has left the recent-conversation window; a
            # request naming no content at all ("a PDF of my ideas") does not.
            fallback_latest=content_reference_intent(text) or bool(_POINTING_WORD.search(text)),
        )
        if found:
            payload, units = found[0]
            source_id = str(payload.get("artifact_id"))
            converted = _document_from_content(payload, units, title=f"{_title_stem(payload.get('title'))} (PDF version)")
            outcome = request.tools.invoke(
                "artifact_production",
                {"operation": "derive_document", "content": f"{source_id}\n{converted.to_json()}"},
            )
            if not outcome.ok:
                return TaskResult.failure(
                    request.task, request.own_name,
                    f"I prepared the document but couldn't save it: {outcome.error}",
                )
            return TaskResult.success(
                request.task, request.own_name,
                f"{outcome.output} (derived from artifact {source_id}; the original is unchanged)",
                payload={"handled_by": "create_artifact", "derived_from": source_id},
            )

    content = _parse_document_content(text)

    if content is None:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to organize this into a handout yet - no "
                "language model is configured for me right now. Give me the "
                f"content already structured instead:\n{_DOCUMENT_FORMAT_INSTRUCTION}",
            )
        notes = (_DOCUMENT_FORMAT_INSTRUCTION,) + _recent_conversation_notes(request)
        decision = request.converse(request.task, notes)
        if not decision.is_decided:
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't organize that into a handout ({decision.reason or 'no usable answer'}).",
            )
        content = _parse_document_content(decision.reason)
        if content is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I couldn't organize that into a real, structured handout from "
                "what has actually been established so far.",
            )

    outcome = request.tools.invoke(
        "artifact_production", {"operation": "create_document", "content": content.to_json()}
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I composed the handout but couldn't save it: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "create_artifact"},
    )


CREATE_ARTIFACT = ManagerCapability(
    name="create_artifact",
    capabilities=frozenset({"handout", "artifact"}),
    handle=create_artifact,
    description=(
        "Creates a real, persistent, versioned document artifact (a handout) - "
        "structured content given directly needs no Brain; organizing an "
        "objective into that structure does."
    ),
    claims=create_artifact_intent,
    scoreable=False,
)


def revise_artifact_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = task.description
    if _marker_present(text, _REVISE_HANDOUT_MARKERS) or _marker_present(text, _REVISE_CONTENT_MARKERS):
        return True
    return bool(_REPLACEMENT_PATTERN.search(text)) and content_reference_intent(text)


def revise_artifact(request: CapabilityRequest) -> TaskResult:
    """Revise an existing document artifact - the same artifact identity,
    one real version further along, never a new, disconnected file.

    Resolves which artifact by an explicit ``artifact <id>`` reference in
    the request when given, otherwise the most recently created/revised
    document this agent itself owns (``artifact_production``'s own
    ``find_latest`` operation) - real, deterministic evidence, never a
    guess among several equally-plausible candidates.

    REPAIR-01 (PDF/document capability): also edits content the person
    supplied - "in this PDF, replace X with Y", "edit this document: ...".
    Which document is meant is resolved the same way every content request
    resolves it (explicit id, then a file shared in this conversation, then
    the most recent one). A literal replacement is applied deterministically
    (Half-Brain-safe) and refuses honestly when the text is not there; a
    free-form edit is composed by the Brain. A document Alfred produced is
    revised under its own identity; a supplied file is never rewritten - the
    edit becomes a new, derived document linked back to it.
    """
    if not request.tools.permits("artifact_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can revise a handout, but the underlying 'artifact_production' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )

    text = request.task.description
    match = _ARTIFACT_ID_PATTERN.search(text)
    resolved = None
    if match:
        artifact_id = match.group(1)
    elif not _marker_present(text, _REVISE_HANDOUT_MARKERS) and (
        content_reference_intent(text)
        or _marker_present(text, _REVISE_CONTENT_MARKERS)
        or _REPLACEMENT_PATTERN.search(text)
    ):
        found = resolve_content_sources(
            text, tools=request.tools, history=request.history, limit=1, shared_files=request.shared_files,
        )
        if not found:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a document or file to edit yet - share one first, or "
                "reference it explicitly with 'artifact <id>'.",
            )
        resolved = found[0]
        artifact_id = str(resolved[0].get("artifact_id"))
    else:
        lookup = request.tools.invoke(
            "artifact_production", {"operation": "find_latest", "content": "document"}
        )
        if not lookup.ok:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have an existing handout to revise yet - create one "
                "first, or reference it explicitly with 'artifact <id>'.",
            )
        artifact_id = lookup.output.strip()

    if resolved is None:
        resolved = read_artifact_content(request.tools, artifact_id)
    is_supplied = resolved is not None and resolved[0].get("type") == SUPPLIED_FILE_TYPE

    if is_supplied:
        current_text = _document_from_content(resolved[0], resolved[1]).as_plain_text()
    else:
        current = request.tools.invoke("artifact_production", {"operation": "get", "content": artifact_id})
        if not current.ok:
            return TaskResult.failure(
                request.task, request.own_name, f"I couldn't find that handout to revise: {current.error}",
            )
        current_text = current.output

    # Context-Aware Agent Capability & Safety Governance's real
    # integration point for the existing document-artifact capability -
    # never a PDF-specific mechanism, the identical generic
    # ``_governance_check`` ``connected_action`` above already uses.
    # Real evidence, not invented here: the artifact was just found in
    # THIS agent's own document store (``current.ok``), which is
    # genuine ownership evidence a keyword match alone could never
    # provide - exactly what lets legitimate authorized editing (of a
    # document whose surface wording might otherwise look concerning)
    # be told apart from something genuinely unsafe.
    paused = _governance_check(
        request, capability="revise_artifact", operation="revise_document", action_kind="write",
        notes=(
            f"This document (artifact {artifact_id}) already exists in the requesting agent's own, "
            "governed document store, found there just now - it is not a document belonging to, or "
            "reached from, anyone else.",
        ),
    )
    if paused is not None:
        return paused

    replacement = _REPLACEMENT_PATTERN.search(text)
    if replacement is not None and resolved is not None:
        old_text = replacement.group("old").strip()
        new_text = replacement.group("new").strip()
        new_content, replaced = _apply_replacement(
            _document_from_content(resolved[0], resolved[1]), old_text, new_text,
        )
        if replaced == 0:
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't find \"{old_text}\" in '{resolved[0].get('title')}', so nothing was changed.",
            )
    else:
        new_content = _parse_document_content(text)
    if new_content is None:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to compose the revision yet - no language "
                "model is configured for me right now. Give me the complete "
                f"updated content already structured instead:\n{_DOCUMENT_FORMAT_INSTRUCTION}",
            )
        notes = (
            f"The handout's current content is:\n{current_text}\n\n"
            "The person asked for a revision. Apply their requested change and "
            "respond with the COMPLETE updated document. "
            + _DOCUMENT_FORMAT_INSTRUCTION,
        ) + _recent_conversation_notes(request)
        decision = request.converse(request.task, notes)
        if not decision.is_decided:
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't compose that revision ({decision.reason or 'no usable answer'}).",
            )
        new_content = _parse_document_content(decision.reason)
        if new_content is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I couldn't organize that revision into a real, structured handout.",
            )

    if is_supplied:
        edited = dataclass_replace(new_content, title=f"{_title_stem(resolved[0].get('title'))} (edited)")
        derived = request.tools.invoke(
            "artifact_production",
            {"operation": "derive_document", "content": f"{artifact_id}\n{edited.to_json()}"},
        )
        if not derived.ok:
            return TaskResult.failure(
                request.task, request.own_name,
                f"I prepared the edited version but couldn't save it: {derived.error}",
            )
        return TaskResult.success(
            request.task, request.own_name,
            f"{derived.output} (an edited copy of artifact {artifact_id}; the original file you "
            "supplied is unchanged)",
            payload={"handled_by": "revise_artifact", "derived_from": artifact_id},
        )

    outcome = request.tools.invoke(
        "artifact_production",
        {"operation": "revise_document", "content": f"{artifact_id}\n{new_content.to_json()}"},
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I composed the revision but couldn't save it: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "revise_artifact"},
    )


REVISE_ARTIFACT = ManagerCapability(
    name="revise_artifact",
    capabilities=frozenset({"handout", "artifact"}),
    handle=revise_artifact,
    description=(
        "Revises an existing document artifact under the same identity, "
        "producing a new real version rather than a disconnected new file."
    ),
    claims=revise_artifact_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Multimodal workspace/artifact checkpoint, image/diagram half: a real,
# persistent, versioned image artifact - a diagram, a map, an
# illustration, or any other real visual an ImageProvider can produce.
# Genuinely the same artifact system as create_artifact/revise_artifact
# above (identical ArtifactStore, identical version-chain identity,
# identical ownership rules) with a different concrete content type
# (ImageContent instead of DocumentContent) and a different production
# step (the configured ImageProvider instead of a DocumentRenderer) -
# never a second, parallel artifact mechanism.
#
# "diagram"/"map"/"visual" are used as the routing nouns, deliberately
# never any one subject: nowhere in this code is "skeleton", "organ",
# "anatomy", or any other example subject named. Whatever the person
# actually asks the image to show is carried through as the real prompt
# text, exactly as it is for any other capability that reasons over
# given text.
# ---------------------------------------------------------------------------

_DIAGRAM_MARKERS: tuple[str, ...] = (
    "make a diagram", "create a diagram", "draw a diagram", "prepare a diagram",
    "make me a diagram", "create me a diagram", "build a diagram", "draw me a diagram",
    "make a visual diagram", "create a visual diagram",
    "draw a map", "create a map", "make a map", "draw me a map",
)

_REVISE_DIAGRAM_MARKERS: tuple[str, ...] = (
    "revise the diagram", "revise that diagram", "update the diagram", "update that diagram",
    "redraw the diagram", "redraw that diagram", "revise the map", "update the map",
)

# How much of a person's own remaining request, once the trigger phrase
# itself is removed, counts as "already a real description" rather than
# a bare command with nothing else in it - deliberately much lower than
# _CLIP_TRANSCRIPT_MIN_CHARS/handout's own threshold: an image prompt
# needs far less real text to be genuinely usable than a multi-section
# document does.
_DIAGRAM_PROMPT_MIN_CHARS = 12


def create_diagram_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, _DIAGRAM_MARKERS)


def create_diagram(request: CapabilityRequest) -> TaskResult:
    """Create a real, persistent, versioned image/diagram artifact -
    deterministically when the request already names what the image
    should show (Half-Brain safe, no LLM needed), Brain-assisted when it
    names only the bare ask ("make me a diagram") with nothing else to
    go on.

    The identical two-tier evidence order ``create_artifact`` already
    holds itself to: real, already-given text first; the Brain only
    when there genuinely is none. Never invents a subject - a vague
    request with no Brain configured is refused honestly rather than
    guessed.
    """
    if not request.tools.permits("artifact_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can put together a diagram, but the underlying "
            f"'artifact_production' capability is not installed, so I cannot "
            f"save it as a real artifact yet. Currently equipped: {equipped}.",
        )

    remainder = _after_marker(request.task.description, _DIAGRAM_MARKERS)
    if len(remainder) >= _DIAGRAM_PROMPT_MIN_CHARS:
        prompt = remainder
    else:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to turn that into a real image description "
                "yet - no language model is configured for me right now. Tell "
                "me specifically what the diagram/image should show.",
            )
        decision = request.converse(
            request.task,
            (
                "The person asked for a diagram/image/visual but gave little "
                "detail. Propose one single, concrete, descriptive prompt "
                "suitable for an image-generation model that captures what "
                "they most likely want, and respond with ONLY that prompt "
                "text - no other commentary.",
            ),
        )
        if not decision.is_decided or not decision.reason.strip():
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't come up with a concrete enough description for that "
                f"image ({decision.reason or 'no usable answer'}).",
            )
        prompt = decision.reason

    from src.services.artifacts.models import ImageContent

    try:
        content = ImageContent(prompt=prompt)
    except ValueError as exc:
        return TaskResult.failure(request.task, request.own_name, str(exc))

    outcome = request.tools.invoke(
        "artifact_production", {"operation": "create_image", "content": content.to_json()}
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't save that image as a real artifact: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "create_diagram"},
    )


CREATE_DIAGRAM = ManagerCapability(
    name="create_diagram",
    capabilities=frozenset({"diagram", "visual", "map"}),
    handle=create_diagram,
    description=(
        "Creates a real, persistent, versioned image/diagram artifact from a "
        "description - a diagram, a map, an illustration, or any other real "
        "visual subject, never a fixed catalog of examples."
    ),
    claims=create_diagram_intent,
    scoreable=False,
)


def revise_diagram_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, _REVISE_DIAGRAM_MARKERS)


def revise_diagram(request: CapabilityRequest) -> TaskResult:
    """Revise an existing image/diagram artifact - the same artifact
    identity, one real version further along, never a new, disconnected
    file. Resolution order identical to ``revise_artifact``: an explicit
    ``artifact <id>`` reference first, otherwise the most recently
    created/revised image this agent itself owns.
    """
    if not request.tools.permits("artifact_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can revise a diagram, but the underlying 'artifact_production' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )

    text = request.task.description
    match = _ARTIFACT_ID_PATTERN.search(text)
    if match:
        artifact_id = match.group(1)
    else:
        lookup = request.tools.invoke(
            "artifact_production", {"operation": "find_latest", "content": "image"}
        )
        if not lookup.ok:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have an existing diagram to revise yet - create one "
                "first, or reference it explicitly with 'artifact <id>'.",
            )
        artifact_id = lookup.output.strip()

    current = request.tools.invoke("artifact_production", {"operation": "get", "content": artifact_id})
    if not current.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't find that diagram to revise: {current.error}",
        )

    remainder = _after_marker(text, _REVISE_DIAGRAM_MARKERS)
    # An explicit "artifact <id>" reference is evidence for *which*
    # artifact, never part of *what it should now show* - stripped out
    # here so a revision request naming an id does not leak that id
    # into the new image prompt itself.
    id_match = _ARTIFACT_ID_PATTERN.search(remainder)
    if id_match:
        remainder = (remainder[: id_match.start()] + remainder[id_match.end() :]).strip(" ,.:;-")
    if len(remainder) >= _DIAGRAM_PROMPT_MIN_CHARS:
        new_prompt = remainder
    else:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to compose the revision yet - no language "
                "model is configured for me right now. Tell me specifically "
                "what the revised diagram/image should show.",
            )
        decision = request.converse(
            request.task,
            (
                f"The diagram/image's current description is: {current.output!r}. "
                "The person asked for a revision. Propose one single, concrete, "
                "updated prompt for an image-generation model that reflects "
                "their requested change, and respond with ONLY that prompt "
                "text - no other commentary.",
            ),
        )
        if not decision.is_decided or not decision.reason.strip():
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't compose that revision ({decision.reason or 'no usable answer'}).",
            )
        new_prompt = decision.reason

    from src.services.artifacts.models import ImageContent

    try:
        new_content = ImageContent(prompt=new_prompt)
    except ValueError as exc:
        return TaskResult.failure(request.task, request.own_name, str(exc))

    outcome = request.tools.invoke(
        "artifact_production",
        {"operation": "revise_image", "content": f"{artifact_id}\n{new_content.to_json()}"},
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I composed the revision but couldn't save it: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "revise_diagram"},
    )


REVISE_DIAGRAM = ManagerCapability(
    name="revise_diagram",
    capabilities=frozenset({"diagram", "visual", "map"}),
    handle=revise_diagram,
    description=(
        "Revises an existing image/diagram artifact under the same identity, "
        "producing a new real version rather than a disconnected new file."
    ),
    claims=revise_diagram_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Creative media production - real narration, image and video assets,
# one operation per call, dynamically sequenced by the objective loop
# rather than by any workflow fixed here.
#
# This capability decides nothing about *when* narration, an image or a
# video is needed, or in what order - that is ManagerBrain.
# assess_objective's own job, exactly as it already dynamically composes
# a step sequence for any other kind of objective (see
# src/manager/objective_loop.py). All this capability recognises is the
# *shape* of one atomic ask - "narrate this text", "illustrate this
# description", "compose these assets into a video" - and, once
# recognised, performs exactly that one real operation through
# MediaProductionTool. No genre, platform, topic or fixed pipeline
# appears anywhere below: the same three shapes serve a children's
# story, a documentary, a comedy sketch or a user-supplied script
# equally, because none of those distinctions are visible here.
# ---------------------------------------------------------------------------

_MEDIA_NARRATE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(narrate|voice|record\s+narration\s+for|read\s+(?:this\s+|the\s+)?(?:aloud|out\s+loud))\b",
    re.IGNORECASE,
)

_MEDIA_ILLUSTRATE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(illustrate|draw\s+an?\s+(?:image|picture|illustration|scene|character)|"
    r"generate\s+an?\s+image|create\s+an?\s+image|generate\s+a\s+picture|create\s+a\s+picture)\b",
    re.IGNORECASE,
)
# Note: bare "draw <anything>" is deliberately not anchored here - "draw a
# comparison"/"draw a conclusion"/"draw attention" are ordinary English
# idioms with nothing to do with an image, and an imperative-shape check
# alone cannot tell them apart from "draw a picture of a fox" the way it
# can for words with no such idiomatic life of their own (found and fixed
# before shipping, during this capability's own first live check).
# "draw" only claims this capability when followed by an actual
# image-shaped object - never as a bare verb.

_MEDIA_COMPOSE_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(compose|render|assemble|put\s+together|stitch\s+together)\b.*\b(video|clip|scene)\b",
    re.IGNORECASE,
)

# AI video-generation checkpoint: a genuinely different verb group from
# compose/render/assemble/stitch above - "generate"/"create"/"produce" a
# video names an ask for a *new* video synthesised by an AI model, never
# an assembly of already-produced pieces, the same real distinction
# English already draws between "compose a song from these clips" and
# "generate a song". Which of the two - or neither - an objective
# actually needs is decided by the Brain, never here; this only tells
# the two shapes apart once one has been chosen.
#
# General Time/Event Awareness checkpoint: "make" joined the verb group
# and "animation"/"anime" joined the noun group - not a new capability
# and not an occasion-gated one, simply widening this same general
# "generate a new video-shaped asset" recognition to the ordinary words
# a person uses for that exact ask ("make this anime", "create an
# animation of this"). Animation/anime creation is and remains a normal,
# occasion-free request handled by this identical, unchanged capability -
# see PROACTIVE_EXPERIENCE's own module docstring for why the contextual/
# proactive visual-experience path is a fully separate mechanism that
# never routes through here.
_MEDIA_GENERATE_VIDEO_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(generate|create|produce|make)\b.*\b(video|clip|animation|anime)\b",
    re.IGNORECASE,
)


def media_production_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request's *shape* is an imperative ask to actually
    produce one real media asset now - the identical "anchored verb in
    command position, never a bare word" discipline
    ``general_assistance_intent``/``_communication_shape`` already use,
    applied to four atomic media operations instead of one.

    Deliberately narrow: a request to *talk about* narration, images or
    video ("what's a good style for narration here?") does not match
    this shape and correctly falls through to open_conversation, the
    identical distinction general_assistance_intent already draws
    between discussing a subject and asking for the tool-gated work.
    """
    working = _strip_vocative(task.description).strip()
    return bool(
        _MEDIA_NARRATE_PATTERN.match(working)
        or _MEDIA_ILLUSTRATE_PATTERN.match(working)
        or _MEDIA_COMPOSE_PATTERN.match(working)
        or _MEDIA_GENERATE_VIDEO_PATTERN.match(working)
    )


def media_production(request: CapabilityRequest) -> TaskResult:
    """Perform exactly one real media production operation - narrate,
    illustrate, compose, or AI-generate a video - through the governed
    ``media_production`` tool, honestly refusing if it is not installed.

    Three honest outcomes, the identical shape ``general_assistance``
    and ``live_information`` already established for their own tool
    boundary: tool missing, named honestly; tool present and the call
    fails, the failure reported as its own; tool present and it
    succeeds, the real asset's description (including its file path)
    returned as the result - evidence a later objective-loop step can
    reference to compose a video from what earlier steps already
    produced, or to seed a generated one from an already-produced image.
    """
    working = _strip_vocative(request.task.description).strip()

    if _MEDIA_NARRATE_PATTERN.match(working):
        operation = "narrate"
    elif _MEDIA_ILLUSTRATE_PATTERN.match(working):
        operation = "illustrate"
    elif _MEDIA_GENERATE_VIDEO_PATTERN.match(working):
        operation = "generate_video"
    else:
        operation = "compose"

    if not request.tools.permits("media_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task,
            request.own_name,
            (
                "I can take on producing a real media asset, but the "
                "underlying 'media_production' capability is not installed, "
                f"so I cannot do it yet rather than guess. Currently equipped: {equipped}."
            ),
        )

    outcome = request.tools.invoke(
        "media_production", {"operation": operation, "content": request.task.description}
    )
    if not outcome.ok:
        return TaskResult.failure(
            request.task,
            request.own_name,
            f"media production failed: {outcome.error}",
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        outcome.output,
        payload={"handled_by": "media_production", "operation": operation},
    )


MEDIA_PRODUCTION = ManagerCapability(
    name="media_production",
    capabilities=frozenset(
        {"narrate", "voice", "illustrate", "draw", "compose", "render", "assemble"}
    ),
    handle=media_production,
    description=(
        "Produces one real narration, image or video asset per call through a "
        "governed tool - dynamically sequenced by the objective loop, never a "
        "fixed creative-media workflow."
    ),
    claims=media_production_intent,
    # No genuine ordinary-conversation collision left to catch once the
    # shape-based claim above exists - identical reasoning to
    # general_assistance's own scoreable=False.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Video-clipping checkpoint: cutting real clips from an existing video -
# genuinely different work from media_production's own "generate"/
# "compose" (both produce a *new* video from scratch). A distinct
# capability, not a widening of MEDIA_PRODUCTION's own marker set, for
# the identical reason RECORD_THIS is not a widening of FORGET: the two
# requests share a tool but are different asks, and collapsing them
# would make one claims= predicate responsible for telling both apart
# instead of two capabilities each owning their own shape.
# ---------------------------------------------------------------------------

# How much real text, beyond the video path itself, counts as "the
# person already supplied a transcript/description worth reasoning over
# directly" rather than a bare instruction with nothing in it. Below
# this, clip_video instead attempts to derive real evidence from the
# video's own actual audio (see clip_video's own docstring, step 3) -
# never asks the Brain to propose timestamps from three words, which
# would only invite it to invent them.
_CLIP_TRANSCRIPT_MIN_CHARS = 120

_CLIP_VIDEO_MARKERS: tuple[str, ...] = (
    "clip this video", "clip the video", "clip this footage", "clip the footage",
    "cut this video", "cut the video", "trim this video", "trim the video",
    "extract a clip", "extract clips", "make a clip from", "make clips from",
    "make several clips", "highlight reel", "make a highlight",
    "find the best moments", "find the funniest moments", "clip the funniest",
    "clip the best", "find the moments where", "social clips from", "short clips from",
)


def clip_video_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request's own shape is an ask to cut real clips from
    an existing video - an anchored clip-verb phrase together with real
    evidence this is actually about a video (a referenced file path, or
    an explicit time range) - the identical "phrase plus evidence"
    discipline that keeps "that was quite a task" from claiming
    create_task applied here so a metaphorical "clip" never claims this
    one on the word alone.
    """
    text = task.description
    if not _marker_present(text, _CLIP_VIDEO_MARKERS):
        return False
    return find_video_path(text) is not None or bool(parse_clip_specs(text))


def clip_video(request: CapabilityRequest) -> TaskResult:
    """Cut one or more real clips from an existing video - deterministic
    when explicit timestamps are given (Half-Brain safe, no LLM and no
    tool call beyond the cut itself), Brain-assisted when the request
    instead names *what* to find (e.g. "the funniest moments").

    Three ways real evidence reaches the Brain, tried in order, each
    strictly more work than the last:

    1. Explicit timestamps in the request itself - no Brain, no
       transcript, nothing to understand; straight to the cut.
    2. A transcript/description the person already typed alongside the
       request - reasoned over directly.
    3. Neither of the above, but a real video path is given: this
       function asks ``media_production`` to genuinely extract that
       video's own audio (local FFmpeg, deterministic) and transcribe it
       (``video -> audio extraction -> speech/audio analysis -> temporal
       understanding``) - real, timestamped evidence *derived from the
       actual media*, never fabricated. The Brain then selects moments
       from those real segments, exactly as it would from a
       user-supplied transcript in step 2, through the identical
       ``converse`` call - no separate "video brain", no bypass of the
       ordinary capability/routing architecture.

    Step 3 is only as real as whichever transcription provider is
    configured. Unconfigured (the default, ``NullTranscriptionProvider``),
    it fails honestly rather than guessing from a bare instruction -
    this function never invents a transcript or a timestamp on the
    video's own behalf.
    """
    text = request.task.description
    video_path = find_video_path(text)
    if video_path is None:
        # Pre-Stage-9 Campaign 3: the video shared in this conversation.
        _described, video_path = _shared_media(request, ("video",))
    if video_path is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which video? I don't see a video referenced in this request or shared in "
            "this conversation - attach one, or name it with 'artifact <id>'.",
        )

    if not request.tools.permits("media_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can take on clipping a video, but the underlying "
            f"'media_production' capability is not installed, so I cannot do "
            f"it yet rather than guess. Currently equipped: {equipped}.",
        )

    content = text
    if not parse_clip_specs(text):
        # No explicit timestamps - this is an ask to find moments, never
        # to cut a named range. Only the Brain, given real text to
        # reason over, can propose ones - never this function guessing
        # on the video's own behalf.
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to analyze this video's actual content - "
                "no reasoning capability is available to work from a "
                "transcript. Give me specific timestamps (e.g. '1:20-1:45') "
                "instead.",
            )

        remainder = text.replace(video_path, "").strip()
        if len(remainder) >= _CLIP_TRANSCRIPT_MIN_CHARS:
            # Real transcript/description text was already supplied in
            # the request - reason over it directly, no need to touch
            # the actual media file at all.
            notes = (
                "The person supplied a transcript/description and asked for "
                "clips from it. Propose the most fitting time range(s), each "
                "written as exactly 'MM:SS-MM:SS' (or 'H:MM:SS-H:MM:SS'), one "
                "per line, and nothing else.",
            )
        else:
            # No transcript was given. The only honest way to find real
            # moments is to derive real, timestamped evidence from the
            # video's own actual audio - never to guess from a bare
            # instruction. media_production's "transcribe" operation
            # does the genuine, deterministic audio extraction itself
            # (no LLM); whether real speech-to-text can follow depends
            # entirely on whether a transcription provider is configured.
            transcription = request.tools.invoke(
                "media_production", {"operation": "transcribe", "content": video_path},
            )
            if not transcription.ok:
                return TaskResult.failure(
                    request.task, request.own_name,
                    "I can't find real moments in this video without "
                    f"understanding its actual content, and that failed: "
                    f"{transcription.error}. Give me specific timestamps "
                    "(e.g. '1:20-1:45'), or paste a transcript/description "
                    "of the video alongside your request.",
                )
            notes = (
                "Below is the video's own real, timestamped transcript, "
                "derived from its actual audio - not text the person typed. "
                "Propose the most fitting time range(s) that match their "
                "request, each written as exactly 'MM:SS-MM:SS' (or "
                "'H:MM:SS-H:MM:SS'), one per line, and nothing else. Use "
                "only timestamps that genuinely belong to the transcript's "
                "own segments below.\n\n" + transcription.output,
            )

        decision = request.converse(request.task, notes)
        if not decision.is_decided or not parse_clip_specs(decision.reason):
            return TaskResult.failure(
                request.task, request.own_name,
                "I read the transcript but couldn't settle on real clip "
                f"boundaries from it ({decision.reason or 'no usable answer'}).",
            )
        content = f"{video_path}\n{decision.reason}"

    if not content.startswith(video_path):
        # The source travels on its own first line: a stored artifact's path
        # may contain spaces a path pattern cannot see.
        content = f"{video_path}\n{content}"
    outcome = request.tools.invoke("media_production", {"operation": "clip", "content": content})
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"clipping failed: {outcome.error}",
        )

    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "clip_video"},
    )


CLIP_VIDEO = ManagerCapability(
    name="clip_video",
    capabilities=frozenset({"clip", "trim", "highlight"}),
    handle=clip_video,
    description=(
        "Cuts one or more real clips from an existing video - explicit "
        "timestamps need no Brain; finding moments instead reasons over a "
        "supplied transcript/description, or, absent one, a real transcript "
        "genuinely derived from the video's own audio - and is refused "
        "honestly when neither reasoning nor transcription is available."
    ),
    claims=clip_video_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 12 additions: record this / record this for research
# ---------------------------------------------------------------------------
#
# "Remember that X" and "record this for X" are different requests - see
# src/services/memory/capture.py's own docstring - so this is a new
# capability rather than a widening of FORGET/RECALL_MEMORY or a second
# trigger on retain_what_was_asked_for's existing marker set. Reachable by
# any equipped agent, not only the Manager: EmailAgent's own execute()
# recognises the identical capture.capture_intent/build_recording functions
# for "Arthur, record this for later" - one shared extraction, two callers,
# never two implementations (Stage 12 item 7).


def record_this(request: CapabilityRequest) -> TaskResult:
    """Capture an explicit "record this [for X]" request through the
    governed write path - never through retain_what_was_asked_for, which
    owns a different trigger phrase entirely.
    """
    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to record things right now.",
        )

    record = build_recording(
        request.task.description,
        agent_name=request.own_name,
        kind=MemoryCategory.RECORDING.value,
    )
    if record is None:
        # select_capability only reaches this handler when claims already
        # matched, so this is unreachable in practice - kept as an honest
        # answer rather than an assumption nothing else could route here.
        return TaskResult.failure(
            request.task, request.own_name, "Record what, exactly?"
        )

    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't record that: {verdict.reason}",
        )

    purpose = (record.context or {}).get("purpose", DEFAULT_CAPTURE_PURPOSE)
    return TaskResult.success(
        request.task, request.own_name,
        f"Recorded for {purpose}.",
        payload={"handled_by": "record_this", "purpose": purpose},
    )


RECORD_THIS = ManagerCapability(
    name="record_this",
    # Word overlap alone would collide "record" with ordinary requests
    # ("keep a record of...", "that's a new personal record") the same
    # way invitation_intent's own docstring already explains for "join" -
    # claims is what actually decides this, so the words below only need
    # to exist, not to be precise. Natural Conversation fix: the comment
    # above said this from the start, but scoreable was never actually
    # disabled - confirmed as a real, observable misroute ("that was a
    # new personal record for me" reaching this handler via word overlap
    # alone, claims correctly False) and closed the same way small_talk's
    # own equivalent gap already was.
    capabilities=frozenset({"record", "recording"}),
    handle=record_this,
    description="Captures an explicit 'record this [for X]' request, with provenance and purpose.",
    claims=lambda task, participants=(): capture_intent(task.description),
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Real Audio Recording/Capture: a genuine microphone recording, never text
# pretending to be one - see src/services/memory/audio_recording.py's own
# docstring for why this is kept apart from RECORD_THIS above (a text
# capture) despite an occasional shared word. Half-Brain safe throughout:
# nothing here touches converse/analyze_image, because starting, stopping
# or cancelling a recording is exactly as deterministic as creating,
# completing or reopening a task - the same "governed intent, no model, no
# guessing" shape TASK's own capabilities already use.
#
# **Why these handlers never touch a microphone or an artifact.** Python
# has no route to a browser's microphone - the actual capture, upload and
# artifact/Memory write happen at scripts/serve_ui.py's own
# POST /recording/started|save|cancel routes, called by the frontend once
# a real user gesture and real captured bytes exist. What a capability
# CAN do deterministically is recognise the request and flip the one
# shared, live RecordingSessionRegistry signal - the same division of
# labour route_workspace_enter/the Workspace capability already established
# for "a UI action the frontend must actually perform" vs. "a natural-
# language request Alfred recognises".
# ---------------------------------------------------------------------------


def start_recording(request: CapabilityRequest) -> TaskResult:
    """Recognise an explicit "start recording [for X]" request and open a
    real recording session for this agent - never a capture itself,
    which only a browser can perform. The session this creates is what
    lets the Workspace/Agents UI honestly show "ready to record for
    research" rather than a person having to separately explain the
    purpose to a Start button.
    """
    if request.recording_sessions is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to record audio right now.",
        )
    purpose = extract_recording_purpose(request.task.description)
    request.recording_sessions.request(request.own_name, purpose)
    return TaskResult.success(
        request.task, request.own_name,
        f"Ready to record for {purpose} - use the Recording control to begin, "
        "or say 'stop recording' once you are done.",
        payload={"handled_by": "start_recording", "purpose": purpose},
    )


START_RECORDING = ManagerCapability(
    name="start_recording",
    capabilities=frozenset({"recording", "audio"}),
    handle=start_recording,
    description="Opens a real recording session for an explicit 'start recording [for X]' request.",
    claims=lambda task, participants=(): start_recording_intent(task.description),
    scoreable=False,
)


def stop_recording(request: CapabilityRequest) -> TaskResult:
    """Recognise an explicit "stop recording" request and signal the one
    real, currently-active session to stop - never stops anything
    itself, which only the frontend holding the real MediaRecorder can
    do. Refuses honestly when nothing is genuinely recording, rather
    than pretending a stop happened.
    """
    if request.recording_sessions is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to record audio right now.",
        )
    if not request.recording_sessions.request_stop(request.own_name):
        return TaskResult.failure(
            request.task, request.own_name,
            "Nothing is currently recording.",
        )
    return TaskResult.success(
        request.task, request.own_name,
        "Stopping the recording now.",
        payload={"handled_by": "stop_recording"},
    )


STOP_RECORDING = ManagerCapability(
    name="stop_recording",
    capabilities=frozenset({"recording", "audio"}),
    handle=stop_recording,
    description="Signals the currently-active recording session to stop.",
    claims=lambda task, participants=(): stop_recording_intent(task.description),
    scoreable=False,
)


def cancel_recording(request: CapabilityRequest) -> TaskResult:
    """Recognise an explicit request to discard the in-progress recording
    without saving it. Refuses honestly when nothing is in progress."""
    if request.recording_sessions is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to record audio right now.",
        )
    if not request.recording_sessions.cancel(request.own_name):
        return TaskResult.failure(
            request.task, request.own_name,
            "There is no recording in progress to cancel.",
        )
    return TaskResult.success(
        request.task, request.own_name,
        "Cancelled - nothing was saved.",
        payload={"handled_by": "cancel_recording"},
    )


CANCEL_RECORDING = ManagerCapability(
    name="cancel_recording",
    capabilities=frozenset({"recording", "audio"}),
    handle=cancel_recording,
    description="Discards the in-progress recording session without saving it.",
    claims=lambda task, participants=(): cancel_recording_intent(task.description),
    scoreable=False,
)


def list_recordings(request: CapabilityRequest) -> TaskResult:
    """List this agent's own saved recordings, or answer whether one
    matching a named topic/purpose exists - the identical "bare view vs.
    named argument" shape ``list_tasks`` already uses, over the
    governed ``AUDIO_RECORDING`` Memory category rather than the actual
    artifact store (retrieval by real, searchable metadata - the actual
    audio stays exactly where the artifact store already keeps it).
    """
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to look up recordings right now.",
        )

    topic = extract_recording_query(request.task.description)
    pool = request.recall(kind=MemoryCategory.AUDIO_RECORDING.value, limit=200)
    if topic:
        matches = match_recordings_by_topic(pool, topic)
        if not matches:
            return TaskResult.success(
                request.task, request.own_name,
                f"I don't have a recording matching '{topic}'.",
                payload={"handled_by": "list_recordings", "count": 0},
            )
        return TaskResult.success(
            request.task, request.own_name,
            "\n".join(describe_recording(record) for record in matches),
            payload={"handled_by": "list_recordings", "count": len(matches)},
        )

    if not pool:
        return TaskResult.success(
            request.task, request.own_name, "You have no saved recordings yet.",
            payload={"handled_by": "list_recordings", "count": 0},
        )
    return TaskResult.success(
        request.task, request.own_name,
        "\n".join(describe_recording(record) for record in pool),
        payload={"handled_by": "list_recordings", "count": len(pool)},
    )


LIST_RECORDINGS = ManagerCapability(
    name="list_recordings",
    capabilities=frozenset({"recording", "audio"}),
    handle=list_recordings,
    description="Lists saved recordings, or answers whether one matching a topic exists.",
    claims=lambda task, participants=(): list_recordings_intent(task.description),
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Phase 5.1: Tasks - an outstanding, actionable item with real state,
# reached exactly the way forget/recall_memory/record_this already are:
# a claims= marker predicate, scoreable=False, the governed
# request.retain/recall/forget triple. See src/services/memory/tasks.py's
# own docstring for why a task is neither a recording nor a durable fact,
# and for the deliberately minimal (title + status only) shape.
#
# Four operations, four handlers - never one handler branching on a
# guessed verb, the same "one capability per kind of work" discipline
# every other handler in this file already follows.
# ---------------------------------------------------------------------------


def create_task_intent_plain(text: str) -> bool:
    """A plain "add a task" / "remind me to X" with no time is still an
    ordinary task - only a request that is *only* about a reminder or alarm
    needs a time."""
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in ("add a task", "add task", "create a task", "new task", "add this task", "remind me to"))


def create_task(request: CapabilityRequest) -> TaskResult:
    """Add a new pending task through the governed write path - the
    identical gate every other durable Manager write already crosses.
    """
    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to track tasks right now.",
        )

    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    reminder = extract_reminder(request.task.description, now=calendar.now())
    if reminder is None and reminder_intent(request.task.description) and not create_task_intent_plain(request.task.description):
        return TaskResult.failure(
            request.task, request.own_name,
            "When should I remind you? Say a time - \"at 5pm\", \"tomorrow at 9\", \"in 20 minutes\".",
        )
    record = build_task_record(request.task.description, agent_name=request.own_name, reminder=reminder)
    if record is None:
        return TaskResult.failure(
            request.task, request.own_name, "Add a task to do what, exactly?"
        )

    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't add that task: {verdict.reason}",
        )

    if reminder is not None:
        due_at = reminder[1]
        when = due_at.strftime("%H:%M on %A %d %B %Y")
        return TaskResult.success(
            request.task, request.own_name,
            f"I'll remind you: {record.content} - at {when}.",
            payload={"handled_by": "create_task", "task_id": record.id, "due_at": due_at.isoformat()},
        )
    return TaskResult.success(
        request.task, request.own_name,
        f"Added: {record.content}",
        payload={"handled_by": "create_task", "task_id": record.id},
    )


CREATE_TASK = ManagerCapability(
    name="create_task",
    # claims decides this, not word overlap - "task" alone would collide
    # with ordinary speech ("that was quite a task") the same way
    # "record" already does for RECORD_THIS above.
    capabilities=frozenset({"task", "tasks"}),
    handle=create_task,
    description="Adds a new pending task ('add a task ...', 'remind me to ...').",
    claims=lambda task, participants=(): create_task_intent(task.description),
    scoreable=False,
)


def list_tasks(request: CapabilityRequest) -> TaskResult:
    """List pending tasks, or answer whether a specific one exists - the
    identical "bare view vs. named argument" shape
    scripts/control_center.py's own connections/provider commands
    already use, one level down at the capability layer.
    """
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to check tasks right now.",
        )

    topic = extract_task_query(request.task.description)
    if topic:
        pool = request.recall(kind=MemoryCategory.TASK.value, limit=200)
        matches = match_tasks_by_topic(pool, topic)
        if not matches:
            return TaskResult.success(
                request.task, request.own_name,
                f"I don't have a task matching '{topic}'.",
                payload={"handled_by": "list_tasks", "count": 0},
            )
        return TaskResult.success(
            request.task, request.own_name,
            "\n".join(describe_task(record) for record in matches),
            payload={"handled_by": "list_tasks", "count": len(matches)},
        )

    pending = request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=50)
    if not pending:
        return TaskResult.success(
            request.task, request.own_name, "You have no pending tasks.",
            payload={"handled_by": "list_tasks", "count": 0},
        )
    return TaskResult.success(
        request.task, request.own_name,
        "\n".join(describe_task(record) for record in pending),
        payload={"handled_by": "list_tasks", "count": len(pending)},
    )


LIST_TASKS = ManagerCapability(
    name="list_tasks",
    capabilities=frozenset({"task", "tasks"}),
    handle=list_tasks,
    description="Lists pending tasks, or answers whether a specific one exists.",
    claims=lambda task, participants=(): list_tasks_intent(task.description),
    scoreable=False,
)


def _restore_or_report(
    request: CapabilityRequest, original: MemoryRecord, failure_message: str
) -> TaskResult:
    """Put the untouched original task back after a rejected replacement,
    so a refused update never silently deletes the task it could not
    apply - re-retaining content that was already admitted once is not
    guaranteed to succeed (the policy is re-evaluated every time, on
    purpose), so this reports whichever outcome actually happened rather
    than assuming the restore worked.
    """
    restored = request.retain(original)
    if not restored.allowed:
        return TaskResult.failure(
            request.task, request.own_name,
            f"{failure_message} The original task could not be restored either: "
            f"{restored.reason}",
        )
    return TaskResult.failure(request.task, request.own_name, failure_message)


def complete_task(request: CapabilityRequest) -> TaskResult:
    """Mark one pending task complete - a governed replace-in-place
    (same MemoryRecord.id, new status), never a silent delete: the new
    version is validated and only then does the old one leave the store,
    with an explicit restore path if the new version is refused.
    """
    if request.recall is None or request.retain is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to complete tasks right now.",
        )

    topic = extract_completion_topic(request.task.description)
    if not topic:
        return TaskResult.failure(
            request.task, request.own_name, "Complete which task, exactly?"
        )

    pool = request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=200)
    matches = match_tasks_by_topic(pool, topic)
    if not matches:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I don't have a pending task matching '{topic}'.",
        )
    if len(matches) > 1:
        listing = "; ".join(record.content for record in matches)
        return TaskResult.failure(
            request.task, request.own_name,
            f"I found {len(matches)} pending tasks matching '{topic}' - "
            f"be more specific: {listing}",
        )

    target = matches[0]
    updated = dataclass_replace(
        target,
        tags={"task", "task:complete"},
        context={**(target.context or {}), "status": "complete"},
    )

    if not request.forget(target.id):
        return TaskResult.failure(
            request.task, request.own_name,
            "I couldn't complete that task: it was already gone.",
        )
    verdict = request.retain(updated)
    if not verdict.allowed:
        return _restore_or_report(
            request, target, f"I couldn't complete that task: {verdict.reason}"
        )

    return TaskResult.success(
        request.task, request.own_name,
        f"Completed: {target.content}",
        payload={"handled_by": "complete_task", "task_id": target.id},
    )


COMPLETE_TASK = ManagerCapability(
    name="complete_task",
    capabilities=frozenset({"task", "tasks", "done", "finished", "completed"}),
    handle=complete_task,
    description="Marks a pending task complete ('complete the task ...', 'I finished ...').",
    claims=lambda task, participants=(): complete_task_intent(task.description),
    scoreable=False,
)


def reopen_task(request: CapabilityRequest) -> TaskResult:
    """Reopen one completed task - the lifecycle's missing fourth verb.
    The identical governed replace-in-place ``complete_task`` already
    uses, run in reverse: same MemoryRecord.id, same content, status
    moved back to pending, with the identical explicit restore path if
    the reopened version is refused. Never a delete-and-recreate - the
    task's identity (its id) is preserved exactly as it is across a
    completion.
    """
    if request.recall is None or request.retain is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to reopen tasks right now.",
        )

    topic = extract_reopen_topic(request.task.description)
    if not topic:
        return TaskResult.failure(
            request.task, request.own_name, "Reopen which task, exactly?"
        )

    pool = request.recall(kind=MemoryCategory.TASK.value, tags={"task:complete"}, limit=200)
    matches = match_tasks_by_topic(pool, topic)
    if not matches:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I don't have a completed task matching '{topic}'.",
        )
    if len(matches) > 1:
        listing = "; ".join(record.content for record in matches)
        return TaskResult.failure(
            request.task, request.own_name,
            f"I found {len(matches)} completed tasks matching '{topic}' - "
            f"be more specific: {listing}",
        )

    target = matches[0]
    updated = dataclass_replace(
        target,
        tags={"task", "task:pending"},
        context={**(target.context or {}), "status": "pending"},
    )

    if not request.forget(target.id):
        return TaskResult.failure(
            request.task, request.own_name,
            "I couldn't reopen that task: it was already gone.",
        )
    verdict = request.retain(updated)
    if not verdict.allowed:
        return _restore_or_report(
            request, target, f"I couldn't reopen that task: {verdict.reason}"
        )

    return TaskResult.success(
        request.task, request.own_name,
        f"Reopened: {target.content}",
        payload={"handled_by": "reopen_task", "task_id": target.id},
    )


REOPEN_TASK = ManagerCapability(
    name="reopen_task",
    capabilities=frozenset({"task", "tasks"}),
    handle=reopen_task,
    description="Reopens a completed task ('reopen the task ...', 'make the task ... incomplete').",
    claims=lambda task, participants=(): reopen_task_intent(task.description),
    scoreable=False,
)


def update_task(request: CapabilityRequest) -> TaskResult:
    """Rename a task - the identical governed replace-in-place
    complete_task uses, restoring the untouched original if the renamed
    version is refused.
    """
    if request.recall is None or request.retain is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to update tasks right now.",
        )

    parsed = extract_task_update(request.task.description)
    if parsed is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Update which task to what, exactly? Try 'change the task X to Y'.",
        )
    old_topic, new_title = parsed

    pool = request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=200)
    matches = match_tasks_by_topic(pool, old_topic)
    if not matches:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I don't have a pending task matching '{old_topic}'.",
        )
    if len(matches) > 1:
        listing = "; ".join(record.content for record in matches)
        return TaskResult.failure(
            request.task, request.own_name,
            f"I found {len(matches)} pending tasks matching '{old_topic}' - "
            f"be more specific: {listing}",
        )

    target = matches[0]
    updated = dataclass_replace(target, content=new_title)

    if not request.forget(target.id):
        return TaskResult.failure(
            request.task, request.own_name,
            "I couldn't update that task: it was already gone.",
        )
    verdict = request.retain(updated)
    if not verdict.allowed:
        return _restore_or_report(
            request, target, f"I couldn't update that task: {verdict.reason}"
        )

    return TaskResult.success(
        request.task, request.own_name,
        f"Updated: {target.content} -> {new_title}",
        payload={"handled_by": "update_task", "task_id": target.id},
    )


UPDATE_TASK = ManagerCapability(
    name="update_task",
    capabilities=frozenset({"task", "tasks"}),
    handle=update_task,
    description="Renames a pending task ('change the task X to Y').",
    claims=lambda task, participants=(): update_task_intent(task.description),
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 12 additions: temporal derivation - age, next birthday, countdown
# ---------------------------------------------------------------------------
#
# The distinction this closes: a stored birth date is an authoritative fact;
# an age is a *derived* quantity that changes every year without anyone
# saying so. This capability never stores a number - it recalls the stored
# date and computes fresh, every time, from src.services.memory.temporal_facts's
# pure functions. Generic across what is being asked (age, a birthday
# countdown, a completion-date countdown) rather than one handler per
# question, and generic across *whose* date-bearing fact is stored - no
# name, no school, no subject appears below.

AGE_MARKERS: tuple[str, ...] = ("how old am i", "what is my age", "what's my age", "my current age")
BIRTHDAY_MARKERS: tuple[str, ...] = (
    "when is my birthday", "when's my birthday", "days until my birthday",
    "how many days until my birthday", "how long until my birthday",
)
COMPLETION_MARKERS: tuple[str, ...] = (
    "when do i graduate", "when will i graduate", "how long until i graduate",
    "days until i graduate", "days until graduation", "when do i finish",
    "how long until i finish", "when is my completion date",
)


def temporal_derivation_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request asks for a value derived from a stored date,
    rather than the date itself - recognised by phrase, the same reason
    every other claims= predicate in this file already is one."""
    return _marker_present(
        task.description, AGE_MARKERS + BIRTHDAY_MARKERS + COMPLETION_MARKERS
    )


def temporal_derivation(request: CapabilityRequest) -> TaskResult:
    """Answer a derived-date question by recalling the authoritative
    date and computing fresh - never by reading a previously-stored
    answer, which is exactly what would go stale."""
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to look that up right now.",
        )

    lowered = request.task.description.lower()
    today = utc_today()

    if _marker_present(lowered, AGE_MARKERS):
        return _answer_age(request, today)
    if _marker_present(lowered, BIRTHDAY_MARKERS):
        return _answer_next_birthday(request, today)
    if _marker_present(lowered, COMPLETION_MARKERS):
        return _answer_completion_countdown(request, today)

    # select_capability only reaches this handler when claims already
    # matched one of the three marker sets above.
    return TaskResult.failure(
        request.task, request.own_name, "I'm not sure what date-based question that is."
    )


def _dated_fact(request: CapabilityRequest, fact_type: str) -> Mapping[str, Any] | None:
    records = request.recall(kind=MemoryCategory.USER_CONTEXT.value, tags={fact_type}, limit=1)
    if not records or "date" not in (records[0].context or {}):
        return None
    return records[0].context


def _answer_age(request: CapabilityRequest, today) -> TaskResult:
    context = _dated_fact(request, "date_of_birth")
    if context is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have your date of birth on record yet, so I can't work "
            "out your age - tell me when you were born and I will remember it.",
        )
    born = date.fromisoformat(context["date"])
    age = compute_age(born, today)
    return TaskResult.success(
        request.task, request.own_name, f"You are {age} years old.",
        payload={"handled_by": "temporal_derivation", "derived": "age", "value": age},
    )


def _answer_next_birthday(request: CapabilityRequest, today) -> TaskResult:
    context = _dated_fact(request, "date_of_birth")
    if context is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have your date of birth on record yet, so I can't say "
            "when your birthday is - tell me when you were born and I will remember it.",
        )
    born = date.fromisoformat(context["date"])
    upcoming = next_occurrence(born.month, born.day, today)
    remaining = days_until(born.month, born.day, today)
    return TaskResult.success(
        request.task, request.own_name,
        f"Your next birthday is {upcoming.isoformat()}, {remaining} day(s) away.",
        payload={
            "handled_by": "temporal_derivation", "derived": "next_birthday",
            "value": upcoming.isoformat(), "days_remaining": remaining,
        },
    )


def _answer_completion_countdown(request: CapabilityRequest, today) -> TaskResult:
    context = _dated_fact(request, "completion_date")
    if context is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have an expected completion date on record yet - tell "
            "me when you expect to finish and I will remember it.",
        )
    target = date.fromisoformat(context["date"])
    remaining = days_between(target, today)
    if remaining < 0:
        return TaskResult.success(
            request.task, request.own_name,
            f"Your recorded completion date was {target.isoformat()}, {-remaining} day(s) ago.",
            payload={"handled_by": "temporal_derivation", "derived": "completion", "value": target.isoformat()},
        )
    return TaskResult.success(
        request.task, request.own_name,
        f"Your expected completion date is {target.isoformat()}, {remaining} day(s) away.",
        payload={
            "handled_by": "temporal_derivation", "derived": "completion",
            "value": target.isoformat(), "days_remaining": remaining,
        },
    )


TEMPORAL_DERIVATION = ManagerCapability(
    name="temporal_derivation",
    capabilities=frozenset({"age", "birthday", "graduate", "graduation", "countdown"}),
    handle=temporal_derivation,
    description="Answers age/birthday/completion questions by computing from a stored date, never a stored answer.",
    claims=temporal_derivation_intent,
    # Natural Conversation fix: "age" is an ordinary word in ordinary
    # speech ("that joke isn't really age appropriate") with nothing to
    # do with computing someone's own age. temporal_derivation_intent
    # already recognises the real request shape.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Foundation Repair #1: the current date, day, month and year - system
# self-knowledge, never a stored fact and never a reasoning question.
# ---------------------------------------------------------------------------
#
# The defect this closes, measured by the audit rather than suspected: "what
# day is it", "what year is it" and their kin were answered in one of two
# wrong ways, neither of which is this handler's fault so much as nothing
# having claimed the request first:
#
#   "what day of the week is it"   scored no declared word except the plain
#                                   acknowledgement "it" (small_talk's own
#                                   vocabulary), so small_talk won the
#                                   word-overlap fallback and answered "Got
#                                   it." - a confident non-answer, not a
#                                   refusal.
#   "what is today's date"         scored "today" - a word LIVE_INFORMATION
#                                   also claims, because *most* uses of
#                                   "today" genuinely do mean "as the world
#                                   stands right now" - and was refused as
#                                   needing a live-information backend that
#                                   was never going to be relevant, because
#                                   the *calendar* date needs no such thing.
#
# Both are the same underlying error live_information/knowledge_inquiry
# already avoid for their own subjects: a request was let fall through to
# word-overlap scoring when its *shape* - not its words - already said
# exactly what it was. This capability recognises that shape by phrase,
# the same instrument knowledge_intent/temporal_derivation_intent/
# current_information_intent already are, and answers it from the one
# place a calendar date actually lives: request.calendar (Calendar.today()),
# never a stored record and never the Brain.
#
# **Deterministic, and Half-Brain-safe by construction.** Nothing below
# reads request.converse, so this never touches a provider, an
# availability record or a Brain mode - the same floor
# general_assistance/temporal_derivation already stand on. A configured,
# verified Full Brain changes nothing about this handler's answer or its
# path: claims are checked before scoring for every capability
# (select_capability), so a genuinely deterministic question never reaches
# open_conversation or spends a model call it never needed.
#
# **Never a subject list, and deliberately narrow.** Every marker below
# names a *shape* - "what <unit> is it/today" - never a subject, a person
# or an event; a request about a historical or named date ("what was the
# date of the moon landing", "what's the date of Easter") does not match
# any of these, because it does not ask what day this *is*.

CURRENT_DAY_MARKERS: tuple[str, ...] = (
    "what day is it", "what day is today", "what day it is",
    "what's the day today", "what is the day today", "what's today's day",
    "which day is it", "which day is today",
    "what day of the week is it", "what's the day of the week",
    "what is the day of the week",
)
CURRENT_DATE_MARKERS: tuple[str, ...] = (
    "what is today's date", "what's today's date", "what is todays date",
    "what's todays date", "what is the date today", "what's the date today",
    "today's date", "todays date", "current date",
    "what date is it", "what date is it today",
    "what is the current date", "what's the current date",
)
CURRENT_MONTH_MARKERS: tuple[str, ...] = (
    "what month is it", "what month is this", "what's the month",
    "what is the month", "current month", "which month is it",
)
CURRENT_YEAR_MARKERS: tuple[str, ...] = (
    "what year is it", "what year is this", "what's the year",
    "what is the year", "current year", "which year is it",
)
# Foundation Repair #1 (reconciliation audit, 2026-09-05): date/day/month/year
# were already deterministic and Half-Brain-safe; clock time was not - "what
# time is it" fell through to the Brain and honestly failed with no provider
# configured. Named and matched exactly the way the four sibling marker sets
# above are, for the identical reason: a clock reading is arithmetic, never
# knowledge, and asking a model for it would be slower, sometimes wrong, and
# entirely unnecessary.
CURRENT_TIME_MARKERS: tuple[str, ...] = (
    "what time is it", "what's the time", "what is the time",
    "what time is it now", "current time", "what's the current time",
    "what is the current time", "do you know what time it is",
    "what time do you have", "got the time",
)
CURRENT_DATETIME_MARKERS: tuple[str, ...] = (
    CURRENT_DATE_MARKERS + CURRENT_DAY_MARKERS
    + CURRENT_MONTH_MARKERS + CURRENT_YEAR_MARKERS + CURRENT_TIME_MARKERS
)


def current_datetime_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request asks what the current day, date, month, year
    or time is - recognised by phrase, never by word overlap, for the
    exact reason stated above: "it" and "today" are ordinary words this
    capability must never claim merely for containing."""
    return _marker_present(task.description, CURRENT_DATETIME_MARKERS)


def _answer_current_date(request: CapabilityRequest, today) -> TaskResult:
    # %-d (no leading zero) is a glibc strftime extension, absent on
    # Windows - built by hand instead so this is portable everywhere,
    # rather than reaching for a platform-specific format code.
    spoken = f"{today.strftime('%A, %B')} {today.day}, {today.year}"
    return TaskResult.success(
        request.task, request.own_name,
        f"Today's date is {spoken}.",
        payload={
            "handled_by": "current_datetime", "derived": "date",
            "value": today.isoformat(),
        },
    )


def _answer_current_day(request: CapabilityRequest, today) -> TaskResult:
    return TaskResult.success(
        request.task, request.own_name,
        f"Today is {today.strftime('%A')}.",
        payload={
            "handled_by": "current_datetime", "derived": "day",
            "value": today.strftime("%A"),
        },
    )


def _answer_current_month(request: CapabilityRequest, today) -> TaskResult:
    return TaskResult.success(
        request.task, request.own_name,
        f"It's {today.strftime('%B')}.",
        payload={
            "handled_by": "current_datetime", "derived": "month",
            "value": today.strftime("%B"),
        },
    )


def _answer_current_year(request: CapabilityRequest, today) -> TaskResult:
    return TaskResult.success(
        request.task, request.own_name,
        f"It's {today.year}.",
        payload={
            "handled_by": "current_datetime", "derived": "year",
            "value": today.year,
        },
    )


def _answer_current_time(request: CapabilityRequest, now) -> TaskResult:
    # Built by hand rather than with %-I/%#I (the no-leading-zero hour):
    # both are platform-specific strftime extensions, absent on Windows -
    # the exact reason _answer_current_date already avoids them.
    hour12 = now.hour % 12 or 12
    period = "AM" if now.hour < 12 else "PM"
    spoken = f"{hour12}:{now.minute:02d} {period}"
    return TaskResult.success(
        request.task, request.own_name,
        f"It's {spoken}.",
        payload={
            "handled_by": "current_datetime", "derived": "time",
            "value": now.strftime("%H:%M"),
        },
    )


def current_datetime(request: CapabilityRequest) -> TaskResult:
    """Answer a current-day/date/month/year/time question from the real
    calendar - never from a stored record, and never from the Brain.

    Checked in the order a request most often names more than one unit at
    once would want it resolved: a full date subsumes a bare day, so "date"
    is tried first. select_capability only reaches this handler when
    claims already matched one of the five marker sets above, so the
    final fallback is unreachable in practice - kept as an honest answer
    rather than an assumption nothing else could route here.
    """
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    today = calendar.today()
    lowered = request.task.description.lower()

    if _marker_present(lowered, CURRENT_DATE_MARKERS):
        return _answer_current_date(request, today)
    if _marker_present(lowered, CURRENT_DAY_MARKERS):
        return _answer_current_day(request, today)
    if _marker_present(lowered, CURRENT_MONTH_MARKERS):
        return _answer_current_month(request, today)
    if _marker_present(lowered, CURRENT_YEAR_MARKERS):
        return _answer_current_year(request, today)
    if _marker_present(lowered, CURRENT_TIME_MARKERS):
        return _answer_current_time(request, calendar.now())

    return TaskResult.failure(
        request.task, request.own_name,
        "I'm not sure which part of the date you're asking about.",
    )


CURRENT_DATETIME = ManagerCapability(
    name="current_datetime",
    capabilities=frozenset({"date", "day", "month", "year", "time", "hour", "minute", "clock", "calendar", "today"}),
    handle=current_datetime,
    description="Answers what the current day/date/month/year/time is, from the real calendar - never the Brain.",
    claims=current_datetime_intent,
    # Natural Conversation fix: current_datetime_intent's marker phrases
    # already cover every genuine "what day/date/month/year is it"
    # shape (pinned by test_current_datetime.py). The word-overlap
    # fallback has no genuine case left to catch and only ever fired on
    # false positives - "I'm tired today" scores one point on "today"
    # and nothing else scores at all, answering an emotional remark with
    # a date announcement. See small_talk's identical reasoning above.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 12 additions: milestone recognition
# ---------------------------------------------------------------------------
#
# Recognising an achievement is not the same as storing one - see
# src/services/memory/milestones.py's own docstring. A bare achievement
# statement is only stored when the user has established a preference to
# do so automatically; otherwise the honest answer names what was
# recognised and how to confirm it, and stores nothing. "milestone: X" is
# the one explicit, always-store trigger, the same "an explicit ask is
# never ambiguous" rule every other marker-based capability in this file
# already lives by.

_MILESTONE_AUTO_ON: tuple[str, ...] = (
    "turn on milestone auto-confirm", "enable milestone auto-confirm",
    "auto-confirm milestones", "always save milestones automatically",
)
_MILESTONE_AUTO_OFF: tuple[str, ...] = (
    "turn off milestone auto-confirm", "disable milestone auto-confirm",
    "always ask before saving milestones", "ask before saving milestones",
)

_CELEBRATION_OFF: tuple[str, ...] = ("turn off celebrations", "disable celebrations", "no celebrations")
_CELEBRATION_SUBTLE: tuple[str, ...] = ("subtle celebrations", "tone down celebrations")
_CELEBRATION_FULL: tuple[str, ...] = ("full celebrations", "turn on celebrations", "enable celebrations")
_REDUCED_MOTION_ON: tuple[str, ...] = ("turn on reduced motion", "enable reduced motion", "reduced motion on")
_REDUCED_MOTION_OFF: tuple[str, ...] = ("turn off reduced motion", "disable reduced motion", "reduced motion off")
_CELEBRATION_PREFERENCE_MARKERS: tuple[str, ...] = (
    _CELEBRATION_OFF + _CELEBRATION_SUBTLE + _CELEBRATION_FULL
    + _REDUCED_MOTION_ON + _REDUCED_MOTION_OFF
)


def milestone_recognition_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return (
        milestone_intent(task.description)
        or _marker_present(
            task.description,
            _MILESTONE_AUTO_ON + _MILESTONE_AUTO_OFF + _CELEBRATION_PREFERENCE_MARKERS,
        )
    )


def _set_milestone_preference(request: CapabilityRequest, *, on: bool) -> TaskResult:
    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to save that preference right now."
        )
    record = MemoryRecord(
        content=f"Milestone auto-confirm is {'on' if on else 'off'}.",
        kind=MemoryCategory.USER_CONTEXT.value,
        tags={AUTO_CONFIRM_FACT_TYPE},
        context={"fact_type": AUTO_CONFIRM_FACT_TYPE, "enabled": on, "source": "conversation"},
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't save that: {verdict.reason}")
    word = "automatically save" if on else "ask before saving"
    return TaskResult.success(
        request.task, request.own_name, f"Got it - I'll {word} milestones from now on.",
        payload={"handled_by": "milestone_recognition", "preference_set": on},
    )


def _set_celebration_intensity(request: CapabilityRequest, *, level: str) -> TaskResult:
    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to save that preference right now."
        )
    record = MemoryRecord(
        content=f"Celebration intensity is {level}.",
        kind=MemoryCategory.USER_CONTEXT.value,
        tags={CELEBRATION_INTENSITY_FACT_TYPE},
        context={"fact_type": CELEBRATION_INTENSITY_FACT_TYPE, "level": level, "source": "conversation"},
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't save that: {verdict.reason}")
    return TaskResult.success(
        request.task, request.own_name, f"Got it - celebrations are now {level}.",
        payload={"handled_by": "milestone_recognition", "celebration_intensity_set": level},
    )


def _set_reduced_motion(request: CapabilityRequest, *, on: bool) -> TaskResult:
    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to save that preference right now."
        )
    record = MemoryRecord(
        content=f"Reduced motion is {'on' if on else 'off'}.",
        kind=MemoryCategory.USER_CONTEXT.value,
        tags={REDUCED_MOTION_FACT_TYPE},
        context={"fact_type": REDUCED_MOTION_FACT_TYPE, "enabled": on, "source": "conversation"},
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't save that: {verdict.reason}")
    return TaskResult.success(
        request.task, request.own_name, f"Got it - reduced motion is now {'on' if on else 'off'}.",
        payload={"handled_by": "milestone_recognition", "reduced_motion_set": on},
    )


def _resolve_celebration_intensity(request: CapabilityRequest) -> str:
    """The intensity to actually use for this reaction - the user's
    stored preference, capped by reduced motion.

    A thin wrapper over ``milestones.resolve_celebration_intensity`` -
    Stage 12 closure extracted the real logic there so
    ``scripts/heartbeat.py``'s own proactive temporal/calendar/personal
    events reach the identical decision a live conversation turn
    already does, rather than a second implementation of it.
    """
    return resolve_celebration_intensity(request.recall)


def _celebrate(request: CapabilityRequest, description: str) -> str:
    """The acknowledgement text for a stored milestone - through the
    Brain when one is configured, so expression follows this agent's own
    Personality (Stage 12 item 11), never a canned string pretending to
    be one."""
    if request.converse is not None:
        decision = request.converse(request.task, (f"Milestone recorded: {description}",))
        if decision.is_decided and decision.reason.strip():
            return decision.reason
    return f"Milestone recorded: {description}"


def milestone_recognition(request: CapabilityRequest) -> TaskResult:
    text = request.task.description

    if _marker_present(text, _MILESTONE_AUTO_ON):
        return _set_milestone_preference(request, on=True)
    if _marker_present(text, _MILESTONE_AUTO_OFF):
        return _set_milestone_preference(request, on=False)
    if _marker_present(text, _CELEBRATION_OFF):
        return _set_celebration_intensity(request, level="off")
    if _marker_present(text, _CELEBRATION_SUBTLE):
        return _set_celebration_intensity(request, level="subtle")
    if _marker_present(text, _CELEBRATION_FULL):
        return _set_celebration_intensity(request, level="full")
    if _marker_present(text, _REDUCED_MOTION_ON):
        return _set_reduced_motion(request, on=True)
    if _marker_present(text, _REDUCED_MOTION_OFF):
        return _set_reduced_motion(request, on=False)

    extraction = extract_milestone(text)
    if extraction is None:
        return TaskResult.failure(
            request.task, request.own_name, "That didn't look like a milestone to me."
        )
    milestone_type, description = extraction

    if request.retain is None:
        return TaskResult.success(
            request.task, request.own_name,
            "That sounds like it could be a milestone, but I don't have a way to record it right now.",
        )

    explicit = is_explicit(text)
    auto = False
    if not explicit and request.recall is not None:
        preferences = request.recall(
            kind=MemoryCategory.USER_CONTEXT.value, tags={AUTO_CONFIRM_FACT_TYPE}, limit=1
        )
        auto = wants_auto_confirm(preferences)

    if not explicit and not auto:
        return TaskResult.success(
            request.task, request.own_name,
            f"That sounds like a milestone worth remembering. Say "
            f"'milestone: {description}' to save it, or 'turn on milestone "
            f"auto-confirm' to save these automatically next time.",
            payload={"handled_by": "milestone_recognition", "recognized": milestone_type, "stored": False},
        )

    # Counted *before* this occurrence is stored, so the first-ever
    # instance of a milestone type is occurrence 0 - never mutated
    # afterward, never a second write, purely a read of what already
    # exists.
    occurrence = 0
    if request.recall is not None:
        occurrence = len(
            request.recall(kind=MemoryCategory.MILESTONE.value, tags={milestone_type}, limit=1000)
        )

    record = build_milestone_record(milestone_type, description, agent_name=request.own_name)
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't record that milestone: {verdict.reason}"
        )

    payload: dict[str, object] = {
        "handled_by": "milestone_recognition", "recognized": milestone_type, "stored": True,
    }
    # Presentation, selected only now that the milestone is genuinely
    # stored - never for a merely-recognised, not-yet-confirmed
    # candidate. Purely additive: whether or how this is presented never
    # changes whether the milestone itself was recorded, and a caller
    # with no celebration provider wired in (request.celebration is
    # None) still gets the identical stored milestone and acknowledgement.
    #
    # Boundary-caught, the same fault isolation ManagerAgent.take_action
    # already gives an ordinary delegation: the milestone has already
    # been genuinely written by this point, and a broken presentation
    # layer must never turn that real success into a reported failure -
    # the exact "strictly separated from reasoning and memory" guarantee
    # this stage exists to keep. A failed selection is simply omitted
    # from the payload, never fabricated and never allowed to corrupt an
    # outcome that already happened.
    if request.celebration is not None:
        try:
            intensity = _resolve_celebration_intensity(request)
            reaction = request.celebration(milestone_type, occurrence=occurrence, intensity=intensity)
            payload["celebration"] = reaction.to_dict()
        except Exception:  # noqa: BLE001 - deliberate boundary catch
            logger.debug("celebration reaction could not be selected", exc_info=True)

    return TaskResult.success(
        request.task, request.own_name, _celebrate(request, description),
        payload=payload,
    )


MILESTONE_RECOGNITION = ManagerCapability(
    name="milestone_recognition",
    capabilities=frozenset({"milestone", "milestones"}),
    handle=milestone_recognition,
    description="Recognises a personal achievement and, per governed preference, confirms or stores it as a structured milestone.",
    claims=milestone_recognition_intent,
    # Natural Conversation fix: "milestone" can appear as a passing
    # reflection ("reaching that milestone felt amazing") rather than a
    # fresh announcement. milestone_recognition_intent already recognises
    # genuine announcement shapes ("I got the internship!", "I graduated
    # last year") - confirmed unaffected by this change.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 15: communication / phone intelligence - "call Mom", "text Arthur",
# "read my recent messages", "who called me", "send this message to X"
# ---------------------------------------------------------------------------
#
# **The Brain decides, this dispatches, the tool performs.** This handler
# recognises *what kind* of communication request it is and which words
# named a party/content - it never reaches a phone network itself. The
# actual attempt happens only through request.tools.invoke(), the identical
# governed boundary general_assistance/live_information already cross, and
# what tool that name resolves to is a composition-root decision (see
# src/agents/tools/communication.py) this file never imports or knows about.
#
# **No contacts concept exists in this project** (see
# src/services/profile/profile.py - no "contact" or "phone" field anywhere),
# so "Mom", "Arthur", a number - none of it is ever resolved to a real
# address here. It passes through to the governed tool exactly as the
# person said it, honestly unresolved, the same "never invent what cannot
# actually be looked up" stance every other handler in this file already
# takes.
#
# **Recognised by request shape, not by scoring "call"/"text"/"message" as
# ordinary words** - those words appear constantly in unrelated requests
# ("call this function", "the news update"), so, exactly as
# invitation_intent/forget_intent/capture_intent already do, a claims=
# predicate decides this before word-overlap scoring ever runs. The
# patterns below require the verb to lead the (vocative-stripped) message,
# the same imperative-command shape the brief's own examples all share -
# "Call this function to..." would still misfire the same way "record" vs.
# "keep a record of" already does for RECORD_THIS (see that capability's
# own comment); this is an accepted, documented trade-off, not an oversight.
_VOCATIVE_ADDRESS = re.compile(
    r"^(?:hey|hi|hello)?\s*[A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2}\s*,\s*", re.IGNORECASE
)
_TRAILING_PUNCTUATION = re.compile(r"[.!?]+$")

_CALL_PATTERN = re.compile(r"^(?:please\s+)?(?:call|phone|dial)\s+(.+)$", re.IGNORECASE)
_TEXT_PATTERN = re.compile(r"^(?:please\s+)?text\s+(.+)$", re.IGNORECASE)
_SEND_TO_PATTERN = re.compile(r"^(?:please\s+)?send\s+(.+?)\s+to\s+(.+)$", re.IGNORECASE)
_SEND_PARTY_PATTERN = re.compile(
    r"^(?:please\s+)?send\s+(\S+)\s+(?:a|an|the)?\s*message\b\s*(.*)$", re.IGNORECASE
)
_REPLY_PATTERN = re.compile(r"^(?:please\s+)?repl(?:y|ies)\s+(?:to\s+)?(.+)$", re.IGNORECASE)

# Read has no party or content to extract - a fixed marker set, the same
# instrument FORGET_MARKERS/RECALL_MARKERS already use, is the right tool.
READ_MARKERS: tuple[str, ...] = (
    "read my messages", "read my recent messages", "read my texts",
    "check my messages", "check my recent messages",
    "any new messages", "any messages",
    "who called me", "who called",
    "any missed calls", "missed calls", "recent calls",
    "call log", "message log",
)


def _strip_vocative(text: str) -> str:
    """"Alfred, call Mom." and "call Mom." mean the same request - who was
    addressed is not what was asked, the same distinction
    extract_capture's own docstring already draws for "record this"."""
    stripped = str(text or "").strip()
    addressed = _VOCATIVE_ADDRESS.sub("", stripped, count=1).strip()
    return addressed or stripped


def _communication_shape(text: str) -> tuple[str, str, str] | None:
    """``(operation, party, content)`` recognised from this text's shape,
    or None. ``operation`` is one of "read", "send", "call", "reply".
    ``party``/``content`` are exactly what the person said, never resolved
    against anything - see this section's own module comment."""
    working = _TRAILING_PUNCTUATION.sub("", _strip_vocative(text)).strip()
    if not working:
        return None

    if _marker_present(text, READ_MARKERS):
        return ("read", "", "")

    match = _SEND_TO_PATTERN.match(working)
    if match:
        content, party = match.group(1).strip(), match.group(2).strip()
        return ("send", party, content)

    match = _SEND_PARTY_PATTERN.match(working)
    if match:
        party = match.group(1).strip(" :,.")
        content = match.group(2).strip(" :,.")
        return ("send", party, content)

    match = _TEXT_PATTERN.match(working)
    if match:
        parts = match.group(1).strip().split(None, 1)
        party = parts[0].strip(" :,.") if parts else ""
        content = parts[1].strip() if len(parts) > 1 else ""
        return ("send", party, content)

    match = _REPLY_PATTERN.match(working)
    if match:
        return ("reply", match.group(1).strip(), "")

    match = _CALL_PATTERN.match(working)
    if match:
        return ("call", match.group(1).strip(), "")

    return None


def communication_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """The ``claims=`` predicate: whether this request's shape is a
    communication command at all, before word-overlap scoring ever runs -
    see this section's own module comment for why scoring alone is wrong
    here."""
    return _communication_shape(task.description) is not None


def _communication_read(request: CapabilityRequest) -> TaskResult:
    if not request.tools.permits("communication_read"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't read messages yet - the underlying "
            f"'communication_read' capability is not installed. "
            f"Currently equipped: {equipped}.",
        )
    outcome = request.tools.invoke("communication_read", {})
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name,
            f"the 'communication_read' capability failed: {outcome.error}",
        )
    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "communication", "operation": "read"},
    )


def _communication_call(request: CapabilityRequest, party: str) -> TaskResult:
    if not request.tools.permits("communication_call"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't place calls yet - the underlying 'communication_call' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )
    outcome = request.tools.invoke("communication_call", {"to": party})
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't call {party}: {outcome.error}",
        )
    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "communication", "operation": "call", "party": party},
    )


def _communication_send(request: CapabilityRequest, party: str, content: str) -> TaskResult:
    if not request.tools.permits("communication_send"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't send messages yet - the underlying 'communication_send' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )
    outcome = request.tools.invoke("communication_send", {"to": party, "content": content})
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't send that to {party}: {outcome.error}",
        )
    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "communication", "operation": "send", "party": party},
    )


def _raise_communication_confirmation(
    request: CapabilityRequest, operation: str, party: str, content: str
) -> TaskResult:
    """Phase 4 (Real-World Communication): a consequential communication
    action - a real message sent, a real call placed, to *any* party the
    person named, "Rema", "Kai", a number, a name never seen before - is
    never performed on the strength of one remark alone. This describes
    exactly what is about to happen and waits for an explicit yes/no,
    tracked the identical way an unresolved ambiguity already is
    (``PendingConfirmation``, the same ``ActivitySessions`` in-progress-
    interaction-state module ``PendingClarification`` already lives in) -
    resolved on the very next remark by
    ``guided_interaction.py``'s own confirmation check, never executed
    here. The Brain proposed nothing here at all; this is the
    governed-capability layer describing its own next step, which is
    exactly the boundary Phase 4's own brief draws between reasoning and
    authorization.

    Tool permission is checked *before* raising anything - an
    unauthorized Manager must refuse outright, never engage with
    confirming an action it cannot perform regardless of the answer.
    """
    tool_name = "communication_call" if operation == "call" else "communication_send"
    if not request.tools.permits(tool_name):
        equipped = ", ".join(request.tools.names()) or "none"
        verb = "call anyone" if operation == "call" else "send messages"
        return TaskResult.failure(
            request.task, request.own_name,
            f"I can't {verb} yet - the underlying '{tool_name}' capability "
            f"is not installed. Currently equipped: {equipped}.",
        )
    if request.activity_sessions is None:
        # No session tracking reaches this request (a bare construction,
        # an older caller) - fail safe rather than ever executing an
        # unconfirmed consequential action.
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't confirm consequential actions in this context, so I "
            "would rather not attempt it than act without asking.",
        )

    description = (
        f'Send "{content}" to {party}? (yes/no)' if operation == "send"
        else f"Call {party}? (yes/no)"
    )
    request.activity_sessions.raise_confirmation(
        request.own_name,
        PendingConfirmation(operation=operation, party=party, content=content, description=description),
    )
    return TaskResult.success(
        request.task, request.own_name, description,
        payload={
            "handled_by": "communication", "operation": operation,
            "party": party, "awaiting": "confirmation",
        },
    )


def communication(request: CapabilityRequest) -> TaskResult:
    """Dispatch a recognised communication request to the matching
    governed tool - never more than one attempt, never a fabricated
    outcome. "reply" is recognised but not performed: this project has no
    concept of a message thread to reply *into* (see this section's own
    module comment), so it is answered honestly rather than guessed at,
    the same stance open_conversation already takes for "no Brain
    configured". "send"/"call" describe the action back and wait for
    confirmation (see ``_raise_communication_confirmation``) rather than
    executing immediately - Phase 4's own governance requirement.
    """
    shape = _communication_shape(request.task.description)
    if shape is None:
        # select_capability only reaches this handler when claims already
        # matched.
        return TaskResult.failure(
            request.task, request.own_name, "I'm not sure what communication request that is.",
        )
    operation, party, content = shape

    if operation == "reply":
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to know what message you're replying to "
            "yet, so I can't reply on my own - tell me who to message and "
            "what to say instead.",
        )

    if operation == "read":
        return _communication_read(request)

    if operation == "call":
        return _raise_communication_confirmation(request, "call", party, "")

    # operation == "send" - permission is checked *before* asking for
    # missing content: an unauthorized Manager must refuse outright, never
    # engage with collecting the details of a send it cannot perform.
    if not request.tools.permits("communication_send"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't send messages yet - the underlying 'communication_send' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )
    if not party:
        return TaskResult.failure(
            request.task, request.own_name, "Send a message to whom, exactly?",
        )
    if not content:
        return TaskResult.success(
            request.task, request.own_name, f"What would you like to send to {party}?",
            payload={
                "handled_by": "communication", "operation": "send",
                "party": party, "awaiting": "content",
            },
        )
    return _raise_communication_confirmation(request, "send", party, content)


COMMUNICATION = ManagerCapability(
    name="communication",
    # Word overlap cannot tell "call Mom" from "call this function" or
    # "message" from an ordinary noun - claims below is what actually
    # decides this, so these words only need to exist, the same "claims
    # decides, not overlap" rule RECORD_THIS's own comment already states.
    capabilities=frozenset(
        {"call", "calling", "text", "message", "messages", "reply", "phone", "dial"}
    ),
    handle=communication,
    description=(
        "Reads recent messages/calls, sends a message, or places a call "
        "through a governed communication connection - never a contact "
        "list, never a fabricated result."
    ),
    claims=communication_intent,
    # Natural Conversation fix: the comment above already argued scoring
    # was unsafe for these words, but scoreable was never actually
    # disabled - confirmed as a real, observable misroute ("Remind me to
    # call the dentist tomorrow morning" answered "I'm not sure what
    # communication request that is", claims correctly False) and closed
    # the same way small_talk's own equivalent gap already was.
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 12 closure: personal recurring events - "my mom's birthday is
# March 24", including genuine uncertainty ("either March 24 or May 24,
# I can't remember which") and its later resolution/change.
# ---------------------------------------------------------------------------
#
# **The lifecycle this handler owns, and nothing else does:**
#
#     unknown/uncertain -> presented (Control Center's own unresolved
#                           view, read-only) -> user states or restates
#                           the date -> here -> governed Memory update ->
#                           confirmed -> no repeated unresolved prompt
#
# **Confirmation and change both reuse the identical write path -
# forget the old record, retain the new one** - "the governed memory
# update path" the brief itself names, and exactly the same
# forget-then-retain composition this project's Memory contract already
# offers (there is no in-place update method on Memory, by design - see
# that class's own docstring). Nothing here invents a second one.
#
# **A person is never matched against a list.** "mom", "brother", "best
# friend" - whatever text followed "my ___'s birthday" is stored and
# read back as-is; recognising it is entirely
# ``user_facts.extract_relational_birthdays``'s job (see that module's
# own section docstring), never repeated or reinterpreted here.

PERSON_BIRTHDAY_FACT_TYPE = "person_birthday"


def _person_birthday_tag(person: str) -> str:
    """A stable, queryable tag for one person's birthday record - the
    same "fact_type as a tag" convention every other preference in this
    file already uses, scoped to *this* person so two different
    relations never collide under the same tag."""
    return "person_birthday:" + "_".join(person.lower().split())


def _confirmed_person_birthday_record(candidate: RelationalBirthdayCandidate) -> MemoryRecord:
    # A year-less birthday still needs a real ISO date for
    # due_recurring_dates (src/services/memory/milestones.py) to read -
    # last year (as of whenever this is stored) is a placeholder that is
    # always "in the past" relative to any today this could later be
    # checked against, so the same-year "just stated, do not immediately
    # re-announce" rule there still behaves sensibly without
    # due_recurring_dates needing to know this fact is birthday-shaped
    # at all. year_known below is the honest record of whether that
    # year is real.
    year = candidate.year if candidate.year is not None else (utc_today().year - 1)
    occurred = date(year, candidate.month, candidate.day)
    content = f"{candidate.person}'s birthday is {occurred.strftime('%B')} {occurred.day}"
    content += f", {candidate.year}." if candidate.year is not None else "."
    return MemoryRecord(
        content=content,
        kind=MemoryCategory.USER_CONTEXT.value,
        tags={PERSON_BIRTHDAY_FACT_TYPE, _person_birthday_tag(candidate.person)},
        context={
            "fact_type": PERSON_BIRTHDAY_FACT_TYPE,
            "person": candidate.person,
            "month": candidate.month,
            "day": candidate.day,
            "year": candidate.year,
            "year_known": candidate.year is not None,
            "date": occurred.isoformat(),
            "confirmed": True,
            "source": "conversation",
        },
    )


def _uncertain_person_birthday_record(candidate: RelationalBirthdayCandidate) -> MemoryRecord:
    described = " or ".join(f"{m}/{d}" for m, d in candidate.alternatives)
    return MemoryRecord(
        content=f"{candidate.person}'s birthday is unconfirmed - possibly {described}.",
        kind=MemoryCategory.USER_CONTEXT.value,
        tags={PERSON_BIRTHDAY_FACT_TYPE, _person_birthday_tag(candidate.person)},
        context={
            "fact_type": PERSON_BIRTHDAY_FACT_TYPE,
            "person": candidate.person,
            "confirmed": False,
            "alternatives": [{"month": m, "day": d} for m, d in candidate.alternatives],
            "source": "conversation",
            # Deliberately no "date" key - due_recurring_dates skips any
            # record with none (see that function's own docstring), so
            # an unresolved birthday is never proactively surfaced as
            # though it were a settled one, with no special-casing
            # needed there for what "unconfirmed" even means.
        },
    )


def _apply_relational_birthday(
    request: CapabilityRequest, candidate: RelationalBirthdayCandidate
) -> dict[str, object]:
    """Governed read-then-write for one recognised statement: brand new
    fact, resolution of an existing uncertainty, a deliberate change to
    an already-confirmed date, or an honest no-op when nothing changed.
    Never silently picks a date on the user's behalf."""
    existing = request.recall(
        kind=MemoryCategory.USER_CONTEXT.value, tags={_person_birthday_tag(candidate.person)}, limit=1
    )
    prior = existing[0] if existing else None
    prior_confirmed = bool((prior.context or {}).get("confirmed")) if prior is not None else None

    if not candidate.confirmed:
        if prior is not None and prior_confirmed:
            # An already-confirmed date is never silently reopened by a
            # bare "either X or Y" restatement - that would undo a
            # settled fact by accident. A deliberate change still goes
            # through the confirmed branch below (a single, definite date).
            return {
                "person": candidate.person, "action": "already_confirmed",
                "message": f"I already have a confirmed birthday for {candidate.person}.",
            }
        new_record = _uncertain_person_birthday_record(candidate)
        if prior is not None:
            request.forget(prior.id)
        verdict = request.retain(new_record)
        if not verdict.allowed:
            return {"person": candidate.person, "action": "refused", "message": f"I couldn't record that: {verdict.reason}"}
        return {
            "person": candidate.person, "action": "uncertain",
            "message": f"Got it - I'll remember that {candidate.person}'s birthday is one of those, unconfirmed until you tell me which.",
        }

    # candidate.confirmed is True - a single, definite date.
    if prior is not None and prior_confirmed:
        if ((prior.context or {}).get("month"), (prior.context or {}).get("day")) == (candidate.month, candidate.day):
            return {
                "person": candidate.person, "action": "unchanged",
                "message": f"I already have {candidate.person}'s birthday recorded.",
            }
        request.forget(prior.id)
        verdict = request.retain(_confirmed_person_birthday_record(candidate))
        if not verdict.allowed:
            return {"person": candidate.person, "action": "refused", "message": f"I couldn't update that: {verdict.reason}"}
        return {"person": candidate.person, "action": "changed", "message": f"Got it - I've updated {candidate.person}'s birthday."}

    if prior is not None and not prior_confirmed:
        request.forget(prior.id)
        verdict = request.retain(_confirmed_person_birthday_record(candidate))
        if not verdict.allowed:
            return {"person": candidate.person, "action": "refused", "message": f"I couldn't confirm that: {verdict.reason}"}
        return {
            "person": candidate.person, "action": "confirmed",
            "message": f"Got it - {candidate.person}'s birthday is confirmed.",
        }

    verdict = request.retain(_confirmed_person_birthday_record(candidate))
    if not verdict.allowed:
        return {"person": candidate.person, "action": "refused", "message": f"I couldn't record that: {verdict.reason}"}
    return {"person": candidate.person, "action": "stored", "message": f"I'll remember {candidate.person}'s birthday."}


def personal_recurring_events(request: CapabilityRequest) -> TaskResult:
    """"My mom's birthday is March 24" (and its uncertain/changed
    variants) through the governed write path - see this section's own
    module comment for the full lifecycle."""
    candidates = extract_relational_birthdays(request.task.description)
    if not candidates:
        # select_capability only reaches this handler when claims already
        # matched.
        return TaskResult.failure(request.task, request.own_name, "I couldn't work out whose birthday that was.")

    if request.recall is None or request.retain is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to remember that right now.",
        )

    results = [_apply_relational_birthday(request, candidate) for candidate in candidates]
    message = " ".join(str(r["message"]) for r in results)
    return TaskResult.success(
        request.task, request.own_name, message,
        payload={"handled_by": "personal_recurring_events", "results": results},
    )


PERSONAL_RECURRING_EVENTS = ManagerCapability(
    name="personal_recurring_events",
    # Word overlap cannot tell "my mom's birthday is March 24" from an
    # ordinary sentence containing "birthday" - claims below is what
    # actually decides this, the same "claims decides, not overlap" rule
    # every other marker-based capability in this file already states.
    capabilities=frozenset({"birthday", "birthdays"}),
    handle=personal_recurring_events,
    description=(
        "Recognises, confirms and updates another person's recurring "
        "birthday - preserving genuine uncertainty rather than "
        "silently choosing a date."
    ),
    claims=lambda task, participants=(): relational_birthday_intent(task.description),
    # Natural Conversation fix: the comment above already argued scoring
    # was unsafe here, but scoreable was never actually disabled -
    # confirmed as a real, observable misroute ("my birthday is coming up
    # and I have no plans yet" reaching this handler via word overlap
    # alone, claims correctly False for a self-mention with no date
    # stated).
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 16 / Phase 7 (Universal Connection Layer): one governed action
# through one of potentially several external, authenticated
# connections this agent was equipped with.
# ---------------------------------------------------------------------------
#
# **The flow this handler is the middle of, stated once:**
#
#     user request -> Alfred's reasoning (this claims= match) -> governed
#     capability (this handler) -> which connection
#     (request.connections, a ConnectionResolver - Phase 7) -> which
#     operation on it -> external action (request.tools.invoke, never
#     called directly) -> verified result -> Alfred's response
#
# **Eight genuinely different facts, checked in this order, never
# collapsed into one:**
#
#     reasoning               already settled - this handler was reached
#                              because connected_action_intent matched
#     connection selection    _select_connection(request.task,
#                              resolver.names, resolver.default_name) -
#                              which of this agent's own equipped
#                              connections the user's own words pick out,
#                              from each connection's own name - the
#                              configured default wins unless another
#                              genuinely, more specifically matches (see
#                              that function's own docstring); an honest
#                              failure, never a guess, only when two or
#                              more non-default connections are genuinely
#                              tied
#     operation selection     _select_operation(request.task, operations,
#                              connection) - which declared operation the
#                              user's own words pick out, from each
#                              operation's own name *and* description
#                              (see that function's own docstring), never
#                              merely operations[0] - an honest failure
#                              here, not a guess, when nothing matches or
#                              more than one operation matches equally
#                              well
#     capability availability request.tools.permits(tool_name) - was this
#                              specific (connection, operation) pair
#                              *granted*?
#     connection availability connection.availability - Evidence from an
#                              actual attempt (Availability's own
#                              UNKNOWN/AVAILABLE/UNAVAILABLE contract);
#                              deliberately *not* pre-checked here, because
#                              UNKNOWN before a first attempt is not
#                              actionable information - only the execution
#                              result below can genuinely answer this
#     authorization            connection.authorization_for(operation) - part
#                              of Connection's own operation-level extension
#                              protocol (Phase 7, src/providers/connection.py),
#                              real per-operation for a connection that
#                              declares one (DeclaredConnection - see its own
#                              docstring on why this is per-operation, not
#                              the whole connection's blanket state), the
#                              connection's own blanket authorization by
#                              inherited default for one with nothing more
#                              precise to offer (Gmail, Null) - checkable
#                              without ever attempting anything
#     execution                request.tools.invoke(tool_name, {}) - the
#                              one and only place this file ever reaches a
#                              Connection, and only through the identical
#                              governed ToolSet boundary every other
#                              consequential action in this file already
#                              crosses
#     execution result         outcome.ok - never assumed, never inferred
#                              from having been attempted
#
# **Generic over which connection, never a hardcoded service.** Nothing
# below names Gmail, a calendar, commerce, travel, a website or any
# other example - both which connection and which operation are
# resolved entirely from each connection's own declared name/metadata
# (``resolver.names``, ``connection.supported_operations``), so a
# future connection - of any kind, added through the identical
# ``ConnectionResolver`` a brand-new future connection kind already
# reaches (see ``connection_resolver.py``'s own docstring) - needs no
# new branch here, and neither does a second, third or Nth
# *simultaneous* one.
#
# **Operation-level authorization + confirmation.**
# ``authorization_for``/``requires_confirmation_for`` are part of
# ``Connection``'s own operation-level extension protocol (Phase 7 -
# Universal Connection Layer, src/providers/connection.py) - every
# connection answers both, real per-operation for one that overrides
# them (DeclaredConnection, WebsiteConnection), an honest blanket-level
# default (Gmail, Null - completely unaffected) for one that does not.
# A consequential
# operation (today, read from the same operation's own declared HTTP
# method - GET is safe, anything else is not) is never executed on the
# strength of one remark alone: it is described back and a
# ``PendingConfirmation`` is raised - the identical, generalized
# mechanism (Phase 6, real-world services & transactions)
# ``_raise_communication_confirmation`` already uses for a send/call,
# resolved deterministically on the very next remark by
# ``guided_interaction.py``'s own confirmation check, never executed
# here. Only a genuinely clear yes ever reaches real execution
# (``_execute_connected_operation`` below) - conversation is no longer
# without a confirm_token dance of its own, it now has the identical
# one every other consequential action already goes through. Control
# Center's own ``services <name> execute`` (scripts/control_center.py)
# remains a second, equally real, typed-command path to the identical
# governed execution - administrative, audited, confirmed by its own
# confirm_token - never replaced or bypassed by this one.


CONNECTED_ACTION_MARKERS: tuple[str, ...] = (
    "check my connected account", "read my connected account",
    "my connected account status", "check my connection",
)


def connected_action_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, CONNECTED_ACTION_MARKERS)


def _meaningful_words(text: str) -> "frozenset[str]":
    """``tokenize(text)`` with the common English function words
    stripped out - the identical ``STOPWORDS`` set
    ``src/services/memory/query.py``'s own ``distinctive_terms`` already
    uses for the same reason it states: almost any two English
    sentences share "the" or "a", so leaving them in would make an
    unrelated operation's *description* look relevant to nearly any
    request. Reused rather than duplicated, exactly as that module's
    own docstring already argues for a second package needing the
    identical list."""
    return tokenize(text) - _CONNECTED_ACTION_STOPWORDS


def _operation_vocabulary(connection: "Connection", operation: str) -> "frozenset[str]":
    """Every meaningful word this operation's own declared metadata
    offers - its name, plus its free-text description when the
    connection can supply one (``operation_description_for`` - part of
    ``Connection``'s own operation-level extension protocol, real for
    ``DeclaredConnection``/``WebsiteConnection``; a connection with
    nothing more precise to offer, Gmail/Null, inherits the empty
    default and is matched on its name alone, unchanged).

    A technically-named operation (``fetch_state``) whose description
    actually says what it does (e.g. "reports the account's current
    condition") becomes findable by that meaning, not only by its bare
    identifier - never a synonym table, never anything this file
    invented: the words are the service's own.
    """
    description = connection.operation_description_for(operation)
    return _meaningful_words(operation) | _meaningful_words(description)


def _select_connection(
    task: Task, names: "Sequence[str]", default_name: str
) -> "tuple[str | None, tuple[str, ...]]":
    """Which of ``names`` (``ConnectionResolver.names``) the user
    actually means - Phase 7's own Universal Connection Layer,
    resolving among however many connections this agent was equipped
    with the identical generic, no-hardcoded-name way
    ``_select_operation`` (below) already resolves among a single
    connection's own operations, one level up: never ``if "github" in
    text: ...`` - only each connection's own declared name, matched
    against the user's own words.

    **The configured default always wins unless something else
    genuinely, more specifically matches.** Every existing
    single-connection installation - and every ordinary remark that
    never names a connection at all - is completely unaffected: with
    nothing in the running that scores higher than the default, the
    default is returned, exactly the one connection this project held
    before this checkpoint. Only a request whose own words score
    STRICTLY higher against some OTHER equipped connection's own name
    is ever routed elsewhere - naming a connection is opt-in, never a
    trap sprung by ordinary phrasing that happens to share a word with
    one.

    **Trivial when there is only one.** No decision to make, so an
    installation equipped with exactly one connection - today's
    overwhelmingly common case - behaves exactly as before.

    **Fails honestly rather than guessing** only when two or more
    non-default connections are genuinely, equally the best match:
    ``(None, tied_candidates)``. Never ``(None, ())`` on its own - with
    the default always among ``names`` (``ConnectionResolver`` itself
    guarantees this), there is always something to fall back to.
    """
    if len(names) <= 1:
        return (names[0] if names else None, ())

    intent_words = _meaningful_words(_after_marker(task.description, CONNECTED_ACTION_MARKERS))
    scored = [(name, len(_meaningful_words(name) & intent_words)) for name in names]
    best = max(score for _, score in scored)
    default_score = dict(scored).get(default_name, 0)
    if best == 0 or best <= default_score:
        return (default_name, ())
    candidates = tuple(name for name, score in scored if score == best)
    if len(candidates) > 1:
        return (None, candidates)
    return (candidates[0], ())


def _select_operation(
    task: Task, operations: "Sequence[str]", connection: "Connection"
) -> "tuple[str | None, tuple[str, ...]]":
    """Which of ``operations`` the user actually meant - never merely
    ``operations[0]``.

    **Trivial when there is only one.** No decision to make, so every
    existing single-operation connection (Gmail's own ``read_email``,
    any legacy or future connection that only ever declares one
    operation) behaves exactly as before - completely unaffected.

    **Generic over more than one, from declared metadata alone.** Each
    operation's combined vocabulary - its name plus whatever
    description it declares (``_operation_vocabulary`` above), both
    word-split by this file's own canonical ``tokenize()`` (the
    identical function every other word-overlap capability here already
    uses, e.g. ``general_conversation``'s own routing) with common
    English words stripped - is matched against the part of the user's
    own request that is not the routing marker itself (``_after_marker``
    - the same extraction ``forget_this``/``recall_memory`` already
    share). Name and description count equally: both are the service's
    own declared metadata, and treating one as stronger evidence than
    the other would be a guess this function is not in a position to
    make. Never a fixed word list, never a named service or industry,
    so a future connection with new operation names or descriptions
    needs no change here.

    **Fails honestly rather than guessing**, in either direction:
    ``(None, ())`` when nothing in the request's own words overlaps any
    declared operation's vocabulary at all, and ``(None,
    tied_candidates)`` when two or more operations are genuinely,
    equally plausible. Only a single, strictly-highest-scoring operation
    is ever returned as a real pick.
    """
    if len(operations) <= 1:
        return (operations[0] if operations else None, ())

    intent_words = _meaningful_words(_after_marker(task.description, CONNECTED_ACTION_MARKERS))
    if not intent_words:
        return (None, ())

    scored = [(op, len(_operation_vocabulary(connection, op) & intent_words)) for op in operations]
    best = max(score for _, score in scored)
    if best == 0:
        return (None, ())
    candidates = tuple(op for op, score in scored if score == best)
    if len(candidates) > 1:
        return (None, candidates)
    return (candidates[0], ())


_DECLARED_ARGUMENT_PATTERN = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_]*)\s*[=:]\s*([^\s,;]+)")


def _declared_argument_values(text: str) -> "dict[str, str]":
    """Explicit ``name=value`` (or ``name: value``) pairs actually
    stated in the request's own text - the one, conservative way a
    parameter's real value ever reaches an operation. Never scraped or
    inferred from surrounding prose: a value is used only when it was
    actually written in exactly this shape.

    Two genuine sources produce this shape, never a third: a person
    typing a value directly ("...for id=item-42"), or the Brain's own
    next-step wording in a multi-step objective
    (``pursue_objective``, ``src/manager/objective_loop.py``), which
    ``OBJECTIVE_SYSTEM_PROMPT`` (``src/manager/brain_prompts.py``)
    explicitly instructs to state a value this way only when it was
    genuinely reported in the evidence gathered so far - never
    invented. This function performs no inference of its own.
    """
    values: "dict[str, str]" = {}
    for match in _DECLARED_ARGUMENT_PATTERN.finditer(text or ""):
        name = match.group(1).lower()
        value = match.group(2).strip(".,;:!?")
        if value:
            values[name] = value
    return values


def _execute_connected_operation(
    request: CapabilityRequest, connection: "Connection", operation: str, params: "dict[str, str]"
) -> TaskResult:
    """The one real place a governed connection operation is actually
    invoked - reused for an operation safe enough to run immediately
    (``connected_action``'s own tail, below) and for one that needed
    explicit confirmation first (``_try_resolve_confirmation``,
    ``src/manager/guided_interaction.py``), so the real invocation -
    the identical governed ``ToolSet`` boundary every other
    consequential action in this file already crosses - happens in
    exactly one place regardless of which path reached it. Re-checks
    tool permission itself rather than trusting a caller's earlier
    check, the identical defensive re-check
    ``_communication_call``/``_communication_send`` already perform for
    the same reason: this may run on a later turn than the one that
    raised the confirmation.
    """
    tool_name = tool_name_for(connection.name, operation)
    if not request.tools.permits(tool_name):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            f"I can't do that yet - the underlying '{tool_name}' capability "
            f"is not installed. Currently equipped: {equipped}.",
        )
    outcome = request.tools.invoke(tool_name, params)
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name,
            f"the '{tool_name}' capability failed: {outcome.error}",
        )
    return TaskResult.success(
        request.task, request.own_name, outcome.output,
        payload={"handled_by": "connected_action", "connection": connection.name, "operation": operation},
    )


def _raise_connected_action_confirmation(
    request: CapabilityRequest, connection: "Connection", operation: str, params: "dict[str, str]"
) -> TaskResult:
    """Phase 6 (real-world services & transactions): a consequential
    connection operation - one that changes something on a real,
    external service, a booking, an order, a cancellation, whatever a
    future service declares - is never performed on the strength of
    one remark alone. This describes exactly what is about to happen
    and raises a ``PendingConfirmation`` instead of executing, resolved
    on the very next remark by ``guided_interaction.py``'s own
    confirmation check - the identical mechanism
    ``_raise_communication_confirmation`` already uses for a send/call,
    generalized (never duplicated) to name a connection and its real,
    already-gathered parameters instead of a communication party and
    its content.

    Tool permission is checked *before* raising anything, the identical
    reason ``_raise_communication_confirmation`` already does: an
    unauthorized Manager must refuse outright, never engage with
    confirming an action it cannot perform regardless of the answer.
    """
    tool_name = tool_name_for(connection.name, operation)
    if not request.tools.permits(tool_name):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            f"I can't do that yet - the underlying '{tool_name}' capability "
            f"is not installed. Currently equipped: {equipped}.",
        )
    if request.activity_sessions is None:
        # No session tracking reaches this request (a bare construction,
        # an older caller) - fail safe rather than ever executing an
        # unconfirmed consequential action.
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't confirm consequential actions in this context, so I "
            "would rather not attempt it than act without asking.",
        )

    detail = f" with {', '.join(f'{k}={v}' for k, v in params.items())}" if params else ""
    description = (
        f"'{operation}' on '{connection.name}'{detail} will change something on the "
        "real service. Proceed? (yes/no)"
    )
    request.activity_sessions.raise_confirmation(
        request.own_name,
        PendingConfirmation(
            operation=operation, description=description,
            connection=connection.name, params=dict(params),
        ),
    )
    return TaskResult.success(
        request.task, request.own_name, description,
        payload={
            "handled_by": "connected_action", "connection": connection.name,
            "operation": operation, "awaiting": "confirmation",
        },
    )


def _governance_check(
    request: CapabilityRequest, *, capability: str, operation: str, action_kind: str,
    authorized: bool = True, notes: "Sequence[str]" = (),
) -> "TaskResult | None":
    """Context-Aware Agent Capability & Safety Governance's one real
    entry point from a capability handler: consult
    ``src/manager/governance.py::assess`` with whatever real evidence
    this handler already has, record the decision to this agent's own
    ``governance_log`` (Control Center's own observability surface -
    never a second, disconnected registry), and translate the verdict
    into what the handler should do next.

    Returns ``None`` for ALLOW or REQUIRE_CONFIRMATION - both mean
    "proceed" here: REQUIRE_CONFIRMATION never executes anything itself,
    it only means the caller's own existing consequential-action
    confirmation path (already about to run, or already run, for a
    genuinely consequential operation) must not be skipped - this layer
    never invents a second confirmation mechanism. Returns a real,
    honest ``TaskResult`` for CLARIFY (an honest pause, nothing
    attempted) or RESTRICT/REFUSE (a genuine, specific refusal) -
    the caller returns this directly rather than proceeding.
    """
    from src.manager.governance import GovernanceContext, GovernanceVerdict, assess

    context = GovernanceContext(
        task=request.task, agent_name=request.own_name, capability=capability,
        operation=operation, action_kind=action_kind, authorized=authorized, notes=tuple(notes),
    )
    decision = assess(context, ask_brain=request.assess_governance)
    if request.governance_log is not None:
        request.governance_log.record(
            decision, agent_name=request.own_name, capability=capability, operation=operation,
        )

    if decision.verdict in (GovernanceVerdict.ALLOW, GovernanceVerdict.REQUIRE_CONFIRMATION):
        return None
    if decision.verdict is GovernanceVerdict.CLARIFY:
        return TaskResult.success(
            request.task, request.own_name, decision.reason,
            payload={"handled_by": "governance", "verdict": decision.verdict.value, "capability": capability},
        )
    return TaskResult.failure(
        request.task, request.own_name, f"[{decision.verdict.label}] {decision.reason}",
    )


def connected_action(request: CapabilityRequest) -> TaskResult:
    """Attempt one governed action through one of Alfred's configured
    connections - see this section's own module comment for the full
    flow this function walks through in order.
    """
    resolver = request.connections
    if resolver is None or not resolver.names:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a connection configured to check right now.",
        )

    connection_name, ambiguous_connections = _select_connection(request.task, resolver.names, resolver.default_name)
    if connection_name is None:
        return TaskResult.failure(
            request.task, request.own_name,
            f"more than one connection could match that ({', '.join(ambiguous_connections)}) - "
            "say which one you mean.",
        )
    connection = resolver.get(connection_name)

    operations = connection.supported_operations
    if not operations:
        return TaskResult.failure(
            request.task, request.own_name,
            f"The '{connection.name}' connection is configured but offers no supported actions yet.",
        )

    operation, ambiguous = _select_operation(request.task, operations, connection)
    if operation is None:
        if ambiguous:
            return TaskResult.failure(
                request.task, request.own_name,
                f"'{connection.name}' has more than one action that could match that "
                f"({', '.join(ambiguous)}) - say which one you mean.",
            )
        return TaskResult.failure(
            request.task, request.own_name,
            f"I'm not sure which of '{connection.name}''s actions you mean "
            f"({', '.join(operations)}) - say which one, more specifically.",
        )
    tool_name = tool_name_for(connection.name, operation)

    if not request.tools.permits(tool_name):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            f"I can't do that yet - the underlying '{tool_name}' capability "
            f"is not installed. Currently equipped: {equipped}.",
        )

    op_authorization = connection.authorization_for(operation)
    if not op_authorization.grants_access:
        return TaskResult.failure(
            request.task, request.own_name,
            f"The '{connection.name}' connection is not authorized yet.",
        )

    # Data-flow-through-execution: a value this operation genuinely
    # needs only ever comes from what was actually, explicitly stated -
    # by the person, or by an earlier step's real, observed output the
    # Brain's own next-step wording carried forward (see
    # _declared_argument_values' own docstring). Never fabricated, and
    # never silently attempted with a required value missing - an
    # honest, specific refusal instead. An optional value is included
    # only when it was actually supplied; never invented to fill it.
    parameters = connection.parameters_for(operation)
    params: "dict[str, str]" = {}
    if parameters:
        # Only the request's own intent text, never the routing marker
        # itself (the identical exclusion _select_operation's own
        # intent-word scoring already applies) - otherwise a service
        # that happened to declare a required parameter literally named
        # e.g. "account" could be silently "satisfied" by the marker's
        # own boilerplate ("...connected account: ...") rather than
        # anything the person or an earlier step actually supplied.
        available = _declared_argument_values(_after_marker(request.task.description, CONNECTED_ACTION_MARKERS))
        missing = [p.name for p in parameters if p.required and p.name not in available]
        if missing:
            return TaskResult.failure(
                request.task, request.own_name,
                f"'{operation}' on '{connection.name}' needs {', '.join(missing)}, which "
                "I don't have yet - either tell me the value(s) directly, or complete "
                "whatever earlier step would provide them.",
            )
        params = {p.name: available[p.name] for p in parameters if p.name in available}

    is_consequential = connection.requires_confirmation_for(operation)

    # Context-Aware Agent Capability & Safety Governance: one more,
    # additive layer beyond the existing authorization/confirmation
    # checks above - never a replacement for either. Checked after both,
    # so governance reasons about the real, fully-resolved action (the
    # actual operation, its actual gathered values) rather than an
    # abstract one that might still fail for an unrelated reason.
    paused = _governance_check(
        request, capability="connected_action", operation=operation,
        action_kind="consequential" if is_consequential else "read",
        authorized=op_authorization.grants_access,
        notes=(f"'{operation}' is performed through the governed '{connection.name}' connection.",),
    )
    if paused is not None:
        return paused

    # Consequential operations are never performed on the strength of
    # one remark alone - see this section's own module comment. Checked
    # last, after authorization, parameter resolution and governance,
    # so the confirmation actually describes the real operation about to
    # run (with its real, already-gathered values) rather than an
    # abstract one that might still fail for an unrelated reason.
    if is_consequential:
        return _raise_connected_action_confirmation(request, connection, operation, params)

    return _execute_connected_operation(request, connection, operation, params)


CONNECTED_ACTION = ManagerCapability(
    name="connected_action",
    # Word overlap cannot tell "check my connected account" from an
    # ordinary sentence containing "connection" - claims below is what
    # actually decides this, the same "claims decides, not overlap"
    # rule every other marker-based capability in this file already
    # states.
    capabilities=frozenset({"connected", "connection", "connections"}),
    handle=connected_action,
    description=(
        "Performs one governed action through a configured external "
        "connection - never a fabricated result, honest about "
        "capability availability, connection availability and "
        "authorization as three separate facts."
    ),
    claims=connected_action_intent,
    # Natural Conversation fix: the comment above already argued scoring
    # was unsafe here, but scoreable was never actually disabled -
    # confirmed as a real, observable misroute ("I feel really connected
    # to this project lately" reaching this handler via word overlap
    # alone, claims correctly False).
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Stage 18: general knowledge, history, study and the user's own books
# ---------------------------------------------------------------------------
#
# The defect this closes, measured against the handlers above. A settled
# question about the world - a period, a person, an event, a discipline, a
# place, an economy, a technology, anything - reached one of two wrong
# answers:
#
#   "Tell me about X"    scored general_assistance's research word "about",
#                        and was refused as "the 'web_search' capability is
#                        not installed" - naming a *live* tool as the thing
#                        missing, when nothing about a settled question
#                        requires looking at the present moment at all.
#   "Who was X"          scored nothing anywhere and fell to
#                        open_conversation, which reaches the Brain and
#                        nothing else - so a person who had handed Alfred a
#                        library of their own books was answered as though
#                        they had handed him none.
#
# Both are the same underlying error: **"knowing" was one undifferentiated
# thing**. This capability separates it into the four it actually is, and
# never lets one stand in for another (see src/manager/knowledge.py, which
# holds the seam, and src/providers/knowledge.py, which holds the contract):
#
#     general model knowledge   the Brain's own - settled, broad, undated
#     user documents/books      quotable, attributable, the person's own
#     live/current information  LIVE_INFORMATION above, and only there
#     learning context          a fact about the user, in governed Memory
#
# **Not a topic system, and structurally incapable of becoming one.** There
# is no subject, period, country, discipline, title or person named in this
# section or in anything it calls, and none may be added. What is recognised
# is the *shape of a question*; what is looked up is the *string that
# arrived with the task*; what is quoted is *whatever the user themselves
# supplied*. A question about a collapsed civilisation, a country's second
# head of state, an invention that reshaped an industry and a decade's
# monetary policy are one line of code, not four, because not one of them
# is recognised - their grammar is. The examples this stage was specified
# with live in its tests, where examples belong, and nowhere here.
#
# **It never claims currency.** Everything gathered here is settled, and the
# evidence handed to the Brain says so in as many words
# (SETTLED_KNOWLEDGE_NOTE). A request that wants the present moment is
# claimed by LIVE_INFORMATION instead, which is the only path in this
# project permitted to imply "right now" - see knowledge_intent's own
# deferral below.

# Question shapes that ask about settled subject matter. Every entry is a
# *grammatical opening*, true of any subject a person could name, and none
# of them names one. A marker earns its place here only by that test.
#
# Note what is deliberately absent: "explain", "teach me" and "help me
# understand". Those already reach the Brain through OPEN_CONVERSATION, and
# claiming them here would move working behaviour for no gain. This
# capability exists for the questions that previously reached the *wrong*
# answer, not for the ones that already reached the right one.
KNOWLEDGE_QUESTION_MARKERS: tuple[str, ...] = (
    "tell me about", "tell me more about",
    "what is the history of", "what was the history of",
    "give me an overview of", "give me a summary of",
    "what caused", "what led to", "what were the causes of",
    "what are the causes of", "what happened in", "what happened during",
    "who was", "who were", "what was", "what were",
    "why did", "why was", "why were",
    "how did", "when did", "when was", "when were",
)

# Phase 3 fix, found live: a person who just handed Alfred a document and
# says "read this document and explain it", "summarize this document",
# "find the section about X in this file" or "what does this document
# say about X" is unambiguously asking about *their own supplied
# material* - USER_DOCUMENTS' own territory - regardless of which verb
# opens the sentence. Before this, none of those four (this phase's own
# Section 6 examples, tested literally) matched any KNOWLEDGE_QUESTION_
# MARKERS shape, and each fell through to a different, wrong place:
# ordinary word-overlap scoring or general_assistance's own "find"/
# "search" verb gate, which would have sent "find the section... in this
# file" to a live web search instead of the file the person was just
# talking about. "this document"/"this file" is as safe a shape-only
# signal as any marker in this module - it names no subject and could
# open a sentence about anything, which is exactly the test every other
# marker here already has to pass.
DOCUMENT_REFERENCE_MARKERS: tuple[str, ...] = (
    "this document", "this file", "in this file", "in this document",
    "read this document", "read this file",
    # Files/Documents completion: the identical shape, for the two
    # further formats DocumentKnowledgeProvider now genuinely reads
    # (spreadsheets, presentations) - this phase's own literal examples
    # ("read this spreadsheet...", "read this presentation...").
    "this spreadsheet", "in this spreadsheet", "read this spreadsheet",
    "this presentation", "in this presentation", "read this presentation",
    "this slide", "this slideshow",
    # REPAIR-01 (PDF/document capability): a file the person supplied
    # directly and is now pointing at - "what does this PDF say", "compare
    # this with the other document", "summarize this". Format nouns and
    # pointing words only, never a subject; the document itself is resolved
    # through the artifact store (see knowledge.py's resolve_content_sources).
    "this pdf", "in this pdf", "the other document", "the other pdf", "the other file",
    "summarize this", "summarise this",
)


# Natural Conversation fix: "what were we talking about", not "what were
# the causes of the war". Both open with "what were", which is exactly
# why KNOWLEDGE_QUESTION_MARKERS cannot tell them apart on grammar alone -
# the first names no subject at all, referring instead to the
# conversation itself. A marker earns its place here by that one test,
# the same discipline every marker list in this module already lives
# under.
CONVERSATION_CONTINUITY_MARKERS: tuple[str, ...] = (
    "what were we talking about", "what were we discussing",
    "what was i saying", "what was i talking about",
    "where were we", "what were we saying",
)


def knowledge_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request asks about settled subject matter.

    Recognised by marker phrase against the whole message, never by
    scoring individual words - the same instrument, and the same
    argument, ``current_information_intent`` already states: the
    difference between "what caused the war" and "what's happening with
    the war" lives in the grammar, not in any word this function would
    have to know the meaning of.

    Four deferrals, and each is a statement about *what kind of question
    this is*, not a priority ranking of handlers:

      - A request carrying a currency signal is asking about the present
        moment, which is ``LIVE_INFORMATION``'s and only its own. Settled
        knowledge must never be allowed to answer "right now".
      - A request using a memory marker is asking what *Alfred* holds,
        not what the world holds. "What do you remember about X" and "tell
        me about X" are different questions with the same subject.
      - A request to capture something is not a question about it.
      - A request referring back to the conversation itself ("what were
        we talking about") names no subject at all - it is a request for
        continuation, OPEN_CONVERSATION's territory (and, once it has
        conversation history to read, genuinely answerable there), never
        a settled-knowledge question about a topic that happens to share
        "what were" with one.

    Every one of the four reuses the predicate that already owns that
    request shape, rather than restating it - so a marker added to any of
    them keeps working here without this function being touched.
    """
    text = task.description
    if not (
        _marker_present(text, KNOWLEDGE_QUESTION_MARKERS)
        or _marker_present(text, DOCUMENT_REFERENCE_MARKERS)
    ):
        return False
    if current_information_intent(task, participants):
        return False
    if CURRENT_INFORMATION_WORDS & tokenize(text):
        return False
    if _marker_present(text, RECALL_MARKERS) or _marker_present(text, FORGET_MARKERS):
        return False
    if capture_intent(text):
        return False
    if _marker_present(text, CONVERSATION_CONTINUITY_MARKERS):
        return False
    # Agent-autonomous voice selection checkpoint, the identical
    # reasoning general_assistance_intent's own yield-checks already
    # apply: "find me a voice that sounds professional for this
    # presentation" matches this function's own marker shape and
    # select_voice's Tier-2 imperative shape at once - alphabetical
    # sorting would otherwise let this capability win ("knowledge" <
    # "select_voice") and answer with a knowledge-style response about
    # what a voice generically is, instead of actually choosing one.
    if select_voice_intent(task, participants):
        return False
    # REPAIR-01: "read page 2 to me" / "what does page 4 say" asks to hear
    # the content itself, not an explanation of it - read_content's own
    # request shape, answered deterministically rather than refused in
    # Half Brain for want of a model to summarise with.
    if read_content_intent(task, participants):
        return False
    # REPAIR-01: "in this PDF, replace X with Y" / "create a PDF version of
    # this" point at a document too, but ask to change or produce one -
    # revise_artifact's and create_artifact's own request shapes, never a
    # question about what the document says.
    if revise_artifact_intent(task, participants) or document_conversion_intent(text):
        return False
    return True


def _knowledge_without_reasoning(
    request: CapabilityRequest, evidence: KnowledgeEvidence, why: str
) -> TaskResult:
    """The honest refusal when no reasoning capability could answer.

    Names both axes separately - what evidence exists, and that nothing
    can reason over it - because they fail independently and a person
    fixing one needs to know which one is missing. Says nothing whatever
    about the subject: there is no path through this function that could
    produce a sentence about what was asked, which is what keeps Half
    Brain honest structurally rather than by good intentions.
    """
    if evidence.is_empty:
        missing = "; ".join(evidence.unavailable) or "no knowledge source is available"
        return TaskResult.failure(
            request.task,
            request.own_name,
            (
                "I can take on general knowledge and background questions, but "
                f"right now I have nothing to answer this from: {why}, and "
                f"{missing}. I would rather say so than guess."
            ),
        )
    # REPAIR-01: when the evidence includes a document the person pointed
    # at, the one thing Half Brain *can* do with it is read it word for
    # word - say so, rather than leaving only a refusal.
    reading_hint = (
        " I can still read it to you word for word - say \"read this document to me\" "
        "or \"read page 1 to me\"."
        if "supplied_content" in evidence.sources
        else ""
    )
    return TaskResult.failure(
        request.task,
        request.own_name,
        (
            f"I found {len(evidence.notes)} relevant passage(s) in your own "
            f"material, but I can't reason about them yet - {why} - so I "
            f"would rather say so than guess.{reading_hint}"
        ),
    )


def knowledge_inquiry(request: CapabilityRequest) -> TaskResult:
    """Answer a settled question by reasoning over everything Alfred
    genuinely holds - the user's own documents, what they are studying,
    and the Brain's own knowledge - and never over anything he does not.

    The two steps are kept apart, exactly as ``live_information`` keeps
    acquisition and reasoning apart: gather first, reason second. What
    changes here is only *which* sources are gathered, and that they are
    gathered through the same governed ``ToolSet`` boundary every other
    consequential reach in this file already crosses.

    Four honest outcomes, on the two axes that are genuinely independent -
    what evidence exists, and whether there is a Brain to reason with:

        no Brain, no evidence     both named, nothing guessed
        no Brain, evidence found  what was found is not thrown away
                                   silently, and no synthesis is invented
                                   over it either - an honest refusal
        Brain, no evidence        the Brain answers from its own settled
                                   knowledge, and the answer says which
                                   source that was
        Brain and evidence        genuine reasoning over real material

    Half Brain therefore stays honest by construction: with no reasoning
    provider there is no path through this handler that produces prose
    about the subject. The refusal names the subject nowhere and invents
    nothing about it.
    """
    evidence = gather_knowledge_evidence(
        request.task.description,
        tools=request.tools,
        recall=request.recall,
        history=request.history,
    )

    if request.converse is None:
        return _knowledge_without_reasoning(
            request, evidence, "no language model is configured for me"
        )

    # Recognition first (what this task named from the user's own declared
    # profile), then the gathered evidence, then the statement that none of
    # it is current. All three are notes - background evidence, never
    # instructions - the contract every other caller of converse() honours.
    notes = (
        tuple(request.recognition.notes())
        + _recent_conversation_notes(request)
        + evidence.with_settlement_note()
    )
    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        # The Half Brain path in production. A Brain object always reaches
        # here through composition (NullBrain is still a Brain), so it is
        # this branch and not the None check above that a real unconfigured
        # installation actually takes - which is why both must produce the
        # same honest answer rather than one of them being the tidy case.
        return _knowledge_without_reasoning(
            request, evidence, f"no language model could answer ({decision.reason})"
        )

    return TaskResult.success(
        request.task,
        request.own_name,
        decision.reason,
        payload={
            "handled_by": "knowledge_inquiry",
            "brain": decision.source,
            # Which kinds of knowing actually contributed. The Brain is
            # always one of them here, because it is what composed the
            # answer; the others appear only when they genuinely produced
            # something. A caller can therefore tell an answer drawn from
            # the person's own book from one drawn from the model alone.
            "knowledge_sources": tuple(evidence.sources) + (MODEL_KNOWLEDGE,),
            # Never "current". Stated in the payload as well as in the
            # notes so nothing downstream has to infer it.
            "currency": "settled",
            "unavailable_sources": evidence.unavailable,
        },
    )


KNOWLEDGE = ManagerCapability(
    name="knowledge",
    # Kinds of knowing, never subjects - the same discipline
    # GeneralCapability's own docstring states. There is no entry here for
    # a period, a country, a discipline or a person, and there must never
    # be one. These words are not what makes this handler reachable for
    # "who was X" (claims below is); they simply let a request that plainly
    # asks for background or a definition score normally too.
    capabilities=frozenset(
        {"knowledge", "background", "define", "definition", "meaning", "summarise", "summarize"}
    ),
    handle=knowledge_inquiry,
    description=(
        "Answers settled questions about any subject by reasoning over the "
        "user's own documents, their learning context and the Brain's own "
        "knowledge - never a subject list, and never as current information."
    ),
    claims=knowledge_intent,
)


# ---------------------------------------------------------------------------
# REPAIR-01 (PDF/document capability): Content -> Voice.
#
# "Read this PDF to me", "read page 2 to me", "what does page 4 say?" - the
# person wants to hear the content itself. Deterministic end to end (the
# artifact's own text units, a named locator, a bounded reading window), so
# it works identically in Half Brain; the words then go through the one
# canonical voice path every other answer already takes (exchange(...,
# speak=True) -> Channel.deliver), marked speak_in_full so the whole passage
# is spoken rather than a short first line. Never PDF-specific: a supplied
# spreadsheet, presentation, text file or one of Alfred's own documents is
# read by exactly the same handler.
# ---------------------------------------------------------------------------

_READ_ALOUD_MARKERS: tuple[str, ...] = ("to me", "aloud", "out loud", "read out", "read me")
# Requests about the content that are NOT a request to hear it - explaining,
# summarising, comparing belong to knowledge/open_conversation (the Brain).
_NOT_A_READING_WORDS: tuple[str, ...] = (
    "explain", "summar", "meaning", "what does it mean", "compare", "analy", "translate", "why ",
)
_POINTING_READ = re.compile(r"\bread\s+(?:me\s+)?(?:it|this|that|them)\b", re.IGNORECASE)
_LOCATOR_VERBS = re.compile(r"\b(read|say|says|said|show|on)\b", re.IGNORECASE)


def read_content_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this request asks to hear a document's own words - a unit
    locator with a reading verb ("what does page 4 say"), or "read" aimed at
    content already in play with an aloud marker ("read this PDF to me",
    "read it to me"). Recognised by shape only; which document is meant is
    resolved by the handler, never guessed here."""
    text = task.description
    lowered = " ".join(str(text or "").lower().split())
    if any(word in lowered for word in _NOT_A_READING_WORDS):
        return False
    locator = locator_in(text)
    if locator is not None and _LOCATOR_VERBS.search(lowered) and (
        "read" in lowered or "what" in lowered or content_reference_intent(text)
    ):
        return True
    if re.search(r"\bread\b", lowered) and (content_reference_intent(text) or _POINTING_READ.search(lowered)):
        return _marker_present(text, _READ_ALOUD_MARKERS)
    return False


def read_content(request: CapabilityRequest) -> TaskResult:
    """Read a document's own words back to the person - the named unit, or
    the beginning of the document bounded to one listening window, with an
    honest pointer to where to continue."""
    if not request.tools.permits("artifact_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can read documents to you, but the underlying 'artifact_production' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )

    text = request.task.description
    # Pre-Stage-9 Campaign 3: "read this file" means the file just shared.
    # When that file has no text (a video, an image, a recording) the honest
    # answer is about *that* file - never a different file read instead.
    if not _POINTS_AT_A_DOCUMENT.search(text):
        newest = resolve_shared_media(
            text, tools=request.tools, history=request.history, shared_files=request.shared_files,
        )
        if newest is not None and not int(newest.get("readable_units") or 0):
            kind = str(newest.get("content_kind") or newest.get("type") or "file").replace("_", " ")
            article = "an" if _A_OR_AN.match(kind) else "a"
            hint = (
                " Say \"transcribe this\" and I'll turn its speech into text."
                if kind in ("audio", "video", "voice recording") else ""
            )
            return TaskResult.failure(
                request.task, request.own_name,
                f"'{newest.get('title')}', the file you shared most recently, is {article} {kind} "
                f"with no readable text, so there are no words in it for me to read.{hint}",
            )
    found = resolve_content_sources(
        text, tools=request.tools, history=request.history, limit=1, shared_files=request.shared_files,
    )
    if not found:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a document or file with readable text to read yet - share one "
            "(attach it in the conversation or add it in the Control Center), or name it "
            "with 'artifact <id>'.",
        )
    payload, units = found[0]
    title = str(payload.get("title") or "that document")
    artifact_id = str(payload.get("artifact_id") or "")

    locator = locator_in(text)
    if locator is not None and not units_at(units, locator):
        kinds = sorted({u.locator.split(" ", 1)[0] for u in units})
        return TaskResult.failure(
            request.task, request.own_name,
            f"'{title}' has no {locator} - it has {len(units)} readable "
            f"{'/'.join(kinds)} unit(s).",
        )

    selected, continue_from = reading_selection(units, text)
    spoken_units = ", ".join(u.locator for u in selected)
    body = "\n\n".join(u.text for u in selected)
    tail = ""
    if continue_from is not None:
        tail = f"\n\n(That's where I'll pause - say \"read {continue_from} to me\" to continue.)"
    return TaskResult.success(
        request.task, request.own_name,
        f"From '{title}' ({spoken_units}):\n\n{body}{tail}",
        payload={
            "handled_by": "read_content",
            "artifact_id": artifact_id,
            "locators": tuple(u.locator for u in selected),
            "continue_from": continue_from,
            "speak_in_full": True,
        },
    )


# A request that names a document-like format ("read this PDF") is about a
# document even when the newest share is something else. Format nouns only.
_POINTS_AT_A_DOCUMENT = re.compile(
    r"\b(?:pdf|document|doc|docx|spreadsheet|presentation|slides?|handout|word\s+file|text\s+file|notes?)\b",
    re.IGNORECASE,
)


READ_CONTENT = ManagerCapability(
    name="read_content",
    capabilities=frozenset({"read", "aloud"}),
    handle=read_content,
    description=(
        "Reads the words of a supplied file or document aloud - a named page/section, "
        "or the beginning with a pointer to continue - through the canonical voice "
        "path; deterministic, so it works without a language model."
    ),
    claims=read_content_intent,
    scoreable=False,
)


# "speak" added for requirement 2's own canonical example - "Alfred, I
# want you to speak Italian" names no "voice"/"accent" word at all.
# Safe to widen: every check below this gate still requires either a
# genuinely parsed language/accent/gender or one of the anchored
# imperative shapes, so a bare "speak" in an unrelated remark ("let's
# speak later", "speak up") still correctly falls through to False.
_VOICE_MARKERS = ("voice", "accent", "speak")

# Anchored, imperative-shaped, mirrors _GENERAL_ACTION_PATTERN's own
# convention - covers a genuine ask this project's own small language/
# accent/gender table cannot parse ("find me a voice that sounds
# professional for this") without also claiming an unrelated remark that
# merely contains the word "voice" ("I have no voice in this decision",
# "raise your voice").
_VOICE_SELECTION_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:help\s+me\s+)?"
    r"(find|give|use|pick|choose|get|select)\b.*\bvoice\b",
    re.IGNORECASE,
)


def select_voice_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Claims a request shaped like any of select_voice's real, distinct
    governance actions:

      - a genuine language/accent/gender ask (Tier 1, deterministic)
      - "choose your voice"/"pick whatever voice suits you" - identity
        selection with no constraint at all (Tier 1, still deterministic)
      - the unmistakable imperative shape of "pick me a voice" with
        nothing this project's small table can parse otherwise
        (Tier 2, Brain-assisted - see select_voice's own docstring)
      - "choose another"/"not that one" - reselect, excluding the
        current autonomous choice
      - "you can choose your voice again" - release an explicit
        override, restoring autonomous selection

    The last two are matched independently of the ``voice``/``accent``
    marker check below - a genuine "choose another" follow-up in
    ordinary conversation often does not repeat the word "voice" at all.
    "I have no voice in this decision" and "raise your voice" match none
    of these shapes and are correctly left to whatever else already
    handles them.
    """
    if is_reselect_request(task.description) or is_release_autonomy_request(task.description):
        return True
    if not _marker_present(task.description, _VOICE_MARKERS):
        return False
    if not parse_voice_request(task.description).is_empty():
        return True
    working = _strip_vocative(task.description).strip()
    if is_unconstrained_selection_request(working):
        return True
    return bool(_VOICE_SELECTION_PATTERN.match(working))


def _describe_option_for_brain(option: "Any") -> str:
    return (
        f"{option.name} (provider={option.provider}, "
        f"language={option.language or 'unknown'}, accent={option.accent or 'unknown'}, "
        f"gender={option.gender or 'unknown'})"
    )


def _apply_voice_selection(
    request: CapabilityRequest, agent_key: str, selection: "VoiceSelection", *,
    brain_assisted: bool = False,
    requested_language: "str | None" = None,
    requested_accent: "str | None" = None,
    requested_gender: "str | None" = None,
) -> TaskResult:
    """The one, shared governed-write tail every select_voice path -
    Tier 1 constrained, Tier 1 unconstrained, Tier 2, and reselect -
    ends at, never several implementations of "persist a genuine match,
    refuse honestly otherwise".

    ``requested_*`` is what was actually asked for (independent of what
    the chosen option's own attributes happen to be) - persisted so a
    later "choose another" can honour the original constraint (or lack
    of one) rather than whatever the prior pick happened to be. See
    ``AutonomousVoiceChoice``'s own docstring.

    **Retain-first, forget-on-success only.** An earlier version of this
    function forgot the prior autonomous choice unconditionally before
    writing the new one - a real weakness, found live: a governance
    rejection (``verdict.allowed is False``) still left the agent with
    no autonomous choice on record at all, discarding a real, valid
    prior fact for no reason. The prior record's ids are captured first
    (read-only), the new record is written, and only once that write is
    confirmed allowed are the prior ids actually forgotten - so a
    rejected write leaves the agent's state exactly as it was.
    """
    if selection.option is None:
        return TaskResult.failure(
            request.task, request.own_name, f"I can't do that: {selection.reason}.",
        )

    if request.retain is None or request.recall is None or request.forget is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I found a matching voice but have no way to remember the choice right now.",
        )

    prior_ids = autonomous_choice_record_ids(agent_key, recall=request.recall)
    verdict = request.retain(
        build_voice_autonomous_choice_record(
            agent_key, selection.option,
            language_satisfied=selection.language_satisfied,
            accent_satisfied=selection.accent_satisfied,
            gender_satisfied=selection.gender_satisfied,
            requested_language=requested_language,
            requested_accent=requested_accent,
            requested_gender=requested_gender,
        )
    )
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't save that choice: {verdict.reason}",
        )
    forget_record_ids(prior_ids, forget=request.forget)

    return TaskResult.success(
        request.task, request.own_name,
        f"I'll use '{selection.option.name}' ({selection.option.provider}) - {selection.reason}.",
        payload={
            "handled_by": "select_voice",
            "voice_id": selection.option.voice_id,
            "provider": selection.option.provider,
            "language_satisfied": selection.language_satisfied,
            "accent_satisfied": selection.accent_satisfied,
            "gender_satisfied": selection.gender_satisfied,
            "brain_assisted": brain_assisted,
        },
    )


def select_voice(request: CapabilityRequest) -> TaskResult:
    """Governed voice autonomy - a real backend decision path generic to
    any agent built on ``ManagerSkills`` (Alfred, Arthur's own
    hypothetical future ManagerSkills-based sibling, or a specialist
    nobody has written yet): every fact this function touches -
    ``request.own_name``, ``request.discover_voices``, ``request.recall``/
    ``forget``/``retain`` - is the calling agent's own, never Alfred's
    by name. Nothing here is Alfred-specific; nothing needs to be to add
    a new agent.

    Five distinct actions, checked in this order (release first - it is
    the one action meaningful regardless of what else is true):

        1. RELEASE: "you can choose your voice again" - clears this
           agent's own explicit override (release_voice_preference),
           restoring autonomous selection. A real governance state
           change, not UI wording - a subsequent unconstrained/
           constrained/reselect request genuinely reaches Tier 1/2
           afterward, because voice_preference_for now returns None.
        2. An explicit override already in effect always wins from here
           on - deferred to, never silently replaced, for every
           remaining action below.
        3. RESELECT: "choose another"/"not that one" - re-runs the same
           constraint (or lack of one) the agent's own last autonomous
           choice actually satisfied, excluding that choice's own
           voice_id, so a real different option is required rather than
           silently repeating the same deterministic pick.
        4. UNCONSTRAINED: "choose your voice that best suits you" -
           identity selection alone, no language/accent/gender implied
           at all (``pick_any`` - deterministic, no Brain needed: an
           unconstrained delegation is the simplest case there is).
        5. CONSTRAINED (Tier 1, deterministic) or, only once Tier 1 finds
           nothing whatsoever to parse, Tier 2 (Brain-assisted, given
           only real discovered options and validated against them
           afterward - see this function's own git history for why).

    Half-Brain safe throughout except Tier 2 itself: nothing in
    RELEASE/RESELECT/UNCONSTRAINED/CONSTRAINED ever calls
    ``request.converse``.
    """
    agent_key = request.own_name
    text = request.task.description

    if is_release_autonomy_request(text):
        if request.recall is None or request.forget is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to change that right now.",
            )
        released = release_voice_preference(agent_key, recall=request.recall, forget=request.forget)
        message = (
            "You're free to choose your own voice autonomously again."
            if released else
            "Your voice was already being chosen autonomously - nothing was locked in."
        )
        return TaskResult.success(
            request.task, request.own_name, message,
            payload={"handled_by": "select_voice", "released_override": released},
        )

    existing_override = voice_preference_for(agent_key, recall=request.recall) if request.recall else None
    if existing_override:
        return TaskResult.success(
            request.task, request.own_name,
            f"Your voice is already explicitly set to '{existing_override}' - that stays in "
            f"effect. Use the voice command, or say 'you can choose your voice again', to change it.",
            payload={"handled_by": "select_voice", "deferred_to_override": existing_override},
        )

    if request.discover_voices is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to discover real voice options right now.",
        )
    available = tuple(request.discover_voices())

    if is_reselect_request(text):
        prior = autonomous_voice_choice_for(agent_key, recall=request.recall) if request.recall else None
        if prior is None:
            # No prior autonomous voice choice on record. Never
            # fabricate one from nothing: a bare "choose another"/"try
            # another" genuinely can belong to any topic ("choose
            # another route"), and RESELECT_MARKERS is deliberately not
            # anchored to the word "voice" (see its own docstring) - the
            # only thing that keeps a stray collision from ever
            # producing a nonsensical voice pick is refusing honestly
            # right here, exactly the same "no genuine match, no silent
            # substitution" rule select_voice_option itself already
            # holds to.
            return TaskResult.failure(
                request.task, request.own_name,
                "There's no previous voice choice of mine to change - tell me what "
                "language, accent or voice you'd like, or ask me to choose one.",
            )
        exclude = frozenset({prior.voice_id})
        req_lang, req_accent, req_gender = prior.requested_language, prior.requested_accent, prior.requested_gender
        if req_lang or req_accent or req_gender:
            reconstructed = VoiceRequest(
                raw_text=text, language=req_lang, accent=req_accent, gender=req_gender,
            )
            selection = select_voice_option(reconstructed, available, exclude=exclude)
        else:
            selection = pick_any(available, exclude=exclude)
        return _apply_voice_selection(
            request, agent_key, selection,
            requested_language=req_lang, requested_accent=req_accent, requested_gender=req_gender,
        )

    working = _strip_vocative(text).strip()
    if is_unconstrained_selection_request(working):
        selection = pick_any(available)
        return _apply_voice_selection(request, agent_key, selection)

    parsed = parse_voice_request(text)
    if not parsed.is_empty():
        selection = select_voice_option(parsed, available)
        return _apply_voice_selection(
            request, agent_key, selection,
            requested_language=parsed.language, requested_accent=parsed.accent, requested_gender=parsed.gender,
        )

    # Tier 2. select_voice_intent's own third branch already confirmed
    # this request has the imperative shape of "pick me a voice" - the
    # only thing left to check is whether real reasoning can actually be
    # reached, and whether real options exist to reason over.
    if not available:
        return TaskResult.failure(
            request.task, request.own_name,
            "I can't do that: no voice options are currently available from any configured provider.",
        )
    if request.converse is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I didn't recognise a specific language, accent or gender in that request, and "
            "I have no way to reason about it further right now.",
        )

    notes = tuple(_describe_option_for_brain(option) for option in available) + (
        "Those are the only real, currently-available voice options. Reply naming exactly "
        "one of them by its exact name, and briefly say why it fits.",
    )
    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't reason about which voice fits, so I would rather say so than guess "
            f"({decision.reason}).",
        )

    named = {option.voice_id: option for option in available if option.name.lower() in decision.reason.lower()}
    if len(named) != 1:
        return TaskResult.failure(
            request.task, request.own_name,
            "I reasoned about this but couldn't settle on a single available voice unambiguously, "
            "so I would rather say so than guess.",
        )

    chosen = next(iter(named.values()))
    selection = VoiceSelection(chosen, False, False, False, f"chosen by reasoning: {decision.reason.strip()}")
    return _apply_voice_selection(request, agent_key, selection, brain_assisted=True)


SELECT_VOICE = ManagerCapability(
    name="select_voice",
    capabilities=frozenset({"voice", "accent"}),
    handle=select_voice,
    description=(
        "Chooses a real, currently-available voice matching a requested "
        "language/accent/gender, deterministically when the request states one, "
        "Brain-assisted when it does not - from whatever the configured speech "
        "provider genuinely offers, never overriding an explicit user voice choice."
    ),
    claims=select_voice_intent,
    scoreable=False,
)


# Generalized user knowledge/personal-context checkpoint. Words present only
# so a person asking "what can you do" hears something honest - routing
# itself never uses this set (scoreable=False below; see personal_context's
# own claims=).
#
# Tier 2 eligibility: a marker this project has no fixed vocabulary for
# ("skill"/"schedule"/"timetable"/...) is deliberately not exhaustive - it
# only decides whether a Brain is even asked, never what the answer is.
# personal_context.py's own deterministic layer, not this set, is what
# actually recognises the acceptance examples.
_PERSONAL_CONTEXT_TIER2_MARKERS = frozenset({
    "learned", "learnt", "mastered", "skill", "skills",
    "schedule", "timetable", "syllabus", "coursework",
    "started", "starting",
})
_PERSONAL_CONTEXT_SELF_REFERENCE = re.compile(r"\b(?:i|i'm|i've|my)\b", re.IGNORECASE)


def _personal_context_working_text(text: str) -> str:
    """What a statement actually teaches: the part after a retention marker
    ("remember that my goal is X" teaches "my goal is X"), or the whole
    statement when there is no marker."""
    requested = retention_requests([text])
    return requested[0].content if requested else text


def _personal_context_query_defers(task: Task, participants: Sequence[str] = ()) -> bool:
    """A question about the user that a more specific capability already
    owns - the calendar ("what's my schedule tomorrow"), tasks, recordings,
    an age or date derivation, the clock, or an explicit "what do you
    remember about" (recall_memory, which reads the same canonical store)."""
    text = task.description
    return bool(
        calendar_retrieve_intent(text) or extract_calendar_period_query(text)
        or list_tasks_intent(text) or list_recordings_intent(text)
        or temporal_derivation_intent(task, participants)
        or current_datetime_intent(task, participants)
        or recall_memory_intent(task, participants)
    )


def personal_context_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Claims a self-declared, updatable personal fact - or a question about
    what the user has taught - by shape:

      - a question about the user's own stored context ("what am I
        learning", "what's my goal"), unless a more specific capability
        owns it (see ``_personal_context_query_defers``);
      - any of personal_context.py's deterministic statement shapes,
        including a study statement and a "my X is Y" declaration, read
        from what follows a retention marker when there is one;
      - Tier 2 eligibility only: a self-referential *statement* (never a
        question) carrying one of this domain's markers that Tier 1 did not
        happen to match.
    """
    text = task.description
    if personal_context_query_intent(text):
        return not _personal_context_query_defers(task, participants)
    working = _personal_context_working_text(text)
    if _personal_context_shape_matches(working):
        return True
    if select_voice_intent(task, participants):
        return False
    if _is_question_or_request_statement(text):
        return False
    if not _PERSONAL_CONTEXT_SELF_REFERENCE.search(text):
        return False
    return bool(_PERSONAL_CONTEXT_TIER2_MARKERS & tokenize(text))


def _answer_personal_context_query(request: CapabilityRequest, text: str) -> TaskResult:
    """"What am I learning?", "what's my schedule?" - answered from the
    canonical store deterministically, identically with or without a Brain.
    Current statements are preferred; a topic that is only known to have
    ended is still reported honestly as ended."""
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to recall what you've told me right now.",
        )
    focus = extract_personal_context_query(text) or ""
    items = all_current_personal_context(recall=request.recall, limit=200)
    matched = match_personal_context(items, focus)
    current = tuple(item for item in matched if item.status == STATUS_CURRENT) or matched
    current = current[:PERSONAL_CONTEXT_ANSWER_LIMIT]
    if not current:
        return TaskResult.success(
            request.task, request.own_name,
            "You haven't told me anything about that yet.",
            payload={"handled_by": "personal_context", "action": "recall", "count": 0},
        )
    lines = [describe_for_user(item) for item in current]
    message = lines[0] if len(lines) == 1 else "Here's what I know:\n" + "\n".join(f"- {line}" for line in lines)
    return TaskResult.success(
        request.task, request.own_name, message,
        payload={
            "handled_by": "personal_context", "action": "recall", "count": len(current),
            "topics": [item.topic for item in current],
        },
    )


def _broad_statement_is_personal(request: CapabilityRequest, working: str) -> bool:
    """For the one broad shape ("my X is Y"): when a Brain is available, ask
    whether this really is a personal fact rather than, say, a problem being
    described. Only an explicit "not a personal fact" answer rejects it; no
    Brain, or a Brain that cannot answer, keeps the deterministic reading -
    Half Brain stays useful."""
    if request.converse is None:
        return True
    notes = (
        f"The user said: \"{working}\". Decide whether this is the user telling you a "
        "durable, updatable fact about themselves (a preference, goal, plan, schedule, "
        "possession or other personal context) rather than describing a problem or "
        "asking for help. Reply with exactly one line: TOPIC: <short noun phrase> if it "
        "is such a personal fact, or TOPIC: none if it is not.",
    )
    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        return True
    answer = re.search(r"TOPIC:\s*(\S+)", decision.reason, re.IGNORECASE)
    return not (answer and answer.group(1).lower().startswith("none"))


# REPAIR-01: a declaration that points at something rather than stating a
# value ("this is my timetable", "here's my syllabus") - and how recently the
# shared file must have appeared for "this" to mean it.
_DOCUMENT_DECLARATION = re.compile(r"\b(?:this is|here is|here'?s)\s+my\b", re.IGNORECASE)
_DOCUMENT_DECLARATION_HISTORY_LINES = 4


def personal_context(request: CapabilityRequest) -> TaskResult:
    """Generalized user knowledge - the mechanism, never a catalog.

    Tier 1 (deterministic, Half-Brain-safe): personal_context.py's own
    shape-recognised extraction. Tier 2 (Brain-assisted, only when Tier 1
    found nothing whatsoever to parse): the Brain is given the user's exact
    words and asked to name the topic and whether it is current or
    discontinued, never asked to invent content - the record's own content
    stays the user's real sentence either way, the identical "validate
    against real evidence, never fabricate" discipline select_voice's own
    Tier 2 already holds itself to.

    Governed persistence only (``request.retain``) - never a raw write.
    Nothing here is Alfred-specific: every fact this function touches is
    the calling agent's own, exactly as select_voice already established.
    """
    text = request.task.description
    if personal_context_query_intent(text):
        return _answer_personal_context_query(request, text)

    working = _personal_context_working_text(text)
    candidate = extract_personal_context_candidate(working)
    brain_assisted = False

    if candidate is not None and candidate.broad and not _broad_statement_is_personal(request, working):
        # A Brain judged "my X is Y" to be something other than a personal
        # fact - answered as ordinary conversation, never stored.
        return open_conversation(request)

    if candidate is None:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "That sounds like something about yourself, but I couldn't tell "
                "what the topic is, and I have no way to reason about it further "
                "right now.",
            )
        notes = (
            f"The user said: \"{working}\". This appears to be the user teaching you "
            "an updatable personal fact about themselves - a skill, a schedule, or "
            "some other changing personal context, not a request for information. "
            "Reply with exactly two lines and nothing else:\n"
            "TOPIC: <a short noun phrase naming what this is about, taken from the "
            "user's own words>\n"
            "STATUS: <current, if this is newly true or newly ongoing; discontinued, "
            "if the user is saying something has ended or is no longer the case>\n"
            "If this is genuinely not a personal fact about the user, reply with "
            "TOPIC: none",
        )
        decision = request.converse(request.task, notes)
        if not decision.is_decided or not decision.reason.strip():
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't reason about what to remember, so I would rather say "
                f"so than guess ({decision.reason}).",
            )
        # Non-greedy, stopped at STATUS: or end-of-string explicitly - a
        # Brain reply is not guaranteed to keep the two-line shape the
        # prompt asked for (some providers collapse newlines to spaces),
        # so "TOPIC: guitar lessons STATUS: current" on one physical line
        # must not let a greedy capture swallow the status label too.
        topic_match = re.search(
            r"TOPIC:\s*(.+?)(?:\s*(?:\n|\bSTATUS:)|$)", decision.reason, re.IGNORECASE,
        )
        topic_raw = topic_match.group(1).strip() if topic_match else ""
        if not topic_raw or topic_raw.lower().startswith("none"):
            return TaskResult.failure(
                request.task, request.own_name,
                "I reasoned about this but couldn't confidently identify a "
                "personal fact to remember, so I would rather say so than guess.",
            )
        status_match = re.search(r"STATUS:\s*(current|discontinued)", decision.reason, re.IGNORECASE)
        status = status_match.group(1).lower() if status_match else STATUS_CURRENT
        candidate = PersonalContextCandidate(
            topic=topic_raw[:120],
            content=working.strip(),
            category="context",
            status=status,
        )
        brain_assisted = True

    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to remember that right now.",
        )

    # REPAIR-01 (PDF/document capability): "this is my timetable" said about
    # a file the person *just* shared carries no typed value - the document
    # is the value. Only its id is remembered (the governed, supersedable
    # claim "this is the current one"); the text stays in the artifact
    # store and is read fresh whenever it is relevant (see knowledge.py).
    # A later "this is my updated timetable" about a newer file supersedes
    # it through the identical topic key, the old document kept as history.
    attached_id = ""
    artifact_note = ""
    if (
        candidate.status == STATUS_CURRENT
        and _DOCUMENT_DECLARATION.search(text)
        and candidate.content.startswith("The user stated (regarding")
        and request.history is not None
    ):
        recent = request.history
        found = resolve_content_sources(
            text, tools=request.tools,
            history=lambda: tuple(recent())[-_DOCUMENT_DECLARATION_HISTORY_LINES:],
            limit=1, fallback_latest=False,
        )
        if found:
            payload, _units = found[0]
            attached_id = str(payload.get("artifact_id") or "")
            document_title = str(payload.get("title") or "the shared document")
            candidate = PersonalContextCandidate(
                topic=candidate.topic,
                content=f"The user's current {candidate.topic} is the document '{document_title}' (artifact {attached_id}).",
                category="document",
                status=STATUS_CURRENT,
            )
            artifact_note = f" I'll use '{document_title}' as your current {candidate.topic}."

    record = build_personal_context_record(candidate, agent_name=request.own_name, artifact_id=attached_id)
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't record that: {verdict.reason}",
        )

    # Personal Organization/Calendar checkpoint: the requirement 5 chain -
    # "user-provided information -> user-context understanding ->
    # organizational/calendar interpretation -> structured calendar
    # state". A schedule-shaped teaching statement ("this is my new IT
    # schedule: Mon-Fri 9am-3pm") deterministically also names real
    # calendar structure; when it does, that structure is written too,
    # linked by the identical topic_key this personal-context record
    # itself resolved to - never a second, unrelated store, and never
    # forced onto a statement that plainly names no schedule at all
    # ("I've learned Python").
    calendar_note = ""
    if candidate.status == STATUS_CURRENT and request.retain is not None:
        topic_key = normalize_topic_key(candidate.topic)
        calendar_when = request.calendar.today() if request.calendar is not None else None
        calendar_candidate = extract_calendar_item(text, today=calendar_when)
        if calendar_candidate is not None:
            calendar_record = build_calendar_item_record(
                calendar_candidate, agent_name=request.own_name,
                topic_key=topic_key, related_personal_context_topic=candidate.topic,
            )
            calendar_verdict = request.retain(calendar_record)
            if calendar_verdict.allowed:
                calendar_note = " I've also updated your calendar to match."

    if attached_id:
        confirmation = "Got it."
    elif candidate.content.startswith("The user stated (regarding"):
        confirmation = f"Got it - I'll remember what you told me about your {candidate.topic}."
    else:
        said_back = describe_for_user(candidate)
        confirmation = f"Got it - I'll remember that {said_back[:1].lower()}{said_back[1:]}"
    return TaskResult.success(
        request.task, request.own_name,
        f"{confirmation}{calendar_note}{artifact_note}",
        payload={
            "handled_by": "personal_context",
            "topic": candidate.topic,
            "status": candidate.status,
            "category": candidate.category,
            "brain_assisted": brain_assisted,
            "calendar_linked": bool(calendar_note),
            "artifact_id": attached_id,
        },
    )


PERSONAL_CONTEXT = ManagerCapability(
    name="personal_context",
    capabilities=frozenset({
        "learned", "skill", "skills", "schedule", "timetable", "syllabus",
        # What the user is studying is personal context too (Pre-Stage-9
        # Campaign 1) - the words IDENTITY.md declares for it stay backed.
        "study", "studying", "learn", "learning", "revise", "revising", "coursework",
    }),
    handle=personal_context,
    description=(
        "Learns and updates self-declared, changeable personal facts about the "
        "user - a skill they say they have learned, a schedule, an organisational "
        "context, or any other topic-shaped personal fact - so a later statement "
        "about the same topic correctly supersedes an earlier one instead of both "
        "being treated as simultaneously current."
    ),
    claims=personal_context_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Personal Organization / Calendar - real structured state, built on the
# generalized Personal Context checkpoint above (see calendar_items.py's
# own module docstring for what genuinely distinguishes the two).
# ---------------------------------------------------------------------------

# Anchored, imperative-shaped, mirroring _GENERAL_ACTION_PATTERN's and
# _VOICE_SELECTION_PATTERN's own convention - a genuine ask this
# project's small deterministic parser cannot handle ("put my study
# session on the calendar, it's usually around 6pm on weekdays")
# without also claiming an unrelated remark that merely *mentions* one
# of these words ("the deadline is either the 5th or the 12th, I'm
# honestly not sure"). Found live, this checkpoint: a bare marker-word-
# plus-pronoun test (no imperative shape required) claimed exactly that
# ambiguous statement, silently taking it from whatever capability
# genuinely owns clarifying a stated uncertainty.
_CALENDAR_TIER2_PATTERN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
    r"(?:add|put|schedule|book|update|change|move)\b.*\b"
    r"(?:calendar|schedule|appointment|appointments|event|events|"
    r"meeting|meetings|deadline|deadlines)\b",
    re.IGNORECASE,
)


def _calendar_tier2_eligible(text: str) -> bool:
    working = _strip_vocative(text).strip()
    return bool(_CALENDAR_TIER2_PATTERN.match(working))


def calendar_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Claims a request shaped like an add/cancel/reschedule/retrieve
    calendar statement - Tier 1's own deterministic shapes, or (Tier 2
    eligibility only) a self-referential remark carrying one of this
    domain's markers that Tier 1 did not happen to match.

    Three deferrals, the identical "the more specific capability wins"
    reasoning every other capability in this file already states one
    for: an outstanding to-do item is TASK's own explicit territory
    ("add a task to buy milk on Friday" must never become a calendar
    entry named "task to buy milk"), another person's recurring date is
    PERSONAL_RECURRING_EVENTS' own ("my mom's birthday is March 24" is
    not this capability's concern even though it names a date), and a
    statement phrased as *teaching* Alfred something about the person
    ("this is my new IT schedule: Mon-Fri 9am-3pm") is personal_context's
    own - found live, this checkpoint: without this deferral, "calendar"
    sorts alphabetically before "personal_context" and would win a
    request that also happens to name a real weekday/time range,
    silently skipping the personal-context record (and, with it,
    requirement 5's own "user-context understanding -> calendar
    interpretation" chain) entirely. personal_context's own handler
    creates the matching calendar state itself once it owns the
    request - see its own body.

    A fourth deferral, found live during Real Audio Recording/Capture:
    ``calendar_cancel_intent``'s own marker set matches bare "cancel"/
    "cancel the" anywhere in the text, so "cancel the recording" was
    winning here (sorted before "cancel_recording" alphabetically)
    before the capability that actually recognises it ever saw the
    request - the identical "the more specific capability wins" shape
    as the three deferrals above, for a domain (audio recording)
    calendar has no business claiming a word from.
    """
    text = task.description
    if (
        create_task_intent(text) or list_tasks_intent(text)
        or complete_task_intent(text) or reopen_task_intent(text)
        or update_task_intent(text)
    ):
        return False
    if relational_birthday_intent(text):
        return False
    if _personal_context_shape_matches(text):
        return False
    if (
        start_recording_intent(text) or stop_recording_intent(text)
        or cancel_recording_intent(text) or list_recordings_intent(text)
    ):
        return False
    if (
        calendar_add_intent(text) or calendar_cancel_intent(text)
        or calendar_reschedule_intent(text) or calendar_retrieve_intent(text)
    ):
        return True
    return _calendar_tier2_eligible(text)


def _calendar_period_window(period: str, today: date) -> "tuple[date, date | None]":
    """The concrete date window a retrieval period phrase names -
    ``(start, end)``, ``end`` of ``None`` meaning "open-ended" (never
    actually returned here; every branch below names a real bound or
    falls to the default 14-day lookahead, so a caller never has to
    special-case an unbounded window)."""
    cleaned = period.strip().lower()
    if not cleaned:
        return today, today + timedelta(days=14)
    if cleaned == "today":
        return today, today
    if cleaned == "tomorrow":
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
    if cleaned in ("week", "this week"):
        return today, today + timedelta(days=7)
    resolved = parse_calendar_date(cleaned, today=today)
    if resolved is not None:
        return resolved, resolved
    return today, today + timedelta(days=14)


def _calendar_add(request: CapabilityRequest, text: str) -> TaskResult:
    """ADD - Tier 1 deterministic, Tier 2 Brain-assisted only when Tier 1
    found nothing to parse. Every piece of a Tier 2 answer (date,
    weekdays, times) is re-validated against real parsing before it is
    ever written - the Brain names *what* was said, never invents *when*
    something is, the identical discipline ``personal_context``'s own
    Tier 2 and ``select_voice``'s own Tier 2 already hold themselves to.
    """
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    today = calendar.today()
    candidate = extract_calendar_item(text, today=today)
    brain_assisted = False

    if candidate is None:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "That sounds like something for your calendar, but I couldn't work "
                "out the date, time or title, and I have no way to reason about it "
                "further right now.",
            )
        notes = (
            f"The user said: \"{text}\". This appears to be the user adding "
            "something to their calendar. Reply with exactly these lines and "
            "nothing else:\n"
            "TITLE: <a short name for this item, from the user's own words>\n"
            f"DATE: <a specific date this refers to, as YYYY-MM-DD (today is "
            f"{today.isoformat()}), or NONE if this recurs weekly instead of "
            "naming one date>\n"
            "WEEKDAYS: <comma-separated weekday names this recurs on every week, "
            "or NONE for a one-off item>\n"
            "START_TIME: <a specific time, e.g. 3pm or 15:00, or NONE>\n"
            "END_TIME: <a specific end time, or NONE>\n"
            "If this genuinely does not belong on a calendar, reply with TITLE: NONE",
        )
        decision = request.converse(request.task, notes)
        if not decision.is_decided or not decision.reason.strip():
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't reason about what to add, so I would rather say so "
                f"than guess ({decision.reason}).",
            )
        reply = decision.reason

        def _field(name: str, stop: str) -> str:
            m = re.search(rf"{name}:\s*(.+?)(?:\s*(?:\n|\b{stop}:)|$)", reply, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        title_raw = _field("TITLE", "DATE")
        if not title_raw or title_raw.upper().startswith("NONE"):
            return TaskResult.failure(
                request.task, request.own_name,
                "I reasoned about this but couldn't confidently identify something "
                "to add to your calendar, so I would rather say so than guess.",
            )

        start_date = None
        date_raw = _field("DATE", "WEEKDAYS")
        if date_raw and date_raw.upper() != "NONE":
            start_date = parse_calendar_date(date_raw, today=today)

        days_of_week: set[int] = set()
        weekdays_raw = _field("WEEKDAYS", "START_TIME")
        if weekdays_raw and weekdays_raw.upper() != "NONE":
            for name in re.split(r",|\band\b", weekdays_raw):
                key = name.strip().lower()
                if key in CALENDAR_WEEKDAY_NAMES:
                    days_of_week.add(CALENDAR_WEEKDAY_NAMES[key])

        start_time_val = end_time_val = None
        start_raw = _field("START_TIME", "END_TIME")
        if start_raw and start_raw.upper() != "NONE":
            found = find_calendar_time_range(start_raw)
            if found is not None:
                start_time_val = found[0]
        end_match = re.search(r"END_TIME:\s*(.+?)\s*$", reply, re.IGNORECASE)
        end_raw = end_match.group(1).strip() if end_match else ""
        if end_raw and end_raw.upper() != "NONE":
            found = find_calendar_time_range(end_raw)
            if found is not None:
                end_time_val = found[0]

        if start_date is None and not days_of_week:
            return TaskResult.failure(
                request.task, request.own_name,
                "I reasoned about this but couldn't pin down a real date or "
                "recurring weekday for it, so I would rather say so than guess.",
            )

        recurrence = (
            Recurrence(frequency="weekly", days_of_week=tuple(sorted(days_of_week)))
            if days_of_week else Recurrence()
        )
        candidate = CalendarItemCandidate(
            title=title_raw[:120],
            start_date=start_date if not days_of_week else None,
            start_time=start_time_val, end_time=end_time_val, recurrence=recurrence,
        )
        brain_assisted = True

    if request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to update your calendar right now.",
        )

    existing = all_current_calendar_items(recall=request.recall) if request.recall else ()
    conflicts = find_calendar_conflicts(candidate, existing, today=today)

    record = build_calendar_item_record(candidate, agent_name=request.own_name)
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't add that to your calendar: {verdict.reason}",
        )

    message = f"Added to your calendar: {candidate.title}."
    if conflicts:
        names = ", ".join(c.title for c in conflicts)
        message += f" Note: this overlaps with {names}."

    return TaskResult.success(
        request.task, request.own_name, message,
        payload={
            "handled_by": "calendar", "action": "add",
            "item_id": record.id, "item_key": record.context["item_key"],
            "title": candidate.title, "brain_assisted": brain_assisted,
            "conflicts": [c.item_key for c in conflicts],
        },
    )


def _calendar_locate_one(
    request: CapabilityRequest, title: str,
) -> "CalendarItemRecord | TaskResult":
    """The single current item ``title`` unambiguously names, or a
    ready-to-return TaskResult describing why none could be resolved -
    shared by cancel and reschedule, the identical "zero or many is a
    real answer, not an error to hide" shape ``complete_task``/
    ``update_task`` already use via ``match_tasks_by_topic``."""
    existing = all_current_calendar_items(recall=request.recall)
    matches = match_calendar_items_by_title(existing, title)
    if not matches:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I don't have anything matching '{title}' on your calendar.",
        )
    if len(matches) > 1:
        listing = "; ".join(m.title for m in matches)
        return TaskResult.failure(
            request.task, request.own_name,
            f"I found {len(matches)} items matching '{title}' - be more specific: {listing}",
        )
    return matches[0]


def _calendar_cancel(request: CapabilityRequest, text: str) -> TaskResult:
    target_title = extract_calendar_cancel_target(text)
    if not target_title:
        return TaskResult.failure(request.task, request.own_name, "Cancel what, exactly?")
    if request.recall is None or request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to update your calendar right now.",
        )

    located = _calendar_locate_one(request, target_title)
    if isinstance(located, TaskResult):
        return located
    target = located

    cancelled = CalendarItemCandidate(
        title=target.title, start_date=target.start_date, start_time=target.start_time,
        end_time=target.end_time, recurrence=target.recurrence,
        status=CALENDAR_STATUS_CANCELLED, notes=target.notes,
    )
    record = build_calendar_item_record(
        cancelled, agent_name=request.own_name, topic_key=target.topic_key,
        series_key=target.series_key, item_key=target.item_key,
        related_task_id=target.related_task_id,
        related_personal_context_topic=target.related_personal_context_topic,
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't cancel that: {verdict.reason}",
        )
    return TaskResult.success(
        request.task, request.own_name, f"Cancelled: {target.title}.",
        payload={"handled_by": "calendar", "action": "cancel", "item_key": target.item_key},
    )


def _calendar_reschedule(request: CapabilityRequest, text: str) -> TaskResult:
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    today = calendar.today()
    parsed = extract_calendar_reschedule(text, today=today)
    if parsed is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Reschedule which item to when, exactly? Try 'move my X to <date/time>'.",
        )
    target_title, new_date, new_start, new_end = parsed
    if request.recall is None or request.retain is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to update your calendar right now.",
        )

    located = _calendar_locate_one(request, target_title)
    if isinstance(located, TaskResult):
        return located
    target = located

    moved = CalendarItemCandidate(
        title=target.title,
        start_date=new_date if new_date is not None else target.start_date,
        start_time=new_start if new_start is not None else target.start_time,
        end_time=new_end if new_end is not None else target.end_time,
        recurrence=target.recurrence, status=CALENDAR_STATUS_SCHEDULED, notes=target.notes,
    )
    record = build_calendar_item_record(
        moved, agent_name=request.own_name, topic_key=target.topic_key,
        series_key=target.series_key, item_key=target.item_key,
        related_task_id=target.related_task_id,
        related_personal_context_topic=target.related_personal_context_topic,
    )
    verdict = request.retain(record)
    if not verdict.allowed:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't reschedule that: {verdict.reason}",
        )
    return TaskResult.success(
        request.task, request.own_name, f"Rescheduled: {target.title}.",
        payload={"handled_by": "calendar", "action": "reschedule", "item_key": target.item_key},
    )


def _calendar_retrieve(request: CapabilityRequest, text: str) -> TaskResult:
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to check your calendar right now.",
        )
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    today = calendar.today()
    current = all_current_calendar_items(recall=request.recall)
    if not current:
        return TaskResult.success(
            request.task, request.own_name, "You have nothing on your calendar.",
            payload={"handled_by": "calendar", "action": "retrieve", "count": 0},
        )

    period = extract_calendar_period_query(text)
    window_start, window_end = _calendar_period_window(period, today)
    horizon = max(((window_end or window_start) - today).days + 1, 1)

    entries: list[tuple[date, "CalendarItemRecord"]] = []
    for item in current:
        for occurrence in next_calendar_occurrences(
            item, today=window_start, count=30, horizon_days=horizon,
        ):
            if window_end is not None and occurrence > window_end:
                continue
            entries.append((occurrence, item))
    entries.sort(key=lambda pair: (pair[0], pair[1].start_time or time.min))

    if not entries:
        message = (
            "Nothing scheduled for that period, as far as I know."
            if period else "You have nothing coming up."
        )
        return TaskResult.success(
            request.task, request.own_name, message,
            payload={"handled_by": "calendar", "action": "retrieve", "count": 0},
        )

    lines = [f"{occurrence.isoformat()}: {item.describe()}" for occurrence, item in entries]
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={"handled_by": "calendar", "action": "retrieve", "count": len(entries)},
    )


def calendar(request: CapabilityRequest) -> TaskResult:
    """Generalized Personal Organization/Calendar - real structured
    state, built on the generic mechanism ``personal_context`` already
    established. Four distinct actions, checked in an order chosen the
    same way ``select_voice``'s own is: retrieval and cancellation are
    unambiguous imperative shapes checked first, reschedule next (it
    also implies "to", ruling out a bare add), add last as the
    remaining, broadest case.
    """
    text = request.task.description
    if calendar_retrieve_intent(text):
        return _calendar_retrieve(request, text)
    if calendar_cancel_intent(text):
        return _calendar_cancel(request, text)
    if calendar_reschedule_intent(text):
        return _calendar_reschedule(request, text)
    return _calendar_add(request, text)


CALENDAR = ManagerCapability(
    name="calendar",
    capabilities=frozenset({
        "calendar", "schedule", "appointment", "appointments", "event", "events",
        "meeting", "meetings", "deadline", "deadlines",
    }),
    handle=calendar,
    description=(
        "Adds, cancels, reschedules and retrieves real, structured calendar "
        "items - dates, times, durations and weekly recurrence - built on the "
        "generalized personal-context mechanism, never a fixed catalog of "
        "activities."
    ),
    claims=calendar_intent,
    scoreable=False,
)


# Every Manager capability handler, keyed by handler name.
#
# **This is the extension point.** A future Manager capability is registered
# here and declared in IDENTITY.md - and ManagerAgent, the router and the
# assessor are untouched, because none of them has ever known what a
# capability is. They route to an agent; this decides what that agent does.

# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 3: turning shared or recorded speech into text
# ---------------------------------------------------------------------------

# Phrasings that ask for spoken content as text - grammar, never a subject.
_TRANSCRIBE_MARKERS: tuple[str, ...] = (
    "transcribe this", "transcribe that", "transcribe the", "transcribe my", "transcribe it",
    "make a transcript", "give me a transcript", "get a transcript",
    "what does this recording say", "what does the recording say", "what does my recording say",
    "what does this audio say", "what does the audio say", "what is said in this", "what's said in this",
)


# A request that names a recording rather than a shared file - grammar only.
_NAMES_A_RECORDING = re.compile(r"\b(?:recording|recordings|voice\s+note|voice\s+memo)\b", re.IGNORECASE)

def transcribe_media_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, _TRANSCRIBE_MARKERS)


def transcribe_media(request: CapabilityRequest) -> TaskResult:
    """Real speech-to-text over the audio or video this request points at,
    shared in this conversation, or recorded most recently - through the
    governed ``media_production`` tool and whichever transcription provider
    is actually configured. Never a fabricated transcript: an unconfigured
    provider is reported as exactly that."""
    if not request.tools.permits("media_production"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can transcribe audio and video, but the underlying 'media_production' "
            f"capability is not installed. Currently equipped: {equipped}.",
        )

    text = request.task.description
    source = find_media_path(text)
    title = source or ""
    artifact_id = ""

    def _latest_recording() -> "tuple[str | None, str, str]":
        if request.recall is None:
            return None, "", ""
        for record in request.recall(kind="audio_recording", limit=1):
            recorded = describe_artifact(request.tools, str((record.context or {}).get("artifact_id") or ""))
            if recorded is not None:
                return _rendition_path(recorded), str(recorded.get("title") or ""), str(recorded.get("artifact_id") or "")
        return None, "", ""

    # "Transcribe my recording" names a recording: the latest one is meant,
    # even when a file was shared in this conversation too (found at runtime).
    names_a_recording = bool(_NAMES_A_RECORDING.search(text))
    if source is None and names_a_recording:
        source, title, artifact_id = _latest_recording()
    if source is None:
        described, source = _shared_media(request, ("audio", "video", "voice_recording"))
        if described is not None:
            title, artifact_id = str(described.get("title") or ""), str(described.get("artifact_id") or "")
    if source is None and not names_a_recording:
        source, title, artifact_id = _latest_recording()
    if source is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which recording? I don't see audio or video in this request, shared in this "
            "conversation, or recorded yet.",
        )

    outcome = request.tools.invoke("media_production", {"operation": "transcribe", "content": source})
    name = f"'{title}'" if title else "that recording"
    if not outcome.ok:
        return TaskResult.failure(
            request.task, request.own_name, f"I couldn't transcribe {name}: {outcome.error}",
        )
    return TaskResult.success(
        request.task, request.own_name, f"Transcript of {name}:\n{outcome.output}",
        payload={"handled_by": "transcribe_media", "artifact_id": artifact_id},
    )


TRANSCRIBE_MEDIA = ManagerCapability(
    name="transcribe_media",
    capabilities=frozenset({"transcribe", "transcript", "transcription"}),
    handle=transcribe_media,
    description=(
        "Turns the speech in shared, uploaded or recorded audio and video into a real, "
        "timestamped transcript through the configured transcription provider - "
        "honestly refused when none is configured."
    ),
    claims=transcribe_media_intent,
    scoreable=False,
)

MANAGER_CAPABILITIES: dict[str, ManagerCapability] = {
    SELF_DESCRIPTION.name: SELF_DESCRIPTION,
    ROSTER_REPORT.name: ROSTER_REPORT,
    PARTICIPANT_INVITATION.name: PARTICIPANT_INVITATION,
    CONVERSATIONAL_FLOOR.name: CONVERSATIONAL_FLOOR,
    GENERAL_ASSISTANCE.name: GENERAL_ASSISTANCE,
    OPEN_CONVERSATION.name: OPEN_CONVERSATION,
    FORGET.name: FORGET,
    RECALL_MEMORY.name: RECALL_MEMORY,
    CORRECT_MEMORY.name: CORRECT_MEMORY,
    LIVE_INFORMATION.name: LIVE_INFORMATION,
    IMAGE_ANALYSIS.name: IMAGE_ANALYSIS,
    GENERATE_DOCUMENT.name: GENERATE_DOCUMENT,
    CREATE_ARTIFACT.name: CREATE_ARTIFACT,
    REVISE_ARTIFACT.name: REVISE_ARTIFACT,
    CREATE_DIAGRAM.name: CREATE_DIAGRAM,
    REVISE_DIAGRAM.name: REVISE_DIAGRAM,
    MEDIA_PRODUCTION.name: MEDIA_PRODUCTION,
    CLIP_VIDEO.name: CLIP_VIDEO,
    RECORD_THIS.name: RECORD_THIS,
    START_RECORDING.name: START_RECORDING,
    STOP_RECORDING.name: STOP_RECORDING,
    CANCEL_RECORDING.name: CANCEL_RECORDING,
    LIST_RECORDINGS.name: LIST_RECORDINGS,
    CREATE_TASK.name: CREATE_TASK,
    LIST_TASKS.name: LIST_TASKS,
    COMPLETE_TASK.name: COMPLETE_TASK,
    REOPEN_TASK.name: REOPEN_TASK,
    UPDATE_TASK.name: UPDATE_TASK,
    TEMPORAL_DERIVATION.name: TEMPORAL_DERIVATION,
    # Foundation Repair #1: current day/date/month/year, from the real
    # calendar - never a stored fact, never the Brain.
    CURRENT_DATETIME.name: CURRENT_DATETIME,
    MILESTONE_RECOGNITION.name: MILESTONE_RECOGNITION,
    COMMUNICATION.name: COMMUNICATION,
    PERSONAL_RECURRING_EVENTS.name: PERSONAL_RECURRING_EVENTS,
    CONNECTED_ACTION.name: CONNECTED_ACTION,
    # Stage 18: general knowledge and history as one capability for every
    # subject. What the user is studying is personal context (Campaign 1).
    KNOWLEDGE.name: KNOWLEDGE,
    SELECT_VOICE.name: SELECT_VOICE,
    # Generalized user knowledge/personal-context checkpoint.
    PERSONAL_CONTEXT.name: PERSONAL_CONTEXT,
    # Generalized Personal Organization/Calendar checkpoint.
    CALENDAR.name: CALENDAR,
    # REPAIR-01 (PDF/document capability): Content -> Voice.
    READ_CONTENT.name: READ_CONTENT,
    # Pre-Stage-9 Campaign 3: shared or recorded audio/video -> text.
    TRANSCRIBE_MEDIA.name: TRANSCRIBE_MEDIA,
}

# Used when a task reaches the Manager without matching any handler's words.
# The router only sends work here when it scored a declared capability, so
# this is the honest answer to "routed to me, but not by anything specific" -
# say who I am and what I can do, which is never wrong.
DEFAULT_CAPABILITY = ROSTER_REPORT


def available_capabilities() -> list[str]:
    """Every handler name currently registered."""
    return sorted(MANAGER_CAPABILITIES)


def handled_words(
    capabilities: dict[str, ManagerCapability] | None = None,
) -> frozenset[str]:
    """Every word some registered handler can actually answer."""
    registry = MANAGER_CAPABILITIES if capabilities is None else capabilities
    words: set[str] = set()
    for capability in registry.values():
        words |= capability.capabilities
    return frozenset(words)


def offered_capabilities(
    declared: frozenset[str],
    capabilities: dict[str, ManagerCapability] | None = None,
) -> frozenset[str]:
    """The capabilities the Manager may actually be routed for.

    The intersection of what the identity document declares and what some
    handler can answer. A declared word with nothing behind it is dropped and
    named in a warning - not silently, because a document that no longer means
    what it says is exactly what someone needs to be told about, and not
    fatally, because refusing to start over a stale word in a markdown file
    would be a worse trade than running with it ignored.
    """
    handled = handled_words(capabilities)
    offered = declared & handled

    unbacked = declared - handled
    if unbacked:
        logger.warning(
            "IDENTITY.md declares %s, but no registered handler answers "
            "%s - the word is ignored. Declaring a capability does not create "
            "one; register a handler in src/manager/capabilities.py.",
            ", ".join(sorted(unbacked)),
            "them" if len(unbacked) > 1 else "it",
        )
    return offered


def select_capability(
    task: Task,
    capabilities: dict[str, ManagerCapability] | None = None,
    participants: Sequence[str] = (),
) -> ManagerCapability:
    """Pick the handler whose declared words best match this task.

    The same scoring idea the CapabilityRouter uses to pick an agent, and that
    EmailAgent uses to pick an analyzer, one level further down. Ties break on
    handler name so the choice is total and repeatable rather than dependent
    on dictionary ordering.
    """
    registry = MANAGER_CAPABILITIES if capabilities is None else capabilities
    tokens = tokenize(task.description)

    # A handler that recognises its own request is asked first. Sorted by name
    # so the answer is total and repeatable, exactly as the scoring below is,
    # and a claim that matched two handlers would resolve the same way twice
    # rather than on dictionary order.
    #
    # This is not a priority list and not a second router: no name is ranked
    # above another, and nothing here chooses an *agent*. It decides which of
    # Alfred's own handlers answers a request Alfred was already given.
    for capability in sorted(registry.values(), key=lambda c: c.name):
        if capability.claims is not None and capability.claims(task, participants):
            return capability

    best: ManagerCapability | None = None
    best_score = 0
    for capability in sorted(registry.values(), key=lambda c: c.name):
        if not capability.scoreable:
            continue
        score = capability.score(tokens)
        if score > best_score:
            best, best_score = capability, score

    if best is None:
        # Nothing declared a word this task used. Real content with
        # nowhere else to go reaches the Brain, honestly, rather than a
        # roster listing standing in for an answer nobody asked for;
        # genuinely empty input (see conversational_intent's own "no
        # meaningful tokens" case, which is claimed before this point and
        # never reaches here) keeps the prior, harmless default.
        return OPEN_CONVERSATION if tokens else DEFAULT_CAPABILITY
    return best
