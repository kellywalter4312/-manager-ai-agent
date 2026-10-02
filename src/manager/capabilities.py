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

import json
import re
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Mapping, Sequence

from src.agents.base_agent import BaseAgent
from src.agents.tools.tool import ToolResult
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskResult, tokenize
from src.core.shared_context import shared_notes
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
    recording_transcript_source,
    resolve_content_sources,
    resolve_shared_media,
    save_transcript,
    describe_artifact,
)
from src.services.artifacts.content import (
    ARTIFACT_ID_PATTERN,
    ContentUnit,
    content_reference_intent,
    locator_in,
    reading_selection,
    recording_reference_intent,
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
    _meaningful_stems,
    build_personal_context_record,
    current_personal_context_for,
    describe_for_user,
    extract_personal_context_candidate,
    extract_personal_context_query,
    extract_remembered_note,
    is_question_or_request as _is_question_or_request_statement,
    match_personal_context,
    normalize_topic_key,
    personal_context_history,
    personal_context_query_intent,
)
from src.services.memory.query import STOPWORDS as _QUERY_STOPWORDS
from src.services.memory.query import distinctive_terms
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
    recording_kind_of,
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
    # plain, already-attributed lines ("Sam: ...", "<agent>: ...") -
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
    # What the provider speaking for this agent can do - voices, spoken
    # languages, cross-language accents, paces - read live.
    voice_facts: "Callable[[], Any] | None" = None
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
    # A small local model capabilities may consult, attributed, for what Half
    # Brain cannot do alone - never the Manager's reasoning brain.
    assist: "Any | None" = None
    # Universal voice translation: offline translation bound narrowly -
    # ``translate(text, target, source=None)`` returning text, source, target
    # and route, raising LookupError with a person-facing reason. Deterministic,
    # so Half Brain translates as well as Full Brain. None when no engine is on.
    translate: "Any | None" = None

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

# Who may use a capability - see ManagerCapability.authority.
AUTHORITY_GRANTABLE = "grantable"
AUTHORITY_EVERY_AGENT = "every_agent"
AUTHORITY_MANAGER = "manager"
CAPABILITY_AUTHORITIES = (AUTHORITY_GRANTABLE, AUTHORITY_EVERY_AGENT, AUTHORITY_MANAGER)


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
    # Campaign 2 workspaces: whether this handler answers in any workspace an
    # agent is in, whatever that workspace's capability profile - talking,
    # describing itself, its own memory, moving between workspaces. A
    # profile scopes *work*; it never makes an agent unable to converse.
    anywhere: bool = False
    # Final ecosystem closure: who may use this capability. A capability is
    # registered once, here, and *authorised* per agent - never owned by one.
    #
    #   grantable    the default. The Manager always has it; the owner may
    #                authorise any specialist for it in the Control Center
    #                (``capabilities grant <agent> <name>``). Granting it
    #                never takes it from the Manager or from anyone else.
    #   every_agent  every agent has it, with its own name, memory and voice.
    #                ``own_path`` names the agent-level route a specialist
    #                reaches it through when that is not the shared
    #                capability handler.
    #   manager      system-level orchestration only the Manager holds;
    #                ``authority_reason`` must say why. Never a default: a
    #                capability is Manager-only because its reason is stated.
    #
    # A new capability therefore needs no list edited anywhere to become
    # authorisable, and no specialist has to exist for it to be registered.
    authority: str = "grantable"
    authority_reason: str = ""
    own_path: str = ""
    # Phase 11 closure: whether a *successful* run of this capability is the
    # whole of what the person asked - one governed record, lookup or act
    # (add a task, answer the date, remember a fact) rather than a step toward
    # something larger. Declared here, by the capability, so the objective
    # loop needs no list of names: it never pursues such a request further.
    # Found with the real local model: "add a task to call the plumber" was
    # completed, then "continued" five times by a model that judged more was
    # needed, adding four tasks nobody asked for and reporting failure.
    # False by default - composing capabilities (open conversation, research,
    # media, documents, connected services) may still be one step of more.
    completes_request: bool = False
    # Phase 11 closure: whether this capability is an act on *who is in the
    # room* (invite, excuse, open a discussion) - the orchestrator's own even
    # when the sentence is said to a participant. "Arthur, you can go" is
    # addressed to Arthur, and an address hands the turn to him - who can
    # neither leave the room himself nor excuse anyone. Declared here so the
    # Manager's routing names no capability.
    room_act: bool = False
    # Phase 11 closure: whether this capability's request carries words that
    # are *material* - a phrase to translate, a message to send - rather than
    # anything the person is telling Alfred. Memory capture reads each remark
    # for "remember that ..." and "my name is ..."; without this, "translate my
    # name is Sam into Spanish" and "send Kai a message saying my name is Sam"
    # each stored the person's name as Sam, the second before the send was
    # even confirmed. Declared here so the capture names no capability.
    carries_material: bool = False
    # P11Z-09: whether this is a self-contained operation of the Manager's
    # own - and may therefore pre-empt specialist delegation.
    #
    # ``claims`` above answers one question: does this handler recognise this
    # request's shape? That is *recognition*, and it was silently doing a
    # second job. ``ManagerAgent.decide`` asked it before the router, and
    # ``collaboration_candidates`` returned nothing when it was true - so any
    # capability that recognised a request also *owned* it, shutting an
    # authorised specialist out of both delegation and collaboration.
    #
    # The reasoning for that (``ManagerSkills.claims``'s own docstring) named
    # seven capabilities and said none of them benefits from specialist
    # collaboration. That was true of seven. It became false once nearly every
    # capability carried ``claims`` - research, knowledge, artifacts, diagrams,
    # 3D, media, documents, translation, content analysis: exactly the work an
    # owner creates and grants a specialist to do.
    #
    # So the two concepts are separated. Recognition stays ``claims``.
    # *Ownership* is declared here, by the capability, and must say why - the
    # same shape ``authority``/``authority_reason`` already uses, for the same
    # reason: a rule that pre-empts delegation has to state its grounds, and a
    # bare boolean would let a future capability acquire that power silently.
    #
    # False by default. A capability earns this by genuinely operating on the
    # Manager's own state - his memory, his session, the room's roster, the
    # person's own governed records, his own equipment, the clock - rather than
    # by producing or reasoning about subject-matter work someone else could be
    # authorised to do. Nothing about the count of capabilities, any domain, or
    # any agent's name is consulted anywhere.
    self_contained: bool = False
    self_contained_reason: str = ""
    # GM campaign: this capability takes a *connection itself* as the object
    # of a request - watching it, grouping it, supervising work on it - rather
    # than performing one of its operations. A request that names an equipped
    # connection used to go to connected_action unconditionally, so "keep an
    # eye on <connection> every 5 minutes" performed an operation instead of
    # declaring a watch. With this declared, such a capability's own claim is
    # asked first; every other connection-naming request still reaches
    # connected_action exactly as before. A property the capability states
    # about itself - ManagerSkills names no capability.
    operates_on_connections: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ManagerCapability.name must not be empty")
        if self.authority not in CAPABILITY_AUTHORITIES:
            raise ValueError(
                f"ManagerCapability '{self.name}' has authority {self.authority!r}; "
                f"expected one of {', '.join(CAPABILITY_AUTHORITIES)}"
            )
        if self.authority == AUTHORITY_MANAGER and not self.authority_reason.strip():
            raise ValueError(
                f"ManagerCapability '{self.name}' is Manager-only but states no reason - "
                "a restriction must say why it exists"
            )
        # The identical discipline, for the identical reason: pre-empting
        # specialist delegation is a restriction, so it states its grounds.
        if self.self_contained and not self.self_contained_reason.strip():
            raise ValueError(
                f"ManagerCapability '{self.name}' declares itself self-contained but states no "
                "reason - a capability that pre-empts specialist delegation must say why it is "
                "the Manager's own work"
            )
        # No inverse rule, deliberately. A first version of this refused a
        # reason stated without the flag, on the theory that one without the
        # other is a typo. It is a restriction the idiom it copies does not
        # have - `authority_reason` may be stated by any capability - and it
        # would refuse a future capability that documents *why* it is open to
        # delegation, which is legitimate metadata about the same axis. The
        # field is documentation; only the claim of ownership is governed.
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


def _direct_recall(request: "CapabilityRequest", *args: Any, **kwargs: Any):
    """GM-13: the person asking about, correcting or forgetting their own
    memory reaches every sensitivity level - the one read that may go past the
    gate every indirect use of memory recalls through."""
    recall = request.recall
    if getattr(recall, "gated", False):
        kwargs.setdefault("max_sensitivity", "restricted")
    return recall(*args, **kwargs)


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

# What every capability handler in this module already writes into its own
# result payload to name itself ("handled_by": "translate", ...). Named here
# because two *readers* outside this function depend on it being that exact
# fact rather than a re-derivation: ``ManagerSkills.execute`` (which corrects
# the capability it recorded when a pending confirmation was resolved on
# another capability's behalf) and ``src/control_center/trail.py`` (which
# reports which capability actually answered). The ~100 handlers that write
# it keep their literals - this names the contract for the side that reads
# it, exactly as ``INVITED_KEY`` above already does for one payload field.
HANDLED_BY_KEY = "handled_by"


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


# ---------------------------------------------------------------------------
# Phase 10 - discussions and participant removal
# ---------------------------------------------------------------------------
#
# The same division of labour invitation already has: these handlers resolve
# *who* and *what* from the words and the roster, and return it in the payload.
# They join, remove and run nothing - there is no room in scope. Whoever owns
# the room (scripts/exchange.py) acts on the payload, and the discussion itself
# is run by src/manager/discussion.py through each participant's own runtime.

DISCUSSION_KEY = "discussion"
DISMISSED_KEY = "dismissed"


def hands_act_to_room(result: TaskResult) -> bool:
    """Whether this result's whole effect is an act the room's owner carries
    out - bringing somebody in, excusing somebody, opening or steering a
    discussion - carried in its payload. Such a request is complete in the one
    step that resolved it: re-pursuing it, or replacing its result with a
    composed summary, would drop the act itself (see objective_loop.py)."""
    payload = result.payload or {}
    return any(key in payload for key in (INVITED_KEY, DISMISSED_KEY, DISCUSSION_KEY))

# What makes a request about a discussion at all.
DISCUSSION_WORDS = frozenset({
    "discuss", "discussion", "discussions", "meeting", "conference", "brainstorm",
    "brainstorming", "debate", "deliberate", "roundtable",
})
# The room as a whole, when no participant is named. Deliberately narrow:
# "all", "team" and "together" are ordinary words ("discuss all the options",
# "the team budget") and would make ordinary conversation a meeting.
ROOM_WORDS = frozenset({"everyone", "everybody"})
_ROOM_PHRASE = re.compile(r"\ball of you\b", re.IGNORECASE)
# A discussion *now* is not a meeting to be put in a calendar. Any of these
# and the request belongs to the calendar, exactly as before this existed.
SCHEDULING_WORDS = frozenset({
    "schedule", "scheduled", "reschedule", "calendar", "book", "booked", "appointment",
    "remind", "reminder", "tomorrow", "today", "tonight", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "am", "pm", "week",
    "month", "next", "later",
})
_CONTINUE_WORDS = frozenset({"continue", "carry", "keep", "another", "round"})
_CONCLUDE_WORDS = frozenset({"end", "conclude", "close", "finish", "wrap", "summarise", "summarize"})
_CANCEL_WORDS = frozenset({"cancel", "abort", "stop", "abandon"})
# The name must follow the verb directly: "excuse me, <agent>" is politeness,
# and "remove" is left out entirely - "remove the meeting with <agent>" is
# calendar work, not a request for anybody to leave.
_DISMISS = re.compile(r"\b(?:dismiss|excuse)\s+(?P<rest>.+)$", re.IGNORECASE)


def _discussion_action(tokens: frozenset[str]) -> str | None:
    """continue / conclude / cancel when the words ask for one, else None."""
    if not (tokens & DISCUSSION_WORDS):
        return None
    if tokens & _CANCEL_WORDS:
        return "cancel"
    if tokens & _CONCLUDE_WORDS:
        return "conclude"
    if tokens & _CONTINUE_WORDS:
        return "continue"
    return None


def _names_after(text: str, position: int, names: Sequence[str]) -> tuple[str, ...]:
    """Every name in ``names`` found in ``text`` after ``position``, as a whole
    word, longest first so a longer name is not also counted as a shorter one.
    A possessive ("<name>'s report") is somebody's thing, not somebody taking
    part, so it never counts."""
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    lowered = text.lower()
    for name in sorted({" ".join(str(n).split()) for n in names if str(n).strip()}, key=len, reverse=True):
        for match in re.finditer(rf"(?<![\w]){re.escape(name.lower())}(?![\w'’])", lowered):
            if match.start() < position:
                continue
            if any(a <= match.start() < b for a, b in taken):
                continue
            taken.append((match.start(), match.end()))
            found.append((match.start(), name))
            break
    return tuple(name for _, name in sorted(found))


# The opening address of a message - "<name> and <name>, discuss ..." - when it
# is nothing but names and conjunctions. Anything else before the comma is a
# clause, not an address.
_VOCATIVE_CLAUSE = re.compile(r"^(?P<names>[^,;:.!?]{1,80}),\s*\S")


def _vocative_names(text: str, names: Sequence[str]) -> tuple[str, ...]:
    """The participants this message opens by addressing, or ``()``."""
    match = _VOCATIVE_CLAUSE.match(" ".join(str(text or "").split()))
    if match is None:
        return ()
    clause = match.group("names")
    found = _names_after(clause, 0, names)
    # Every word of the address has to be one of those names or a connective -
    # "<name>, before we start" opens with a name and is not an address list.
    remainder = clause
    for name in sorted(found, key=len, reverse=True):
        remainder = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", " ", remainder, flags=re.IGNORECASE)
    if tokenize(remainder) - frozenset({"and", "plus"}):
        return ()
    return found


# A discussion *now* is not a meeting to be put in a calendar - but a
# scheduling word inside what is to be discussed ("whether I should answer
# recruiters this week") is part of the subject, not a request to schedule
# anything. So the calendar wins on its own evidence: a real extractable dated
# item, or a scheduling word standing in front of the discussion word (where a
# scheduling verb leads the sentence - "schedule a meeting with X on Friday").
# Found by the Phase 11 closure sweep: the flat "any scheduling word anywhere"
# guard silently dropped every discussion whose subject mentioned a day or a
# week.
def _defers_to_scheduling(text: str, opening: int) -> bool:
    from src.services.memory.calendar_items import extract_calendar_item

    if extract_calendar_item(text) is not None:
        return True
    return bool(tokenize(str(text or "")[:opening]) & SCHEDULING_WORDS)


def discussion_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this asks the orchestrator to open, continue, conclude or
    cancel a discussion.

    Continue/conclude/cancel need a discussion word and the action. Opening
    needs a discussion word followed - later in the sentence - by a registered
    participant's name or a word for the room as a whole, and no scheduling
    word: "let's discuss the budget with <agent>" is a discussion now, while
    "<orchestrator>, let's discuss the budget" stays ordinary conversation
    and "schedule a meeting with <agent> on Friday" stays the calendar's.
    No agent is named here - names arrive from the roster at call time."""
    tokens = tokenize(task.description)
    if not (tokens & DISCUSSION_WORDS):
        return False
    if _discussion_action(tokens) is not None:
        return True
    words = task.description.lower()
    opening = min(
        (m.start() for w in DISCUSSION_WORDS for m in [re.search(rf"\b{w}\b", words)] if m),
        default=None,
    )
    if opening is None:
        return False
    if _defers_to_scheduling(task.description, opening):
        return False
    if _names_after(task.description, opening, participants):
        return True
    # A discussion can also be asked of the room by addressing it - "<name> and
    # <name>, discuss whether ...". Two or more names in the opening address is
    # what makes it a discussion rather than a remark to one agent, which is
    # what keeps "<orchestrator>, let's discuss the budget" ordinary
    # conversation exactly as this function's docstring promises.
    if len(_vocative_names(task.description, participants)) >= 2:
        return True
    return bool(tokenize(words[opening:]) & ROOM_WORDS) or bool(_ROOM_PHRASE.search(words[opening:]))


_SUBJECT = re.compile(
    r"\b(?:discuss(?:ion)?|meeting|conference|brainstorm(?:ing)?|debate|deliberate|roundtable)\b"
    r"(?:\s+(?:about|on|regarding|over|of))?\s+(?P<subject>.+)$",
    re.IGNORECASE,
)


def _discussion_subject(text: str, names: Sequence[str]) -> str:
    """What is to be discussed: the words after the discussion word, without
    the list of who is to discuss it. Empty when nothing was named."""
    match = _SUBJECT.search(text)
    if match is None:
        return ""
    subject = match.group("subject")
    # Drop a trailing "with <names ...>" / "with everyone" clause.
    subject = re.split(r"\s+(?:with|between|among|amongst)\s+", subject, maxsplit=1, flags=re.IGNORECASE)[0]
    for name in names:
        subject = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s+(?:and|,)\s*$", "", subject.strip(" ,.!?"))
    return " ".join(subject.split()).strip(" ,.!?")


def discussion_request(request: CapabilityRequest) -> TaskResult:
    """Resolve a discussion request: which action, on what subject, with whom.

    Returns the resolution in the payload under ``DISCUSSION_KEY`` and opens,
    runs and closes nothing itself. Names are matched against the registered
    specialists (the orchestrator chairs every discussion it opens, so it is
    never listed); a name nobody is registered under is not invented. With no
    name, the discussion is with whoever is in the room."""
    text = request.task.description
    tokens = tokenize(text)
    action = _discussion_action(tokens)
    available = request.specialists()
    names = tuple(a.name for a in available)
    if action is not None:
        return TaskResult.success(
            request.task, request.own_name,
            {"continue": "Continuing the discussion.", "conclude": "Concluding the discussion.",
             "cancel": "Cancelling the discussion."}[action],
            payload={HANDLED_BY_KEY: "discussion", DISCUSSION_KEY: {"action": action}},
        )
    opening = min(
        (m.start() for w in DISCUSSION_WORDS for m in [re.search(rf"\b{w}\b", text.lower())] if m),
        default=0,
    )
    named = _names_after(text, opening, names)
    subject = _discussion_subject(text, names)
    if not subject:
        return TaskResult.failure(
            request.task, request.own_name,
            "What should we discuss? Name the subject - for example 'discuss the launch plan with everyone'.",
        )
    who = " and ".join(named) if named else "everyone in the room"
    return TaskResult.success(
        request.task, request.own_name,
        f"Let's discuss {subject} - with {who}.",
        payload={
            HANDLED_BY_KEY: "discussion",
            DISCUSSION_KEY: {"action": "open", "subject": subject, "with": list(named) or None},
        },
    )


DISCUSSION = ManagerCapability(
    authority="manager",
    authority_reason=(
        "opening a discussion decides who speaks with whom and in what order - "
        "orchestration, which a specialist holding it would turn into a second Manager"
    ),
    name="discussion",
    self_contained=True,
    self_contained_reason=(
        "a room act - who is discussing what with whom is the orchestrator's, never a participant's"
    ),
    # Deliberately NOT a room_act, though the two below are. ``room_act`` means
    # "changes who is in the room", and the orchestrator owns that whoever the
    # sentence was addressed to - "<name>, you can leave now" is the
    # orchestrator's act.
    # Opening a discussion is not a membership change, it is work, and Phase 10
    # settled that a specialist cannot open one: asked to, it answers for itself
    # and honestly declines (tests/test_phase_10_multi_agent.py::
    # TestADiscussionThroughTheRealSystem::test_a_specialist_cannot_open_one).
    # Flagging it here intercepted that request for the Manager and quietly
    # reversed the invariant.
    capabilities=frozenset({"discussion", "roundtable"}),
    handle=discussion_request,
    description="Opens, continues, concludes or cancels a discussion among the room's participants.",
    claims=discussion_intent,
    # Reachable only through its own shape check: "discuss" alone is ordinary
    # conversation and must stay open_conversation's.
    scoreable=False,
)


def _leave_shapes(name: str) -> "tuple[re.Pattern[str], ...]":
    """The other ordinary ways of excusing one named participant, each the
    whole message (Phase 11 closure sweep: only "dismiss/excuse <name>" was
    recognised, so "<name>, you can go" was answered by that agent as conversation
    and "<name>, you can leave the room now" scored as a live-news request).
    Anchored end to end, so "<name>, you can go ahead and check my inbox" is
    work for Arthur, never a dismissal."""
    n = re.escape(name)
    leave = r"(?:leave(?:\s+(?:the\s+)?(?:room|conversation|chat|discussion))?|go)"
    thanks = r"(?:(?:thanks?|thank\s+you|cheers)\s*,?\s*)?"
    tail = r"(?:\s+(?:now|for\s+now))?\s*[.!]*\s*$"
    return (
        re.compile(rf"^\s*{thanks}{n}\s*,?\s*{thanks}(?:you\s+)?(?:can|may|are\s+free\s+to)\s+(?:now\s+)?{leave}{tail}", re.IGNORECASE),
        re.compile(rf"^\s*{thanks}(?:you\s+)?(?:can|may)\s+(?:now\s+)?{leave}(?:\s+now)?\s*,\s*{n}\s*[.!]*\s*$", re.IGNORECASE),
        re.compile(rf"^\s*(?:please\s+)?(?:send|let)\s+{n}\s+(?:out|go){tail}", re.IGNORECASE),
    )


def _dismissed_name(text: str, names: Sequence[str]) -> str | None:
    """The registered name directly after "dismiss"/"excuse" - or the one a
    whole-message "<name>, you can go" / "send <name> out" excuses - or None."""
    match = _DISMISS.search(str(text or ""))
    if match is not None:
        rest = match.group("rest")
        named = _names_after(rest, 0, names)
        if named and rest.lower().startswith(named[0].lower()):
            return named[0]
    for name in sorted({" ".join(str(n).split()) for n in names if str(n).strip()}, key=len, reverse=True):
        if any(shape.match(str(text or "")) for shape in _leave_shapes(name)):
            return name
    return None


def dismissal_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """"dismiss <agent>", "excuse <agent>" - the verb directly followed by a
    registered name. Names arrive from the roster at call time."""
    return _dismissed_name(task.description, participants) is not None


def dismiss_participant(request: CapabilityRequest) -> TaskResult:
    """Work out which registered specialist a person is asking to remove from
    the room. Resolves and returns the name; removes nothing - the room's
    owner does. The orchestrator itself is never dismissable from here, being
    the one who answers in the room."""
    name = _dismissed_name(request.task.description, tuple(a.name for a in request.specialists()))
    named = (name,) if name else ()
    if not named:
        return TaskResult.success(
            request.task, request.own_name,
            "I have no specialist by that name in the room to excuse.",
            payload={HANDLED_BY_KEY: "participant_dismissal", DISMISSED_KEY: None},
        )
    return TaskResult.success(
        request.task, request.own_name,
        f"{named[0]} can leave the conversation.",
        payload={HANDLED_BY_KEY: "participant_dismissal", DISMISSED_KEY: named[0]},
    )


PARTICIPANT_DISMISSAL = ManagerCapability(
    authority="manager",
    authority_reason=(
        "deciding who stays in the room is the orchestrator's role, the mirror of "
        "invitation - a specialist able to remove its peers would be a second Manager"
    ),
    name="participant_dismissal",
    self_contained=True,
    self_contained_reason=(
        "a room act - only the orchestrator excuses a participant"
    ),
    room_act=True,
    capabilities=frozenset({"dismiss", "excuse"}),
    handle=dismiss_participant,
    description="Resolves which registered specialist a person is asking to remove from the room.",
    claims=dismissal_intent,
    scoreable=False,
)


# Bringing somebody into a conversation. Declared here because a capability
# word with no handler behind it is dropped, so these words mean something
# exactly as far as this function goes.
PARTICIPANT_INVITATION = ManagerCapability(
    authority='manager',
    authority_reason="coordinating the other agents is the orchestrator's role - a specialist that could list or invite its peers would be a second Manager",
    name="participant_invitation",
    self_contained=True,
    self_contained_reason=(
        "a room act - only the orchestrator brings a participant in"
    ),
    room_act=True,
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
#
# `report`/`reporting` (bare) removed - closure campaign, 2026-09-16. The
# identical failure mode `plan`/`coordinate` above already document, found
# live: "I have a task: finish the report.", "I need to finish the report."
# and even the unrelated life statement "I am reporting to my new manager
# next week" all scored highest for this capability on that one word alone
# and were answered with Alfred's own roster listing - a task about a
# document, or a fact about someone's job, mistaken for a request about
# Alfred himself. "status" alone still reaches this capability correctly
# ("status report please", "give me a status report" - both proven still
# routed here in this fix's own regression test), so nothing genuine was
# lost; only the bare, over-broad word was.
#
# Final ecosystem closure: word scoring alone never reached this for the
# plainest questions a person asks the orchestrator - "which agents do you
# have?", "list your specialists", "who's on your team?" all came back
# unanswered in Half Brain. Recognised here by shape, never by a bare word:
# each names agents or specialists, or asks after *his* team, so "the report"
# and "my team at work" stay what they are.
_ROSTER_QUESTION = re.compile(
    r"\b(?:which|what)\s+(?:other\s+)?(?:agents|specialists)"
    r"(?:\s+(?:do|does|are|is|have|has|can|could|exist|work|you)\b|\s*[?.!]|\s*$)"
    r"|\b(?:list|show|name)\s+(?:me\s+)?(?:all\s+(?:of\s+)?)?(?:the|your|all|every)\s+(?:other\s+)?(?:agents?|specialists?)\b"
    r"|\bhow\s+many\s+(?:agents|specialists)\b"
    r"|\bwho(?:'s|\s+is|\s+are)\s+(?:on|in)\s+your\s+team\b"
    r"|\bwho\s+(?:works\s+with\s+you|do\s+you\s+work\s+with)\b"
    # "What can you do?" asks after capabilities, and this capability is the
    # one that reports them (with the team, which the question's answer needs
    # anyway). ``identity_question`` also recognises the phrase, for the
    # introduction it renders elsewhere; ``asks_who_you_are`` already yields
    # to this predicate, so naming the shape here is the whole repair - found
    # by the Phase 11 closure sweep answering it with a biography that
    # mentioned no capability at all.
    r"|\bwhat\s+(?:can|do|could)\s+you\s+do\b"
    r"|\bwhat\s+are\s+you\s+(?:able\s+to\s+do|capable\s+of)\b"
    r"|\bwhat\s+can\s+you\s+help\s+(?:me\s+)?with\b",
    re.IGNORECASE,
)


def roster_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return bool(_ROSTER_QUESTION.search(str(task.description or "")))


ROSTER_REPORT = ManagerCapability(
    authority='manager',
    authority_reason="coordinating the other agents is the orchestrator's role - a specialist that could list or invite its peers would be a second Manager",
    name="roster_report",
    self_contained=True,
    self_contained_reason=(
        "who is registered is a fact about this system, not work in any domain"
    ),
    completes_request=True,
    anywhere=True,
    capabilities=frozenset(
        {
            "capabilities",
            "status",
            "roster",
            "summary",
        }
    ),
    handle=report_roster,
    description="Reports the Manager's own capabilities and its specialist roster.",
    claims=roster_intent,
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

    A question that also asks after the team ("what can you do, and who is
    on your team?") is the roster's to answer - it reports both - rather
    than an introduction that silently drops its second half (Phase 11
    closure sweep).
    """
    return identity_question(task.description) and not roster_intent(task)


SELF_DESCRIPTION = ManagerCapability(
    authority='every_agent',
    own_path="CognitiveAgent.conversational_reply - the agent's own identity",
    # Named to sort first among the claiming handlers, and deliberately: a
    # direct question about who you are is answered by identity, not by
    # whichever other handler also recognises a word in it. This is not a
    # priority list (see select_capability's own comment) - it is one name,
    # chosen so a total, repeatable order puts identity first for the one
    # kind of request that is unambiguously about identity.
    name="about_self",
    self_contained=True,
    self_contained_reason=(
        "the Manager describing itself; nobody else can answer for it"
    ),
    completes_request=True,
    anywhere=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.conversational_reply - the agent's own identity",
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
    self_contained=True,
    self_contained_reason=(
        "talking with the Manager is not work that could be handed to anyone"
    ),
    completes_request=True,
    anywhere=True,
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

    if capability.kind == "coding":
        # Post-Pre-Stage-9: code is written by reasoning (the Brain, else the
        # attributed local assistant) and saved through the tool as a real
        # code artifact - never by the tool itself.
        return _write_code(request, capability)

    # What to search for is the ask itself, never the name of whoever was
    # asked - see ``_asked`` for the runtime evidence.
    outcome = request.tools.invoke(capability.tool, {"query": _asked(request)})
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


_CODE_LANGUAGES = (
    "python", "javascript", "typescript", "html", "css", "sql", "bash", "powershell", "java", "c#", "csharp",
    "go", "rust", "json", "c++", "ruby", "php", "kotlin", "swift",
)
_FENCED_CODE = re.compile(r"```[ \t]*[\w#+.-]*[ \t]*\n(.*?)```", re.DOTALL)
_CODE_REQUEST_VERB = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?(?:help\s+me\s+)?"
    r"(?:write|create|build|generate|implement|fix|debug)\s+(?:me\s+)?",
    re.IGNORECASE,
)


def _code_language(text: str) -> str:
    """The language a coding request names - Python when it names none."""
    lowered = f" {str(text or '').lower()} "
    for language in _CODE_LANGUAGES:
        if re.search(rf"(?<![\w#+]){re.escape(language)}(?![\w#+])", lowered):
            return "c#" if language == "csharp" else language
    return "python"


def _code_title(text: str) -> str:
    words = _CODE_REQUEST_VERB.sub("", " ".join(str(text or "").split())).strip(" .?!")
    return (words[:1].upper() + words[1:])[:80] or "Code"


def _asked(request: CapabilityRequest) -> str:
    """What was asked for, with the address to whoever was asked removed.

    The distinction this draws is the one ``_strip_vocative`` already exists
    to draw, applied to *content* rather than to recognition. Conversation
    legitimately keeps the whole utterance - who is being spoken to is part
    of what was said, and ``open_conversation`` passes it to the Brain
    unchanged. But when a handler takes the request's own words and uses
    them as the *thing itself* - the query to search for, the prompt to
    write code from, the title to save it under - the name of the agent
    being asked is not part of that thing.

    Found at runtime, 2026-09-23, immediately after the identical defect was
    repaired in ``translation_request``: "Nova, write a python function that
    reverses a list" saved a real code artifact into the owner's coding
    workspace titled *"Nova, write a python function that reverses a list"*
    - ``_CODE_REQUEST_VERB`` is anchored, so with a name in front of it
    nothing was stripped at all - and handed the model a prompt that opened
    by addressing an agent the model is not. The same line reached
    ``web_search`` as the literal query "Nova, research ...". Alfred
    addressed by name was affected identically.
    """
    return _strip_vocative(request.task.description)


def _write_code(request: CapabilityRequest, capability: GeneralCapability) -> TaskResult:
    asked = _asked(request)
    language = _code_language(asked)
    written, brain = None, None
    if request.converse is not None:
        decision = request.converse(
            Task(description=(
                f"Write {language} code for this request. Reply with only the code in one fenced code block.\n\n"
                f"{asked}"
            )),
            tuple(_recent_conversation_notes(request)),
        )
        if decision.is_decided and decision.reason.strip():
            written, brain = decision.reason, decision.source
    if written is None and request.assist is not None:
        text = request.assist.write_code(asked, language=language)
        if text:
            written, brain = text, "local_assist"
    if written is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I can take on writing code, but nothing here can write it right now - no language model is "
            "available and the local assistant is off (in the Control Center: provider local_assist_provider "
            "use local). I won't guess at code.",
        )
    fenced = _FENCED_CODE.search(written)
    source = (fenced.group(1) if fenced else written).strip("\n")
    title = _code_title(asked)
    attribution = ""
    if brain == "local_assist":
        from src.providers.local_assistant import ATTRIBUTION

        attribution = f"\n\n({ATTRIBUTION})"
    block = f"```{language}\n{source}\n```"
    payload = {"handled_by": "general_assistance", "kind": "coding", "tool": capability.tool, "brain": brain, "language": language}
    outcome = request.tools.invoke(capability.tool, {"operation": "save", "content": json.dumps({
        "title": title, "language": language, "source": source, "request": asked,
    })})
    if not outcome.ok:
        return TaskResult.success(
            request.task, request.own_name,
            f"{block}\n\nI couldn't save it to your coding workspace: {outcome.error}{attribution}",
            payload={**payload, "saved": False},
        )
    saved = json.loads(outcome.output)
    return TaskResult.success(
        request.task, request.own_name,
        f"{block}\n\nSaved as '{saved['title']}' (artifact {saved['artifact_id']}, v{saved['version']}) "
        f"in your coding workspace.{attribution}",
        payload={**payload, "saved": True, "artifact_id": saved["artifact_id"]},
    )


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
    # Final ecosystem closure, the identical reasoning again, found live:
    # "create a workspace called garden planning" matches this function's
    # own "create" verb gate and workspace_navigation's own create shape at
    # once - alphabetical sorting would otherwise let this capability win
    # ("general_assistance" < "workspace_navigation") and answer with a
    # Brain's guess instead of the owner's governed route.
    if _asks_to_create_workspace(task.description or ""):
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
        assisted = _assisted_answer(request)
        if assisted is not None:
            return assisted
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
        # Phase 10: context another participant deliberately shared on this
        # task - attributed and marked shared, never anybody's memory.
        + shared_notes(request.task)
    )

    decision = request.converse(request.task, notes)
    if not decision.is_decided or not decision.reason.strip():
        assisted = _assisted_answer(request, context=notes)
        if assisted is not None:
            return assisted
        # Before refusing: an unclaimed question whose answer is written in
        # the person's own material is answered by quoting it. Every question
        # no other capability claimed arrives here, so this is where "I have
        # your timetable in front of me and still said I could not help" was
        # actually happening.
        quoted = quoted_from_own_material(
            request, evidence, f"no language model could answer ({decision.reason})",
            handled_by="open_conversation",
        )
        if quoted is not None:
            return quoted
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


def _assisted_answer(request: CapabilityRequest, *, context: "Sequence[str]" = ()) -> "TaskResult | None":
    """An open request answered by the local assistant when the Manager's own
    Brain could not answer - attributed plainly, so the words are never taken
    for the Manager's verified reasoning. None when no assistant is equipped or
    it produced nothing."""
    if request.assist is None:
        return None
    guided = guided_interaction_response(request)
    if guided is not None:
        return guided
    from src.providers.local_assistant import ATTRIBUTION

    text = request.assist.answer(request.task.description, context=tuple(str(n) for n in context)[-8:])
    if not text:
        return None
    return TaskResult.success(
        request.task, request.own_name, f"{text}\n\n({ATTRIBUTION})",
        payload={"handled_by": "open_conversation", "brain": "local_assist", "assisted": True},
    )


OPEN_CONVERSATION = ManagerCapability(
    authority='every_agent',
    own_path="CognitiveAgent.general_conversation - the agent's own Brain",
    name="open_conversation",
    anywhere=True,
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


# Closure campaign (2026-09-16, REQUIREMENT_LEDGER.md N-06): every
# _after_marker caller above only ever matches its trigger phrase
# immediately preceding the subject ("make a diagram of X", "make a
# handout about X"). "I need a diagram of a tank, can you make one?" -
# found live, not by reading code - states the same thing in the equally
# natural opposite order (subject first, confirmation-seeking ask
# second) and matched nothing, falling through to open_conversation
# silently instead of creating the artifact or even refusing honestly.
# The identical fallback src.services.memory.tasks._reordered_task_subject
# provides for task creation, generalised here to any noun set so
# create_diagram and create_artifact can share one implementation instead
# of two copies of the same regex.
def _reordered_subject(text: str, *, nouns: tuple[str, ...]) -> "str | None":
    """The subject of a reordered "I have/need/want a <noun> of/about X,
    can you <verb>?" or "I have/need/want a <noun>[:] X" request - or
    ``None`` when the text does not have this shape at all, so a caller
    always tries its ordinary marker-based extraction first and only
    reaches this as a fallback.

    ``nouns`` names the specific thing this capability makes ("diagram",
    "map", "handout"...) so an unrelated "I have a meeting at 3pm" can
    never be mistaken for one - the identical precision every marker set
    in this file already holds itself to, just order-free.
    """
    noun_group = "|".join(re.escape(n) for n in nouns)
    pattern = re.compile(
        rf"^(?:i\s+(?:have|need|want|would\s+love|would\s+like)|there'?s)\s+an?\s+(?:{noun_group})\b"
        r"(?:\s*:|\s+(?:to|for|about|of|showing|explaining))\s*(?P<subject>.+?)"
        r"(?:,\s*(?:can|could|would)\s+you\s+\w+.*)?$",
        re.IGNORECASE,
    )
    match = pattern.match(str(text or "").strip())
    if match is None:
        return None
    subject = match.group("subject").strip(" ,.:;!?\t\n")
    return subject or None


# Phase 11 final gate: a construction request is a verb, optional
# descriptors, and the capability's own noun - "design an interactive diagram
# of X", "draw me a labelled map showing X" - not one of a fixed list of
# exact phrases. The verbs are shared; the nouns are each capability's own
# vocabulary; nothing here names a subject. Declared beside
# ``_reordered_subject`` above, the other order-free shape helper, because
# several capabilities spread through this file read it (see
# ``_construction_subject``, ``document_generation_intent``,
# ``named_construction_request``).
_CONSTRUCTION_VERBS = (
    r"make|create|draw|build|design|prepare|produce|sketch|generate|render|illustrate"
    r"|give\s+me|put\s+together|come\s+up\s+with"
)


# "Forget my passport expiry", "please forget about my old address", "stop
# remembering my gym times", "delete what you know about my ex", "erase my
# dentist from your memory". Anchored at the start, so "I always forget my
# keys" and "don't forget to call mum" (a reminder) are never a request to
# forget anything.
_FORGET_REQUEST = re.compile(
    r"^(?:(?:ok(?:ay)?|and|also|actually|now)\s*,?\s*)?(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:forget|stop\s+remembering|delete|erase|remove)\s+"
    r"(?:(?:about|what\s+(?:you\s+know|i\s+(?:said|told\s+you))\s+about|everything\s+about)\s+)?"
    r"(?P<subject>(?:my|the|that|this)\b[^?!]*?)"
    r"(?:\s+from\s+(?:your\s+)?memory)?\s*[.!]*$",
    re.IGNORECASE,
)
_FORGET_NOT_MEMORY = re.compile(r"\b(?:task|reminder|file|document|artifact|recording|appointment|calendar|message|email)s?\b", re.IGNORECASE)


def _forget_subject(text: str) -> str:
    working = _strip_vocative(text).strip()
    match = _FORGET_REQUEST.match(working)
    if match is None or _FORGET_NOT_MEMORY.search(match.group("subject")):
        return ""
    return match.group("subject").strip()


def forget_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return _marker_present(task.description, FORGET_MARKERS) or bool(_forget_subject(task.description))


def _forget_canonical_topic(request: CapabilityRequest, subject: str) -> "TaskResult | None":
    try:
        items = [item for item in all_current_personal_context(recall=request.recall, limit=200)]
    except Exception as exc:  # noqa: BLE001 - an unreadable store falls back to the plain path
        logger.warning("could not read personal context to forget: %s", exc)
        return None
    wanted = _meaningful_stems(subject)
    if not wanted or not items:
        return None
    scored = sorted(
        ((len(wanted & _meaningful_stems(f"{item.topic} {item.content}")), item) for item in items),
        key=lambda pair: -pair[0],
    )
    best = scored[0][0]
    if best == 0:
        return None
    # Every topic the words name best - never one that merely shares a
    # weaker overlap - forgotten with its whole history.
    top = [item for score, item in scored if score == best][:5]
    ids: "list[str]" = []
    for item in top:
        for record in (*personal_context_history(item.topic, recall=request.recall), item):
            if record.record_id not in ids:
                ids.append(record.record_id)
    removed = sum(1 for record_id in ids if request.forget(record_id))
    if removed < len(ids):
        return TaskResult.failure(
            request.task, request.own_name, f"Only {removed} of {len(ids)} record(s) could be forgotten.",
        )
    said = "; ".join(describe_for_user(item).rstrip(".") for item in top)
    return TaskResult.success(
        request.task, request.own_name,
        f"Forgotten ({removed} record(s)): {said}.",
        payload={"forgotten": removed},
    )


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

    topic = _after_marker(request.task.description, FORGET_MARKERS) or _forget_subject(request.task.description)
    if not topic:
        return TaskResult.failure(
            request.task, request.own_name,
            "Forget what, exactly? Try 'forget that ...'.",
        )

    # The canonical view first: the one topic the words name best is
    # forgotten entirely - its current statement and its history - and
    # nothing that merely shares a common word with it. Two equally good
    # matches are asked about, never guessed between.
    canonical = _forget_canonical_topic(request, topic)
    if canonical is not None:
        return canonical

    # Asked with the topic's distinctive words only (memory/query.py: the
    # caller decides what to ask about). Phase 11 closure: the raw topic
    # matched any record sharing "the", and every match is forgotten.
    query = distinctive_terms(topic)
    matches = _direct_recall(request, query, limit=5) if query else ()
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

    # Phase 11 closure: the raw topic matched any record sharing "the" - "what
    # do you remember about the boiler?" answered with a note about a kiln.
    query = distinctive_terms(topic)
    records = _direct_recall(request, query, limit=5) if query else ()
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
    authority='every_agent',
    name="forget",
    self_contained=True,
    self_contained_reason=(
        "removes from this Manager's own memory store"
    ),
    completes_request=True,
    anywhere=True,
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
    authority='every_agent',
    name="recall_memory",
    self_contained=True,
    self_contained_reason=(
        "reads this Manager's own memory store"
    ),
    completes_request=True,
    anywhere=True,
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
    """Write the corrected statement under the existing topic's own key. The
    statement that was wrong is kept as history, never deleted: the newest
    statement for a key is the current truth, and the old one stays
    answerable as what was said before (marked by ``corrects``)."""
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


# Kinds of value a correction can swap for another of the same kind, without
# knowing anything about the subject: a weekday for a weekday, a clock time
# for a clock time. Grammar only - there is no list of topics here, and none
# may be added.
_VALUE_KINDS: "tuple[tuple[str, re.Pattern[str]], ...]" = (
    ("weekday", re.compile(
        r"\b(?:" + "|".join(sorted(CALENDAR_WEEKDAY_NAMES, key=len, reverse=True)) + r")s?\b",
        re.IGNORECASE)),
    ("time", re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", re.IGNORECASE)),
)


def _like_for_like_correction(content: str, replacement: str) -> "str | None":
    """``content`` with its value of the same kind as ``replacement`` swapped
    for it - or None when the two are not the same kind of value, in which
    case the caller's ordinary whole-topic replacement is right.
    """
    said = str(replacement or "").strip()
    body = str(content or "")
    if not said or not body:
        return None
    for _kind, pattern in _VALUE_KINDS:
        in_replacement = pattern.fullmatch(said.strip(" .!?"))
        if in_replacement is None:
            continue
        if pattern.search(body) is None:
            return None
        swapped = pattern.sub(said.strip(" .!?"), body, count=1)
        return swapped if swapped != body else None
    return None


def _swap_named_value(content: str, old_value: str, replacement: str) -> str:
    """``content`` with the named wrong value replaced by the new one, once.
    When the replacement opens with a word already standing before the old
    value ("on Thursdays" for "... on Tuesdays"), only the rest is swapped in."""
    body = str(content or "")
    old = " ".join(str(old_value or "").split())
    new = " ".join(str(replacement or "").split()).strip(" .!?")
    if not body or not old or not new:
        return body
    lead, _sep, rest = new.partition(" ")
    if rest and re.search(rf"\b{re.escape(lead)}\s+{re.escape(old)}\b", body, re.IGNORECASE):
        new = rest
    return re.sub(rf"\b{re.escape(old)}\b", lambda _m: new, body, count=1, flags=re.IGNORECASE)


def _narrowed_topic(topic: str, replacement: str) -> "str | None":
    """The stored topic with the same like-for-like swap applied, so the topic
    key follows the statement rather than drifting from it."""
    return _like_for_like_correction(topic, replacement)


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

    # "Actually, it's on Thursdays, not Tuesdays": the wrong value was named,
    # and it is in the stored statement - so exactly that value is swapped and
    # everything else said stays ("learning Spanish on Tuesdays" becomes
    # "learning Spanish on Thursdays", never "learning on Thursdays"). A
    # leading word the replacement shares with the statement ("on") is not
    # doubled.
    # A free note ("remember that I am working on Project Beacon") corrected
    # stays a note under the same topic - the replacement as said, or the
    # named wrong value swapped - never "the user's <whole sentence> is ...".
    if getattr(source, "category", "") == "note":
        if statement.old_value and _mentions(source.content, statement.old_value):
            content = _swap_named_value(source.content, statement.old_value, replacement)
        else:
            restated = extract_remembered_note(replacement)
            content = restated.content if restated is not None else replacement
        corrected = PersonalContextCandidate(topic=source.topic, content=content, category="note")
        return _replace_canonical(
            request, source, corrected, reply=f"Got it - I've corrected that. {describe_for_user(corrected)}",
            payload={**payload, "action": "corrected"},
        )

    if statement.old_value and _mentions(source.content, statement.old_value):
        swapped = _swap_named_value(source.content, statement.old_value, replacement)
        if swapped != source.content:
            corrected = PersonalContextCandidate(
                topic=source.topic, content=swapped, category=source.category,
                value=_swap_named_value(source.value, statement.old_value, replacement) if source.value else "",
                activity=source.activity,
            )
            return _replace_canonical(
                request, source, corrected, reply=f"Got it - I've corrected that. {describe_for_user(corrected)}",
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
    # to a new topic: the corrected subject takes the old one's place - unless
    # the correction is plainly about one *part* of it. "I'm learning Spanish
    # on Tuesdays" corrected with "it is Thursdays" is about the day, and
    # replacing the whole topic dropped the subject entirely, leaving "the
    # user is learning Thursdays". Only a like-for-like swap is made (a
    # weekday for a weekday, a time for a time), which is grammar, not a
    # guess about the subject.
    narrowed = _like_for_like_correction(source.content, replacement)
    if narrowed is not None:
        # The topic key stays the statement's own, so the corrected statement
        # supersedes the wrong one and the wrong one is kept as history.
        corrected = PersonalContextCandidate(
            topic=source.topic, content=narrowed, category=source.category, activity=source.activity,
        )
        record = build_personal_context_record(corrected, agent_name=request.own_name)
        record = MemoryRecord(
            content=record.content, kind=record.kind, tags=record.tags,
            context={**dict(record.context or {}), "corrects": source.record_id},
        )
        verdict = request.retain(record)
        if not verdict.allowed:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't update that: {verdict.reason}")
        return TaskResult.success(
            request.task, request.own_name, f"Got it - I've corrected that. {describe_for_user(corrected)}",
            payload={**payload, "action": "corrected"},
        )
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
        # A keyed fact is rewritten as one; a plain "remember that" note (no
        # value - its topic is the whole note) is corrected as the note below.
        # Phase 11 closure: a note went through the fact template and was
        # stored as "The user's the kiln shelf is cracked is repaired."
        if existing is not None and existing.value:
            candidate = PersonalContextCandidate(
                topic=existing.topic, content=f"The user's {existing.topic} is {new_value}.",
                category=existing.category, value=new_value,
            )
            return _replace_canonical(
                request, existing, candidate, reply=f"Updated what I remember about {topic}.",
                payload={"handled_by": "correct_memory", "action": "updated", "topic": topic},
            )
        # Phase 11 closure: the raw topic matched any note sharing "the", and
        # the match is replaced - "update what you remembered about the boiler"
        # deleted a note about a kiln shelf.
        query = distinctive_terms(topic)
        matches = _direct_recall(request, query, kind=MemoryCategory.USER_CONTEXT.value, limit=1) if query else ()
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
        target = _direct_recall(request, kind=MemoryCategory.USER_CONTEXT.value, limit=1)
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
    authority='manager',
    authority_reason='writes the one canonical record of the person, which only the Manager writes - every specialist reads it (personal_context_reply) and keeps its own memory',
    name="correct_memory",
    self_contained=True,
    self_contained_reason=(
        "corrects this Manager's own memory store"
    ),
    completes_request=True,
    anywhere=True,
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

    # What to look up is the ask itself, never the name of whoever was
    # asked - the identical distinction ``_asked`` above draws, and the one
    # ``media_production`` below already drew for its own operation
    # detection while still handing the raw utterance to the tool.
    outcome = request.tools.invoke("live_information", {"query": _asked(request)})
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
    self_contained=True,
    self_contained_reason=(
        "a deterministic read from the one source this Manager is configured with"
    ),
    capabilities=CURRENT_INFORMATION_WORDS,
    handle=live_information,
    description=(
        "Acquires current, external information through a governed tool, "
        "then reasons over it as evidence - never a subject-specific word list."
    ),
    # The marker-phrase signal above, checked first - the same "claims
    # before scoring" precedence forget/recall_memory/participant_invitation
    # already rely on, for the identical reason: a request's shape can
    # settle what word-overlap scoring alone cannot. Content already in play
    # ("read my latest recording to me") is never a question about the world
    # right now, however "latest" it is (found at runtime 2026-09-21).
    claims=lambda task, participants=(): (
        current_information_intent(task, participants) and not content_reference_intent(task.description)
    ),
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


_DOCUMENT_GENERATION_NOUNS = ("report", "document", "write-up", "writeup")

# "as a document", "as a report" - the same ask with the noun at the end
# ("save your findings as a document"), which no forward-ordered verb+noun
# pattern can reach.
_DOCUMENT_GENERATION_AS = re.compile(
    rf"\b(?:{_CONSTRUCTION_VERBS}|write|write\s+up|save|turn|export)\b[^.!?]*?"
    rf"\bas\s+(?:an?\s+)?(?:{'|'.join(_DOCUMENT_GENERATION_NOUNS)})\b",
    re.IGNORECASE,
)


def document_generation_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    if _marker_present(task.description, DOCUMENT_GENERATION_MARKERS):
        return True
    # The shared construction shape - a verb, optional descriptors, and this
    # capability's own noun - rather than only the exact phrases above. The
    # identical generalization ``create_diagram_intent`` already carries, and
    # for the identical reason its own comment gives: the phrase list missed
    # natural asks ("write up a report of your findings as a document",
    # "save your findings as a document") that name this capability plainly.
    if _construction_subject(task.description, nouns=_DOCUMENT_GENERATION_NOUNS) is not None:
        return True
    return bool(_DOCUMENT_GENERATION_AS.search(str(task.description or "")))


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
#
# Phase 11 closure (this pass): the medium became data. This capability
# answers one question - "what can be seen in this" - and a still picture
# and a moving one are two shapes of the same question, not two features.
# Until now only a still could be looked at: a request about a *video*
# reached no visual reasoning at all ("what do you see in this video?"
# fell through to open_conversation, and "what is happening in this
# video?" was claimed by live_information, an unrelated capability).
# Nothing below knows what is *in* the picture - no subject, no species,
# no scene category - and nothing below is video-specific: a moving
# medium is sampled into stills through one generalized primitive
# (``sample_frames``) and then read by the identical vision path a still
# already went through, so a medium this list has never heard of becomes
# one entry of data rather than a second handler.
#
# The capability keeps the name ``image_analysis`` deliberately. It is a
# persisted governance key (``capabilities enable/disable <name>``, the
# evolution ledger's own component ids), so renaming it would be a store
# migration for a label - the name is historical, the contract is
# "visual understanding".
# ---------------------------------------------------------------------------

# What a person calls the thing they want looked at. Two groups because the
# *medium* genuinely differs - a still is looked at once, a moving one is
# looked at across time - never because the subject does.
_STILL_VISUAL_NOUNS: tuple[str, ...] = ("image", "picture", "photo", "photograph", "screenshot")
_MOVING_VISUAL_NOUNS: tuple[str, ...] = ("video", "clip", "footage", "animation")
_VISUAL_NOUNS: tuple[str, ...] = _STILL_VISUAL_NOUNS + _MOVING_VISUAL_NOUNS

# How the ask is phrased. Generated against the nouns above rather than
# hand-written, so a new medium word is one entry, never eleven sentences -
# and so the phrasings a hand-written list happened to miss ("what is in
# this picture", "look at the photo") are covered for every noun equally.
_VISUAL_ASK_SHAPES: tuple[str, ...] = (
    "look at {d} {n}", "what do you see in {d} {n}", "what is in {d} {n}",
    "what's in {d} {n}", "analyze {d} {n}", "describe {d} {n}",
    # A moving medium's own natural ask. Harmless for a still (nobody says
    # "what is happening in this photograph" and means something else), and
    # it is the phrasing that was being mistaken for a request about the
    # live world.
    "what is happening in {d} {n}", "what's happening in {d} {n}",
    "what is going on in {d} {n}", "what's going on in {d} {n}",
)

_VISUAL_DETERMINERS: tuple[str, ...] = ("this", "that", "the")

IMAGE_ANALYSIS_MARKERS: tuple[str, ...] = tuple(
    shape.format(d=determiner, n=noun)
    for noun in _VISUAL_NOUNS
    for determiner in _VISUAL_DETERMINERS
    for shape in _VISUAL_ASK_SHAPES
)

_IMAGE_PATH_PATTERN = re.compile(r"\S+\.(?:png|jpg|jpeg|gif|webp)", re.IGNORECASE)

# Any audio/video path a request may have named - the same shape
# ``media_production.py``'s own ``_MEDIA_PATH_PATTERN`` matches, written here
# rather than imported so this module keeps its existing import boundary
# (capabilities.py imports that module's *functions* at call time, never at
# module level). Used only to keep a path out of what a vision model is asked.
_MEDIA_PATH_IN_PROMPT = re.compile(
    r"\S+\.(?:mp4|mov|mkv|webm|avi|wav|mp3|m4a|ogg|oga|opus|flac|aac)", re.IGNORECASE
)


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


# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 8 (IMG-03): whether an image may be edited or generated.
#
# "Was this photo edited?", "is this picture fake?", "check if this image is AI
# generated". Answered from what the file itself shows - metadata, editor and
# generator traces, Content Credentials, JPEG error-level analysis - through a
# local examination with no model, so it works identically in Half Brain. Never
# a verdict: the reply says plainly that certainty needs a trained forensic
# model this system does not have.
# ---------------------------------------------------------------------------

_AUTHENTICITY_WORDS = re.compile(
    r"\b(?:edited|photoshopped|manipulated|doctored|altered|tampered|retouched|faked?|genuine|authentic|"
    r"ai[-\s]?generated|generated\s+by\s+ai|deepfake|real\s+or\s+fake|been\s+changed)\b",
    re.IGNORECASE,
)
_AUTHENTICITY_SUBJECT = re.compile(r"\b(?:image|photo|picture|pic|screenshot|selfie|this|it)\b", re.IGNORECASE)
_AUTHENTICITY_ASK = re.compile(r"\?|\b(?:is|was|has|have|did|does|check|tell\s+me|detect|find\s+out|can\s+you\s+see)\b", re.IGNORECASE)
_NOT_AN_IMAGE_WORDS = re.compile(r"\b(?:document|pdf|doc|docx|file\s+name|text|essay|spreadsheet|video|recording)\b", re.IGNORECASE)


def image_authenticity_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    if _NOT_AN_IMAGE_WORDS.search(text) and not re.search(r"\b(?:image|photo|picture|pic|screenshot|selfie)\b", text, re.IGNORECASE):
        return False
    # "is artifact 9078bdc2ac89 edited": the subject is named exactly rather
    # than with a pointing word, which the subject pattern alone never matched.
    subject = _AUTHENTICITY_SUBJECT.search(text) or ARTIFACT_ID_PATTERN.search(text) or _IMAGE_PATH_PATTERN.search(text)
    return bool(_AUTHENTICITY_WORDS.search(text) and subject and _AUTHENTICITY_ASK.search(text))


def image_authenticity(request: CapabilityRequest) -> TaskResult:
    """Examine the named or shared image and report the evidence."""
    from pathlib import Path

    from src.providers.media.image_forensics import ImageForensicsError, examine_image

    described = None
    match = _IMAGE_PATH_PATTERN.search(request.task.description)
    if match is not None:
        image_path = Path(match.group(0))
    else:
        described, shared_path = _shared_media(request, ("image",))
        if shared_path is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "Which image? Share it in this conversation (or name it with 'artifact <id>') and I'll "
                "check what it shows about being edited or generated.",
            )
        image_path = Path(shared_path)
    if not image_path.is_file():
        return TaskResult.failure(request.task, request.own_name, f"I couldn't find an image at '{image_path}'.")
    try:
        evidence = examine_image(image_path)
    except ImageForensicsError as exc:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't examine that image: {exc}")
    if described and described.get("title"):
        # The file as the person shared it, never its stored rendition's name.
        evidence = dataclass_replace(evidence, filename=str(described["title"]))
    return TaskResult.success(
        request.task, request.own_name, evidence.describe(),
        payload={
            "handled_by": "image_authenticity",
            "editors_named": evidence.editors_named,
            "generators_named": evidence.generators_named,
            "content_credentials": evidence.content_credentials,
            "uneven_recompression": evidence.uneven_recompression,
        },
    )


IMAGE_AUTHENTICITY = ManagerCapability(
    name="image_authenticity",
    completes_request=True,
    capabilities=frozenset({"image", "authenticity"}),
    handle=image_authenticity,
    description=(
        "Reports what an image itself shows about being edited or AI-generated - metadata, editor and "
        "generator traces, Content Credentials, JPEG error-level analysis - as evidence, never a verdict."
    ),
    claims=image_authenticity_intent,
    scoreable=False,
)


# The same nouns the marker table is generated from - one list, so the
# reordered shape ("I have a clip I want you to look at, can you describe
# it?") and the plain one recognise exactly the same media.
_IMAGE_ANALYSIS_NOUNS = _VISUAL_NOUNS
# A real word/intent collision, found live (2026-09-16) while extending the
# identical reordered mechanism to media_production: "I would love an image
# of a cat, can you make one?" and "I want a look at this picture, can you
# describe it?" both name "image"/"picture" as their noun, so the noun
# alone cannot tell a generation request from an analysis request apart -
# and image_analysis (alphabetically before media_production) silently won
# every such collision (N-07's tie-break mechanism), including the ones
# that were clearly asking to *create* something. The confirmation
# clause's own verb is what actually distinguishes them, so this now
# requires an analysis-shaped verb there and explicitly excludes
# creation-shaped ones - never both capabilities trusting the noun alone.
_IMAGE_ANALYSIS_REORDERED_VERB = re.compile(
    r"\b(?:describe|look|see|tell|show|analyz[es]|explain)\b", re.IGNORECASE
)
_IMAGE_CREATION_VERB = re.compile(r"\b(?:make|generate|create|produce|draw)\b", re.IGNORECASE)

# A moving medium named anywhere in the sentence, in any order relative to
# the ask - the identical order-free verb+noun shape
# ``_CLIP_VIDEO_VERB_NOUN_PATTERN`` already established for clipping, and
# for the same reason: requiring the noun to sit immediately after the verb
# left the equally natural "tell me what is going on in this video" claimed
# by nothing, and "say what it is doing in the footage" claimed by
# `translate`. Built from the noun table above, so a moving medium that
# does not exist yet is one entry of data.
#
# Deliberately moving-only. A still picture already has a mature marker
# table and the reordered mechanism; broadening the same order-free rule to
# "image"/"picture" would make this capability claim requests that are
# about sending or showing a file rather than about what is in it.
_MOVING_MEDIUM_REFERENCE = re.compile(
    r"\b(?:this|that|the|my|these|those)\s+(?:\w+\s+){0,2}?(?:"
    + "|".join(_MOVING_VISUAL_NOUNS)
    + r")s?\b",
    re.IGNORECASE,
)


def _asks_what_can_be_seen_in_a_moving_medium(text: str) -> bool:
    """Whether ``text`` asks what is in a moving medium, whatever order the
    words come in - an observation-shaped ask (or a request for Alfred to
    speak for what is in it), a moving medium named, and no creation verb
    that would make this a request to *make* one instead."""
    words = str(text or "")
    if _MOVING_MEDIUM_REFERENCE.search(words) is None:
        return False
    if _IMAGE_CREATION_VERB.search(words):
        return False
    return bool(
        _IMAGE_ANALYSIS_REORDERED_VERB.search(words) or _SPEAK_FOR_SUBJECT.search(words)
    )


def image_analysis_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    # GM-03: "tell me how many ... are in this video" asks to find and count,
    # not to describe - perceive_media's own shape, whose name sorts later.
    if perceive_media_intent(task, participants):
        return False
    if _marker_present(task.description, IMAGE_ANALYSIS_MARKERS):
        return True
    # Phase 11 closure (this pass): the same ask about a moving medium, in
    # any word order. Without it "tell me what the dog is doing in this
    # video" reached ordinary conversation - where a Full Brain answers
    # about a recording it never looked at - and "say what it is doing in
    # the footage" was claimed by `translate`.
    if _asks_what_can_be_seen_in_a_moving_medium(task.description):
        return True
    # Closure campaign (2026-09-16, N-06): "I have a photo I want you to
    # look at, can you tell me what it shows?" states the identical ask
    # in the equally natural opposite order and matched nothing - the
    # same shared mechanism WS-02/ORG-01/create_artifact already use.
    # No extraction concern here (unlike those): the handler already
    # falls back to the raw text when it is not an exact marker match
    # (see the helper just above IMAGE_ANALYSIS's own registration), so
    # only the intent side needed broadening.
    if _reordered_subject(task.description, nouns=_IMAGE_ANALYSIS_NOUNS) is None:
        return False
    if _IMAGE_CREATION_VERB.search(task.description):
        return False
    return bool(_IMAGE_ANALYSIS_REORDERED_VERB.search(task.description))


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

    **The medium is data.** A still picture is looked at once; a moving one
    is sampled into real stills and looked at across time
    (``_moving_visual_analysis``). Both go through the identical vision
    above - one capability answering "what can be seen in this", never a
    second handler because the file moves. Which one a request is about is
    the artifact store's own recorded kind, or the file itself, never the
    request's wording.
    """
    local_vision = getattr(request.assist, "describe_image", None) if request.assist is not None else None
    if request.analyze_image is None and local_vision is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to reason about what I am shown yet - no "
            "vision-capable reasoning is configured for me right now, so I "
            "would rather say so than guess.",
        )

    from pathlib import Path

    from src.agents.tools.media_production import find_video_path

    moving = False
    match = _IMAGE_PATH_PATTERN.search(request.task.description)
    named_video = find_video_path(request.task.description)
    if match is not None:
        image_path = Path(match.group(0))
    elif named_video is not None:
        # A moving medium named by path. `find_video_path` is the project's
        # one shared answer to "is this a moving-image file" (the identical
        # helper clip_video_intent already reads) - never a second list of
        # container extensions kept in sync by hand.
        image_path = Path(named_video)
        moving = True
    else:
        # Pre-Stage-9 Campaign 3: the medium the person shared in this
        # conversation, resolved to its stored file - no server path needed.
        # Both visual kinds are asked for: whether the newest shared file
        # moves is the artifact store's own recorded fact, read from it,
        # never guessed from the request's words.
        described, shared_path = _shared_media(request, ("image", "video"))
        if shared_path is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't see an image or video in that request or shared in this "
                "conversation - attach one, or name it with 'artifact <id>'.",
            )
        image_path = Path(shared_path)
        moving = _is_moving_medium(described, shared_path)
    if not image_path.is_file():
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't find a {'video' if moving else 'image'} at '{image_path}'.",
        )

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't read that {'video' if moving else 'image'}: {exc}",
        )

    if moving:
        return _moving_visual_analysis(request, image_bytes)

    decision = request.analyze_image(image_bytes, request.task.description) if request.analyze_image is not None else None
    if decision is not None and decision.is_decided and decision.reason.strip():
        return TaskResult.success(
            request.task, request.own_name, decision.reason,
            payload={"handled_by": "image_analysis", "brain": decision.source},
        )

    # Post-Pre-Stage-9: the Brain has no view (Half Brain, or a vendor with no
    # vision), so the attributed local vision model looks - its words are never
    # presented as the Manager's verified reasoning.
    if local_vision is not None:
        from src.providers.local_assistant import VISION_ATTRIBUTION

        seen = local_vision(image_bytes, _vision_prompt(request.task.description))
        if seen:
            return TaskResult.success(
                request.task, request.own_name, f"{seen}\n\n({VISION_ATTRIBUTION})",
                payload={"handled_by": "image_analysis", "brain": "local_vision", "assisted": True},
            )
    reason = decision.reason if decision is not None else "the local vision model produced no description"
    return TaskResult.failure(
        request.task, request.own_name,
        f"I couldn't analyze that image: {reason}",
    )


# How many moments of a moving medium are looked at. Each look is one real
# call to whatever vision the installation has, so this is a genuine cost,
# not a free loop: a small local vision model on an ordinary laptop takes
# minutes per frame. Three is enough for "what is going on here" to be
# answerable across time rather than from a single instant, and few enough
# that the answer arrives. Not a constant a caller has to accept - a
# deployment with faster vision raises it.
MOVING_VISUAL_SAMPLE_COUNT = 3

# What is said about an account built from sampled moments, every time. It
# is not a hedge: a description of three instants out of a recording is
# genuinely a description of three instants, and saying so is the
# difference between evidence and a claim about the whole.
_SAMPLED_LOOK_NOTE = (
    "This is what is visible at the moments I looked at, not a frame-by-frame "
    "account of the whole recording."
)

# When a person asks Alfred to speak *for* what is in the medium ("voice out
# what it is doing", "say what he is thinking", "what is it telling us") the
# honest answer is still only what can be seen. Naming the subject, its
# intent or its words as fact would be inventing them. Nothing here is about
# any particular kind of subject - a person, an animal, a machine, a crowd
# all get the identical treatment.
_SPEAK_FOR_SUBJECT = re.compile(
    r"\b(?:voice(?:\s+(?:out|over))?|narrate|speak\s+for|say\s+what|tell\s+me\s+what\s+(?:it|he|she|they)"
    r"|in\s+(?:its|his|her|their)\s+(?:own\s+)?words|what\s+(?:is|are|'s)\s+(?:it|he|she|they)\s+"
    r"(?:saying|thinking|feeling))\b",
    re.IGNORECASE,
)

_INTERPRETIVE_NOTE = (
    "That is my reading of what is visible - not words or intentions the "
    "subject actually expressed."
)


def _is_moving_medium(described: "dict | None", path: str) -> bool:
    """Whether the resolved medium is a moving one.

    The artifact store's own recorded kind is the authority - a file is a
    video because it was registered as one, never because of how a person
    worded the request. The path is only consulted when nothing was
    recorded (a medium resolved straight from a filesystem path), through
    the identical shared helper the rest of this project already uses.
    """
    from src.agents.tools.media_production import find_video_path

    for key in ("content_kind", "type"):
        value = str((described or {}).get(key, "") or "").strip().lower()
        if value in _MOVING_MEDIUM_KINDS:
            return True
        if value in _STILL_MEDIUM_KINDS:
            return False
    return find_video_path(str(path)) is not None


# What the artifact store calls a medium that moves, and one that does not.
# Read from the store's own recorded kinds rather than inferred: "video" and
# "video_recording" are what registration already writes (see
# src/services/artifacts/*), and a kind this list has never heard of is
# decided by the file itself rather than assumed either way.
_MOVING_MEDIUM_KINDS = frozenset({"video", "video_recording"})
_STILL_MEDIUM_KINDS = frozenset({"image", "diagram", "screenshot"})


def _look_once(request: CapabilityRequest, image_bytes: bytes, prompt: str) -> "tuple[str, str] | None":
    """One real look at one still picture - ``(what was seen, who saw it)``,
    or None when no vision could answer.

    The identical two-step every single-image look already takes: the
    Manager's own Brain first (real, attributed reasoning), then the local
    vision model beside it (attributed as such). Factored out here, not
    reimplemented, so a moving medium is read by exactly the same vision a
    still one is - the whole point of treating the medium as data.
    """
    decision = request.analyze_image(image_bytes, prompt) if request.analyze_image is not None else None
    if decision is not None and decision.is_decided and decision.reason.strip():
        return decision.reason.strip(), decision.source
    local_vision = getattr(request.assist, "describe_image", None) if request.assist is not None else None
    if local_vision is not None:
        seen = local_vision(image_bytes, prompt)
        if seen:
            return str(seen).strip(), "local_vision"
    return None


def _moving_visual_analysis(request: CapabilityRequest, video_bytes: bytes) -> TaskResult:
    """What can be seen in a moving medium, read across time.

    Sampled into stills by one generalized primitive and then looked at by
    the identical vision path a single picture already goes through - never
    a second vision provider, never a second kind of reasoning, and nothing
    here that knows what the subject is. A medium with no visual content at
    all is reported as exactly that, never described from its filename.
    """
    from src.providers.media.video import VideoError, sample_frames

    try:
        frames = sample_frames(video_bytes, count=MOVING_VISUAL_SAMPLE_COUNT)
    except VideoError as exc:
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't get a look at that - {exc}.",
        )
    except Exception as exc:  # noqa: BLE001 - a decoder failure is reported, never guessed around
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't get a look at that: {type(exc).__name__}: {exc}",
        )

    prompt = _vision_prompt(request.task.description)
    looks: "list[tuple[float, str]]" = []
    sources: "list[str]" = []
    for frame in frames:
        seen = _look_once(request, frame.image_bytes, prompt)
        if seen is None:
            continue
        looks.append((frame.at_seconds, seen[0]))
        sources.append(seen[1])

    if not looks:
        return TaskResult.failure(
            request.task, request.own_name,
            "I got real frames out of that, but nothing here could tell me what is in "
            "them - no vision-capable reasoning is configured, so I would rather say "
            "so than guess.",
        )

    lines = [f"At {seconds:g}s: {text}" for seconds, text in looks]
    lines.append("")
    lines.append(_SAMPLED_LOOK_NOTE)
    if _SPEAK_FOR_SUBJECT.search(request.task.description or ""):
        lines.append(_INTERPRETIVE_NOTE)
    if "local_vision" in sources:
        from src.providers.local_assistant import VISION_ATTRIBUTION

        lines.append(f"({VISION_ATTRIBUTION})")

    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={
            "handled_by": "image_analysis",
            "medium": "moving",
            "frames_looked_at": len(looks),
            "sampled_at_seconds": tuple(seconds for seconds, _ in looks),
            "brain": sources[0] if sources else "",
            "assisted": "local_vision" in sources,
        },
    )


_DEFAULT_VISION_PROMPT = (
    "Describe this image in detail: the main subject, the setting, colours and any visible text."
)


def _vision_prompt(request_text: str) -> str:
    """What the small vision model is asked - the person's own question when it
    is one, otherwise a plain request to describe the image.

    A filesystem path the request happened to name is removed first. The model
    is being handed the picture itself; the path is how *this* process found
    it, is meaningless to a vision model, and was measurably reaching one -
    "voice out what the subject is doing in this video: C:\\...\\subject.mp4"
    was asked verbatim, path and all (found in the live run of this pass).
    """
    text = " ".join(str(request_text or "").split())
    for pattern in (_IMAGE_PATH_PATTERN, _MEDIA_PATH_IN_PROMPT):
        text = " ".join(pattern.sub(" ", text).split())
    # A request that was nothing but a path, or a bare verb with the path
    # removed ("describe <path>" -> "describe"), carries no question worth
    # asking - the plain default is a better question than a fragment.
    text = text.rstrip(" :,-")
    if len(text.split()) < 3:
        text = ""
    for marker in IMAGE_ANALYSIS_MARKERS:
        if text.lower().rstrip("?.! ") == marker:
            return _DEFAULT_VISION_PROMPT
    return text or _DEFAULT_VISION_PROMPT


IMAGE_ANALYSIS = ManagerCapability(
    name="image_analysis",
    capabilities=frozenset({"image_analysis", "vision"}),
    handle=image_analysis,
    description=(
        "Says what can be seen in a real still or moving image, with genuine "
        "visual reasoning, when the configured provider supports it - a moving "
        "one read across real sampled moments. Never OCR, never metadata, "
        "never a fabricated description."
    ),
    claims=image_analysis_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# GM-03: structured perception of a shared still or recording - how many,
# where each went, when each arrived, stopped or left. A different question
# from image_analysis's "what can be seen in this": that one describes, this
# one finds, follows and counts, through the perception provider axis
# (``runtime.perception``), in Half Brain as in Full Brain.
#
# Recognised by the shape of a structured ask (count / how many / track /
# follow / where did ... go / which way / when did ... stop|arrive|leave /
# detect / what moved) about a named visual medium (the shared noun table
# above, a recording, a media path or an artifact id) - never by a subject.
# "How many cows", "how many aircraft", "how many parcels" are one ask.
# ---------------------------------------------------------------------------

_PERCEPTION_ASK = re.compile(
    r"\bhow\s+many\b|\bcount(?:s|ing)?\b|\bnumber\s+of\b|\b(?:track|follow)(?:s|ing|ed)?\b(?!\s+up)|"
    r"\bwhere\s+did\b|\bwhich\s+(?:way|direction)\b|\bdetect(?:s|ing)?\b|\bwhat\s+moved\b|"
    r"\bmoving\s+(?:things|objects|shapes|regions)\b|"
    r"\bwhen\s+did\b.*\b(?:stop|arriv|leav|left|enter|exit|came|come|appear|disappear|mov|park|start)",
    re.IGNORECASE,
)
# Questions about the medium itself, or about its words, not what is in it.
_NOT_A_PERCEPTION_ASK = re.compile(
    r"\bhow\s+many\s+(?:seconds|minutes|hours|frames|words|times|pages|lines|bytes|megabytes|mb)\b|"
    r"\b(?:say|says|said|mention|mentions|mentioned|word|words|speak|speaks|spoke|language|languages)\b|"
    r"\bcount\s+(?:me|us|him|her|them)\s+(?:in|out)\b|\bcount\s+on\b|"
    r"\bfollow(?:ing)?\s+(?:\w+\s+)?(?:instructions|steps|advice|recipe|guide|directions|tutorial|rules|link|along)\b",
    re.IGNORECASE,
)
# The medium is what the ask is *in* - "how many ... in this video", "look at
# the footage and count ..." - never merely mentioned beside an unrelated
# question ("I watched the video, how many episodes are left?"), and never a
# video call or chat.
_PERCEIVABLE_MEDIUM = re.compile(
    r"\b(?:in|on|from|through|across|during|within|of|at)\s+(?:this|that|the|my|these|those)\s+"
    r"(?:[\w'-]+\s+){0,3}?(?:"
    + "|".join(_VISUAL_NOUNS + ("recording",))
    + r")s?\b(?!\s+(?:call|calls|chat|conference|meeting|game|link|channel))",
    re.IGNORECASE,
)
_MEDIA_FILE = re.compile(r"\S+\.(?:png|jpe?g|gif|webp|bmp|mp4|webm|mov|mkv|avi)\b", re.IGNORECASE)


def perceive_media_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    if not _PERCEPTION_ASK.search(text) or _NOT_A_PERCEPTION_ASK.search(text):
        return False
    if _IMAGE_CREATION_VERB.search(text):
        return False
    return bool(_PERCEIVABLE_MEDIUM.search(text) or _MEDIA_FILE.search(text) or ARTIFACT_ID_PATTERN.search(text))


PERCEPTION_TOOL = "perception"
_ASKED_KIND = re.compile(
    r"\bhow\s+many\s+((?:[a-z][\w'-]*\s+){0,2}?[a-z][\w'-]*?)\s+(?:are|were|is|was|do|did|can|came|went|moved|landed|"
    r"passed|entered|left|arrived|there|in|on|at|appear|show)\b|"
    r"\b(?:count|track|follow|detect)\s+(?:the|all|every|each)?\s*((?:[a-z][\w'-]*\s+){0,2}?[a-z][\w'-]*?)\s+(?:in|on|at|from|through)\b",
    re.IGNORECASE,
)
_GENERIC_KINDS = {"thing", "things", "object", "objects", "moving things", "moving objects", "of them", "them", "it"}


def _asked_kind(text: str) -> str:
    match = _ASKED_KIND.search(text)
    kind = next((g for g in (match.groups() if match else ()) if g), "")
    kind = " ".join(kind.split()).lower()
    return "" if kind in _GENERIC_KINDS else kind


def perceive_media(request: CapabilityRequest) -> TaskResult:
    """Find, follow and count what is in the medium the request points at,
    through the governed perception tool; say plainly what the backend could
    not tell (what the things are, when it only finds where)."""
    text = request.task.description
    content: dict = {"about": _asked_kind(text)}
    from src.manager.knowledge import artifact_ids_in, resolve_shared_media_all

    # "This picture" is the person's picture, never the view of it this
    # capability saved a moment ago - unless that view is named by its id.
    named_ids = set(artifact_ids_in([text]))
    described = next((
        found for found in resolve_shared_media_all(
            text, tools=request.tools, history=request.history, shared_files=request.shared_files,
            kinds=("image", "video"), limit=6,
        )
        if found.get("technique") != "perception" or found.get("artifact_id") in named_ids
    ), None)
    if described is not None:
        content["artifact_id"] = described.get("artifact_id", "")
    else:
        named = _MEDIA_FILE.search(text)
        if named is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "Which picture or recording? Share it in this conversation (or name it with 'artifact <id>') "
                "and I'll find, follow and count what is in it.",
            )
        content["path"] = named.group(0).strip("'\".,;")
    if not request.tools.permits(PERCEPTION_TOOL):
        return TaskResult.failure(request.task, request.own_name,
                                  "Perception is not installed for me right now, so I can't look for what is in it.")
    outcome = request.tools.invoke(PERCEPTION_TOOL, {"operation": "analyze", "content": json.dumps(content)})
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't look at it: {outcome.error}")
    result = json.loads(outcome.output)
    report = result["report"]
    lines = [result["text"]]
    if result.get("picture_artifact_id"):
        lines.append(f"A picture of what I followed is saved as artifact {result['picture_artifact_id']}.")
    if result.get("observations_recorded"):
        lines.append(f"What was seen is kept as {result['observations_recorded']} observation(s) of "
                     f"'{report['source']}', so you can ask about it or set a condition on it.")
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={
            "handled_by": "perceive_media", "backend": report["backend"], "distinct": report["distinct"],
            "peak_in_view": report["peak_in_view"], "labelled": report["labelled"],
            "picture_artifact_id": result.get("picture_artifact_id", ""),
            "source_artifact_id": result.get("source_artifact_id", ""), "events": report["events"],
            "asked_about": content["about"],
        },
    )


PERCEIVE_MEDIA = ManagerCapability(
    name="perceive_media",
    capabilities=frozenset({"perception", "tracking", "counting"}),
    handle=perceive_media,
    description=(
        "Finds, follows and counts what is in a shared picture or recording - how many, where each went, "
        "when each arrived, stopped or left - through the perception provider axis (GM-03). Says plainly "
        "what its backend cannot tell; never names a thing it did not name."
    ),
    claims=perceive_media_intent,
    scoreable=False,
    completes_request=True,
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


_HANDOUT_NOUNS = ("handout",)


def create_artifact_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    # GM-06: "make me a dashboard of <things that are observed>" is a live
    # view bound to data - live_view's own shape - never a static document.
    from src.manager.ecosystem_capabilities import live_view_intent

    if live_view_intent(task, participants):
        return False
    if _marker_present(task.description, _HANDOUT_MARKERS) or document_conversion_intent(task.description):
        return True
    if _reordered_subject(task.description, nouns=_HANDOUT_NOUNS) is not None:
        return True
    # Any other kind the person names and fills in - see
    # ``structured_construction_request`` for why the kind is open rather
    # than a list this file keeps.
    return structured_construction_request(task.description) is not None


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
        # The content the request already carried, in the person's own shape
        # ("a worksheet titled Fractions with: Monday - halves; Tuesday -
        # quarters") - read deterministically, so this works with no model at
        # all. Found by the Phase 11 closure sweep: the only structure this
        # handler could read without a Brain was ``TITLE:``/``SECTION:``,
        # which is the *Brain's* own answer format, so in Half Brain the
        # handler failed and printed that internal instruction at the person
        # as though it were something to type.
        construction = named_construction_request(text)
        if construction is not None:
            content = document_from_construction(construction)

    if content is None:
        if request.converse is None:
            return TaskResult.failure(
                request.task, request.own_name,
                "I don't have a way to organize this into a handout yet - no "
                "language model is configured for me right now. Tell me what "
                "should go in it and I'll make it now - a title and the "
                "entries, like: a handout titled Study Plan with: Monday - "
                "the first item; Tuesday - the second.",
            )
        notes = (_DOCUMENT_FORMAT_INSTRUCTION,) + _recent_conversation_notes(request)
        decision = request.converse(request.task, notes)
        if not decision.is_decided:
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't organize that into a handout ({decision.reason or 'no usable answer'}). "
                # Structured content needs no reasoning at all - said, so a
                # declined Brain never hides the path that works without one,
                # and said in the person's own shape rather than in the
                # model-facing format instruction this handler sends inward.
                "Tell me what should go in it and I'll make it now - a title "
                "and the entries, like: a handout titled Study Plan with: "
                "Monday - the first item; Tuesday - the second.",
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


_REVISION_VERBS = r"revise|update|edit|change|modify|redraw|amend"


def _named_revision(text: str, nouns: "tuple[str, ...]") -> "tuple[str, str] | None":
    """``(title words, the change asked for)`` when a revision names its
    artifact by title - "revise the water cycle diagram to add labels",
    "update my Study Plan handout: add Wednesday - chemistry" - or None.

    Phase 11 closure sweep: only "revise the diagram"/"update the handout"
    (no title) were recognised, so naming the thing - the natural way to
    say which one - reached open conversation. At least one meaningful word
    must stand between the article and the noun; the bare form keeps its
    own markers."""
    noun_group = "|".join(re.escape(n) for n in nouns)
    match = re.search(
        rf"\b(?:{_REVISION_VERBS})\s+(?:the|that|my|our|this)\s+(?P<title>(?:[\w'’-]+\s+){{1,8}}?)"
        rf"(?:{noun_group})\b\s*[:,-]?\s*(?P<rest>.*)$",
        str(text or ""), re.IGNORECASE,
    )
    if match is None:
        return None
    title = match.group("title").strip()
    if not (tokenize(title) - _QUERY_STOPWORDS):
        return None
    return title, match.group("rest").strip()


def _find_titled(request: CapabilityRequest, artifact_type: str, title: str) -> "tuple[str | None, str]":
    """``(artifact id, "")`` for the owned artifact the title names, or
    ``(None, why)`` - the tool's own honest answer when nothing matches."""
    found = request.tools.invoke("artifact_production", {"operation": "find_titled", "content": f"{artifact_type}\n{title}"})
    return (found.output.strip(), "") if found.ok else (None, str(found.error or "no match"))


_HANDOUT_REVISION_NOUNS = ("handout", "document")


# The revision counterpart of the generalized construction shape above: a
# revision verb, the thing named by title and kind, and the change asked for.
# The kind is open here for the same reason it is open there - an artifact
# created as a "worksheet" has to be revisable as one - but which artifact is
# meant is never guessed from the words: the store is the authority, so this
# returns every plausible title/kind split and the handler asks the store
# which one actually exists (see ``revise_artifact``).
_NAMED_REVISION_OPENER = re.compile(
    rf"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    rf"(?:{_REVISION_VERBS})\s+(?:the|that|my|our|this)\s+(?P<rest>\S.*)$",
    re.IGNORECASE,
)
_REVISION_CHANGE_COLON = re.compile(r"^(?P<phrase>[^:]+?)\s*:\s*(?P<change>\S.*)$")
_REVISION_CHANGE_VERB = re.compile(
    r"^(?P<phrase>.+?)\s+(?:to|and)\s+(?P<change>(?:add|include|append|remove|drop|delete|"
    r"change|say|show|list|mention|replace|correct|fix|rename|reword)\b.*)$",
    re.IGNORECASE,
)
# How many trailing words of the phrase may be the kind ("worksheet",
# "study guide"): the rest is the title the store is asked for.
_REVISION_KIND_WORD_LIMIT = 2


def named_revision_candidates(text: str) -> "tuple[tuple[tuple[str, str], ...], str] | None":
    """``((title, kind), ...), change`` for a revision that names its artifact
    by title, longest title first - or ``None`` when the text is not that
    shape. Every split is offered because "the Exam Prep study guide" is two
    readings ("Exam Prep study"/"guide" and "Exam Prep"/"study guide") and
    only the store knows which one was ever created.
    """
    raw = " ".join(str(text or "").split())
    opener = _NAMED_REVISION_OPENER.match(_strip_vocative(raw).strip())
    if opener is None:
        return None
    rest = opener.group("rest").strip()

    change_match = _REVISION_CHANGE_COLON.match(rest) or _REVISION_CHANGE_VERB.match(rest)
    if change_match is None:
        return None
    phrase = change_match.group("phrase").strip(" ,.;:-")
    change = change_match.group("change").strip(" ,.;:")
    words = phrase.split()

    candidates: list[tuple[str, str]] = []
    for kind_length in range(1, _REVISION_KIND_WORD_LIMIT + 1):
        if len(words) <= kind_length:
            break
        title = " ".join(words[:-kind_length]).strip(" ,.;:-")
        kind = " ".join(words[-kind_length:]).strip(" ,.;:-")
        if not title or not (tokenize(title) - _QUERY_STOPWORDS):
            continue
        # The kinds other surfaces own are theirs here too, exactly as on the
        # construction side - "update the Friday meeting to add Sam" is not a
        # document revision.
        if frozenset(re.findall(r"[\w'’-]+", kind.lower())) & {
            n.lower() for n in _SCHEDULING_OBJECT_NOUNS + _TASK_OBJECT_NOUNS + _AGENT_OBJECT_NOUNS
        }:
            continue
        candidates.append((title, kind))
    if not candidates or not change:
        return None
    return tuple(candidates), change


def revise_artifact_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = task.description
    if _marker_present(text, _REVISE_HANDOUT_MARKERS) or _marker_present(text, _REVISE_CONTENT_MARKERS):
        return True
    if _named_revision(text, _HANDOUT_REVISION_NOUNS) is not None:
        return True
    if bool(_REPLACEMENT_PATTERN.search(text)) and content_reference_intent(text):
        return True
    if revise_diagram_intent(task):
        # An image's own revision capability owns its kinds - asked directly
        # rather than copied, the same deferral shape used everywhere here.
        return False
    return named_revision_candidates(text) is not None


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
    named = _named_revision(text, _HANDOUT_REVISION_NOUNS)
    # Only when the request named neither the bare kind this file already owns
    # ("update the handout") nor content in play does the open-kind reading
    # apply - the identical precedence the branch chain below already keeps, so
    # "update the handout" plus a structured block stays a revision of the most
    # recent document rather than a search for one titled "handout".
    open_kind = (
        None
        if named is not None
        or _marker_present(text, _REVISE_HANDOUT_MARKERS)
        or _marker_present(text, _REVISE_CONTENT_MARKERS)
        else named_revision_candidates(text)
    )
    if match:
        artifact_id = match.group(1)
    elif named is not None:
        # Named by its title: that one, never merely the most recent.
        artifact_id, why = _find_titled(request, "document", named[0])
        if artifact_id is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't find that handout to revise: {why}.")
    elif open_kind is not None:
        # Named by title and a kind this file never enumerated ("the Fractions
        # worksheet"). The store decides which reading of the phrase was ever
        # real - asked longest title first, and nothing is guessed when none
        # of them exists.
        candidates, _change = open_kind
        artifact_id, why = None, ""
        for title, _kind in candidates:
            artifact_id, why = _find_titled(request, "document", title)
            if artifact_id is not None:
                break
        if artifact_id is None:
            tried = ", ".join(f"'{title}'" for title, _ in candidates)
            return TaskResult.failure(
                request.task, request.own_name,
                f"I couldn't find anything of mine by that name to revise (I looked for {tried}): {why}.",
            )
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
    if new_content is None and open_kind is not None and resolved is not None:
        # "revise the Fractions worksheet: add Wednesday - eighths" - the
        # change is additive and already structured, so it is applied with no
        # model at all, the same Half-Brain-safe path creation now has.
        new_content = document_with_additions(
            _document_from_content(resolved[0], resolved[1]), open_kind[1],
        )
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
# Running what was made, and reporting what actually happened.
#
# Phase 11 closure: the generalized creation path ended at a saved artifact.
# That is the right place for *creation* to end and the wrong place for the work
# to end, because a source file is evidence that something was written and never
# evidence that it runs - and for an artifact meant to be interactive, whether it
# runs is the whole question. A verified browser driver had existed in this
# repository for some time (src/providers/browser/cdp_browser.py, already used by
# WebsiteConnection); nothing connected it to anything Alfred created. This
# capability is that connection, through the ArtifactRuntimeProvider axis, so the
# engine stays replaceable and no artifact kind gets a runner of its own.
#
# Nothing here names a kind of artifact, a technology or a subject. "Run it" is
# the same request whether it points at an optics simulation, a game, a web app
# or something nobody has built yet.
# ---------------------------------------------------------------------------

# "does it work", asked of something already made.
_RUN_QUESTION = re.compile(
    r"\b(?:does|did|do)\s+(?:it|that|this)\s+(?:work|run|load|render)\b", re.IGNORECASE,
)
# ...but "why does that work?" asks for an explanation, not for a run. Found
# live: this claimed "I moved the increment before the check like you suggested
# and it fixed it - why does that work?" - a real reasoning question about code,
# which belongs to the Brain. Asking whether something works and asking why it
# works are different requests that share every word but this one.
_ASKS_WHY_IT_WORKS = re.compile(
    r"\b(?:why|how\s+come|how\s+does|how\s+did|how\s+do|what\s+makes)\b", re.IGNORECASE,
)
# A running verb in command position, pointing at what was made.
_RUN_POINTED = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:run|launch|preview|execute|try|test|open)\s+(?:it|that|this|them)\b",
    re.IGNORECASE,
)
# The same, naming what to run. Deliberately the narrower verb set: "open the
# ..." and "test the ..." are ordinary enough to belong to whatever else owns
# them, while "run"/"launch"/"preview"/"execute" are unambiguous asks to start
# something.
_RUN_NAMED = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:run|launch|preview|execute)\s+(?:the|my)\s+(?P<name>[\w'’-]+(?:\s+[\w'’-]+){0,5})\s*[.?!]*$",
    re.IGNORECASE,
)


# The unambiguous running verbs, wherever they appear - read only together with
# an explicit artifact id, which is what makes their position unnecessary there.
_RUN_VERB_ANYWHERE = re.compile(r"\b(?:run|launch|preview|execute)\b", re.IGNORECASE)


def run_artifact_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    working = _strip_vocative(text).strip()
    if _RUN_QUESTION.search(working) and not _ASKS_WHY_IT_WORKS.search(working):
        return True
    # "run artifact <id>" - an explicit reference is evidence enough on its own,
    # the same convention every other capability here reads an artifact id under.
    if _ARTIFACT_ID_PATTERN.search(working) and _RUN_VERB_ANYWHERE.search(working):
        return True
    has_verb = bool(_RUN_POINTED.match(working)) or bool(_RUN_NAMED.match(working))
    if not has_verb:
        return False
    # The capabilities that own their own "start this" requests keep them - the
    # same deferral discipline every other rule in this file states one for,
    # asked of the predicates themselves rather than copied.
    if (
        start_recording_intent(text) or stop_recording_intent(text)
        or cancel_recording_intent(text) or list_recordings_intent(text)
        or workspace_navigation_intent(task, participants)
        or communication_intent(task, participants)
    ):
        return False
    return True


def run_artifact(request: CapabilityRequest) -> TaskResult:
    """Run an artifact this agent owns and report what actually happened.

    Deterministic end to end - which artifact, the run, and the reading of it
    all come from real evidence - so it works identically in Half Brain. The
    artifact is never modified: an observation is a reading, and correcting a
    failing artifact is a revision the person asks for separately.
    """
    if not request.tools.permits("artifact_runtime"):
        equipped = ", ".join(request.tools.names()) or "none"
        return TaskResult.failure(
            request.task, request.own_name,
            "I can run something I've made and tell you what it actually did, but the "
            f"underlying 'artifact_runtime' capability is not installed. Currently equipped: {equipped}.",
        )

    text = request.task.description
    match = _ARTIFACT_ID_PATTERN.search(text)
    outcome = request.tools.invoke(
        "artifact_runtime", {"operation": "run", "content": match.group(1) if match else ""}
    )
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't run that: {outcome.error}")

    report = json.loads(outcome.output)
    title, artifact_id = report.get("title") or "it", report.get("artifact_id")
    summary, engine = report.get("summary") or "", report.get("engine") or "the runtime"

    if not report.get("ran"):
        # Not a failure of the artifact and not pretended to be one: nothing
        # observed it, and that is what gets said.
        # TaskResult.failure carries no payload by design (see contracts.py):
        # a failed turn reports a reason, and nothing downstream should be able
        # to read "results" off work that did not happen.
        return TaskResult.failure(
            request.task, request.own_name,
            f"I couldn't actually run '{title}' (artifact {artifact_id}), so I can't tell you "
            f"whether it works: {summary}",
        )

    errors = list(report.get("errors") or ())
    observed = report.get("observations") or {}
    seen = ", ".join(f"{k} {v}" for k, v in sorted(observed.items()) if k != "loaded")
    payload = {
        "handled_by": "run_artifact", "artifact_id": artifact_id, "ran": True,
        "clean": bool(report.get("clean")), "errors": errors, "observations": observed,
        "engine": engine,
    }
    if errors:
        # Ran and failed: reported as a failure, because "it ran" is not the
        # question that was asked.
        listed = "; ".join(errors[:3])
        return TaskResult.failure(
            request.task, request.own_name,
            f"I ran '{title}' (artifact {artifact_id}) in {engine} and it reported "
            f"{len(errors)} error(s): {listed}. What loaded: {seen or 'nothing to count'}. "
            "Tell me what it should do instead and I'll revise it.",
        )
    return TaskResult.success(
        request.task, request.own_name,
        f"I ran '{title}' (artifact {artifact_id}) in {engine} and it loaded with no errors. "
        f"What's actually there: {seen or 'nothing countable'}.",
        payload=payload,
    )


RUN_ARTIFACT = ManagerCapability(
    name="run_artifact",
    completes_request=True,
    capabilities=frozenset({"run", "preview", "launch"}),
    handle=run_artifact,
    description=(
        "Runs an artifact this agent owns in a real runtime and reports what actually "
        "happened - loaded or not, what it contains, and any runtime errors."
    ),
    claims=run_artifact_intent,
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


_DIAGRAM_NOUNS = ("diagram", "map")


_SUBJECT_CONNECTIVE = re.compile(
    r"^(?:of|about|on|for|showing|that\s+shows|which\s+shows|depicting|illustrating|explaining|with)\s+",
    re.IGNORECASE,
)


def _construction_subject(text: str, *, nouns: "tuple[str, ...]", exclude: "str | None" = None) -> "str | None":
    """What a "<verb> [me] [a|an|the] [up to four descriptors] <noun> [of|about|
    showing ...] <subject>" request asks to be made, with its leading connective
    removed - or None when the text is not that shape (or matches ``exclude``,
    a more specific kind of request another capability owns)."""
    raw = str(text or "")
    if exclude and re.search(exclude, raw, re.IGNORECASE):
        return None
    noun_group = "|".join(re.escape(n) for n in nouns)
    match = re.search(
        rf"\b(?:{_CONSTRUCTION_VERBS})\s+(?:me\s+|us\s+)?(?:(?:an?|the|one|some)\s+)?"
        rf"(?:[\w'-]+\s+){{0,4}}?(?:{noun_group})s?\b\s*[:,-]?\s*(?P<subject>[^?!]*)",
        raw, re.IGNORECASE,
    )
    if match is None:
        return None
    return subject_without_connective(match.group("subject"))


def subject_without_connective(subject: str) -> str:
    """"of a lighthouse at dusk" -> "a lighthouse at dusk"."""
    cleaned = " ".join(str(subject or "").split()).strip(" ,.:;")
    return _SUBJECT_CONNECTIVE.sub("", cleaned).strip(" ,.:;")


# ---------------------------------------------------------------------------
# The generalized named-construction shape - shared by the capability that
# makes a structured document and by every capability that must not mistake a
# request to *make* something for a request of its own.
#
# A person asking for something persistent names it, or says what goes in it,
# or both: "create a worksheet titled Fractions with: Monday - halves",
# "make a checklist called Preflight with: fuel on; flaps set". The KIND word
# is open on purpose - "handout", "worksheet", "syllabus", "briefing note", a
# word this project has never seen - because the kind belongs to the person's
# vocabulary, not to this file. What makes the request recognisable is its
# shape: a production verb, a kind, and a name and/or a content list.
#
# The Phase 11 closure sweep measured the cost of the closed alternative.
# ``_HANDOUT_NOUNS`` held "handout" alone, so "create a worksheet titled
# Fractions with: Monday - halves; Tuesday - quarters" matched no
# construction capability at all, fell through to the calendar - which sorts
# earlier and does claim a weekday - and was written to real calendar state
# as an event named "quarters", reported back as done. Adding "worksheet" to
# the noun list would have fixed that one sentence and left the next unseen
# noun failing in exactly the same way. The shape is the architecture; the
# nouns were the example.
#
# What the guards below name is not a vocabulary of documents but the kinds
# *other* capabilities already own - the identical "the more specific
# capability wins" reasoning ``calendar_intent`` states five deferrals for,
# and the identical way ``_DIAGRAM_NOUNS`` and ``_MEDIA_*_NOUNS`` each own
# their own words. Nothing here names a subject, a topic or a title.
# ---------------------------------------------------------------------------

# The words a scheduling capability owns as its own object. Declared once
# here and used by ``_CALENDAR_TIER2_PATTERN`` below, so "create a meeting
# titled Q3 Review" stays with the capability that actually keeps meetings
# instead of becoming a document that merely says "meeting".
_SCHEDULING_OBJECT_NOUNS: tuple[str, ...] = (
    "calendar", "schedule", "appointment", "appointments", "event", "events",
    "meeting", "meetings", "deadline", "deadlines",
)
# The same, for the outstanding-work and agent-roster surfaces: TASK's own
# object, and the specialist the Control Center's 'agent create' makes. A
# named agent is a real member of the ecosystem, never a document about one.
_TASK_OBJECT_NOUNS: tuple[str, ...] = (
    "task", "tasks", "to-do", "todo", "to-dos", "todos", "reminder", "reminders",
)
_AGENT_OBJECT_NOUNS: tuple[str, ...] = (
    "agent", "agents", "specialist", "specialists", "assistant", "assistants",
    "teammate", "teammates", "workspace", "workspaces",
)

_CONSTRUCTION_OPENER = re.compile(
    rf"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    rf"(?:{_CONSTRUCTION_VERBS}|write|draft|compose|write\s+up|put\s+together)\s+"
    r"(?:me\s+|us\s+|for\s+me\s+)?(?:(?:an?|the|one|some|another|my|our)\s+)?"
    r"(?P<rest>\S.*)$",
    re.IGNORECASE,
)
# "<kind> titled|called|named <title>" - the naming clause, any kind, any title.
_NAMING_CLAUSE = re.compile(
    r"^(?P<kind>.*?)\b(?:titled|entitled|called|named)\s+(?P<title>\S.*)$",
    re.IGNORECASE,
)
# "<kind> with|containing|including: <items>" - the content clause, introduced
# by a connective and a colon, and the bare-colon form ("a checklist: a; b").
_CONTENT_CLAUSE = re.compile(
    r"^(?P<head>.*?)(?:\s*,)?\s*(?:with|containing|holding|including|listing|"
    r"that\s+contains|which\s+contains|that\s+lists|which\s+lists)"
    # "with the sections:", "containing the following:" - a word or two naming
    # what the list is, between the connective and the colon. Non-greedy, so
    # the shortest reading wins and the title stays out of it.
    r"(?:\s+[\w'’-]+){0,3}?\s*:\s*(?P<body>\S.*)$",
    re.IGNORECASE,
)
_BARE_CONTENT_CLAUSE = re.compile(r"^(?P<head>[^:]*?)\s*:\s*(?P<body>\S.*)$")
# How a person separates the entries of a list they typed inline. A semicolon
# is unambiguous; a newline is what a pasted list carries.
_CONTENT_ITEM_SEPARATOR = re.compile(r"\s*[;\n]\s*|\s+•\s*")
# "Monday - the first item", "goal: ship", "part one -- the opening": an entry that names
# its own heading. Every dash variant, and the colon, are the same convention.
_ITEM_HEADING = re.compile(r"^(?P<heading>[^:–—-]{1,60}?)\s*(?::|–|—|--|-)\s*(?P<body>\S.*)$")
# A kind is a short noun phrase. Longer than this and the match is a sentence
# that merely began with a production verb, not a kind at all.
_KIND_WORD_LIMIT = 4


@dataclass(frozen=True)
class NamedConstruction:
    """A request to construct one named and/or filled thing, read off the
    request's own shape.

    ``kind`` is the person's own word for what to make, verbatim and
    unvalidated - this type carries it rather than judging it, so a kind
    nobody has ever typed before survives the trip to whichever capability
    owns it. ``title`` is "" when the request named no title, and ``items``
    is empty when it listed no content; at least one of the two is always
    present, because a request carrying neither is not this shape (see
    ``named_construction_request``).
    """

    kind: str
    title: str
    items: tuple[str, ...]

    @property
    def kind_words(self) -> frozenset[str]:
        return frozenset(re.findall(r"[\w'’-]+", self.kind.lower()))

    def kind_owned_by(self, nouns: "Sequence[str]") -> bool:
        """Whether some other capability already owns this kind's noun."""
        return bool(self.kind_words & {n.lower() for n in nouns})


def _construction_items(body: str) -> tuple[str, ...]:
    """The entries of an inline list, in the order they were typed."""
    parts = (p.strip(" ,.;\t") for p in _CONTENT_ITEM_SEPARATOR.split(str(body or "")))
    return tuple(p for p in parts if p)


def named_construction_request(text: str) -> "NamedConstruction | None":
    """The ``NamedConstruction`` this request is, or ``None`` when it is not
    that shape at all.

    Evidence, never vocabulary: a production verb in command position, a
    short kind phrase, and a naming clause and/or an inline content list. A
    request that names nothing and lists nothing ("create a meeting on
    Friday at 3pm") is deliberately not this shape - it asks for a time, not
    for content, and the capability that keeps times must keep it.
    """
    raw = " ".join(str(text or "").split())
    if not raw or raw.rstrip().endswith("?"):
        return None
    opener = _CONSTRUCTION_OPENER.match(_strip_vocative(raw).strip())
    if opener is None:
        return None

    rest = opener.group("rest").strip()
    items: tuple[str, ...] = ()
    content = _CONTENT_CLAUSE.match(rest)
    if content is not None:
        rest, items = content.group("head").strip(), _construction_items(content.group("body"))
    else:
        bare = _BARE_CONTENT_CLAUSE.match(rest)
        if bare is not None:
            rest, items = bare.group("head").strip(), _construction_items(bare.group("body"))

    title = ""
    naming = _NAMING_CLAUSE.match(rest)
    if naming is not None:
        rest = naming.group("kind").strip()
        title = naming.group("title").strip(" ,.;:'\"‘’“”")

    kind = rest.strip(" ,.;:-")
    # A title alone is weak evidence on its own, so a request that named
    # nothing needs a real list of at least two entries to be read as one
    # thing being filled in rather than a single remark that happens to
    # carry a colon ("make a note: buy milk" belongs to whoever keeps notes).
    if not title and len(items) < 2:
        return None
    if not kind or len(kind.split()) > _KIND_WORD_LIMIT:
        return None
    if not re.search(r"[A-Za-z]", kind):
        return None
    return NamedConstruction(kind=kind, title=title, items=items)


def structured_construction_request(text: str) -> "NamedConstruction | None":
    """``named_construction_request`` narrowed to the kinds no other
    capability owns - the open remainder that is a structured *document*.

    The deferrals are the sibling capabilities' own claims, asked directly
    so this rule needs no second copy of their vocabulary, plus the three
    object-noun sets declared above for the surfaces that are not Manager
    capabilities at all. ``calendar_intent`` is deliberately never asked:
    it already defers here, and a rule cannot be both sides of its own
    boundary - the scheduling nouns above are how this side states it.
    """
    construction = named_construction_request(text)
    if construction is None:
        return None
    if construction.kind_owned_by(
        _SCHEDULING_OBJECT_NOUNS + _TASK_OBJECT_NOUNS + _AGENT_OBJECT_NOUNS
    ):
        return None
    task = Task(text)
    if (
        create_diagram_intent(task) or create_3d_model_intent(task)
        or media_production_intent(task)
    ):
        return None
    return construction


# The heading a plain list of entries is kept under when the person gave the
# entries no headings of their own. Named, because an addition to such a
# document has to recognise it to extend the same list rather than inventing
# a second one - see ``document_with_additions``.
_COLLECTED_ITEMS_HEADING = "Contents"


def document_from_construction(construction: NamedConstruction) -> "DocumentContent | None":
    """Real, structured ``DocumentContent`` from what the request already
    carried - no model consulted, so this path works in Half Brain.

    An entry that names its own heading ("Monday - the first item") becomes a
    section with that heading; entries that do not are kept together, in
    order, under one honest heading rather than being given invented ones.
    ``None`` when the request carried a name but no content at all - honest
    absence, and the caller then asks the Brain to organize it.
    """
    from src.services.artifacts.models import DocumentContent, DocumentSection

    title = construction.title or construction.kind.strip().capitalize()
    if not title.strip() or not construction.items:
        return None

    headed = [(m.group("heading").strip(), m.group("body").strip())
              for m in (_ITEM_HEADING.match(i) for i in construction.items) if m is not None]
    sections: list[DocumentSection] = []
    try:
        if len(headed) == len(construction.items):
            sections = [DocumentSection(heading=h, body=b) for h, b in headed]
        else:
            sections = [DocumentSection(heading=_COLLECTED_ITEMS_HEADING, body="\n".join(construction.items))]
    except ValueError:
        return None
    if not sections:
        return None
    try:
        return DocumentContent(title=title, sections=tuple(sections))
    except ValueError:
        return None


# An additive revision names what to add and nothing else, so it needs no
# reasoning to apply - the counterpart of document_from_construction for a
# document that already exists.
_ADDITIVE_CHANGE = re.compile(r"^(?:add|include|append|also\s+add)\b\s*(?P<added>\S.*)$", re.IGNORECASE)


def document_with_additions(base: "DocumentContent", change: str) -> "DocumentContent | None":
    """``base`` with the entries ``change`` asks to add appended, in order -
    or ``None`` when the change is not purely additive, or names entries
    without their own headings, in which case the caller asks the Brain to
    compose the revision instead. Nothing existing is ever rewritten here.
    """
    from src.services.artifacts.models import DocumentContent, DocumentSection

    additive = _ADDITIVE_CHANGE.match(" ".join(str(change or "").split()))
    if additive is None:
        return None
    items = _construction_items(additive.group("added"))
    if not items:
        return None
    matches = [_ITEM_HEADING.match(item) for item in items]
    if not all(matches):
        # Entries with no headings of their own extend the list this document
        # already keeps them in, when that is the shape it has - the same
        # collection ``document_from_construction`` builds. A document of real
        # headed sections is never guessed at this way: it goes to the Brain.
        last = base.sections[-1] if base.sections else None
        if last is None or last.heading != _COLLECTED_ITEMS_HEADING or any(matches):
            return None
        try:
            extended = DocumentSection(heading=last.heading, body="\n".join((last.body, *items)))
        except ValueError:
            return None
        return DocumentContent(title=base.title, sections=base.sections[:-1] + (extended,))

    added: list[DocumentSection] = []
    for headed in matches:
        try:
            added.append(DocumentSection(heading=headed.group("heading").strip(), body=headed.group("body").strip()))
        except ValueError:
            return None
    try:
        return DocumentContent(title=base.title, sections=base.sections + tuple(added))
    except ValueError:
        return None


# A three-dimensional request belongs to create_3d_model, never to a flat image.
_THREE_DIMENSIONAL = r"\b(?:3d|3-d|three[\s-]dimensional)\b"


def create_diagram_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    if re.search(_THREE_DIMENSIONAL, str(task.description or ""), re.IGNORECASE):
        return False
    if _marker_present(task.description, _DIAGRAM_MARKERS):
        return True
    if _construction_subject(task.description, nouns=_DIAGRAM_NOUNS) is not None:
        return True
    return _reordered_subject(task.description, nouns=_DIAGRAM_NOUNS) is not None


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

    remainder = subject_without_connective(
        _after_marker(request.task.description, _DIAGRAM_MARKERS)
        or _construction_subject(request.task.description, nouns=_DIAGRAM_NOUNS)
        or _reordered_subject(request.task.description, nouns=_DIAGRAM_NOUNS) or ""
    )
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

    return _visual_result(request, outcome.output, "create_diagram")


def _visual_result(request: CapabilityRequest, output: str, handled_by: str) -> TaskResult:
    """A visual artifact is delivered only when a real image was produced.
    The description is always kept (a later revision renders it once an
    engine is configured), but reporting the request *completed* without an
    image would be a false completion (Phase 11 final gate) - so it is an
    honest failure that says what was kept and why no image exists."""
    from src.agents.tools.artifact_production import NO_RENDITION_MARKER

    if NO_RENDITION_MARKER not in output:
        return TaskResult.success(request.task, request.own_name, output, payload={"handled_by": handled_by})
    return TaskResult.failure(
        request.task, request.own_name,
        "No image was produced, so the diagram is not done. I kept what it should show as a real artifact, "
        "so it can be drawn as soon as an image engine is configured (revise it then). " + output,
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
    return (
        _marker_present(task.description, _REVISE_DIAGRAM_MARKERS)
        or _named_revision(task.description, _DIAGRAM_NOUNS) is not None
    )


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
    named = _named_revision(text, _DIAGRAM_NOUNS)
    if match:
        artifact_id = match.group(1)
    elif named is not None:
        # Named by its title: that one, never merely the most recent.
        artifact_id, why = _find_titled(request, "image", named[0])
        if artifact_id is None:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't find that diagram to revise: {why}.")
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

    remainder = named[1] if named is not None and match is None else _after_marker(text, _REVISE_DIAGRAM_MARKERS)
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

    return _visual_result(request, outcome.output, "revise_diagram")


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
    r"generate\s+an?\s+image|create\s+an?\s+image|generate\s+a\s+picture|create\s+a\s+picture|"
    # "make me an image of a quiet lounge" - the way people actually ask,
    # found at runtime against the real vendor (2026-09-15) reaching nothing.
    # Anchored the same way: the noun must be an image, so "make me a
    # handout" still belongs to create_artifact.
    r"make\s+(?:me\s+)?an?\s+(?:image|picture|illustration|drawing))\b",
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


# Closure campaign (2026-09-16, N-06): the last of six confirmed
# instances, deliberately left unfixed in the first pass because this
# function's anchoring is explicit, stated design - distinguishing
# *talking about* narration/images/video from *asking* for the
# tool-gated work. Re-examined once the shared mechanism existed and
# was proven elsewhere: _reordered_subject's declarative-desire shape
# ("I have/need/want a <noun>...") cannot match a question at all - "what
# is a good style for narration here?" has no such opener - so the same
# false-positive guard this function's docstring describes survives
# intact. Confirmed live before applying. Noun sets mirror each pattern's
# own vocabulary above, never invented.
_MEDIA_NARRATE_NOUNS = ("narration", "voiceover")
# "scene" deliberately excluded from the *reordered* illustrate noun set
# (the forward-ordered _MEDIA_ILLUSTRATE_PATTERN above still recognises
# "draw a scene", unaffected - that pattern is verb-anchored, so "scene"
# there never collides with compose/generate_video). Found live: unlike
# the forward-ordered patterns, which disambiguate by verb first, the
# reordered fallback's priority order checked illustrate's nouns
# unconditionally before compose/generate_video's, so "I need a scene of
# a sunset, can you compose it?" would have been silently routed to
# illustrate regardless of the verb actually asking for composition.
# image/picture/illustration/drawing/character remain unambiguous.
_MEDIA_ILLUSTRATE_NOUNS = ("image", "picture", "illustration", "drawing", "character")
_MEDIA_COMPOSE_NOUNS = ("video", "clip", "scene")
_MEDIA_GENERATE_VIDEO_NOUNS = ("video", "clip", "animation", "anime")


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
    The reordered fallback below preserves this: it can only ever match
    a declarative "I have/need/want" shape, never a question.
    """
    working = _strip_vocative(task.description).strip()
    if (
        _MEDIA_NARRATE_PATTERN.match(working)
        or _MEDIA_ILLUSTRATE_PATTERN.match(working)
        or _MEDIA_COMPOSE_PATTERN.match(working)
        or _MEDIA_GENERATE_VIDEO_PATTERN.match(working)
    ):
        return True
    # A production verb plus this operation's own noun - the shared
    # construction shape, for the asks the verb-anchored patterns above cannot
    # reach because the verb is generic and the noun carries the meaning
    # ("create a narration of X", "make an audio narration saying hello").
    # Found by the Phase 11 closure sweep; the same generalization
    # ``create_diagram_intent`` and ``document_generation_intent`` already use.
    if _construction_subject(working, nouns=_MEDIA_NARRATE_NOUNS) is not None:
        return True
    return _media_reordered_operation(working) is not None


# compose/generate_video share "video"/"clip" in their noun vocabulary -
# the reordered form still needs the same verb distinction the
# forward-ordered patterns already draw (assembling existing pieces vs.
# synthesising something new), so this checks the confirmation clause's
# own verb rather than guessing from the noun alone.
_MEDIA_REORDERED_GENERATE_VERB = re.compile(r"\b(?:generate|create|produce|make)\b", re.IGNORECASE)
_MEDIA_VIDEO_ISH_NOUNS = tuple(dict.fromkeys(_MEDIA_COMPOSE_NOUNS + _MEDIA_GENERATE_VIDEO_NOUNS))


# "make a narration that says <words>", "record a voiceover: <words>" - a
# construction request that carries the exact words to be spoken (Phase 11
# closure sweep: unclaimed, and answered as open conversation). A narration
# "of"/"about" a topic is not this shape: its words still have to be written,
# which is the Brain's work through the objective loop.
_NARRATION_WITH_WORDS = re.compile(
    rf"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:{_CONSTRUCTION_VERBS})\s+(?:me\s+)?(?:an?\s+)?"
    r"(?:short\s+|quick\s+|brief\s+)?(?:narration|voiceover|voice-over)"
    r"(?:\s+(?:that\s+says|saying|which\s+says)\s+|\s*:\s*)(?P<words>.+)$",
    re.IGNORECASE,
)
# "narrate this: <words>" / "narrate the following - <words>": the words
# after the request itself.
_NARRATE_LEAD_IN = re.compile(r"^\s*(?:this|that|the\s+following)?\s*[:\-]?\s*", re.IGNORECASE)


def _narration_text(working: str) -> str:
    """What a narration request asks to be *said* - never the request itself.
    "narrate this: welcome" says "welcome", not "narrate this: welcome".
    Anything this cannot separate is passed through unchanged, as before."""
    with_words = _NARRATION_WITH_WORDS.match(working)
    if with_words is not None:
        said = with_words.group("words")
    else:
        forward = _MEDIA_NARRATE_PATTERN.match(working)
        said = _NARRATE_LEAD_IN.sub("", working[forward.end():], count=1) if forward is not None else ""
    said = said.strip().strip("\"'“”‘’").strip()
    return said or working


def _media_reordered_operation(working: str) -> "str | None":
    """Which of the four operations a reordered request names, or None -
    the shared lookup ``media_production_intent`` and ``media_production``
    both use, so intent detection and operation selection can never
    disagree about the same request."""
    if _reordered_subject(working, nouns=_MEDIA_NARRATE_NOUNS) is not None or _NARRATION_WITH_WORDS.match(working):
        return "narrate"
    if _reordered_subject(working, nouns=_MEDIA_ILLUSTRATE_NOUNS) is not None:
        return "illustrate"
    if _reordered_subject(working, nouns=_MEDIA_VIDEO_ISH_NOUNS) is not None:
        return "generate_video" if _MEDIA_REORDERED_GENERATE_VERB.search(working) else "compose"
    return None


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
        # Neither forward-ordered pattern matched - try the reordered
        # shape (the same shared lookup media_production_intent already
        # used to claim this request) before falling back to the
        # original, unconditional "compose" default.
        operation = _media_reordered_operation(working) or "compose"

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

    # ``working`` - the address already removed, four lines above, to decide
    # which operation this is. Handing the tool the *raw* utterance instead
    # was a real, visible defect: ``_illustrate`` passes its content straight
    # to the image generator as the prompt and saves it as the artifact
    # title, so "Nova, make me an image of a cat" asked the generator to draw
    # "Nova, make me an image of a cat" and titled it that; ``_narrate``
    # would have spoken the address aloud. One function, two readings of the
    # same text - now one.
    outcome = request.tools.invoke(
        "media_production",
        {"operation": operation, "content": _narration_text(working) if operation == "narrate" else working},
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

# Closure campaign (2026-09-16): the fixed phrase list above only ever
# matched a clip-verb immediately followed by "this/the video/footage" -
# which never fires for the equally natural "clip 0:01 to 0:04 of that
# video", where the time range sits between the verb and its object. Found
# by a live, real end-to-end run (a genuine shared video, a genuine
# request), not a unit test: the request fell through to open_conversation
# and was refused with a generic "no reasoning capability" message instead
# of either clipping or a clip-specific refusal. This second, order-free
# check keeps the same "phrase plus evidence" discipline the docstring
# below describes - a clip-verb and a video/footage noun must BOTH be
# present as whole words, in either order - so "that was quite a task"
# still cannot claim this capability on an unrelated word alone, and the
# evidence check in clip_video_intent itself is unchanged.
_CLIP_VIDEO_VERB_NOUN_PATTERN = re.compile(
    r"\b(?:clip|cut|trim|extract|pull\s+out|grab)\b[\s\S]*\b(?:video|footage)\b"
    r"|\b(?:video|footage)\b[\s\S]*\b(?:clip|cut|trim|extract|pull\s+out|grab)\b"
    # "Give me the part of the video between 2 and 6 seconds" - a piece of
    # the video asked for by name; the time-range evidence check below still
    # has to hold, so "the best part of the video was the ending" never is.
    r"|\b(?:part|section|segment|bit|portion|excerpt|piece)\s+of\s+(?:this|that|the|my)\s+(?:video|footage)\b",
    re.IGNORECASE,
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
    if not (_marker_present(text, _CLIP_VIDEO_MARKERS) or _CLIP_VIDEO_VERB_NOUN_PATTERN.search(text)):
        return False
    # Ordinary talk about a video clip is not a request to cut one ("I ran 5
    # to 10 miles, then watched a video clip"): the order-free verb/noun
    # shape only counts when the sentence actually asks for something, and a
    # number range that measures something else (miles, people, dollars) is
    # never a clip boundary. The explicit imperative markers above ("clip this
    # video", "cut the video") are requests by themselves.
    if not _marker_present(text, _CLIP_VIDEO_MARKERS) and not _CLIP_REQUEST_CUE.search(text):
        return False
    if find_video_path(text) is not None:
        return True
    return bool(parse_clip_specs(_without_non_time_ranges(text)))


# A sentence that asks: an imperative opening (after an optional "please" or
# a name), or a request phrase anywhere. Grammar only.
_CLIP_REQUEST_CUE = re.compile(
    r"(?:^|[,;:]\s*)\s*(?:(?:please|ok(?:ay)?|hey|so|now)\s*,?\s+)?(?:[A-Z][a-z]+\s*,\s*)?(?:please\s+)?"
    r"(?:clip|cut|trim|extract|make|give|get|create|pull|grab|find|export|save|send|produce|split|take)\b"
    r"|\b(?:can|could|would|will)\s+you\b|\bplease\b|\bi\s+(?:need|want|would\s+like)\b|\bi'd\s+like\b|"
    r"\blet'?s\b|\bhelp\s+me\b|\bfor\s+me\b",
    re.IGNORECASE,
)
# "5 to 10 miles", "between 20 and 30 people" - a range of something other than time.
# The word after the range has to be able to name what was counted: a
# grammatical word ("seconds 2 to 4 out of that video", "... from the video")
# names nothing, and a range introduced by a time unit ("seconds 2 to 4") is a
# time range whatever follows it (found at runtime 2026-09-21).
_TIME_UNIT = r"(?:s|secs?|seconds?|mins?|minutes?|hours?|hrs?)"
_NON_TIME_RANGES = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:-|to|and|until|through)\s*\d+(?:\.\d+)?\s+"
    rf"(?!(?:{_TIME_UNIT}|out|of|off|from|in|into|on|onto|at|for|to|and|or|but|so|then|as|with|please|now)\b)[a-z]+",
    re.IGNORECASE,
)
_AFTER_TIME_UNIT = re.compile(rf"\b{_TIME_UNIT}\s*(?:from\s+|between\s+)?$", re.IGNORECASE)


def _without_non_time_ranges(text: str) -> str:
    """``text`` with every range that measures something other than time
    removed, so its numbers are never read as clip boundaries."""

    def _drop(match: "re.Match[str]") -> str:
        return match.group(0) if _AFTER_TIME_UNIT.search(text[: match.start()]) else " "

    return _NON_TIME_RANGES.sub(_drop, text)


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
    # Phase 11 closure: every agent's own, like remembering and the recording
    # session itself. It writes only to the recording agent's own memory, and
    # the shipped email specialist already did it with no grant at all - so a
    # "grant"/"revoke" of it in the Control Center was shown and changed
    # nothing. Declared as what it is, the Control Center says so truthfully.
    authority="every_agent",
    name="record_this",
    self_contained=True,
    self_contained_reason=(
        "this Manager's own recording session"
    ),
    completes_request=True,
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
    """Recognise an explicit "start recording [for X]" request - audio,
    screen or camera, ``recording_kind_of`` says which - and open a real
    recording session for this agent - never a capture itself, which
    only a browser can perform. The session this creates is what lets
    the Workspace/Agents UI honestly show "ready to record for
    research" rather than a person having to separately explain the
    purpose to a Start button.
    """
    if request.recording_sessions is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to record right now.",
        )
    purpose = extract_recording_purpose(request.task.description)
    kind = recording_kind_of(request.task.description)
    request.recording_sessions.request(request.own_name, purpose, kind=kind)
    noun = {"screen": "screen", "camera": "camera"}.get(kind, "")
    return TaskResult.success(
        request.task, request.own_name,
        f"Ready to record {(noun + ' ') if noun else ''}for {purpose} - use the Recording control to begin, "
        "or say 'stop recording' once you are done.",
        payload={"handled_by": "start_recording", "purpose": purpose, "kind": kind},
    )


START_RECORDING = ManagerCapability(
    authority='every_agent',
    own_path="CognitiveAgent.bind_recording - the agent's own recording session",
    name="start_recording",
    self_contained=True,
    self_contained_reason=(
        "this Manager's own recording session"
    ),
    completes_request=True,
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
        # "Nothing is currently recording" is true but unhelpful for the one
        # case that actually happens: the person said "start recording", the
        # session is waiting for the page's Recording control to open the
        # microphone, and no recording ever began. Say which of the two it is,
        # and clear the waiting request so the next "start recording" is clean.
        waiting = request.recording_sessions.get(request.own_name)
        if waiting is not None and getattr(waiting.state, "value", str(waiting.state)) == "requested":
            request.recording_sessions.cancel(request.own_name)
            return TaskResult.failure(
                request.task, request.own_name,
                "Nothing was recording - I was still waiting for the Recording control on the page to "
                "open the microphone, so there was nothing to stop. I've cleared that request.",
            )
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
    authority='every_agent',
    own_path="CognitiveAgent.bind_recording - the agent's own recording session",
    name="stop_recording",
    self_contained=True,
    self_contained_reason=(
        "this Manager's own recording session"
    ),
    completes_request=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.bind_recording - the agent's own recording session",
    name="cancel_recording",
    self_contained=True,
    self_contained_reason=(
        "this Manager's own recording session"
    ),
    completes_request=True,
    capabilities=frozenset({"recording", "audio"}),
    handle=cancel_recording,
    description="Discards the in-progress recording session without saving it.",
    claims=lambda task, participants=(): cancel_recording_intent(task.description),
    scoreable=False,
)


def list_recordings(request: CapabilityRequest) -> TaskResult:
    """List this agent's own saved recordings - audio, screen and
    camera alike - or answer whether one matching a named topic/purpose
    exists - the identical "bare view vs. named argument" shape
    ``list_tasks`` already uses, over the governed ``AUDIO_RECORDING``/
    ``VIDEO_RECORDING`` Memory categories rather than the actual
    artifact store (retrieval by real, searchable metadata - the actual
    audio/video stays exactly where the artifact store already keeps
    it). ``recall`` takes one ``kind`` per call, so both categories are
    asked for and merged, newest first - a person asking "what
    recordings do I have" must see every kind, not only the first one
    someone happened to query.
    """
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't have a way to look up recordings right now.",
        )

    topic = extract_recording_query(request.task.description)
    pool = sorted(
        (
            *request.recall(kind=MemoryCategory.AUDIO_RECORDING.value, limit=200),
            *request.recall(kind=MemoryCategory.VIDEO_RECORDING.value, limit=200),
        ),
        key=lambda record: record.created_at, reverse=True,
    )
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
    authority='every_agent',
    own_path="CognitiveAgent.bind_recording - the agent's own recording session",
    name="list_recordings",
    self_contained=True,
    self_contained_reason=(
        "the state of this Manager's own recording session"
    ),
    completes_request=True,
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
    description = request.task.description
    source_note = ""
    pointed = _TASK_FROM_CONTENT.search(description)
    if pointed is not None:
        # "Add a task from my design review recording for the drawings": the
        # task is in that content, not in these words (2026-09-21: the words
        # themselves became the task's title).
        passage, source_note, problem = _task_passage_from_content(request, description, pointed.group("about"))
        if problem:
            return TaskResult.failure(request.task, request.own_name, problem)
        description = f"Add a task: {passage}"
    record = build_task_record(description, agent_name=request.own_name, reminder=reminder)
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
        f"Added: {record.content}{source_note}",
        payload={"handled_by": "create_task", "task_id": record.id},
    )


# "... from my <kind> [about/for/on X]" - a task that lives in content already
# in play (a recording's transcript, a document, a file). Format nouns only.
_TASK_FROM_CONTENT = re.compile(
    r"\bfrom\s+(?:my|the|this|that|our)\s+(?:[a-z'-]+\s+){0,3}?"
    r"(?:recording|voice\s+note|voice\s+memo|transcript|document|file|pdf|notes|handout)\b"
    r"(?:\s+(?:for|about|on|regarding|covering)\s+(?P<about>[^.?!]+))?",
    re.IGNORECASE,
)


def _task_passage_from_content(request: CapabilityRequest, text: str, about: "str | None") -> "tuple[str, str, str]":
    """The one passage of the pointed-at content a task should be made from:
    (passage, a note naming where it came from, a problem to say instead).
    Chosen by the words the person gave for it, never guessed: no words, no
    match, or a tie between passages is asked about rather than decided."""
    found = resolve_content_sources(
        text, tools=request.tools, history=request.history, limit=1, shared_files=request.shared_files,
    )
    if not found:
        transcript_reason = ""
        if recording_reference_intent(text):
            _t, transcript_reason = recording_transcript_source(text, request.tools)
        return "", "", "I can't find that content to take a task from" + (f": {transcript_reason}." if transcript_reason else ".")
    payload, units = found[0]
    title = str(payload.get("title") or "that content")
    words = tokenize(about or "") - _QUERY_STOPWORDS
    if not words:
        return "", "", f"Which part of '{title}' should become the task? Say what it is about - \"...for the drawings\"."
    # One sentence is one thing to do: passages are split into sentences, and a
    # transcript's time heading ("0:05-0:08:") is where it was said, not what.
    sentences = [
        (unit.locator, sentence.strip())
        for unit in units
        for sentence in _SENTENCE_BREAK.split(_TIME_HEADING.sub("", unit.text))
        if sentence.strip()
    ]
    scored = sorted(((len(words & tokenize(s)), -n) for n, (_loc, s) in enumerate(sentences)), reverse=True)
    if not scored or scored[0][0] == 0:
        return "", "", f"Nothing in '{title}' mentions {about.strip()} - which part should become the task?"
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        options = "; ".join(f"{sentences[-n][0]}: {sentences[-n][1][:80]}" for s, n in scored if s == scored[0][0])
        return "", "", f"More than one part of '{title}' matches - which one? {options}"
    locator, sentence = sentences[-scored[0][1]]
    return sentence.rstrip("."), f" (taken from '{title}', {locator})", ""


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_TIME_HEADING = re.compile(r"^\s*\d+:\d{2}(?::\d{2})?\s*-\s*\d+:\d{2}(?::\d{2})?\s*:\s*")


CREATE_TASK = ManagerCapability(
    authority='every_agent',
    own_path="CognitiveAgent.workspace_task_reply - the agent's own task board",
    name="create_task",
    self_contained=True,
    self_contained_reason=(
        "the person's own task board, which this Manager governs"
    ),
    completes_request=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.workspace_task_reply - the agent's own task board",
    name="list_tasks",
    self_contained=True,
    self_contained_reason=(
        "the person's own task board, which this Manager governs"
    ),
    completes_request=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.workspace_task_reply - the agent's own task board",
    name="complete_task",
    self_contained=True,
    self_contained_reason=(
        "the person's own task board, which this Manager governs"
    ),
    completes_request=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.workspace_task_reply - the agent's own task board",
    name="reopen_task",
    self_contained=True,
    self_contained_reason=(
        "the person's own task board, which this Manager governs"
    ),
    completes_request=True,
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
    authority='every_agent',
    own_path="CognitiveAgent.workspace_task_reply - the agent's own task board",
    name="update_task",
    self_contained=True,
    self_contained_reason=(
        "the person's own task board, which this Manager governs"
    ),
    completes_request=True,
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
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="temporal_derivation",
    self_contained=True,
    self_contained_reason=(
        "arithmetic over dated facts in this Manager's memory and the clock"
    ),
    completes_request=True,
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
    authority='every_agent',
    name="current_datetime",
    self_contained=True,
    self_contained_reason=(
        "the clock - deterministic, and nothing a domain expert answers better"
    ),
    completes_request=True,
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
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="milestone_recognition",
    self_contained=True,
    self_contained_reason=(
        "a preference and a record this Manager holds"
    ),
    completes_request=True,
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
# Pre-Stage-9 Campaign 10 (CON-04): "send a message to Mum saying I'll be late",
# "text a note to Sam: running late", "message Kai to say hello". The message
# noun and the joining word are grammar; the party and the words are what the
# person said.
_MESSAGE_TO_PARTY_PATTERN = re.compile(
    r"^(?:please\s+)?(?:send|text|message|write)\s+(?:(?:a|an|the)\s+)?(?:quick\s+)?(?:message|text|note|msg)\s+"
    r"to\s+(?P<party>.+?)(?:\s*(?::|,)?\s+(?:saying|that\s+says|that|to\s+say|telling\s+(?:them|him|her))\s+|\s*:\s*)(?P<content>.+)$",
    re.IGNORECASE,
)
_MESSAGE_TO_PARTY_ONLY_PATTERN = re.compile(
    r"^(?:please\s+)?(?:send|text|write)\s+(?:(?:a|an|the)\s+)?(?:quick\s+)?(?:message|text|note|msg)\s+to\s+(?P<party>.+)$",
    re.IGNORECASE,
)
_CONTENT_LEAD = re.compile(r"^(?:saying|that\s+says|that|to\s+say)\s+", re.IGNORECASE)
# "message Kai to say hello", "message Dr. Okafor saying the results are in".
_MESSAGE_PARTY_PATTERN = re.compile(
    r"^(?:please\s+)?message\s+(?P<party>.+?)\s+(?:saying|that\s+says|that|to\s+say|telling\s+(?:them|him|her))\s+(?P<content>.+)$",
    re.IGNORECASE,
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

    match = _MESSAGE_TO_PARTY_PATTERN.match(working)
    if match:
        return ("send", match.group("party").strip(" :,."), match.group("content").strip())

    match = _MESSAGE_TO_PARTY_ONLY_PATTERN.match(working)
    if match:
        return ("send", match.group("party").strip(" :,."), "")

    match = _MESSAGE_PARTY_PATTERN.match(working)
    if match:
        return ("send", match.group("party").strip(" :,."), match.group("content").strip())

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
        content = _CONTENT_LEAD.sub("", parts[1].strip()) if len(parts) > 1 else ""
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
    self_contained=True,
    self_contained_reason=(
        "the exact Stage 15 case: a request to read or send a message is this Manager's however incidentally a specialist's own domain word overlaps it"
    ),
    # Word overlap cannot tell "call Mom" from "call this function" or
    # "message" from an ordinary noun - claims below is what actually
    # decides this, so these words only need to exist, the same "claims
    # decides, not overlap" rule RECORD_THIS's own comment already states.
    capabilities=frozenset(
        {"call", "calling", "text", "message", "messages", "reply", "phone", "dial"}
    ),
    handle=communication,
    carries_material=True,
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
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="personal_recurring_events",
    self_contained=True,
    self_contained_reason=(
        "dated personal facts in this Manager's own memory"
    ),
    completes_request=True,
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


def _compact(text: str) -> str:
    """Letters and digits only, lower-cased - "Bella Salon", "bella-salon" and
    "bellasalon" are one name."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def text_names_connection(text: str, name: str) -> bool:
    """Whether ``text`` names this one connection - as a whole run of up to
    four words, so a short name inside an unrelated word never matches
    ("bella salon", "bella-salon" and "bellasalon" are the same name).

    Extracted from ``named_connection`` below rather than duplicated,
    because two genuinely different questions need it: *which* non-default
    connection was named (that function), and whether a request names a
    connection at all (the routing gate in
    ``ManagerSkills._names_equipped_connection``).
    """
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    target = _compact(name)
    if len(target) < 3:
        return False
    for start in range(len(words)):
        joined = ""
        for end in range(start, min(len(words), start + 4)):
            joined += words[end]
            if joined == target:
                return True
            if len(joined) >= len(target):
                break
    return False


def named_connection(text: str, names: "Sequence[str]", default_name: str) -> "str | None":
    """The one non-default equipped connection ``text`` names, or None - a name
    counts only as a whole run of words in the request (so a short name inside
    an unrelated word never matches). Ambiguous (two named) is None too; the
    connected_action handler then asks which one is meant.

    The default is skipped deliberately and that is correct *here*: an
    unqualified request already means the default, and ``_select_connection``
    falls back to it. Asking "does this request name a connection at all?"
    is a different question - see ``text_names_connection`` above.
    """
    found = [name for name in names if name != default_name and text_names_connection(text, name)]
    return found[0] if len(found) == 1 else None


def names_equipped_connection(text: str, resolver: "Any | None") -> bool:
    """Whether ``text`` names a connection this agent was actually equipped
    with - the *routing* question, and deliberately not the same question
    ``named_connection`` answers ("which non-default one").

    One rule, two callers: ``ManagerSkills._names_equipped_connection`` and
    ``SpecialistCapabilities.__call__``. It lives here rather than in either
    of them because having it in only one was itself the defect - the Manager
    had this gate and a specialist did not, so a specialist genuinely granted
    ``connected_action`` could reach a connection only through the literal
    phrase "check my connected account", never by naming the service
    (measured 2026-09-23: Alfred reached the library and the clinic by name;
    Rhea, granted the capability, reached neither).

    The default connection is included, unlike in ``named_connection``,
    except when it supports no operations at all - which is exactly the
    "null" no-op connection an unconfigured installation runs with, where
    naming it could not mean anything to do. That is a property of the
    connection, never a name this function knows.
    """
    if resolver is None:
        return False
    if named_connection(text, resolver.names, resolver.default_name) is not None:
        return True
    default = resolver.get(resolver.default_name)
    if not getattr(default, "supported_operations", ()):
        return False
    return text_names_connection(text, resolver.default_name)


def _connection_request_text(task: Task) -> str:
    """The words that choose a connection and an operation: what follows a
    routing phrase when one was used, otherwise the whole request."""
    if _marker_present(task.description, CONNECTED_ACTION_MARKERS):
        # A routing phrase was used: only what follows it counts, so the
        # phrase's own words never favour an operation.
        return _after_marker(task.description, CONNECTED_ACTION_MARKERS)
    return task.description


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

    named = named_connection(task.description, names, default_name)
    if named is not None:
        return (named, ())
    intent_words = _meaningful_words(_connection_request_text(task))
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

    intent_words = _meaningful_words(_connection_request_text(task))
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
        request.task, request.own_name, readable_connection_output(connection.name, outcome.output),
        payload={"handled_by": "connected_action", "connection": connection.name, "operation": operation},
    )


def readable_connection_output(connection_name: str, output: str) -> str:
    """What a person reads back from a connection: a web page receipt
    (``url``/``title``/``text``/``elements``) becomes a short plain summary -
    the page's own title and words, then its fields, buttons and links by their
    visible labels - instead of raw JSON. Any other output is returned as it
    came. The raw result stays in the payload."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return output
    if not isinstance(data, dict) or not ({"url", "title", "text"} & set(data)):
        return output
    lines = []
    title = str(data.get("title") or "").strip()
    lines.append(f"{connection_name}: {title}" if title else f"{connection_name}: {data.get('url', '')}")
    text = " ".join(str(data.get("text") or "").split())
    if text:
        lines.append(text[:900])
    groups: "dict[str, list[str]]" = {}
    for element in data.get("elements") or []:
        if not isinstance(element, dict):
            continue
        tag = str(element.get("tag") or "")
        kind = "links" if tag == "a" else ("buttons" if tag == "button" or element.get("type") in ("submit", "button") else "fields")
        label = str(element.get("label") or element.get("locator") or "").strip()
        if label:
            groups.setdefault(kind, []).append(label)
    for kind in ("fields", "buttons", "links"):
        if groups.get(kind):
            lines.append(f"{kind.capitalize()}: " + ", ".join(groups[kind]))
    return "\n".join(lines)


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
        # Phase 11 closure: said as what it is. "The 'null' connection is
        # configured" read as though something real had been set up. A
        # connection with no actions is the honest absence of one; the ones
        # that can act, if any, are named so the person can ask for them.
        able = sorted(
            name for name in resolver.names
            if name != connection.name and getattr(resolver.get(name), "supported_operations", ())
        )
        return TaskResult.failure(
            request.task, request.own_name,
            (
                f"No connection that can act is set up for that - '{connection.name}' offers no actions. "
                + (f"Connections that can: {', '.join(able)} - name the one you mean. " if able else "")
                + "Add one in the Control Center ('services <name> discover <url>', or "
                "'connections <service> connect <account-id> <credential>')."
            ),
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
        # A value the request supplies by naming something the connection
        # genuinely observed (a website's field or button, by its visible
        # label) - asked of the connection itself, never guessed here.
        observed = getattr(connection, "values_from_request", None)
        if observed is not None:
            for name, value in (observed(operation, request.task.description) or {}).items():
                available.setdefault(name, value)
        # A declared parameter keeps its service's own spelling ("trackId");
        # a value stated in conversation arrives lower-cased ("trackid=...").
        # Matched without regard to case, sent under the declared name.
        supplied = {str(name).lower(): value for name, value in available.items()}
        missing = [p.name for p in parameters if p.required and p.name.lower() not in supplied]
        if missing:
            return TaskResult.failure(
                request.task, request.own_name,
                f"'{operation}' on '{connection.name}' needs {', '.join(missing)}, which "
                "I don't have yet - either tell me the value(s) directly, or complete "
                "whatever earlier step would provide them.",
            )
        params = {p.name: supplied[p.name.lower()] for p in parameters if p.name.lower() in supplied}

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
    self_contained=True,
    self_contained_reason=(
        "reaches a connection this Manager itself is equipped with"
    ),
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
# Deliberately NOT here, and checked by
# tests/test_stage18_knowledge_and_learning.py: "teach"/"explain"/"discuss".
# Those are OPEN_CONVERSATION's own declared words - being taught something is
# a conversation with the agent's own Brain, not a settled-knowledge lookup -
# and every marker above is an interrogative opener rather than a verb, which
# that test also enforces. The Phase 11 closure sweep tried adding them and
# was right to be reverted: "Teach me how a car engine works" belongs where it
# already went.

# "what is photosynthesis?", "who are the Medici?" - the plain definitional
# question, which no phrase list can enumerate because the phrase IS the
# subject. Shape-only, and narrowed by two things it must not be: a question
# about the person or about Alfred ("what is my goal?", "what are your
# capabilities?"), which belongs to whoever holds that, and a question whose
# subject is only a pointing word ("what is this?"), which names nothing.
_DEFINITIONAL_QUESTION = re.compile(
    r"^(?:so\s+|and\s+|ok(?:ay)?,?\s+)?(?:what|who)\s+(?:is|are|was|were)\s+"
    r"(?:a|an|the)?\s*(?P<subject>[^?]{2,80})\s*\??\s*$",
    re.IGNORECASE,
)
_SELF_OR_AGENT_REFERENCE = re.compile(
    r"\b(?:my|mine|me|i|our|ours|we|us|your|yours|you)\b", re.IGNORECASE,
)


def definitional_question_intent(text: str) -> bool:
    match = _DEFINITIONAL_QUESTION.match(" ".join(str(text or "").split()))
    if match is None:
        return False
    subject = match.group("subject").strip(" ,.;:")
    if _SELF_OR_AGENT_REFERENCE.search(subject):
        return False
    return bool(tokenize(subject) - _QUERY_STOPWORDS)

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

# The same reference, to the person's own body of material rather than to one
# file they just handed over - "what do my documents say about the project
# history?", "what does my documentation say about deployment?". A pattern
# rather than more entries above, because the possessive and the format noun
# are the whole signal and neither of them is a subject: enumerating
# "my documents"/"my documentation"/"my files"/"my notes" would just be the
# closed-list mistake again one level down.
_OWN_CORPUS_REFERENCE = re.compile(
    r"\b(?:my|our)\s+(?:own\s+)?"
    r"(?:documents?|documentation|docs?|files?|notes?|papers?|records?|"
    r"spreadsheets?|presentations?|slides?|pdfs?)\b",
    re.IGNORECASE,
)


def own_corpus_reference_intent(text: str) -> bool:
    return bool(_OWN_CORPUS_REFERENCE.search(str(text or "")))


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


# The grammar of asking about something - a question, or a request to have it
# explained, summarised or described - never a subject word.
_ASKS_ABOUT_CONTENT = re.compile(
    r"\?|\b(?:what|who|when|where|why|how|which|did|does|was|were|summari[sz]e|explain|describe|tell\s+me|find)\b",
    re.IGNORECASE,
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
        # The person's own material, and the plain definitional question - see
        # each predicate's own note for why neither can be a phrase list.
        or own_corpus_reference_intent(text)
        or definitional_question_intent(text)
        # A question about a recording is a question about its words - its
        # transcript, answered like a document (2026-09-21). Only a question:
        # "give me captions for this recording" asks for something to be done
        # with it.
        or (recording_reference_intent(text) and _ASKS_ABOUT_CONTENT.search(text))
    ):
        return False
    # Starting, stopping, cancelling, listing, transcribing, clipping or
    # translating a recording is an instruction about it, never a question
    # about what it says.
    if (start_recording_intent(text) or stop_recording_intent(text) or cancel_recording_intent(text)
            or list_recordings_intent(text) or transcribe_media_intent(task, participants)
            or translate_media_intent(task, participants) or clip_video_intent(task, participants)):
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
    # "read my notes to me" names the person's own material, which
    # ``own_corpus_reference_intent`` above recognises - but reading back what
    # Alfred was asked to keep is READ_BACK's own work, and it sorts later than
    # "knowledge". A seventh deferral of the same kind as the six above, added
    # when the own-corpus reading was generalized (Phase 11 closure).
    if read_back_intent(task, participants):
        return False
    # GM-03: "when did the cart stop in the video" is about what the recording
    # shows over time - perceive_media's own shape - never settled knowledge.
    if perceive_media_intent(task, participants):
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
    # "Translate this document into French" asks for the document's words in
    # another language - translate's own request shape (it resolves the
    # document itself), never a question about what the document means.
    if translate_intent(task, participants):
        return False
    return True


# How many of the person's own passages a quoted answer shows before it says
# how many more there are. A screenful, not a dump - the same reasoning
# MEMORY_SAMPLE carries in the Control Center's own views.
_QUOTED_PASSAGE_LIMIT = 6

# Asking for something *written in* the material ("what time does the train
# leave", "find the deadline", "how much is the deposit"), as opposed to asking
# to be told about a subject ("tell me about Napoleon", "explain this",
# "summarise it"). The first is a lookup and is answerable by quoting; the
# second needs a model and is refused without one.
_LOOKUP_SHAPE = re.compile(
    r"^\s*(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?"
    r"(?:what|when|where|who|which|whose|how\s+(?:much|many|long|often))\b"
    r"|\b(?:find|look\s+up|search\s+for|show\s+me|tell\s+me\s+the)\b",
    re.IGNORECASE,
)
_NOT_A_LOOKUP = re.compile(
    r"\b(?:explain|summari[sz]e|summary|compare|analy[sz]e|why|opinion|think|mean[s]?\b|"
    r"tell\s+me\s+about|what\s+do\s+you\s+(?:think|make))\b",
    re.IGNORECASE,
)


# "What does this PDF say?" names no thing to find - it asks for the document
# itself, which is read_content's job and what the refusal below points at
# (REPAIR-01 decided deliberately that this shape is reasoning, not reading).
# Quoting every matched passage at it would quietly replace that decision.
_JUST_ASKS_WHAT_IT_SAYS = re.compile(
    r"^what(?:'s|\s+is|\s+does|\s+do)?\s+(?:this|the|that|my|it)\b[\w\s]{0,20}?"
    r"(?:say|says|about|in\s+it)\s*[?.!]*$",
    re.IGNORECASE,
)


def _asks_to_look_something_up(text: str) -> bool:
    """Whether this question asks for a particular thing that would be written
    down in the person's own material - answerable by quoting it back."""
    body = " ".join(str(text or "").split())
    if _NOT_A_LOOKUP.search(body) or _JUST_ASKS_WHAT_IT_SAYS.match(body):
        return False
    return bool(_LOOKUP_SHAPE.search(body))


def quoted_from_own_material(
    request: CapabilityRequest, evidence: KnowledgeEvidence, why: str, *, handled_by: str,
) -> "TaskResult | None":
    """The person's own material, quoted, when their question asks for
    something written in it and nothing can reason over it - otherwise None.

    This is retrieval, never reasoning: the passages are reproduced exactly as
    they stand, with their own citations, and the answer says plainly that
    they were matched on words and not interpreted. That keeps the structural
    honesty of the Half Brain paths (nothing here can produce a sentence
    *about* the subject) while ending the case that made those paths useless -
    a person whose own documents contain the answer being told only that no
    model is configured.
    """
    if evidence.is_empty or not _asks_to_look_something_up(request.task.description):
        return None
    # Phase 11 closure: this answer says the passages were matched on the
    # question's words, so only passages that really share one are quoted.
    # Evidence gathering keeps a "most recent" fallback when nothing overlaps
    # (right for a Brain, which reasons over background), and without this
    # "what is the kitchen speaker doing?" was answered with an unrelated
    # stored fact presented as a match. A passage's own label ("Personal
    # context:", "From your own documents:") is not its content, so it never
    # counts as a match.
    asked = tokenize(request.task.description) - _QUERY_STOPWORDS
    matched = tuple(
        note for note in evidence.notes
        if tokenize(note.split(": ", 1)[1] if ": " in note else note) & asked
    )
    if not matched:
        return None
    shown = "\n".join(f"  - {note}" for note in matched[:_QUOTED_PASSAGE_LIMIT])
    more = (
        f"\n  ...and {len(matched) - _QUOTED_PASSAGE_LIMIT} more passage(s)."
        if len(matched) > _QUOTED_PASSAGE_LIMIT else ""
    )
    return TaskResult.success(
        request.task,
        request.own_name,
        (
            "Here is what your own material says, word for word - I matched the words in your "
            f"question, I did not interpret or summarise ({why}):\n{shown}{more}"
        ),
        payload={
            "handled_by": handled_by,
            "knowledge_sources": tuple(evidence.sources),
            "reasoning": "none - quoted verbatim",
            "currency": "settled",
            "unavailable_sources": evidence.unavailable,
        },
    )


def _knowledge_with_local_assist(request: CapabilityRequest, evidence: KnowledgeEvidence) -> "TaskResult | None":
    """Post-Pre-Stage-9: when no Brain can answer, the attributed local assistant
    works over the same gathered evidence - a supplied document explained,
    summarised or discussed from its own passages, or a settled-knowledge
    question answered - always marked as a small local model's words, never
    the Manager's verified reasoning. None when no assistant is equipped or it
    produced nothing."""
    if request.assist is None:
        return None
    from src.providers.local_assistant import ATTRIBUTION

    question = request.task.description
    if evidence.notes:
        text = request.assist.explain("\n".join(evidence.notes)[:6000], question=question)
    else:
        text = request.assist.answer(question, context=tuple(_recent_conversation_notes(request)))
    if not text:
        return None
    return TaskResult.success(
        request.task, request.own_name, f"{text}\n\n({ATTRIBUTION})",
        payload={
            "handled_by": "knowledge_inquiry", "brain": "local_assist", "assisted": True,
            "sources": list(evidence.sources),
        },
    )


# "Summarize this", "give me the gist", "what are the key points", "outline it".
_SUMMARY_SHAPE = re.compile(
    r"\b(?:summar(?:y|ies|i[sz]e|i[sz]ing)|gist|key\s+points|main\s+points|outline|overview|tl;?dr|recap|highlights)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_DOCUMENT_NOTE = re.compile(r"^From the document '(?P<title>[^']*)'[^:]*:\s*(?P<text>.*)$", re.DOTALL)


def extractive_outline(text: str, *, limit: int = 5) -> "tuple[str, ...]":
    """The sentences of ``text`` whose words recur most across it, in their
    original order - an extract, never new prose. Deterministic: the same text
    always gives the same sentences, so it is honest in Half Brain."""
    sentences = [s.strip() for s in _SENTENCE_END.split(" ".join(str(text or "").split())) if len(s.strip()) > 12]
    if len(sentences) <= limit:
        return tuple(sentences)
    frequency: "dict[str, int]" = {}
    for sentence in sentences:
        for word in tokenize(sentence) - _QUERY_STOPWORDS:
            frequency[word] = frequency.get(word, 0) + 1
    scored = []
    for position, sentence in enumerate(sentences):
        words = tokenize(sentence) - _QUERY_STOPWORDS
        score = sum(frequency.get(word, 0) for word in words) / (len(words) ** 0.5 or 1.0)
        scored.append((score, position))
    chosen = sorted(position for _score, position in sorted(scored, reverse=True)[:limit])
    return tuple(sentences[position] for position in chosen)


def _extract_of_supplied_content(
    request: CapabilityRequest, evidence: KnowledgeEvidence, why: str,
) -> "TaskResult | None":
    """"Summarize this document" with nothing to reason with: the document's
    most central sentences, quoted in order and labelled as an extract - never
    passed off as a summary somebody wrote."""
    if "supplied_content" not in evidence.sources or not _SUMMARY_SHAPE.search(request.task.description):
        return None
    titles: "list[str]" = []
    body: "list[str]" = []
    for note in evidence.notes:
        match = _DOCUMENT_NOTE.match(note)
        if match is None:
            continue
        if match.group("title") not in titles:
            titles.append(match.group("title"))
        body.append(match.group("text"))
    outline = extractive_outline(" ".join(body))
    if not outline:
        return None
    named = ", ".join(f"'{title}'" for title in titles) or "the document"
    lines = "\n".join(f"  - {sentence}" for sentence in outline)
    return TaskResult.success(
        request.task, request.own_name,
        (
            f"The central sentences of {named}, word for word and in order - an extract I picked by how "
            f"often their words recur, not a summary written by a model ({why}):\n{lines}"
        ),
        payload={"handled_by": "knowledge_inquiry", "reasoning": "none - extractive outline", "sentences": len(outline)},
    )


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
    quoted = quoted_from_own_material(request, evidence, why, handled_by="knowledge_inquiry")
    if quoted is not None:
        return quoted
    extract = _extract_of_supplied_content(request, evidence, why)
    if extract is not None:
        return extract
    # "What does this PDF say?" with nothing to reason with: the document's own
    # words, read - never a refusal while the answer is sitting right there.
    if "supplied_content" in evidence.sources and _WHAT_DOES_IT_SAY.search(request.task.description):
        read = read_content(request)
        if read.succeeded:
            return TaskResult.success(
                request.task, request.own_name,
                f"No model can summarise it for me ({why}), so here are its own words.\n\n{read.output}",
                payload={**dict(read.payload or {}), "handled_by": "knowledge_inquiry", "reasoning": "none - read verbatim"},
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
            " If you are after something written in it, ask for that directly - "
            "\"what time does the train leave?\", \"find the deadline in this document\" - "
            "and I will quote it back to you."
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
        assisted = _knowledge_with_local_assist(request, evidence)
        if assisted is not None:
            return assisted
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
        assisted = _knowledge_with_local_assist(request, evidence)
        if assisted is not None:
            return assisted
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
# Pre-Stage-9 Campaign 7 (RD-04): asking for the words written in a picture -
# "read the text in this photo", "what does this sign say?", "read this
# screenshot to me". Shape only; which image is meant is resolved by the handler.
_PICTURE_NOUNS = r"(?:image|photo|picture|screenshot|scan|sign|poster|label|whiteboard|receipt|menu)"
_IMAGE_TEXT_READ = re.compile(
    rf"\bread\b.{{0,40}}\b(?:text|words|writing)\b.{{0,40}}\b{_PICTURE_NOUNS}s?\b"
    rf"|\bwhat\s+does\s+(?:this|the|that|my)\s+{_PICTURE_NOUNS}\s+say\b"
    rf"|\bwhat(?:\s+is|\s+was|'s)?\s+written\s+(?:on|in)\s+(?:this|the|that|my)\s+{_PICTURE_NOUNS}\b"
    rf"|\bread\s+(?:me\s+)?(?:this|the|that|my)\s+{_PICTURE_NOUNS}\b",
    re.IGNORECASE,
)
_LOCATOR_VERBS = re.compile(r"\b(read|say|says|said|show|on)\b", re.IGNORECASE)
# A file on this machine, named in full. The media pattern covers audio and
# video; documents and images are named the same way people name any file.
_NAMED_FILE_PATTERN = re.compile(
    r"\S+\.(?:pdf|txt|md|rtf|docx|doc|odt|html|htm|epub|csv|json|pptx|xlsx"
    r"|png|jpg|jpeg|gif|webp|bmp|tiff?"
    r"|wav|mp3|m4a|ogg|oga|opus|flac|aac|mp4|mov|mkv|webm|avi)\b",
    re.IGNORECASE,
)


def names_specific_content(text: str) -> bool:
    """Whether this request points at one exact thing - ``artifact <id>`` or a
    file named in full - rather than at "this"/"it".

    Shared by the capabilities that were unreachable precisely when a person
    did the most specific thing available to them: every refusal in this file
    invites "name it with 'artifact <id>'", and until now naming it dropped
    the request into open_conversation instead.
    """
    body = str(text or "")
    return bool(ARTIFACT_ID_PATTERN.search(body) or _NAMED_FILE_PATTERN.search(body))


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
        # Naming the thing exactly - "read artifact 4bc880fe28f8", "read the
        # text in artifact 4bc880fe28f8" - is already unambiguous, so it needs
        # no "to me" to prove a reading was meant. Without this, a person who
        # took the refusal's own advice ("name it with 'artifact <id>'") was
        # dropped into open_conversation and told no model was configured.
        return _marker_present(text, _READ_ALOUD_MARKERS) or names_specific_content(text)
    return bool(_IMAGE_TEXT_READ.search(lowered))


# "What does this file say?", "what's in this document?", "what is written in
# the PDF?" - asking to hear the words themselves, with no question about
# them. Deterministic reading answers it with or without a model.
_WHAT_DOES_IT_SAY = re.compile(
    r"^\s*(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?(?:what\s+(?:does|do)\s+(?:this|that|the|my|it)"
    r"(?:\s+(?:file|document|pdf|doc|text|note|notes|letter|book|article|handout|page|attachment|upload))?\s+say"
    r"|what(?:'s|\s+is)\s+(?:written\s+)?in\s+(?:this|that|the|my)\s+(?:file|document|pdf|doc|attachment|upload))"
    r"\s*[?.!]*\s*$",
    re.IGNORECASE,
)


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
    # Pre-Stage-9 Campaign 7 (RD-05): a request naming a web address is about
    # that page - never a different, already-shared file read instead - and
    # opening an arbitrary address is an outward action this capability does
    # not take on its own.
    if re.search(r"\bhttps?://\S+|\bwww\.\S+", text, re.IGNORECASE) and not re.search(
        r"\bartifact[:\s]+[0-9a-f]{6,32}\b", text, re.IGNORECASE,
    ):
        return TaskResult.failure(
            request.task, request.own_name,
            "I don't open web addresses myself. Save the page (or download the book) and share "
            "the file here - a web page (.html) or an e-book (.epub) - and I'll read it to you.",
        )
    # "Read my recording to me": a recording's words are its transcript -
    # kept, or made now through the configured transcription provider.
    if recording_reference_intent(text) and not ARTIFACT_ID_PATTERN.search(text):
        transcript, reason = recording_transcript_source(text, request.tools)
        if transcript is None:
            return TaskResult.failure(request.task, request.own_name, f"I can't read that recording to you: {reason}.")
    # Pre-Stage-9 Campaign 3: "read this file" means the file just shared.
    # When that file has no text (a video, an image, a recording) the honest
    # answer is about *that* file - never a different file read instead.
    elif not _POINTS_AT_A_DOCUMENT.search(text):
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


# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 7 (RD-03): read back what is kept for the person.
#
# "Read my notes to me", "read back my schedule for tomorrow", "go over my
# tasks" - the same words the person would hear from the note store, the
# calendar listing and the task listing, spoken in full. Each collection is
# answered by the code that already owns it; this only recognises the request
# and joins the answers. A request aimed at a file ("read my notes PDF to me")
# stays read_content's.
# ---------------------------------------------------------------------------

_READ_BACK_VERB = re.compile(r"\b(?:read|go\s+over|run\s+through|recite)\b", re.IGNORECASE)
_READ_BACK_FILE_WORDS = re.compile(
    r"\b(?:pdf|document|doc|docx|file|spreadsheet|presentation|slides?|handout|artifact)\b", re.IGNORECASE,
)
_READ_BACK_COLLECTIONS: "tuple[tuple[str, re.Pattern[str]], ...]" = (
    ("notes", re.compile(
        r"\bmy\s+(?:saved\s+)?(?:notes?|memos?|recordings?\s+notes?)\b"
        r"|\bwhat\s+i\s+(?:asked\s+you\s+to\s+(?:remember|record|note)|told\s+you\s+to\s+(?:remember|note))\b",
        re.IGNORECASE,
    )),
    ("schedule", re.compile(r"\bmy\s+(?:schedule|calendar|agenda|appointments?|diary|plans)\b", re.IGNORECASE)),
    ("tasks", re.compile(r"\bmy\s+(?:tasks?|to-?dos?|to-?do\s+list|reminders?|alarms?)\b", re.IGNORECASE)),
)


def read_back_collections(text: str) -> "tuple[str, ...]":
    """Which kept collections a read-back request names, in the order they
    were said - empty when it is not a read-back request at all."""
    working = str(text or "")
    lowered = " ".join(working.lower().split())
    if not _READ_BACK_VERB.search(working) or _READ_BACK_FILE_WORDS.search(working):
        return ()
    reading_shape = (
        _marker_present(working, _READ_ALOUD_MARKERS)
        or re.search(r"\bread\s+(?:\w+\s+){0,3}back\b|\bread\s+back\b", lowered)
        or re.search(r"\b(?:go\s+over|run\s+through|recite)\b", lowered)
    )
    if not reading_shape:
        return ()
    found = []
    for name, pattern in _READ_BACK_COLLECTIONS:
        match = pattern.search(working)
        if match is not None:
            found.append((match.start(), name))
    return tuple(name for _position, name in sorted(found))


def read_back_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    return bool(read_back_collections(task.description))


def _kept_notes(request: CapabilityRequest) -> "list[str]":
    from src.services.memory.session import RETAINED_KIND, RETAINED_SOURCE

    notes = [
        record for record in request.recall(kind=RETAINED_KIND, limit=200)
        if (record.context or {}).get("source") == RETAINED_SOURCE
    ]
    notes.extend(request.recall(kind=MemoryCategory.RECORDING.value, limit=200))
    notes.sort(key=lambda record: str(getattr(record, "created_at", "") or ""))
    return [record.content for record in notes]


def read_back(request: CapabilityRequest) -> TaskResult:
    """Read the named kept collections back in full."""
    if request.recall is None:
        return TaskResult.failure(
            request.task, request.own_name, "I don't have a way to read back what I keep for you right now.",
        )
    from dataclasses import replace as _replace

    text = request.task.description
    sections = []
    for name in read_back_collections(text):
        if name == "notes":
            notes = _kept_notes(request)
            body = "\n".join(f"- {note}" for note in notes) if notes else "You haven't asked me to note anything yet."
            sections.append(("Your notes", body))
        elif name == "schedule":
            answer = _calendar_retrieve(request, text)
            sections.append(("Your schedule", answer.output or answer.error or ""))
        elif name == "tasks":
            # Spoken, so no internal ids; a reminder says when it is due.
            pending = request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=50)
            if not pending:
                plain = _replace(request, task=Task(description="what are my tasks?"))
                answer = list_tasks(plain)
                sections.append(("Your tasks", answer.output or answer.error or ""))
                continue
            lines = []
            for record in pending:
                due = (record.context or {}).get("due_at")
                when = ""
                if due:
                    try:
                        from datetime import datetime as _datetime

                        when = " - at " + _datetime.fromisoformat(str(due)).strftime("%H:%M on %A %d %B")
                    except ValueError:
                        when = ""
                lines.append(f"- {record.content}{when}")
            sections.append(("Your tasks", "\n".join(lines)))
    if not sections:
        return TaskResult.failure(request.task, request.own_name, "Read back what, exactly - your notes, schedule or tasks?")
    if len(sections) == 1:
        spoken = f"{sections[0][0]}:\n\n{sections[0][1]}"
    else:
        spoken = "\n\n".join(f"{title}:\n{body}" for title, body in sections)
    return TaskResult.success(
        request.task, request.own_name, spoken,
        payload={
            "handled_by": "read_back",
            "collections": tuple(name for name in read_back_collections(text)),
            "speak_in_full": True,
        },
    )


# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 9 (COACH-01): a ruthless personal coach - Alfred's own
# capability, answered from the goals, open tasks and missed reminders he
# actually keeps. No invented progress, no pep talk standing in for facts.
# ---------------------------------------------------------------------------

_COACH_ASK = re.compile(
    r"\bcoach\s+me\b|\bhold\s+me\s+(?:to\s+account|accountable)\b|\bkeep\s+me\s+accountable\b"
    r"|\bhow\s+am\s+i\s+doing\s+(?:on|with)\s+my\s+(?:goals?|tasks?|plans?|resolutions?)\b"
    r"|\bam\s+i\s+on\s+track\b|\bcheck\s+(?:in\s+)?on\s+my\s+(?:goals?|progress)\b"
    r"|\bwhat\s+have\s+i\s+been\s+putting\s+off\b|\bwhat\s+am\s+i\s+avoiding\b",
    re.IGNORECASE,
)
_COACH_STYLE = re.compile(
    r"\bbe\s+(?:my\s+)?(?:a\s+)?(?P<style>ruthless|brutal|tough|strict|harsh|gentle|kind|soft)\s+(?:with\s+me|coach)\b"
    r"|\bbe\s+(?P<harder>harder|tougher|stricter)\s+on\s+me\b|\bgo\s+(?P<easy>easy|easier)\s+on\s+me\b"
    r"|\bdon'?t\s+(?P<nosugar>sugar[-\s]?coat)\b",
    re.IGNORECASE,
)
_GOAL_TOPIC = re.compile(r"\b(?:goal|aim|ambition|resolution|target|plan|objective|dream)s?\b", re.IGNORECASE)
COACH_STYLE_TOPIC = "coaching style"
_RUTHLESS_WORDS = ("ruthless", "brutal", "tough", "strict", "harsh", "harder", "tougher", "stricter", "no sugar")


def coach_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    from src.services.memory.coaching import parse_non_negotiables, progress_report_shape, review_intent

    text = str(task.description or "")
    return bool(
        _COACH_ASK.search(text) or _COACH_STYLE.search(text)
        or parse_non_negotiables(text) or review_intent(text) or progress_report_shape(text)
    )


def _requested_style(text: str) -> "str | None":
    match = _COACH_STYLE.search(text)
    if match is None:
        return None
    if match.group("easy") or (match.group("style") or "").lower() in ("gentle", "kind", "soft"):
        return "gentle"
    return "ruthless"


def _days_open(record, today) -> int:
    created = getattr(record, "created_at", None)
    try:
        return max(0, (today - created.date()).days)
    except (AttributeError, TypeError):
        return 0

# ---------------------------------------------------------------------------
# Whole-ecosystem campaign: the coach's weekly loop - non-negotiables, what was
# actually done, an honest graded review with the two lowest areas named, and
# a recurring review reminder - all in the canonical personal-context store.
# ---------------------------------------------------------------------------


def _personal_records(request: CapabilityRequest) -> "tuple":
    try:
        return all_current_personal_context(recall=request.recall, limit=600)
    except Exception as exc:  # noqa: BLE001 - an unreadable store coaches from nothing, honestly
        logger.warning("could not read personal context for coaching: %s", exc)
        return ()


def _retain_candidate(request: CapabilityRequest, candidate: PersonalContextCandidate) -> bool:
    if request.retain is None:
        return False
    return request.retain(build_personal_context_record(candidate, agent_name=request.own_name)).allowed


def _pending_tasks(request: CapabilityRequest) -> "list":
    try:
        return list(request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=300))
    except Exception:  # noqa: BLE001
        return []


def _due_of(record) -> "datetime | None":
    due = (record.context or {}).get("due_at")
    if not due:
        return None
    try:
        return datetime.fromisoformat(str(due))
    except ValueError:
        return None


def _schedule_review_reminder(request: CapabilityRequest, now) -> "datetime | None":
    """Keep exactly one pending weekly-review reminder ahead of now - completing
    any that have come due - so the review recurs through the same proactive
    reminder path every other reminder already reaches the person by."""
    from src.services.memory.coaching import REVIEW_REMINDER_TITLE, next_review_moment

    if request.retain is None or request.forget is None:
        return None
    upcoming = None
    for record in _pending_tasks(request):
        if record.content != REVIEW_REMINDER_TITLE:
            continue
        due = _due_of(record)
        if due is not None and due > now:
            upcoming = due if upcoming is None or due < upcoming else upcoming
            continue
        if request.forget(record.id):
            request.retain(dataclass_replace(
                record, tags={"task", "task:complete", "reminder"},
                context={**(record.context or {}), "status": "complete"},
            ))
    if upcoming is not None:
        return upcoming
    moment = next_review_moment(now)
    made = build_task_record("", agent_name=request.own_name, reminder=(REVIEW_REMINDER_TITLE, moment))
    return moment if made is not None and request.retain(made).allowed else None


def _set_non_negotiables(request: CapabilityRequest, commitments, now, style: str) -> TaskResult:
    from src.services.memory.coaching import commitment_candidate

    kept = [c for c in commitments if _retain_candidate(request, commitment_candidate(c))]
    if not kept:
        return TaskResult.failure(request.task, request.own_name, "I couldn't record those non-negotiables.")
    review_at = _schedule_review_reminder(request, now)
    listed = "; ".join(c.describe for c in kept)
    when = f" I'll review your week every Sunday at 18:00 - the first on {review_at:%A %d %B}." if review_at else ""
    hold = (
        "No excuses accepted - report what you actually did, and I'll grade it." if style == "ruthless"
        else "Tell me what you actually do as the week goes (\"this week I exercised twice\") and I'll grade it honestly."
    )
    return TaskResult.success(
        request.task, request.own_name,
        f"Your weekly non-negotiables: {listed}.{when} {hold}",
        payload={"handled_by": "coach", "action": "non_negotiables", "count": len(kept)},
    )


def _record_progress(request: CapabilityRequest, commitments, today, style: str) -> "TaskResult | None":
    from src.services.memory.coaching import AT_MOST, grade, parse_progress, progress_candidate, week_id

    reported = parse_progress(request.task.description, commitments)
    if not reported:
        return None
    week = week_id(today)
    lines = []
    for commitment in commitments:
        if commitment.area not in reported:
            continue
        count = reported[commitment.area]
        _retain_candidate(request, progress_candidate(week, commitment.area, count))
        scored = grade(commitment, count)
        if commitment.kind == AT_MOST:
            state = "within the limit" if count <= commitment.target else f"{count - commitment.target} over your limit"
        else:
            state = "done" if count >= commitment.target else f"{commitment.target - count} still to do"
        lines.append(f"- {commitment.area}: {count} ({state}, {scored.score:.0f}/10 so far)")
    missing = [c.area for c in commitments if c.area not in reported]
    if missing:
        lines.append(
            ("Nothing reported for: " if style != "ruthless" else "You said nothing about: ")
            + ", ".join(missing) + (" - silence counts as zero." if style == "ruthless" else ".")
        )
    return TaskResult.success(
        request.task, request.own_name, f"Logged for {week}:\n" + "\n".join(lines),
        payload={"handled_by": "coach", "action": "progress", "areas": sorted(reported)},
    )


_CORRECTIVE = {
    "ruthless": {
        "short": "You committed to {target} and did {done}. That is {gap} you chose to skip. Book {gap} session(s) into next week's calendar tonight - specific day, specific time - or drop the commitment honestly.",
        "over": "Your limit was {target}; you went to {done}. The decision happens before the moment - remove the option before temptation, don't negotiate with it.",
        "silent": "You reported nothing. No report is no progress - report it or admit it didn't happen.",
    },
    "direct": {
        "short": "{done} of {target}. Put {gap} session(s) on specific days next week and tell me when each is done.",
        "over": "{done} against a limit of {target}. Decide in advance how you'll avoid it next time.",
        "silent": "Nothing reported - tell me what actually happened so this can be graded.",
    },
    "gentle": {
        "short": "{done} of {target} - a start. Could you pick {gap} specific time(s) next week for it?",
        "over": "{done} against a limit of {target}. What made it hard, and what would make next week easier?",
        "silent": "I haven't heard how this went - how did it actually go?",
    },
}


def _weekly_review(request: CapabilityRequest, records, commitments, now, today, style: str) -> TaskResult:
    from src.services.memory.coaching import (
        AT_MOST, AreaGrade, grade, previous_scores, progress_for_week, review_candidate, week_id,
    )

    week = week_id(today)
    done = progress_for_week(records, week)
    grades = [grade(c, done.get(c.area)) for c in commitments]
    pending = _pending_tasks(request)
    missed = [r for r in pending if (_due_of(r) is not None and _due_of(r) < now and r.content != "weekly review with your coach")]
    stale = [r for r in pending if not (r.context or {}).get("due_at") and _days_open(r, today) >= 7]
    if missed or stale:
        grades.append(AreaGrade(
            "following through on tasks", max(0.0, 10.0 - 2.5 * (len(missed) + len(stale))),
            f"{len(missed)} missed reminder(s), {len(stale)} task(s) open a week or more",
        ))
    if not grades:
        return TaskResult.success(
            request.task, request.own_name,
            "There's nothing to review yet - set your weekly non-negotiables first "
            "(\"my weekly non-negotiables are: exercise 4 times, read 3 times, no takeaway\").",
            payload={"handled_by": "coach", "action": "review", "areas": 0},
        )
    previous = previous_scores(records, week)
    lines = [f"Weekly review, {week} ({style}):"]
    for scored in grades:
        was = previous.get(scored.area)
        trend = f"; last week {was:.1f}" if was is not None else ""
        lines.append(f"- {scored.area}: {scored.score:.1f}/10 ({scored.letter}) - {scored.detail}{trend}")
    average = sum(g.score for g in grades) / len(grades)
    lines.append(f"Overall: {average:.1f}/10.")
    lowest = sorted(grades, key=lambda g: g.score)[:2]
    lines.append("Your two lowest areas: " + " and ".join(f"{g.area} ({g.score:.1f})" for g in lowest) + ".")
    words = _CORRECTIVE.get(style, _CORRECTIVE["direct"])
    by_area = {c.area: c for c in commitments}
    for scored in lowest:
        commitment = by_area.get(scored.area)
        if commitment is None:
            lines.append(f"- {scored.area}: clear the missed and stale items first - finish them or delete them honestly.")
            continue
        count = done.get(commitment.area)
        if count is None:
            advice = words["silent"]
        elif commitment.kind == AT_MOST:
            advice = words["over"]
        else:
            advice = words["short"]
        lines.append("- " + commitment.area + ": " + advice.format(
            target=commitment.target, done=count if count is not None else 0,
            gap=max(0, commitment.target - (count or 0)),
        ))
    goals = [item for item in records if item.status == STATUS_CURRENT and item.category not in ("commitment", "progress", "review", "goal_link") and _GOAL_TOPIC.search(item.topic)]
    from src.services.memory.coaching import stems as _coach_stems

    tracked = set().union(*(_coach_stems(c.area) for c in commitments)) if commitments else set()
    tracked |= set().union(*(_coach_stems(r.content) for r in pending)) if pending else set()
    for goal in goals[:5]:
        if not (_coach_stems(goal.value or goal.content) & tracked):
            lines.append(f"Nothing you track this week visibly moves your goal: {describe_for_user(goal).rstrip('.')}.")
    if style == "ruthless" and average < 7:
        lines.append("That is not a good week. Don't explain it - fix the two lowest areas first.")
    _retain_candidate(request, review_candidate(week, grades))
    next_review = _schedule_review_reminder(request, now)
    if next_review is not None:
        lines.append(f"Next review: {next_review:%A %d %B, %H:%M}.")
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={
            "handled_by": "coach", "action": "review", "week": week, "average": round(average, 1),
            "lowest": [g.area for g in lowest], "scores": {g.area: round(g.score, 1) for g in grades},
        },
    )


def coach(request: CapabilityRequest) -> TaskResult:
    """Coach the person from the facts Alfred keeps, in their chosen style."""
    if request.recall is None:
        return TaskResult.failure(request.task, request.own_name, "I can't see your goals or tasks right now.")
    text = request.task.description
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    now = calendar.now()
    today = calendar.today()

    context = all_current_personal_context(recall=request.recall, limit=200)
    style_record = next((item for item in context if item.topic_key == COACH_STYLE_TOPIC), None)
    style = "direct"
    if style_record is not None:
        stored = f"{style_record.value} {style_record.content}".lower()
        style = "ruthless" if any(word in stored for word in _RUTHLESS_WORDS) else ("gentle" if "gentle" in stored else "direct")

    from src.services.memory.coaching import commitments_from, parse_non_negotiables, progress_report_shape, review_intent

    stated = parse_non_negotiables(text)
    if stated:
        return _set_non_negotiables(request, stated, now, style)
    records = _personal_records(request)
    commitments = commitments_from(records)
    if progress_report_shape(text) and not review_intent(text):
        logged = _record_progress(request, commitments, today, style) if commitments else None
        if logged is not None:
            return logged
        if not (_COACH_ASK.search(text) or _COACH_STYLE.search(text)):
            # An ordinary statement about the person's life, not a report
            # against anything they committed to - learned as one.
            return personal_context(request)
    if review_intent(text):
        return _weekly_review(request, records, commitments, now, today, style)

    requested = _requested_style(text)
    changed_style = ""
    if requested is not None and requested != style:
        candidate = extract_personal_context_candidate(f"my coaching style is {requested}")
        if candidate is not None and request.retain is not None:
            verdict = request.retain(build_personal_context_record(candidate, agent_name=request.own_name))
            if verdict.allowed:
                style = requested
                changed_style = f"Done - I'll coach you {requested}ly from now on." if requested == "ruthless" else "Done - I'll go easier on you from now on."

    goals = [item for item in context if item.status == STATUS_CURRENT and item.category != "goal_link" and _GOAL_TOPIC.search(item.topic)]
    pending = list(request.recall(kind=MemoryCategory.TASK.value, tags={"task:pending"}, limit=200))
    missed = []
    for record in pending:
        due = (record.context or {}).get("due_at")
        if not due:
            continue
        try:
            moment = datetime.fromisoformat(str(due))
        except ValueError:
            continue
        if moment < now:
            missed.append((moment, record))
    stale = sorted(
        (record for record in pending if not (record.context or {}).get("due_at")),
        key=lambda record: _days_open(record, today), reverse=True,
    )

    lines = []
    if changed_style:
        lines.append(changed_style)
    if not goals and not pending:
        if style == "ruthless":
            lines.append("There's nothing to hold you to - you haven't told me a single goal or given me one task. Tell me what you're working towards (\"my goal this year is ...\") and what you'll do about it.")
        else:
            lines.append("I don't know your goals yet. Tell me one (\"my goal this year is ...\") and add a task for the next step, and I'll keep you honest about it.")
        return TaskResult.success(request.task, request.own_name, "\n".join(lines),
                                  payload={"handled_by": "coach", "style": style, "goals": 0, "pending": 0, "missed": 0})

    if goals:
        told = "; ".join(describe_for_user(item).strip().rstrip(".") for item in goals[:5])
        lines.append(f"What you told me you're working towards: {told}.")
    else:
        lines.append("You haven't told me a goal, so these tasks aren't tied to anything you said matters.")
    if missed:
        missed.sort(key=lambda pair: pair[0])
        names = ", ".join(f"'{record.content}' (due {moment.strftime('%H:%M %A %d %B')})" for moment, record in missed[:5])
        lines.append(
            (f"You missed {len(missed)} reminder(s): {names}. That's a decision you made, not something that happened to you."
             if style == "ruthless" else f"These reminders have passed and are still open: {names}.")
        )
    if stale:
        oldest = stale[0]
        age = _days_open(oldest, today)
        if age >= 1:
            lines.append(
                (f"'{oldest.content}' has been sitting open for {age} day(s). Do it today or admit you're not going to."
                 if style == "ruthless" else f"'{oldest.content}' has been open for {age} day(s) - is it still worth doing?")
            )
    open_count = len(pending)
    if open_count:
        lines.append(
            (f"{open_count} task(s) open. Pick the one that moves a goal forward and finish it before anything else."
             if style == "ruthless" else f"You have {open_count} open task(s). Which one moves a goal forward most?")
        )
    elif goals:
        lines.append(
            ("Not one task is working towards those goals. A goal without a next step is a wish - add one now."
             if style == "ruthless" else "There's no task for any of those goals yet - what's one small next step?")
        )
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={"handled_by": "coach", "style": style, "goals": len(goals), "pending": open_count, "missed": len(missed)},
    )


# ---------------------------------------------------------------------------
# Pre-Stage-9 Campaign 9 (TRN-01): translation, named and honest.
# ---------------------------------------------------------------------------

_LANGUAGE_WORD = r"(?P<language>[A-Za-z][A-Za-z\- ]{1,30}?)"
# The phrase runs to the *last* "to"/"in" (Phase 11 closure: a lazy phrase
# stopped at the first, so "translate add a task to buy milk into Spanish" was
# asked for a language called "buy milk into Spanish").
_TRANSLATE_PATTERNS = (
    re.compile(rf"^\s*(?:please\s+)?translate\s+(?P<phrase>.+)\s+(?:in)?to\s+{_LANGUAGE_WORD}\s*[?.!]*\s*$", re.IGNORECASE),
    re.compile(rf"^\s*how\s+(?:do|would)\s+(?:you|i)\s+say\s+(?P<phrase>.+)\s+in\s+{_LANGUAGE_WORD}\s*[?.!]*\s*$", re.IGNORECASE),
    re.compile(rf"^\s*what(?:'s|\s+is)\s+(?P<phrase>.+)\s+in\s+{_LANGUAGE_WORD}\s*[?.!]*\s*$", re.IGNORECASE),
    re.compile(rf"^\s*say\s+(?P<phrase>.+)\s+in\s+{_LANGUAGE_WORD}\s*[?.!]*\s*$", re.IGNORECASE),
)
# "What's the weather in Paris" names a place, not a language; a translation
# request's target is a language word, never a subject list - so the phrase
# must be quoted, or the verb must be translate/say.
_QUOTED = re.compile(r"^['\"\u2018\u201c].+['\"\u2019\u201d]$")


def translation_request(text: str) -> "tuple[str, str] | None":
    """``(phrase, target language)`` for a translation request, or None.

    The four patterns below are anchored on purpose - "what's the weather in
    Paris" names a place, not a language, and only anchoring keeps a
    translation request distinguishable from an ordinary question that
    happens to contain "in <word>". Anchoring alone, though, also refused
    every request that opens by naming who is being asked: "Nova, translate
    'the harvest begins at dawn' into Italian" matched nothing at all, so a
    specialist genuinely granted ``translate``, with real offline language
    packs installed, fell through to open conversation and answered "no
    language model is configured" - a registered, granted capability that
    was unreachable through the one way a person actually addresses a
    specialist (found at runtime, 2026-09-23, not by reading code; Alfred
    addressed by name was refused identically).

    ``_strip_vocative`` is exactly the instrument for that, and is already
    what ``_communication_shape``, ``select_voice`` and six other anchored
    shapes in this module use - "who was addressed is not what was asked".
    Applied here rather than duplicated: the negative cases stay negative
    (no leading "<name>," to remove), and nothing about the anchoring that
    keeps them negative changes.
    """
    # Read as said first, and only then without a leading "Name, " address -
    # the stripper is shape-based and, on its own, also swallowed an opening
    # "translate good morning, " as if it were a name (Phase 11 closure; the
    # same order extract_correction already reads in).
    said = " ".join(str(text or "").split()).strip()
    for working in dict.fromkeys((said, _strip_vocative(text).strip())):
        for index, pattern in enumerate(_TRANSLATE_PATTERNS):
            match = pattern.match(working)
            if match is None:
                continue
            phrase = match.group("phrase").strip()
            language = " ".join(match.group("language").split()).strip()
            if index == 2 and not _QUOTED.match(phrase):
                continue
            phrase = phrase.strip("'\"\u2018\u2019\u201c\u201d ")
            if phrase and language:
                return phrase, language
    return None


# A thing, never a language: a language's name does not open with a
# determiner or a possessive ("in the footage", "into my notes"). Grammar only.
_NOT_A_LANGUAGE = re.compile(r"^(?:the|this|that|these|those|a|an|my|your|our|his|her|their|its)\b", re.IGNORECASE)


def names_a_language_target(text: str) -> bool:
    """Whether a translation-shaped request's target could be a language -
    what lets translation outrank the other claims on its own phrase
    (``select_capability``). "Say what the dog is doing in the footage" has
    the "say ... in ..." shape, and its target is footage, not a language."""
    request = translation_request(text)
    return request is not None and not _NOT_A_LANGUAGE.match(request[1])


def translate_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    # "Translate this video into English" is media translation, which hears
    # the media first - never the words "this video" translated.
    if translate_media_intent(task, participants):
        return False
    return translation_request(task.description) is not None


_NAMED_SOURCE = re.compile(r"^(?P<phrase>.+?)\s+from\s+(?P<source>[A-Za-z][A-Za-z\- ]{1,30})\s*$", re.IGNORECASE)


_DOCUMENT_PHRASE = re.compile(
    r"^(?:(?:this|that|the|my)\s+(?:document|file|pdf|page|text|note|notes|handout|letter|book|chapter|"
    r"spreadsheet|presentation)|it|this|that)$",
    re.IGNORECASE,
)


def _document_to_translate(request: CapabilityRequest, phrase: str) -> "tuple[bool, tuple[str, str] | None]":
    """``(points_at_a_document, (title, text) | None)`` - a translation request
    whose phrase is a document reference translates that document's own words."""
    if not _DOCUMENT_PHRASE.match(" ".join(str(phrase or "").split())):
        return False, None
    found = resolve_content_sources(
        request.task.description, tools=request.tools, history=request.history,
        limit=1, fallback_latest=True, shared_files=request.shared_files,
    )
    if not found:
        return True, None
    described, units = found[0]
    text = "\n".join(unit.text for unit in units if unit.text.strip())[:8000]
    if not text.strip():
        return True, None
    return True, (str((described or {}).get("title") or "the document"), text)


def translate(request: CapabilityRequest) -> TaskResult:
    found = translation_request(request.task.description)
    if found is None:
        return TaskResult.failure(request.task, request.own_name, "Translate what, into which language?")
    phrase, language = found
    source = None
    named = _NAMED_SOURCE.match(phrase)
    if named is not None:
        phrase = named.group("phrase").strip().strip("'\"\u2018\u2019\u201c\u201d ")
        source = named.group("source").strip()
    # Universal content: "translate this document into Spanish" translates the
    # document's own words, never the words "this document".
    points_at_document, document = _document_to_translate(request, phrase)
    if points_at_document and document is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which document? Share it in this conversation (or name it with 'artifact <id>') and I'll translate it.",
        )
    title = None
    if document is not None:
        title, phrase = document
    # Post-Pre-Stage-9: offline translation first - deterministic, so Half Brain
    # translates as well as Full Brain. The Brain is asked only when no offline
    # engine can translate this pair.
    offline_problem = ""
    if request.translate is not None:
        try:
            result = request.translate(phrase, language, source)
        except LookupError as exc:
            offline_problem = str(exc)
        except Exception as exc:  # noqa: BLE001 - an engine failing is reported, never hidden
            offline_problem = f"offline translation failed ({type(exc).__name__}: {exc})"
        else:
            output = f"Translation of '{title}' into {language}:\n\n{result.text}" if title else result.text
            return TaskResult.success(
                request.task, request.own_name, output,
                payload={
                    "handled_by": "translate", "language": language, "phrase": phrase[:200], "engine": "offline",
                    **({"document": title} if title else {}),
                    "source": result.source, "target": result.target, "route": "->".join(result.route),
                },
            )
    decision = None
    if request.converse is not None:
        decision = request.converse(
            Task(description=(
                f"Translate the following into {language}. Reply with only the translation, nothing else.\n\n{phrase}"
            )),
            (),
        )
    if decision is None or not decision.is_decided or not decision.reason.strip():
        why = decision.reason if decision is not None else "no reasoning capability is configured"
        offline = offline_problem or (
            "offline translation is not enabled (in the Control Center: provider translation_provider use argos)"
        )
        return TaskResult.failure(
            request.task, request.own_name,
            f"I can't translate that into {language} right now - {offline.rstrip('.')}, and no language model "
            f"is available to do it instead ({why}). I won't guess at a translation.",
        )
    return TaskResult.success(
        request.task, request.own_name, decision.reason.strip(),
        payload={"handled_by": "translate", "language": language, "phrase": phrase},
    )


TRANSLATE = ManagerCapability(
    name="translate",
    capabilities=frozenset({"translate", "language"}),
    handle=translate,
    description=(
        "Translates a phrase into a named language through the Brain and speaks it through the voice path; "
        "honest when no language model is available."
    ),
    claims=translate_intent,
    carries_material=True,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Whole-ecosystem campaign: image intelligence beyond describing - one image
# transformed, enhanced or reconstructed by an open, governed set of
# techniques, and two images compared by measurement. Both work in Half Brain.
# ---------------------------------------------------------------------------

_IMAGE_NOUN = re.compile(r"\b(?:image|images|photo|photos|photograph|picture|pictures|pic|pics|screenshot|selfie|scan)\b", re.IGNORECASE)
# "an image of a cat", "a picture of the sea" names something to generate.
_IMAGE_OF_SUBJECT = re.compile(r"\b(?:an?|some)\s+(?:new\s+)?(?:image|photo|picture|pic|drawing)\s+of\b", re.IGNORECASE)
IMAGE_TOOL = "image_intelligence"


# A question about an image ("is this photo too dark?") asks for an answer, not
# an edit - unless it is a polite request ("could you crop this photo?").
_IMAGE_QUESTION = re.compile(
    r"^\s*(?:is|are|was|were|does|do|did|has|have|what|what's|whats|why|how|which|who|when|where|should)\b", re.IGNORECASE,
)
_IMAGE_EDIT_REQUEST = re.compile(
    r"\b(?:make|turn|change|convert|edit|transform|restyle|styli[sz]e|retouch|redo|give)\s+(?:this|the|my|that|our)\s+(?:\w+\s+)?"
    r"(?:image|photo|photograph|picture|pic|screenshot|selfie|scan)\b(?!\s+(?:of|about)\b)",
    re.IGNORECASE,
)


def image_transform_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    from src.providers.media.image_edits import parse_edit_instruction
    from src.providers.media.image_techniques import current_techniques, technique_for_request

    text = str(task.description or "")
    if not _IMAGE_NOUN.search(text) or _IMAGE_OF_SUBJECT.search(text) or _IMAGE_QUESTION.search(text):
        return False
    techniques = current_techniques()
    return (
        technique_for_request(text, techniques) is not None
        or bool(parse_edit_instruction(text, techniques).steps)
        # An edit asked of the image in words no operation reads ("make this
        # photo look like a watercolour") still belongs here - to be restated
        # by a Brain, or refused honestly - never to general conversation.
        or bool(_IMAGE_EDIT_REQUEST.search(text))
    )


def _image_edit_plan(request: CapabilityRequest) -> "tuple[dict | None, str | None]":
    """The edit plan for this request: read deterministically, and - only when
    part of it could not be read and a Brain is available - restated by the
    Brain in the edit vocabulary and read again the same way. Returns
    (plan, refusal)."""
    outcome = request.tools.invoke(IMAGE_TOOL, {"operation": "plan", "content": json.dumps({"instruction": request.task.description})})
    if not outcome.ok:
        return None, f"I couldn't read that as image edits: {outcome.error}"
    plan = json.loads(outcome.output)
    plan["planner"] = "deterministic"
    if plan["not_understood"] and request.converse is not None:
        notes = (
            "Restate the person's image-editing request as edits separated by ' ; ', in the order asked, using ONLY "
            "these forms (fill in the numbers, colours and quoted text the person asked for; never add an edit they did "
            "not ask for). If something they asked for cannot be expressed with these forms, include 'CANNOT: <that part>'.\n"
            + "\n".join(f"- {line}" for line in plan["vocabulary"]),
        )
        decision = request.converse(request.task, notes)
        restated = decision.reason.strip() if decision is not None and decision.is_decided else ""
        if restated and "CANNOT:" not in restated:
            again = request.tools.invoke(IMAGE_TOOL, {"operation": "plan", "content": json.dumps({"instruction": restated})})
            if again.ok:
                replanned = json.loads(again.output)
                if replanned["steps"] and not replanned["not_understood"]:
                    replanned["planner"] = decision.source
                    return replanned, None
    if plan["not_understood"]:
        understood = "; ".join(step["clause"] for step in plan["steps"])
        return None, (
            (f"I can do this part: {understood}. But I" if understood else "I")
            + " don't know how to " + "; ".join(repr(x) for x in plan["not_understood"])
            + " - nothing was changed. The edits I can make: " + " | ".join(plan["vocabulary"])
        )
    return plan, None


def image_transform(request: CapabilityRequest) -> TaskResult:
    """Edit the image the request points at as its instruction says - one
    named technique, or an ordered plan of parametric edits, each verified -
    saving the result as a new image derived from it."""
    if not request.tools.permits(IMAGE_TOOL):
        return TaskResult.failure(request.task, request.own_name, "Image techniques are not installed for me right now.")
    described, _path = _shared_media(request, ("image",))
    if described is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which image? Share it in this conversation (or name it with 'artifact <id>') and I'll work on it.",
        )
    plan, refusal = _image_edit_plan(request)
    if refusal is not None:
        return TaskResult.failure(request.task, request.own_name, refusal)
    if not (len(plan["steps"]) == 1 and plan["steps"][0]["op"] == "technique"):
        edited = request.tools.invoke(IMAGE_TOOL, {"operation": "edit", "content": json.dumps({
            "artifact_id": described["artifact_id"], "steps": plan["steps"],
        })})
        if not edited.ok:
            return TaskResult.failure(request.task, request.own_name, f"I couldn't edit the image: {edited.error}")
        made = json.loads(edited.output)
        done = "; ".join(f"{s['step']}. {s['detail']} (checked, {s['size'][0]}x{s['size'][1]})" for s in made["applied"])
        return TaskResult.success(
            request.task, request.own_name,
            (
                f"Artifact created: id={made['artifact_id']} type=image title={made['title']!r} png={made['rendition']} - "
                f"edited: {done}. Your original (artifact {made['source_id']}) is unchanged."
            ),
            payload={"handled_by": "image_transform", "technique": "edit", "planner": plan["planner"],
                     "edits": [s["op"] for s in made["applied"]], "artifact_id": made["artifact_id"], "source_id": made["source_id"]},
        )
    outcome = request.tools.invoke(
        IMAGE_TOOL,
        {"operation": "transform", "content": json.dumps({"artifact_id": described["artifact_id"], "request": request.task.description})},
    )
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't do that to the image: {outcome.error}")
    made = json.loads(outcome.output)
    return TaskResult.success(
        request.task, request.own_name,
        (
            f"Artifact created: id={made['artifact_id']} type=image title={made['title']!r} png={made['rendition']} - "
            f"{made['label']}: {made['detail']}. {made['width']}x{made['height']}. "
            f"Your original (artifact {made['source_id']}) is unchanged."
        ),
        payload={"handled_by": "image_transform", "technique": made["technique"], "purpose": made["purpose"],
                 "artifact_id": made["artifact_id"], "source_id": made["source_id"]},
    )


_COMPARE_ASK = re.compile(
    r"\b(?:compare|comparison|difference|differences|differ|different|same|match|matches|identical|changed)\b",
    re.IGNORECASE,
)
_TWO_IMAGES = re.compile(
    r"\b(?:these|those|both|two|2)\b|\breference\b|\boriginal\b|\bprevious\b|\bother\b|\bthe\s+first\b",
    re.IGNORECASE,
)


def image_comparison_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    return bool(_IMAGE_NOUN.search(text) and _COMPARE_ASK.search(text) and _TWO_IMAGES.search(text))


def image_comparison(request: CapabilityRequest) -> TaskResult:
    """Compare the two images a request points at - the newest shared image
    against the one before it, or against the one called the reference."""
    from src.manager.knowledge import resolve_shared_media_all

    if not request.tools.permits(IMAGE_TOOL):
        return TaskResult.failure(request.task, request.own_name, "Image comparison is not installed for me right now.")
    images = resolve_shared_media_all(
        request.task.description, tools=request.tools, history=request.history,
        shared_files=request.shared_files, kinds=("image",), limit=6,
    )
    if len(images) < 2:
        return TaskResult.failure(
            request.task, request.own_name,
            "I need two images to compare - share both in this conversation (or name them with 'artifact <id>').",
        )
    newest = images[0]
    if re.search(r"\breference\b", request.task.description, re.IGNORECASE):
        other = next((image for image in images[1:] if "reference" in str(image.get("title", "")).lower()), images[1])
    else:
        other = images[1]
    outcome = request.tools.invoke(
        IMAGE_TOOL, {"operation": "compare", "content": json.dumps({"first": other["artifact_id"], "second": newest["artifact_id"]})},
    )
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't compare them: {outcome.error}")
    compared = json.loads(outcome.output)
    return TaskResult.success(
        request.task, request.own_name, compared["text"],
        payload={"handled_by": "image_comparison", "verdict": compared["verdict"], "hash_distance": compared["hash_distance"]},
    )


IMAGE_TRANSFORM = ManagerCapability(
    name="image_transform",
    capabilities=frozenset({"image_transform"}),
    handle=image_transform,
    description=(
        "Transforms, enhances or reconstructs a real image (black and white, sharpen, denoise, enhance, upscale, "
        "deblur by deconvolution, or any technique added through Ecosystem Evolution) into a new derived image - "
        "the original untouched, nothing invented in the picture."
    ),
    claims=image_transform_intent,
    scoreable=False,
)

IMAGE_COMPARISON = ManagerCapability(
    name="image_comparison",
    completes_request=True,
    capabilities=frozenset({"image_comparison"}),
    handle=image_comparison,
    description=(
        "Compares two real images - identical bytes, perceptual structure, pixel difference and where they "
        "differ - including against a reference image, by measurement rather than a guess."
    ),
    claims=image_comparison_intent,
    scoreable=False,
)

# ---------------------------------------------------------------------------
# Whole-ecosystem campaign: personal guidance - life direction, something
# bothering the person, organising the week, what to do first, how goals and
# commitments connect - reasoned over the person's own canonical context, with
# a deterministic floor in Half Brain and the Brain given every source in Full.
# ---------------------------------------------------------------------------


def personal_guidance_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    from src.services.memory.guidance import guidance_kind

    return guidance_kind(task.description) is not None


def _guidance_sources(request: CapabilityRequest, now, today) -> dict:
    from src.services.memory.coaching import commitments_from, progress_for_week, week_id
    from src.services.memory.guidance import SITUATION_CATEGORY, DatedItem, OpenTask

    records = _personal_records(request)
    goals = [
        item for item in records
        if item.status == STATUS_CURRENT and item.category not in ("commitment", "progress", "review", "goal_link") and _GOAL_TOPIC.search(item.topic)
    ]
    situation = next((item for item in records if item.category == SITUATION_CATEGORY and item.status == STATUS_CURRENT), None)
    commitments = commitments_from(records)
    done = progress_for_week(records, week_id(today))
    tasks = []
    for record in _pending_tasks(request):
        if record.content == "weekly review with your coach":
            continue
        created = getattr(record, "created_at", None)
        tasks.append(OpenTask(title=record.content, created=created, due=_due_of(record)))
    items = []
    try:
        for item in all_current_calendar_items(recall=request.recall):
            if getattr(item, "status", "") == CALENDAR_STATUS_CANCELLED:
                continue
            for day in next_calendar_occurrences(item, today=today, count=7, horizon_days=7):
                when = item.start_time.strftime("%H:%M") + " " if item.start_time is not None else ""
                items.append(DatedItem(day=day, label=f"{when}{item.title}"))
    except Exception as exc:  # noqa: BLE001 - a calendar that cannot be read plans without it, and says so
        logger.warning("could not read calendar items for guidance: %s", exc)
    other = [
        item for item in records
        if item.status == STATUS_CURRENT and item not in goals and item is not situation
        and item.category not in ("commitment", "progress", "review")
    ]
    return {
        "records": records, "goals": goals, "situation": situation, "commitments": commitments, "done": done,
        "tasks": tasks, "items": items, "other": other,
    }


def _guidance_notes(sources: dict) -> "tuple[str, ...]":
    notes = []
    if sources["situation"] is not None:
        notes.append(f"What the person is dealing with: {sources['situation'].value or sources['situation'].content}")
    notes.extend(f"Goal: {describe_for_user(goal)}" for goal in sources["goals"][:6])
    notes.extend(f"Weekly non-negotiable: {c.describe} (done so far: {sources['done'].get(c.area, 0)})" for c in sources["commitments"])
    notes.extend(f"Open task: {t.title}" + (f" (due {t.due:%a %d %b %H:%M})" if t.due else "") for t in sources["tasks"][:10])
    notes.extend(f"Calendar: {i.day:%a %d %b} {i.label}" for i in sources["items"][:14])
    notes.extend(f"Known about them: {describe_for_user(item)}" for item in sources["other"][:8])
    return tuple(notes)


def personal_guidance(request: CapabilityRequest) -> TaskResult:
    from src.services.memory.guidance import (
        CRISIS, CRISIS_REPLY, GOALS, LIFE, PLAN, PRIORITY, SITUATION, TALK, TEACH_LINK,
        best_match, extract_situation, goal_connections, goal_link_candidate, guidance_kind, parse_goal_link,
        plan_week, priorities, taught_links, validated_suggestions,
    )

    text = request.task.description
    kind = guidance_kind(text)
    calendar = request.calendar if request.calendar is not None else Calendar.utc()
    now, today = calendar.now(), calendar.today()
    payload = {"handled_by": "personal_guidance", "kind": kind}

    if kind == CRISIS:
        return TaskResult.success(request.task, request.own_name, CRISIS_REPLY, payload=payload)

    if kind == SITUATION:
        candidate = extract_situation(text)
        if candidate is None or not _retain_candidate(request, candidate):
            return TaskResult.failure(request.task, request.own_name, "I couldn't keep that just now.")
        return TaskResult.success(
            request.task, request.own_name,
            f"I'll keep that in mind: {candidate.value}. When you want to work through it, say "
            "\"guide me through this\" - or tell me what you're weighing up.",
            payload={**payload, "stored": True},
        )

    sources = _guidance_sources(request, now, today)
    goal_texts = [goal.value or goal.content for goal in sources["goals"]]
    taught = taught_links(_personal_records(request))
    items = [c.describe for c in sources["commitments"]] + [t.title for t in sources["tasks"]]

    if kind == TEACH_LINK:
        thing, goal_phrase, negated = parse_goal_link(text)
        goal = best_match(goal_phrase, goal_texts)
        if goal is None:
            known = "; ".join(goal_texts) or "none yet - say \"my goal this year is ...\""
            return TaskResult.failure(
                request.task, request.own_name,
                f"Which goal is that? I couldn't tell which one '{goal_phrase}' means. Your goals: {known}.",
            )
        item = best_match(thing, items) or thing
        if not _retain_candidate(request, goal_link_candidate(item, goal, negated)):
            return TaskResult.failure(request.task, request.own_name, "I couldn't keep that just now.")
        tracked = "" if item in items else " (it isn't something you track yet - tell me as a commitment or task and I'll count it)"
        return TaskResult.success(
            request.task, request.own_name,
            f"Noted: {item} {'does not serve' if negated else 'serves'} your goal: {goal}.{tracked}",
            payload={**payload, "item": item, "goal": goal, "negated": negated},
        )

    if kind in (PLAN, PRIORITY):
        ranked = priorities(now=now, tasks=sources["tasks"], goals=goal_texts, taught=taught)
        plan = plan_week(
            today=today, now=now, items=sources["items"], tasks=sources["tasks"],
            commitments=sources["commitments"], done=sources["done"], goals=goal_texts,
        )
        if not (ranked or sources["items"] or sources["commitments"]):
            return TaskResult.failure(
                request.task, request.own_name,
                "I have nothing to plan with yet - no tasks, calendar items or weekly commitments. "
                "Tell me what's on (\"I have yoga every Wednesday at 7am\", \"add a task to ...\") and I'll lay the week out.",
            )
        lines = []
        if ranked:
            first, why = ranked[0]
            lines.append(f"Do first: {first} - {why}.")
            if len(ranked) > 1 and kind == PRIORITY:
                lines.extend(f"Then: {title} - {reason}." for title, reason in ranked[1:4])
        if kind == PRIORITY and sources["items"]:
            fixed = sorted(sources["items"], key=lambda item: item.day)[:4]
            lines.append("Fixed in your calendar around that: " + "; ".join(f"{i.day:%a %d %b} {i.label}" for i in fixed) + ".")
        if kind == PLAN or not ranked:
            lines.append("Your week, from your calendar, reminders, tasks and non-negotiables:")
            lines.extend(f"- {line}" for line in plan)
        floor = "\n".join(lines)
        return _with_brain_if_available(request, sources, floor, payload, instruction=(
            "Help the person plan their week. Use only the listed sources. Keep the fixed times fixed; be specific "
            "about days; say which goal each block serves."
        ))

    if kind == GOALS:
        if not sources["goals"]:
            return TaskResult.success(
                request.task, request.own_name,
                "You haven't told me a goal yet, so there's nothing to connect them to. "
                "Say \"my goal this year is ...\".", payload={**payload, "goals": 0},
            )
        connections = goal_connections(goal_texts, items, taught)
        lines = []
        for record, goal in list(zip(sources["goals"], goal_texts))[:6]:
            said = describe_for_user(record).rstrip(".")
            serving = connections.get(goal, [])
            if serving:
                lines.append(f"- {said}: served by " + ", ".join(
                    f"{link.item} ({'you told me' if link.evidence == 'told' else link.evidence})" for link in serving) + ".")
            else:
                lines.append(f"- {said}: nothing you track is linked to it yet.")
        linked_items = {link.item.lower() for links in connections.values() for link in links}
        unlinked = [item for item in items if item.lower() not in linked_items]
        suggestions = []
        if request.converse is not None and unlinked and goal_texts:
            decision = request.converse(request.task, (
                "Propose which of the person's commitments/tasks plausibly serve which of their goals, using ONLY the "
                "exact names below, written as 'item => goal' separated by ' ; '. Propose nothing when no link is "
                "plausible.",
                "Goals: " + " | ".join(goal_texts),
                "Commitments and tasks not yet linked: " + " | ".join(unlinked),
            ))
            if decision is not None and decision.is_decided:
                suggestions = validated_suggestions(decision.reason, goal_texts, unlinked, connections)
                payload = {**payload, "brain": decision.source}
        if suggestions:
            lines.append("Possible links (suggested, not confirmed - tell me if they're right): " + "; ".join(
                f"{s.item} for {s.goal}" for s in suggestions) + ".")
        still = [item for item in unlinked if item not in {s.item for s in suggestions}]
        if still:
            lines.append("Not linked to any goal: " + ", ".join(still) + ".")
        if still or any(not connections.get(goal) for goal in goal_texts):
            example_goal = next((g for g in goal_texts if not connections.get(g)), goal_texts[0])
            example_item = still[0] if still else (items[0] if items else "your running")
            said_goal = f"goal {example_goal}" if example_goal.lower().startswith("to ") else f"{example_goal} goal"
            lines.append(f"If one serves a goal, tell me - e.g. \"{example_item} is for my {said_goal}\".")
        floor = "How your goals, commitments and tasks connect:\n" + "\n".join(lines)
        counts = {"links": sum(len(v) for v in connections.values()), "suggested": len(suggestions),
                  "told": sum(1 for v in connections.values() for link in v if link.evidence == "told")}
        # With a Brain, its advice follows the evidence - the labelled links stay
        # exactly as found, never rewritten into prose that hides their source.
        advised = _with_brain_if_available(request, sources, floor, {**payload, **counts}, instruction=(
            "The person's goal links are listed with their evidence. Advise one concrete change for each goal nothing "
            "serves, using only the listed sources. Do not restate the links."
        ))
        if advised.succeeded and (advised.payload or {}).get("brain") not in (None, "deterministic"):
            return TaskResult.success(request.task, request.own_name, floor + "\n\n" + advised.output, payload=advised.payload)
        return TaskResult.success(request.task, request.own_name, floor, payload={**payload, **counts, "brain": "deterministic"})

    # LIFE or TALK - guided reflection grounded in what is known.
    known = []
    if sources["situation"] is not None:
        known.append(f"what you're dealing with: {sources['situation'].value}")
    known.extend(describe_for_user(goal).rstrip(".") for goal in sources["goals"][:3])
    known.extend(describe_for_user(item).rstrip(".") for item in sources["other"][:3])
    if kind == TALK:
        opening = "I'm listening. Take your time - what's going on?"
        questions = (
            "What happened, in your own words?",
            "What part of it is weighing on you most?",
            "Is there a decision in it, or does it just need saying out loud?",
        )
    else:
        opening = "Let's work through it together, one piece at a time."
        questions = (
            "What would you want to be true a year from now, if nothing stopped you?",
            "What are you doing now that you'd keep, and what would you stop?",
            "What is one small step you could take this week - and when exactly?",
        )
    lines = [opening]
    if known:
        lines.append("What I already know that might matter: " + "; ".join(known) + ".")
    lines.append("A few questions to start with:")
    lines.extend(f"- {q}" for q in questions)
    lines.append("Answer any of them and I'll keep track - say \"remember what I'm dealing with: ...\" to keep the situation itself.")
    floor = "\n".join(lines)
    return _with_brain_if_available(request, sources, floor, payload, instruction=(
        "Act as a thoughtful personal guide (not a therapist or medical professional). Listen, reflect back what the "
        "person said, use what is known about their situation and goals, and ask at most three focused questions or "
        "propose one concrete next step."
    ))


def _with_brain_if_available(request: CapabilityRequest, sources: dict, floor: str, payload: dict, *, instruction: str) -> TaskResult:
    """The deterministic answer, improved by the Brain when one can reason -
    given every source the floor used, and the floor itself - never replaced
    by a guess when it cannot."""
    if request.converse is not None:
        notes = (f"Guidance instruction: {instruction}",) + _guidance_notes(sources) + (
            f"A deterministic draft built from those sources: {floor}",
        )
        decision = request.converse(request.task, notes)
        if decision is not None and decision.is_decided and decision.reason.strip():
            return TaskResult.success(
                request.task, request.own_name, decision.reason.strip(),
                payload={**payload, "brain": decision.source, "sources": len(_guidance_notes(sources))},
            )
    return TaskResult.success(
        request.task, request.own_name, floor,
        payload={**payload, "brain": "deterministic", "sources": len(_guidance_notes(sources))},
    )


PERSONAL_GUIDANCE = ManagerCapability(
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="personal_guidance",
    self_contained=True,
    self_contained_reason=(
        "carries the crisis path select_capability already routes ahead of everything else; it must never leave the Manager"
    ),
    completes_request=True,
    capabilities=frozenset({"guidance", "plan_week"}),
    handle=personal_guidance,
    description=(
        "Guides the person through what they're dealing with, their direction, their week and what to do first - "
        "from their own situation, goals, commitments, tasks and calendar, with a safe, honest floor in Half Brain."
    ),
    claims=personal_guidance_intent,
    scoreable=False,
)

# ---------------------------------------------------------------------------
# Campaign 2: media translated and routed to outputs - "translate this video
# into English subtitles", "give me French subtitles for this recording",
# "translate this audio into Spanish and read it to me". Any language the
# installed engine translates; the outputs are an open registry.
# ---------------------------------------------------------------------------

_MEDIA_NOUN = re.compile(
    r"\b(?:video|videos|recording|recordings|audio|clip|movie|film|episode|show|footage|voice\s+note|podcast|interview)\b",
    re.IGNORECASE,
)
_SUBTITLE_WORDS = re.compile(r"\b(?:subtitle[sd]?|caption(?:s|ed)?|closed\s+captions?)\b", re.IGNORECASE)
_TRANSLATE_WORD = re.compile(r"\btranslat(?:e|ed|ion)\b|\bdub\b", re.IGNORECASE)
_INTO_LANGUAGE = re.compile(r"\b(?:in|into|to)\s+(?P<language>[A-Za-z][A-Za-z\-]{2,20})\b", re.IGNORECASE)
_NOT_A_TARGET = frozenset({"the", "this", "that", "my", "your", "me", "text", "words", "subtitles", "captions", "audio", "speech"})
_LANGUAGE_ADJECTIVE = re.compile(r"\b(?P<language>[A-Za-z][A-Za-z\-]{2,20})\s+(?:subtitles?|captions?)\b", re.IGNORECASE)


def _media_target_language(text: str) -> "str | None":
    from src.services.identity.voice_configuration import canonical_language

    for pattern in (_LANGUAGE_ADJECTIVE, _INTO_LANGUAGE):
        for match in pattern.finditer(text):
            found = canonical_language(match.group("language"))
            if found:
                return found
    # A language no table here knows yet ("into Swahili") is still the target
    # named - a capitalised word after into/to - so the request reaches the
    # translator, which says honestly whether it can do it.
    for match in _INTO_LANGUAGE.finditer(text):
        word = match.group("language")
        if word[:1].isupper() and word.lower() not in _NOT_A_TARGET:
            return word
    return None


def translate_media_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    if not _MEDIA_NOUN.search(text):
        return False
    if _SUBTITLE_WORDS.search(text):
        # Captions with no language named are captions in the language spoken.
        return True
    return bool(_TRANSLATE_WORD.search(text)) and _media_target_language(text) is not None


def _requested_translation_outputs(text: str) -> "list[str]":
    outputs = []
    if _SUBTITLE_WORDS.search(text) or not re.search(r"\b(?:read|speak|say|dub|voice|out\s+loud|aloud)\b", text, re.IGNORECASE):
        outputs += ["subtitles"]
        if re.search(r"\b(?:video|movie|film|episode|show|footage|clip)\b", text, re.IGNORECASE):
            outputs += ["captioned_video"]
    if re.search(r"\b(?:read|speak|say|dub|voice|out\s+loud|aloud)\b", text, re.IGNORECASE):
        outputs += ["audio"]
    outputs += ["display"]
    return list(dict.fromkeys(outputs))


def translate_media(request: CapabilityRequest) -> TaskResult:
    if not request.tools.permits("translation_output"):
        return TaskResult.failure(request.task, request.own_name, "Media translation is not installed for me right now.")
    text = request.task.description
    target = _media_target_language(text)
    described, _path = _shared_media(request, ("video", "audio", "voice_recording"))
    if described is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which video or recording? Share it in this conversation (or name it with 'artifact <id>') and I'll translate it.",
        )
    outcome = request.tools.invoke("translation_output", {"operation": "translate_media", "content": json.dumps({
        "artifact_id": described["artifact_id"], "target": target, "outputs": _requested_translation_outputs(text),
    })})
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't translate that: {outcome.error}")
    result = json.loads(outcome.output)
    if target is None or result["source_language"] == result["target_language"]:
        lines = [f"Captioned '{result['source_title']}' in the language spoken ({result['source_language']}):"]
    else:
        lines = [f"Translated '{result['source_title']}' from {result['source_language']} into {result['target_language']}:"]
    lines.extend(f"  {int(l['start']) // 60}:{int(l['start']) % 60:02d} {l['text']}" for l in result["lines"][:8])
    for name, produced in result["outputs"].items():
        if "artifact_id" in produced:
            lines.append(f"- {name}: artifact {produced['artifact_id']} (artifact/{produced['artifact_id']}.{produced['format']})")
        elif "lines" in produced:
            lines.append(f"- {name}: shown on the live display ({produced['lines']} line(s))")
        else:
            lines.append(f"- {name}: {produced.get('skipped') or produced.get('error')}")
    return TaskResult.success(
        request.task, request.own_name, "\n".join(lines),
        payload={"handled_by": "translate_media", "target": result["target_language"], "source": result["source_language"],
                 "outputs": result["outputs"]},
    )


# ---------------------------------------------------------------------------
# Campaign 2: genuine 3D content - "make a 3D model of X", described as parts
# by the person, or restated as parts by a Brain, assembled into a real glTF
# mesh and checked to be three-dimensional before it is saved.
# ---------------------------------------------------------------------------

# What a person calls the 3D thing they want. "view", "map", "visualisation"
# were missing (2026-09-21: "an interactive 3D view of a water molecule" and
# "an interactive 3D map of ..." fell through to open conversation).
_MODEL3D_NOUNS = (
    r"(?:model|object|scene|version|render(?:ing)?|mesh|sculpture|figure|shape|print|asset"
    r"|view|map|visuali[sz]ation|representation|replica|reconstruction)"
)
# Phase 11 final gate: up to four descriptors may stand before "3D" ("design
# an interactive, animated 3D diagram of ...") - the request is still 3D. The
# verb is what makes it a request: "3D diagram" alone is also the name of a
# workspace ("go to the 3D diagram workspace"), so it is not a noun above.
_MODEL3D_REQUEST = re.compile(
    rf"\b(?:3d|3-d|three[\s-]dimensional)\s+{_MODEL3D_NOUNS}\b"
    r"|\b(?:model|build|make|create|sculpt|render)\b[^.?!]{0,60}?\bin\s+3d\b"
    r"|\b(?:make|build|create|design|give\s+me|generate|sculpt)\s+(?:me\s+)?(?:an?\s+|the\s+)?(?:[\w,'-]+\s+){0,4}?3d\s+\w+",
    re.IGNORECASE,
)
_MODEL3D_SUBJECT = re.compile(
    rf"\b(?:3d|3-d|three[\s-]dimensional)\s+{_MODEL3D_NOUNS}\s+of\s+(?P<a>[^:;.?!]{{2,80}})"
    r"|\b(?:make|build|create|design|give\s+me|generate|sculpt)\s+(?:me\s+)?(?:an?\s+|the\s+)?(?:[\w,'-]+\s+){0,4}?3d\s+(?P<b>[^:;.?!]{2,80})"
    r"|\b(?:model|build|make|create|sculpt|render)\s+(?P<c>[^:;.?!]{2,60}?)\s+in\s+3d\b",
    re.IGNORECASE,
)


def create_3d_model_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    return bool(_MODEL3D_REQUEST.search(text)) and not re.match(r"^\s*(?:what|why|how|is|are|does|do)\b", text, re.IGNORECASE)


# Interaction a person asks of a 3D result. T-3D-02 (2026-09-21): the 3D
# workspace only showed a model turning on its own, and camera control,
# fly-throughs and lighting modes were told to the person as not built rather
# than silently dropped. They are now real (the workspace's model viewer:
# drag-orbit, scroll-zoom, a fly-through toggle, a day/night lighting toggle -
# see ui/index.html's mountStage opts.interactive) - the note below now
# points there truthfully instead of disclaiming it. What remains outside the
# 3D workspace's own reach - a name typed into a request choosing where the
# view opens, or a scripted multi-waypoint tour with stops - is still named
# honestly rather than implied.
_MODEL3D_INTERACTION = re.compile(
    r"\b(?:interactive|rotate|rotating|spin|zoom|orbit|pan|fly[\s-]?(?:through|over|by)|walk[\s-]?through|explore"
    r"|day\s*(?:and|/|&)\s*night|night\s+mode|day\s+mode|click\s+on|tour)\b",
    re.IGNORECASE,
)


def create_3d_model(request: CapabilityRequest) -> TaskResult:
    """Genuine 3D content for any subject (see ``_build_3d_model``), with an
    honest word about the real interaction the 3D workspace's viewer offers
    once it is open."""
    result = _build_3d_model(request)
    if not _MODEL3D_INTERACTION.search(str(request.task.description or "")):
        return result
    note = (
        " About the interaction you asked for: open the 3D workspace to view it - drag to orbit the camera around it,"
        " scroll to zoom, toggle a fly-through for a hands-free turn around it, and switch between day and night"
        " lighting. It also turns on its own until you take the camera; you can download it as .glb. A scripted tour"
        " with its own stops isn't built."
    )
    from dataclasses import replace as _replace

    if result.error:
        return _replace(result, error=result.error + note)
    return _replace(result, output=result.output + note)


def _build_3d_model(request: CapabilityRequest) -> TaskResult:
    """Genuine 3D content for any subject: the parts the person described, or -
    with a Brain - the subject restated as parts in the same grammar and read by
    the same parser. Nothing is saved that is not a valid, three-dimensional
    glTF mesh; without parts or a Brain the request is refused with the grammar."""
    if not request.tools.permits("model3d"):
        return TaskResult.failure(request.task, request.own_name, "3D model building is not installed for me right now.")
    text = request.task.description
    found = _MODEL3D_SUBJECT.search(text)
    subject = " ".join((next((g for g in found.groups() if g), "") if found else "").split()).strip(" ,") or "a model"
    subject = re.split(r"\s+(?:with|made\s+of|using|(?:that|which|so)?\s*(?:i|we|you)\s+(?:can|could)\b)\s*", subject,
                       maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,") or "a model"
    # "a 3D windmill, then a document": the next step of a longer request is
    # not part of what is being modelled.
    subject = re.split(r"\s*,?\s+(?:and\s+)?(?:then|after\s+that|afterwards)\b", subject,
                       maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,") or "a model"
    subject = re.sub(rf"^{_MODEL3D_NOUNS}\s+of\s+", "", subject, flags=re.IGNORECASE) or "a model"
    parsed = json.loads(request.tools.invoke("model3d", {"operation": "parse", "content": json.dumps({"description": text})}).output)
    grammar = json.loads(request.tools.invoke("model3d", {"operation": "grammar", "content": "{}"}).output)
    description, planner = text, "described"
    if parsed["not_understood"] and parsed["parts"]:
        return TaskResult.failure(
            request.task, request.own_name,
            "I can build the parts I understood (" + ", ".join(p["name"] for p in parsed["parts"]) + "), but not: "
            + "; ".join(repr(x) for x in parsed["not_understood"]) + ". Nothing was built. " + grammar["grammar"],
        )
    if not parsed["parts"] and len(grammar["generators"]) == 1:
        no_model = (
            f"To build {subject} in 3D I need its parts, and no reasoning model worked them out"
            " (the local model on this machine was measured unreliable at decomposing objects)."
            " Describe them and I'll build a real 3D mesh: " + grammar["grammar"]
        )
        if request.converse is None:
            return TaskResult.failure(request.task, request.own_name, no_model)
        decision = request.converse(request.task, (
            f"Break '{subject}' into simple geometric parts so it can be built as a 3D model. {grammar['grammar']} "
            "Use realistic proportions in one consistent unit, and describe every part it needs to be recognisable. "
            "Respond with ONLY the parts.",
        ))
        if decision is None or not decision.is_decided:
            return TaskResult.failure(request.task, request.own_name, no_model)
        restated = decision.reason.strip()
        again = json.loads(request.tools.invoke("model3d", {"operation": "parse", "content": json.dumps({"description": restated})}).output)
        if not again["parts"] or again["not_understood"]:
            return TaskResult.failure(
                request.task, request.own_name,
                f"The parts I was given for {subject} weren't all buildable ({restated[:200]}). Nothing was built. "
                "Describe its parts and I'll build it: " + grammar["grammar"],
            )
        description, planner = restated, decision.source or "brain"
    # How the whole subject moves ("a balloon drifting ...") - read from the
    # subject phrase with the same open motion vocabulary each part's own
    # clause is read with; never a subject-specific branch.
    from src.providers.media.model3d import motion_in

    whole_motion = motion_in(subject)
    outcome = request.tools.invoke("model3d", {"operation": "build", "content": json.dumps(
        {"subject": subject, "description": description, "motion": whole_motion})})
    if not outcome.ok:
        return TaskResult.failure(request.task, request.own_name, f"I couldn't build that in 3D: {outcome.error}")
    made = json.loads(outcome.output)
    report = made["report"]
    parts = ", ".join(f"{p['name']} ({p['shape']})" for p in made["parts"]) or made["generator"]
    # A part whose position could not be read was placed by default - said,
    # never left looking like the placement that was asked for.
    guessed = [f"{p['name']} ({p['note']})" for p in made["parts"] if str(p.get("note") or "").startswith("no position given")]
    placed = (" I couldn't read where these go, so I placed them by default: " + "; ".join(guessed) + ".") if guessed else ""
    # Movement is reported only as the inspector read it back from the file.
    moving = [f"{p['name']} {p['motion']}s" for p in made["parts"] if p.get("motion")]
    if made.get("motion"):
        moving.insert(0, f"the whole {subject} {made['motion']}s")
    channels = int(report.get("animated_channels") or 0)
    motion_note = (f" Moving ({channels} animated channel(s) in the file): " + "; ".join(moving) + ".") if moving and channels else ""
    return TaskResult.success(
        request.task, request.own_name,
        (
            f"Artifact created: id={made['artifact_id']} type=model3d title={made['title']!r} glb=artifact/{made['artifact_id']}.glb - "
            f"a real glTF 2.0 mesh: {report['meshes']} part(s), {report['triangles']} triangles, "
            f"{report['extent'][0]:g} x {report['extent'][1]:g} x {report['extent'][2]:g} units. Parts: {parts}.{placed}{motion_note} "
            "Open the 3D diagram workspace to turn it around."
        ),
        payload={"handled_by": "create_3d_model", "artifact_id": made["artifact_id"], "planner": planner,
                 "genuinely_3d": report["genuinely_3d"], "triangles": report["triangles"], "animated_channels": channels},
    )


CREATE_3D_MODEL = ManagerCapability(
    name="create_3d_model",
    capabilities=frozenset({"create_3d_model"}),
    handle=create_3d_model,
    description=(
        "Builds genuine 3D content for any subject - a glTF 2.0 mesh checked to be three-dimensional - from parts "
        "the person describes, or parts a reasoning model works out."
    ),
    claims=create_3d_model_intent,
    scoreable=False,
)


# ---------------------------------------------------------------------------
# Campaign 2: moving between workspaces - "Alfred, go to the research
# workspace", "which workspace are you in?". Any workspace the owner created
# and gave this agent; the names come from the owner's definitions, never
# from here.
# ---------------------------------------------------------------------------

_WORKSPACE_NAME = r"(?P<name>[\w][\w '&-]{0,48}?)"
_WORKSPACE_MOVE = (
    re.compile(
        r"\b(?:go|move|switch|head|hop|jump|change|come|get)\s+(?:back\s+|over\s+)?(?:to|into|in)\s+"
        r"(?:the\s+|your\s+|my\s+|our\s+)?" + _WORKSPACE_NAME + r"\s+workspace\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:open|enter|take\s+(?:me|us)\s+to|work\s+in|use)\s+(?:the\s+|your\s+|my\s+|our\s+)?"
        + _WORKSPACE_NAME + r"\s+workspace\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:go|move|switch|head|change|take\s+(?:me|us))\s+(?:back\s+|over\s+)?(?:to|into)\s+(?:the\s+|a\s+)?"
        r"workspace\s+(?:called|named)\s+" + r"(?P<name>[\w][\w '&-]{0,48})",
        re.IGNORECASE,
    ),
)
_WORKSPACE_QUESTION = re.compile(
    r"\b(?:which|what)\s+workspace\s+(?:are|am|is)\s+\w+\s+(?:in|working\s+in)\b"
    r"|\b(?:which|what)\s+workspaces\s+(?:do|have|are|can|does)\b"
    r"|\b(?:list|show)\s+(?:me\s+)?(?:your|my|the|all|his|her|their)\s+(?:\w+\s+)?workspaces\b",
    re.IGNORECASE,
)
# "Alfred, create a workspace called garden planning": making a workspace is
# the owner's governed write (Control Center ``workspace create``), never an
# agent's - so the request is answered with that, truthfully, rather than left
# to a Brain that could claim to have made one. Only a request - the verb in
# command position, after an optional name, "please", "can you" or "let's" -
# so "I set up a new workspace yesterday" stays a remark; and prepositions are
# excluded so "make a model in the workspace" stays the work it is.
_WORKSPACE_CREATE_SHAPE = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i(?:'d|\s+would)\s+like\s+you\s+to\s+"
    r"|i\s+(?:want|need)\s+you\s+to\s+|let'?s\s+)?"
    r"(?:create|make|set\s+up|start|add|open)\s+(?:me\s+|us\s+)?(?:a|an|another)\s+(?:new\s+)?"
    r"(?:(?!(?:in|into|for|on|at|to|of)\b)[\w'&-]+\s+){0,2}?workspace\b",
    re.IGNORECASE,
)


def _asks_to_create_workspace(text: str) -> bool:
    return bool(_WORKSPACE_CREATE_SHAPE.search(_strip_vocative(text)))


def _workspace_move_target(text: str) -> "str | None":
    for pattern in _WORKSPACE_MOVE:
        match = pattern.search(str(text or ""))
        if match is not None:
            name = " ".join(match.group("name").strip(" .!?,'").split())
            if name and name.lower() not in ("a", "the", "that", "this", "another", "other", "new"):
                return name
    return None


def workspace_navigation_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    text = str(task.description or "")
    return (_workspace_move_target(text) is not None or bool(_WORKSPACE_QUESTION.search(text))
            or _asks_to_create_workspace(text))


def _profile_words(workspace: dict) -> str:
    caps = workspace.get("capabilities") or []
    return f"set up for {', '.join(caps)}" if caps else "open to any kind of work"


def workspace_navigation_reply(task: Task, agent_name: str, invoke: "Callable[[dict], Any]") -> "TaskResult | None":
    """Move ``agent_name`` into the workspace a request names, or say which it
    is in and which it has - through the governed ``workspace`` tool. None when
    the request is not about moving between workspaces. Generic to any agent:
    the Manager's own capability and a specialist's turn both come here."""
    text = str(task.description or "")
    if _asks_to_create_workspace(text):
        return TaskResult.failure(
            task, agent_name,
            "I can't create workspaces - that's the owner's to do in the Control Center "
            "(workspace create <id> <label>|<panel>,<panel>, then workspace "
            f"{agent_name} add <id>). Once it's mine I can go to it and work there.",
        )
    target = _workspace_move_target(text)
    if target is None and not _WORKSPACE_QUESTION.search(text):
        return None
    operation = "enter" if target is not None else "list"
    outcome = invoke({"operation": operation, "content": json.dumps({"agent": agent_name, "workspace": target or ""})})
    payload = {"handled_by": "workspace_navigation", "operation": operation}
    if not outcome.ok:
        reason = re.sub(r"^\w+Error:\s*", "", str(outcome.error or ""))
        opening = "I can't go there" if target is not None else "I can't see my workspaces right now"
        return TaskResult.failure(task, agent_name, f"{opening}: {reason}.")
    state = json.loads(outcome.output)
    mine = state.get("workspaces") or []
    active = next((w for w in mine if w["id"] == state.get("active")), None)
    others = ", ".join(w["label"] for w in mine if active is None or w["id"] != active["id"]) or "no others"
    if target is not None:
        entered = state["entered"]
        reply = f"I'm in the {entered['label']} workspace now - {_profile_words(entered)}."
        payload.update(workspace=entered["id"])
    elif active is None:
        reply = "I don't have any workspaces yet - the owner gives them to me in the Control Center."
    else:
        reply = f"I'm in the {active['label']} workspace - {_profile_words(active)}. My other workspaces: {others}."
        payload.update(workspace=active["id"])
    return TaskResult.success(task, agent_name, reply, payload={**payload, "workspaces": [w["id"] for w in mine]})


def workspace_navigation(request: CapabilityRequest) -> TaskResult:
    if not request.tools.permits("workspace"):
        return TaskResult.failure(request.task, request.own_name, "Workspaces aren't available to me right now.")
    result = workspace_navigation_reply(
        request.task, request.own_name, lambda arguments: request.tools.invoke("workspace", arguments),
    )
    if result is None:
        return TaskResult.failure(
            request.task, request.own_name,
            "Which workspace? Name one of mine - or ask which workspace I'm in.",
        )
    return result


WORKSPACE_NAVIGATION = ManagerCapability(
    authority='every_agent',
    own_path="ManagerAgent.take_action - the agent's own workspaces",
    name="workspace_navigation",
    self_contained=True,
    self_contained_reason=(
        "moves an agent between workspaces - the orchestrator's own act"
    ),
    completes_request=True,
    anywhere=True,
    capabilities=frozenset({"workspace", "workspaces"}),
    handle=workspace_navigation,
    description=(
        "Moves this agent between the workspaces the owner created and gave it, and says which it is in - "
        "each with its own capability profile."
    ),
    claims=workspace_navigation_intent,
    scoreable=False,
)


TRANSLATE_MEDIA = ManagerCapability(
    name="translate_media",
    capabilities=frozenset({"translate_media", "subtitles"}),
    handle=translate_media,
    description=(
        "Translates the speech in shared video or audio into any language the installed engine translates, and routes "
        "it to outputs: subtitle files, a video with a subtitle track, translated speech, the live display."
    ),
    claims=translate_media_intent,
    carries_material=True,
    scoreable=False,
)


COACH = ManagerCapability(
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="coach",
    self_contained=True,
    self_contained_reason=(
        "the coach is a capability of the Manager, never a separate agent (ledger SCOPE-02)"
    ),
    completes_request=True,
    capabilities=frozenset({"coach", "accountability"}),
    handle=coach,
    description=(
        "The Manager's own personal coach: holds the person to the goals they taught it, their open tasks and "
        "missed reminders, in the style they chose (ruthless, direct or gentle) - from real records only."
    ),
    claims=coach_intent,
    scoreable=False,
)


READ_BACK = ManagerCapability(
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="read_back",
    self_contained=True,
    self_contained_reason=(
        "reads back notes this Manager itself kept"
    ),
    completes_request=True,
    capabilities=frozenset({"read", "aloud"}),
    handle=read_back,
    description=(
        "Reads back what is kept for the person - notes, schedule, tasks and reminders - "
        "in full through the canonical voice path, from the stores that already own them."
    ),
    claims=read_back_intent,
    scoreable=False,
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
    # "Speak Italian", "English with an Italian accent", "sound like someone
    # from Liverpool", "speak slower" - how the agent sounds, configured.
    from src.services.identity.voice_configuration import parse_voice_configuration

    if not parse_voice_configuration(task.description).is_empty():
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


def configure_voice(request: CapabilityRequest, configuration: "Any") -> TaskResult:
    """Spoken language, accent and pace for this agent, planned against what
    the speech provider actually speaking for it can do - stored as this
    agent's own settings, or honestly refused. A question ("can you speak
    German?") is answered and changes nothing."""
    from src.services.identity.voice_configuration import current_option, plan_voice, set_voice_setting, voice_setting_for
    from src.services.identity.voice_preference import effective_voice_for, set_voice_preference

    payload = {"handled_by": "select_voice", "action": "configure"}
    if request.voice_facts is None:
        return TaskResult.failure(request.task, request.own_name, "I can't see which voice provider is speaking for me right now.")
    facts = request.voice_facts()
    agent_key = request.own_name
    declared = getattr(request.identity, "voice", None)
    current = current_option(facts, effective_voice_for(
        request.identity, recall=request.recall, discover_voices=lambda: facts.voices,
    )) if declared is not None else None
    current_language = voice_setting_for(agent_key, "language", recall=request.recall) or "english"
    gender = parse_voice_request(request.task.description).gender
    plan = plan_voice(
        facts, current=current, language=configuration.language, accent=configuration.accent,
        gender=gender if configuration.accent or configuration.language else None,
        pace=configuration.pace, current_language=current_language,
        variant=configuration.variant, style=configuration.style,
    )
    if configuration.question:
        answer = f"Yes - I can: {plan.message}." if plan.ok else f"No. {plan.message}"
        return TaskResult.success(request.task, request.own_name, answer, payload={**payload, "question": True, "possible": plan.ok})
    if not plan.ok:
        return TaskResult.failure(request.task, request.own_name, plan.message)
    if request.recall is None or request.retain is None or request.forget is None:
        return TaskResult.failure(request.task, request.own_name, "I don't have a way to change how I sound right now.")
    if plan.voice is not None and (current is None or plan.voice.voice_id != current.voice_id):
        if voice_preference_for(agent_key, recall=request.recall):
            # The person already chose a voice and is now changing how it
            # sounds: their choice moves with their request.
            set_voice_preference(
                agent_key, plan.voice.name, available=tuple(v.name for v in facts.voices),
                recall=request.recall, remember=request.retain, forget=request.forget,
            )
        else:
            # The person asked for a language/accent/variant, not a voice: the
            # voice that satisfies it is the agent's own choice - still theirs
            # to override - never a pinned "user override" nobody made
            # (2026-09-21: "speak English again" had locked in a voice).
            from src.services.identity.voice_selection import VoiceSelection

            chosen = _apply_voice_selection(
                request, agent_key,
                VoiceSelection(option=plan.voice, language_satisfied=True, accent_satisfied=True,
                               gender_satisfied=True, reason="satisfies the requested voice settings"),
                requested_language=plan.language, requested_accent=configuration.accent or configuration.variant,
            )
            if not chosen.succeeded:
                return chosen
    if plan.language:
        set_voice_setting(agent_key, "language", plan.language, recall=request.recall, remember=request.retain, forget=request.forget)
    if plan.language or configuration.variant:
        # A named variety belongs to the language named with it; naming a
        # language alone clears any earlier variety of another language.
        set_voice_setting(agent_key, "variant", plan.variant or "", recall=request.recall,
                          remember=request.retain, forget=request.forget)
    if plan.style:
        set_voice_setting(agent_key, "style", plan.style, recall=request.recall, remember=request.retain, forget=request.forget)
    if configuration.accent or (plan.voice is not None and (current is None or plan.voice.voice_id != current.voice_id)):
        # The accent asked for travels to other providers; a voice changed for
        # any other reason leaves no stale accent behind.
        set_voice_setting(agent_key, "accent", configuration.accent or "", recall=request.recall,
                          remember=request.retain, forget=request.forget)
    if plan.pace:
        set_voice_setting(
            agent_key, "pace", "" if plan.pace == "normal" else plan.pace,
            recall=request.recall, remember=request.retain, forget=request.forget,
        )
    message = f"Done - from now on I'm {plan.message} ({facts.provider})."
    if plan.language and plan.language != "english" and request.translate is None:
        # Replies are written in English: speaking another language means
        # translating them first. Without a translator the setting is kept,
        # but it is not yet what is heard - said, never implied
        # (2026-09-21: "speaking Italian" was confirmed and English was said).
        message = (
            f"I've set my spoken language to {plan.language.title()} ({facts.provider}), but I can't speak it yet: "
            "my replies are written in English and no translation is enabled to put them into "
            f"{plan.language.title()}, so until then they are said as written. Enable offline translation in the "
            "Control Center with 'provider translation_provider use <backend>'."
        )
    return TaskResult.success(
        request.task, request.own_name, message,
        payload={**payload, "voice": plan.voice.name if plan.voice else None, "language": plan.language, "pace": plan.pace,
                 "variant": plan.variant, "style": plan.style, "provider": facts.provider,
                 "translated": plan.language in (None, "english") or request.translate is not None},
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

    from src.services.identity.voice_configuration import parse_voice_configuration

    configuration = parse_voice_configuration(text)
    if not configuration.is_empty():
        return configure_voice(request, configuration)

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
    authority='every_agent',
    own_path="CognitiveAgent.bind_voice - the agent's own voice",
    name="select_voice",
    self_contained=True,
    self_contained_reason=(
        "changes an agent's own voice configuration - an orchestration act"
    ),
    completes_request=True,
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
    from src.services.memory.guidance import parse_goal_link

    # "my duolingo lessons are for my Portuguese goal" links two things the
    # person already holds - personal guidance keeps it, resolved to both.
    if parse_goal_link(text) is not None:
        return False
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

    # A new value for a topic that already had a different one replaces it -
    # said out loud, so a person who meant "and another one" can say so.
    superseded_note = ""
    if candidate.status == STATUS_CURRENT and candidate.value and not attached_id:
        try:
            previous = current_personal_context_for(candidate.topic, recall=request.recall)
        except Exception:  # noqa: BLE001 - a read failure only loses the note
            previous = None
        if (
            previous is not None and previous.status == STATUS_CURRENT and previous.value
            and previous.value.strip().lower() != candidate.value.strip().lower()
        ):
            superseded_note = (
                f" That replaces what you told me before ({previous.value}) - I've kept that as history. "
                f"If it's an additional one, say \"my other {candidate.topic} is ...\"."
            )

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
        f"{confirmation}{calendar_note}{artifact_note}{superseded_note}",
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
    authority='manager',
    authority_reason='writes the one canonical record of the person, which only the Manager writes - every specialist reads it (personal_context_reply) and keeps its own memory',
    name="personal_context",
    self_contained=True,
    self_contained_reason=(
        "the person's own context, as this Manager holds it"
    ),
    completes_request=True,
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
    # The one canonical personal memory (MEM-01): learning and recalling what a
    # person tells about themselves is the agent's own memory, never work a
    # workspace profile scopes away (found at runtime 2026-09-21: "What is my
    # goal?" was refused inside an owner-created workspace).
    anywhere=True,
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
    # This domain's own object nouns, declared once beside the generalized
    # construction shape that has to defer to them - one list, two readers,
    # so the boundary cannot drift apart from either side of itself.
    rf"(?:{'|'.join(_SCHEDULING_OBJECT_NOUNS)})\b",
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
    # A fifth deferral, found by the Phase 11 closure sweep: a request to
    # *make* something - a handout, a document, a diagram, a 3D model, a
    # media asset - whose content happens to list weekdays ("create a
    # handout titled Study Plan with: Monday - the first item; Tuesday - the second")
    # was claimed here first (alphabetical order) and written to the
    # calendar as an event named after its last entry, reported as done. The
    # thing being made is the request; the days are its content. Deferring to
    # each construction capability's own claim keeps this rule free of any
    # noun list of its own.
    if (
        create_artifact_intent(task) or document_generation_intent(task)
        or create_diagram_intent(task) or create_3d_model_intent(task)
        or media_production_intent(task)
        or revise_artifact_intent(task) or revise_diagram_intent(task)
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
    authority='manager',
    authority_reason="works on the person's own calendar, commitments and records kept in the Manager's memory - run for a specialist it would start a second, disconnected copy in that specialist's memory",
    name="calendar",
    self_contained=True,
    self_contained_reason=(
        "the person's own calendar, which this Manager governs"
    ),
    completes_request=True,
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
    text = str(task.description or "")
    if _marker_present(text, _TRANSCRIBE_MARKERS):
        return True
    # "transcribe <a file>" / "transcribe artifact <id>": the verb plus the
    # exact thing to transcribe. The marker list only covered pointing words
    # ("transcribe this/that/the/my"), so naming the file - the one way a
    # terminal session can point at anything - was not claimed at all.
    return bool(re.search(r"\btranscri(?:be|ption|pt)\b", text, re.IGNORECASE) and names_specific_content(text))


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
    # Kept as a readable document derived from the recording, so later
    # questions ("what was decided in it?") and "read it to me" reach its
    # words without transcribing again. One transcript per recording: an
    # existing one is not duplicated.
    kept = ""
    if artifact_id:
        located = request.tools.invoke("artifact_production", {"operation": "find_recording", "content": artifact_id})
        existing = ""
        if located.ok:
            try:
                existing = str(json.loads(located.output).get("transcript_artifact_id") or "")
            except ValueError:
                existing = ""
        saved = existing or save_transcript(request.tools, artifact_id, title or "recording", outcome.output)
        if saved:
            kept = f"\n(Kept as a readable transcript: artifact {saved}.)"
    return TaskResult.success(
        request.task, request.own_name, f"Transcript of {name}:\n{outcome.output}{kept}",
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

# ---------------------------------------------------------------------------
# GM campaign: the ecosystem's general mechanisms, reached from conversation.
# The claims and handlers live in src/manager/ecosystem_capabilities.py, which
# holds the one thing they share and nothing above needs: the live vocabulary
# of what the ecosystem currently observes, watches and is connected to.
# ---------------------------------------------------------------------------

from src.manager.ecosystem_capabilities import (  # noqa: E402 - defined after everything they build on
    condition_watch,
    condition_watch_intent,
    ecosystem_events,
    ecosystem_events_intent,
    entity_groups,
    entity_groups_intent,
    observation_history,
    observation_history_intent,
    observation_watch,
    observation_watch_intent,
    sensitivity_rules,
    sensitivity_rules_intent,
    series_analysis,
    series_analysis_intent,
    live_view,
    live_view_intent,
)

_ECOSYSTEM_STATE_REASON = (
    "reads or declares the ecosystem's own observed state - one store the orchestrator keeps for the whole "
    "system, not work inside any specialist's area"
)

OBSERVATION_HISTORY = ManagerCapability(
    name="observation_history",
    capabilities=frozenset({"observation_history"}),
    handle=observation_history,
    description=(
        "What an observed entity was over time - history, a window, min/max/mean/change - from the "
        "ecosystem's observation store (GM-01). Never a live read; never invents a reading."
    ),
    claims=observation_history_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    operates_on_connections=True,
)

OBSERVATION_WATCH = ManagerCapability(
    name="observation_watch",
    capabilities=frozenset({"observation_watch"}),
    handle=observation_watch,
    description=(
        "Starts or stops watching something a connection can safely read - sampled on a schedule, or "
        "listened to when it streams - so its history is kept (GM-01, GM-04)."
    ),
    claims=observation_watch_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    operates_on_connections=True,
)

CONDITION_WATCH = ManagerCapability(
    name="condition_watch",
    capabilities=frozenset({"condition_watch"}),
    handle=condition_watch,
    description=(
        "\"Tell me when <something observed> goes above/below/stops reporting ...\" and \"if <...>, <do "
        "something>\": a declared condition over observations that notifies, or proposes an action and "
        "waits for a yes - never acts alone (GM-02). Also lists and cancels conditions."
    ),
    claims=condition_watch_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    operates_on_connections=True,
)

LIVE_VIEW = ManagerCapability(
    name="live_view",
    capabilities=frozenset({"live_view"}),
    handle=live_view,
    description=(
        "Live views - dashboards and operational screens whose widgets are bound to observed readings, their "
        "history (optionally through a series technique), groups, events or what a camera saw, redrawn as the "
        "data changes; served at live/<view> and as a workspace panel (GM-06). Views only ever read."
    ),
    claims=live_view_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    operates_on_connections=True,
)

SERIES_ANALYSIS = ManagerCapability(
    name="series_analysis",
    capabilities=frozenset({"series_analysis"}),
    handle=series_analysis,
    description=(
        "What can be found in a history of readings - smoothing, rate of change, peaks and dips, unusual readings, "
        "changes of level, repeating patterns, and how two histories relate - through the governed series "
        "technique registry (GM-09). Measurements, never invention; refuses when there is too little to say."
    ),
    claims=series_analysis_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    operates_on_connections=True,
)

ENTITY_GROUPS = ManagerCapability(
    name="entity_groups",
    capabilities=frozenset({"entity_groups"}),
    handle=entity_groups,
    description=(
        "Groups of things that belong together across different connections (a room, a line, a field): "
        "declare and reshape them, read all their members at once with group-wide aggregates, and act on "
        "every member - each step listed and confirmed first (GM-15)."
    ),
    claims=entity_groups_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
    # A group's members are connections; naming one never means running one
    # of its operations directly - a group action goes through its own
    # confirmation listing every member's step.
    operates_on_connections=True,
)

SENSITIVITY_RULES = ManagerCapability(
    name="sensitivity_rules",
    capabilities=frozenset({"sensitivity_rules"}),
    handle=sensitivity_rules,
    description=(
        "The owner's rules for handling personal information: which words, sources and kinds of record are "
        "personal, sensitive or restricted, and how long each level is kept (GM-13)."
    ),
    claims=sensitivity_rules_intent,
    scoreable=False,
    completes_request=True,
    authority="manager",
    authority_reason="how the owner's personal information is handled is the owner's own governance, set through the orchestrator",
    self_contained=True,
    self_contained_reason="declares the owner's own policy over every agent's information - no specialist's area",
    # "mark readings from <connection> as sensitive" takes the connection as
    # its object; it never performs one of its operations.
    operates_on_connections=True,
)

ECOSYSTEM_EVENTS = ManagerCapability(
    name="ecosystem_events",
    capabilities=frozenset({"ecosystem_events"}),
    handle=ecosystem_events,
    description="What the declared conditions have raised - recent events and any proposal awaiting a yes (GM-02).",
    claims=ecosystem_events_intent,
    scoreable=False,
    completes_request=True,
    self_contained=True,
    self_contained_reason=_ECOSYSTEM_STATE_REASON,
)


MANAGER_CAPABILITIES: dict[str, ManagerCapability] = {
    SELF_DESCRIPTION.name: SELF_DESCRIPTION,
    ROSTER_REPORT.name: ROSTER_REPORT,
    PARTICIPANT_INVITATION.name: PARTICIPANT_INVITATION,
    # Phase 10: discussions among the room's participants, and removal.
    DISCUSSION.name: DISCUSSION,
    PARTICIPANT_DISMISSAL.name: PARTICIPANT_DISMISSAL,
    CONVERSATIONAL_FLOOR.name: CONVERSATIONAL_FLOOR,
    GENERAL_ASSISTANCE.name: GENERAL_ASSISTANCE,
    OPEN_CONVERSATION.name: OPEN_CONVERSATION,
    FORGET.name: FORGET,
    RECALL_MEMORY.name: RECALL_MEMORY,
    CORRECT_MEMORY.name: CORRECT_MEMORY,
    LIVE_INFORMATION.name: LIVE_INFORMATION,
    IMAGE_ANALYSIS.name: IMAGE_ANALYSIS,
    # GM-03: structured perception - find, follow, count.
    PERCEIVE_MEDIA.name: PERCEIVE_MEDIA,
    # Pre-Stage-9 Campaign 8: was this image edited or generated - evidence only.
    IMAGE_AUTHENTICITY.name: IMAGE_AUTHENTICITY,
    # Whole-ecosystem campaign: image transformation/enhancement/reconstruction
    # and comparison, through an open, governed technique registry.
    IMAGE_TRANSFORM.name: IMAGE_TRANSFORM,
    IMAGE_COMPARISON.name: IMAGE_COMPARISON,
    GENERATE_DOCUMENT.name: GENERATE_DOCUMENT,
    CREATE_ARTIFACT.name: CREATE_ARTIFACT,
    REVISE_ARTIFACT.name: REVISE_ARTIFACT,
    # Phase 11 closure: creation ended at a saved file; this is the half that
    # runs it and says what actually happened.
    RUN_ARTIFACT.name: RUN_ARTIFACT,
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
    # Pre-Stage-9 Campaign 7: kept notes, schedule and tasks, read back aloud.
    READ_BACK.name: READ_BACK,
    # Pre-Stage-9 Campaign 9: Alfred's own ruthless personal coach.
    COACH.name: COACH,
    # Whole-ecosystem campaign: personal guidance across the person's own sources.
    PERSONAL_GUIDANCE.name: PERSONAL_GUIDANCE,
    # Campaign 2: media translation routed to outputs.
    TRANSLATE_MEDIA.name: TRANSLATE_MEDIA,
    WORKSPACE_NAVIGATION.name: WORKSPACE_NAVIGATION,
    CREATE_3D_MODEL.name: CREATE_3D_MODEL,
    # Pre-Stage-9 Campaign 9: translation, named and honest.
    TRANSLATE.name: TRANSLATE,
    # GM campaign.
    OBSERVATION_HISTORY.name: OBSERVATION_HISTORY,
    OBSERVATION_WATCH.name: OBSERVATION_WATCH,
    CONDITION_WATCH.name: CONDITION_WATCH,
    ECOSYSTEM_EVENTS.name: ECOSYSTEM_EVENTS,
    SENSITIVITY_RULES.name: SENSITIVITY_RULES,
    ENTITY_GROUPS.name: ENTITY_GROUPS,
    SERIES_ANALYSIS.name: SERIES_ANALYSIS,
    LIVE_VIEW.name: LIVE_VIEW,
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

    # The one exception to "no handler outranks another", and it is safety,
    # not preference: a message that speaks of ending one's life or self-harm
    # is answered with care and a pointer to real help before any other
    # reading of its words ("I don't want to live anymore" also has the shape
    # of a changed preference, and a memory correction must never be what
    # answers it).
    guidance = registry.get("personal_guidance")
    if guidance is not None:
        from src.services.memory.guidance import crisis_signal

        if crisis_signal(task.description):
            return guidance

    # Phase 10, the second and last exception, and also not preference: a
    # request to *discuss* a piece of work with others is about the work, never
    # the work itself. "Let's discuss whether to build a 3D model of a chair
    # with <agent>" also has the shape the 3D capability claims, and the claim
    # pass below breaks ties by name - so without this the discussion request
    # built the chair. Talking about doing something must never do it.
    discussion = registry.get(DISCUSSION.name)
    if discussion is not None and discussion.claims is not None and discussion.claims(task, participants):
        return discussion

    # Phase 11 closure, the same rule for words: a request to translate a
    # phrase is about the phrase, never a request to do what it says.
    # "Translate 'add a task to buy milk' into Spanish" also has the shape
    # create_task claims, and the claim pass below goes by name - so it added
    # the task; "... I am learning to cook ..." was remembered as a fact about
    # the person, "... I have a meeting tomorrow at 3 ..." was booked. The
    # translation shapes are anchored on the whole remark, and the target must
    # be able to name a language, so this only ever answers a remark that is a
    # translation request.
    translate = registry.get(TRANSLATE.name)
    if (
        translate is not None and translate.claims is not None and translate.claims(task, participants)
        and names_a_language_target(task.description)
    ):
        return translate

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
