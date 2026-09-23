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
from src.manager.capabilities import HANDLED_BY_KEY, INVITED_KEY
from src.manager.cognition import assess_cognition, assess_manager
from src.manager.collaboration_entry import answer_with_collaboration
from src.manager.manager import ManagerAgent
from src.manager.objective_loop import pursue_objective
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
    addressed: str | None = None,
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
            # decide(), not diagnose(): the trail must show the decision
            # that was actually made, and an addressed task is decided
            # by the address. Reporting the router's opinion instead
            # would print a reason that did not happen.
            decision = manager.decide(task, addressed)
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

    # Stage 13 final integration, extended by the Pre-Phase Agentic
    # Workflow checkpoint: an unaddressed request is where Alfred decides
    # for himself, so it is the one case the governed objective loop
    # applies to. `pursue_objective` runs the identical single
    # `answer_with_collaboration` step this call site has always run, and
    # - only in Full Brain, only when the Brain itself judges more is
    # genuinely needed - reasons about whether the objective is actually
    # complete, dynamically, never through a hardcoded workflow. A Half
    # Brain installation, or any objective the Brain judges already
    # answered by that one step, produces byte-identical output to before
    # this checkpoint existed.
    #
    # An explicit address is a deliberate override ("Arthur, ...") and
    # keeps going straight to `handle_task`, unchanged: addressing somebody
    # by name has always meant the Manager's own decision is skipped, and a
    # multi-step or collaboration synthesis picking a different final
    # speaker would break exactly that guarantee.
    objective_outcome = None
    if addressed is None:
        objective_outcome = pursue_objective(manager, task)
        result = objective_outcome.result
    else:
        result = manager.handle_task(task, addressed)

    # Only shown when more than the ordinary one step actually ran - an
    # objective the Brain judged already answered by a single step (the
    # overwhelming majority of ordinary requests) leaves the trail exactly
    # as it was before this checkpoint existed.
    if objective_outcome is not None and len(objective_outcome.steps) > 1:
        # Goal-level planning checkpoint: the same summary as before,
        # with the real planned-step detail ObjectiveOutcome.step_summaries()
        # now derives appended - which operation each step reached, and
        # (the genuine dependency evidence) whether it carried forward a
        # value an earlier step's own real output actually produced.
        # Nothing here is re-derived independently; it is read straight
        # from the identical ObjectiveOutcome this call already produced.
        plan = " -> ".join(objective_outcome.step_summaries())
        steps.append(
            TrailStep(
                label="Objective pursuit",
                detail=(
                    f"{objective_outcome.status.label} after "
                    f"{len(objective_outcome.steps)} step(s): {plan}"
                ),
                actor=_manager_name(manager),
            )
        )

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
        # What the specialist actually did, when a registered capability
        # answered - falling back to the router's own word overlap for a
        # delegation that reports no capability (EmailAgent's domain work).
        capability = (
            _executed_capability(result)
            or _selected_capability(agent, task)
            or _shared_words(task, agent)
        )
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
    # Each write is shown as the agent whose memory it went into - a delegated
    # turn's record is often the Manager's own note that he delegated, and
    # crediting it to the specialist who answered would say it learned
    # something it did not.
    for writer, count in _memory_writes(manager, before).items():
        steps.append(
            TrailStep(
                label="Memory",
                detail=f"{count} record(s) retained",
                actor=writer,
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
        # Assessed again after the work: the mode a turn reports is the one
        # its own reasoning call just proved (or disproved). The pre-turn
        # reading above said HALF BRAIN for the very turn a newly switched
        # model first answered, so the label lagged the runtime by a turn.
        brain_mode=assess_manager(manager).mode.label,
        specialist_brain_mode=specialist_mode,
        memory_recalled=recalled,
        memory_written=written,
        tool_used=tool_used,
        tool_available=tool_available,
        invited=_invited_in(result),
        celebration=_celebration_of(result),
        speak_in_full=bool((result.payload or {}).get("speak_in_full")),
        # Read from the payload the confirming capability already writes,
        # never inferred: a turn that only asked "(yes/no)" executed nothing.
        awaiting_confirmation=(result.payload or {}).get("awaiting") == "confirmation",
    )


def _celebration_of(result: TaskResult) -> dict[str, object] | None:
    """The presentation-shaped reaction a genuine milestone produced, if
    any - read from the payload milestone_recognition already builds
    (CelebrationReaction.to_dict()), never re-selected or re-derived
    here. Nothing is inferred: a turn that was not a milestone, or one
    handled with no celebration provider configured, carries no such
    key and this returns None - the identical honest-absence shape
    ``_invited_in`` already has just above.
    """
    celebration = (result.payload or {}).get("celebration")
    return celebration if isinstance(celebration, dict) else None


def _invited_in(result: TaskResult) -> str | None:
    """Whoever the Manager resolved an invitation to, if anyone.

    Read from the payload the capability handler already produced.
    Nothing is inferred: a request that was not an invitation carries
    no such key, and a name nobody is registered under was already
    resolved to None by the handler.
    """
    invited = (result.payload or {}).get(INVITED_KEY)
    return invited if isinstance(invited, str) and invited.strip() else None


def _executed_capability(result: TaskResult) -> str | None:
    """The registered capability that actually ran, as its own handler
    reported it - or None when this result came from somewhere that reports
    no capability at all (EmailAgent's own domain work, a specialist's own
    conversation floor).

    Read from the payload the handler already produced, the identical
    "learn the answer rather than work it out" rule ``_invited_in`` above
    and ``_own_capability`` below both already follow. Nothing is inferred:
    a name no capability is registered under is not one.

    **Why this exists.** A delegated turn used to report ``_shared_words``
    - the overlap between the request's words and the specialist's declared
    spec - as its capability. That is an honest description of *why the
    router picked that agent*, and it was the only thing available when the
    only delegation was to an agent with domain code of its own. It is not
    what the specialist then *did*: once a specialist could be granted a
    registered capability (``capabilities grant``) and run the identical
    handler Alfred runs, every such turn still reported a word like
    "images", or nothing at all.

    Two things read that field, and both were wrong for a specialist. The
    Control Center's own trail showed the owner no capability name for work
    a specialist genuinely performed; and ``scripts/exchange.py`` gates the
    durable consequential-action audit log on
    ``turn.capability in CONSEQUENTIAL_CAPABILITIES`` - so a specialist
    granted ``communication`` really sent a message through the real
    provider and **nothing was written to the action log at all**, while
    Alfred doing the identical thing wrote an entry (found at runtime,
    2026-09-23). A consequential action that no audit record names is the
    one thing that log exists to make impossible.
    """
    name = (result.payload or {}).get(HANDLED_BY_KEY)
    if not isinstance(name, str) or not name.strip():
        return None
    from src.manager.capabilities import MANAGER_CAPABILITIES

    return name if name in MANAGER_CAPABILITIES else None


def _manager_name(manager: ManagerAgent) -> str:
    skills = manager.own_skills
    return skills.name if skills is not None else "manager"


def _selected_capability(agent: Any, task: Task) -> str | None:
    """The registered capability this agent's own authorised-capability
    handler actually chose for this task, or None.

    The identical question ``_own_capability`` below asks the Manager
    through the identical method name, asked of a specialist instead - so a
    capability that *failed* (and therefore returned no payload for
    ``_executed_capability`` to read) is still named as the one that was
    reached, rather than falling through to the router's word overlap.
    """
    if agent is None:
        return None
    selected = getattr(agent, "selected_capability_for", None)
    try:
        return selected(task.id) if callable(selected) else None
    except Exception:  # noqa: BLE001 - watching must not break the request
        return None


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

    from src.manager.capabilities import CORRECT_MEMORY, GENERAL_CAPABILITIES, OPEN_CONVERSATION, select_capability

    try:
        # Mirrors the identical priority check ManagerSkills.execute()
        # itself applies: while a clarification or guided activity is
        # genuinely active, that is what actually answers, regardless of
        # which capability's words this task's text would otherwise
        # match - the trail must show the decision that was actually
        # made, never a re-derived guess that ignores session state.
        sessions = getattr(skills, "_activity_sessions", None)
        chosen = getattr(skills, "selected_capability_for", lambda _task_id: None)(task.id)
        if chosen is not None and chosen in skills.capabilities:
            # Pre-Stage-9 Campaign 2: the capability ManagerSkills actually
            # selected for this very task - never a re-derivation after the
            # session state that decided it has already changed.
            capability = skills.capabilities[chosen]
        elif sessions is not None and (
            sessions.pending_clarification(skills.name) is not None
            or sessions.activity(skills.name) is not None
        ):
            capability = OPEN_CONVERSATION
        elif sessions is not None and sessions.pending_correction(skills.name) is not None and (
            sessions.pending_correction(skills.name).select_option(task.description) is not None
            or sessions.pending_correction(skills.name).is_cancel(task.description)
        ):
            capability = CORRECT_MEMORY
        else:
            capability = select_capability(
                task,
                skills.capabilities,
                participants=[a.name for a in manager.registry.all_agents()],
            )
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
    # Recall is not counted here. The agents recall through their own brains,
    # and reporting a number this module cannot actually observe would be
    # exactly the fabrication the Control Center must not commit.
    return 0, sum(_memory_writes(manager, before).values())


def _memory_writes(manager: ManagerAgent, before: dict[str, int]) -> dict[str, int]:
    """How many records each agent's own memory gained while this request
    ran - only the agents whose memory actually grew, the Manager first."""
    after = {_manager_name(manager): _memory_size(manager.memory)}
    after.update({
        agent.name: _memory_size(getattr(agent, "memory", None))
        for agent in manager.registry.all_agents()
        if getattr(agent, "memory", None) is not None and agent.name not in after
    })
    grown = {name: count - before.get(name, 0) for name, count in after.items()}
    return {name: delta for name, delta in grown.items() if delta > 0}


def _result_detail(result: TaskResult) -> str:
    """A short, honest note about the shape of the answer."""
    payload = result.payload or {}
    findings = payload.get("findings")
    if findings is not None:
        return f"{len(findings)} finding(s)"
    if result.error:
        return result.error
    return ""
