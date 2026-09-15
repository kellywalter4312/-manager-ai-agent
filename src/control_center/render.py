"""Turns view models into text, and decides nothing else.

The thin half of the Control Center. Every value it prints was already
computed by the observer; this module chooses layout and wording. That split
is what lets a web or mobile front end arrive later and consume the same
records without any of this being ported.

**Responsive by width, because that is what a terminal has.** A phone terminal
is roughly 40 columns and a laptop 100+, and the same information architecture
serves both:

    narrow   one column, labels above values, deep views reached one at a time
    wide     the same screens with room to breathe - never more screens at once

The second half of that is the important half. Extra width is not permission to
put the whole system on one page: a laptop shows the same overview, more
comfortably. Progressive disclosure is a property of the information, not of
the display it happens to land on.

**Nothing is padded to look busy.** A system with one specialist shows one
specialist. An agent with no tools shows none. An empty history says so. The
screen is allowed to be short when the truth is short.
"""

from __future__ import annotations

import shutil

from src.control_center.views import (
    AgentView,
    CalendarItemView,
    ConversationTurn,
    ExperienceView,
    GovernanceDecisionView,
    HistoryView,
    OverviewView,
    PersonalContextTopicView,
    RecordingAgentView,
    TaskItemView,
    UnresolvedFactView,
    VoiceView,
    WorkspaceAgentView,
)

# Below this many columns the renderer assumes a phone-sized terminal and
# stacks everything into one column. Chosen because 60 is comfortably wider
# than a portrait phone terminal and narrower than any laptop window.
NARROW = 60

# Indicators, kept as words rather than colour so the output is readable when
# piped, logged, or read by someone who cannot see colour.
_MARK = {"GREEN": "[OK]", "AMBER": "[--]", "RED": "[!!]"}


def width() -> int:
    """The terminal's width, or a sensible default when there is no terminal."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:  # noqa: BLE001 - rendering must never fail on a screen size
        return 80


def is_narrow(columns: int | None = None) -> bool:
    return (columns if columns is not None else width()) < NARROW


def _rule(columns: int, char: str = "-") -> str:
    return char * min(columns, 78)


def _heading(text: str, columns: int) -> list[str]:
    return [text.upper(), _rule(columns, "=")]


def _field(label: str, value: str, columns: int) -> str:
    """One label/value pair, stacked on a narrow screen and aligned on a wide one."""
    if is_narrow(columns):
        return f"{label}:\n  {value}"
    return f"  {label:<14} {value}"


def _short(text: str, columns: int) -> str:
    """A one-line summary of a longer role or description.

    An overview is a summary; a role sentence written for a document is not.
    Truncated rather than reflowed so the first line of the screen stays one
    line on a phone.
    """
    limit = max(24, min(columns, 78) - 12)
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip(" ,-") + "..."


def _wrap(text: str, columns: int, indent: str = "  ") -> list[str]:
    """Wrap prose to the available width without importing a text engine."""
    limit = max(20, min(columns, 78) - len(indent))
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > limit:
            lines.append(indent + current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(indent + current)
    return lines or [indent]


# ---------------------------------------------------------------------------
# Overview - the first screen, and deliberately short
# ---------------------------------------------------------------------------


def render_overview(overview: OverviewView, columns: int | None = None) -> str:
    """What matters now. Detail lives one command away, not on this screen."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("agent control center", columns)

    manager = overview.manager
    out.append("")
    out.append(f"{manager.name} - {_short(manager.role, columns)}")
    out.append(_field("Brain", f"{_MARK[manager.brain.indicator]} {manager.brain.mode}", columns))
    out.append(_field("Capabilities", str(len(manager.capabilities)), columns))
    out.append(
        _field(
            "Tools",
            ", ".join(t.name for t in manager.tools) if manager.tools else "none installed",
            columns,
        )
    )
    out.append(_field("Memory", f"{manager.memory.backend} ({manager.memory.sampled} recent)", columns))
    out.append(_field("Working on", manager.current_work, columns))

    out.append("")
    out.append(f"SPECIALISTS ({len(overview.specialists)})")
    if not overview.specialists:
        out.append("  none registered")
    for specialist in overview.specialists:
        mark = _MARK[specialist.brain.indicator]
        out.append(f"  {specialist.name} - {mark} {specialist.brain.mode}")
        if not is_narrow(columns):
            out.append(f"      {len(specialist.capabilities)} capabilities, {specialist.memory.backend}")
        if getattr(specialist.interface, "has_workspace", False):
            # Named only when a specialist actually asked for more than a
            # conversation. Most will not, and an overview must not imply a
            # screen that does not exist.
            out.append("      offers a dedicated workspace")

    system = overview.system
    out.append("")
    out.append("SYSTEM")
    out.append(_field("Heartbeat", "enabled" if system.heartbeat.enabled else "disabled", columns))
    out.append(
        _field(
            "Last wake",
            system.heartbeat.last_wake.strftime("%Y-%m-%d %H:%M UTC")
            if system.heartbeat.last_wake
            else "never",
            columns,
        )
    )
    out.append(_field("Mailbox", f"{system.mailbox} ({system.gmail_scope.rsplit('/', 1)[-1]})", columns))
    out.append(_field("Sending", "refused" if not system.can_send else "ENABLED", columns))
    out.append(_field("Provider", system.provider, columns))

    if system.warnings:
        out.append("")
        out.append("NOTES")
        for warning in system.warnings:
            out += _wrap(f"- {warning}", columns)

    out.append("")
    out.append(_rule(columns))
    # The command hint has to fit the screen it is printed on, or the first
    # thing a phone user sees is a wrapped line.
    out.append(
        "status | history | talk"
        if is_narrow(columns)
        else "status <agent> | status system | history | talk | quit"
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Drill-down - one agent at a time
# ---------------------------------------------------------------------------


def render_agent(agent: AgentView, columns: int | None = None) -> str:
    """One agent in full: brain, memory, capabilities, tools - kept apart."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading(f"{agent.name} - {agent.kind}", columns)

    # Phase 1.1: role and area of responsibility already reach this view
    # (see AgentView.role/.area, populated by observer.py's manager_view())
    # but neither was ever printed here - the overview screen shows a
    # truncated one-line role, and role's own full sentence reaches a
    # conversational "who are you", but the *detail* screen (this
    # function) silently dropped both. Rendered here from data that was
    # already computed; nothing new is loaded or invented.
    if agent.role:
        out.append("")
        out.append("ROLE")
        out += _wrap(agent.role, columns)

    if agent.area:
        out.append("")
        out.append("RESPONSIBILITY")
        out += _wrap(agent.area, columns)

    if agent.purpose:
        out.append("")
        out.append("PURPOSE")
        out += _wrap(agent.purpose, columns)

    out.append("")
    out.append("BRAIN")
    out.append(_field("Mode", f"{_MARK[agent.brain.indicator]} {agent.brain.mode}", columns))
    out.append(_field("Verified", "yes" if agent.brain.verified else "no", columns))
    out.append(_field("Provider", agent.brain.provider or "none", columns))
    out += _wrap(agent.brain.reason, columns)
    if agent.brain.missing_capabilities:
        out.append(_field("Without", ", ".join(agent.brain.missing_capabilities), columns))

    out.append("")
    out.append("MEMORY")
    out.append(_field("Store", f"{agent.memory.backend} ({'durable' if agent.memory.durable else 'this run only'})", columns))
    out.append(_field("Owner", agent.memory.owner, columns))
    if agent.memory.is_empty:
        out.append("  nothing retained yet")
    for record in agent.memory.recent:
        out.append(f"  {record.when.strftime('%Y-%m-%d %H:%M')}  [{record.kind}]")
        out += _wrap(record.content, columns, indent="    ")

    out.append("")
    out.append("CAPABILITIES")
    if not agent.capabilities:
        out.append("  none declared")
    for capability in agent.capabilities:
        if capability.tool is None:
            out.append(f"  {capability.name}")
        else:
            state = "available" if capability.tool_available else "NOT INSTALLED"
            out.append(f"  {capability.name}  (tool: {capability.tool} - {state})")

    out.append("")
    out.append("TOOLS")
    if not agent.tools:
        out.append("  none granted")
    for tool in agent.tools:
        out.append(f"  {tool.name} - {'available' if tool.available else 'unavailable'}")

    if agent.interface is not None:
        out.append("")
        out.append("INTERFACE")
        available = ", ".join(sorted(f.value for f in agent.interface.implemented))
        out.append(_field("Available", available or "none", columns))
        if agent.interface.planned:
            # Declared and not yet built. Stated rather than hidden, and never
            # rendered as though it were ready - the same rule a capability
            # whose tool is missing lives under.
            planned = ", ".join(sorted(f.value for f in agent.interface.planned))
            out.append(_field("Planned", planned, columns))

    return "\n".join(out)


def render_system(overview: OverviewView, columns: int | None = None) -> str:
    """Heartbeat, capabilities, tools and health - the installation, not an agent."""
    columns = columns if columns is not None else width()
    system = overview.system
    beat = system.heartbeat
    out: list[str] = []
    out += _heading("system", columns)

    out.append("")
    out.append("HEARTBEAT")
    out.append(_field("Enabled", "yes" if beat.enabled else "no", columns))
    out.append(_field("Interval", f"{beat.interval_minutes} minutes", columns))
    out.append(
        _field(
            "Last wake",
            beat.last_wake.strftime("%Y-%m-%d %H:%M UTC") if beat.last_wake else "never",
            columns,
        )
    )
    # Deliberately not predicted: scheduling lives outside this process, so a
    # time here would describe a mechanism the project does not have.
    out.append(_field("Next wake", "scheduled externally", columns))
    out.append(_field("Seen", f"{beat.messages_seen} message(s), {beat.opportunities_seen} opportunity(ies)", columns))
    out.append(_field("Reports", f"{beat.reports}" + (f" (latest {beat.latest_report})" if beat.latest_report else ""), columns))

    out.append("")
    out.append("PERMISSIONS")
    out.append(_field("Gmail", system.gmail_scope.rsplit("/", 1)[-1], columns))
    out.append(_field("Sending", "refused - no outbox exists" if not system.can_send else "ENABLED", columns))
    out.append(_field("Mailbox", system.mailbox, columns))

    out.append("")
    out.append("TOOLS")
    if not system.tools:
        out.append("  none installed")
    for tool in system.tools:
        out.append(f"  {tool.name} - {'available' if tool.available else 'unavailable'}")

    out.append("")
    out.append("HEALTH")
    out.append(_field("Provider", system.provider, columns))
    out.append(_field("Memory", system.memory_backend, columns))
    out.append(_field("Timezone", f"{system.timezone} (today {system.today})", columns))
    for warning in system.warnings:
        out += _wrap(f"- {warning}", columns)
    if not system.warnings:
        out.append("  no warnings")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Talk to Manager - conversation with the trail beside it
# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


def render_voice(voice: VoiceView, columns: int | None = None) -> str:
    """What is known about speech. Reported, never asserted.

    The status line is the Availability's own words. This function does not
    classify, infer or soften anything: an unproven engine reads as unproven
    rather than as missing, and a missing one carries the reason it gave.
    Deciding here what "usable" means would put a second opinion on a screen
    that exists to show the first one.
    """
    columns = columns if columns is not None else width()
    out: list[str] = ["VOICE"]
    out.append(_field("Engine", voice.engine, columns))
    out.append(_field("Speech", f"{_MARK[voice.indicator]} {voice.status}", columns))
    out.append("")
    # Named by role, never by name. Nothing in this package may hardcode which
    # agent is running - see the audit in tests/test_control_center.py - and a
    # sentence on a screen is no exception.
    out += _wrap(
        "Text and voice are two doors into the same Manager - the same brain, "
        "memory, capabilities, tools and specialists. Typing always works; "
        "speech is added when an engine is installed and has been proven to "
        "work.",
        columns,
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------


def render_turn(turn: ConversationTurn, columns: int | None = None) -> str:
    """One exchange, with the agent behaviour behind it shown compactly.

    The trail is deliberately short - a handful of observable steps, not a
    transcript. It exists so a person can see routing, delegation, brain mode
    and tool availability actually happening, without the answer being buried.
    """
    columns = columns if columns is not None else width()
    out: list[str] = []

    # The delegating agent's name comes from the turn, never from this file.
    # A renderer that spelled it out would have to be edited to rename him,
    # which is precisely what identity documents exist to avoid.
    arrow = (
        f"{turn.manager} -> {turn.handled_by}"
        if turn.delegated and turn.manager
        else turn.handled_by
    )
    out.append(arrow)

    for step in turn.steps:
        if step.label in ("Received",):
            continue
        out += _wrap(f"{step.label}: {step.detail}", columns)

    if turn.capability:
        out.append(f"  Capability: {turn.capability}")

    # Stage 12 closure: the presentation-shaped reaction a genuine
    # milestone produced, if any - rendered as a single line, never
    # re-derived. "Off" (enabled=False) or no celebration at all shows
    # nothing, which is the honest, correct presentation of "off" -
    # this is deliberately the only branch in this file that reads
    # turn.celebration, so a future avatar/environment renderer has
    # exactly one boundary to mirror.
    if turn.celebration and turn.celebration.get("enabled"):
        out.append(f"  Celebration: {_celebration_line(turn.celebration)}")

    out.append("")
    out.append("RESULT")
    out += _result_lines(turn, columns)
    if turn.detail and turn.detail not in (turn.outcome or ""):
        out += _wrap(turn.detail, columns)

    return "\n".join(out)


def _celebration_line(celebration: dict[str, object]) -> str:
    """One compact line from a CelebrationReaction.to_dict() payload -
    presentation labels only, exactly what the dict already carries.
    Never a word of what the milestone means or a reason it happened;
    that sentence already came from Alfred's own answer, above."""
    parts = [str(celebration.get("intensity", ""))]
    avatar = celebration.get("avatar_action")
    environment = celebration.get("environment_action")
    overlay = celebration.get("overlay")
    if avatar:
        parts.append(f"avatar={avatar}")
    if environment:
        parts.append(f"environment={environment}")
    if overlay:
        parts.append(f"overlay={overlay}")
    return " ".join(p for p in parts if p)


# A conversation shows an answer, not a report. A specialist that analysed a
# whole inbox returns many lines, and printing all of them buries the exchange
# the person is actually having. The full text stays reachable through history
# and the specialist's own view.
RESULT_LINES = 12


def _result_lines(turn: ConversationTurn, columns: int) -> list[str]:
    """The answer, capped - with the cap stated rather than silently applied."""
    text = turn.outcome or "(no output)"
    lines: list[str] = []
    for paragraph in text.splitlines():
        lines += _wrap(paragraph, columns) if paragraph.strip() else [""]

    if len(lines) <= RESULT_LINES:
        return lines
    hidden = len(lines) - RESULT_LINES
    return lines[:RESULT_LINES] + [
        f"  ... {hidden} more line(s); see history or the agent view"
    ]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def render_history(history: HistoryView, columns: int | None = None) -> str:
    """What happened, filtered before it arrives rather than after."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("history", columns)

    if history.is_filtered:
        shown = ", ".join(f"{k}={v}" for k, v in sorted(history.filtered_by.items()))
        out.append(f"filtered by {shown}")
    out.append(f"showing {len(history.events)} of {history.total_available} event(s)")

    if history.is_empty:
        out.append("")
        out.append("  nothing recorded yet")
    for event in history.events:
        stamp = event.when.strftime("%Y-%m-%d %H:%M") if event.when else "unknown time"
        out.append("")
        out.append(f"  {stamp}  [{event.category}] {event.actor}")
        out += _wrap(event.summary, columns, indent="    ")

    if history.categories:
        out.append("")
        out.append(f"categories: {', '.join(history.categories)}")
        out.append(f"actors:     {', '.join(history.actors)}")
    return "\n".join(out)


def render_unresolved_facts(facts: tuple[UnresolvedFactView, ...], columns: int | None = None) -> str:
    """Genuine, currently-unresolved personal facts - Stage 12 closure's
    own example: "mom's birthday is either March 24 or May 24".
    Read-only, like every other screen: resolving one happens by saying
    the correct date to Alfred, not from this screen."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("unresolved", columns)

    if not facts:
        out.append("")
        out.append("  nothing unresolved")
        return "\n".join(out)

    for fact in facts:
        out.append("")
        label = fact.fact_type.replace("person_", "").replace("_", " ") or "fact"
        out.append(f"  {fact.person}'s {label}")
        out.append(f"    which is correct? {' / '.join(fact.alternatives)} / enter another date")
        out.append(
            f"    (restate it in conversation, e.g. \"{fact.person}'s birthday is "
            f"{fact.alternatives[0]}\" - id={fact.record_id})"
        )
    return "\n".join(out)


def render_personal_context(
    topics: tuple[PersonalContextTopicView, ...], columns: int | None = None
) -> str:
    """The real, current state of everything the user has taught Alfred
    about themselves - one line per topic, always the latest statement.
    Read-only: teaching happens through ordinary conversation, never from
    this screen."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("personal context", columns)

    if not topics:
        out.append("")
        out.append("  nothing taught yet")
        return "\n".join(out)

    for topic in topics:
        out.append("")
        marker = "[ended]" if topic.status == "discontinued" else "[current]"
        out.append(f"  {marker} {topic.topic} ({topic.category})")
        out.append(f"    {topic.content}")
    return "\n".join(out)


def render_governance(decisions: tuple[GovernanceDecisionView, ...], columns: int | None = None) -> str:
    """The real, most-recent Context-Aware Agent Capability & Safety
    Governance decisions - most recent first, one block per decision,
    naming exactly what was attempted, by which capability, and why it
    was allowed, paused for clarification, sent through confirmation,
    restricted, or refused. Read-only: a decision is made only when a
    real governed capability handler actually attempts an action, never
    from this screen."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("governance", columns)

    if not decisions:
        out.append("")
        out.append("  no governance decisions recorded yet")
        return "\n".join(out)

    for decision in decisions:
        out.append("")
        operation = f":{decision.operation}" if decision.operation else ""
        out.append(f"  [{decision.verdict}] {decision.agent_name}/{decision.capability}{operation}")
        out.append(f"    {decision.reason}")
        out.append(f"    at {decision.at} (source={decision.source})")
    return "\n".join(out)


def render_calendar(
    items: tuple[CalendarItemView, ...],
    columns: int | None = None,
    reminders: "tuple[TaskItemView, ...]" = (),
) -> str:
    """The real, current state of every calendar item, and every pending
    timed reminder - one line each, always its latest write. Read-only:
    adding/cancelling/rescheduling happens through ordinary conversation,
    never from this screen.

    Reminders are shown here, under their own heading rather than mixed in,
    because they are genuinely a different kind of thing (a task with a
    moment, delivered by the proactive check) and pretending otherwise would
    be the "second, parallel view of the same data" this project avoids -
    but a person looking for "what is coming up" looks at this screen, and
    before this they found nothing here at all.
    """
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("calendar", columns)

    if not items:
        out.append("")
        out.append("  nothing on the calendar yet")
    for item in items:
        out.append("")
        marker = {"scheduled": "[current]", "cancelled": "[cancelled]", "completed": "[done]"}.get(
            item.status, f"[{item.status}]"
        )
        out.append(f"  {marker} {item.title}")
        if item.when:
            out.append(f"    {item.when}")

    if reminders:
        out.append("")
        out.append("REMINDERS (delivered at their moment)")
        for reminder in reminders:
            out.append(f"  {reminder.describe}")
    return "\n".join(out)


def render_experiences(items: tuple[ExperienceView, ...], columns: int | None = None) -> str:
    """The real, current state of every selected proactive visual
    experience - pending first, then delivered history. Read-only:
    selection happens through the governed proactive evaluation step,
    never from this screen."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("experience", columns)

    if not items:
        out.append("")
        out.append("  nothing selected yet")
        return "\n".join(out)

    pending = [i for i in items if not i.delivered]
    delivered = [i for i in items if i.delivered]

    for label, group in (("pending", pending), ("delivered", delivered)):
        if not group:
            continue
        out.append("")
        out.append(f"  {label}:")
        for item in group:
            out.append(
                f"    {item.event_type} ({item.agent_key}) - {item.reaction_id} "
                f"[{item.intensity}, {item.duration_ms}ms]"
            )
    return "\n".join(out)


def render_workspaces(items: tuple[WorkspaceAgentView, ...], columns: int | None = None, governed=None) -> str:
    """Alfred's Real 3D Workspace checkpoint: every agent's own real
    Workspace state - discovered from that agent's own declared
    InterfaceDescriptor, never a fixed list naming one agent. Read-only:
    entering happens through the real Workspace route, never from this
    screen; the visible task board is the identical governed task store
    the 'tasks' command already reads."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("workspace", columns)

    if not items:
        out.append("")
        out.append("  no agent has declared a Workspace yet")
        return "\n".join(out)

    from src.control_center.workspace_catalog import active_workspace_for, workspaces_for

    for item in items:
        out.append("")
        out.append(f"  {item.agent} - {item.visits} visit(s)")
        types = workspaces_for(item.agent, governed or {})
        active = active_workspace_for(item.agent, governed or {})
        out.append("    types: " + (", ".join(
            f"{t.workspace_id}{' (open)' if t.workspace_id == active else ''}" for t in types
        ) or "none"))
        if item.last_entered:
            out.append(f"    last entered: {item.last_entered}")
        else:
            out.append("    never entered")
        if not item.tasks:
            out.append("    no tasks yet")
        else:
            for task in item.tasks:
                out.append(f"    {task.describe}")
    return "\n".join(out)


def render_recordings(item: "RecordingAgentView | None", columns: int | None = None) -> str:
    """Real Audio Recording/Capture checkpoint: one agent's own real
    recordings and its current live recording session. Read-only:
    starting/stopping/cancelling stays conversational
    (start_recording/stop_recording/cancel_recording, reached through
    'talk'), never from this screen. ``item`` is None for an
    unrecognised agent name - the caller (scripts/control_center.py's
    own 'recordings' command) has already reported that honestly and
    never reaches this renderer in that case, so this only ever renders
    a real, resolved agent."""
    columns = columns if columns is not None else width()
    out: list[str] = []
    out += _heading("recordings", columns)

    if item is None:
        out.append("")
        out.append("  no such agent")
        return "\n".join(out)

    out.append("")
    out.append(f"  {item.agent}")
    if item.session is not None:
        detail = f" - {item.session['error']}" if item.session.get("error") else ""
        out.append(f"    current session: {item.session['state']} ({item.session['purpose']}){detail}")
    if not item.items:
        out.append("    no saved recordings yet")
    else:
        for recording in item.items:
            out.append(f"    {recording.describe}")
    return "\n".join(out)
