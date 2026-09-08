"""Reads the assembled system and reports what is true of it.

This module is the whole of the Control Center's contact with the
architecture, and everything it does is a read. It routes nothing, matches no
capability, assesses no brain, admits no memory, registers no specialist and
schedules no wake. When it needs one of those answers it asks the component
that owns it:

    brain mode          assess_cognition / assess_manager
    specialists         runtime.specialists  (the registry, not a list)
    capabilities        agent.spec.capabilities
    tools               the agent's own ToolSet
    memory              the agent's own store
    heartbeat           StateStore and the settings

That list is the point. A Control Center that computed any of those itself
would become a second implementation of the system, drifting quietly out of
step with the first, and the screen would eventually describe a program that
no longer exists.

**Specialists are discovered, never named.** Everything here iterates the
registry, so a specialist added later appears with its own brain, memory,
capabilities and tools without a line changing. There is no list of agents in
this file, and adding one would be the beginning of the redesign-per-specialist
problem the architecture was built to avoid.

**Nothing is invented.** Where the system cannot answer, the view says so.
An absent tool is absent, an unproven model is Half Brain, an unrun heartbeat
has no last wake, and a next wake is None because this project schedules
nothing and predicting one would describe a mechanism it does not have.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from src.agents.base_agent import BaseAgent
from src.control_center.interfaces import describe_interface
from src.core.versioning import SYSTEM_VERSION
from src.events.narrator import compose
from src.control_center.views import (
    AgentView,
    BrainView,
    CalendarItemView,
    CapabilityView,
    ExperienceView,
    HeartbeatView,
    HistoryEvent,
    HistoryView,
    MemoryRecordView,
    MemoryView,
    OverviewView,
    PersonalContextTopicView,
    SystemView,
    ToolView,
    UnresolvedFactView,
)
from src.manager.capabilities import GENERAL_CAPABILITIES, PERSON_BIRTHDAY_FACT_TYPE
from src.manager.cognition import assess_cognition, assess_manager
from src.manager.manager import ManagerAgent
from src.manager.proactive_experience import evaluate_and_persist_experiences
from src.services.memory.calendar_items import all_current_calendar_items
from src.services.memory.day import Calendar
from src.services.memory.experience_history import all_experiences
from src.services.memory.personal_context import all_current_personal_context
from src.services.identity.presence import environment_for, presence_for
from src.utils.logging import get_logger

logger = get_logger(__name__)

# How many records to sample when showing what an agent has retained. Bounded
# because this is a screen, not an export: a person reads the most recent few,
# and an unbounded fetch would turn an observability view into a dump.
MEMORY_SAMPLE = 8

# How many history events to consider before filtering. Same reasoning.
HISTORY_LIMIT = 200


def _as_datetime(value: Any) -> datetime | None:
    """A stored timestamp as a datetime, or None when it cannot be read.

    Heartbeat state is JSON, so a time arrives as a string. An unreadable one
    is reported as unknown rather than guessed - a screen saying "never" when
    a wake actually happened would be a small lie, but it is still a lie, and
    None renders as "unknown" instead.
    """
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _brain_view(agent_name: str, profile: Any) -> BrainView:
    """Turn a CognitiveProfile into something a screen can show.

    A translation, not a decision. Every field comes from the profile that
    assess_cognition already produced - this function has no opinion about
    what mode anything is in, which is exactly why Full Brain cannot be
    accidentally granted by the Control Center.
    """
    return BrainView(
        agent=agent_name,
        mode=profile.mode.label,
        verified=profile.verified,
        reason=profile.reason,
        provider=profile.brain_provider,
        router_provider=profile.router_provider,
        degraded=profile.is_degraded,
        available_capabilities=tuple(profile.capabilities),
        missing_capabilities=tuple(profile.missing),
    )


def _memory_view(agent_name: str, memory: Any, owner: str) -> MemoryView:
    """Sample what one agent has retained.

    Reads through the Memory interface every agent already implements, so an
    ephemeral store and a persistent one are shown the same way. The backend
    name is reported because "this disappears when the process exits" is
    genuinely useful to a person; the file layout is not, and is left out.
    """
    backend = type(memory).__name__
    durable = backend == "PersistentMemory"

    try:
        records = memory.recall(limit=MEMORY_SAMPLE)
    except Exception as exc:  # noqa: BLE001 - an observability read must not raise
        logger.warning("could not sample %s memory: %s", agent_name, type(exc).__name__)
        records = []

    return MemoryView(
        agent=agent_name,
        owner=owner,
        backend=backend,
        durable=durable,
        recent=tuple(
            MemoryRecordView(
                when=record.created_at,
                kind=str(getattr(record, "kind", "") or "note"),
                content=record.content,
                tags=tuple(sorted(getattr(record, "tags", ()) or ())),
            )
            for record in records
        ),
        sampled=len(records),
    )


def _tool_views(tools: Any) -> tuple[ToolView, ...]:
    """Every tool an agent actually holds.

    An empty ToolSet produces an empty tuple, and a screen showing nothing is
    the correct rendering of an agent that has been granted nothing. The
    Control Center never lists a tool the agent does not have.
    """
    views = []
    for spec in tools.specs():
        views.append(ToolView(name=spec.name, available=True, description=spec.description))
    return tuple(views)


def _capability_views(agent: BaseAgent, tools: Any) -> tuple[CapabilityView, ...]:
    """What an agent takes responsibility for, and whether it could act.

    The pairing of a capability with the tool it needs is read from the
    declared general capabilities rather than guessed: a capability whose tool
    is missing is reported as offered-but-not-performable, which is the honest
    state and the one the architecture is careful to preserve.
    """
    needs = {}
    for capability in GENERAL_CAPABILITIES:
        for word in capability.capabilities if hasattr(capability, "capabilities") else ():
            needs[word] = capability.tool
    for capability in GENERAL_CAPABILITIES:
        for word in capability.words:
            needs[word] = capability.tool

    views = []
    for word in sorted(agent.spec.capabilities):
        tool = needs.get(word)
        views.append(
            CapabilityView(
                name=word,
                tool=tool,
                tool_available=tool is None or tools.permits(tool),
            )
        )
    return tuple(views)


class ControlCenter:
    """A read-only window onto one assembled system.

    Holds the runtime and the Manager it was given and never builds either:
    composition stays in the composition root, and a presentation layer that
    started assembling agents would have become the owner of the thing it is
    supposed to describe.
    """

    def __init__(self, runtime: Any, manager: ManagerAgent) -> None:
        self._runtime = runtime
        self._manager = manager

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    @property
    def manager(self) -> ManagerAgent:
        return self._manager

    @property
    def runtime(self) -> Any:
        """The composed Runtime this centre observes - exposed so a
        caller that needs a specific collaborator (Stage 17: an
        entry point resolving ``runtime.avatar_provider`` for the
        ``avatar`` command/route) reads the one the composition root
        actually built, rather than assembling its own."""
        return self._runtime

    def manager_view(self) -> AgentView:
        """The Manager, with his own brain, memory, capabilities and tools."""
        identity = self._runtime.identity
        skills = self._manager.own_skills
        tools = skills.tools if skills is not None else _EMPTY_TOOLS

        return AgentView(
            name=identity.name,
            role=identity.role or "orchestrator",
            kind="manager",
            purpose=identity.purpose,
            area=identity.area_of_responsibility,
            brain=_brain_view(identity.name, assess_manager(self._manager)),
            memory=_memory_view(identity.name, self._manager.memory, identity.name),
            capabilities=_capability_views(skills, tools) if skills is not None else (),
            tools=_tool_views(tools),
            interface=describe_interface(skills) if skills is not None else None,
            # Stage 17: read from the Manager's own canonical Memory -
            # own_skills itself exposes no .memory (see presence.py's
            # own module docstring for why every presence function
            # takes a bound recall rather than an agent object).
            presence=presence_for(
                identity.name,
                provider=self._runtime.avatar_provider,
                recall=self._manager.memory.recall if self._manager.memory is not None else None,
                # Stage 17.2: a real, live SPEAKING signal (written only
                # by VoiceChannel.deliver() around a real speak() call)
                # wins over the IDLE default the moment it is true.
                live=self._runtime.presence_registry,
            ),
            environment=environment_for(
                identity.name,
                provider=self._runtime.environment_provider,
                recall=self._manager.memory.recall if self._manager.memory is not None else None,
            ),
        )

    def specialist_views(self) -> tuple[AgentView, ...]:
        """Every registered specialist, discovered rather than listed.

        Iterates the registry the composition root filled. A specialist added
        tomorrow appears here with no change to this method, which is the
        property that lets the Control Center grow from one specialist to many
        without a redesign.
        """
        manager_name = self._manager.own_skills.name if self._manager.own_skills else None
        views = []

        for agent in self._manager.registry.all_agents():
            if agent.name == manager_name:
                continue
            views.append(self._specialist_view(agent))
        return tuple(views)

    def _specialist_view(self, agent: BaseAgent) -> AgentView:
        wiring = self._wiring_for(agent)
        brain = getattr(agent, "brain", None)
        memory = getattr(agent, "memory", None)
        tools = getattr(agent, "tools", _EMPTY_TOOLS)

        identity = getattr(agent, "identity", None)
        return AgentView(
            name=agent.name,
            role=(getattr(identity, "role", "") or agent.spec.description),
            kind="specialist",
            purpose=getattr(identity, "purpose", "") or "",
            area=(wiring.brain_domain if wiring is not None else ""),
            brain=_brain_view(agent.name, assess_cognition(brain)),
            memory=(
                _memory_view(agent.name, memory, agent.name)
                if memory is not None
                else MemoryView(agent=agent.name, owner=agent.name, backend="none", durable=False)
            ),
            capabilities=_capability_views(agent, tools),
            tools=_tool_views(tools),
            # Asked of the agent, not decided here. A specialist that declares
            # a workspace gets one offered; one that declares nothing gets
            # conversation, and neither needs this file to know it exists.
            interface=describe_interface(agent),
            # Stage 17: a specialist's own Memory is directly on the
            # agent (unlike the Manager's own_skills), so this reaches
            # it the same way _memory_view above already does.
            presence=presence_for(
                agent.name,
                provider=self._runtime.avatar_provider,
                recall=memory.recall if memory is not None else None,
                live=self._runtime.presence_registry,
            ),
            environment=environment_for(
                agent.name,
                provider=self._runtime.environment_provider,
                recall=memory.recall if memory is not None else None,
            ),
        )

    def _wiring_for(self, agent: BaseAgent) -> Any:
        """The registry entry behind an agent, if it came from one.

        Looked up by the name the agent registered under, so it works for any
        specialist the registry knows and returns None for one registered by
        hand - a stand-in in a demo, for instance - rather than guessing.
        """
        for entry in getattr(self._runtime, "specialists", {}).values():
            if entry.wiring.name == agent.name:
                return entry.wiring
        return None

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def system_view(self) -> SystemView:
        settings = self._runtime.settings
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE

        specialists = self.specialist_views()
        can_send = any(
            getattr(self._manager.registry.get(view.name), "can_send", False)
            for view in specialists
        )

        warnings = []
        manager_brain = assess_manager(self._manager)
        if manager_brain.is_degraded:
            warnings.append(f"model configured but unusable: {manager_brain.reason}")
        if not any(view.tools for view in (self.manager_view(),) + specialists):
            warnings.append("no tools are installed; capabilities needing one cannot run")

        return SystemView(
            system_version=SYSTEM_VERSION,
            heartbeat=self.heartbeat_view(),
            mailbox=settings.mailbox,
            gmail_scope=GMAIL_READONLY_SCOPE,
            can_send=can_send,
            timezone=settings.timezone,
            today=str(self._runtime.calendar.today()),
            provider=self._runtime.provider.name,
            memory_backend=settings.memory,
            tools=_tool_views(
                self._manager.own_skills.tools
                if self._manager.own_skills is not None
                else _EMPTY_TOOLS
            ),
            warnings=tuple(warnings),
        )

    def heartbeat_view(self) -> HeartbeatView:
        """The autonomous loop as it currently stands, observed only.

        No timer is started, no wake is triggered and no schedule is computed.
        ``next_wake`` stays None because this project registers its entry point
        with an external scheduler and has no internal clock to predict from -
        naming a time would describe a mechanism that does not exist.
        """
        settings = self._runtime.settings
        from src.heartbeat.state import StateStore

        last_wake = None
        messages = opportunities = 0
        try:
            state = StateStore(settings.state_path).load()
            # Stored as an ISO string, because that is what survives a JSON
            # round trip. Coerced here rather than in the view so every screen
            # receives a datetime and none has to know how state is persisted.
            last_wake = _as_datetime(state.last_run_at)
            messages = len(state.messages)
            opportunities = len(state.opportunities)
        except Exception as exc:  # noqa: BLE001 - a missing state file is normal
            logger.debug("no readable heartbeat state: %s", type(exc).__name__)

        reports = sorted(Path(settings.reports_dir).glob("*.md")) if Path(
            settings.reports_dir
        ).exists() else []

        return HeartbeatView(
            enabled=settings.heartbeat_is_enabled,
            interval_minutes=settings.heartbeat_interval,
            state_path=settings.state_path,
            last_wake=last_wake,
            messages_seen=messages,
            opportunities_seen=opportunities,
            reports=len(reports),
            latest_report=reports[-1].name if reports else None,
        )

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def overview(self) -> OverviewView:
        """The first screen. Summary fields only are needed to render it."""
        return OverviewView(
            manager=self.manager_view(),
            specialists=self.specialist_views(),
            system=self.system_view(),
            generated_at=datetime.now(timezone.utc),
        )

    def history(
        self,
        category: str | None = None,
        actor: str | None = None,
        limit: int = 20,
    ) -> HistoryView:
        """What happened, filtered before it reaches a screen.

        Built from what the system genuinely recorded: the events the Manager
        raised, the memories each agent retained, and the reports the heartbeat
        wrote. Nothing is synthesised to fill the screen - a system that has
        done little shows little, which is the truthful rendering.

        **This reads events; it never raises one.** The Manager owns the log
        and is the only thing that appends to it, because only the Manager
        knows it delegated. A Control Center that raised its own events would
        be a second account of what happened, and the first time the two
        disagreed a person would believe the screen. Wording comes from the
        narrator for the same reason: a screen that wrote its own sentences
        would become the system's vocabulary.
        """
        events: list[HistoryEvent] = []

        for event in self._manager.events.all():
            if not event.significance.worth_recording:
                # ROUTINE is dropped here rather than filtered later. The event
                # carries how far it deserves to travel, so a future source of
                # events cannot become chatty by forgetting a check.
                continue
            events.append(
                HistoryEvent(
                    when=event.when,
                    category="event",
                    actor=event.actor,
                    summary=compose(event),
                    detail=event.describe(),
                )
            )

        for view in (self.manager_view(),) + self.specialist_views():
            for record in view.memory.recent:
                events.append(
                    HistoryEvent(
                        when=record.when,
                        category="memory",
                        actor=view.name,
                        summary=record.content,
                        detail=f"kind={record.kind}",
                    )
                )

        settings = self._runtime.settings
        reports_dir = Path(settings.reports_dir)
        if reports_dir.exists():
            for report in sorted(reports_dir.glob("*.md"))[-HISTORY_LIMIT:]:
                events.append(
                    HistoryEvent(
                        when=datetime.fromtimestamp(report.stat().st_mtime, timezone.utc),
                        category="report",
                        actor="heartbeat",
                        summary=f"opportunity report written: {report.name}",
                        detail=str(report),
                    )
                )

        categories = tuple(sorted({event.category for event in events}))
        actors = tuple(sorted({event.actor for event in events}))
        total = len(events)

        filtered_by = {}
        if category:
            events = [e for e in events if e.category == category]
            filtered_by["category"] = category
        if actor:
            events = [e for e in events if e.actor.lower() == actor.lower()]
            filtered_by["actor"] = actor

        events.sort(key=lambda e: e.when or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        return HistoryView(
            events=tuple(events[:limit]),
            categories=categories,
            actors=actors,
            filtered_by=filtered_by,
            total_available=total,
        )

    def unresolved_facts(self) -> tuple[UnresolvedFactView, ...]:
        """Genuine, currently-unresolved uncertainty in Alfred's own
        Memory - Stage 12 closure's own example: "mom's birthday is
        either March 24 or May 24, unconfirmed".

        Read-only, like every other view here: this presents what is
        already stored, exactly as stored - it does not resolve
        anything, and there is no field here through which it could.
        Confirming one happens by restating the fact through the
        ordinary conversational path (``personal_recurring_events`` in
        ``src/manager/capabilities.py``), and once that governed write
        lands, the record this method reads no longer has
        ``confirmed=False`` - so it stops appearing here on its own,
        with nothing here needing to know that happened.
        """
        memory = self._manager.memory
        if memory is None:
            return ()

        views: list[UnresolvedFactView] = []
        for record in memory.recall(kind="user_context", tags={PERSON_BIRTHDAY_FACT_TYPE}, limit=200):
            context = record.context or {}
            if context.get("confirmed", True):
                continue
            alternatives = tuple(
                f"{alt.get('month')}/{alt.get('day')}" for alt in (context.get("alternatives") or ())
            )
            views.append(
                UnresolvedFactView(
                    record_id=record.id,
                    person=str(context.get("person", "")),
                    fact_type=str(context.get("fact_type", "")),
                    alternatives=alternatives,
                )
            )
        return tuple(views)

    def personal_context(self) -> tuple[PersonalContextTopicView, ...]:
        """The real, current, per-topic state of everything the user has
        taught Alfred about themselves - one entry per topic, always the
        latest statement (personal_context.py's own supersession
        resolution), never a raw history dump and never a decorative
        example. Read-only: teaching happens through the ordinary
        conversational path (the ``personal_context`` capability in
        ``src/manager/capabilities.py``); this only presents what is
        already, genuinely stored.
        """
        memory = self._manager.memory
        if memory is None:
            return ()
        resolved = all_current_personal_context(recall=memory.recall, limit=200)
        return tuple(
            PersonalContextTopicView(
                record_id=item.record_id,
                topic=item.topic,
                content=item.content,
                category=item.category,
                status=item.status,
                noted_by=item.noted_by,
            )
            for item in resolved
        )

    def calendar_items(self) -> tuple[CalendarItemView, ...]:
        """The real, current state of every calendar item - one entry
        per real-world item, always its latest write
        (calendar_items.py's own two-level "latest wins" resolution),
        never a raw history dump and never a decorative example.
        Read-only: adding/cancelling/rescheduling happens through the
        ordinary conversational path (the ``calendar`` capability in
        ``src/manager/capabilities.py``); this only presents what is
        already, genuinely stored.
        """
        memory = self._manager.memory
        if memory is None:
            return ()
        resolved = all_current_calendar_items(recall=memory.recall, limit=200)
        return tuple(
            CalendarItemView(
                record_id=item.record_id,
                item_key=item.item_key,
                topic_key=item.topic_key,
                title=item.title,
                when=item.temporal_description,
                status=item.status,
                noted_by=item.noted_by,
            )
            for item in resolved
        )

    def experiences(self) -> tuple[ExperienceView, ...]:
        """The real, current state of every proactive visual experience -
        pending (selected, not yet reached a live frontend) and history
        (already delivered) alike, each one real, persisted state, never
        a decorative example.

        Evaluates the same idempotent, governed step
        (``evaluate_and_persist_experiences``) a live server's own
        snapshot read already performs before reading - so a terminal
        session and a served page never disagree about what is
        currently due, and calling this twice never re-selects or
        re-persists anything already decided.
        """
        if self._manager.memory is None or self._manager.own_skills is None:
            return ()
        today = Calendar.configured(self._runtime.settings.timezone).today()
        evaluate_and_persist_experiences(
            self._manager, today=today, calendar_events=self._runtime.calendar_events,
        )
        resolved = all_experiences(recall=self._manager.memory.recall, limit=200)
        return tuple(
            ExperienceView(
                record_id=item.record_id,
                event_type=item.event_type,
                agent_key=item.agent_key,
                reaction_id=item.reaction_id,
                avatar_action=item.avatar_action,
                environment_action=item.environment_action,
                overlay=item.overlay,
                intensity=item.intensity,
                duration_ms=item.duration_ms,
                delivered=item.delivered,
            )
            for item in resolved
        )


class _EmptyTools:
    """Stands in for an agent that was granted no tools.

    A null object rather than None, so every caller can ask the same questions
    without a guard - the same pattern NullProvider and NullBrain already use.
    """

    def specs(self) -> Sequence[Any]:
        return ()

    def names(self) -> tuple[str, ...]:
        return ()

    def permits(self, name: str) -> bool:
        return False

    @property
    def is_empty(self) -> bool:
        return True

    def __len__(self) -> int:
        return 0


_EMPTY_TOOLS = _EmptyTools()
