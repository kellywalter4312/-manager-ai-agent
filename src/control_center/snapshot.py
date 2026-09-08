"""One honest picture of the running system, in a shape an interface can read.

The Control Center already reads the architecture and renders it to a terminal.
An interface that is not a terminal needs the same reading and a different
rendering, so this module separates the two: it produces **data**, and knows
nothing about screens, colours, layout or HTML.

    ControlCenter (reads the system)  ->  snapshot (data)  ->  any interface

**It invents nothing.** Every value here traces to a read that already existed -
a CognitiveProfile, a MemoryView, a ToolSet, a VoiceProfile, a Conversation.
Where the system cannot answer, the snapshot says so with ``available: False``
and a reason, rather than emitting a plausible number. A screen that received a
fabricated value could not tell it from a real one, and neither could the person
reading the screen.

**It is not a second Control Center.** Nothing here routes, assesses, admits or
decides; it asks the observer that already does those things. Two readers of the
same system would drift, and the screen is what a person would believe.

**No agent is named.** Everything iterates the registry, so a specialist
registered later appears with its own brain, memory, capabilities, tools and
voice without a line of this changing - the same rule the terminal renderer
already lives under.

Why a plain dict rather than a class per surface: this is a boundary format,
read once by something in another language. A dataclass per view would be a
second copy of the view layer that has to be kept in step with it, and the
thing on the other side sees JSON either way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from src.control_center.manifest import upgrade_manifest
from src.control_center.observer import ControlCenter
from src.services.artifacts.delivery import available_delivery_methods
from src.services.identity.personality import personality_for
from src.services.identity.voice import activation_for, voice_for
from src.services.identity.voice_preference import effective_voice_for

# What this format is. An interface reads it to know whether it understands
# what it was handed, rather than guessing from the shape.
SNAPSHOT_VERSION = 1


def _moment(value: datetime | None) -> str | None:
    """A timestamp an interface can read, or None when there was none.

    None is a real answer - "this has never happened" - and rendering it as a
    date would be the interface inventing an event.
    """
    return value.isoformat() if isinstance(value, datetime) else None


def _brain(view: Any) -> dict[str, Any]:
    """One agent's reasoning, exactly as assess_cognition reported it.

    ``mode`` is FULL BRAIN only when a call has actually succeeded, and
    ``verified`` carries that evidence separately so an interface can show
    *why* a Half Brain is a Half Brain instead of implying a fault.
    """
    return {
        "mode": view.mode,
        "verified": view.verified,
        "reason": view.reason,
        "provider": view.provider,
        "router_provider": view.router_provider,
        "degraded": view.degraded,
        "indicator": view.indicator,
        "available_capabilities": list(view.available_capabilities),
        "missing_capabilities": list(view.missing_capabilities),
    }


def _memory(view: Any) -> dict[str, Any]:
    """What one agent has retained. Counts and samples, never a dump.

    ``durable`` is the fact a person actually wants: whether this survives the
    process. Content is included because it is the agent's own store and the
    Memory interface is what a person inspects it through - but bounded by
    whatever the observer already sampled, not re-read here.
    """
    return {
        "owner": view.owner,
        "backend": view.backend,
        "durable": view.durable,
        "sampled": view.sampled,
        "recent": [
            {
                "when": _moment(record.when),
                "kind": record.kind,
                "content": record.content,
                "tags": list(record.tags),
            }
            for record in view.recent
        ],
    }


def _voice(agent: Any, *, recall: Any = None, discover_voices: Any = None) -> dict[str, Any]:
    """An agent's voice identity, and the truth about whether it can be heard.

    Two separate facts, deliberately. A profile existing says *what this agent
    would sound like*; it says nothing about whether any engine can produce it.
    Collapsing them is how an interface ends up showing a speaker icon for a
    system that has never made a sound.

    Agent-autonomous voice selection checkpoint: ``recall``, when given,
    resolves the real *effective* voice (an explicit override or the
    agent's own autonomous choice, applied over the bare declared
    default) - exactly what the Control Center's own ``voice`` command
    and the real speaking channel (``VoiceChannel._voice_of``) already
    resolve, so this snapshot cannot show a stale ``preferred_voice`` a
    person just changed. ``None`` (no recall reaches this call - a
    snapshot taken without a live Memory) degrades to the bare declared
    default, never a crash and never a fabricated preference.
    """
    profile = (
        effective_voice_for(agent, recall=recall, discover_voices=discover_voices)
        if recall is not None else voice_for(agent)
    )
    if profile is None:
        return {"declared": False}
    return {
        "declared": True,
        "key": profile.key,
        "traits": list(profile.traits),
        "describes": profile.describe(),
        # The same facts, separated by kind - nothing added, nothing
        # removed, and both keys above untouched. ``traits`` is
        # (accent, pace, pitch, warmth, preferred_voice) flattened into
        # one list, and the last of those is a different kind of fact
        # from the four before it: "Microsoft David Desktop" is a name
        # this machine's own speech engine gave one of its installed
        # voices, not a description of how this agent sounds. An
        # interface that prints the list whole shows a person an engine
        # voice id where it promised them an identity, so the two are
        # reported apart and each surface chooses which it is showing.
        "character": [
            trait
            for trait in (profile.accent, profile.pace, profile.pitch, profile.warmth)
            if trait
        ],
        "preferred_voice": profile.preferred_voice or None,
    }


def _activation(agent: Any) -> dict[str, Any]:
    """How this agent is addressed by voice - a separate fact from _voice
    above, and read the same duck-typed way. An interface reads ``phrases``
    to know what a heard utterance must match, rather than a phrase list
    ever being written into the interface itself: the agent's own document
    stays the one place a phrase is added (see
    src/services/identity/identity.py's ``## Voice Activation`` section).
    """
    activation = activation_for(agent)
    if activation is None:
        return {"declared": False}
    return {"declared": True, "key": activation.key, "phrases": list(activation.phrases)}


def _personality(agent: Any) -> dict[str, Any]:
    """How this agent tends to communicate - a separate fact from _voice
    and _activation above, read the same duck-typed way (Stage 11.1). An
    observation only: nothing here decides how a reply is phrased, and
    this module still owns no business logic - it reports what the
    agent's own identity already declares.
    """
    personality = personality_for(agent)
    if personality is None:
        return {"declared": False}
    return {
        "declared": True,
        "key": personality.key,
        "traits": list(personality.traits),
        "describes": personality.describe(),
    }


def _tools(view_tools: Any) -> list[dict[str, Any]]:
    """What this agent was granted. Empty is the common and honest answer."""
    return [
        {"name": tool.name, "description": tool.description, "available": tool.available}
        for tool in view_tools
    ]


def _agent(view: Any, agent: Any, *, recall: Any = None, discover_voices: Any = None) -> dict[str, Any]:
    """Everything an Agent Workspace needs about one agent.

    The workspace is an *interface into* this agent, so this gathers what the
    agent already has rather than producing anything new. There is no workspace
    brain and no workspace memory here, because there is no such thing: the
    brain and memory below are the ones the agent uses everywhere else.
    """
    interface = view.interface
    return {
        "name": view.name,
        "role": view.role,
        "is_manager": view.is_manager,
        "brain": _brain(view.brain),
        "memory": _memory(view.memory),
        "capabilities": [
            {
                "name": capability.name,
                "tool": capability.tool,
                "tool_available": capability.tool_available,
            }
            for capability in view.capabilities
        ],
        "tools": _tools(view.tools),
        "voice": _voice(agent, recall=recall, discover_voices=discover_voices),
        "activation": _activation(agent),
        "personality": _personality(agent),
        "interface": {
            "implemented": sorted(f.value for f in interface.implemented),
            "planned": sorted(f.value for f in interface.planned),
            "summary": interface.summary,
        }
        if interface is not None
        else None,
        # Stage 17: genuinely populated now, closing the seam
        # ARCHITECTURE.md recorded as future work. "activity" carries the
        # same value as presence.state - three of the documented activity
        # vocabulary's own words (idle/listening/speaking) are now
        # honestly derivable; thinking/working/delegating/waiting/error
        # remain undeclared until something can honestly derive them too
        # (see PresenceState's own docstring for why they are not
        # invented here).
        "activity": view.presence.state.value if view.presence is not None else None,
        "avatar": view.presence.to_dict() if view.presence is not None else None,
        # Final acceptance sweep (2026-09-08): this agent's own chosen or
        # deterministic-default EnvironmentProfile - previously resolvable
        # only through the CLI/HTTP "environment" command and completely
        # absent from every snapshot, so no UI surface could show or
        # change it. Exposed here the identical way "avatar" already is.
        "environment": view.environment.to_dict() if view.environment is not None else None,
    }


def _conversation(conversation: Any) -> dict[str, Any]:
    """The room, if one was supplied. Participants are identities, as always."""
    if conversation is None:
        return {"available": False, "reason": "no conversation is open"}
    return {
        "available": True,
        "id": conversation.id,
        "started_at": _moment(conversation.started_at),
        "participants": [
            {"name": p.name, "kind": p.kind.value} for p in conversation.participants
        ],
        "utterances": [
            {
                "speaker": u.speaker,
                "text": u.text,
                "when": _moment(u.when),
                "addressed": u.addressed,
            }
            for u in conversation.utterances
        ],
    }


def _speech(channel: Any) -> dict[str, Any]:
    """Whether anything can actually be heard or said.

    Read from the composed adapter rather than assumed. Three states, not a
    boolean, so an unproven engine reads as unproven rather than as missing.
    """
    speech = getattr(channel, "speech", None)
    if speech is None:
        return {"available": False, "reason": "this channel carries no speech engine"}
    availability = speech.availability
    return {
        "engine": speech.name,
        "state": availability.state.value,
        "usable": availability.is_usable,
        "describes": availability.describe(),
    }


# A real, fixed preference among an artifact's own renditions when it
# has more than one - a finished export over a plain-text fallback, a
# video over a still image - never simply "whichever key a dict
# happened to store first". Presentation's own decision (which one is
# shown/linked primarily), kept distinct from delivery.py's own
# question ("is anything deliverable at all").
_RENDITION_PREFERENCE = ("pdf", "mp4", "png", "jpg", "wav", "txt")


def _preferred_rendition_format(renditions: Mapping[str, str]) -> "str | None":
    for fmt in _RENDITION_PREFERENCE:
        if fmt in renditions:
            return fmt
    # A real, future rendition format this preference list does not yet
    # rank - still shown, never dropped merely because it is unranked.
    return next(iter(renditions), None)


def _artifacts(store: Any) -> list[dict[str, Any]]:
    """Every real, persisted artifact's latest version, newest first -
    read from the identical, authoritative ArtifactStore
    ArtifactProductionTool itself writes through (see
    ``src/composition.py``'s own ``Runtime.artifacts`` docstring), never
    a second, UI-only copy. ``owner_agent`` lets any interface (one
    agent's own private line, or a future specialist's) filter to its
    own without this module drawing that line itself - the identical "report what
    exists, decide nothing about presentation" boundary every other
    function in this module already holds to.
    """
    return [
        {
            "artifact_id": artifact.artifact_id,
            "type": artifact.type,
            "owner_agent": artifact.owner_agent,
            "version": artifact.latest.version,
            "status": artifact.latest.status,
            "title": artifact.latest.title,
            "created_at": _moment(artifact.latest.created_at),
            "renditions": dict(artifact.latest.renditions),
            "primary_rendition": _preferred_rendition_format(artifact.latest.renditions),
            # The real composition/derivation seam
            # (store.py::ArtifactVersion.related_artifact_ids) - which
            # other real, tracked artifacts this version was produced
            # from (a clip's own source video, say). Empty when nothing
            # tracked was used, honestly.
            "related_artifact_ids": list(artifact.latest.related_artifact_ids),
            # The fourth, distinct concern src/services/artifacts/delivery.py's
            # own docstring names - computed fresh here, never persisted
            # alongside the version it describes, so an interface never
            # reads a delivery claim that has gone stale.
            "delivery": list(available_delivery_methods(artifact.latest)),
        }
        for artifact in sorted(store.all(), key=lambda a: a.latest.created_at, reverse=True)
    ]


def _personal_context(topics: Any) -> list[dict[str, Any]]:
    """Generalized user knowledge/personal-context checkpoint: the real,
    current, per-topic state (see ``ControlCenter.personal_context``'s
    own docstring) - one entry per topic, always the latest statement,
    never a raw history dump and never a decorative example."""
    return [
        {
            "record_id": item.record_id,
            "topic": item.topic,
            "content": item.content,
            "category": item.category,
            "status": item.status,
            "noted_by": item.noted_by,
        }
        for item in topics
    ]


def _calendar_items(items: Any) -> list[dict[str, Any]]:
    """Generalized Personal Organization/Calendar checkpoint: the real,
    current calendar state (see ``ControlCenter.calendar_items``'s own
    docstring) - one entry per real-world item, always its latest write,
    never a raw history dump and never a decorative example."""
    return [
        {
            "record_id": item.record_id,
            "item_key": item.item_key,
            "topic_key": item.topic_key,
            "title": item.title,
            "when": item.when,
            "status": item.status,
            "noted_by": item.noted_by,
        }
        for item in items
    ]


def _experiences(items: Any) -> dict[str, Any]:
    """General Time/Event Awareness + Proactive Visual Experience
    checkpoint: the real, current selection/delivery state (see
    ``ControlCenter.experiences``'s own docstring) - split into
    ``pending`` (selected, not yet reached a live frontend - what a
    served page actually presents) and ``history`` (already delivered),
    never a raw undifferentiated dump and never a decorative example.
    """
    pending = [
        {
            "record_id": item.record_id,
            "event_type": item.event_type,
            "agent_key": item.agent_key,
            "reaction_id": item.reaction_id,
            "avatar_action": item.avatar_action,
            "environment_action": item.environment_action,
            "overlay": item.overlay,
            "intensity": item.intensity,
            "duration_ms": item.duration_ms,
        }
        for item in items if not item.delivered
    ]
    history = [
        {
            "record_id": item.record_id,
            "event_type": item.event_type,
            "agent_key": item.agent_key,
            "reaction_id": item.reaction_id,
        }
        for item in items if item.delivered
    ]
    return {"pending": pending, "history": history}


def snapshot(
    centre: ControlCenter,
    *,
    conversation: Any = None,
    channel: Any = None,
) -> dict[str, Any]:
    """One reading of the whole system, for an interface to render.

    ``conversation`` and ``channel`` are optional because not every caller has
    one - a status-only export is a legitimate use - and a snapshot that
    invented an empty room would be claiming a session that never started.
    """
    overview = centre.overview()
    system = overview.system
    beat = system.heartbeat
    registry = {agent.name: agent for agent in centre.manager.registry.all_agents()}

    # Agent-autonomous voice selection checkpoint: the identical
    # per-agent Memory lookup VoiceChannel._voice_of already performs at
    # speak time - the Manager's own store when the agent IS the
    # Manager (own_skills exposes no .memory directly), that agent's own
    # store otherwise - so this snapshot's voice field can resolve the
    # real effective voice rather than only the bare declared default.
    manager_name = centre.manager.own_skills.name if centre.manager.own_skills else None

    def _recall_for(name: str) -> Any:
        memory = centre.manager.memory if name == manager_name else getattr(registry.get(name), "memory", None)
        return memory.recall if memory is not None else None

    # The same real, live discovery call the voice command and the
    # speaking channel already use - never a second mechanism - so an
    # explicit override typed by real display name resolves to the
    # correct provider field here too.
    discover_voices = getattr(centre.runtime.speech, "available_voices", None)

    agents = [
        _agent(view, registry.get(view.name), recall=_recall_for(view.name), discover_voices=discover_voices)
        for view in (overview.manager,) + overview.specialists
    ]

    history = centre.history()

    return {
        "version": SNAPSHOT_VERSION,
        "taken_at": _moment(datetime.now().astimezone()),
        "agents": agents,
        # Stage 17.2: the *choices* an appearance picker needs, read
        # once from the one provider every agent's own presence above
        # already resolved through - never per agent, because this
        # catalog is the provider's own, not any one agent's fact.
        "avatar_catalog": [p.to_dict() for p in centre.runtime.avatar_provider.catalog()],
        # Final acceptance sweep (2026-09-08): the identical choices
        # catalog for the environment picker, mirroring avatar_catalog
        # above for the same reason - the provider's own choices, not
        # any one agent's fact.
        "environment_catalog": [p.to_dict() for p in centre.runtime.environment_provider.catalog()],
        # Multimodal workspace/artifact checkpoint: every real, persisted
        # artifact - the one authoritative record (see this module's own
        # _artifacts docstring), never a fabricated or UI-only listing.
        "artifacts": _artifacts(centre.runtime.artifacts),
        # Generalized user knowledge/personal-context checkpoint: the
        # real, current per-topic state - the same data the "personal"
        # terminal command renders, never a UI-only duplicate.
        "personal_context": _personal_context(centre.personal_context()),
        # Generalized Personal Organization/Calendar checkpoint: the same
        # real, current state the "calendar" terminal command renders,
        # never a UI-only duplicate.
        "calendar_items": _calendar_items(centre.calendar_items()),
        # General Time/Event Awareness + Proactive Visual Experience
        # checkpoint: the same real, current selection/delivery state
        # the "experience" terminal command renders - never a UI-only
        # duplicate, and never Control-Center-only: this is what a
        # served page actually presents (see scripts/serve_ui.py's own
        # POST /experience route for how a real presentation is
        # acknowledged).
        "experience": _experiences(centre.experiences()),
        "conversation": _conversation(conversation),
        "speech": _speech(channel),
        "system": {
            # The assembled product's own version - a different concept
            # from this payload's own "version" key above (SNAPSHOT_VERSION,
            # the shape of this export) and from any one store's own
            # SCHEMA_VERSION (src/services/memory/persistent.py,
            # src/heartbeat/state.py). See src/core/versioning.py.
            "system_version": system.system_version,
            "provider": system.provider,
            "memory_backend": system.memory_backend,
            "timezone": system.timezone,
            "today": str(system.today),
            "gmail_scope": system.gmail_scope,
            "can_send": system.can_send,
            "mailbox": system.mailbox,
            "tools": _tools(system.tools),
            "warnings": list(system.warnings),
            "heartbeat": {
                "enabled": beat.enabled,
                "interval_minutes": beat.interval_minutes,
                "last_wake": _moment(beat.last_wake),
                "messages_seen": beat.messages_seen,
                "opportunities_seen": beat.opportunities_seen,
                "reports": beat.reports,
            },
        },
        "history": {
            "categories": list(history.categories),
            "total_available": history.total_available,
            "events": [
                {
                    "when": _moment(event.when),
                    "category": event.category,
                    "actor": event.actor,
                    "summary": event.summary,
                    "detail": event.detail,
                }
                for event in history.events
            ],
        },
        # What version of each real compatibility contract is installed -
        # not agent identity, not Brain or Memory state, not a migration
        # engine. See src/control_center/manifest.py and ARCHITECTURE.md.
        "upgrade_manifest": upgrade_manifest(snapshot_version=SNAPSHOT_VERSION).to_dict(),
    }
