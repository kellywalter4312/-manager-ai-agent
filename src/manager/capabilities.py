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

from dataclasses import dataclass
from typing import Callable, Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import Task, TaskResult, tokenize
from src.services.identity.identity import AgentIdentity
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


# Every Manager capability handler, keyed by handler name.
#
# **This is the extension point.** A future Manager capability is registered
# here and declared in IDENTITY.md - and ManagerAgent, the router and the
# assessor are untouched, because none of them has ever known what a
# capability is. They route to an agent; this decides what that agent does.
MANAGER_CAPABILITIES: dict[str, ManagerCapability] = {
    ROSTER_REPORT.name: ROSTER_REPORT,
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
