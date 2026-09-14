"""One place where the system is assembled.

Two entry points already wire this agent - main.py and scripts/heartbeat.py -
and they were building the same fourteen collaborators independently. A third
consumer would have meant a third copy, and copies drift: the specialist brain,
persistent memory and the opportunity interpreter each reached one entry point
before the other, and each gap was found by inspection rather than by a test.

So this module owns the construction, and the entry points own the decisions.

**Two functions, because there are genuinely two lifetimes.** This is the whole
design, and collapsing it would be a bug:

    build_runtime   built ONCE - a provider, a router, brains, a calendar.
                    None holds per-run state, so sharing them is free.

    build_manager   built PER RUN - and for the heartbeat, per tick.

The Manager and its memories must be rebuilt each tick, and not as an
efficiency oversight. A PersistentMemory re-reads its file at construction, so
a fresh Manager is what lets tick 2 see what tick 1 wrote; and the profile is
re-read each tick so an edit to USER.md takes effect without a restart. A
single long-lived Manager - the obvious "System" object - would freeze both,
quietly breaking the temporal memory design in a way no existing test covers.

What this module deliberately does **not** decide: which mailbox to use, how to
handle a missing profile, which extra agents to register, how to report an
error, or what to do next. Those differ between the two entry points on
purpose - the heartbeat swaps in a sample inbox for --dry-run and treats a
missing profile as a stop condition, while main.py degrades to detection-only -
and moving them here would turn product decisions into shared machinery.

Nothing here has a side effect worth fearing: no file is written, no network is
touched, and no credential is read. build_provider checks that a key *exists*
without importing a vendor SDK, and GmailMailbox connects lazily on first use,
so a Control Center may construct all of this simply to look at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.agents.brain_factory import (
    build_opportunity_interpreter,
    build_specialist_brain,
)
from src.agents.specialists import (
    SPECIALISTS,
    SpecialistContext,
    SpecialistWiring,
    wiring_for,
)
from src.agents.base_agent import BaseAgent
from src.agents.tools.toolset import ToolSet
from src.agents.brain import SpecialistBrain
from src.channels.channel import Channel, VoiceChannel
from src.channels.speech import SpeechAdapter
from src.config.settings import Settings
from src.conversation.conversation import (
    Conversation,
    Participant,
    ParticipantKind,
)
from src.manager.activity_session import ActivityBrainOps
from src.manager.brain import ManagerBrain
from src.manager.brain_factory import build_brain
from src.manager.manager import ManagerAgent
from src.control_center.interfaces import InterfaceDescriptor, InterfaceFeature
from src.manager.manager_skills import ManagerSkills
from src.manager.memory_policy import ManagerMemoryPolicy
from src.manager.router import Router
from src.manager.router_factory import build_router
from src.providers.avatar import AvatarProvider
from src.providers.avatar_factory import build_avatar_provider
from src.providers.environment import EnvironmentProvider
from src.providers.environment_factory import build_environment_provider
from src.providers.base import LLMProvider
from src.providers.calendar_events import CalendarEventProvider
from src.providers.calendar_events_factory import build_calendar_event_provider
from src.providers.celebration import CelebrationProvider
from src.providers.celebration_factory import build_celebration_provider
from src.providers.communication import CommunicationProvider
from src.providers.communication_factory import build_communication_provider
from src.providers.connection_factory import build_all_connections
from src.providers.connection_resolver import ConnectionResolver
from src.providers.documents.factory import build_document_rendering
from src.providers.documents.pdf import DocumentRenderer
from src.providers.factory import build_provider
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.providers.knowledge import KnowledgeSourceProvider
from src.providers.knowledge_factory import build_knowledge_provider
from src.providers.live_info import LiveInfoProvider
from src.providers.live_info_factory import build_live_info_provider
from src.providers.media.factory import MediaTools, build_media
from src.providers.speech_factory import build_speech
from src.registry.agent_registry import AgentRegistry
from src.services.email.mailbox import Mailbox
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.identity.presence import PresenceRegistry
from src.services.identity.recording_session import RecordingSessionRegistry
from src.services.artifacts.store import ArtifactStore
from src.services.memory.day import Calendar
from src.services.memory.factory import (
    EMAIL_AGENT,
    MANAGER,
    adopt_legacy_manager_memory,
    build_memory,
)
from src.services.profile.profile import UserProfile
from src.services.profile.user_knowledge import UserKnowledge
from src.services.schedule.schedule import UserSchedule
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The technical key of the specialist this project ships. Defined beside the
# registry that uses it and re-exported here, because callers already reach
# for it from the composition root.
EMAIL_AGENT_KEY = EMAIL_AGENT


@dataclass(frozen=True)
class SpecialistRuntime:
    """One specialist's long-lived half, resolved once at startup.

    Everything here is either identity or a stateless collaborator, which is
    what makes sharing it across heartbeat ticks correct. Note what is absent:
    the agent itself and its memory. Those are per-tick and are produced by
    the builder, not stored here - a specialist held in Runtime would be a
    singleton whose persistent store never re-read its file.
    """

    key: str
    wiring: SpecialistWiring
    brain: SpecialistBrain
    build: Any

    @property
    def name(self) -> str:
        return self.wiring.name


@dataclass(frozen=True)
class Runtime:
    """The collaborators that are safe to build once and share.

    Frozen, and holding nothing that changes during a run. Everything here is
    either configuration, a stateless strategy, or a client that connects
    lazily - which is what makes sharing them across heartbeat ticks correct
    rather than merely convenient.

    Note what is absent: no Manager, no memory, no mailbox, no profile. Those
    have a per-run lifetime and belong to build_manager.
    """

    settings: Settings
    calendar: Calendar
    identity: AgentIdentity
    provider: LLMProvider
    router: Router
    manager_brain: ManagerBrain
    interpreter: Any | None
    # Every specialist this project ships, keyed by its technical key, each
    # with its own identity and its own brain. Plural because there is no
    # architectural reason for there to be one - the fact that this project
    # currently ships a single specialist is a fact about the project, not a
    # shape the composition root should be built around.
    #
    # An identity document describes an *agent*, not the user, so it has an
    # agent's lifetime: read once, here. Nothing in it changes between
    # heartbeat ticks, which is precisely why it belongs in Runtime and the
    # user's profile does not.
    specialists: Mapping[str, SpecialistRuntime]

    # How this installation can hear and speak, decided once, here.
    #
    # The *adapter* belongs in Runtime and the *channel* does not, and the line
    # between them is lifetime. An adapter holds an engine and a record of what
    # that engine last did; it has no Manager and nothing per-run, so building
    # it once and sharing it is correct in exactly the way sharing the provider
    # is. A Channel holds a Manager, and the Manager is rebuilt every heartbeat
    # tick - a Channel kept here would go on pointing at the first tick's
    # Manager forever, which is the same trap SpecialistRuntime documents for
    # specialists and their memories. See build_channel below.
    #
    # Nothing here knows how speech is done. Runtime holds the interface; what
    # is behind it is a composition decision, made by build_speech from
    # settings.speech (Stage 10) - "none" (NoSpeech, honestly UNAVAILABLE) is
    # the only shipped option today, and remains what a default configuration
    # gets, exactly as an unconfigured provider stays "null".
    speech: SpeechAdapter

    # Stage 14: where Alfred's current-information tool actually reaches
    # for real evidence, selected by name exactly as `provider` above -
    # see build_live_info_provider. "null" (honestly empty) is what an
    # unconfigured installation gets, the same default `provider` itself
    # already uses.
    live_info: LiveInfoProvider

    # Creative-media checkpoint: the real narration/image/video
    # collaborators media_production is built from, selected by name
    # exactly as `live_info` above already is. "null" (honestly
    # unavailable) is what an unconfigured installation gets; "local"
    # is genuine, offline production - no vendor credential, ever.
    media: MediaTools

    # Stage 15: where Alfred's governed communication tools actually
    # reach for a real phone/messaging connection, selected by name
    # exactly as `live_info` above already is. "null" (honestly
    # unavailable) is what an unconfigured installation gets.
    communication: CommunicationProvider

    # Stage 18: where Alfred's knowledge tool reaches for the documents,
    # books and notes the *user* supplied - selected by name exactly as
    # `live_info` above already is, and a deliberately separate axis from
    # it. One says what the world looks like now; this says what the
    # person actually handed over. An installation can have either, both
    # or neither, and all four states are describable rather than guessed.
    knowledge: KnowledgeSourceProvider

    # Stage 12 closure: the single, shared source every agent's own
    # milestone-celebration presentation reaction comes from, selected by
    # name exactly as every provider above already is. "default"
    # (DefaultCelebrationProvider, genuinely working - see that module's
    # own docstring for why there is no "null" variant) is what an
    # unconfigured installation gets, unlike live_info/communication's own
    # honestly-empty defaults.
    celebration: CelebrationProvider

    # Stage 12 closure: where Alfred's temporal-event engine reaches for
    # named, recurring calendar occasions, selected by name exactly as
    # every provider above already is. "default" (genuinely computed,
    # not a placeholder - see that module's own docstring) is what an
    # unconfigured installation gets, the same choice celebration above
    # already makes.
    calendar_events: CalendarEventProvider

    # Stage 16 / Phase 7 (Universal Connection Layer): where Alfred's
    # governed connection tools reach for every real, external,
    # authenticated connection this session is equipped with - not one
    # fixed connection, an open-ended ConnectionResolver (see that
    # module's own docstring). The configured default
    # (settings.connection_provider) is always among these, built
    # exactly as before ("null", honestly unavailable, for an
    # unconfigured installation - unlike celebration/calendar_events, a
    # connection is consequential, the same reasoning communication
    # above already follows); every other currently declared service/
    # site joins it, built once, here, at process-construction time.
    connections: ConnectionResolver

    # Stage 17: where every agent's presence reaches for its avatar
    # catalog and deterministic default, selected by name exactly as
    # every provider above already is. One shared provider, never one
    # per agent - the identical reasoning celebration above already
    # follows.
    avatar_provider: AvatarProvider

    # Universal Ecosystem checkpoint: where an agent's presence reaches
    # for its scene description, selected by name exactly as
    # avatar_provider above already is, and a genuinely independent
    # axis from it - see src/providers/environment.py's own docstring.
    # "default" (deterministic, no-network) is what an unconfigured
    # installation gets, the same choice avatar_provider already makes.
    environment_provider: EnvironmentProvider

    # Stage 17.2: the one live, in-process record of what a real caller
    # currently has genuine evidence of an agent doing (today: SPEAKING,
    # written only by VoiceChannel.deliver() around a real, blocking
    # speak() call). Not a second Memory - it remembers nothing once a
    # state ends. One shared instance so the channel that asserts a
    # state and the Control Center that reads it are looking at the
    # same signal, the way Runtime already shares one avatar_provider.
    presence_registry: PresenceRegistry

    # Real Audio Recording/Capture checkpoint: the one live, in-process
    # record of where a real recording currently stands for each agent,
    # the identical shared-instance role presence_registry above already
    # plays - a natural-language "stop recording" (a capability handler)
    # and the frontend's own upload route must read and write the exact
    # same state, never two independently-tracked copies of "is this
    # agent currently recording".
    recording_sessions: RecordingSessionRegistry

    # Multimodal workspace/artifact checkpoint: the one authoritative,
    # persistent record of every real artifact this installation has
    # produced - one shared instance, the identical role
    # presence_registry above already plays: the tool that writes a real
    # artifact and the Control Center that reads it back are looking at
    # the same object, never two independently-loaded copies.
    # Agent-independent and type-independent by construction (see
    # src/services/artifacts/store.py's own docstring).
    artifacts: ArtifactStore

    # Multimodal workspace/artifact checkpoint: which real engine an
    # artifact's document content is exported to a real file format
    # with, selected by name exactly as every provider above already is.
    # A genuinely separate concern from `media` (that axis produces
    # narration/image/video; this one exports structured document
    # content) and from `artifacts` above (identity/persistence vs.
    # rendering - the same split MediaProductionTool already keeps
    # between a provider's own output and the write).
    document_renderer: DocumentRenderer

    # ------------------------------------------------------------------
    # Named accessors for the one specialist this project ships.
    #
    # The mapping above is the source of truth; these are conveniences for
    # callers that already know they mean the email specialist - a startup
    # banner, a test. They read from the mapping rather than holding a second
    # copy, so there is nothing here that could fall out of step with it.
    # ------------------------------------------------------------------

    @property
    def email_specialist(self) -> SpecialistWiring:
        return self.specialists[EMAIL_AGENT_KEY].wiring

    @property
    def specialist_brain(self) -> SpecialistBrain:
        return self.specialists[EMAIL_AGENT_KEY].brain

    def describe(self) -> str:
        """A short line for a startup banner. Never prints a credential."""
        return (
            f"provider={self.provider.name} "
            f"speech={self.speech.name} "
            f"live_info={self.live_info.name} "
            f"media={self.media.name} "
            f"communication={self.communication.name} "
            f"knowledge={self.knowledge.name} "
            f"celebration={self.celebration.name} "
            f"calendar_events={self.calendar_events.name} "
            f"connections={self.connections.default.name}({len(self.connections.names)}) "
            f"avatar_provider={self.avatar_provider.name} "
            f"environment_provider={self.environment_provider.name} "
            f"router={type(self.router).__name__} "
            f"brain={type(self.manager_brain).__name__} "
            f"specialists={','.join(s.name for s in self.specialists.values())} "
            f"interpreter={'yes' if self.interpreter is not None else 'no'} "
            f"timezone={self.settings.timezone}"
        )


def build_runtime(
    settings: Settings,
    *,
    accounts: CredentialStore | None = None,
    specialist_accounts: Mapping[str, ProviderAccount] | None = None,
    video_generation_account: ProviderAccount | None = None,
    video_understanding_account: ProviderAccount | None = None,
    speech_account: ProviderAccount | None = None,
    communication_account: ProviderAccount | None = None,
    connection_adapter_version: str = "1",
) -> Runtime:
    """Build everything that outlives a single run.

    Raises rather than degrades. A timezone that cannot be resolved or a
    provider that cannot be configured is a misconfiguration, and the caller -
    which knows whether it is a CLI that should exit or an interface that
    should show a message - decides how to report it. That is why this function
    does no printing and calls no sys.exit.

    check_configuration is deliberately *not* called here. It is a startup
    policy about whether a combination of settings makes sense, and the entry
    points already run it before anything is built; calling it again here would
    make constructing a system for inspection illegal.

    ``accounts``/``specialist_accounts`` (Phase 1.5) are optional, additive
    seams onto the credential layer (``src/services/credentials/account.py``):
    when given, ``accounts.active(settings.provider)`` names exactly which
    account authorises the Manager's own provider, and
    ``specialist_accounts[key]`` names one for the specialist registered
    under ``key``. ``video_generation_account`` (AI video-generation
    checkpoint) is the identical seam applied to
    ``build_media``'s own AI video-generation vendor, entirely
    independent of which LLM account authorises the Brain.
    ``video_understanding_account`` (video-understanding checkpoint) is
    the same seam again, applied to ``build_media``'s own speech-
    transcription vendor - a third, independent axis from both
    ``video_generation_account`` and whichever LLM account authorises
    the Brain.
    ``speech_account`` (Universal Ecosystem checkpoint) is the same
    seam again, applied to whichever of ``build_speech``'s own engines
    currently needs a credential - Runtime holds no opinion about which
    that is (see ``build_speech``'s own module for that).
    ``communication_account`` (universal provider/connection management
    repair) is the identical seam applied to
    ``build_communication_provider``'s own backend - closing the one
    real asymmetry CHECK found between this category and its siblings:
    every other credentialed category already had this parameter, and
    this one did not, purely because no credentialed communication
    backend existed yet to need it. None of the five is required -
    every existing caller that omits all of them reproduces this
    function's prior, environment-based behaviour exactly, unchanged.

    **What this parameter does not do.** Accepting
    ``communication_account`` here is the construction-time half of the
    repair; it does not by itself make a Control Center account
    selection take effect on an already-running process; see
    ``scripts/control_center.py::build``'s own docstring for the other
    half (loading the persisted store and passing it in here), and that
    module's own note on the live-activation gap that remains.

    Each specialist's provider is its own, independently constructed
    ``build_provider(...)`` call - never the Manager's own provider object
    reused. Before Phase 1.5 this loop passed the single ``provider`` built
    above to every specialist brain, which meant Alfred and every
    specialist shared one literal ``LLMProvider`` instance (and therefore
    one credential) by construction, not merely by today's coincidence of
    there being only one configured account - the opposite of the
    independence this project's own architecture already documents for
    Brain, Memory and Voice. A specialist now receiving its own instance
    (defaulting to the identical Settings/environment-resolved credential
    when no override is given, so today's single-account behaviour is
    byte-for-byte unchanged) is what makes a future specialist's own,
    independently authorised account a matter of passing one in here,
    never a change to Brain, Memory, Voice or any agent's own code.
    """
    calendar = Calendar.configured(settings.timezone)
    identity = load_identity(settings.identity_path)
    manager_account = accounts.active(settings.provider) if accounts is not None else None
    provider = build_provider(settings, account=manager_account)

    # One pass over the registry: every specialist gets its identity resolved
    # and its own brain built from its own domain. The loop is what makes a
    # second specialist a data change rather than a code change - nothing
    # below counts them, names them, or is written for exactly one.
    specialists = {
        key: SpecialistRuntime(
            key=key,
            wiring=(wiring := wiring_for(
                key, fallback_name=key, identities_dir=settings.identities_dir
            )),
            # Its own brain, from its own domain, built from its own,
            # independently constructed provider. Two specialists get two
            # brains and two provider instances; neither gets the
            # Manager's own.
            brain=build_specialist_brain(
                settings,
                build_provider(
                    settings,
                    account=(specialist_accounts or {}).get(key),
                ),
                wiring.brain_domain,
                personality=wiring.personality,
            ),
            build=build,
        )
        for key, build in SPECIALISTS.items()
    }

    runtime = Runtime(
        settings=settings,
        calendar=calendar,
        identity=identity,
        provider=provider,
        router=build_router(settings, provider),
        manager_brain=build_brain(settings, provider, identity.personality),
        interpreter=build_opportunity_interpreter(settings, provider),
        specialists=specialists,
        # Selected by name from settings.speech (Stage 10), the same pattern
        # build_provider already uses for the model - see
        # src/providers/speech_factory.py. What matters is that the decision
        # is made *here* rather than at a screen: before this, the Control
        # Center constructed its own NoSpeech, so it reported a hardcoded fact
        # rather than the system's.
        speech=build_speech(settings, account=speech_account),
        # Stage 14: selected by name from settings.live_info_provider,
        # the identical pattern build_provider/build_speech already use -
        # "null" (honestly empty) unless a real backend was deliberately
        # configured.
        live_info=build_live_info_provider(settings),
        media=build_media(
            settings,
            video_generation_account=video_generation_account,
            video_understanding_account=video_understanding_account,
        ),
        # Stage 15: selected by name from settings.communication_provider,
        # the identical pattern live_info above already uses - "null"
        # (honestly unavailable) unless a real connection was deliberately
        # configured. Universal provider/connection management repair:
        # now also accepts communication_account, the identical
        # optional seam video_generation_account/speech_account already
        # give their own categories above.
        communication=build_communication_provider(settings, account=communication_account),
        # Stage 18: selected by name from settings.knowledge_provider, the
        # identical pattern live_info above already uses - "documents"
        # (a local folder, honestly empty until the user puts something in
        # it) is what an unconfigured installation gets.
        knowledge=build_knowledge_provider(settings),
        # Stage 12 closure: selected by name from settings.celebration_provider,
        # the identical pattern every provider above already uses - "default"
        # (genuinely working, not a placeholder) unless a future
        # implementation is deliberately configured.
        celebration=build_celebration_provider(settings),
        # Stage 12 closure: selected by name from settings.calendar_event_provider,
        # the identical pattern celebration above already uses.
        calendar_events=build_calendar_event_provider(settings),
        # Stage 16 / Phase 7 (Universal Connection Layer): the default
        # is selected by name from settings.connection_provider, the
        # identical pattern communication above already uses - "null"
        # (honestly unavailable) unless a real connection was
        # deliberately configured - but build_all_connections equips
        # this session with every OTHER currently declared service/site
        # too, an open-ended ConnectionResolver rather than one fixed
        # Connection (see that module's own docstring). Full Alfred OS
        # Ecosystem Evolution:
        # connection_adapter_version is the identical optional,
        # additive seam every other account parameter above already is,
        # applied to which real *implementation* a builder with more
        # than one (today, only Gmail) should use - never the
        # account/credential, which stays exactly what `accounts` above
        # already governs.
        connections=build_all_connections(settings, adapter_version=connection_adapter_version),
        # Stage 17: selected by name from settings.avatar_provider, the
        # identical pattern celebration above already uses.
        avatar_provider=build_avatar_provider(settings),
        # Universal Ecosystem checkpoint: selected by name from
        # settings.environment_provider, the identical pattern
        # avatar_provider above already uses - a genuinely independent
        # axis, never derived from it.
        environment_provider=build_environment_provider(settings),
        # Stage 17.2: one fresh, empty registry per runtime - never
        # configured by settings, because there is nothing to choose
        # between (this is not a provider seam; it is in-process,
        # per-run runtime state, the same non-configurable role
        # AgentRegistry() already plays a few lines below).
        presence_registry=PresenceRegistry(),
        # Real Audio Recording/Capture checkpoint: one fresh, empty
        # registry per runtime, the identical non-configurable role
        # presence_registry above already plays - there is nothing to
        # choose between, and no recording is ever in progress at
        # process start.
        recording_sessions=RecordingSessionRegistry(),
        # Multimodal workspace/artifact checkpoint: loaded fresh from
        # settings.artifacts_dir - a real, side-effect-free read (this
        # module's own docstring's "no file is written" invariant holds:
        # loading is not writing), the identical "no side effect worth
        # fearing" reasoning every other construction here already
        # follows. Genuine writes only ever happen later, when a real
        # ArtifactProductionTool call actually creates or revises one.
        artifacts=ArtifactStore(settings.artifacts_dir),
        # Selected by name from settings.document_rendering, the
        # identical pattern celebration/avatar_provider above already
        # use - "simple_pdf" (real, local, zero external risk) is what
        # an unconfigured installation gets, the same choice those two
        # already make for the identical reason (see that setting's own
        # docstring).
        document_renderer=build_document_rendering(settings),
    )
    logger.info("runtime ready: %s", runtime.describe())
    return runtime


def build_channel(runtime: Runtime, manager: ManagerAgent) -> Channel:
    """The door this installation offers onto one assembled Manager.

    Per-run, like the Manager it wraps, and for the same reason: a channel
    built once would keep answering through whichever Manager happened to exist
    when it was made.

    Always a VoiceChannel, which is not a claim that voice works. A
    VoiceChannel with no usable engine *is* a text channel - it falls back to
    typing to receive, still prints every answer, and reports spoken=False -
    and building one unconditionally means the day an engine is installed,
    nothing here changes. Choosing between two channel types based on whether
    speech happens to be available would put a decision in the composition root
    that VoiceChannel already makes, better, at the point of use.

    Stage 17.2: the same ``runtime.presence_registry`` every agent's
    presence is already read through (see ``ControlCenter``) is handed
    to the channel too, so a real ``deliver()`` call and a real
    snapshot read are looking at one shared, live signal - never two.
    """
    return VoiceChannel(manager, speech=runtime.speech, presence=runtime.presence_registry)


def build_conversation(manager: ManagerAgent, person: str = "you") -> Conversation:
    """One room, opened with the person and the Manager in it.

    Per-run, like the Manager and the channel: a conversation is the record of
    *this* session, and one built once and shared would carry a previous run's
    transcript into the next.

    **Specialists are not put in the room here, and that is the design.** A
    conversation is not a roster - it is who is actually taking part. Alfred
    brings a specialist in when the work needs one, so the room starts as a
    person and their Manager, and grows. Pre-populating it from the registry
    would make "bring Research into this" meaningless, because Research would
    already have been sitting there.

    Nothing about how a specialist is built is known here or there. A
    participant is a name and a kind, so joining costs a name.
    """
    return Conversation(
        participants=(
            Participant(person, ParticipantKind.PERSON),
            Participant(
                manager.own_skills.name if manager.own_skills else "manager",
                ParticipantKind.AGENT,
            ),
        )
    )


def build_manager(
    runtime: Runtime,
    *,
    mailbox: Mailbox,
    profile: UserProfile | None,
    schedule: UserSchedule | None = None,
    tools: ToolSet | None = None,
    # What each specialist may reach, keyed by its technical key. Separate from
    # ``tools`` above, which equips the Manager: an integration granted to
    # Alfred is not thereby granted to anybody else, and vice versa.
    specialist_tools: Mapping[str, ToolSet] | None = None,
    fetch_limit: int | None = None,
    extra_agents: Sequence[BaseAgent] = (),
    # Control Center repair item #1 (2026-09-07): which handlers this
    # Manager's own ManagerSkills may ever select. None (every existing
    # caller - main.py, the heartbeat, every test that does not pass this)
    # keeps ManagerSkills' own unchanged default, MANAGER_CAPABILITIES in
    # full. scripts/control_center.py's own build() is the one real caller
    # that passes a filtered dict, computed once from GovernedConfig - see
    # its own comment for why enforcement belongs at this one composition
    # site and nowhere inside a capability's own code.
    capabilities: "Mapping[str, object] | None" = None,
) -> ManagerAgent:
    """Assemble one Manager, with the email specialist registered.

    Called once per process by main.py and **once per tick** by the heartbeat.
    Each call produces a fresh Manager with fresh memories, which is what makes
    a persistent store re-read its file and see what the previous tick wrote.

    ``mailbox`` and ``profile`` are parameters rather than decisions taken here:
    the heartbeat substitutes a sample inbox for --dry-run, and the two entry
    points disagree about what a missing profile means. ``extra_agents`` exists
    for the same reason - main.py registers a calendar stand-in that the
    autonomous path deliberately does not.

    The Manager and every specialist receive **separate** memories, selected
    by owner, and the **same** calendar. Same temporal rules, different
    stores. A specialist's memory is built inside its builder, per call, which
    is what lets a persistent store re-read its file each tick.
    """
    registry = AgentRegistry()

    # The Manager's memory owner is his stable technical key, MANAGER -
    # never runtime.identity.name, which is only whatever IDENTITY.md's
    # declared name currently is ("Alfred", today). A specialist's store is
    # deliberately still selected by its own declared name - see
    # src/agents/specialists.py and ARCHITECTURE.md's entry on this: that is
    # an honest, tested, *different* policy this composition root does not
    # change, made for a specialist's own reasons. The Manager gets the
    # stable rule because he already has an unused stable key sitting right
    # here for exactly this purpose, and because he is the one agent whose
    # continuity this whole system is least able to afford losing by
    # accident. adopt_legacy_manager_memory() is the one-time bridge: if a
    # store already exists under an old, display-name-derived file (most
    # likely "alfred.json", from before this rule existed), it is carried
    # forward into the stable file the first time this runs after the fix -
    # never merged into or overwritten once a real "manager.json" exists.
    adopt_legacy_manager_memory(runtime.settings, runtime.identity.name)

    # Built here, as local values, rather than inside the ManagerAgent(...)
    # call below - ManagerSkills needs a bound way to read/forget this
    # Manager's own records (Stage 12) before the ManagerAgent that would
    # normally own the Memory exists. Passed to both collaborators below,
    # so there is exactly one Memory and one MemoryPolicy instance for
    # this Manager, never two. Writing is deliberately not threaded
    # through to ManagerSkills - see CapabilityRequest's own docstring:
    # scripts/exchange.py already retains what a person asks to be
    # remembered, through this exact policy, on every real turn.
    manager_memory = build_memory(runtime.settings, MANAGER)
    manager_memory_policy = ManagerMemoryPolicy()
    # Pre-Stage-9 Campaign 1: one canonical view of the person, built over
    # the Manager's own governed store (where everything taught in
    # conversation is written) and the per-run USER.md profile. Handed to
    # Alfred and granted, read-only, to every specialist below - never a
    # second store and never a copy.
    user_knowledge = UserKnowledge(base_profile=profile, recall=manager_memory.recall)

    # Built as a local rather than inline, so its governed write path
    # (``retain``) can be bound once the ManagerAgent that owns the real
    # one exists - see the bind_retain call just below. Everything else
    # about this construction is unchanged.
    own_skills = ManagerSkills(
        runtime.identity,
        roster=registry.all_agents,
        user=profile,
        user_knowledge=user_knowledge,
        # None here means "use ManagerSkills' own default" (MANAGER_CAPABILITIES,
        # unfiltered) - identical to never passing this parameter at all.
        capabilities=capabilities,
        # A second per-run document about the same person. Passed here and
        # never held in Runtime: a schedule read once at startup would
        # leave every later heartbeat tick reasoning about a week the user
        # has since changed, which is the same trap the profile avoids.
        schedule=schedule,
        # What Alfred is equipped to do himself. Empty until a caller
        # asks for something: this function is the shared composition
        # root every test in this project also builds a Manager
        # through, so its own default stays the conservative,
        # long-established one (see ManagerSkills.__init__). The real
        # production conversation path - scripts/control_center.py's
        # own build(), reused by scripts/serve_ui.py - is what
        # actually equips Alfred with the live_information tool,
        # backed by runtime.live_info; a caller here still gets
        # exactly what it asks for, and nothing added behind its back.
        tools=tools,
        # The same manager_brain given to ManagerAgent below - one
        # canonical Brain, never a second instance - so an open-ended
        # request Alfred takes on himself can reach genuine reasoning.
        # Bound rather than the brain object itself, matching every
        # other narrow collaborator ManagerSkills receives.
        converse=runtime.manager_brain.converse,
        # Files/Documents completion: the same canonical Brain's own
        # visual-reasoning question, bound the identical way - never a
        # Tool holding an LLMProvider directly.
        analyze_image=runtime.manager_brain.analyze_image,
        # Final Pre-Phase Interaction Completion: the same canonical
        # Brain's own ambiguity/guided-activity questions, bundled -
        # never a second Brain, never the brain object itself. See
        # ActivityBrainOps's own docstring for why this is four bound
        # callables rather than the brain.
        activity_brain=ActivityBrainOps.from_brain(runtime.manager_brain),
        # Context-Aware Agent Capability & Safety Governance: the same
        # canonical Brain's own narrow governance judgement, bound the
        # identical way - never a second Brain, never the brain object
        # itself.
        assess_governance=runtime.manager_brain.assess_governance,
        # Stage 12: the exact same store ManagerAgent itself uses
        # below - never a second memory.
        recall=manager_memory.recall,
        forget=manager_memory.forget,
        # Stage 12 closure: the single, Settings-selected provider every
        # agent's own celebration reaction now comes from, in place of
        # each agent silently constructing its own DefaultCelebrationProvider.
        celebration=runtime.celebration,
        # Stage 16 / Phase 7 (Universal Connection Layer): read-only
        # status access to every connection Alfred is equipped with -
        # see CapabilityRequest's own docstring for why only
        # connected_action ever reaches request.tools.invoke() to
        # actually execute anything through any of them.
        connections=runtime.connections,
        # Foundation Repair #1: the same real, timezone-configured
        # Calendar given to ManagerAgent below - one canonical clock,
        # never a second one - so current_datetime answers "what day is
        # it" from this person's actual local day, not bare UTC.
        calendar=runtime.calendar,
        # Real Audio Recording/Capture checkpoint: the same shared
        # registry Runtime holds - one live "is this agent recording
        # right now" signal, never a second one this ManagerSkills
        # tracks independently. Generic on this class (any agent could
        # receive one); only this, Alfred's own composition root,
        # actually wires it in.
        recording_sessions=runtime.recording_sessions,
        # Alfred's own declared surface, asked the same way EmailAgent's
        # hardcoded ``interface`` property already answers it - see
        # InterfaceDescriptor's own docstring. WORKSPACE is declared and
        # implemented: Alfred's 3D Workspace (src/services/identity/
        # workspace.py, wired into Control Center and the UI) is a real,
        # persistent, iterative environment, not a claim about something
        # planned. This is the one call site that decides it - ManagerSkills
        # itself stays generic, exactly as EmailAgent's own descriptor lives
        # only on EmailAgent and nowhere in the generic base class.
        interface=InterfaceDescriptor(
            agent=runtime.identity.name,
            display_name=runtime.identity.name,
            features=frozenset(
                {InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY, InterfaceFeature.WORKSPACE}
            ),
            implemented=frozenset(
                {InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY, InterfaceFeature.WORKSPACE}
            ),
            summary="coordinates specialists, and keeps a living 3D workspace for iterative work",
        ),
    )

    manager = ManagerAgent(
        registry=registry,
        router=runtime.router,
        brain=runtime.manager_brain,
        # Alfred is given who he serves as well as who he is. The profile was
        # already loaded per run for the specialist's analyzer and passed
        # straight through this function; the orchestrator accountable for the
        # mission simply never saw it. It arrives per call, like the memory
        # beside it, so an edit to USER.md takes effect on the next tick.
        own_skills=own_skills,
        memory=manager_memory,
        memory_policy=manager_memory_policy,
        calendar=runtime.calendar,
    )
    # Stage 12 addition: the one governed write path a capability handler
    # needing to *write* (record_this, milestone_recognition) reaches
    # through CapabilityRequest.retain - bound here, once, to the real
    # ManagerAgent.retain, never a second implementation of it. This is
    # the only place this call happens.
    own_skills.bind_retain(manager.retain)

    # Every registered specialist, built and registered the same way. This
    # loop is the whole of Step 3: before it, adding a specialist meant
    # writing another construction here; now it means adding an entry to
    # SPECIALISTS and shipping an identity document.
    #
    # Iterating the *resolved* mapping rather than the registry directly is
    # deliberate - the registry was consulted once at startup, so a specialist
    # cannot appear here without a brain and an identity having been built for
    # it, and there is no lookup that could miss.
    for entry in runtime.specialists.values():
        manager.register_agent(
            entry.build(
                SpecialistContext(
                    settings=runtime.settings,
                    calendar=runtime.calendar,
                    # What this specialist may reach in the world, keyed by its
                    # own technical key so equipping one agent equips exactly
                    # that agent. There is deliberately no way to grant
                    # something to "all specialists": an integration that
                    # reached every agent because one of them needed it would
                    # make "who can read that?" a question nobody could answer.
                    #
                    # Empty when nobody equipped it, which is the honest
                    # default - the agent then names what it cannot do rather
                    # than guessing.
                    tools=(specialist_tools or {}).get(entry.key) or ToolSet(),
                    provider=runtime.provider,
                    wiring=entry.wiring,
                    brain=entry.brain,
                    # Per-run, every run. A profile freshly loaded this tick
                    # is what lets a skill learned since the last one count.
                    profile=profile,
                    # The same canonical view Alfred reads, granted read-only.
                    user_knowledge=user_knowledge,
                    mailbox=mailbox,
                    fetch_limit=fetch_limit,
                    interpreter=runtime.interpreter,
                    # Stage 12 closure: the same single, shared provider
                    # own_skills above receives - one CelebrationProvider
                    # instance for the whole system, never one per agent.
                    celebration=runtime.celebration,
                )
            )
        )

    for agent in extra_agents:
        manager.register_agent(agent)

    return manager
