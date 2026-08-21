"""A bounded observe-think-act cycle for a specialist.

    observe -> think -> request a tool -> execute -> observe -> think -> finish

Three collaborators, and the split is the point:

    ToolUsingBrain  decides what to try next          (reasoning)
    AgentLoop       validates and sequences it        (orchestration)
    ToolSet         decides what may run, and runs it (capability)

A brain says "I would like to call inspect_posting". It does not call anything:
it holds no toolset, receives no tool, and its answer is a description of an
intention. The loop takes that description to the toolset, which decides
whether it is permitted. Requesting is not doing.

**Bounded by construction, not by discipline.** The cycle is a ``for`` over a
fixed range - there is no ``while`` in this module, and a test asserts the
absence at the syntax level. A brain that never says it is finished runs
exactly max_iterations times and then stops with EXHAUSTED. A brain that keeps
asking for the same failing tool does the same. There is no input, malicious or
confused, that produces an unbounded run.

Why a separate brain interface rather than extending SpecialistBrain: that
contract answers "what do you make of this?" and returns an Insight, which
deliberately has no action vocabulary. A tool request is a different kind of
answer, and stuffing one into Insight would give every specialist reflection an
action field it should not have. Brains that reason without tools keep the old
interface untouched.

What a specialist brain still cannot ask for: delegating, escalating, routing,
or running another agent. There is no step kind for any of them - only USE_TOOL
and FINISH exist - so those requests cannot be expressed here at all. They are
Manager decisions and they stay there.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from src.agents.brain import Insight
from src.agents.tools.tool import ToolResult, ToolSpec, ToolStatus
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# How many reasoning steps a specialist gets before the loop stops it. Small on
# purpose: a domain question that genuinely needs more than a handful of tool
# calls is usually a question that should have been split up. Raising it is a
# deliberate act by whoever builds the specialist.
DEFAULT_MAX_ITERATIONS = 5


class LoopError(Exception):
    """Raised when a loop contract cannot be formed."""


class StepKind(str, Enum):
    """What a brain wants to happen next. Exactly two things are expressible."""

    USE_TOOL = "use_tool"
    FINISH = "finish"


class Termination(str, Enum):
    """Why the loop stopped."""

    FINISHED = "finished"          # the brain said it was done
    EXHAUSTED = "exhausted"        # the iteration limit was reached
    BRAIN_FAILED = "brain_failed"  # reasoning broke; nothing further attempted
    INVALID_STEP = "invalid_step"  # the brain answered with something unusable

    @property
    def is_clean(self) -> bool:
        return self is Termination.FINISHED


@dataclass(frozen=True)
class ToolRequest:
    """A brain's request to call one tool. A description, never a call."""

    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        tool = str(self.tool).strip()
        if not tool:
            raise LoopError("a tool request must name a tool")
        object.__setattr__(self, "tool", tool)

        if not isinstance(self.arguments, Mapping):
            raise LoopError(
                f"tool arguments must be a mapping, got {type(self.arguments).__name__}"
            )
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class NextStep:
    """Either a tool to try, or a conclusion. Never both, never neither."""

    kind: StepKind
    request: ToolRequest | None = None
    insight: Insight | None = None

    def __post_init__(self) -> None:
        if self.kind is StepKind.USE_TOOL and self.request is None:
            raise LoopError("a USE_TOOL step must name a tool request")
        if self.kind is StepKind.FINISH and self.insight is None:
            raise LoopError("a FINISH step must carry an insight")
        if self.kind is StepKind.USE_TOOL and self.insight is not None:
            # A step that both acts and concludes would leave the loop unsure
            # whether to continue.
            raise LoopError("a USE_TOOL step cannot also carry a conclusion")

    @classmethod
    def use_tool(cls, tool: str, **arguments: Any) -> "NextStep":
        return cls(kind=StepKind.USE_TOOL, request=ToolRequest(tool=tool, arguments=arguments))

    @classmethod
    def finish(cls, insight: Insight) -> "NextStep":
        return cls(kind=StepKind.FINISH, insight=insight)


@dataclass(frozen=True)
class LoopStep:
    """One iteration, recorded so a run can be understood afterwards."""

    iteration: int
    tool: str
    permitted: bool
    status: ToolStatus
    detail: str = ""

    def describe(self) -> str:
        """A short line. Carries no tool output - that may be domain content."""
        return (
            f"#{self.iteration} {self.tool} "
            f"{'permitted' if self.permitted else 'refused'} {self.status.value}"
        )


@dataclass(frozen=True)
class LoopOutcome:
    """Everything that happened, kept together so it can be reviewed as one run."""

    task_id: str
    termination: Termination
    reason: str
    insight: Insight | None = None
    steps: tuple[LoopStep, ...] = field(default_factory=tuple)

    @property
    def finished(self) -> bool:
        return self.termination.is_clean

    @property
    def tool_calls(self) -> int:
        return len(self.steps)

    def describe(self) -> str:
        return (
            f"{self.termination.value} after {self.tool_calls} tool call(s): "
            f"{self.reason}"
        )


class ToolUsingBrain(ABC):
    """A brain that can work through a problem in steps, using tools.

    Separate from SpecialistBrain, not a replacement for it. A specialist may
    have one, the other, or neither.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used for provenance and logs."""

    @abstractmethod
    def next_step(
        self,
        task: Task,
        tools: Sequence[ToolSpec] = (),
        notes: Sequence[str] = (),
        observations: Sequence[ToolResult] = (),
    ) -> NextStep:
        """Decide what should happen next.

        ``tools`` describes what is available, using each tool's own spec - so
        a brain can choose without ever holding something callable.

        ``notes`` is inert context, typically the specialist's recalled memory.
        ``observations`` is what previous tool calls returned, in order.

        Implementations should return a step rather than raise. The loop guards
        this boundary anyway, because a brain is injected and may be anything.
        """


class AgentLoop:
    """Runs a bounded reasoning cycle for one specialist.

    Holds a brain and a toolset and nothing else. There is no manager here, no
    router, no action layer, no registry and no memory - a loop cannot reach
    any of them, so a specialist working inside one cannot either.
    """

    def __init__(
        self,
        brain: ToolUsingBrain,
        tools: ToolSet | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        if max_iterations <= 0:
            raise LoopError(f"max_iterations must be positive, got {max_iterations}")

        self._brain = brain
        self._tools = tools if tools is not None else ToolSet()
        self.max_iterations = max_iterations

    def __repr__(self) -> str:
        return f"<AgentLoop max_iterations={self.max_iterations}>"

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self._tools.names()

    def run(
        self, task: Task, notes: Sequence[str] = ()
    ) -> LoopOutcome:
        """Think, act and observe until the brain finishes or the budget runs out.

        Never raises for an ordinary failure. A brain that breaks, a tool that
        is refused and a tool that fails all become part of the recorded run.
        Interrupts still propagate.
        """
        log = task_logger(logger, task.id)
        specs = self._tools.specs()
        observations: list[ToolResult] = []
        steps: list[LoopStep] = []

        # A for-loop, not a while. The budget is spent whatever happens inside,
        # so no answer from the brain can extend the run.
        for iteration in range(1, self.max_iterations + 1):
            try:
                step = self._brain.next_step(task, specs, tuple(notes), tuple(observations))
            except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
                log.exception("brain raised %s", type(exc).__name__)
                return self._stop(
                    task,
                    Termination.BRAIN_FAILED,
                    f"reasoning failed: {type(exc).__name__}: {exc}",
                    steps,
                )

            if not isinstance(step, NextStep):
                log.error("brain returned %s, not a NextStep", type(step).__name__)
                return self._stop(
                    task,
                    Termination.INVALID_STEP,
                    f"reasoning returned {type(step).__name__}, which is not a step",
                    steps,
                )

            if step.kind is StepKind.FINISH:
                outcome = LoopOutcome(
                    task_id=task.id,
                    termination=Termination.FINISHED,
                    reason="the specialist finished",
                    insight=step.insight,
                    steps=tuple(steps),
                )
                log.info("loop: %s", outcome.describe())
                return outcome

            # A tool request. The brain asked; the toolset decides.
            request = step.request
            result = self._tools.invoke(request.tool, request.arguments)

            observations.append(result)
            steps.append(
                LoopStep(
                    iteration=iteration,
                    tool=request.tool,
                    permitted=result.status.ran,
                    status=result.status,
                    detail=result.error or "",
                )
            )
            log.debug("loop step %s", steps[-1].describe())

        return self._stop(
            task,
            Termination.EXHAUSTED,
            f"stopped after the maximum of {self.max_iterations} step(s) "
            f"without the specialist finishing",
            steps,
        )

    def _stop(
        self,
        task: Task,
        termination: Termination,
        reason: str,
        steps: list[LoopStep],
    ) -> LoopOutcome:
        """End the run without a conclusion, and say why.

        No Insight is invented. A run that did not reach one has not reached
        one, and manufacturing something here would present a stopped loop as
        a considered answer.
        """
        outcome = LoopOutcome(
            task_id=task.id,
            termination=termination,
            reason=reason,
            insight=None,
            steps=tuple(steps),
        )
        task_logger(logger, task.id).warning("loop: %s", outcome.describe())
        return outcome
