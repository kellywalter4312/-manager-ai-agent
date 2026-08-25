"""Reading the running system and noticing things about it.

Every observer here answers a question from state the architecture already
publishes - a ToolSet's membership, a capability's declared tool, a brain's
assessed mode, an agent's registered spec. None of them runs anything, changes
anything, or asks a model what it thinks.

That constraint is what makes the findings checkable. "The research capability
is offered and its tool is not installed" is something a person can verify in
one command; "the research experience feels weak" is a mood. Only the first
kind belongs here.

Observers stop at describing. Turning an observation into a proposal is
governance's job, and keeping the two apart means a new observer cannot
quietly acquire the ability to suggest widening something.
"""

from __future__ import annotations

from typing import Any

from src.improvement.improvement import Observation, Severity
from src.manager.cognition import assess_cognition, assess_manager


def unusable_capabilities(manager: Any) -> tuple[Observation, ...]:
    """Capabilities the Manager offers that no installed tool can perform.

    The most useful thing the system can currently notice about itself: it
    takes responsibility for work it has no means to do. That is honest rather
    than broken - the alternative was refusing ordinary requests outright - but
    it is worth surfacing, because installing one tool would close it.
    """
    skills = getattr(manager, "own_skills", None)
    if skills is None:
        return ()

    from src.manager.capabilities import GENERAL_CAPABILITIES

    tools = skills.tools
    findings = []
    for capability in GENERAL_CAPABILITIES:
        offered = capability.words & skills.spec.capabilities
        if offered and not tools.permits(capability.tool):
            findings.append(
                Observation(
                    subject=capability.kind,
                    finding=(
                        f"offered as a capability, but the {capability.tool!r} tool "
                        "it needs is not installed, so requests can only be declined"
                    ),
                    severity=Severity.IMPEDING,
                    evidence={
                        "capability": capability.kind,
                        "tool": capability.tool,
                        "words_offered": len(offered),
                        "tools_installed": len(tools),
                    },
                    observed_by=skills.name,
                )
            )
    return tuple(findings)


def unverified_reasoning(manager: Any) -> tuple[Observation, ...]:
    """A model configured but never proven usable.

    Worth reviewing rather than impeding: the system works deterministically,
    and somebody may simply not have exercised it yet. But a provider that is
    configured and never verified is also what an exhausted balance looks like
    before anyone asks a question, and that is worth knowing early.
    """
    profile = assess_manager(manager)
    if profile.is_full_brain or profile.availability.is_known_broken:
        return ()
    if profile.brain_provider in (None, "null") and profile.router_provider in (
        None,
        "null",
    ):
        return ()

    return (
        Observation(
            subject="reasoning",
            finding=(
                "a model is configured but has never answered, so full reasoning "
                "is unproven and the system is running deterministically"
            ),
            severity=Severity.WORTH_REVIEWING,
            evidence={
                "provider": profile.brain_provider or profile.router_provider,
                "mode": profile.mode.label,
                "verified": profile.verified,
            },
        ),
    )


def specialists_without_reasoning(manager: Any) -> tuple[Observation, ...]:
    """Specialists running deterministically while the Manager has a model.

    Each agent's brain is independent, which is correct - but a deployment that
    equipped the Manager and forgot the specialists is a configuration nobody
    intended, and it is invisible unless somebody looks.
    """
    if not assess_manager(manager).is_full_brain:
        return ()

    skills = getattr(manager, "own_skills", None)
    manager_name = skills.name if skills is not None else None

    findings = []
    for agent in manager.registry.all_agents():
        if agent.name == manager_name:
            continue
        brain = getattr(agent, "brain", None)
        if brain is None:
            continue
        profile = assess_cognition(brain)
        if not profile.is_full_brain:
            findings.append(
                Observation(
                    subject=agent.name,
                    finding=(
                        "reasons deterministically while the Manager has verified "
                        "model reasoning, which may be deliberate or may be an "
                        "unset specialist provider"
                    ),
                    severity=Severity.WORTH_REVIEWING,
                    evidence={"specialist": agent.name, "mode": profile.mode.label},
                )
            )
    return tuple(findings)


def idle_specialists(manager: Any) -> tuple[Observation, ...]:
    """Registered specialists whose memory shows no work at all.

    Informational on purpose. A specialist that has done nothing may be
    correctly waiting for work of its kind; the observation is worth having
    when several accumulate, not worth acting on alone.
    """
    skills = getattr(manager, "own_skills", None)
    manager_name = skills.name if skills is not None else None

    findings = []
    for agent in manager.registry.all_agents():
        if agent.name == manager_name:
            continue
        memory = getattr(agent, "memory", None)
        if memory is None:
            continue
        try:
            retained = len(memory.recall(limit=1))
        except Exception:  # noqa: BLE001 - an observation must not raise
            continue
        if retained == 0:
            findings.append(
                Observation(
                    subject=agent.name,
                    finding=(
                        "has retained nothing, so it has not yet done work worth "
                        "remembering"
                    ),
                    severity=Severity.INFORMATIONAL,
                    evidence={"specialist": agent.name, "retained": 0},
                )
            )
    return tuple(findings)


# Every observer the system runs. A tuple of callables, in the same
# small-registry shape as every other extension point here, so a new observer
# is one entry rather than an edit to whatever collects them.
OBSERVERS = (
    unusable_capabilities,
    unverified_reasoning,
    specialists_without_reasoning,
    idle_specialists,
)


def observe_system(manager: Any) -> tuple[Observation, ...]:
    """Run every observer over the running system.

    Fault-isolated the way a delegated agent is: one observer that fails must
    not cost the others their findings, and noticing nothing is a far better
    outcome than a crash inside a self-inspection routine.
    """
    findings: list[Observation] = []
    for observer in OBSERVERS:
        try:
            findings.extend(observer(manager))
        except Exception:  # noqa: BLE001 - deliberate boundary catch
            continue
    return tuple(findings)
