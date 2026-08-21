"""Turning a BrainDecision into something that actually happens - or refusing to.

The layer between thinking and doing:

    Brain        "what should we do?"
    ACTION LAYER "is that permitted here, and how is it carried out?"
    Specialist   "how do I accomplish this in my domain?"
    Assessor     "did it actually succeed?"

It is **not a second brain**. It never decides what the Manager should do; it
receives a decision already made and answers a much narrower question - whether
that decision can be carried out in the situation as it stands right now. Every
branch below is validation and dispatch. There is no judgement in this file.

Why a separate layer at all: a decision is formed at one moment and acted on at
another, and the world moves in between. An agent named in a decision may have
been unregistered since. A task that looked routable may no longer be. Checking
that at the point of execution - against live state, not against whatever was
true when the model answered - is this module's entire job.

**Router authority is preserved, explicitly.** A DELEGATE decision names an
agent, but naming is not choosing: the Router is asked to route the task, and
execution proceeds only if it independently selects the same agent. Disagreement
is refused and reported, never resolved in either direction. Nothing can run
here that the Router would not have chosen anyway, which is what stops a model's
preference from quietly becoming the routing policy.

Execution and assessment are not reimplemented. ``assemble``, ``take_action``
and ``assess`` already exist on the Manager, and this composes them - so an
action-layer delegation and an ordinary one go through byte-for-byte the same
path.

Deliberately absent: any provider, any model, any mailbox, any memory. This file
imports none of them and holds no reference to one. It cannot think, cannot
retain, and cannot reach a specialist's internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.core.contracts import Task, TaskResult
from src.manager.brain import BrainAction, BrainDecision
from src.manager.manager import ManagerAgent
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class ActionStatus(str, Enum):
    """What became of a decision once it met reality."""

    EXECUTED = "executed"        # work ran; see ``result``
    REFUSED = "refused"          # valid decision, not permitted here and now
    UNSUPPORTED = "unsupported"  # the Manager cannot do this at all yet
    ESCALATED = "escalated"      # needs a person; nothing was run
    NO_ACTION = "no_action"      # nothing to do, and that is correct

    @property
    def ran_work(self) -> bool:
        return self is ActionStatus.EXECUTED


@dataclass(frozen=True)
class ActionResult:
    """What the action layer did about one decision.

    Distinct from TaskResult on purpose. A TaskResult says how a piece of work
    went; this says whether work was attempted at all, and why. Collapsing them
    would make "the specialist failed" indistinguishable from "we declined to
    run anything", which are opposite situations for a caller to be in.

    Frozen, like every other record here.
    """

    action: BrainAction
    status: ActionStatus
    reason: str
    result: TaskResult | None = None
    agent_name: str | None = None

    def __post_init__(self) -> None:
        if not str(self.reason).strip():
            raise ValueError("ActionResult must explain itself")
        object.__setattr__(self, "reason", " ".join(str(self.reason).split()))

        target = (self.agent_name or "").strip()
        object.__setattr__(self, "agent_name", target or None)

        # A result may only accompany work that actually ran. Attaching one to
        # a refusal would let a caller read an outcome into a non-event.
        if self.result is not None and self.status is not ActionStatus.EXECUTED:
            raise ValueError(
                f"an action with status '{self.status.value}' cannot carry a TaskResult"
            )

    @property
    def executed(self) -> bool:
        return self.status.ran_work

    def describe(self) -> str:
        """A short line for logs. Excludes the reason's payload-derived text."""
        target = f" -> {self.agent_name}" if self.agent_name else ""
        return f"{self.action.value}{target}: {self.status.value}"


class ActionExecutor:
    """Carries out a BrainDecision, or explains why it will not.

    The Manager is injected. This class holds no state of its own, so the same
    decision against the same Manager always produces the same result.
    """

    def __init__(self, manager: ManagerAgent) -> None:
        self._manager = manager

    def __repr__(self) -> str:
        return "<ActionExecutor>"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def execute(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Validate ``decision`` against live state and carry it out.

        ``task`` is the work the decision was formed about. It is supplied by
        the caller rather than reconstructed from the decision, because a
        decision carries reasoning and not a unit of work - inventing a Task
        from a model's prose is exactly the kind of fabrication this project
        refuses elsewhere.
        """
        if not isinstance(decision, BrainDecision):
            # A malformed decision is refused before anything is dispatched.
            # Coercing it would mean guessing what was meant.
            return ActionResult(
                action=BrainAction.UNKNOWN,
                status=ActionStatus.REFUSED,
                reason=(
                    f"expected a BrainDecision, got {type(decision).__name__}; "
                    f"nothing was executed"
                ),
            )

        if not isinstance(task, Task):
            return ActionResult(
                action=decision.action,
                status=ActionStatus.REFUSED,
                reason=f"expected a Task, got {type(task).__name__}; nothing was executed",
            )

        handler = _HANDLERS.get(decision.action)
        if handler is None:
            # Unreachable while BrainAction is a closed set, and present so a
            # newly added action fails loudly instead of silently doing nothing.
            return ActionResult(
                action=decision.action,
                status=ActionStatus.REFUSED,
                reason=f"no handling is defined for action '{decision.action.value}'",
            )

        log = task_logger(logger, task.id)
        outcome = handler(self, decision, task)
        log.info("action: %s", outcome.describe())
        return outcome

    # ------------------------------------------------------------------
    # Per-action handling
    # ------------------------------------------------------------------

    def _delegate(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Run the work, but only where the Router independently agrees.

        Three checks, in order of cheapness, and all against live state rather
        than against whatever was true when the decision was formed.
        """
        target = decision.delegate_to
        if not target:
            # BrainDecision already refuses this at construction; repeated here
            # because this layer must not depend on an upstream invariant it
            # cannot see.
            return self._refuse(decision, "the decision names no agent to delegate to")

        # 1. Does the named agent exist *now*?
        agent = self._manager.assemble(target)
        if agent is None:
            registered = ", ".join(sorted(a.name for a in self._manager.registry.all_agents()))
            return self._refuse(
                decision,
                f"'{target}' is not a registered agent "
                f"(registered: {registered or 'none'})",
                agent_name=None,
            )

        # 2. Does the Router agree? The brain proposes; routing stays the
        #    Router's to decide. Asked once - with an LLM router, each call
        #    costs a request.
        routed = self._manager.diagnose(task)
        if routed is None:
            return self._refuse(
                decision,
                f"the router found no agent capable of this task, so '{target}' "
                f"was not run",
                agent_name=target,
            )

        if routed.agent_name != target:
            return self._refuse(
                decision,
                f"the brain proposed '{target}' but the router selected "
                f"'{routed.agent_name}'; nothing was executed",
                agent_name=target,
            )

        # 3. Agreed. Execute through the Manager's own path - the same
        #    fault-isolated delegation and the same Assessor an ordinary
        #    mission uses. Nothing about execution is reimplemented here.
        result = self._manager.take_action(agent, task)
        verdict = self._manager.assess(task, agent.name, result)

        return ActionResult(
            action=BrainAction.DELEGATE,
            status=ActionStatus.EXECUTED,
            reason=f"delegated to '{agent.name}' with the router's agreement",
            result=verdict,
            agent_name=agent.name,
        )

    def _handle_self(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Run the Manager's own skills, if it actually has any.

        No self-execution capability is invented. The Manager's skills are an
        ordinary registered agent when they exist at all, so this is the
        delegation path pointed at that agent - and when they do not exist, the
        honest answer is that this cannot be done yet.
        """
        skills = self._manager.own_skills
        if skills is None:
            return ActionResult(
                action=BrainAction.HANDLE_SELF,
                status=ActionStatus.UNSUPPORTED,
                reason=(
                    "this Manager has no skills of its own registered, so it "
                    "cannot handle work directly"
                ),
            )

        # Reuses delegation wholesale, Router concurrence included: the Manager
        # wins its own work only when the router judges it best suited, exactly
        # as it does during an ordinary mission.
        proposed = BrainDecision(
            action=BrainAction.DELEGATE,
            reason=decision.reason,
            confidence=decision.confidence,
            delegate_to=skills.name,
            source=decision.source,
        )
        outcome = self._delegate(proposed, task)

        return ActionResult(
            action=BrainAction.HANDLE_SELF,
            status=outcome.status,
            reason=outcome.reason,
            result=outcome.result,
            agent_name=outcome.agent_name,
        )

    def _report(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Carry the recommendation back without pretending work happened."""
        return ActionResult(
            action=BrainAction.REPORT,
            status=ActionStatus.NO_ACTION,
            reason=decision.reason,
        )

    def _escalate(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Represent the escalation. There is no escalation service to call.

        Inventing one - a notification, a queue, an email - would be inventing
        a capability this system does not have. The honest form of "a person
        needs to decide" is a result saying so, handed to whoever is driving.
        """
        return ActionResult(
            action=BrainAction.ESCALATE,
            status=ActionStatus.ESCALATED,
            reason=decision.reason,
        )

    def _unknown(self, decision: BrainDecision, task: Task) -> ActionResult:
        """Do nothing, and say why. Acting on "I don't know" would be guessing."""
        return ActionResult(
            action=BrainAction.UNKNOWN,
            status=ActionStatus.NO_ACTION,
            reason=f"no action taken: {decision.reason}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refuse(
        self, decision: BrainDecision, reason: str, agent_name: str | None = None
    ) -> ActionResult:
        return ActionResult(
            action=decision.action,
            status=ActionStatus.REFUSED,
            reason=reason,
            agent_name=agent_name,
        )


# Every BrainAction maps to exactly one handler. A table rather than a chain of
# conditionals so that "is every action handled?" is a question the tests can
# ask directly, and so adding an action without handling it is visible.
_HANDLERS = {
    BrainAction.DELEGATE: ActionExecutor._delegate,
    BrainAction.HANDLE_SELF: ActionExecutor._handle_self,
    BrainAction.REPORT: ActionExecutor._report,
    BrainAction.ESCALATE: ActionExecutor._escalate,
    BrainAction.UNKNOWN: ActionExecutor._unknown,
}
