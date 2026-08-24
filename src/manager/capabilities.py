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

from dataclasses import dataclass, field
from typing import Callable, Sequence

from src.agents.base_agent import BaseAgent
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskResult, tokenize
from src.manager.recognition import Recognition
from src.services.identity.identity import AgentIdentity
from src.services.profile.profile import UserProfile
from src.services.schedule.schedule import UserSchedule
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
        words=frozenset(
            {"research", "search", "lookup", "find", "about", "overview", "information"}
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
        the request is not ordinary     -> the default handler answers

    What it never does is fabricate. With no search tool installed there is no
    result to give, and inventing one - or answering from whatever the model
    happens to remember - would be worse than saying nothing. It reports what
    is missing, by name, so the gap is fixable rather than mysterious.

    And note what is absent from the refusal: "I am only the Manager." That
    sentence is the thing this handler exists to make impossible to say.
    """
    capability = _general_kind_for(tokenize(request.task.description))
    if capability is None:
        return report_roster(request)

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


GENERAL_ASSISTANCE = ManagerCapability(
    name="general_assistance",
    capabilities=frozenset(
        word for capability in GENERAL_CAPABILITIES for word in capability.words
    ),
    handle=general_assistance,
    description="Ordinary information and coding work the Manager attempts himself.",
)


# Every Manager capability handler, keyed by handler name.
#
# **This is the extension point.** A future Manager capability is registered
# here and declared in IDENTITY.md - and ManagerAgent, the router and the
# assessor are untouched, because none of them has ever known what a
# capability is. They route to an agent; this decides what that agent does.
MANAGER_CAPABILITIES: dict[str, ManagerCapability] = {
    ROSTER_REPORT.name: ROSTER_REPORT,
    GENERAL_ASSISTANCE.name: GENERAL_ASSISTANCE,
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
) -> ManagerCapability:
    """Pick the handler whose declared words best match this task.

    The same scoring idea the CapabilityRouter uses to pick an agent, and that
    EmailAgent uses to pick an analyzer, one level further down. Ties break on
    handler name so the choice is total and repeatable rather than dependent
    on dictionary ordering.
    """
    registry = MANAGER_CAPABILITIES if capabilities is None else capabilities
    tokens = tokenize(task.description)

    best: ManagerCapability | None = None
    best_score = 0
    for capability in sorted(registry.values(), key=lambda c: c.name):
        score = capability.score(tokens)
        if score > best_score:
            best, best_score = capability, score

    if best is None:
        return DEFAULT_CAPABILITY
    return best
