"""The reasoning execution path: think about a task, then act on the thinking.

    Task
      |  diagnose            what the Router would choose - captured as evidence
      |  deliberate          recall memory, then ask the brain      -> BrainDecision
      |  action layer        validate, require Router concurrence   -> ActionResult
      |    -> specialist         through the Manager's own delegation path
      |    -> assessor           through the Manager's own assessment
      |  remember             record the outcome, policy-gated
      v
    CognitiveOutcome

**This does not replace handle_task.** That path stays exactly what it was: a
mechanical D-A-T-A cycle with no model anywhere in it, predictable enough that
routing and assessment can be reasoned about without thinking about what a
model might say. This is a second, explicit path a caller chooses deliberately.
Both end up delegating through the same methods, so a specialist cannot tell
which one invoked it.

Why a separate module rather than a method on ManagerAgent: the action layer
imports ManagerAgent, so the Manager importing the action layer would be a
cycle. That constraint turns out to describe the right architecture anyway -
the Manager knows how to route, delegate, assess and remember, and something
*above* it decides to compose those into a reasoning loop.

Nothing here does any of the work itself. Every line delegates to a collaborator
that already existed:

    routing      Router, via manager.diagnose
    recall       Memory, via manager.deliberate
    reasoning    ManagerBrain, via manager.deliberate
    validation   ActionExecutor
    execution    manager.take_action, reached through the action layer
    assessment   Assessor, reached through the action layer
    retention    manager.remember_delegation

Note what the brain is given and what it is not. It receives the task, the
roster, recalled memory and the Router's view - all as inert text. It receives
no agent, no registry, no store, and no way to run anything. Its answer is a
recommendation that the action layer may refuse, and frequently does.

One cost worth stating plainly: on a DELEGATE decision the Router runs twice -
once here for evidence, once inside the action layer for concurrence. With the
default CapabilityRouter that is a pure, cheap set intersection. With
MANAGER_ROUTER=llm it is two model calls, and a caller pairing an LLM router
with this loop should know that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.contracts import RoutingDecision, Task, TaskResult
from src.manager.actions import ActionExecutor, ActionResult, ActionStatus
from src.manager.brain import BrainAction, BrainDecision
from src.manager.manager import ManagerAgent
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CognitiveOutcome:
    """The whole cycle, kept together so it can be reviewed as one story.

    Every stage is retained rather than collapsed into a final answer: what the
    Router thought, what the brain concluded, what the action layer permitted,
    and what the work returned. A caller that only wants the result can ask for
    ``result``; a caller trying to understand *why* nothing ran needs all four.
    """

    task_id: str
    decision: BrainDecision
    action: ActionResult
    routed_to: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def result(self) -> TaskResult | None:
        """The specialist's assessed result, if any work actually ran."""
        return self.action.result

    @property
    def executed(self) -> bool:
        return self.action.executed

    @property
    def agreed(self) -> bool:
        """Whether the brain's proposal and the Router's choice matched."""
        return (
            self.decision.action is BrainAction.DELEGATE
            and self.decision.delegate_to == self.routed_to
        )

    def describe(self) -> str:
        """A short line for logs. Carries no model prose or task content."""
        routed = self.routed_to or "-"
        return (
            f"router={routed} brain={self.decision.action.value} "
            f"action={self.action.status.value}"
        )


def _routing_note(routed: RoutingDecision | None) -> str:
    """State the Router's view for the brain, as evidence.

    Phrased as an observation about what *would* happen, never as an
    instruction. The brain is entitled to disagree with it - and if it does,
    the action layer refuses and nothing runs, so the disagreement costs a
    mission rather than causing a wrong one.
    """
    if routed is None:
        return "the capability router finds no registered agent suited to this task"
    return (
        f"the capability router would select '{routed.agent_name}' for this task "
        f"({routed.reason})"
    )


class CognitiveLoop:
    """Runs one task through reasoning and, if permitted, through execution."""

    def __init__(
        self, manager: ManagerAgent, executor: ActionExecutor | None = None
    ) -> None:
        """The executor is injectable but defaults to one wrapping this Manager.

        Injectable so a caller can substitute a stricter action layer later
        without this class learning anything new; defaulted so the ordinary
        case needs no ceremony.
        """
        self._manager = manager
        self._executor = executor if executor is not None else ActionExecutor(manager)

    def __repr__(self) -> str:
        return "<CognitiveLoop>"

    def run(self, task: Task) -> CognitiveOutcome:
        """Think about ``task``, then act on the thinking if that is permitted.

        Never raises for an ordinary failure. A brain that breaks, a Router
        that declines, an action the layer refuses and a specialist that
        crashes all arrive as a structured outcome. Interrupts still propagate,
        as everywhere else here.
        """
        log = task_logger(logger, task.id)

        # 1. Diagnose. Not to choose - the action layer will ask again, and it
        #    is that second answer which decides anything - but so the brain
        #    reasons knowing what the mechanical router concluded.
        routed = self._manager.diagnose(task)
        notes = (_routing_note(routed),)

        # 2 and 3. Recall, then deliberate. Both happen inside deliberate(),
        #    in that order: the brain is handed what the Manager remembers
        #    before it forms a view. Recalled records arrive marked as
        #    evidence, never as instructions.
        decision = self._manager.deliberate(task, notes)

        # 4. The action layer decides whether that recommendation may be
        #    carried out, and carries it out through the Manager's own
        #    delegation and assessment if so. A proposal the Router does not
        #    share is refused here, not honoured.
        action = self._executor.execute(decision, task)

        # 5. Record only what actually happened. A refusal is not a delegation
        #    outcome, and recording one would put a mission in memory that
        #    never ran.
        if action.status is ActionStatus.EXECUTED and action.result is not None:
            self._manager.remember_delegation(task, action.result)

        outcome = CognitiveOutcome(
            task_id=task.id,
            decision=decision,
            action=action,
            routed_to=routed.agent_name if routed is not None else None,
            notes=notes,
        )
        log.info("cognitive cycle: %s", outcome.describe())
        return outcome
