"""The Agent Control Center - the front door to Alfred.

    python scripts/control_center.py              interactive
    python scripts/control_center.py status       one overview, then exit
    python scripts/control_center.py status Arthur
    python scripts/control_center.py history --category memory
    python scripts/control_center.py talk "scan my inbox for job opportunities"

Three ways in, and no more:

    TALK TO MANAGER   say what you want; Alfred decides how it gets done
    STATUS            what is happening now
    HISTORY           what happened

**This file owns nothing.** It builds the system through the same composition
root main.py and the heartbeat use, then reads it through the Control Center.
It does not route, match a capability, assess a brain, admit a memory, register
a specialist or schedule a wake - every one of those belongs to a component
that already exists, and duplicating any of them here would make the front door
a second implementation of the house.

**You never pick a specialist.** You talk to Alfred. He decides whether he can
take the work on himself, whether it needs a tool he does not have, or whether
a specialist is better suited - and the trail printed beside each answer shows
which of those happened. That is the difference between an agent system and a
chat box, and it is why the trail is shown by default rather than hidden behind
a flag.

Read-only for Gmail specifically, exactly as every other entry point: the
Gmail token carries gmail.readonly, and no Outbox implementation exists - that
has not changed. Phase 4 (Real-World Communication) made general
communication (any recipient, any message, a real call) genuinely reachable
here too, but never silently: every consequential send or call is described
back and requires an explicit yes/no before anything actually happens (see
``src/manager/guided_interaction.py``'s own ``PendingConfirmation`` handling) -
the identical governed-confirmation gate every provider this axis will ever
have goes through, never a per-provider exception.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import fields, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exchange import exchange, memory_for, record_shared_file  # noqa: E402
from src.services.audit.action_log import (  # noqa: E402
    DEFAULT_ACTION_LOG_PATH,
    ActionLogEntry,
    load_action_log,
    record_action,
    utc_now as audit_utc_now,
)
from src.agents.tools.communication import (  # noqa: E402
    CommunicationCallTool,
    CommunicationReadTool,
    CommunicationSendTool,
)
from src.agents.tools.connection import ConnectionActionTool  # noqa: E402
from src.agents.tools.knowledge import KnowledgeLookupTool  # noqa: E402
from src.agents.tools.live_information import LiveInformationTool  # noqa: E402
from src.agents.tools.web_search import WebSearchTool  # noqa: E402
from src.agents.tools.document_generation import DocumentGenerationTool  # noqa: E402
from src.agents.tools.artifact_production import ArtifactProductionTool  # noqa: E402
from src.agents.tools.code_assistant import CodeAssistantTool  # noqa: E402
from src.agents.tools.image_intelligence import ImageIntelligenceTool  # noqa: E402
from src.agents.tools.translation_output import TranslationOutputTool  # noqa: E402
from src.agents.tools.workspace_navigation import WorkspaceNavigationTool  # noqa: E402
from src.agents.tools.model3d_production import Model3DProductionTool  # noqa: E402
from src.services.identity.voice_configuration import voice_facts  # noqa: E402
from src.providers.media.image_techniques import bind_governed_values as bind_image_technique_values  # noqa: E402
from src.services.artifacts.content import SHARED_FILE_PREFIX, artifact_ids_in, read_artifact_units  # noqa: E402
from src.composition import reasoning_settings_fields, rebind_backends, rebind_reasoning  # noqa: E402
from src.services.artifacts.delivery import available_delivery_methods  # noqa: E402
from src.services.artifacts.ingestion import IngestionError, ingest_supplied_file  # noqa: E402
from src.agents.tools.media_production import MediaProductionTool  # noqa: E402
from src.agents.tools.toolset import ToolSet  # noqa: E402
from src.channels.channel import Channel  # noqa: E402
from src.composition import (  # noqa: E402
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.coherence import ConfigurationError, check_configuration  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.control_center.observer import ControlCenter  # noqa: E402
from src.control_center.render import (  # noqa: E402
    render_agent,
    render_history,
    render_overview,
    render_calendar,
    render_experiences,
    render_governance,
    render_personal_context,
    render_recordings,
    render_workspaces,
    render_system,
    render_turn,
    render_unresolved_facts,
    render_voice,
    width,
)
from src.control_center.views import VoiceView  # noqa: E402
from src.conversation.conversation import Conversation  # noqa: E402
from src.providers.base import LLMProvider  # noqa: E402
from src.providers.connection import AuthorizationState, Connection  # noqa: E402
from src.providers.connection_resolver import ConnectionResolver  # noqa: E402
from src.providers.connection_factory import build_connection  # noqa: E402
from src.providers.factory import ProviderConfigurationError, available_providers, build_provider  # noqa: E402
from src.providers.media.factory import (  # noqa: E402
    available_video_generation_backends,
    available_video_understanding_backends,
)
from src.providers.speech_factory import (  # noqa: E402
    SpeechConfigurationError,
    available_speech_providers,
    build_speech,
)
from src.manager.cognition import assess_manager  # noqa: E402
from src.services.credentials.account import ProviderAccount  # noqa: E402
from src.services.credentials.authorization import (  # noqa: E402
    check_authorization,
    check_speech_authorization,
    check_vendor_authorization,
)
from src.services.credentials.account_store_file import (  # noqa: E402
    DEFAULT_ACCOUNTS_PATH,
    load_credential_store,
    save_credential_store,
)
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation, ServiceParameter  # noqa: E402
from src.providers.service_discovery import ServiceDiscoveryError, discover_service  # noqa: E402
from src.services.connections.service_registry import (  # noqa: E402
    DEFAULT_SERVICES_PATH,
    declare_service,
    forget_service,
    load_service_registry,
)
from src.providers.site_descriptor import SiteDescriptor  # noqa: E402
from src.services.connections.site_registry import (  # noqa: E402
    DEFAULT_SITES_PATH,
    declare_site,
    forget_site,
    load_site_registry,
)
from src.services.ecosystem.registry_factory import build_ecosystem_registry  # noqa: E402
from src.evolution import RiskLevel  # noqa: E402
from src.services.evolution import ecosystem_evolution  # noqa: E402
from src.services.evolution.governed_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    GovernedConfig,
    load_governed_config,
    save_governed_config,
)
from src.manager.capabilities import (  # noqa: E402
    MANAGER_CAPABILITIES,
    OPEN_CONVERSATION,
    ROSTER_REPORT,
    available_capabilities,
)
from src.manager.memory_policy import MemoryCategory  # noqa: E402
from src.services.memory.tasks import describe_task  # noqa: E402
from src.services.evolution.inventory import (  # noqa: E402
    CAPABILITY_GENERAL_CONVERSATION,
    CONNECTION_GMAIL_ADAPTER,
    DEFAULT_CONVERSATION_HISTORY_LIMIT,
    DEFAULT_GMAIL_ADAPTER_VERSION,
    TOOL_DOCUMENT_GENERATION,
    brain_development_snapshot,
    discover_components,
    memory_development_snapshot,
)
from src.services.evolution.ledger_store import DEFAULT_LEDGER_PATH, load_evolution_state  # noqa: E402
from src.services.evolution.ecosystem_evolution import (  # noqa: E402
    DEFAULT_BACKUPS_DIR,
    DEFAULT_STAGING_DIR,
)
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.identity.presence import (  # noqa: E402
    DIGITAL_HUMAN_STYLE_FIELDS,
    digital_human_style_for,
    environment_for,
    set_avatar_preference,
    set_digital_human_style,
    set_environment_preference,
)
from src.services.identity.voice_preference import (  # noqa: E402
    autonomous_voice_choice_for,
    effective_voice_for,
    set_voice_preference,
    voice_preference_for,
)
from src.services.memory.day import TimezoneError  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import ProfileError  # noqa: E402
from src.services.schedule.loader import load_schedule  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402

HELP = """
  talk <what you want>     ask Alfred to do something
  status                   the overview
  status <agent>           one agent: brain, memory, capabilities, tools
  status system            heartbeat, permissions, tools, health
  history                  what happened this session - rebuilt fresh every run
  history <category>       filter: memory, report
  actions                  the last 20 consequential tool actions, durably
                           recorded across restarts (see 'Memory' step
                           in a talk trail for when one is recorded)
  unresolved               personal facts Alfred knows only as uncertainty
  personal                 self-declared personal context taught in conversation
                           (skills, schedules, ...) - current state, per topic
  governance               the real, most-recent Context-Aware Agent Capability
                           & Safety Governance decisions - what was attempted,
                           by which capability/agent, and why it was allowed,
                           paused for clarification, sent through confirmation,
                           restricted, or refused. Read-only: a decision is
                           made only when a real governed action is attempted.
  calendar                 real, structured calendar items taught in conversation -
                           current state, per item
  experience               genuinely due events and the real visual/animation
                           experience selected for each - pending and delivered
  workspace                every agent with a declared 3D Workspace: real
                           visit history and its current task board - the
                           same backend state the live page reads
  workspace <agent> add|remove|use <type>
                           give an agent a workspace type, take one away,
                           or choose which one opens
  workspace type <id> <label>|<panel>,<panel>
                           define a brand-new kind of workspace from panels
                           the page already renders (tasks, diagram3d,
                           artifacts:<type>) - no code change
  workspace type <id> remove
                           withdraw an owner-defined workspace type
  recordings                one agent's real, saved audio recordings and
                           its current live recording session - starting/
                           stopping/cancelling stays conversational
  recordings <agent>       the same, for a different agent
  artifacts                every real, persisted artifact (documents/handouts)
  artifacts <id>           one artifact's full version history
  avatar                   every agent's current presence and avatar
  avatar <agent>           one agent's avatar, and what else it could be
  avatar <agent> <choice>  choose that agent's avatar - never a new agent
  environment              every agent's current environment
  environment <agent>      one agent's environment, and what else it could be
  environment <agent> <choice>
                           choose that agent's environment
  profile <agent>          that agent's fuller presentation style
  profile <agent> <field> <value>
                           set one presentation style dimension
  voice                    whether Alfred can hear and speak
  voice <agent>            that agent's current voice, and its choices
  voice <agent> <name>     choose that agent's voice - never Alfred's Brain
  review                   what Alfred has noticed about his own system
  capabilities             the real capability matrix - what Alfred can
                           actually do right now, and why not otherwise
  provider                 every ecosystem category and its backends
  provider <category>      one category's detail: active, dependents, accounts
  provider <category> accounts
                           every persisted account for that category's backends
  provider <category> add <backend> <account-id> <credential> [model]
                           register a new persisted account for that backend
  provider <category> select <backend> <account-id>
                           make an already-registered account active for that backend
  provider <category> revoke <backend> <account-id>
                           remove an account immediately
  provider <category> validate <key> [credential]
                           attempt real construction, report honestly -
                           no live external call is ever made
  provider llm_provider check <backend> [account-id]
                           ask the vendor whether that credential actually
                           works, by making one real request with it. The
                           only check that can answer "is my key funded and
                           accepted?" - so it leaves the machine, needs an
                           explicit confirmation, and is recorded in the
                           durable action log. Reports whether the vendor was
                           reached at all, separately from whether it said yes.

  Accounts are keyed by backend (e.g. 'anthropic', 'runway', 'null'),
  never by category - the same key build_provider/build_media/
  build_speech/build_communication_provider themselves read. Switching
  the active backend never deletes another account: 'select' only
  changes which one is active, 'revoke' is the only way to remove one.

  connections              every external app/service: Gmail's real OAuth
                           status, plus every additively-connected service
  connections gmail        Gmail's real availability/authorization
                           (OAuth via a token file - see
                           scripts/authorize_gmail.py - never a typed
                           credential)
  connections <service>    that service's connected accounts
  connections <service> connect <account-id> <credential> [model]
                           connect a service (e.g. whatsapp, telegram) -
                           never disconnects any other service
  connections <service> disconnect <account-id>
                           disconnect one account - every other
                           connection is unaffected
  connections <service> validate [credential]
                           real construction where a backend exists;
                           an honest refusal otherwise, never fabricated

  Connections are additive: WhatsApp, Telegram and Gmail (and any
  future service) can all be connected at once. There is no single
  "active connection" the way there is one active LLM/voice/video
  backend - connecting or disconnecting one never touches another.

  services                 every open-ended-discovery service declared so
                           far - name, base URL, operation count
  services <name>          one declared service's full detail
  services <name> discover <base-url>
                           read what that service already publishes -
                           its own .well-known/alfred-service.json, or
                           (falling back) its own OpenAPI/Swagger
                           document - and propose a declare command;
                           writes nothing, no confirmation needed,
                           never itself a connection
  services <name> declare <base-url> <op>:<METHOD>:<path>:<auth|open>[,...]
                           declare a legitimate new service - no code
                           change, ever, for a new company or website
  services <name> execute <operation> [key=value ...]
                           run one already-declared, already-connected
                           operation for real - a genuine network call,
                           the real result. A read (GET) needs no
                           confirmation; anything else does, exactly
                           like declare above. Authorization is checked
                           per operation, not for the whole service -
                           a harmless read is never blocked merely
                           because a different operation needs a
                           credential nobody has provided yet.
  services <name> forget   remove a declared service permanently

  A declared service becomes a real, usable connection (reachable
  through 'talk', the same 'connected account' capability Gmail already
  uses) once it also has a credential ('connections <name> connect ...'
  above) and is selected with MANAGER_CONNECTION_PROVIDER=<name> on the
  next start - the same "declared, credentialed, then selected" shape
  gmail already follows, generalised to any name.

  sites                     every declared website - name, origin,
                             whether it needs a session credential
  sites <name>               one declared site's full detail
  sites <name> declare <base-url> [auth-cookie-name]
                             declare a legitimate new website - no code
                             change, ever, for a new site. Its usable
                             fields/controls are never declared up
                             front: they are discovered live, every
                             visit, by the real, JS-capable browser
                             backend itself. auth-cookie-name is
                             optional - omit it for a site that needs
                             no login at all.
  sites <name> execute <operation> [key=value ...]
                             run one already-declared, already-
                             connected browser operation for real,
                             through the actual Chromium engine -
                             navigate/read_page/fill_field/click, the
                             identical fixed vocabulary every declared
                             site shares. navigate/read_page/fill_field
                             need no confirmation; click - the one
                             operation that can have a real effect on
                             the real site - needs the identical
                             confirm_token dance every other
                             consequential write here already requires.
  sites <name> forget        remove a declared site permanently

  A declared site becomes a real, usable connection (reachable through
  'talk', the identical 'connected account' capability every other
  connection already uses) the identical "declared, credentialed where
  needed, then selected" way a declared service does -
  MANAGER_CONNECTION_PROVIDER=<name> on the next start.

  RELOAD / RESTART REQUIRED: 'provider ... add/select/revoke' and
  'connections ... connect/disconnect' persist to disk immediately, but
  the already-running Manager was already built from whatever was
  active at startup - runtime.provider/media/communication/speech are
  each constructed once and do not hot-swap. Two ways to make a change
  live: type 'reload' right here to rebuild this session in place
  (ephemeral memory, if configured, is discarded by this - persisted
  memory is not), or restart scripts/control_center.py/export_ui.py/
  heartbeat.py, all of which read the same persisted state.

  reload                   rebuild this session now from current
                           settings and persisted accounts/connections
  reload <field>=<value>   also switch which BACKEND is active for this
                           session (e.g. 'reload provider=openai') -
                           the one thing 'provider ... select' cannot
                           do, since that only ever changes which
                           ACCOUNT authorises an already-selected
                           backend. Session-scoped, not persisted - set
                           the matching MANAGER_<FIELD> environment
                           variable to make it the default on every
                           future start.
  ecosystem                the whole ecosystem's real inventory: Alfred,
                           every specialist, every provider/connection
                           category, Brain mode, and the system/schema
                           Upgrade Manifest
  ecosystem <component>    one component's detail, version, and every
                           proposal on record for it
  ecosystem <component> propose <new-version> <risk> <reason>|<benefit>
                           propose a governed component upgrade (risk:
                           low/medium/high/critical)
  ecosystem <component> propose-content <path.md> <risk> <reason>|<benefit>
                           specialist/Alfred identity evolution only -
                           the real new document content, staged and
                           hashed, never a bare version string
  ecosystem <component> run <proposal-id> yes|no
                           evaluate -> approve -> apply -> verify ->
                           activate a proposal in one governed step -
                           the real side effect exists today for:
                           provider.<category> (an account's model),
                           alfred / specialist.<key> (the real identity
                           document, backed up first), tool.document_generation
                           (its real output directory),
                           capability.general_conversation (real
                           context-turn count), connection.gmail (its
                           real adapter version - never the account).
                           Anything else is refused honestly, never
                           fabricated.
  ecosystem <component> rollback <proposal-id> yes|no
                           reverse an ACTIVE proposal's real effect
  ecosystem history [component]
                           every governed transition on record
  ecosystem memory <agent> a real, developmental memory snapshot
                           (retained knowledge by kind, corrections,
                           learning sessions) - never a version, never
                           "more records means more capable"
  ecosystem brain <agent>  Brain development - reasoning mode,
                           corrections learned, lessons recorded -
                           read-only: never conflated with which
                           provider vendor answers (that is
                           provider.llm_provider)
  ecosystem system         the existing SYSTEM_VERSION/schema/visual-
                           environment Upgrade Manifest
  ecosystem system upgrade <target-system-version> yes|no
                           the real, existing governed system upgrade
                           pipeline (plan -> execute -> verify)
  help                     this
  quit                     leave
""".strip("\n")

# Natural Conversation: how many recent turns a capability handler that
# consults the Brain (open_conversation, knowledge_inquiry,
# live_information) is offered as background. Small on purpose - this is
# short-term context for a follow-up, not a transcript replay; a handful
# of turns is what a person means by "what were we just talking about".
CONVERSATION_HISTORY_LIMIT = 6


def _connection_tools(resolver: ConnectionResolver) -> "list[ConnectionActionTool]":
    """Grant a real ``ConnectionActionTool`` for every operation every
    connection ``resolver`` holds declares - the exact missing half of
    Stage 16, generalized (Phase 7 - Universal Connection Layer) from
    the one connection it originally covered to however many
    ``resolver`` actually equips this session with.

    **The gap this closes.** ``connected_action``
    (``src/manager/capabilities.py``) has always reached a connection
    only through ``request.tools.invoke(tool_name_for(connection_name,
    operation), ...)`` - a real ``Connection`` was never enough by
    itself, it also had to be *granted* under that name in the ToolSet
    Alfred's own process is built with. Nothing here ever did that
    granting: this file's own ``tools=ToolSet([...])`` below named
    every other collaborator (``runtime.communication``,
    ``runtime.media``, ...) but never ``runtime.connections``, so a
    live, authorized connection's own operation was unreachable
    through ordinary conversation regardless of how real, authorized
    or selected the connection itself was.

    **Why this is the right place, not composition.py.** Building each
    connection (``runtime.connections``) and granting a *tool* for one
    of its operations are different questions - the same reason
    ``runtime.communication`` is built once but
    ``CommunicationReadTool``/``SendTool``/``CallTool`` are granted here,
    not inside ``composition.py``. This is the one real place every
    other governed action-tool already gets constructed from its own
    runtime collaborator.

    **Every operation is granted, safe or consequential - the identical
    shape ``CommunicationSendTool``/``CallTool`` already have.** Those
    two tools have always been granted unconditionally even though a
    send/call is consequential; what makes that safe is that
    ``communication()`` never invokes them on the strength of one
    remark alone, it raises a ``PendingConfirmation`` first (Phase 4).
    Phase 6 (real-world services & transactions) gives ``connected_action``
    the identical governed confirm path for a consequential connection
    operation (a booking, an order, a cancellation), so the tool-grant
    boundary and the safety boundary can be the same one place they
    already are for communication: capability logic, never which tools
    happen to exist.

    **Connection-scoped tool names.** Two different connections
    declaring an operation of the same name (two declared services
    both offering "search", say) must never collide under one ToolSet
    entry once more than one connection can be granted at once - see
    ``tool_name_for``'s own docstring (``src/agents/tools/connection.py``).
    A future connection - of any kind, however many are simultaneously
    equipped - needs no change here either way: this reads only
    ``resolver.names`` and each connection's own
    ``supported_operations``/``parameters_for``.
    """
    tools: "list[ConnectionActionTool]" = []
    for name in resolver.names:
        connection = resolver.get(name)
        for operation in connection.supported_operations:
            tools.append(
                ConnectionActionTool(
                    connection,
                    operation=operation,
                    description=f"perform the already-declared '{operation}' action "
                    f"through the '{connection.name}' connection",
                    # Data-flow-through-execution checkpoint: the operation's
                    # own declared inputs (empty for a connection with
                    # nothing more precise to offer, Gmail/Null -
                    # unchanged), so a real, explicit value a step genuinely
                    # has can actually reach the connection, never merely a
                    # decision about which operation to call.
                    parameters=connection.parameters_for(operation),
                )
            )
    return tools


# Pre-Stage-9 Campaign 4: which provider category is the reasoning provider,
# and where the owner's chosen backend for it is persisted (the governed
# config every start reads - never an environment variable edited by hand).
LLM_CATEGORY = "llm_provider"
CHOSEN_LLM_BACKEND = "provider.llm_provider"


# The governed components that decide how agents reason, and the Settings
# field each one sets.
GOVERNED_REASONING_FIELDS = {
    CHOSEN_LLM_BACKEND: "provider",
    "brain.manager": "brain",
    "brain.specialist_default": "specialist_brain",
}


# Pre-Stage-9 Campaign 8: every other backend category the owner can choose,
# and the Settings field each one sets. Bound into tools at startup, so a
# persisted choice is honoured by every start.
GOVERNED_BACKEND_FIELDS = {
    "video_generation_provider": "video_generation",
    "video_understanding_provider": "video_understanding",
    "speech_provider": "speech",
    "media": "media",
    "image_generation_provider": "image_generation",
    "local_assist_provider": "local_assist",
    "translation_provider": "translation",
    "desktop_notification_provider": "desktop_notifications",
    "avatar_provider": "avatar_provider",
    "environment_provider": "environment_provider",
    "knowledge_provider": "knowledge_provider",
    "live_info_provider": "live_info_provider",
    "communication_provider": "communication_provider",
    "celebration_provider": "celebration_provider",
    "calendar_event_provider": "calendar_event_provider",
    "document_rendering_provider": "document_rendering",
}


def governed_backend_settings(settings: Settings, config) -> Settings:
    """``settings`` with every non-reasoning backend choice the owner saved
    through ``provider <category> use <backend>`` applied."""
    overrides = {
        field_name: value
        for category, field_name in GOVERNED_BACKEND_FIELDS.items()
        if (value := config.get(f"provider.{category}")) and value != getattr(settings, field_name)
    }
    return replace(settings, **overrides) if overrides else settings


def governed_reasoning_settings(settings: Settings, config) -> Settings:
    """``settings`` with every reasoning choice the owner governed applied -
    the vendor, the Manager's reasoning mode, the specialists' reasoning mode -
    so every start, and every live rebind, uses the same answer."""
    overrides = {
        field_name: value
        for component_id, field_name in GOVERNED_REASONING_FIELDS.items()
        if (value := config.get(component_id)) and value != getattr(settings, field_name)
    }
    return replace(settings, **overrides) if overrides else settings


def _reasoning_mode_note(settings: Settings, backend: str) -> str:
    """What the *other* reasoning axis is doing, said out loud.

    Which vendor answers (``provider.llm_provider``) and whether the Manager
    reasons with it at all (``brain.manager``) are deliberately separate -
    a vendor alone never turns reasoning on. That separation is right, but
    silence about it was not: "Switched the reasoning provider to 'local'
    live" read as "Alfred now reasons through local", while every request
    still answered "no reasoning capability is configured" and nothing on
    the screen explained why. Neither half is changed here - only whether
    the person is told which of the two switches is still where it was.
    """
    if settings.brain == "null" and backend.strip().lower() != "null":
        return (
            f" Note: the Manager's own reasoning mode is still 'null', so Alfred keeps reasoning "
            f"deterministically (Half Brain) and this backend answers nothing yet. Turn reasoning on with: "
            "ecosystem brain.manager propose llm low <reason>|<benefit>, then 'ecosystem brain.manager run "
            "<proposal-id> yes'."
        )
    if settings.brain != "null" and backend.strip().lower() == "null":
        return (
            f" Note: the Manager's reasoning mode is still '{settings.brain}', but with no vendor behind it "
            "every reasoning request now fails honestly. Set it back to 'null' with 'ecosystem brain.manager "
            "propose null ...' to return to deterministic Half Brain answers."
        )
    return ""


def _apply_reasoning_live(centre: "ControlCenter", store) -> str:
    """Rebind the running session's reasoning in place from what the owner has
    governed (vendor, account, reasoning modes) - the same Manager,
    conversation, memory and identity continue. A failure changes nothing and
    says so."""
    settings = governed_reasoning_settings(centre.runtime.settings, load_governed_config(DEFAULT_CONFIG_PATH))
    try:
        runtime = rebind_reasoning(centre.runtime, centre.manager, settings, accounts=store)
    except (ConfigurationError, ProviderConfigurationError, ValueError) as exc:
        return (
            f" Not applied to this running session: {exc}. The running session is unchanged."
        )
    centre.rebind_runtime(runtime)
    return (
        f" Applied live: this running session now reasons through '{settings.provider}' "
        f"(Manager reasoning mode '{settings.brain}', specialists '{settings.specialist_brain}') "
        "- same conversation, memory and identity."
    )


def _apply_backends_live(centre: "ControlCenter") -> str:
    """Rebind the running session's non-reasoning backends in place from what
    the owner has governed (backend choices, accounts, connection adapter) -
    the same Manager, conversation, memory, sessions and identity continue.
    Built all-or-nothing: a failure changes nothing and says so."""
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    settings = governed_backend_settings(centre.runtime.settings, config)
    try:
        runtime = rebind_backends(
            centre.runtime, centre.manager, settings,
            accounts=load_credential_store(DEFAULT_ACCOUNTS_PATH),
            connection_adapter_version=config.get(CONNECTION_GMAIL_ADAPTER, DEFAULT_GMAIL_ADAPTER_VERSION),
            tools=lambda live: manager_tools(
                live, settings, load_governed_config(DEFAULT_CONFIG_PATH), agent_names=lambda: _agent_names(centre.manager),
            ),
            channel=centre.channel,
        )
    except Exception as exc:  # noqa: BLE001 - every construction failure is reported, nothing is half-applied
        return (
            f" Saved, but not applied to this running session: {type(exc).__name__}: {exc}. "
            "The running session is unchanged."
        )
    centre.rebind_runtime(runtime)
    return " Applied live: this running session uses it now - the same conversation, memory and identity continue."


def _apply_evolution_live(centre: "ControlCenter", component_id: str) -> str:
    """Make an applied (or rolled-back) evolution live in this running session.

    The LLM category's account model is reasoning; a capability's governed
    configuration is read by the running session on every turn; an identity
    document is re-read by every new session (said plainly, never claimed
    live); every other governed value - a tool's configuration, a connection's
    adapter, a backend account's model, a newly registered category - is
    rebound through the same live backend path a fresh start would use."""
    if component_id == f"provider.{LLM_CATEGORY}":
        return _apply_reasoning_live(centre, load_credential_store(DEFAULT_ACCOUNTS_PATH))
    if component_id.startswith("capability.") or component_id.startswith("image_technique."):
        return " Live now: the running session reads this governed value on every turn."
    if component_id == "alfred" or component_id.startswith("specialist."):
        return (
            " The identity document is re-read by every new session - restart (or 'reload') to hear "
            "the evolved identity; this conversation keeps the one it started with."
        )
    return _apply_backends_live(centre)


def _new_family_member(components, component_id: str):
    """A component the inventory does not list yet that can genuinely begin
    from nothing - its handler supplies a starting version - and joins a family
    that really exists (a new surface kind for a real agent: ``surface.arthur.``
    already has members). Anything else is still "no such component"."""
    from src.services.evolution.handlers import handler_for
    from src.services.evolution.inventory import ComponentSnapshot

    handler = handler_for(component_id)
    initial = handler.initial_version(component_id) if handler is not None else None
    family, sep, member = component_id.rpartition(".")
    sibling = next((c for c in components if sep and c.component_id.startswith(family + ".")), None)
    if initial is None or sibling is None:
        return None
    return ComponentSnapshot(
        component_id=component_id, category=sibling.category,
        label=f"{member} (not declared yet)", version=initial,
    )


def render_voice_providers(channel) -> str:
    """Every speech provider this installation knows, which one is speaking,
    and what the active one can do - voices, spoken languages, accents, paces."""
    from src.providers.speech_factory import available_speech_providers

    speech = getattr(channel, "speech", None)
    active = getattr(speech, "name", "none")
    lines = ["VOICE PROVIDERS"]
    for name in available_speech_providers():
        marker = "*" if name == active else " "
        lines.append(f"  [{marker}] {name}")
    if speech is not None:
        facts = voice_facts(speech)
        lines.append(f"  active: {facts.provider} ({speech.availability.state.value})")
        lines.append(f"  voices: {len(facts.voices)}")
        lines.append(f"  speaks: {', '.join(facts.spoken_languages) or 'nothing reported'}")
        lines.append(f"  accents: {', '.join(facts.accents()) or 'none reported'}")
        lines.append(
            "  a voice can speak another language (keeping its accent): "
            + ("yes" if facts.across_languages else "no - each voice speaks only its own language")
        )
        lines.append(f"  paces: {', '.join(facts.paces) or 'not adjustable'}")
    lines.append("  switch with: provider speech_provider use <backend>   accounts: provider speech_provider add|select|revoke ...")
    lines.append("  per agent: voice <agent> <voice> | language <name|clear> | accent <name> | pace <word> | test <words>")
    return "\n".join(lines)


def run_voice_configuration_command(centre, channel, agent_name: str, agent, memory, action: str, value: str) -> str:
    """One agent's spoken language, accent or pace, planned against the provider
    that actually speaks - or a real spoken sample saved as an artifact."""
    from src.services.identity.voice_configuration import current_option, plan_voice, set_voice_setting, voice_setting_for
    from src.services.identity.voice_preference import effective_voice_for, set_voice_preference

    speech = getattr(channel, "speech", None)
    if speech is None:
        return f"the {channel.name} channel carries no speech engine"
    facts = voice_facts(speech)
    remember = memory.remember
    if action == "test":
        words = value or "This is how I sound right now."
        profile = effective_voice_for(agent, recall=memory.recall, discover_voices=getattr(speech, "available_voices", None))
        synthesize = getattr(speech, "synthesize", None)
        if profile is None or not callable(synthesize):
            return f"the {facts.provider} provider cannot produce a sample here"
        text = words
        if profile.language and hasattr(channel, "_in_spoken_language"):
            text, profile = channel._in_spoken_language(words, profile)
        try:
            audio = synthesize(text, profile)
        except Exception as exc:  # noqa: BLE001 - a provider failure is reported, never hidden
            return f"FAILED: the {facts.provider} provider could not speak that: {exc}"
        from src.services.artifacts.registration import register_new_artifact

        artifact = register_new_artifact(
            centre.runtime.artifacts, type="audio", owner_agent=agent_name,
            title=f"Voice sample - {agent_name}", content_bytes=text.encode("utf-8"),
            rendition_format="wav", rendition_bytes=audio,
            metadata={"content_kind": "audio", "media_type": "audio/wav", "provider": facts.provider},
        )
        return (
            f"Voice sample saved: artifact {artifact.artifact_id} ({len(audio)} bytes WAV, {facts.provider}) - "
            f"said: {text!r}. Play it from artifact/{artifact.artifact_id}.wav"
        )
    current = current_option(
        facts, effective_voice_for(agent, recall=memory.recall, discover_voices=getattr(speech, "available_voices", None)),
    )
    current_language = voice_setting_for(agent_name, "language", recall=memory.recall) or "english"
    if action == "language" and value.lower() in ("clear", "default", "none"):
        set_voice_setting(agent_name, "language", "", recall=memory.recall, remember=remember, forget=memory.forget)
        return f"{agent_name} speaks the language replies are written in again."
    plan = plan_voice(
        facts, current=current, current_language=current_language,
        language=value if action == "language" else None,
        accent=value if action == "accent" else None,
        pace=value.lower() if action == "pace" else None,
    )
    if not plan.ok:
        return f"FAILED: {plan.message}"
    if plan.voice is not None and (current is None or plan.voice.voice_id != current.voice_id):
        set_voice_preference(
            agent_name, plan.voice.name, available=tuple(v.name for v in facts.voices),
            recall=memory.recall, remember=remember, forget=memory.forget,
        )
    if plan.language:
        set_voice_setting(agent_name, "language", plan.language, recall=memory.recall, remember=remember, forget=memory.forget)
    if action == "accent" or (plan.voice is not None and (current is None or plan.voice.voice_id != current.voice_id)):
        # The accent asked for travels to other providers; a voice changed for
        # any other reason leaves no stale accent behind.
        set_voice_setting(agent_name, "accent", value.lower() if action == "accent" else "",
                          recall=memory.recall, remember=remember, forget=memory.forget)
    if plan.pace:
        set_voice_setting(
            agent_name, "pace", "" if plan.pace == "normal" else plan.pace,
            recall=memory.recall, remember=remember, forget=memory.forget,
        )
    return f"{agent_name} is now {plan.message} ({facts.provider}). Live now - the next thing said uses it."


def render_translation(interpreter) -> str:
    """The interpreter's real, current state, for a terminal or the served page."""
    if interpreter is None:
        return "TRANSLATION\n\n  Not assembled in this session."
    status = interpreter.status()
    lines = ["TRANSLATION", ""]
    lines.append(f"  engine:   {status['provider'] or 'none'}" + ("" if status["available"] else f" - {status['reason']}"))
    lines.append(f"  hearing:  {status['listener'] or 'none'}" + ("" if status["listens"] else f" - {status['listening_reason']}"))
    lines.append(f"  voice:    {status['voice'] or 'device voices only'}")
    lines.append("")
    for language in status["languages"]:
        translates = "translate" if language["translatable"] else "-"
        speaks = "local voice" if language["speakable"] else ("device voice" if language["translatable"] else "-")
        lines.append(f"  {language['name']:<11} {language['code']:<3} {translates:<10} {speaks}")
    if status["pairs"]:
        lines.append("")
        lines.append("  installed packs: " + ", ".join(status["pairs"]))
    from src.services.translation.outputs import translation_outputs

    lines.append("")
    lines.append("  outputs: " + ", ".join(translation_outputs()))
    lines.append("  e.g. 'translate this video into English subtitles', or the Translate panel for live speech")
    return "\n".join(lines)


def run_workspace_type_definition_command(rest: str) -> str:
    """Define a brand-new workspace type, or withdraw one the owner defined -
    ``workspace type <id> <label>|<panel>,<panel>`` / ``workspace type <id> remove``.

    A type is data (``workspace_catalog``): a label and panels the page already
    renders generically (``tasks``, ``diagram3d``, ``artifacts:<type>``), so an
    open-ended new kind of workspace needs no code. Refused honestly when a
    panel cannot be rendered; the built-in types cannot be withdrawn here."""
    from src.control_center.workspace_catalog import (
        DEFAULT_WORKSPACE_TYPES, PANEL_KINDS, WORKSPACE_TYPE_PREFIX, WorkspaceType,
    )

    type_id, _sep, spec = rest.strip().partition(" ")
    ident = "_".join(type_id.strip().lower().split())
    if not ident or not spec.strip():
        return (
            "usage: workspace type <id> <label>|<panel>,<panel>  (panels: tasks, diagram3d, artifacts:<type>)"
            "  or  workspace type <id> remove"
        )
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    key = WORKSPACE_TYPE_PREFIX + ident
    if spec.strip().lower() == "remove":
        if ident in {t.workspace_id for t in DEFAULT_WORKSPACE_TYPES}:
            return f"'{ident}' is a built-in workspace type and stays - remove it from an agent instead."
        if config.get(key) is None:
            return f"no owner-defined workspace type '{ident}'."
        save_governed_config(config.without_value(key), DEFAULT_CONFIG_PATH)
        return f"Removed the workspace type '{ident}'. Saved - the page shows it on its next refresh."
    label, bar, panels = spec.partition("|")
    if not bar:
        return "give the panels after '|', e.g. 'workspace type research Research|tasks,artifacts:document'"
    try:
        defined = WorkspaceType(ident, label.strip() or ident, "Added by the owner.", tuple(panels.split(",")))
    except ValueError as exc:
        return f"FAILED: {exc}. Panels the page renders: {', '.join(PANEL_KINDS)} (artifacts:<type>)."
    save_governed_config(config.with_value(key, f"{defined.label}|{','.join(defined.panels)}"), DEFAULT_CONFIG_PATH)
    return (
        f"Defined the workspace type '{defined.workspace_id}' ({defined.label}: {', '.join(defined.panels)}). "
        f"Give it to an agent with 'workspace <agent> add {defined.workspace_id}'. Saved - the page shows it on its next refresh."
    )


WORKSPACE_DEFINITION_ACTIONS = ("create", "rename", "describe", "profile", "panels", "archive", "restore", "remove")


def run_workspace_definition_command(centre: "ControlCenter", action: str, rest: str) -> str:
    """The owner's workspaces, managed from here - every one, the built-in rows
    included:

        workspace create <id> <label>|<panel>,<panel>[|<capability>,<capability>]
        workspace rename <id> <new label>
        workspace describe <id> <what it is for>
        workspace profile <id> <capability>,<capability> | all
        workspace panels <id> <panel>,<panel>
        workspace archive <id>   /   workspace restore <id>
        workspace remove <id>    (one the owner created; a built-in is archived instead)

    A capability profile names Manager capabilities or specialist capability
    words that really exist - checked against the live registry, never a list
    kept here. Saved in the governed config; live at once."""
    from src.control_center.workspace_catalog import (
        DEFAULT_WORKSPACE_TYPES, PANEL_KINDS, WORKSPACE_TYPE_PREFIX, WorkspaceType, all_workspaces,
    )
    from dataclasses import replace as _replace

    ident_raw, _sep, value = rest.strip().partition(" ")
    ident = "_".join(ident_raw.strip().lower().split())
    value = value.strip()
    if not ident:
        return "usage: workspace " + action + " <id> ..." + "  (actions: " + ", ".join(WORKSPACE_DEFINITION_ACTIONS) + ")"
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    existing = {t.workspace_id: t for t in all_workspaces(config.values)}
    key = WORKSPACE_TYPE_PREFIX + ident
    built_in = ident in {t.workspace_id for t in DEFAULT_WORKSPACE_TYPES}

    def known_capabilities() -> "set[str]":
        names = set(MANAGER_CAPABILITIES)
        for agent in centre.manager.registry.all_agents():
            names |= {str(c).lower() for c in agent.spec.capabilities}
        return names

    def parse_profile(text: str) -> "tuple[tuple[str, ...], str | None]":
        if text.strip().lower() in ("all", "any", "everything", ""):
            return (), None
        wanted = tuple(dict.fromkeys("_".join(c.strip().lower().split()) for c in text.split(",") if c.strip()))
        unknown = [c for c in wanted if c not in known_capabilities()]
        if unknown:
            return wanted, (
                f"FAILED: no capability called {', '.join(repr(u) for u in unknown)}. "
                "See 'capabilities list' for the Manager's, and 'agents' for each specialist's."
            )
        return wanted, None

    def save(defined: "WorkspaceType", message: str) -> str:
        save_governed_config(config.with_value(key, defined.to_value()), DEFAULT_CONFIG_PATH)
        reread = {t.workspace_id: t for t in all_workspaces(load_governed_config(DEFAULT_CONFIG_PATH).values)}.get(ident)
        if reread != defined:
            return f"FAILED: wrote the workspace '{ident}' but the independent re-read did not match"
        return message + " Saved - live now, and every start uses it."

    if action == "create":
        if ident in existing:
            return f"a workspace '{ident}' already exists ({existing[ident].label}) - rename or profile it instead."
        label, bar, remainder = value.partition("|")
        panels, _bar2, profile_text = remainder.partition("|")
        if not bar:
            return "give the panels after '|', e.g. 'workspace create research Research lab|tasks,produced|translate_media'"
        profile, problem = parse_profile(profile_text)
        if problem:
            return problem
        try:
            defined = WorkspaceType(ident, label.strip() or ident, "Created by the owner.", tuple(panels.split(",")), profile)
        except ValueError as exc:
            return f"FAILED: {exc}. Panels the page renders: {', '.join(PANEL_KINDS)} (artifacts:<type>)."
        from src.control_center.workspace_catalog import workspaces_for

        created = save(defined, f"Created the workspace '{defined.workspace_id}' ({defined.label}; panels {', '.join(defined.panels)}; "
                                f"{'capabilities ' + ', '.join(defined.capabilities) if defined.capabilities else 'any work'}).")
        if created.startswith("FAILED"):
            return created
        values = load_governed_config(DEFAULT_CONFIG_PATH).values
        names = _agent_names(centre.manager)
        having = [n for n in names if ident in {t.workspace_id for t in workspaces_for(n, values)}]
        narrowed = [n for n in names if n not in having]
        return created + (
            f" Agents who have it: {', '.join(having) or 'none'}."
            + (f" Agents with a chosen list get it with: workspace <agent> add {ident} ({', '.join(narrowed)})." if narrowed else "")
        )
    current = existing.get(ident)
    if current is None:
        return f"no workspace '{ident}'. Workspaces: {', '.join(existing)}"
    if action == "rename":
        if not value:
            return "usage: workspace rename <id> <new label>"
        return save(_replace(current, label=value), f"Renamed '{ident}' from {current.label} to {' '.join(value.split())} - same workspace, same id.")
    if action == "describe":
        return save(_replace(current, description=value), f"Described '{ident}': {value or '(no description)'}.")
    if action == "profile":
        profile, problem = parse_profile(value)
        if problem:
            return problem
        return save(_replace(current, capabilities=profile),
                    f"The {current.label} workspace is now " + (f"set up for {', '.join(profile)}." if profile else "open to any work."))
    if action == "panels":
        try:
            defined = _replace(current, panels=tuple(value.split(",")))
        except ValueError as exc:
            return f"FAILED: {exc}. Panels the page renders: {', '.join(PANEL_KINDS)} (artifacts:<type>)."
        return save(defined, f"The {current.label} workspace now shows {', '.join(defined.panels)}.")
    if action in ("archive", "restore"):
        archived = action == "archive"
        if current.archived == archived:
            return f"the {current.label} workspace is already {'archived' if archived else 'in use'}."
        return save(_replace(current, archived=archived),
                    f"{'Archived' if archived else 'Restored'} the {current.label} workspace" +
                    (" - no agent works in it until it is restored; what was made there is kept." if archived else "."))
    if action == "remove":
        if built_in:
            return f"'{ident}' is a built-in workspace - archive it instead: workspace archive {ident}"
        save_governed_config(config.without_value(key), DEFAULT_CONFIG_PATH)
        return f"Removed the workspace '{ident}' ({current.label}); what was made there is kept. Saved - live now."
    return "actions: " + ", ".join(WORKSPACE_DEFINITION_ACTIONS)


def run_workspace_types_command(centre: "ControlCenter", agent_name: str, action: str, type_id: str) -> str:
    """Give an agent a workspace type, take one away, or choose which is open -
    any agent, any type in the catalog, saved in the governed config."""
    from src.control_center.workspace_catalog import (
        AGENT_ACTIVE_WORKSPACE_PREFIX, AGENT_WORKSPACES_PREFIX, agent_key, workspace_catalog, workspaces_for,
    )

    names = {a.name.lower(): a.name for a in centre.overview().agents}
    agent = names.get(agent_name.lower())
    if agent is None:
        return f"no agent called '{agent_name}'. Registered: {', '.join(sorted(names.values()))}"
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    values = config.values
    catalog = {t.workspace_id: t for t in workspace_catalog(values)}
    wanted = "_".join(type_id.strip().lower().split())
    if wanted not in catalog:
        return f"no workspace type '{type_id}'. Available: {', '.join(catalog)}"
    current = [t.workspace_id for t in workspaces_for(agent, values)]
    key = agent_key(agent)
    if action == "add":
        if wanted in current:
            return f"{agent} already has the '{wanted}' workspace."
        config = config.with_value(AGENT_WORKSPACES_PREFIX + key, ",".join(current + [wanted]))
        message = f"{agent} now has the '{catalog[wanted].label}' workspace."
    elif action == "remove":
        if wanted not in current:
            return f"{agent} does not have the '{wanted}' workspace."
        remaining = [w for w in current if w != wanted]
        config = config.with_value(AGENT_WORKSPACES_PREFIX + key, ",".join(remaining))
        message = f"Removed the '{catalog[wanted].label}' workspace from {agent}" + ("." if remaining else " - no workspace types remain.")
    else:
        if wanted not in current:
            return f"{agent} does not have the '{wanted}' workspace - add it first: workspace {agent} add {wanted}"
        config = config.with_value(AGENT_ACTIVE_WORKSPACE_PREFIX + key, wanted)
        message = f"{agent}'s Workspace now opens on '{catalog[wanted].label}'."
    save_governed_config(config, DEFAULT_CONFIG_PATH)
    return message + " Saved - the page shows it on its next refresh."


def _governed_control_center(runtime, manager) -> "ControlCenter":
    """A Control Center view reading governed surface states from this
    module's own config path, looked up at call time (Pre-Stage-9 Campaign 5)."""
    centre = ControlCenter(runtime, manager)
    centre.bind_governed_config(lambda: load_governed_config(DEFAULT_CONFIG_PATH).values)
    return centre


def _governed_image_values():
    return load_governed_config(DEFAULT_CONFIG_PATH).values


def _agent_names(manager) -> "tuple[str, ...]":
    """Every agent really registered with this Manager, the Manager first."""
    if manager is None:
        return ()
    names = [manager.own_skills.name] if manager.own_skills is not None else []
    names += [a.name for a in manager.registry.all_agents() if a.name not in names]
    return tuple(names)


def _save_governed_value(key: str, value: str) -> None:
    save_governed_config(load_governed_config(DEFAULT_CONFIG_PATH).with_value(key, value), DEFAULT_CONFIG_PATH)


def workspace_tool(agent_names) -> WorkspaceNavigationTool:
    """The workspace tool over the governed config this module reads and writes,
    for the agents ``agent_names()`` reports as registered."""
    return WorkspaceNavigationTool(
        load_values=lambda: load_governed_config(DEFAULT_CONFIG_PATH).values,
        save_value=_save_governed_value, agent_names=agent_names,
    )


def bind_workspaces(runtime, manager) -> None:
    """Workspaces reach the running system: the scope every agent's work is
    checked against, movement between workspaces for any agent, and each new
    artifact recording the workspace its agent was in - all read per call from
    the governed config, so a Control Center change is live at once."""
    from src.control_center.workspace_catalog import workspace_artifact_metadata, workspace_refusal
    from src.manager.capabilities import workspace_navigation_reply

    def values():
        return load_governed_config(DEFAULT_CONFIG_PATH).values

    def navigate(agent_name, task):
        tools = manager.own_skills.tools if manager.own_skills is not None else None
        if tools is None or not tools.permits("workspace"):
            return None
        return workspace_navigation_reply(task, agent_name, lambda arguments: tools.invoke("workspace", arguments))

    manager.bind_workspaces(scope=lambda agent, names: workspace_refusal(agent, values(), names), navigation=navigate)
    runtime.artifacts.bind_creation_context(lambda owner: workspace_artifact_metadata(owner, values()))


def manager_tools(runtime, settings: Settings, config, agent_names=None) -> ToolSet:
    """Everything the Manager is equipped with, built from ``runtime``'s current
    backends. One construction, used by ``build`` at start and by a live
    backend rebind (``_apply_backends_live``) - a backend the owner switches in
    the Control Center reaches the running Manager through the identical tools
    a fresh start would give him, never a second assembly."""
    bind_image_technique_values(_governed_image_values)
    return ToolSet([
            LiveInformationTool(runtime.live_info),
            WebSearchTool(runtime.live_info),
            CommunicationReadTool(runtime.communication),
            CommunicationSendTool(runtime.communication),
            CommunicationCallTool(runtime.communication),
            # Natural-language/live-connection integration gap closure,
            # generalized to every connection this session is equipped
            # with (Phase 7 - Universal Connection Layer): a real
            # ConnectionActionTool for every operation each one
            # declares, safe or consequential - see _connection_tools's
            # own docstring for exactly what gap this closes and why
            # granting a consequential operation's tool is safe (the
            # identical confirm-before-execute boundary
            # CommunicationSendTool/CallTool already rely on, never the
            # tool grant itself). Empty for an installation equipped
            # with only "null" (no operations at all) and unchanged for
            # every existing single-connection caller.
            *_connection_tools(runtime.connections),
            # Stage 18: read-only access to the documents, books and notes
            # the user themselves put in runtime.knowledge's folder. It
            # sits beside live_information rather than replacing it -
            # "what the world looks like now" and "what this person gave
            # me" are separate reaches, separately granted - and it
            # honours this file's own read-only invariant: a knowledge
            # source can be quoted and can do nothing else.
            KnowledgeLookupTool(runtime.knowledge),
            # Creative-media / AI video-generation / video-clipping /
            # video-understanding checkpoints: real narration/image/video
            # production, real AI video generation, real local video
            # clipping, and real speech transcription over a video's own
            # audio, backed by runtime.media - honestly absent (every
            # Null* collaborator) unless MANAGER_MEDIA,
            # MANAGER_VIDEO_GENERATION and/or MANAGER_VIDEO_UNDERSTANDING
            # were deliberately configured, the identical opt-in default
            # every other real capability here already follows. Clipping
            # needs no vendor configuration of its own (see
            # MediaTools.clipper's own docstring) - it is real
            # (FFmpegVideoClipper) whenever MANAGER_MEDIA selects
            # "local"/"ai_image", null only for the "null" media backend,
            # exactly mirroring composer. Transcription is vendor-backed
            # like video_generator, and independently configured.
            MediaProductionTool(
                runtime.media.narrator,
                runtime.media.illustrator,
                runtime.media.composer,
                runtime.media.video_generator,
                runtime.media.clipper,
                runtime.media.transcriber,
                asset_dir=Path(settings.media_asset_dir),
                # Ecosystem artifact/workspace checkpoint: every real
                # narration/image/composed-video/AI-generated-video/clip
                # this tool actually produces is now also a real,
                # persistent, versioned Artifact in the identical
                # runtime.artifacts store ArtifactProductionTool's own
                # document/image artifacts already live in - never a
                # second store, never Alfred-specific machinery (any
                # future specialist's own MediaProductionTool instance
                # gets this identically, by its own owner_agent name).
                artifacts=runtime.artifacts,
                owner_agent=runtime.identity.name,
            ),
            # Phase 3: real, saved text documents ("create a report from
            # these findings") - a governed write boundary, the identical
            # role MediaProductionTool's own asset_dir plays for media.
            # Full Alfred OS Ecosystem Evolution: the output directory is
            # now a real, governed tool-configuration value
            # (tool.document_generation) - config.get(...) falls back to
            # settings.documents_dir unchanged when nothing was ever
            # evolved, so an unconfigured installation behaves identically.
            DocumentGenerationTool(output_dir=Path(config.get(TOOL_DOCUMENT_GENERATION, settings.documents_dir))),
            # Multimodal workspace/artifact checkpoint: real, persistent,
            # versioned artifacts (documents and images/diagrams) -
            # genuinely different from DocumentGenerationTool above (one
            # flat, disposable file, no identity, no version chain).
            # Backed by runtime.artifacts (the same store the Control
            # Center's own `artifacts` command reads - one authoritative
            # record, never a second copy), runtime.document_renderer
            # (real PDF export by default; "null" only if
            # MANAGER_DOCUMENT_RENDERING was deliberately set to it),
            # and runtime.media.illustrator - the identical ImageProvider
            # media_production's own "illustrate" operation already
            # reaches, never a second image-generation mechanism, so a
            # diagram/image artifact is exactly as real (and exactly as
            # honestly "null" by default) as any other AI-image
            # capability this project already has. owner_agent is this
            # Manager's own declared name, never hardcoded - the
            # identical name-driven ownership every other per-agent
            # construction here already uses, so a future specialist's
            # own instance of this exact class needs no code change,
            # only its own name.
            ArtifactProductionTool(
                runtime.artifacts, runtime.document_renderer, runtime.media.illustrator,
                owner_agent=runtime.identity.name,
            ),
            # Post-Pre-Stage-9: fills the code_assistant extension point -
            # written code saved as a versioned artifact in the coding
            # workspace. Never writes or runs code itself.
            CodeAssistantTool(runtime.artifacts, owner_agent=runtime.identity.name),
            # Whole-ecosystem campaign: image transformation, enhancement,
            # reconstruction and comparison - an open technique registry read
            # from the governed config on every call, so a technique evolved in
            # this Control Center is live at once.
            ImageIntelligenceTool(
                runtime.artifacts, owner_agent=runtime.identity.name, governed_values=_governed_image_values,
            ),
            # Genuine 3D content: parts assembled into a checked glTF mesh.
            Model3DProductionTool(runtime.artifacts, owner_agent=runtime.identity.name),
            # Translation routed to outputs - subtitles, a subtitled video,
            # translated speech, the live display - from shared media.
            TranslationOutputTool(
                interpreter=getattr(runtime, "translation", None), transcriber=runtime.media.transcriber,
                artifacts=runtime.artifacts, owner_agent=runtime.identity.name,
                display=getattr(runtime, "translation_display", None),
            ),
            # Moving between the workspaces the owner created - one saved fact
            # the Control Center, the page and a restart all read.
            workspace_tool(agent_names or (lambda: (runtime.identity.name,))),
            # Files/Documents completion: real visual reasoning reaches
            # Alfred through the Brain's own analyze_image question
            # (bound below via ManagerSkills, exactly like converse) -
            # never a Tool, since a Tool must never hold an LLMProvider
            # directly. Nothing to grant here.
    ])


def build(settings: Settings):
    """Assemble the system exactly as the other entry points do.

    Same composition root, same per-run documents. The Control Center observes
    what everything else already builds rather than arranging its own version,
    which is what keeps the screen describing the real program.

    **Universal provider/connection management repair.** Before this,
    ``build_runtime`` was called bare here - the persisted
    ``CredentialStore`` (``data/credentials/accounts.json``, the very
    file the ``provider <category> add/select/revoke`` command writes)
    was never consulted by the actual running system, for any category.
    An account added or selected through this Control Center genuinely
    updated the file and nothing else. Fixed by loading that same store
    once, here, and passing each category's currently-active account
    into ``build_runtime`` exactly as ``test_phase_1_5_provider_accounts.py``
    already exercises directly - the identical, pre-existing seam, now
    actually reached from the real production entry point rather than
    only from a test.

    **What this does not yet do.** This makes a selection made *before*
    this process starts take effect *at* that start - restart, not live
    reload. Changing an account while this Control Center is already
    running still has no effect on it; the next command still only
    writes the file. Live, in-session activation remains a separate,
    unimplemented gap (see the ``provider`` command's own help text).
    """
    accounts = load_credential_store(DEFAULT_ACCOUNTS_PATH)
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    # Pre-Stage-9 Campaign 4: the reasoning backend the owner chose in the
    # Control Center ("provider llm_provider use <backend>") is the one every
    # start uses.
    settings = governed_backend_settings(governed_reasoning_settings(settings, config), config)
    # The Manager the workspace tool names agents from - it exists only once
    # built below, and the tool asks per call.
    built_manager: list = []
    runtime = build_runtime(
        settings,
        accounts=accounts,
        video_generation_account=accounts.active(settings.video_generation),
        video_understanding_account=accounts.active(settings.video_understanding),
        speech_account=accounts.active(settings.speech),
        communication_account=accounts.active(settings.communication_provider),
        # Full Alfred OS Ecosystem Evolution: the one real, governed
        # connection-*adapter* axis this checkpoint added - genuinely
        # distinct from the account/credential `accounts` above already
        # governs. Defaults to "1" (this project's own prior, unchanged
        # behaviour) unless a real evolution selected "2".
        connection_adapter_version=config.get(CONNECTION_GMAIL_ADAPTER, DEFAULT_GMAIL_ADAPTER_VERSION),
    )

    try:
        profile = load_profile(settings.profile_path)
    except ProfileError:
        # Degrades rather than stopping, as main.py does: a Control Center that
        # refused to open because USER.md was missing would be useless exactly
        # when someone needs to look at why.
        profile = None

    manager = build_manager(
        runtime,
        mailbox=build_mailbox(settings),
        profile=profile,
        schedule=load_schedule(settings.schedule_path),
        # Stage 14: the one real tool Alfred is equipped with on this,
        # the actual production conversation path (this function is also
        # what scripts/serve_ui.py builds through). Backed by whatever
        # runtime.live_info resolved to - honestly empty (NullLiveInfoProvider)
        # unless MANAGER_LIVE_INFO_PROVIDER was deliberately configured.
        # Phase 3 (Knowledge, Research & Live Information): "web_search" is
        # no longer unfilled - WebSearchTool wraps this same runtime.live_info
        # provider under the tool name general_assistance's own "research"
        # GeneralCapability already names, so a real, governed research path
        # exists with no new backend and no change to that capability's own
        # routing. "code_assistant" is filled post-Pre-Stage-9 by
        # CodeAssistantTool below (it saves written code; it never writes it).
        #
        # Stage 15 granted only CommunicationReadTool here - deliberately,
        # while send/call had no confirmation gate at all, so "a Brain
        # producing text" could never by itself become "a message actually
        # sent." Phase 4 (Real-World Communication) built that gate
        # (PendingConfirmation, see src/manager/guided_interaction.py) and
        # grants Send/Call here now: every attempt still describes itself
        # and waits for an explicit yes/no before anything with a real
        # external effect happens, backed by whatever runtime.communication
        # actually resolved to - honestly UNAVAILABLE (NullCommunicationProvider)
        # unless a real provider is deliberately configured, the identical
        # opt-in default every other real capability here already follows.
        tools=manager_tools(runtime, settings, config, agent_names=lambda: _agent_names(built_manager[0] if built_manager else None)),
        # Control Center repair item #1: the one real enforcement point for
        # capability enable/disable. MANAGER_CAPABILITIES with every
        # genuinely-disabled entry removed, computed from the identical
        # `config` already loaded above for connection_adapter_version -
        # never a second read. A disabled capability is absent from this
        # dict, so select_capability() - given this dict by ManagerSkills,
        # see manager_skills.py's own docstring on why this parameter is
        # injectable - can never claim, score, or run it, for the life of
        # this process.
        capabilities=filtered_manager_capabilities(config),
    )
    # The door onto that Manager, built by the composition root rather than
    # here. This file used to reach the Manager directly and construct its own
    # NoSpeech to report on; both are now decisions taken in one place, which
    # is what makes the screen describe the real program instead of a local
    # copy of it.
    conversation = build_conversation(manager)

    # Natural Conversation: the real Conversation exists only from this
    # point on, so this is the one place - the identical construction-order
    # reason own_skills.bind_retain is called from composition.py rather
    # than passed to ManagerSkills directly - that can close the loop.
    # Bound, never handed over as the Conversation object itself: a
    # capability handler receives only recent lines to reason over, never
    # a way to read, join or say anything into the room.
    if manager.own_skills is not None:
        # Full Alfred OS Ecosystem Evolution: the turn count is now a
        # real, governed capability-configuration value
        # (capability.general_conversation) - falls back to this
        # module's own, unchanged CONVERSATION_HISTORY_LIMIT default
        # when nothing was ever evolved.
        # Read on every turn, so an evolution applied in the Control Center is
        # live in this running session, not only in the next one.
        def _history_limit() -> int:
            governed = load_governed_config(DEFAULT_CONFIG_PATH)
            return int(governed.get(CAPABILITY_GENERAL_CONVERSATION, DEFAULT_CONVERSATION_HISTORY_LIMIT))

        manager.own_skills.bind_conversation_history(
            lambda: [f"{u.speaker}: {u.text}" for u in conversation.latest(_history_limit())]
            if len(conversation)
            else []
        )
        # Pre-Stage-9 Campaign 3: every file shared in this room, newest first,
        # read from the room's own record of the shares - never limited to the
        # short history window above.
        manager.own_skills.bind_shared_files(
            lambda: artifact_ids_in(
                u.text for u in conversation.utterances if u.text.startswith(SHARED_FILE_PREFIX)
            )
        )
        # Agent-autonomous voice selection checkpoint: bound to whichever
        # real speech adapter runtime.speech actually is - never a second
        # discovery mechanism, and never assumed present. An adapter with
        # no available_voices() (NoSpeech, or an engine this checkpoint
        # did not add discovery to) yields an honestly empty tuple rather
        # than an AttributeError, the same "ask, don't assume" rule
        # request.tools.permits already lives under.
        _discover = getattr(runtime.speech, "available_voices", None)
        manager.own_skills.bind_discover_voices(_discover if _discover is not None else (lambda: ()))

    built_manager.append(manager)
    bind_workspaces(runtime, manager)

    channel = build_channel(runtime, manager)
    if manager.own_skills is not None:
        # The provider that actually speaks - read through the channel, so a
        # live provider switch is what the next voice request plans against.
        manager.own_skills.bind_voice_facts(lambda: voice_facts(channel.speech))
    return (
        runtime,
        manager,
        channel,
        conversation,
    )


# ---------------------------------------------------------------------------
# Files, Documents & Control Center completion: the real capability matrix
# and provider-management surface, reusing the Universal Ecosystem registry
# and the file-backed CredentialStore exactly as built for those - never a
# second UI, never a second credential mechanism, never a hardcoded provider
# switch. This is the Control Center's own user-facing management path:
# routine provider/component changes happen here, at a terminal a person
# already has open, never by editing source code.
# ---------------------------------------------------------------------------


def _capability_row(name: str, *, ready: bool, detail: str) -> str:
    state = "READY" if ready else "UNAVAILABLE"
    return f"  [{state:^11}] {name} - {detail}"


def render_capabilities(centre: "ControlCenter") -> str:
    """The real capability matrix - never a card shown because a class
    exists. Every line below reads an actual runtime fact (a granted
    tool, the Manager's own assessed Brain mode, a configured provider's
    real name) and reports a state a person can act on, exactly as
    Section 9 of this checkpoint's own brief asks for.
    """
    runtime = centre.runtime
    manager = centre.manager
    tools = manager.own_skills.tools if manager.own_skills is not None else None
    brain = assess_manager(manager)

    def permits(tool_name: str) -> bool:
        return tools is not None and tools.permits(tool_name)

    lines = ["CAPABILITIES - the real, current state, not what merely exists in code", ""]

    lines.append(_capability_row(
        "General Knowledge", ready=brain.is_full_brain and runtime.knowledge.name != "null",
        detail=(
            f"knowledge source={runtime.knowledge.name}, brain={brain.mode.label}"
            if brain.is_full_brain else
            f"requires Full Brain (currently {brain.mode.label}: {brain.reason})"
        ),
    ))
    lines.append(_capability_row(
        "Live Information", ready=brain.is_full_brain and permits("live_information") and runtime.live_info.name != "null",
        detail=f"source={runtime.live_info.name}, brain={brain.mode.label}",
    ))
    lines.append(_capability_row(
        "Research", ready=brain.is_full_brain and permits("web_search") and runtime.live_info.name != "null",
        detail=(
            f"web_search tool={'granted' if permits('web_search') else 'not granted'}, "
            f"source={runtime.live_info.name}, brain={brain.mode.label}"
        ),
    ))
    documents_name = runtime.knowledge.name
    lines.append(_capability_row(
        "Files: Text/PDF/Spreadsheet/Presentation",
        ready=brain.is_full_brain and permits("knowledge_lookup") and documents_name != "null",
        detail=(
            f"knowledge source={documents_name}"
            + (f" ({runtime.knowledge.directory})" if hasattr(runtime.knowledge, "directory") else "")
            + f", brain={brain.mode.label}"
        ),
    ))
    vision_capable = hasattr(runtime.provider, "analyze_image")
    lines.append(_capability_row(
        "Image Analysis (visual reasoning)",
        ready=vision_capable,
        detail=(
            f"provider={runtime.provider.name} supports vision"
            if vision_capable else
            f"current provider ('{runtime.provider.name}') cannot perform visual "
            "reasoning - select a vision-capable provider via 'provider llm_provider'"
        ),
    ))
    lines.append(_capability_row(
        "Document Generation", ready=brain.is_full_brain and permits("document_generation"),
        detail=f"document_generation tool={'granted' if permits('document_generation') else 'not granted'}, brain={brain.mode.label}",
    ))
    # Multimodal workspace/artifact checkpoint: a real, persistent,
    # versioned artifact - genuinely READY with no Brain at all when
    # given already-structured content (Half-Brain safe), so this row's
    # readiness reflects the tool grant alone, never brain.is_full_brain.
    lines.append(_capability_row(
        "Artifact Workspace (documents, images/diagrams, audio, video)",
        ready=permits("artifact_production") or permits("media_production"),
        detail=(
            f"artifact_production tool={'granted' if permits('artifact_production') else 'not granted'} "
            f"(documents/images), media_production tool={'granted' if permits('media_production') else 'not granted'} "
            f"(narration/images/video also become real artifacts here - see MediaProductionTool's own "
            f"artifacts= wiring), {len(runtime.artifacts.all())} artifact(s) persisted, "
            f"pdf export={runtime.document_renderer.name}, "
            f"image/diagram export={runtime.media.illustrator.name}"
        ),
    ))

    # Whole-System Reality Audit: the matrix above (Phase 3's own scope)
    # widened to the rest of the real system, per this audit's own
    # broader Section 6 - every row below is the identical "read an
    # actual runtime fact, never a class's mere existence" discipline.
    media = runtime.media
    lines.append(_capability_row(
        "AI Image Generation", ready=media.illustrator.name != "null",
        detail=f"provider={media.illustrator.name}",
    ))
    lines.append(_capability_row(
        "Narration", ready=media.narrator.name != "null",
        detail=f"provider={media.narrator.name}",
    ))
    lines.append(_capability_row(
        "Video Composition", ready=media.composer.name != "null",
        detail=f"provider={media.composer.name}",
    ))
    lines.append(_capability_row(
        "AI Video Generation", ready=media.video_generator.name != "null",
        detail=(
            f"provider={media.video_generator.name}"
            if media.video_generator.name != "null" else
            "no video-generation provider configured - see 'provider video_generation_provider'"
        ),
    ))
    lines.append(_capability_row(
        "Video Understanding (transcription)", ready=media.transcriber.name != "null",
        detail=(
            f"provider={media.transcriber.name}"
            if media.transcriber.name != "null" else
            "no transcription provider configured - clip_video can only reason over "
            "explicit timestamps or a supplied transcript; see 'provider "
            "video_understanding_provider'"
        ),
    ))
    lines.append(_capability_row(
        "Document Rendering (PDF)", ready=runtime.document_renderer.name != "null",
        detail=(
            f"engine={runtime.document_renderer.name}"
            if runtime.document_renderer.name != "null" else
            "no rendering engine configured - artifacts still persist their real "
            "structured content, with no PDF rendition; see 'provider "
            "document_rendering_provider'"
        ),
    ))
    lines.append(_capability_row(
        "Voice (speech I/O)", ready=runtime.speech.name != "none",
        detail=f"engine={runtime.speech.name}",
    ))
    lines.append(_capability_row(
        "Avatar", ready=True,
        detail=f"provider={runtime.avatar_provider.name} - manage with 'avatar <agent> <choice>'",
    ))
    lines.append(_capability_row(
        "Environment", ready=True,
        detail=f"provider={runtime.environment_provider.name} - manage with 'environment <agent> <choice>'",
    ))
    lines.append(_capability_row(
        "Connection", ready=runtime.connections.default.name != "null",
        detail=(
            f"default={runtime.connections.default.name} "
            f"({len(runtime.connections.names)} equipped: {', '.join(runtime.connections.names)}) - "
            "manage with 'connections'/'services'/'sites'"
        ),
    ))
    lines.append(_capability_row(
        "Communication (messaging/calling, any recipient)",
        ready=runtime.communication.name != "null",
        detail=(
            f"provider={runtime.communication.name} - manage with 'provider communication_provider'"
            if runtime.communication.name != "null" else
            "no communication provider configured - see 'provider communication_provider'"
        ),
    ))
    lines.append(_capability_row(
        "Memory", ready=True,
        detail=f"backend={type(manager.memory).__name__} - genuinely persistent unless 'ephemeral' is selected",
    ))

    lines.append("")
    lines.append("Use 'provider' to see and manage the providers backing these capabilities.")
    return "\n".join(lines)


def _require_category(registry, category: str):
    adapter = registry.adapter_for(category)
    if adapter is None:
        known = ", ".join(registry.categories())
        return None, f"no such category '{category}'. Known categories: {known}"
    return adapter, None


def _require_backend(adapter, category: str, backend: str):
    """A backend key must actually belong to this category's own,
    honest catalog (``adapter.available()``) - the identical "no
    fabricated access" discipline every other real capability in this
    project already holds itself to, applied to which vendor an account
    is allowed to claim it authorises.
    """
    known = {descriptor.key for descriptor in adapter.available()}
    if backend not in known:
        listing = ", ".join(sorted(known)) or "(none registered)"
        return f"no such backend '{backend}' in category '{category}'. Known backends: {listing}"
    return None


# ---------------------------------------------------------------------------
# Control Center repair item #1 (2026-09-07): capability enable/disable.
#
# Reuses GovernedConfig verbatim - the identical real, persisted,
# non-secret key/value store CapabilityConfigHandler/ToolConfigHandler
# already write through for exactly this kind of "make a hardcoded
# behaviour governable" gap, applied here to a capability's own
# reachability rather than one of its tunable values. Deliberately NOT
# routed through the Evolution proposal/ledger machinery
# (ecosystem_evolution.propose/run): that machinery exists for
# *versioned* component changes with a rollback story; enable/disable is
# a direct, immediate, idempotent boolean toggle - the same shape
# `provider <category> select` already is; see run_provider_command's
# own docstring for that precedent.
#
# Enforcement happens once, at composition time - see
# _filtered_manager_capabilities() and its one real call site in
# build() below - never inside a capability's own code. This matches
# governed_config.py's own explicit rule: "nothing here is read by a
# capability directly - only ... the specific build() call sites this
# checkpoint threads it through". A capability disabled here is not
# merely hidden from a menu: select_capability() never sees it in the
# registry it is given, so it can never be claimed, never be scored,
# and never run, for the life of the process that was built with it
# filtered out. Taking effect requires 'reload' or a restart - the
# identical, already-established rule every other persisted
# provider/connection change in this file already lives under.
def _capability_enabled_key(name: str) -> str:
    return f"capability.enabled.{name}"


# Structural fallbacks select_capability() reaches as literal, imported
# objects - never as a registry lookup - when nothing else claims or
# scores a request (see select_capability's own "best is None" branch).
# Filtering the registry dict cannot stop either of them from still
# being reachable that way, so disabling one would be a lie about what
# the system actually does - refused below rather than silently
# accepted and silently ineffective.
_UNDISABLEABLE_CAPABILITIES = frozenset({ROSTER_REPORT.name, OPEN_CONVERSATION.name})


def disabled_capability_names(config: "GovernedConfig") -> frozenset[str]:
    """Every capability name this installation has genuinely persisted
    as disabled. Absent means enabled - the same "unconfigured means
    the pre-existing default behaviour" rule every other GovernedConfig
    key in this project already follows."""
    disabled = set()
    for name in available_capabilities():
        if config.get(_capability_enabled_key(name)) == "false":
            disabled.add(name)
    return frozenset(disabled)


def filtered_manager_capabilities(
    config: "GovernedConfig",
) -> "dict[str, object]":
    """MANAGER_CAPABILITIES with every genuinely-disabled entry removed -
    the one real enforcement point. Returns the identical registry
    object's entries, never a re-derived or re-invented one, so a
    capability's own behaviour when it does run is completely
    unchanged - only whether it is ever reached is."""
    disabled = disabled_capability_names(config)
    return {name: cap for name, cap in MANAGER_CAPABILITIES.items() if name not in disabled}


def run_capability_registry_command(centre: "ControlCenter", rest: str) -> str:
    """'capabilities list' / 'capabilities enable <name>' / 'capabilities
    disable <name>' - the registry-management counterpart to the
    existing, unchanged 'capabilities' (bare) feature-matrix view above.
    A distinct concern: render_capabilities() reports real runtime
    *readiness* of a handful of user-facing features; this reports and
    governs which of the Manager's ~24 registered *handlers*
    (current_datetime, connected_action, ...) may ever be selected.
    """
    args = rest.split()
    config = load_governed_config(DEFAULT_CONFIG_PATH)
    disabled = disabled_capability_names(config)

    if args[0] == "list":
        lines = ["CAPABILITY REGISTRY (governs what select_capability() may ever reach)", ""]
        for name in available_capabilities():
            state = "disabled" if name in disabled else "enabled"
            locked = " (cannot be disabled - a structural fallback)" if name in _UNDISABLEABLE_CAPABILITIES else ""
            lines.append(f"  [{state:^8}] {name}{locked}")
        lines.append("")
        lines.append("enable/disable with: capabilities enable|disable <name>")
        return "\n".join(lines)

    if args[0] in ("enable", "disable"):
        if len(args) < 2:
            return f"usage: capabilities {args[0]} <name>"
        name = args[1]
        if name not in MANAGER_CAPABILITIES:
            known = ", ".join(available_capabilities())
            return f"no such capability '{name}'. Known: {known}"

        if args[0] == "disable":
            if name in _UNDISABLEABLE_CAPABILITIES:
                return (
                    f"'{name}' cannot be disabled - it is a structural fallback "
                    "select_capability() reaches directly, not through the registry, "
                    "so disabling it here could never actually take effect."
                )
            updated = config.with_value(_capability_enabled_key(name), "false")
        else:
            updated = config.without_value(_capability_enabled_key(name))

        save_governed_config(updated, DEFAULT_CONFIG_PATH)
        reloaded = load_governed_config(DEFAULT_CONFIG_PATH)
        now_disabled = name in disabled_capability_names(reloaded)
        if args[0] == "disable" and not now_disabled:
            return f"FAILED: wrote the disabled state for '{name}', but the independent re-read did not confirm it"
        if args[0] == "enable" and now_disabled:
            return f"FAILED: wrote the enabled state for '{name}', but the independent re-read still reports it disabled"

        verb_past = "disabled" if args[0] == "disable" else "enabled"
        # Post-Pre-Stage-9: live at once in this running session - 'reload'
        # exists only in the terminal loop, so a choice made from the served
        # Control Center otherwise waited for a restart.
        own_skills = getattr(centre.manager, "own_skills", None)
        if own_skills is not None and hasattr(own_skills, "replace_capabilities"):
            own_skills.replace_capabilities(filtered_manager_capabilities(reloaded))
            return (
                f"'{name}' is now {verb_past} (independently re-read from disk) and live in this session now - "
                "every start uses the same choice."
            )
        return (
            f"'{name}' is now {verb_past} (independently re-read from disk). "
            "Persisted only - not yet active on this running process. "
            "Type 'reload' now to make it live in this session, or restart."
        )

    return f"unknown capabilities action '{args[0]}'. Try: list, enable, disable"


def run_provider_command(centre: "ControlCenter", rest: str) -> str:
    """The Control Center's own provider-management surface - the exact
    mechanism ``scripts/ecosystem_cli.py`` already established, reached
    from the same terminal a person already has open rather than a
    second tool they would have to know exists. Every operation below
    goes through the real ``EcosystemRegistry``/``CredentialStore`` this
    project already built; nothing here is a second implementation of
    either.

    **Universal provider/connection management repair.** Accounts are
    now persisted keyed by *backend* (``account.provider`` = "anthropic",
    "runway", "elevenlabs", "null", ...), never by *category*
    ("llm_provider", "video_generation_provider", ...). This is not a
    new convention - it is the one ``build_provider``/``build_media``/
    ``build_speech``/``build_communication_provider`` already require
    (each reads ``account.provider`` to decide which concrete builder to
    call; see their own docstrings) and the one Phase 1.5's own tests
    already construct accounts under
    (``ProviderAccount(provider="anthropic", ...)``,
    ``provider="runway"``, never a category string). Before this fix,
    ``add``/``select``/``revoke``/``accounts`` persisted and looked
    accounts up by *category* - which meant a persisted account could
    never actually reach a real provider construction (feeding it into
    ``build_runtime`` would have raised "Unknown provider
    'llm_provider'"), the exact disconnect CHECK's audit found between
    this command surface and the live system. ``category`` still
    selects which registered ecosystem category's own backend catalog
    an account must belong to (``_require_backend``, an honest refusal
    for a name that category does not recognise) - it is no longer
    conflated with the credential's own storage key.
    """
    registry = build_ecosystem_registry(centre.runtime.settings)
    args = rest.split()

    if not args:
        return registry.describe_all()

    category, *rest_args = args
    adapter, error = _require_category(registry, category)
    if error:
        return error

    if not rest_args:
        lines = [adapter.describe()]
        active = adapter.active()
        lines.append(f"active: {active.key if active is not None else '(none)'}")
        dependents = adapter.dependents()
        if dependents:
            lines.append("depends on this category being configured:")
            lines.extend(f"  - {line}" for line in dependents)
        return "\n".join(lines)

    action, *action_args = rest_args
    store = load_credential_store(DEFAULT_ACCOUNTS_PATH)

    if action == "accounts":
        backends = {descriptor.key for descriptor in adapter.available()}
        accounts = [account for backend in backends for account in store.accounts_for(backend)]
        if not accounts:
            return f"no accounts registered for '{category}'"
        lines = []
        for account in accounts:
            active = store.active(account.provider)
            marker = "*" if active is not None and active.account_id == account.account_id else " "
            model = f" model={account.model}" if account.model else ""
            lines.append(f"[{marker}] {account.describe()}{model}")
        return "\n".join(lines)

    if action == "add":
        if len(action_args) < 3:
            return "usage: provider <category> add <backend> <account-id> <credential> [model]"
        backend, account_id, credential, *model_args = action_args
        backend_error = _require_backend(adapter, category, backend)
        if backend_error:
            return backend_error
        account = ProviderAccount(
            provider=backend, account_id=account_id, credential=credential,
            model=model_args[0] if model_args else None,
        )
        store.add(account)
        save_credential_store(store, DEFAULT_ACCOUNTS_PATH)
        if category == LLM_CATEGORY and backend == centre.runtime.settings.provider:
            return f"added {account.describe()} (active) - no secret is ever echoed back." + _apply_reasoning_live(centre, store)
        return f"added {account.describe()} (active) - no secret is ever echoed back." + (
            _apply_backends_live(centre) if category != LLM_CATEGORY else
            f" Persisted - it authorises '{backend}' when that backend is chosen with "
            f"'provider {LLM_CATEGORY} use {backend}'."
        )

    if action == "select":
        if len(action_args) < 2:
            return "usage: provider <category> select <backend> <account-id>"
        backend, account_id = action_args[0], action_args[1]
        backend_error = _require_backend(adapter, category, backend)
        if backend_error:
            return backend_error
        try:
            store.select(backend, account_id)
        except KeyError as exc:
            return f"FAILED: {exc}"
        save_credential_store(store, DEFAULT_ACCOUNTS_PATH)
        if category == LLM_CATEGORY and backend == centre.runtime.settings.provider:
            return f"{backend}/{account_id} is now active." + _apply_reasoning_live(centre, store)
        return f"{backend}/{account_id} is now active." + (
            _apply_backends_live(centre) if category != LLM_CATEGORY else
            f" Persisted - it authorises '{backend}' when that backend is chosen with "
            f"'provider {LLM_CATEGORY} use {backend}'."
        )

    if action == "revoke":
        if len(action_args) < 2:
            return "usage: provider <category> revoke <backend> <account-id>"
        backend, account_id = action_args[0], action_args[1]
        backend_error = _require_backend(adapter, category, backend)
        if backend_error:
            return backend_error
        store.revoke(backend, account_id)
        save_credential_store(store, DEFAULT_ACCOUNTS_PATH)
        if category == LLM_CATEGORY and backend == centre.runtime.settings.provider:
            return f"revoked {backend}/{account_id}." + _apply_reasoning_live(centre, store)
        return f"revoked {backend}/{account_id}." + (_apply_backends_live(centre) if category != LLM_CATEGORY else "")

    if action == "use":
        # Pre-Stage-9 Campaign 4: switch which backend the running system
        # reasons through, live, without recreating Alfred - and persist the
        # choice only once the switch has genuinely succeeded.
        if not action_args:
            return f"usage: provider {category} use <backend>"
        backend = action_args[0]
        backend_error = _require_backend(adapter, category, backend)
        if backend_error:
            return backend_error
        if category != LLM_CATEGORY:
            # Pre-Stage-9 Campaign 8: every backend category is chosen the same
            # way. These are bound into tools at startup, so the validated
            # choice is saved for every start rather than swapped mid-session.
            field_name = GOVERNED_BACKEND_FIELDS.get(category)
            if field_name is None:
                return f"'use' is not available for '{category}' - it has no single backend choice to save."
            outcome = adapter.validate(backend, credential=None)
            if not outcome.ok:
                return (
                    f"FAILED: '{backend}' cannot be used for '{category}': {outcome.detail}. "
                    "Nothing was saved."
                )
            config = load_governed_config(DEFAULT_CONFIG_PATH)
            save_governed_config(config.with_value(f"provider.{category}", backend), DEFAULT_CONFIG_PATH)
            return f"Saved: '{category}' now uses '{backend}' - the choice every start uses." + _apply_backends_live(centre)
        previous = centre.runtime.settings.provider
        governed = governed_reasoning_settings(centre.runtime.settings, load_governed_config(DEFAULT_CONFIG_PATH))
        candidate = replace(governed, provider=backend)
        try:
            runtime = rebind_reasoning(centre.runtime, centre.manager, candidate, accounts=store)
        except (ConfigurationError, ProviderConfigurationError, ValueError) as exc:
            return (
                f"FAILED: could not switch to '{backend}': {exc}. Nothing changed - this session "
                f"still reasons through '{previous}', and no choice was saved."
            )
        centre.rebind_runtime(runtime)
        config = load_governed_config(DEFAULT_CONFIG_PATH)
        save_governed_config(config.with_value(CHOSEN_LLM_BACKEND, backend), DEFAULT_CONFIG_PATH)
        return (
            f"Switched the reasoning provider from '{previous}' to '{backend}' live - the same "
            "conversation, memory, identity and specialists continue. Saved as the choice every "
            "future start uses." + _reasoning_mode_note(governed, backend)
        )

    if action == "validate":
        if not action_args:
            return "usage: provider <category> validate <key> [credential]"
        key = action_args[0]
        credential = action_args[1] if len(action_args) > 1 else None
        outcome = adapter.validate(key, credential=credential)
        detail = (
            f"{'OK' if outcome.ok else 'FAILED'}: {outcome.detail}\n"
            "(Real construction only - no live external call was made. "
            "This is IMPLEMENTED/TEST-VERIFIED evidence, never LIVE EXTERNAL VERIFIED.)"
        )
        return detail

    if action == "check":
        # The question 'validate' above deliberately cannot answer: would the
        # vendor actually accept this credential? Only the real request proves
        # that, so this makes exactly one - which is why it is a confirmed,
        # audited administrative write and never happens on its own.
        #
        # Closure campaign (2026-09-16): this used to refuse every category
        # except 'llm_provider' outright ("no completion call to prove one
        # with"), which was true for most categories but not 'speech_provider' -
        # ElevenLabsSpeechAdapter already had a real, live, vendor-backed call
        # (probe_authorization) sitting unused for exactly this purpose.
        #
        # Closure campaign 3 (2026-09-17): the tuple that replaced it was the
        # same mistake one size smaller - a hardcoded list of checkable
        # categories, which would have gone on refusing a video or image
        # backend the day it grew a real probe until someone remembered to edit
        # this line. The category no longer decides. The adapter is asked to
        # construct its backend (the same construction 'validate' already
        # performs, handed back instead of discarded) and the built backend is
        # asked whether it can prove its own credential. A category with no
        # construction path, or a backend with no probe, is refused honestly
        # by the mechanism itself rather than by a list kept here.
        if not action_args:
            return f"usage: provider {category} check <backend> [account-id]"
        backend = action_args[0]
        backend_error = _require_backend(adapter, category, backend)
        if backend_error:
            return backend_error
        account_id = action_args[1] if len(action_args) > 1 else None
        if account_id is not None:
            account = next((a for a in store.accounts_for(backend) if a.account_id == account_id), None)
        else:
            account = store.active(backend)
        if account is None:
            known = ", ".join(a.account_id for a in store.accounts_for(backend)) or "none"
            return (
                f"no {'account ' + account_id if account_id else 'active account'} for '{backend}' "
                f"to check (registered: {known}). Add one with "
                f"'provider {category} add {backend} <account-id> <credential>'."
            )
        try:
            # The stored account itself, not just its secret: an account may
            # pin its own model, and the check must exercise what the account
            # actually describes.
            built = adapter.construct(backend, credential=account)
        except NotImplementedError:
            return (
                f"'check' asks a vendor whether a credential works, which needs a real "
                f"backend to ask - '{category}' has no construction path to build one from. "
                f"Use 'provider {category} validate <backend>' instead."
            )
        except Exception as exc:  # noqa: BLE001 - boundary catch, mirrors CategoryAdapter.validate
            return (
                f"FAILED: could not build '{backend}' from that account: "
                f"{type(exc).__name__}: {exc}. Nothing was sent."
            )
        # An LLM proves its credential by completing; anything else by its own
        # probe. Both are the backend's real vendor call, never a hand-rolled
        # HTTP request invented here.
        if isinstance(built, LLMProvider):
            outcome = check_authorization(built)
        elif category == "speech_provider":
            outcome = check_speech_authorization(built)
        else:
            outcome = check_vendor_authorization(built)
        where = "the vendor answered" if outcome.reached_vendor else "the vendor was never reached"
        return (
            f"{'AUTHORIZED' if outcome.authorized else 'NOT AUTHORIZED'}: "
            f"{backend}/{account.account_id} - {where}. {outcome.reason}\n"
            "(A real request was made to the vendor. This is LIVE EXTERNAL VERIFIED evidence "
            "when it reached them, and an honest 'never reached' when it did not.)"
        )

    return f"unknown provider action '{action}'. Try: accounts, add, select, revoke, use, validate, check"


# ---------------------------------------------------------------------------
# Independent-manageability repair: PROVIDERS (above - one active backend
# per category, e.g. LLM/voice/video) are architecturally distinct from
# CONNECTIONS (below) - external applications/services (Gmail, and, in
# future, WhatsApp/Telegram/X/Spotify/...) that are ADDITIVE: many can be
# connected at once, connecting one never disconnects another, and there is
# no single "active connection" across unrelated services. This is not a
# second credential system - it reuses the identical CredentialStore/
# ProviderAccount every provider account already lives in, addressed
# directly by the service's own name instead of through an
# EcosystemRegistry category (a category implies "pick one active
# backend", which is exactly the shape connections must not have).
# ---------------------------------------------------------------------------


def _provider_axis_backend_names() -> set[str]:
    """Backend keys belonging to the *provider* axis (LLM/voice/video) -
    single-active-per-category, never an external app connection. Excluded
    from the additive connections listing so 'anthropic'/'runway'/
    'elevenlabs' are never mistaken for a connected service."""
    return (
        set(available_providers())
        | set(available_speech_providers())
        | set(available_video_generation_backends())
        | set(available_video_understanding_backends())
    )


def _gmail_connection_status(settings: Settings) -> list[str]:
    """Gmail's real status, independent of whichever backend the
    single-active 'connection' ecosystem category currently has selected -
    ``replace(..., connection_provider="gmail")`` builds Gmail specifically,
    the identical seam that category's own ``validate("gmail")`` already
    uses internally, so this reports the truth about Gmail even when
    ``settings.connection_provider`` is presently "null".

    Gmail authenticates via a real, already-working OAuth flow
    (``scripts/authorize_gmail.py``, a token file, never a typed secret) -
    never routed through CredentialStore, and never faked here as though
    it were one.
    """
    connection = build_connection(replace(settings, connection_provider="gmail"))
    lines = [
        "gmail (OAuth - connected via a token file, not a typed credential):",
        f"  availability : {connection.availability.state.value}",
        f"  authorization: {connection.authorization.value}",
        f"  operations   : {', '.join(connection.supported_operations) or '(none)'}",
    ]
    if connection.authorization != AuthorizationState.AUTHORIZED:
        lines.append("  connect with : python scripts/authorize_gmail.py")
    else:
        # "Authorized" only means a token file exists. What the file itself
        # says about its access token is read here, locally - whether Google
        # still accepts its refresh can only be learned by using it, and that
        # is said plainly rather than implied.
        expiry = _gmail_token_expiry(settings.gmail_token_path)
        if expiry is not None:
            from datetime import datetime, timezone

            state = "expired" if expiry < datetime.now(timezone.utc) else "valid until"
            lines.append(
                f"  access token : {state} {expiry:%Y-%m-%d %H:%M} UTC - refreshed on first use; if Google refuses "
                "the refresh, re-authorize with: python scripts/authorize_gmail.py"
            )
    lines.append(f"  disconnect by: deleting '{settings.gmail_token_path}'")
    return lines


def _gmail_token_expiry(token_path) -> "object | None":
    """The access-token expiry recorded in the token file, or None - a local
    read only, never a network call and never a credential shown."""
    import json as _json
    from datetime import datetime, timezone

    try:
        raw = _json.loads(Path(token_path).read_text(encoding="utf-8")).get("expiry")
        if not raw:
            return None
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, AttributeError):
        return None


def run_connections_command(centre: "ControlCenter", rest: str) -> str:
    """The additive connection-management surface: connect/disconnect/list/
    status/validate for external applications and services, none of which
    ever displaces another. Reuses the same persisted ``CredentialStore``
    every provider account already lives in - a connection's credential is
    a ``ProviderAccount`` like any other, keyed by the service's own name
    ("whatsapp", "telegram", ...) rather than by an ecosystem category,
    since there is no "pick one active service" concept to enforce here.

    Any service name is accepted - open-ended on purpose (Phase 4's own
    "examples are not boundaries" principle): a future service needs no
    change here to become connectable, only a name.

    Gmail is the one connection with a genuinely different authentication
    shape today (OAuth via a token file, not a single secret string) and is
    reported through its own real mechanism (see ``_gmail_connection_status``)
    rather than forced into the string-credential shape that fits
    WhatsApp/Telegram-style access tokens correctly but would misrepresent
    Gmail's own.
    """
    settings = centre.runtime.settings
    args = rest.split()
    store = load_credential_store(DEFAULT_ACCOUNTS_PATH)
    excluded = _provider_axis_backend_names() | {"null"}
    connected_services = sorted(p for p in store.providers() if p not in excluded and p != "gmail")

    if not args:
        lines = ["CONNECTIONS (persistent, additive - connecting one never disconnects another)", ""]
        lines.extend(_gmail_connection_status(settings))
        lines.append("")
        if connected_services:
            for service in connected_services:
                lines.append(f"{service}: connected ({len(store.accounts_for(service))} account(s))")
        else:
            lines.append("no other services connected yet")
        lines.append("")
        lines.append("connect a new one with: connections <service> connect <account-id> <credential> [model]")
        return "\n".join(lines)

    service, *rest_args = args

    if service == "gmail":
        if not rest_args or rest_args[0] == "status":
            return "\n".join(_gmail_connection_status(settings))
        action = rest_args[0]
        if action in ("connect", "disconnect"):
            return (
                "gmail authenticates via OAuth, not a typed credential - "
                "connect with 'python scripts/authorize_gmail.py'; "
                f"disconnect by deleting '{settings.gmail_token_path}'."
            )
        if action == "validate":
            registry = build_ecosystem_registry(settings)
            outcome = registry.adapter_for("connection").validate("gmail")
            return (
                f"{'OK' if outcome.ok else 'FAILED'}: {outcome.detail}\n"
                "(Real construction only - no live external call was made.)"
            )
        return f"unknown connections action '{action}' for gmail. Try: status, validate"

    if service in _provider_axis_backend_names():
        return (
            f"'{service}' is a provider backend (LLM/voice/video), not an external app "
            f"connection - manage it with 'provider <category> ...' instead."
        )

    if not rest_args:
        accounts = store.accounts_for(service)
        if not accounts:
            return (
                f"'{service}' is not connected. "
                f"Connect with: connections {service} connect <account-id> <credential> [model]"
            )
        active = store.active(service)
        lines = [f"{service}:"]
        for account in accounts:
            marker = "*" if active is not None and active.account_id == account.account_id else " "
            model = f" model={account.model}" if account.model else ""
            lines.append(f"  [{marker}] {account.describe()}{model}")
        return "\n".join(lines)

    action, *action_args = rest_args

    if action == "connect":
        if len(action_args) < 2:
            return "usage: connections <service> connect <account-id> <credential> [model]"
        account_id, credential, *model_args = action_args
        account = ProviderAccount(
            provider=service, account_id=account_id, credential=credential,
            model=model_args[0] if model_args else None,
        )
        store.add(account)
        save_credential_store(store, DEFAULT_ACCOUNTS_PATH)
        return (
            f"connected {account.describe()} - no secret is ever echoed back. "
            f"'{service}' stays connected independently of every other service. "
            "Persisted only - not yet active on this running process. "
            "Type 'reload' now to make it live in this session, or restart."
        )

    if action == "disconnect":
        if not action_args:
            return "usage: connections <service> disconnect <account-id>"
        store.revoke(service, action_args[0])
        save_credential_store(store, DEFAULT_ACCOUNTS_PATH)
        return f"disconnected {service}/{action_args[0]} - every other connection is unaffected."

    if action == "status":
        accounts = store.accounts_for(service)
        if not accounts:
            return f"'{service}' is not connected."
        active = store.active(service)
        lines = [f"{service}:"]
        for account in accounts:
            marker = "*" if active is not None and active.account_id == account.account_id else " "
            model = f" model={account.model}" if account.model else ""
            lines.append(f"  [{marker}] {account.describe()}{model}")
        return "\n".join(lines)

    if action == "validate":
        registry = build_ecosystem_registry(settings)
        credential = action_args[0] if action_args else None
        for category in ("communication_provider", "connection"):
            adapter = registry.adapter_for(category)
            if service in {descriptor.key for descriptor in adapter.available()}:
                outcome = adapter.validate(service, credential=credential)
                return (
                    f"{'OK' if outcome.ok else 'FAILED'}: {outcome.detail}\n"
                    "(Real construction only - no live external call was made.)"
                )
        return (
            f"no automated validation exists yet for '{service}' - the credential is "
            "stored, but its correctness cannot be mechanically checked without a real "
            "integration for this service."
        )

    return f"unknown connections action '{action}'. Try: connect, disconnect, status, validate"


# ---------------------------------------------------------------------------
# Open-ended service discovery (Phase 6 foundation, 2026-09-09): the
# smallest missing generalized mechanism for a legitimate service Alfred
# was never built knowing about to become a real, governed connection -
# never a hard-coded catalogue, never a new capability per company.
#
#     services                          -> every declared service
#     services <name>                   -> one service's own detail
#     services <name> discover <url>    -> read what the service itself
#                                          already publishes and propose
#                                          a declare command - writes
#                                          nothing, requires no
#                                          confirmation
#     services <name> declare <url> <op>:<METHOD>:<path>:<auth|open>[,...]
#     services <name> execute <op> [key=value ...]
#                                        -> operation-level authorization
#                                          (DeclaredConnection.authorization_for),
#                                          a real network call
#                                          (DeclaredConnection.execute),
#                                          and an honest result - GET
#                                          needs no confirmation; every
#                                          other method does, the exact
#                                          same confirm_token/audit path
#                                          declare/forget already use
#     services <name> forget
#
# ``discover`` (src/providers/service_discovery.py) tries two real,
# pre-existing conventions in order - a service's own
# .well-known/alfred-service.json if it specifically knows about
# Alfred, falling back to a bounded subset of OpenAPI/Swagger at the
# conventional locations real, already-existing services already
# publish it (/openapi.json, /.well-known/openapi.json, /swagger.json)
# - the genuinely open-ended source, since it needs no accommodation
# from the service at all. This is the DISCOVER/INSPECT/VALIDATE half
# of a lifecycle ahead of the REPRESENT/authorize/CONNECT half below,
# which is unchanged - a discovered proposal still has to be reviewed
# and separately declared
# by a person; discovery alone is never authorization to act.
#
# ``declare``/``forget`` are administrative writes - confirmed and
# audited exactly like ``connections connect``/``provider add`` above,
# through the identical classify_administrative_write/
# _record_if_administrative_write pair. A credential for a declared
# service is still added the ordinary way, through the already-generic
# ``connections <name> connect ...`` above - this verb only ever
# declares what the service *is* (its base URL and operations), never a
# second, parallel credential mechanism. Once both exist, selecting
# ``MANAGER_CONNECTION_PROVIDER=<name>`` at the next process start
# builds a real ``DeclaredConnection`` (connection_factory.py's own
# fallback) - reachable through the unmodified ``connected_action``
# capability, the identical governed path Gmail already uses.
# ---------------------------------------------------------------------------


def _format_parameters(op: "ServiceOperation") -> str:
    """One operation's own declared inputs, rendered for a person to
    read - the identical real metadata ``connected_action``
    (``src/manager/capabilities.py``) and ``DeclaredConnection.execute``
    actually resolve values against, never a separate description of
    it. Empty for an operation that declares none."""
    if not op.parameters:
        return ""
    parts = [
        f"{p.name} ({p.location}{'' if p.required else ', optional'})" for p in op.parameters
    ]
    return " [" + ", ".join(parts) + "]"


def _op_spec(op: "ServiceOperation") -> str:
    """One operation, round-tripped through the identical compact text
    shape ``run_services_command``'s own ``declare`` action parses back
    - the real, machine-usable form ``_format_parameters`` above is the
    human-readable one for.

    **The gap this closes.** ``discover``'s own suggested follow-up
    command used to build this from only ``name:METHOD:path:auth|open``
    - the identical four fields ``declare`` has only ever accepted -
    which meant a real, discovered parameter (a booking's date, an
    order's item, anything the Data-flow-through-execution checkpoint
    exists for) was silently dropped the moment a person actually ran
    the command discovery itself suggested. A service with no
    consequential operation that needs a real value never noticed; any
    real-world transaction (a reservation, a purchase, an appointment)
    always would have.

    **Encoding, never ambiguous with what already exists.** A fifth,
    optional field, appended after the existing four with one more
    ``:`` - so an op-spec with no parameters is byte-for-byte what
    ``declare`` already accepted, and every existing caller/test is
    unaffected. Multiple parameters on one operation join with ``|``
    (never ``,``, which already separates *operations* in the outer
    list - see ``declare``'s own parsing) - each one itself
    ``name:location:required|optional``, mirroring the outer shape at a
    smaller scale rather than inventing a different one.
    """
    base = f"{op.name}:{op.method}:{op.path}:{'auth' if op.requires_auth else 'open'}"
    if not op.parameters:
        return base
    params = "|".join(
        f"{p.name}:{p.location}:{'required' if p.required else 'optional'}" for p in op.parameters
    )
    return f"{base}:{params}"


def run_services_command(centre: "ControlCenter", rest: str) -> str:
    """Decide what to answer for the ``services`` verb - declare,
    forget, or list what open-ended service discovery has registered so
    far. Real, persisted state throughout
    (``src/services/connections/service_registry.py``); never a mock
    catalogue and never in-process-only.

    Reads/writes ``DEFAULT_SERVICES_PATH`` by the identical explicit-
    argument shape ``run_connections_command`` already threads
    ``DEFAULT_ACCOUNTS_PATH`` through - a module-level name resolved
    fresh on every call, not a function default bound once at import
    time, so a test can monkeypatch this module's own
    ``DEFAULT_SERVICES_PATH`` and have every call below honour it.
    """
    args = rest.split()
    if not args:
        registry = load_service_registry(DEFAULT_SERVICES_PATH)
        if not registry:
            return (
                "no services declared yet.\n"
                "declare one with: services <name> declare <base-url> "
                "<op-name>:<METHOD>:<path>:<auth|open>[:<param>:<location>:<required|optional>"
                "[|<param2>...]][,<op2>...]"
            )
        lines = ["DECLARED SERVICES (open-ended discovery)", ""]
        for name, descriptor in sorted(registry.items()):
            lines.append(f"{name}: {descriptor.base_url} ({len(descriptor.operations)} operation(s))")
        return "\n".join(lines)

    name, *rest_args = args
    normalised = name.strip().lower()

    if not rest_args:
        registry = load_service_registry(DEFAULT_SERVICES_PATH)
        descriptor = registry.get(normalised)
        if descriptor is None:
            return f"'{name}' is not a declared service. Try: services (to list all)"
        lines = [f"{descriptor.name}: {descriptor.base_url}"]
        if descriptor.description:
            lines.append(descriptor.description)
        for op in descriptor.operations:
            auth = "auth required" if op.requires_auth else "open"
            lines.append(
                f"  {op.name} ({op.method} {op.path}, {auth}){_format_parameters(op)}"
                + (f" - {op.description}" if op.description else "")
            )
        return "\n".join(lines)

    action = rest_args[0].lower()

    if action == "discover":
        # Open-ended service DISCOVERY (2026-09-09): read-only, on
        # purpose - fetches the service's own published metadata and
        # turns it into a *proposed* ServiceDescriptor, never a
        # persisted one. Not classify_administrative_write-gated: unlike
        # declare/forget, this writes nothing and changes no governed
        # state - the same reasoning 'provider <category> validate'
        # already applies to a real-but-inert construction attempt.
        # Discovery alone is never authorization to act; a person still
        # runs the real, confirmed 'declare' below to make anything of
        # what this finds.
        if len(rest_args) < 2:
            return "usage: services <name> discover <base-url>"
        base_url = rest_args[1]
        try:
            found = discover_service(name, base_url)
        except ServiceDiscoveryError as exc:
            return f"could not discover '{name}' at {base_url}: {exc}"
        d = found.descriptor
        dropped = found.declared_operation_count - len(d.operations)
        op_spec = ",".join(_op_spec(op) for op in d.operations)
        source_label = {
            "alfred-service": "a real .well-known/alfred-service.json this service published for Alfred",
            "openapi": "a real OpenAPI/Swagger document this service already publishes for anyone",
        }.get(found.source_kind, found.source_kind)
        lines = [
            f"DISCOVERED '{d.name}' from {found.source_url}",
            f"  source: {source_label}",
            f"  base URL: {d.base_url}",
        ]
        if d.description:
            lines.append(f"  description: {d.description}")
        lines.append(
            f"  {len(d.operations)} of {found.declared_operation_count} declared operation(s) "
            f"were usable" + (f" ({dropped} malformed, dropped)" if dropped else "") + ":"
        )
        for op in d.operations:
            auth = "auth required" if op.requires_auth else "open"
            lines.append(
                f"    {op.name} ({op.method} {op.path}, {auth}){_format_parameters(op)}"
                + (f" - {op.description}" if op.description else "")
            )
        lines.append("")
        lines.append("Nothing has been declared or connected yet - discovery only reads and validates.")
        lines.append("Review the above, then declare it yourself (still requires confirmation) with:")
        lines.append(f"  services {d.name} declare {d.base_url} {op_spec}")
        return "\n".join(lines)

    if action == "declare":
        if len(rest_args) < 3:
            return (
                "usage: services <name> declare <base-url> "
                "<op-name>:<METHOD>:<path>:<auth|open>[:<param>:<location>:<required|optional>"
                "[|<param2>:<location>:<required|optional>...]][,<op2>...]"
            )
        base_url = rest_args[1]
        op_specs = rest_args[2].split(",")
        operations: "list[ServiceOperation]" = []
        for spec in op_specs:
            # maxsplit=4: a fifth field (real, declared parameters - see
            # _op_spec's own docstring for exactly what gap this closes)
            # is optional and, when present, carries its own "|"/":"
            # structure intact rather than being re-split by this outer
            # ":" - an op-spec with no fifth field is byte-for-byte the
            # original four-field shape, so every existing caller is
            # unaffected.
            fields = spec.split(":", 4)
            if len(fields) < 4:
                return (
                    f"could not declare '{name}': operation '{spec}' must have the "
                    "shape name:METHOD:path:auth|open"
                )
            op_name, method, path, auth_word = fields[:4]
            parameters: "list[ServiceParameter]" = []
            if len(fields) == 5 and fields[4]:
                for param_spec in fields[4].split("|"):
                    param_fields = param_spec.split(":")
                    if len(param_fields) != 3:
                        return (
                            f"could not declare '{name}': parameter '{param_spec}' on "
                            f"'{op_name}' must have the shape name:location:required|optional"
                        )
                    param_name, param_location, param_required_word = param_fields
                    try:
                        parameters.append(
                            ServiceParameter(
                                name=param_name, location=param_location,
                                required=(param_required_word.strip().lower() == "required"),
                            )
                        )
                    except ValueError as exc:
                        return f"could not declare '{name}': {exc}"
            try:
                operations.append(
                    ServiceOperation(
                        name=op_name, method=method, path=path,
                        requires_auth=(auth_word.strip().lower() == "auth"),
                        parameters=tuple(parameters),
                    )
                )
            except ValueError as exc:
                return f"could not declare '{name}': {exc}"
        try:
            descriptor = ServiceDescriptor(name=name, base_url=base_url, operations=tuple(operations))
        except ValueError as exc:
            return f"could not declare '{name}': {exc}"
        declare_service(descriptor, DEFAULT_SERVICES_PATH)
        return (
            f"declared '{descriptor.name}' with {len(descriptor.operations)} operation(s) "
            f"at {descriptor.base_url}. Add a credential with 'connections {descriptor.name} "
            f"connect <account-id> <credential>' if any operation needs one, then select it "
            f"with MANAGER_CONNECTION_PROVIDER={descriptor.name} on the next start."
        )

    if action == "execute":
        # Operation-level, generalized real-world action execution
        # (Phase 6, 2026-09-09) - the actual DISCOVER -> ... -> EXECUTE ->
        # VERIFY -> RECORD lifecycle's own final steps, reusing every
        # governance mechanism already established rather than inventing
        # a second one: the identical confirm_token dance
        # classify_administrative_write/_record_if_administrative_write
        # already give declare/forget above, now also covering a
        # consequential (non-GET) operation - never a GET, which needs
        # no more confirmation than 'connections <name> status' already
        # does. The credential, if any, is still whatever
        # 'connections <name> connect ...' already put on file - never a
        # second credential path.
        if len(rest_args) < 2:
            return "usage: services <name> execute <operation> [key=value ...]"
        operation = rest_args[1]
        params: "dict[str, str]" = {}
        for raw_param in rest_args[2:]:
            if "=" not in raw_param:
                return f"could not execute '{operation}' on '{name}': '{raw_param}' must be key=value"
            key, _, value = raw_param.partition("=")
            if not key:
                return f"could not execute '{operation}' on '{name}': a parameter name must not be empty"
            params[key] = value

        registry = load_service_registry(DEFAULT_SERVICES_PATH)
        descriptor = registry.get(normalised)
        if descriptor is None:
            return f"'{name}' is not a declared service - discover and declare it first."
        if descriptor.operation(operation) is None:
            return (
                f"'{name}' has no operation named '{operation}'. "
                f"Declared: {', '.join(descriptor.operation_names) or 'none'}"
            )

        from src.providers.declared_connection import DeclaredConnection
        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(DEFAULT_ACCOUNTS_PATH)
        account = store.active(descriptor.name)
        connection = DeclaredConnection(descriptor, account)

        auth_state = connection.authorization_for(operation)
        if not auth_state.grants_access:
            return (
                f"'{operation}' on '{name}' is not authorized - no credential is on file. "
                f"Add one with 'connections {name} connect <account-id> <credential>' first."
            )

        result = connection.execute(operation, **params)
        if not result.ok:
            return f"'{operation}' on '{name}' did not succeed ({result.status.value}): {result.detail}"
        return f"'{operation}' on '{name}' succeeded:\n{result.detail}"

    if action == "forget":
        removed = forget_service(normalised, DEFAULT_SERVICES_PATH)
        return f"forgot '{normalised}'" if removed else f"'{name}' was not declared"

    return f"unknown services action '{action}' for '{name}'. Try: discover, declare, execute, forget"


def run_sites_command(centre: "ControlCenter", rest: str) -> str:
    """Decide what to answer for the ``sites`` verb - literal website
    interaction's own declare/execute/forget/list, the direct
    counterpart to ``run_services_command`` above for a browser-backed
    connection instead of a REST one.

    **Why there is no ``discover`` here, unlike ``services``.** A REST
    service's own operations are worth discovering and previewing
    ahead of time because they are fixed and declared once. A
    website's usable fields and controls are not - they exist only on
    the page, discovered live, every visit, by ``WebsiteConnection``
    itself the moment ``read_page`` actually runs. There is nothing
    meaningful to preview at declare time; declaring a site is just
    naming it and its one fixed origin.

    Reads/writes ``DEFAULT_SITES_PATH`` by the identical explicit-
    argument shape ``run_services_command`` already threads
    ``DEFAULT_SERVICES_PATH`` through, for the identical reason: a test
    can monkeypatch this module's own ``DEFAULT_SITES_PATH`` and have
    every call below honour it.
    """
    args = rest.split()
    if not args:
        registry = load_site_registry(DEFAULT_SITES_PATH)
        if not registry:
            return (
                "no sites declared yet.\n"
                "declare one with: sites <name> declare <base-url> [auth-cookie-name]"
            )
        lines = ["DECLARED SITES (literal website interaction)", ""]
        for name, descriptor in sorted(registry.items()):
            auth = "session credential required" if descriptor.requires_auth else "open"
            lines.append(f"{name}: {descriptor.base_url} ({auth})")
        return "\n".join(lines)

    name, *rest_args = args
    normalised = name.strip().lower()

    if not rest_args:
        registry = load_site_registry(DEFAULT_SITES_PATH)
        descriptor = registry.get(normalised)
        if descriptor is None:
            return f"'{name}' is not a declared site. Try: sites (to list all)"
        lines = [f"{descriptor.name}: {descriptor.base_url}"]
        if descriptor.description:
            lines.append(descriptor.description)
        auth = f"session credential required (cookie '{descriptor.auth_cookie_name}')" if descriptor.requires_auth else "open - no credential needed"
        lines.append(f"  authorization: {auth}")
        from src.providers.website_connection import SUPPORTED_OPERATIONS, _CONSEQUENTIAL_OPERATIONS

        for op in SUPPORTED_OPERATIONS:
            kind = "consequential - needs confirmation" if op in _CONSEQUENTIAL_OPERATIONS else "safe"
            lines.append(f"  {op} ({kind})")
        return "\n".join(lines)

    action = rest_args[0].lower()

    if action == "declare":
        if len(rest_args) < 2:
            return "usage: sites <name> declare <base-url> [auth-cookie-name]"
        base_url = rest_args[1]
        auth_cookie_name = rest_args[2] if len(rest_args) >= 3 else ""
        try:
            descriptor = SiteDescriptor(name=name, base_url=base_url, auth_cookie_name=auth_cookie_name)
        except ValueError as exc:
            return f"could not declare '{name}': {exc}"
        declare_site(descriptor, DEFAULT_SITES_PATH)
        credential_note = (
            f" Add a credential with 'connections {descriptor.name} connect <account-id> <credential>' "
            f"- it will be sent as the '{auth_cookie_name}' session cookie."
            if descriptor.requires_auth else " No credential is needed - this site is open."
        )
        return (
            f"declared '{descriptor.name}' at {descriptor.base_url}.{credential_note} "
            f"Select it with MANAGER_CONNECTION_PROVIDER={descriptor.name} on the next start."
        )

    if action == "execute":
        # The identical DISCOVER(live) -> EXECUTE -> VERIFY -> RECORD
        # lifecycle 'services <name> execute' already gives a REST
        # operation, reusing the identical governance mechanisms -
        # never a second confirm_token dance, never a second audit
        # path - now reaching a real, JS-capable browser instead of a
        # REST client. navigate/read_page/fill_field need no more
        # confirmation than a plain read already does; click - the one
        # operation that can have a real effect on the real site - is
        # gated exactly like every other consequential write here
        # already is (see classify_administrative_write's own 'sites
        # ... execute' clause, which this command is reached through).
        if len(rest_args) < 2:
            return "usage: sites <name> execute <operation> [key=value ...]"
        operation = rest_args[1]
        params: "dict[str, str]" = {}
        for raw_param in rest_args[2:]:
            if "=" not in raw_param:
                return f"could not execute '{operation}' on '{name}': '{raw_param}' must be key=value"
            key, _, value = raw_param.partition("=")
            if not key:
                return f"could not execute '{operation}' on '{name}': a parameter name must not be empty"
            params[key] = value

        registry = load_site_registry(DEFAULT_SITES_PATH)
        descriptor = registry.get(normalised)
        if descriptor is None:
            return f"'{name}' is not a declared site - declare it first."

        from src.providers.website_connection import SUPPORTED_OPERATIONS, WebsiteConnection
        from src.services.credentials.account_store_file import load_credential_store

        if operation not in SUPPORTED_OPERATIONS:
            return f"'{name}' has no operation named '{operation}'. Declared: {', '.join(SUPPORTED_OPERATIONS)}"

        store = load_credential_store(DEFAULT_ACCOUNTS_PATH)
        account = store.active(descriptor.name)
        connection = WebsiteConnection(descriptor, account)

        auth_state = connection.authorization_for(operation)
        if not auth_state.grants_access:
            return (
                f"'{operation}' on '{name}' is not authorized - no credential is on file. "
                f"Add one with 'connections {name} connect <account-id> <credential>' first."
            )

        result = connection.execute(operation, **params)
        connection.close()
        if not result.ok:
            return f"'{operation}' on '{name}' did not succeed ({result.status.value}): {result.detail}"
        return f"'{operation}' on '{name}' succeeded:\n{result.detail}"

    if action == "forget":
        removed = forget_site(normalised, DEFAULT_SITES_PATH)
        return f"forgot '{normalised}'" if removed else f"'{name}' was not declared"

    return f"unknown sites action '{action}' for '{name}'. Try: declare, execute, forget"


# ---------------------------------------------------------------------------
# Full Alfred OS Ecosystem Evolution: a real, governed management surface
# spanning the whole ecosystem - providers, connections, specialists,
# Brain mode, and the existing system/schema/visual-environment Upgrade
# Manifest - not only Alfred's own LLM. Reuses everything already built:
# EcosystemRegistry/CredentialStore for providers and connections,
# runtime.specialists for specialists, src.evolution's own governed
# ledger for the propose/evaluate/approve/apply/verify/activate/rollback
# lifecycle, and src.control_center.manifest/upgrade_lifecycle for the
# system-version/schema axis this project already has a complete,
# separate governed pipeline for. Memory participates as a *developmental*
# read-only report (`ecosystem memory <agent>`), never through this same
# propose/apply pipeline - see src/services/evolution/ecosystem_evolution.py's
# own docstring for why conflating the two would misrepresent what memory
# actually is.
# ---------------------------------------------------------------------------


def run_ecosystem_command(centre: "ControlCenter", rest: str) -> str:
    settings = centre.runtime.settings
    args = rest.split()

    state = load_evolution_state(DEFAULT_LEDGER_PATH)
    components = discover_components(
        centre.runtime, centre.manager, accounts_path=DEFAULT_ACCOUNTS_PATH, config_path=DEFAULT_CONFIG_PATH,
    )
    state = ecosystem_evolution.seed_component_versions(state, components)
    ecosystem_evolution.save_evolution_state(state, DEFAULT_LEDGER_PATH)

    if not args:
        lines = ["ECOSYSTEM INVENTORY (real, currently-installed components)"]
        by_category: dict[str, list] = {}
        for component in components:
            by_category.setdefault(component.category, []).append(component)
        for category in sorted(by_category):
            lines.append(f"\n{category}:")
            for component in by_category[category]:
                lines.append(f"  {component.describe()}")
        lines.append(
            "\nUse 'ecosystem <component-id>' for detail, 'ecosystem memory <agent>' for a "
            "development snapshot, 'ecosystem brain <agent>' for Brain development, "
            "'ecosystem history' for what has happened, or 'ecosystem system' for the "
            "system/schema/visual-environment Upgrade Manifest."
        )
        return "\n".join(lines)

    if args[0] == "history":
        component_filter = args[1] if len(args) > 1 else None
        entries = state.history_for(component_filter)
        if not entries:
            return "no evolution history yet" + (f" for '{component_filter}'" if component_filter else "")
        return "\n".join(entry.describe() for entry in entries)

    if args[0] == "memory":
        if len(args) < 2:
            return "usage: ecosystem memory <agent-name>"
        agent_name = args[1]
        memory = memory_for(centre.manager, agent_name)
        if memory is None:
            return f"'{agent_name}' has no memory to report on"
        return memory_development_snapshot(agent_name, memory).describe()

    if args[0] == "brain":
        if len(args) < 2:
            return "usage: ecosystem brain <agent-name>"
        agent_name = args[1]
        memory = memory_for(centre.manager, agent_name)
        if memory is None:
            return f"'{agent_name}' has no memory to report Brain development from"
        mode = settings.brain if agent_name == centre.manager.own_skills.name else settings.specialist_brain
        return brain_development_snapshot(agent_name, memory, reasoning_mode=mode)

    if args[0] == "system":
        return _run_system_upgrade_command(centre, args[1:])

    component_id, *rest_args = args
    component = next((c for c in components if c.component_id == component_id), None)
    if component is None:
        from src.services.evolution.inventory import MANAGER_COMPONENT_ID, is_manager_component

        if is_manager_component(component_id):
            # A legacy Manager id ("alfred", HC-01) still addresses the Manager
            # component, under its canonical role id - proposals are found by
            # their own id, so nothing already recorded is orphaned.
            component_id = MANAGER_COMPONENT_ID
            component = next((c for c in components if c.component_id == MANAGER_COMPONENT_ID), None)
    if component is None:
        component = _new_family_member(components, component_id)
    if component is None:
        known = ", ".join(c.component_id for c in components)
        return f"no such component '{component_id}'. Known components: {known}"

    if not rest_args:
        lines = [component.describe()]
        if component.detail:
            lines.append(f"  detail: {component.detail}")
        proposals = state.proposals_for(component_id)
        if proposals:
            lines.append("  proposals:")
            for proposal in proposals:
                lines.append(
                    f"    {proposal.proposal_id}: {proposal.status.value} "
                    f"({proposal.current_version} -> {proposal.proposed_version}) - {proposal.reason}"
                )
        else:
            lines.append("  no proposals yet")
        return "\n".join(lines)

    action, *action_args = rest_args

    if action == "propose":
        if len(action_args) < 3:
            return "usage: ecosystem <component> propose <new-version> <risk> <reason>|<expected-benefit>"
        proposed_version, risk_name, *remainder = action_args
        try:
            risk = RiskLevel(risk_name.lower())
        except ValueError:
            return f"'{risk_name}' is not a real risk level. Try: low, medium, high, critical"
        text = " ".join(remainder)
        reason, sep, benefit = text.partition("|")
        if not sep:
            return "usage: ecosystem <component> propose <new-version> <risk> <reason>|<expected-benefit>"
        result = ecosystem_evolution.propose(
            state, component_id=component_id, proposed_version=proposed_version,
            proposed_change=f"{component_id}: {component.version} -> {proposed_version}",
            reason=reason.strip(), expected_benefit=benefit.strip(), risk_level=risk,
            ledger_path=DEFAULT_LEDGER_PATH,
        )
        if not result.success:
            return f"FAILED: {result.reason}"
        return f"proposed '{result.proposal.proposal_id}': {component.version} -> {proposed_version} ({result.reason})"

    if action == "propose-content":
        if len(action_args) < 3:
            return "usage: ecosystem <component> propose-content <path-to-new-content.md> <risk> <reason>|<expected-benefit>"
        content_path, risk_name, *remainder = action_args
        try:
            risk = RiskLevel(risk_name.lower())
        except ValueError:
            return f"'{risk_name}' is not a real risk level. Try: low, medium, high, critical"
        text = " ".join(remainder)
        reason, sep, benefit = text.partition("|")
        if not sep:
            return "usage: ecosystem <component> propose-content <path-to-new-content.md> <risk> <reason>|<expected-benefit>"
        result = ecosystem_evolution.propose_identity_content(
            state, component_id=component_id, new_content_path=content_path,
            reason=reason.strip(), expected_benefit=benefit.strip(), risk_level=risk,
            ledger_path=DEFAULT_LEDGER_PATH, staging_dir=DEFAULT_STAGING_DIR,
        )
        if not result.success:
            return f"FAILED: {result.reason}"
        return f"proposed '{result.proposal.proposal_id}': {component.version} -> {result.proposal.proposed_version} ({result.reason})"

    if action == "run":
        if len(action_args) < 2:
            return "usage: ecosystem <component> run <proposal-id> yes|no"
        proposal_id, decision = action_args[0], action_args[1]
        approved = decision.lower() in ("yes", "y", "true")
        result = ecosystem_evolution.run(
            state, proposal_id=proposal_id, approved=approved, settings=settings,
            ledger_path=DEFAULT_LEDGER_PATH, accounts_path=DEFAULT_ACCOUNTS_PATH,
            config_path=DEFAULT_CONFIG_PATH, staging_dir=DEFAULT_STAGING_DIR, backups_dir=DEFAULT_BACKUPS_DIR,
        )
        status = result.proposal.status.value if result.proposal else "(unknown)"
        detail = (
            f"{'OK' if result.success else 'FAILED'}: {result.reason} (status: {status})"
        )
        if result.success and component_id.startswith("brain."):
            # Pre-Stage-9 Campaign 4: a reasoning mode applies to the running
            # session in place, the same way a provider switch does.
            detail += "\nPersisted and independently re-verified." + _apply_reasoning_live(
                centre, load_credential_store(DEFAULT_ACCOUNTS_PATH)
            )
        elif result.success and component_id.startswith("surface."):
            detail += (
                "\nPersisted and independently re-verified. Live now: surfaces are described "
                "from the governed config on every read, so no reload is needed."
            )
        elif result.success:
            detail += "\nPersisted and independently re-verified." + _apply_evolution_live(centre, component_id)
        return detail

    if action == "rollback":
        if len(action_args) < 2:
            return "usage: ecosystem <component> rollback <proposal-id> yes|no"
        proposal_id, decision = action_args[0], action_args[1]
        approved = decision.lower() in ("yes", "y", "true")
        result = ecosystem_evolution.rollback(
            state, proposal_id=proposal_id, approved=approved, settings=settings,
            ledger_path=DEFAULT_LEDGER_PATH, accounts_path=DEFAULT_ACCOUNTS_PATH,
            config_path=DEFAULT_CONFIG_PATH, staging_dir=DEFAULT_STAGING_DIR, backups_dir=DEFAULT_BACKUPS_DIR,
        )
        status = result.proposal.status.value if result.proposal else "(unknown)"
        detail = f"{'OK' if result.success else 'FAILED'}: {result.reason} (status: {status})"
        if result.success and component_id.startswith("brain."):
            # A rolled-back reasoning mode is live in this session too.
            detail += "\n" + _apply_reasoning_live(centre, load_credential_store(DEFAULT_ACCOUNTS_PATH)).lstrip()
        elif result.success and not component_id.startswith("surface."):
            detail += "\n" + _apply_evolution_live(centre, component_id).lstrip()
        return detail

    return f"unknown ecosystem action '{action}'. Try: propose, propose-content, run, rollback"


def _run_system_upgrade_command(centre: "ControlCenter", args: list[str]) -> str:
    """The real, existing Stage 6-9 Upgrade Manifest/Planner/Executor/
    Lifecycle (src/control_center/manifest.py, upgrade_planner.py,
    src/upgrade_executor.py, src/upgrade_lifecycle.py) - complete and
    already tested, never duplicated here. Only the command surface is
    new: nothing before this checkpoint ever called ``run_upgrade`` from
    the Control Center at all.
    """
    from src.control_center.manifest import upgrade_manifest
    from src.control_center.snapshot import SNAPSHOT_VERSION
    from src.control_center.upgrade_planner import UpgradeRequest
    from src.upgrade_lifecycle import run_upgrade

    manifest = upgrade_manifest(snapshot_version=SNAPSHOT_VERSION)

    if not args:
        lines = [
            "SYSTEM UPGRADE MANIFEST (real, currently-installed compatibility contracts)",
            f"  system_version       : {manifest.system_version}",
            f"  snapshot_version     : {manifest.snapshot_version}",
            f"  memory_schema_version: {manifest.memory_schema_version}",
            f"  state_schema_version : {manifest.state_schema_version}",
        ]
        for entry in manifest.visual_environments:
            lines.append(f"  visual_environment[{entry.surface}]: {entry.id}:{entry.version}")
        lines.append("")
        lines.append("upgrade with: ecosystem system upgrade <target-system-version> yes|no")
        return "\n".join(lines)

    if args[0] != "upgrade":
        return f"unknown 'system' action '{args[0]}'. Try: (nothing), upgrade"
    if len(args) < 3:
        return "usage: ecosystem system upgrade <target-system-version> yes|no"

    target_version, decision = args[1], args[2]
    approved = decision.lower() in ("yes", "y", "true")
    request = UpgradeRequest(target_system_version=target_version)
    result = run_upgrade(request, approved=approved, current_manifest=manifest)

    lines = [
        f"plan status : {result.plan.status.value}",
        f"reason      : {result.reason}",
        f"executed    : {result.executed}",
        f"verified    : {result.verified}",
    ]
    if result.execution is not None and result.execution.stores_affected:
        lines.append(f"stores affected: {', '.join(result.execution.stores_affected)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Administrative-write audit trail (2026-09-08).
#
# The gap CHECK found after the Control Center repair: provider/
# connections/capabilities/ecosystem writes reachable through /command
# were not recorded anywhere durable - only a conversational
# consequential action (see scripts/exchange.py's own
# retain/CONSEQUENTIAL_CAPABILITIES wiring) ever reached
# src.services.audit.action_log. Closed here, once, at the one real
# place both doors (a terminal's own run_command, and serve_ui.py's
# route_command, which calls this identical function for the same
# reason it already reuses run_provider_command etc.) already share -
# never a second recording call at either door, and never a second
# audit store. `classify_administrative_write` is also the confirmation
# gate's own classifier (see route_command's docstring) - one
# definition of "which commands are administrative writes", read by
# both concerns.
# ---------------------------------------------------------------------------


def classify_administrative_write(command: str) -> "tuple[bool, str]":
    """Whether `command` is one of the administrative writes this project
    gates behind confirmation and records to the durable audit trail, and
    - when it is - a plain-language description of what it would do,
    shown to a person before they confirm it. Returns ``(False, "")`` for
    everything else, including every read-only verb and every
    pre-existing preference-setting verb (avatar/environment/profile/
    voice) - cosmetic preferences, not the credential/provider/
    capability/evolution surface this covers.
    """
    parts = command.split()
    if not parts:
        return False, ""
    verb = parts[0].lower()

    if verb == "capabilities" and len(parts) >= 3 and parts[1].lower() in ("enable", "disable"):
        return True, f"{parts[1].lower()} the '{parts[2]}' capability"

    if verb == "provider" and len(parts) >= 4 and parts[2].lower() == "check":
        # The one provider action that leaves the machine: it asks the vendor
        # to authorise a real request. Confirmed and audited like every other
        # outward action, never run as a side effect of looking at a screen.
        return True, (
            f"send one real request to '{parts[3]}' to find out whether that credential is "
            "accepted (this leaves the machine and a metered vendor may charge for it)"
        )

    if verb == "provider" and len(parts) >= 3 and parts[2].lower() in ("add", "select", "revoke", "use"):
        category, action = parts[1], parts[2].lower()
        if action == "use" and len(parts) >= 4:
            what = "the live reasoning provider" if category.lower() == LLM_CATEGORY else f"'{category}'"
            return True, f"switch {what} to '{parts[3]}' in the running system and for every future start"
        if action == "add" and len(parts) >= 5:
            return True, (
                f"add a new '{parts[3]}' account '{parts[4]}' to '{category}' "
                "(the credential itself is never shown here)"
            )
        if action == "select" and len(parts) >= 5:
            return True, f"make '{parts[3]}/{parts[4]}' the active account for '{category}'"
        if action == "revoke" and len(parts) >= 5:
            return True, f"revoke the account '{parts[3]}/{parts[4]}' from '{category}'"
        return True, f"{action} an account for '{category}'"

    if verb == "connections" and len(parts) >= 3 and parts[2].lower() in ("connect", "disconnect"):
        return True, f"{parts[2].lower()} the '{parts[1]}' connection"

    if verb == "services" and len(parts) >= 3 and parts[2].lower() in ("declare", "forget"):
        action = parts[2].lower()
        if action == "declare":
            return True, f"declare a new service connection '{parts[1]}' (open-ended discovery)"
        return True, f"forget the declared service '{parts[1]}'"

    if verb == "services" and len(parts) >= 4 and parts[2].lower() == "execute":
        # Operation-level, not connection-level: a GET (read) needs no
        # more confirmation than 'connections <name> status' already
        # does; anything else genuinely changes state on a real,
        # external service and is gated exactly like every other
        # consequential write above - read from the operation's own
        # already-declared method, the same real signal
        # DeclaredConnection.requires_confirmation_for reuses, never a
        # second classification of the identical fact.
        service_name, operation_name = parts[1], parts[3]
        descriptor = load_service_registry(DEFAULT_SERVICES_PATH).get(service_name.strip().lower())
        operation = descriptor.operation(operation_name) if descriptor is not None else None
        if operation is not None and operation.method == "GET":
            return False, ""
        return True, f"execute the operation '{operation_name}' on the connected service '{service_name}'"

    if verb == "sites" and len(parts) >= 3 and parts[2].lower() in ("declare", "forget"):
        action = parts[2].lower()
        if action == "declare":
            return True, f"declare a new website connection '{parts[1]}'"
        return True, f"forget the declared site '{parts[1]}'"

    if verb == "sites" and len(parts) >= 4 and parts[2].lower() == "execute":
        # Literal-website-interaction counterpart to 'services ...
        # execute' above: the identical operation-level, not
        # connection-level, classification - navigate/read_page/
        # fill_field need no more confirmation than a plain read
        # already does; click genuinely changes state on the real
        # site. Read from WebsiteConnection's own fixed, universal
        # classification directly (never a second, hand-maintained
        # copy of "which operation is consequential" kept in sync by
        # hand) - importing the one real constant that already governs
        # every other path to the identical fact.
        from src.providers.website_connection import _CONSEQUENTIAL_OPERATIONS

        operation_name = parts[3].lower()
        if operation_name not in _CONSEQUENTIAL_OPERATIONS:
            return False, ""
        return True, f"execute the operation '{operation_name}' on the connected site '{parts[1]}'"

    if (
        verb == "workspace" and len(parts) >= 3 and parts[1].lower() in WORKSPACE_DEFINITION_ACTIONS
        and not (len(parts) >= 4 and parts[2].lower() in ("add", "remove", "use"))
    ):
        return True, f"{parts[1].lower()} the workspace '{parts[2]}'"
    if verb == "workspace" and len(parts) >= 4 and parts[1].lower() == "type":
        return True, f"define or withdraw the workspace type '{parts[2]}'"
    if verb == "workspace" and len(parts) >= 4 and parts[2].lower() in ("add", "remove", "use"):
        return True, f"{parts[2].lower()} the '{parts[3]}' workspace for '{parts[1]}'"

    if (
        verb == "ecosystem" and len(parts) >= 5
        and parts[2].lower() in ("run", "rollback")
        and parts[4].lower() in ("yes", "y", "true")
    ):
        component_id, sub_action, proposal_id = parts[1], parts[2].lower(), parts[3]
        verb_word = "apply" if sub_action == "run" else "roll back"
        return True, f"{verb_word} proposal '{proposal_id}' for component '{component_id}'"

    return False, ""


def _administrative_write_succeeded(verb: str, result: str) -> bool:
    """Whether an administrative write's own result string reports
    genuine success - a precise, per-verb whitelist of this project's
    own actual success wording, verified by reading every real success
    and failure return in run_provider_command/run_connections_command/
    run_capability_registry_command/run_ecosystem_command directly, not
    a generic "does this look like an error" guess.

    A blacklist of failure markers ("FAILED:", "no such", ...) was tried
    first and rejected: real review found at least three failure paths
    reachable through these exact write actions that never say any of
    those words - a misdirected 'connections <provider-backend> connect'
    ("... is a provider backend ..."), gmail's own connect/disconnect
    refusal ("gmail authenticates via OAuth ..."), and capabilities'
    own structural-fallback refusal ("... cannot be disabled ..."). A
    whitelist of the few, consistent success sentences these functions
    actually return is the more accurate signal - anything not
    recognisably one of them is honestly recorded as failed, which is
    the safer direction for an audit trail to be wrong in.
    """
    text = result.strip()
    if verb == "provider":
        # 'provider <category> use <backend>' (Campaign 4/8) arrived after this
        # whitelist was written and has its own two success sentences - the
        # live reasoning rebind, and the saved-for-next-start form every other
        # governed backend category returns. Without them a switch that really
        # happened was recorded in the durable audit trail as `failed`, which
        # is the audit being wrong in the one direction that matters: it made
        # a real change to the running system look like a no-op.
        return (
            text.startswith("added ")
            or " is now active." in text
            or text.startswith("revoked ")
            or text.startswith("Switched the reasoning provider ")
            or (text.startswith("Saved: ") and " now uses " in text)
            # 'check' asked a vendor and got an answer - both outcomes are the
            # write succeeding; only never reaching them is a failure.
            or text.startswith("AUTHORIZED:")
            or (text.startswith("NOT AUTHORIZED:") and "the vendor answered" in text)
        )
    if verb == "workspace":
        # run_workspace_types_command returns early, with no "Saved", for
        # every refusal (unknown agent, unknown type, nothing to change).
        return " Saved - " in text
    if verb == "connections":
        return text.startswith("connected ") or text.startswith("disconnected ")
    if verb in ("services", "sites"):
        return text.startswith("declared ") or text.startswith("forgot ") or "' succeeded:" in text
    if verb == "capabilities":
        return " is now enabled " in text or " is now disabled " in text
    if verb == "ecosystem":
        return text.startswith("OK:")
    return False


def _record_if_administrative_write(command: str, result: str, action_log_path) -> None:
    """Append one durable audit entry when, and only when, `command` was
    a genuine administrative write - never for a read, never for a
    preference-setting verb, and never twice for the same execution
    (called exactly once, from run_command's own single dispatch for
    that verb). `action_log_path` of None (every existing caller that
    does not deliberately choose one - including every test that has
    not opted in) records nothing, the identical opt-in shape
    scripts/exchange.py's own action_log_path parameter already has.
    """
    if action_log_path is None:
        return
    requires_confirmation, _ = classify_administrative_write(command)
    if not requires_confirmation:
        return
    verb = command.strip().split(None, 1)[0].lower()
    record_action(
        ActionLogEntry(
            timestamp=audit_utc_now().isoformat(),
            # A person operating the Control Center directly, not an
            # agent's own action - the existing field must not
            # misrepresent one as the other.
            actor="operator",
            capability=f"admin.{verb}",
            succeeded=_administrative_write_succeeded(verb, result),
        ),
        path=action_log_path,
    )


def run_command(
    centre: ControlCenter,
    command: str,
    columns: int,
    channel: Channel,
    conversation: Conversation,
    *,
    action_log_path: "str | Path | None" = None,
) -> str:
    """One command, one screen. Overview first, detail on demand.

    ``channel`` and ``conversation`` are required rather than
    optional-with-a-default, deliberately. A default would have to build its
    own, and every caller that took it - including every test - would then be
    exercising something the composition root never made. The point of these
    phases is that requests travel through the composed door and are recorded
    in the composed room; a parameter that can be skipped is a door that can be
    walked around.

    ``action_log_path``, when given, durably records the administrative
    writes among ``capabilities``/``provider``/``connections``/``ecosystem``
    (see ``classify_administrative_write``) after they actually execute -
    the one real place both a terminal session and ``serve_ui.py``'s own
    ``route_command`` end up, so the identical command typed at either
    door is recorded by identical code, once. None (the default, and
    every existing caller that does not pass this) records nothing -
    unchanged behaviour for every caller that has not opted in.
    """
    command = command.strip()
    if not command:
        return ""
    # The composed door, held by the view so a backend switched below (a
    # speech engine) reaches the channel that actually speaks.
    centre.bind_channel(channel)

    verb, _, rest = command.partition(" ")
    verb, rest = verb.lower(), rest.strip()

    if verb in ("help", "?"):
        return HELP

    if verb == "status":
        overview = centre.overview()
        if not rest:
            return render_overview(overview, columns)
        if rest.lower() == "system":
            return render_system(overview, columns)
        agent = overview.agent(rest)
        if agent is None:
            known = ", ".join(a.name for a in overview.agents)
            return f"no agent called '{rest}'. Registered: {known}"
        return render_agent(agent, columns)

    if verb == "history":
        category = rest.lower() or None
        view = centre.history(category=category)
        if category and not view.events and category not in view.categories:
            return (
                f"no events in category '{category}'. "
                f"Available: {', '.join(view.categories) or 'none yet'}"
            )
        return render_history(view, columns)

    if verb == "actions":
        # Foundation Repair #4 (reconciliation audit, 2026-09-05): the
        # durable counterpart to `history` above - which is real, but is
        # rebuilt fresh every process and so cannot answer "what did
        # Alfred do across restarts". Read-only, like every other view in
        # this file: recording happens only inside exchange(), never here.
        entries = load_action_log(DEFAULT_ACTION_LOG_PATH)
        if not entries:
            return "no consequential actions recorded yet"
        return "\n".join(entry.describe() for entry in entries[-20:])

    if verb == "unresolved":
        # Stage 12 closure: genuine, currently-unresolved personal
        # uncertainty - "mom's birthday is either March 24 or May 24".
        # Read-only, like history/status: resolving one happens by
        # saying the correct date to Alfred (the "talk" command above),
        # never from this screen.
        return render_unresolved_facts(centre.unresolved_facts(), columns)

    if verb == "personal":
        # Generalized user knowledge/personal-context checkpoint:
        # real, current, per-topic state - never decorative. Read-only,
        # like every other view here: teaching happens through ordinary
        # conversation, not from this screen.
        return render_personal_context(centre.personal_context(), columns)

    if verb == "governance":
        # Context-Aware Agent Capability & Safety Governance: the real,
        # most-recent decisions Alfred's own governed capabilities
        # actually made - never decorative, never a second registry.
        # Read-only, like every other view here: a decision is made only
        # when a real governed action is actually attempted, never from
        # this screen.
        return render_governance(centre.governance(), columns)

    if verb == "calendar":
        # Generalized Personal Organization/Calendar checkpoint: real,
        # current calendar state - never decorative. Read-only, like
        # every other view here: adding/cancelling/rescheduling happens
        # through ordinary conversation, not from this screen.
        return render_calendar(centre.calendar_items(), columns, reminders=centre.reminders())

    if verb == "experience":
        # General Time/Event Awareness + Proactive Visual Experience
        # checkpoint: real, current selection/history state - never
        # decorative. Read-only: selection happens through the governed
        # proactive evaluation step, not from this screen.
        return render_experiences(centre.experiences(), columns)

    if verb == "workspace":
        # Alfred's Real 3D Workspace checkpoint: real, current Workspace
        # state - discovered from each agent's own declared
        # InterfaceDescriptor, never a fixed list naming Alfred. Entering
        # happens through the real served-page route (POST /workspace/enter).
        # Post-Pre-Stage-9: which workspace *types* each agent has, and which
        # is open, is the owner's choice here - saved in the governed config.
        parts_rest = rest.split()
        if (
            len(parts_rest) >= 2 and parts_rest[0].lower() in WORKSPACE_DEFINITION_ACTIONS
            and not (len(parts_rest) >= 3 and parts_rest[1].lower() in ("add", "remove", "use"))
        ):
            result = run_workspace_definition_command(centre, parts_rest[0].lower(), rest.split(None, 1)[1])
            _record_if_administrative_write(command, result, action_log_path)
            return result
        if len(parts_rest) >= 3 and parts_rest[0].lower() == "type":
            result = run_workspace_type_definition_command(rest.split(None, 1)[1])
            _record_if_administrative_write(command, result, action_log_path)
            return result
        if len(parts_rest) >= 3 and parts_rest[1].lower() in ("add", "remove", "use"):
            result = run_workspace_types_command(centre, parts_rest[0], parts_rest[1].lower(), parts_rest[2])
            # Confirmed over HTTP like every other administrative write
            # (classify_administrative_write names it), so it belongs in the
            # durable trail for the same reason - it changes what the owner's
            # agents offer, across restarts.
            _record_if_administrative_write(command, result, action_log_path)
            return result
        return render_workspaces(centre.workspaces(), columns, governed=centre.governed_values())

    if verb == "tasks":
        # Phase 5.1: read-only state visibility, the same "genuine
        # management surface, never a fake parallel one" standard every
        # other Control Center view already holds to - and deliberately
        # read-only. The real create/complete/update surface is
        # conversational (src/manager/capabilities.py's own
        # create_task/list_tasks/complete_task/update_task, reached
        # through 'talk'), because a task is ordinary personal data a
        # person names in passing, not administrative configuration -
        # building a second, form-based way to type the same sentence
        # would be exactly the parallel task app this phase's own brief
        # forbids. See src/services/memory/tasks.py's own describe_task
        # for the identical rendering a conversational answer already
        # uses, so the two surfaces never show a task two different ways.
        agent_name, _, qualifier = rest.partition(" ")
        agent_name = agent_name.strip() or centre.manager.own_skills.name
        include_complete = qualifier.strip().lower() == "all"

        view = centre.overview().agent(agent_name)
        if view is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"

        memory = memory_for(centre.manager, view.name)
        if memory is None:
            return f"'{view.name}' has no memory to hold tasks in"

        tags = set() if include_complete else {"task:pending"}
        records = memory.recall(kind=MemoryCategory.TASK.value, tags=tags, limit=100)
        if not records:
            return f"{view.name} has no {'tasks' if include_complete else 'pending tasks'}."
        lines = [f"TASKS - {view.name}"]
        lines.extend(f"  {describe_task(record)}" for record in records)
        if not include_complete:
            lines.append(f"  (type 'tasks {view.name} all' to include completed tasks)")
        return "\n".join(lines)

    if verb == "translation":
        # Universal voice translation: what is installed and switched on - the
        # engine, the listener, the voice, and every language with whether it
        # can be translated and spoken. Read-only; choosing the engine is
        # 'provider translation_provider use <backend>'.
        return render_translation(getattr(centre.runtime, "translation", None))

    if verb == "recordings":
        # Real Audio Recording/Capture checkpoint: read-only state
        # visibility, the identical "genuine management surface, never a
        # fake parallel one" standard the 'tasks'/'workspace' commands
        # above already hold to - and deliberately read-only for the
        # SAVED recordings themselves: starting/stopping/cancelling is
        # conversational (src/manager/capabilities.py's own
        # start_recording/stop_recording/cancel_recording, reached
        # through 'talk'). Reads centre.recordings() - the identical
        # discovery/backend-state a served page's own snapshot uses -
        # never a second, inline recall() here.
        agent_name = rest.strip() or centre.manager.own_skills.name
        match = next((v for v in centre.recordings() if v.agent == agent_name), None)
        if match is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"
        return render_recordings(match, columns)

    if verb == "artifacts":
        # Multimodal workspace/artifact checkpoint: read-only visibility
        # over the identical, authoritative ArtifactStore
        # ArtifactProductionTool itself writes through
        # (centre.runtime.artifacts) - never a second copy, never a
        # fabricated listing. The real write surface is conversational
        # ("make me a handout", "revise the handout", reached through
        # 'talk'), the same "administrative configuration vs. ordinary
        # personal content" boundary the 'tasks' command above already
        # draws for the identical reason.
        artifact_id = rest.strip()

        # REPAIR-01/02 (user-facing file ingestion): the terminal's own door
        # for supplying a file - a path on the machine this terminal is
        # running on, the local-terminal equivalent of a browser's file
        # picker. Through the one governed ingestion mechanism every other
        # door uses (src/services/artifacts/ingestion.py), never a second
        # store. Refused over HTTP by serve_ui.py's route_command: a server
        # filesystem path must never be accepted from the network - a
        # browser supplies bytes through POST /upload instead.
        if artifact_id.lower() == "add" or artifact_id.lower().startswith("add "):
            raw_path = artifact_id[3:].strip().strip('"').strip("'")
            if not raw_path:
                return "usage: artifacts add <path to a file on this machine>"
            source_path = Path(raw_path).expanduser()
            if not source_path.is_file():
                return f"no file found at '{raw_path}'"
            try:
                data = source_path.read_bytes()
            except OSError as exc:
                return f"could not read '{raw_path}': {exc}"
            owner = centre.runtime.identity.name
            try:
                added, created = ingest_supplied_file(
                    centre.runtime.artifacts, owner_agent=owner, filename=source_path.name,
                    data=data, source="control_center",
                )
            except IngestionError as exc:
                return f"not added: {exc}"
            units = read_artifact_units(added)
            readable = f"{len(units)} readable unit(s)" if units else "no readable text"
            state = "added" if created else "already present (identical content)"
            # The share belongs in the room, because in a terminal session this
            # *is* the room: the next line typed here is the person's next line
            # in the same conversation. Without this the file was registered
            # and then invisible to every capability that resolves "this file"
            # - "read this file to me" answered "share one (attach it in the
            # conversation or add it in the Control Center)" to the person who
            # had just done exactly that. The served page's own Control Center
            # panel is deliberately different (serve_ui.route_upload: added
            # there, nothing is said in the room) - that upload has no
            # conversation in front of it to be part of.
            kind = added.latest.metadata.get("content_kind", "other")
            shared = record_shared_file(
                conversation,
                description=(
                    f"{SHARED_FILE_PREFIX} {added.latest.title} "
                    f"(artifact {added.artifact_id}, {kind}, {readable})"
                ),
                addressed=owner,
            )
            pointer = (
                "ask Alfred about it - \"read this file to me\", \"what does this file say\""
                if shared else f"ask Alfred about it with 'artifact {added.artifact_id}'"
            )
            return (
                f"{state}: {added.artifact_id} [{added.type}] {added.latest.title!r} "
                f"owner={owner} ({readable}) - {pointer}, or 'artifacts {added.artifact_id}'"
            )

        artifacts = centre.runtime.artifacts.all()

        if not artifact_id:
            if not artifacts:
                return "no artifacts persisted yet"
            lines = ["ARTIFACTS"]
            for artifact in sorted(artifacts, key=lambda a: a.latest.created_at, reverse=True):
                latest = artifact.latest
                renditions = ", ".join(sorted(latest.renditions)) or "none"
                delivery = ", ".join(available_delivery_methods(latest)) or "none yet"
                lines.append(
                    f"  {artifact.artifact_id} [{artifact.type}] v{latest.version} "
                    f"owner={artifact.owner_agent} status={latest.status} "
                    f"title={latest.title!r} renditions={renditions} delivery={delivery}"
                )
            lines.append("  (type 'artifacts <id>' for one artifact's full version history)")
            return "\n".join(lines)

        artifact = centre.runtime.artifacts.get(artifact_id)
        if artifact is None:
            return f"no artifact found with id '{artifact_id}'"
        lines = [
            f"ARTIFACT {artifact.artifact_id} [{artifact.type}] owner={artifact.owner_agent}"
        ]
        for version in artifact.versions:
            renditions = ", ".join(f"{fmt}={path}" for fmt, path in sorted(version.renditions.items())) or "none"
            delivery = ", ".join(available_delivery_methods(version)) or "none yet"
            related = ", ".join(version.related_artifact_ids) or "none"
            lines.append(
                f"  v{version.version} status={version.status} title={version.title!r} "
                f"created={version.created_at.isoformat()} content={version.content_path} "
                f"renditions={renditions} delivery={delivery} derived_from={related}"
            )
        return "\n".join(lines)

    if verb == "avatar":
        # Stage 17: a governed preference, read and changed here through
        # the same AvatarProvider/set_avatar_preference() this project's
        # `POST /avatar` route also calls - one write path, two entry
        # points. Choosing an avatar never builds, replaces or renames an
        # agent: it only ever changes what an already-registered agent
        # looks like.
        provider = centre.runtime.avatar_provider
        agent_name, _, avatar_id = rest.partition(" ")
        agent_name, avatar_id = agent_name.strip(), avatar_id.strip()

        if not agent_name:
            lines = ["AVATAR"]
            for view in centre.overview().agents:
                presence = view.presence
                if presence is None:
                    continue
                lines.append(
                    f"  {view.name}: {presence.avatar.label} "
                    f"({presence.avatar.avatar_id}) - {presence.state.value}"
                )
            return "\n".join(lines)

        view = centre.overview().agent(agent_name)
        if view is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"

        if not avatar_id:
            lines = [f"AVATAR - {view.name}"]
            if view.presence is not None:
                lines.append(
                    f"  current: {view.presence.avatar.label} "
                    f"({view.presence.avatar.avatar_id})"
                )
            lines.append("  choices:")
            for profile in provider.catalog():
                lines.append(f"    {profile.avatar_id}: {profile.label}")
            lines.append(f"  choose with: avatar {view.name} <avatar_id>")
            return "\n".join(lines)

        memory = memory_for(centre.manager, view.name)
        if memory is None:
            return f"'{view.name}' has no memory to store an avatar preference in"

        ok = set_avatar_preference(
            view.name,
            avatar_id,
            provider=provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )
        if not ok:
            known = ", ".join(p.avatar_id for p in provider.catalog())
            return f"no such avatar '{avatar_id}'. Choices: {known}"
        return f"{view.name}'s avatar is now {avatar_id.strip().lower()}"

    if verb == "environment":
        # Whole-System Reality Audit: the identical governed-preference
        # pattern the 'avatar' command above already uses, extended to
        # the environment axis - found missing during that audit. One
        # write path (set_environment_preference), reached here; never
        # a second implementation of what avatar already established.
        # Choosing an environment never builds, replaces or renames an
        # agent, and never touches Brain/Memory/Voice/Avatar - only
        # what space an already-registered agent is described as being
        # presented within.
        env_provider = centre.runtime.environment_provider
        agent_name, _, environment_id = rest.partition(" ")
        agent_name, environment_id = agent_name.strip(), environment_id.strip()

        if not agent_name:
            lines = ["ENVIRONMENT"]
            for view in centre.overview().agents:
                memory = memory_for(centre.manager, view.name)
                current = environment_for(
                    view.name, provider=env_provider,
                    recall=memory.recall if memory is not None else None,
                )
                lines.append(f"  {view.name}: {current.label} ({current.environment_id})")
            return "\n".join(lines)

        view = centre.overview().agent(agent_name)
        if view is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"

        memory = memory_for(centre.manager, view.name)

        if not environment_id:
            current = environment_for(
                view.name, provider=env_provider,
                recall=memory.recall if memory is not None else None,
            )
            lines = [f"ENVIRONMENT - {view.name}", f"  current: {current.label} ({current.environment_id})"]
            lines.append("  choices:")
            for profile in env_provider.catalog():
                lines.append(f"    {profile.environment_id}: {profile.label}")
            lines.append(f"  choose with: environment {view.name} <environment_id>")
            return "\n".join(lines)

        if memory is None:
            return f"'{view.name}' has no memory to store an environment preference in"

        ok = set_environment_preference(
            view.name,
            environment_id,
            provider=env_provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )
        if not ok:
            known = ", ".join(p.environment_id for p in env_provider.catalog())
            return f"no such environment '{environment_id}'. Choices: {known}"
        return f"{view.name}'s environment is now {environment_id.strip().lower()}"

    if verb == "profile":
        # Stage 17.4: the *other* dimensions of a DigitalHumanProfile -
        # posture, clothing, idle/listening/speaking behaviour,
        # expression and gesture style. Never the avatar itself, which
        # stays the `avatar` command above; this is another caller of
        # the same governed set_digital_human_style() write `POST
        # /profile` also calls - one write path, two entry points, the
        # identical shape the avatar command already established.
        agent_name, _, remainder = rest.partition(" ")
        agent_name = agent_name.strip()

        if not agent_name:
            lines = ["PROFILE"]
            for view in centre.overview().agents:
                lines.append(f"  {view.name}")
            lines.append("  see one with: profile <agent>")
            return "\n".join(lines)

        view = centre.overview().agent(agent_name)
        if view is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"

        field, _, value = remainder.strip().partition(" ")
        field, value = field.strip(), value.strip()

        if not field:
            lines = [f"PROFILE - {view.name}"]
            if view.presence is not None and view.presence.profile is not None:
                for name in DIGITAL_HUMAN_STYLE_FIELDS:
                    current = getattr(view.presence.profile, name) or "—"
                    lines.append(f"  {name}: {current}")
            lines.append(
                f"  set one with: profile {view.name} <field> <value> "
                f"(fields: {', '.join(DIGITAL_HUMAN_STYLE_FIELDS)})"
            )
            return "\n".join(lines)

        if not value:
            return f"say what {field} should be: profile {view.name} {field} <value>"

        memory = memory_for(centre.manager, view.name)
        if memory is None:
            return f"'{view.name}' has no memory to store a presentation style in"

        ok = set_digital_human_style(
            view.name, {field: value},
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        if not ok:
            return (
                f"no such field '{field}'. Fields: {', '.join(DIGITAL_HUMAN_STYLE_FIELDS)}"
            )
        return f"{view.name}'s {field} is now '{value}'"

    if verb == "voice":
        # Reported, never asserted. What is shown is the adapter's own
        # Availability - three states, not a boolean - so an engine that has
        # not been proven reads as unproven rather than as missing.
        #
        # Read from the channel the composition root built, not constructed
        # here. This used to build its own NoSpeech, which meant the screen
        # reported a fact about this function rather than about the running
        # system - and would have gone on saying "no speech engine" after one
        # was installed.
        # Duck-typed, in the same spirit as availability_of and
        # describe_interface: a door that carries a speech engine is asked for
        # it, and one that does not says so. A TextChannel genuinely has no
        # engine, and that is a truthful answer rather than a missing case.
        speech = getattr(channel, "speech", None)

        # 0.2 Reality Audit: `voice` alone stays exactly the engine-status
        # report above - unchanged. `voice <agent>` and `voice <agent>
        # <name>` are additive, the same governed-preference shape
        # `avatar`/`avatar <agent> <id>` already established, applied to
        # which real voice an agent is heard in rather than which avatar
        # it is drawn as.
        agent_name, _, voice_name = rest.partition(" ")
        agent_name, voice_name = agent_name.strip(), voice_name.strip()

        if agent_name.lower() == "providers":
            return render_voice_providers(channel)
        if not agent_name:
            if speech is None:
                return f"VOICE\n  the {channel.name} channel carries no speech engine"
            return render_voice(
                VoiceView(engine=speech.name, availability=speech.availability), columns
            )

        view = centre.overview().agent(agent_name)
        if view is None:
            known = ", ".join(a.name for a in centre.overview().agents)
            return f"no agent called '{agent_name}'. Registered: {known}"

        memory = memory_for(centre.manager, view.name)
        if memory is None:
            return f"'{view.name}' has no memory to store a voice preference in"

        agent = (
            centre.manager.own_skills
            if view.name == (centre.manager.own_skills.name if centre.manager.own_skills else None)
            else centre.manager.registry.get(view.name)
        )
        # Agent-autonomous voice selection checkpoint: the same real,
        # live discovery call bound onto capabilities - never a second
        # mechanism - so an explicit override typed by real display name
        # (e.g. an ElevenLabs/Kokoro voice) resolves to the correct
        # provider-specific field, exactly as an autonomous choice
        # already does.
        discover_fn = getattr(speech, "available_voices", None)
        current = (
            effective_voice_for(agent, recall=memory.recall, discover_voices=discover_fn)
            if agent is not None else None
        )

        # Only a Windows-style engine can enumerate its own installed
        # voices (SpeechAdapter's own contract carries no such method -
        # see channels/speech.py's docstring on why); duck-typed, exactly
        # as `_select_voice_name` itself already reads this.
        catalog_fn = getattr(speech, "installed_voice_names", None)
        available = tuple(catalog_fn()) if callable(catalog_fn) else None

        sub_action, _, sub_value = voice_name.partition(" ")
        if sub_action.lower() in ("language", "accent", "pace", "test") and agent is not None:
            return run_voice_configuration_command(
                centre, channel, view.name, agent, memory, sub_action.lower(), sub_value.strip(),
            )

        if not voice_name:
            lines = [f"VOICE - {view.name}"]
            lines.append(f"  current preferred_voice: {current.preferred_voice if current else '(none declared)'}")
            stored_choice = voice_preference_for(view.name, recall=memory.recall) if memory else None
            if stored_choice:
                lines.append(f"  chosen voice: {stored_choice}")
            # The voice the active provider actually speaks with - resolved the
            # way the channel resolves it, not only the declared name field.
            if speech is not None and current is not None:
                from src.services.identity.voice_configuration import current_option as _current_option

                speaking_with = _current_option(voice_facts(speech), current)
                lines.append(
                    f"  speaking with ({speech.name}): "
                    + (speaking_with.name if speaking_with is not None else "the provider's default - no installed voice is chosen")
                )
            # Agent-autonomous voice selection checkpoint: which of the
            # three governed sources (explicit override, this agent's
            # own autonomous choice, the bare declared default) the
            # current value actually came from - real state, read the
            # same way effective_voice_for itself resolves precedence,
            # never inferred from the string alone.
            override = voice_preference_for(view.name, recall=memory.recall) if memory else None
            autonomous = autonomous_voice_choice_for(view.name, recall=memory.recall) if memory else None
            if override:
                lines.append("  source: explicit user override")
            elif autonomous is not None:
                lines.append(
                    f"  source: agent's own autonomous choice "
                    f"(language={autonomous.language or 'unknown'}, accent={autonomous.accent or 'unknown'}, "
                    f"gender={autonomous.gender or 'unknown'})"
                )
            else:
                lines.append("  source: declared default")
            if available is not None:
                lines.append("  choices (this engine's installed voices):")
                for name in available:
                    lines.append(f"    {name}")
            else:
                lines.append(
                    "  choices: the discovered options below" if callable(discover_fn)
                    else "  choices: unknown for this engine - type the exact voice name it reports"
                )
            # Real, live-discovered options with language/accent/locale,
            # when this adapter's own available_voices() reports them -
            # never persisted, always asked fresh (see VoiceOption's own
            # docstring on why). Reuses the same discover_fn resolved
            # above for the effective-voice override lookup.
            options = tuple(discover_fn()) if callable(discover_fn) else ()
            if options:
                lines.append("  discovered options (language/accent/locale/gender, where reported):")
                for option in options:
                    facts = ", ".join(
                        f"{label}={value}"
                        for label, value in (
                            ("language", option.language), ("accent", option.accent),
                            ("locale", option.locale), ("gender", option.gender),
                        )
                        if value
                    ) or "no language/accent/locale/gender reported"
                    lines.append(f"    {option.name} ({option.provider}): {facts}")
            from src.services.identity.voice_configuration import voice_setting_for as _setting

            lines.append(f"  spoken language: {_setting(view.name, 'language', recall=memory.recall) or 'the language replies are written in'}")
            accent_setting = _setting(view.name, "accent", recall=memory.recall) or "the chosen voice's own"
            lines.append(f"  accent: {accent_setting}")
            lines.append(f"  pace: {_setting(view.name, 'pace', recall=memory.recall) or (current.pace if current and current.pace else 'provider default')}")
            lines.append(f"  choose with: voice {view.name} <name>")
            lines.append(f"  configure: voice {view.name} language <name|clear> | accent <name> | pace <word> | test <words>")
            lines.append(
                "  or ask in conversation, e.g. 'find me an Italian voice' - "
                "select_voice will choose autonomously among the options above"
            )
            return "\n".join(lines)

        ok = set_voice_preference(
            view.name,
            voice_name,
            available=available,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )
        if not ok:
            known = ", ".join(available) if available else "(unknown - this engine cannot list its own voices)"
            return f"no such voice '{voice_name}' for this engine. Choices: {known}"
        return f"{view.name}'s voice is now '{voice_name.strip()}'"

    if verb == "review":
        # Observation only. Nothing here changes anything, and there is no
        # command that would - applying a proposal is a person's decision.
        from src.improvement.governance import ImprovementBoard

        board = ImprovementBoard()
        observations = board.review(centre.manager)
        proposals = board.propose()

        lines = ["SELF-REVIEW", f"  {board.summarise()}", ""]
        for observation in observations:
            lines.append(f"  [{observation.severity.value}] {observation.subject}")
            lines += [f"    {line.strip()}" for line in [observation.finding]]
        if proposals:
            lines.append("")
            lines.append("  PROPOSALS (all require your approval; none is applied)")
            for proposal in proposals:
                lines.append(f"    {proposal.change.value}: {proposal.rationale}")
        return "\n".join(lines)

    if verb == "capabilities":
        if not rest:
            return render_capabilities(centre)
        result = run_capability_registry_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "provider":
        result = run_provider_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "connections":
        result = run_connections_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "services":
        result = run_services_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "sites":
        result = run_sites_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "ecosystem":
        result = run_ecosystem_command(centre, rest)
        _record_if_administrative_write(command, result, action_log_path)
        return result

    if verb == "talk":
        if not rest:
            return "say what you would like Alfred to do, for example: talk scan my inbox"
        return render_turn(
            exchange(channel, conversation, rest, action_log_path=DEFAULT_ACTION_LOG_PATH), columns
        )

    # Anything else is treated as something to say to Alfred. Talking is the
    # primary mode, so it should not need a keyword.
    #
    # Through the channel rather than straight to observe(). The call this
    # replaces went directly to the Manager, which meant the door existed and
    # nothing ever walked through it. Channel.ask calls the same observe() this
    # file used to call - so no orchestration moved - and the trail it produces
    # is the same one. What changed is that text and voice now share one path
    # instead of voice having a path nobody used.
    return render_turn(
        exchange(channel, conversation, command, action_log_path=DEFAULT_ACTION_LOG_PATH), columns
    )


def _reload_session(centre: "ControlCenter", command: str, *, channel=None, conversation=None):
    """Rebuild the whole session through the real ``build()`` every
    entry point already uses - the smallest safe activation this
    architecture supports (see ``interactive``'s own comment on why
    this is a whole-session rebuild, never a hot-swap of one provider
    object inside an already-running Manager).

    ``command`` is the raw ``"reload ..."`` line; anything after the
    verb is parsed as ``field=value`` overrides onto this session's own
    ``Settings`` - e.g. ``reload provider=openai`` switches *which
    backend* is active for this session, the one thing
    ``provider <category> select`` cannot do (that command only ever
    changes which *account* authorises an already-selected backend -
    see this file's own HELP text). Session-scoped only: nothing here
    is written to disk or to the environment, so it does not survive a
    real restart on its own - the matching ``MANAGER_*`` environment
    variable is what makes a backend choice the default on every future
    start, exactly as it already was before this command existed.

    Prints its own result and returns ``None`` on a bad override
    (nothing rebuilt, the prior session is untouched), or the new
    ``(centre, channel, conversation)`` on success.
    """
    _, _, rest = command.partition(" ")
    valid_fields = {f.name for f in fields(Settings)}
    overrides: dict[str, str] = {}
    for pair in rest.split():
        key, sep, value = pair.partition("=")
        if not sep or not key:
            print(f"ignoring '{pair}' - expected field=value, e.g. provider=openai")
            continue
        if key not in valid_fields:
            print(
                f"'{key}' is not a real Settings field - nothing was reloaded. "
                "Examples: provider, video_generation, speech, communication_provider, connection_provider."
            )
            return None
        overrides[key] = value

    settings = centre.runtime.settings
    if overrides:
        settings = replace(settings, **overrides)

    if overrides and set(overrides) <= reasoning_settings_fields():
        # Pre-Stage-9 Campaign 4: a reasoning-only change never recreates
        # Alfred - the same Manager, conversation, memory and identity keep
        # going; only the provider-bound pieces are rebound. Returns the very
        # Control Center, channel and conversation it was given.
        try:
            runtime = rebind_reasoning(
                centre.runtime, centre.manager, settings, accounts=load_credential_store(DEFAULT_ACCOUNTS_PATH),
            )
        except (ConfigurationError, ProviderConfigurationError, ValueError) as exc:
            print(f"reload failed, nothing changed: {exc}")
            return None
        centre.rebind_runtime(runtime)
        shown = ", ".join(f"{k}={v}" for k, v in overrides.items())
        print(
            f"Reloaded with this session's own override(s) ({shown}) - switched in place: the same "
            "conversation, memory and identity continue. Session-scoped only, not persisted: "
            f"'provider {LLM_CATEGORY} use <backend>' makes a backend the choice every start uses."
        )
        return centre, channel, conversation

    try:
        runtime, manager, channel, conversation = build(settings)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        print(f"reload failed, nothing changed: {exc}")
        return None

    new_centre = _governed_control_center(runtime, manager)

    message = "Reloaded"
    if overrides:
        shown = ", ".join(f"{k}={v}" for k, v in overrides.items())
        message += f" with this session's own override(s) ({shown})"
    message += " - runtime rebuilt from current settings and persisted accounts/connections."
    if overrides:
        message += (
            " Session-scoped only, not persisted: set the matching MANAGER_* "
            "environment variable to make this the default on every future start."
        )
    if type(manager.memory).__name__ == "EphemeralMemory":
        message += (
            "\nNote: memory for this run is ephemeral - anything remembered "
            "since this session started was just discarded by the rebuild."
        )
    print(message)
    return new_centre, channel, conversation


def interactive(
    centre: ControlCenter,
    columns: int,
    channel: Channel,
    conversation: Conversation,
) -> int:
    print(render_overview(centre.overview(), columns))
    print()
    print("type 'help' for commands, or just say what you want.")

    while True:
        try:
            command = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if command.lower() in ("quit", "exit"):
            return 0
        if command.lower() == "reload" or command.lower().startswith("reload "):
            # The smallest safe activation this project's architecture
            # actually supports: not hot-swapping one provider object
            # inside a live Manager (runtime.provider/media/communication/
            # speech are each bound once, directly into ToolSet/tool
            # construction - see ARCHITECTURE.md 52.27), but rebuilding
            # the whole session the identical way a restart already
            # would, without leaving this terminal. Calls the exact same
            # `build()` every entry point uses - never a second
            # construction path - so this reads the accounts/connections
            # exactly as they stand right now.
            #
            # Optional `field=value` overrides (e.g. `reload
            # provider=openai`) are this session's own answer to
            # "switch the active *backend*, not just the active account
            # for one backend" - a genuinely different axis from
            # `provider <category> select`, which only ever changes
            # which account authorises an already-selected backend.
            # Session-scoped only, by design: it never touches
            # environment variables or any file, so it never survives a
            # real restart on its own - reload without an override, or
            # a fresh restart, both read Settings from the environment
            # exactly as before. Setting the matching MANAGER_* variable
            # is what makes a backend choice the default on every future
            # start.
            print()
            reloaded = _reload_session(centre, command, channel=channel, conversation=conversation)
            if reloaded is not None:
                centre, channel, conversation = reloaded
            continue
        print()
        print(run_command(
            centre, command, columns, channel, conversation, action_log_path=DEFAULT_ACTION_LOG_PATH
        ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Agent Control Center.")
    parser.add_argument("command", nargs="*", help="status | history | talk <request>")
    parser.add_argument("--category", default=None, help="filter history by category")
    parser.add_argument("--width", type=int, default=None, help="render for this many columns")
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    # A screen, not a log. INFO-level records narrate every construction and
    # would bury the answer a person came to read, so the Control Center is
    # quiet by default - and stays exactly as loud as asked when someone sets
    # MANAGER_LOG_LEVEL deliberately. A presentation decision, which is the
    # only kind this file is allowed to make.
    configure_logging(
        settings.log_level if "MANAGER_LOG_LEVEL" in os.environ else "WARNING"
    )

    try:
        check_configuration(settings)
        runtime, manager, channel, conversation = build(settings)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        # Stop before showing anything. A Control Center that opened on a
        # misconfigured system would describe a program that is not running.
        print(f"configuration error: {exc}")
        return 2

    centre = _governed_control_center(runtime, manager)
    columns = args.width or width()

    if not args.command:
        return interactive(centre, columns, channel, conversation)

    command = " ".join(args.command)
    if args.category and command.strip().lower().startswith("history"):
        command = f"history {args.category}"
    print(run_command(
        centre, command, columns, channel, conversation, action_log_path=DEFAULT_ACTION_LOG_PATH
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
