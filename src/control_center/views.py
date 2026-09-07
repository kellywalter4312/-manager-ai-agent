"""What the Control Center shows, as data rather than as text.

Every screen in the Control Center is a frozen record built here and rendered
somewhere else. That split is the whole design decision, and it is worth being
explicit about why:

**The Control Center owns no business logic.** It does not route, match a
capability, assess a brain, admit a memory, register a specialist or schedule a
wake. It asks the architecture what is true and arranges the answer. A view
model is the shape of that answer, and because it is inert data it cannot
quietly start deciding anything.

**Rendering is an adapter, not the product.** This project ships a terminal
renderer because it has no UI framework and adding one would be a dependency
bought for a screen. A web or mobile front end later consumes these same
records without any of them changing - the observability layer is the part
worth building carefully, and the pixels are the easy part.

**Nothing here may be fabricated.** A field exists only if the system can
actually answer it. Tool availability is membership in a real ToolSet; a brain
mode is what assess_cognition concluded from an observed call; a memory count
is records that are really there. Where the system does not know, the record
says so - `None` and "unknown" are honest, and a plausible default is not.

One rule runs through all of it: **overview first, detail on demand.** The
records are deliberately nested so a caller can render a summary without ever
touching the deep views, which is what stops the Control Center becoming one
wall of everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from src.providers.availability import Availability, AvailabilityState


@dataclass(frozen=True)
class VoiceView:
    """Whether Alfred can speak, as evidence rather than as a claim.

    Carries the ``Availability`` itself rather than fields copied out of it.
    That is the whole design: a screen that stored its own "can speak" flag
    would be a second availability model, and the first time the two disagreed
    a person would believe the screen.

    So there is no boolean here, no speech-status enum, and nothing that
    decides anything. Three states arrive and three states are shown:

        UNKNOWN       an engine exists and nothing has proven it usable
        AVAILABLE     speech has actually been observed working
        UNAVAILABLE   known not usable, and the reason is carried with it

    ``engine`` is the adapter's own name - "none" for NoSpeech - so a person
    can tell "there is no engine" from "there is one and it has not spoken
    yet", which is exactly the distinction a boolean used to destroy.

    Note what is absent: separate hearing and speaking lines. One adapter
    publishes one Availability, so printing two verdicts derived from one value
    would re-invent at the screen the conflation the contract deliberately
    accepts. When listening and speaking become separate interfaces, each will
    bring its own view.
    """

    engine: str
    availability: Availability

    @property
    def status(self) -> str:
        """The evidence in words, phrased by the record that holds it."""
        return self.availability.describe()

    @property
    def indicator(self) -> str:
        """A mark for a status line. A mapping of the three states, not a
        judgement about them - the same vocabulary BrainView uses."""
        if self.availability.state is AvailabilityState.AVAILABLE:
            return "GREEN"
        if self.availability.state is AvailabilityState.UNAVAILABLE:
            return "RED"
        return "AMBER"


@dataclass(frozen=True)
class BrainView:
    """How deeply one agent can reason, and on what evidence.

    Deliberately separate from MemoryView. Brain is reasoning capability;
    memory is retained information. Collapsing them is the single easiest way
    to lose the distinction this architecture is built on, and a shared record
    would invite exactly that.

    ``verified`` is the honest half of the mode. FULL implies it - an
    unverified provider is reported HALF - but a caller inspecting a HALF brain
    can use it to tell "no model configured" from "configured, never proven".
    """

    agent: str
    mode: str
    verified: bool
    reason: str
    provider: str | None = None
    router_provider: str | None = None
    degraded: bool = False
    available_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()

    @property
    def is_full(self) -> bool:
        return self.mode == "FULL BRAIN"

    @property
    def indicator(self) -> str:
        """A glyph for a status line. Green only for verified reasoning."""
        if self.degraded:
            return "RED"
        return "GREEN" if self.is_full else "AMBER"


@dataclass(frozen=True)
class MemoryRecordView:
    """One retained record, summarised for display.

    Carries the record's own content because that is what a person came to
    read. It does not carry the store's internals - a path, an index, a file
    format - because those explain nothing to the person and are the kind of
    detail that turns an observability screen into a database dump.
    """

    when: datetime
    kind: str
    content: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryView:
    """What one agent has retained, and what it recently did with it.

    Three things kept apart on purpose, because they answer different
    questions and merging them produces a screen nobody can read:

        recent      what is retained now
        recalled    what this request actually looked up
        written     what this request added

    ``total_known`` is capped by how many records were asked for, so it is
    reported as "at least" rather than as a total the store never promised.
    """

    agent: str
    owner: str
    backend: str
    durable: bool
    recent: tuple[MemoryRecordView, ...] = ()
    sampled: int = 0
    recalled_this_request: int = 0
    written_this_request: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.recent


@dataclass(frozen=True)
class UnresolvedFactView:
    """One personal fact Alfred knows only as genuine uncertainty -
    Stage 12 closure's own example: "mom's birthday is either March 24
    or May 24, unconfirmed". Read-only, the same as every other view in
    this file: resolving it happens through the ordinary conversational
    path (restating the fact with the correct date), never from this
    screen.
    """

    record_id: str
    person: str
    fact_type: str
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class ToolView:
    """A concrete mechanism, and whether it is genuinely present.

    The Control Center must never print "Web Search: available" because a
    capability mentions one. Availability here is membership in a real
    ToolSet, so an absent tool renders as absent.
    """

    name: str
    available: bool
    description: str = ""


@dataclass(frozen=True)
class CapabilityView:
    """A kind of work an agent takes responsibility for.

    Separate from ToolView, and the pairing is the point: a capability can be
    offered while the tool it needs is missing, and that is the honest state
    the Control Center has to be able to show. ``tool`` is None for capabilities
    that need no external mechanism - reporting a roster needs nothing but the
    registry.
    """

    name: str
    tool: str | None = None
    tool_available: bool = True

    @property
    def is_performable(self) -> bool:
        """Whether the work could actually be attempted right now."""
        return self.tool is None or self.tool_available


@dataclass(frozen=True)
class AgentView:
    """One agent, at the depth a status overview needs.

    The same record for the Manager and for every specialist, because the
    Control Center must not have a shape that only fits one of them. A second
    specialist renders through this unchanged; that is what makes the interface
    scale without a redesign.
    """

    name: str
    role: str
    kind: str
    brain: BrainView
    memory: MemoryView
    capabilities: tuple[CapabilityView, ...] = ()
    tools: tuple[ToolView, ...] = ()
    purpose: str = ""
    area: str = ""
    current_work: str = "idle"
    # How this agent wants to be interacted with, as it declared. None means
    # it declared nothing, which is a complete answer: conversation through
    # the Manager is a real way to work with an agent, not a gap.
    interface: Any | None = None
    # Stage 17: this agent's own presence - a chosen or deterministic-
    # default avatar, and whatever presence state the observer has
    # genuine evidence for. None only for a caller that predates this
    # field (an older test construction) - every real view built by
    # observer.py supplies one.
    presence: Any | None = None
    # Final acceptance sweep (2026-09-08): this agent's own chosen or
    # deterministic-default EnvironmentProfile - the identical governed
    # preference `environment_for()` already resolves for the CLI's own
    # `environment` command, exposed here so the Control Center UI can
    # finally show and change it rather than it being reachable only by
    # typing the raw command. None only for a caller that predates this
    # field; every real view built by observer.py supplies one.
    environment: Any | None = None

    @property
    def is_manager(self) -> bool:
        return self.kind == "manager"


@dataclass(frozen=True)
class HeartbeatView:
    """The autonomous loop, observed and never driven.

    The Control Center reads this. It does not start a wake, stop one, or hold
    a timer: scheduling stays outside the process entirely, exactly as it did
    before this layer existed.

    ``next_wake`` is None whenever nothing can honestly predict one - this
    project registers `--once` with an external scheduler, and inventing a
    time would be describing a mechanism that does not exist here.
    """

    enabled: bool
    interval_minutes: int
    state_path: str
    last_wake: datetime | None = None
    messages_seen: int = 0
    opportunities_seen: int = 0
    reports: int = 0
    latest_report: str | None = None
    next_wake: datetime | None = None

    @property
    def has_run(self) -> bool:
        return self.last_wake is not None


@dataclass(frozen=True)
class SystemView:
    """Everything that is about the installation rather than about an agent."""

    # The assembled product's own version (src/core/versioning.py) - not a
    # subsystem's SCHEMA_VERSION, not the snapshot format's own version.
    # "What is Alfred OS today", the same kind of fact this view already
    # reports for the provider or the memory backend.
    system_version: str
    heartbeat: HeartbeatView
    mailbox: str
    gmail_scope: str
    can_send: bool
    timezone: str
    today: str
    provider: str
    memory_backend: str
    tools: tuple[ToolView, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_read_only(self) -> bool:
        return not self.can_send and self.gmail_scope.endswith("readonly")


@dataclass(frozen=True)
class OverviewView:
    """The first screen: what matters now, and nothing else.

    Holds the full agent records so a drill-down needs no second pass, but a
    renderer showing the overview reads only the summary fields. Overview
    first, detail on demand - expressed in the data rather than trusted to the
    renderer.
    """

    manager: AgentView
    specialists: tuple[AgentView, ...]
    system: SystemView
    generated_at: datetime | None = None

    @property
    def agents(self) -> tuple[AgentView, ...]:
        return (self.manager,) + self.specialists

    def agent(self, name: str) -> AgentView | None:
        """Find one agent by name, for a drill-down request."""
        for candidate in self.agents:
            if candidate.name.lower() == name.lower():
                return candidate
        return None


# ---------------------------------------------------------------------------
# Talk to Manager: the operational trail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailStep:
    """One observable thing that happened while a request was handled.

    **Observable** is the operative word. A step is something the system did
    that can be pointed at afterwards - a routing decision and its stated
    reason, a capability selected, a brain consulted, a tool found missing, a
    result assessed.

    It is never private reasoning. A model's internal deliberation is not a
    step, and the difference matters: "Email specialist selected because the
    task requires mailbox analysis" is a decision summary the router itself
    produced, while a chain of thought is the model talking to itself and is
    not the user's to read.
    """

    label: str
    detail: str
    actor: str = ""


@dataclass(frozen=True)
class ConversationTurn:
    """One exchange, with the agent behaviour behind it made visible.

    This is what stops Talk to Manager looking like a chat box. Every request
    already goes through diagnosis, capability selection, possibly delegation,
    a brain, a memory and an assessment; the turn records that trail so a
    person can see an agent system working rather than infer it.
    """

    request: str
    handled_by: str
    delegated: bool
    steps: tuple[TrailStep, ...]
    outcome: str
    succeeded: bool
    # Who delegated, when anyone did. Carried on the turn rather than written
    # into the renderer: the Manager's name comes from his identity document,
    # and a screen that spelled it out would need editing to rename him.
    manager: str = ""
    detail: str = ""
    capability: str | None = None
    brain_mode: str = ""
    specialist_brain_mode: str | None = None
    memory_recalled: int = 0
    memory_written: int = 0
    tool_used: str | None = None
    tool_available: bool | None = None
    # Who the Manager resolved an invitation to, when the request was
    # one. A name, never an agent - carried for the same reason
    # `capability` and `tool_used` are: it is a fact the orchestration
    # already produced, and re-deriving it downstream would mean
    # something other than the Manager deciding who was meant.
    invited: str | None = None
    # Stage 12 closure: the presentation-shaped CelebrationReaction a
    # genuine, already-stored milestone produced, as CelebrationReaction.to_dict()
    # already renders it - never re-derived here, only carried. None for
    # every ordinary turn (which is most of them); a real Off/dict with
    # enabled=False for a milestone that was recorded with celebration
    # turned off; a real dict with enabled=True carrying an avatar/
    # environment/overlay/intensity/duration for a genuine celebration.
    # This is the event/consumption boundary a presentation layer (today:
    # this Control Center's own render_turn and serve_ui.py's JSON
    # response; tomorrow: a real avatar/environment renderer) reads from -
    # see src/providers/celebration.py's own docstring for why nothing
    # upstream of this field ever reaches a Brain or a Memory to produce it.
    celebration: dict[str, object] | None = None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryEvent:
    """Something that happened, with enough context to be understood later.

    History answers "what happened"; status answers "what is happening". They
    are separate screens because they are separate questions, and a single
    stream that tried to serve both would serve neither.
    """

    when: datetime | None
    category: str
    actor: str
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class HistoryView:
    """A filtered slice of the past, never the whole of it at once.

    Filtering lives in the query rather than in the renderer, so a screen
    cannot accidentally become the raw dump this design exists to avoid.
    """

    events: tuple[HistoryEvent, ...] = ()
    categories: tuple[str, ...] = ()
    actors: tuple[str, ...] = ()
    filtered_by: Mapping[str, str] = field(default_factory=dict)
    total_available: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.events

    @property
    def is_filtered(self) -> bool:
        return bool(self.filtered_by)
