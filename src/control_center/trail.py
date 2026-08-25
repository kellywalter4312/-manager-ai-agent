"""What happened while the Manager handled one request, made observable.

This is what stops Talk to Manager being a chat box. Every request already
passes through diagnosis, capability selection, possibly a delegation, a brain,
a memory and an assessment. All of that happens whether or not anybody watches;
this module watches, and records the parts a person can legitimately see.

**Observable, not internal.** The line matters and is drawn deliberately:

    shown      the routing decision and the reason the router itself gave,
               which agent was selected, which capability answered, the brain
               mode in force, whether a needed tool was present, how many
               records were recalled or written, the assessed outcome

    not shown  a model's deliberation. A brain's private reasoning is the
               model talking to itself on the way to an answer. The answer is
               the user's; the muttering is not, and printing it would be
               dressing up an implementation detail as insight.

Everything below is drawn from values the architecture already produces -
RoutingDecision.reason, TaskResult, CognitiveProfile, ToolSet membership,
Memory counts. Nothing is invented, and no step is fabricated to make the
trail look busier than the work was.

**One routing pass, disclosed.** Building the trail asks the Manager to
diagnose the task, then hands the task to handle_task, which diagnoses again.
With the deterministic CapabilityRouter that is free. With a model-driven
router it is one extra call, which is a real cost and the reason ``observe``
takes a flag to skip it - a caller who does not want the trail does not pay
for it. The alternative was re-implementing the DATA loop here, which would
have made this module a second orchestrator.
"""

from __future__ import annotations

from typing import Any

from src.control_center.views import ConversationTurn, TrailStep
from src.core.contracts import Task, TaskResult, TaskStatus
from src.manager.cognition import assess_cognition, assess_manager
from src.manager.manager import ManagerAgent
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _memory_size(memory: Any, limit: int = 200) -> int:
    """How many records a store currently holds, bounded.

    Bounded because this runs either side of a request and an unbounded read
    would make watching a task more expensive than performing it.
    """
    try:
        return len(memory.recall(limit=limit))
    except Exception:  # noqa: BLE001 - an observability read must not raise
        return 0


def observe(
    manager: ManagerAgent,
    task: Task,
    *,
    explain: bool = True,
) -> ConversationTurn:
    """Handle one request and report what the system observably did.

    ``explain`` controls whether the routing decision is asked for. Left on it
    produces the reason the router gave for choosing an agent; turned off it
    skips that call, which matters only when a model-driven router is
    configured.

    The Manager still does all the work. This function calls handle_task and
    reports; it does not route, select, assess or decide anything itself.
    """
    steps: list[TrailStep] = []
    manager_brain = assess_manager(manager)

    steps.append(
        TrailStep(
            label="Received",
            detail=task.description,
            actor="you",
        )
    )
    steps.append(
        TrailStep(
            label="Manager brain",
            detail=f"{manager_brain.mode.label} - {manager_brain.reason}",
            actor=_manager_name(manager),
        )
    )

    decision = None
    if explain:
        try:
            decision = manager.diagnose(task)
        except Exception as exc:  # noqa: BLE001 - watching must not break the request
            logger.warning("could not read a routing decision: %s", type(exc).__name__)

    if decision is not None:
        steps.append(
            TrailStep(
                label="Routing decision",
                detail=f"{decision.agent_name} (score {decision.score}) - {decision.reason}",
                actor=_manager_name(manager),
            )
        )

    before = {
        agent.name: _memory_size(getattr(agent, "memory", None))
        for agent in manager.registry.all_agents()
        if getattr(agent, "memory", None) is not None
    }
    before[_manager_name(manager)] = _memory_size(manager.memory)

    result = manager.handle_task(task)

    handled_by = result.agent_name or "nobody"
    delegated = bool(result.agent_name) and not manager.handled_it_myself(result)

    specialist_mode = None
    tool_used = None
    tool_available = None
    capability = None

    if result.status is TaskStatus.NO_AGENT_FOUND:
        steps.append(
            TrailStep(
                label="No capability",
                detail="nothing registered takes responsibility for this kind of work",
                actor=_manager_name(manager),
            )
        )
    elif delegated:
        agent = manager.registry.get(handled_by)
        specialist_brain = assess_cognition(getattr(agent, "brain", None))
        specialist_mode = specialist_brain.mode.label
        steps.append(
            TrailStep(
                label="Delegated",
                detail=f"{_manager_name(manager)} -> {handled_by}",
                actor=_manager_name(manager),
            )
        )
        steps.append(
            TrailStep(
                label="Specialist brain",
                detail=f"{specialist_mode} - {specialist_brain.reason}",
                actor=handled_by,
            )
        )
        capability = _shared_words(task, agent)
    else:
        steps.append(
            TrailStep(
                label="Handled directly",
                detail=f"{handled_by} took this on rather than delegating",
                actor=handled_by,
            )
        )
        capability, tool_used, tool_available = _own_capability(manager, task, result)

    if tool_used is not None:
        steps.append(
            TrailStep(
                label="Tool",
                detail=(
                    f"{tool_used} - available"
                    if tool_available
                    else f"{tool_used} - NOT INSTALLED"
                ),
                actor=handled_by,
            )
        )

    recalled, written = _memory_delta(manager, before)
    if written:
        steps.append(
            TrailStep(
                label="Memory",
                detail=f"{written} record(s) retained",
                actor=handled_by,
            )
        )

    steps.append(
        TrailStep(
            label="Assessed",
            detail=(
                "accepted" if result.succeeded else f"not accepted: {result.status.value}"
            ),
            actor=_manager_name(manager),
        )
    )

    return ConversationTurn(
        request=task.description,
        handled_by=handled_by,
        delegated=delegated,
        manager=_manager_name(manager),
        steps=tuple(steps),
        outcome=result.output or result.error or "",
        succeeded=result.succeeded,
        detail=_result_detail(result),
        capability=capability,
        brain_mode=manager_brain.mode.label,
        specialist_brain_mode=specialist_mode,
        memory_recalled=recalled,
        memory_written=written,
        tool_used=tool_used,
        tool_available=tool_available,
    )


def _manager_name(manager: ManagerAgent) -> str:
    skills = manager.own_skills
    return skills.name if skills is not None else "manager"


def _shared_words(task: Task, agent: Any) -> str | None:
    """Which declared capabilities this task actually named.

    A description of what the router matched on, not a re-run of the matching:
    the router has already chosen, and this reports the overlap so a person can
    see why that agent was a fit. It decides nothing.
    """
    if agent is None:
        return None
    shared = sorted(agent.spec.capabilities & task.tokens)
    return ", ".join(shared) if shared else None


def _own_capability(
    manager: ManagerAgent, task: Task, result: TaskResult
) -> tuple[str | None, str | None, bool | None]:
    """Which of the Manager's own capabilities answered, and what it needed.

    Read from the capability registry the Manager already consults, so the
    Control Center learns the answer rather than working it out. A capability
    needing no tool reports None for one, which renders as nothing rather than
    as a missing mechanism.
    """
    skills = manager.own_skills
    if skills is None:
        return None, None, None

    from src.manager.capabilities import GENERAL_CAPABILITIES, select_capability

    try:
        capability = select_capability(task, skills.capabilities)
    except Exception:  # noqa: BLE001 - watching must not break the request
        return None, None, None

    for general in GENERAL_CAPABILITIES:
        if general.words & task.tokens:
            return capability.name, general.tool, skills.tools.permits(general.tool)

    return capability.name, None, None


def _memory_delta(manager: ManagerAgent, before: dict[str, int]) -> tuple[int, int]:
    """How much was retained while this request ran.

    Counts rather than contents. What an agent wrote is available in its own
    memory view; a conversation trail wants the fact that something was
    learned, not the text of it.
    """
    written = 0
    after = {
        agent.name: _memory_size(getattr(agent, "memory", None))
        for agent in manager.registry.all_agents()
        if getattr(agent, "memory", None) is not None
    }
    after[_manager_name(manager)] = _memory_size(manager.memory)

    for name, count in after.items():
        written += max(0, count - before.get(name, 0))

    # Recall is not counted here. The agents recall through their own brains,
    # and reporting a number this module cannot actually observe would be
    # exactly the fabrication the Control Center must not commit.
    return 0, written


def _result_detail(result: TaskResult) -> str:
    """A short, honest note about the shape of the answer."""
    payload = result.payload or {}
    findings = payload.get("findings")
    if findings is not None:
        return f"{len(findings)} finding(s)"
    if result.error:
        return result.error
    return ""
