"""Pre-Phase Agentic Workflow checkpoint - a genuinely dynamic multi-step
objective loop, built entirely from mechanisms that already exist and are
already real:

    OBJECTIVE
      |  UNDERSTAND / EXECUTE   answer_with_collaboration - exactly the
      |                         same governed, production-reachable path
      |                         every ordinary request already goes
      |                         through: collaboration when a specialist's
      |                         declared capabilities are relevant, the
      |                         Manager's own ordinary D-A-T-A dispatch
      |                         otherwise (which already includes the
      |                         Brain-driven open_conversation fallback).
      |  OBSERVE                the real TaskResult that step produced -
      |                         never assumed, never invented.
      |  VERIFY / ADAPT         ManagerBrain.assess_objective, given the
      |                         objective and everything genuinely
      |                         gathered so far: complete, one more
      v                         concrete step, ask the person, or failed.
    ObjectiveOutcome

**Why this does not use CognitiveLoop/ActionExecutor.** CHECK (this
checkpoint) found that machinery real, tested and already governed - but
its own Router-concurrence check (``ActionExecutor._delegate`` asking
``manager.diagnose()``) is narrower than the routing decision the rest of
the system actually trusts (``manager.decide()``, which additionally
knows about the Manager's own shape-based capability claims and is the
default owner of any work no specialist's declared capabilities match).
Reproduced directly: ``diagnose()`` returns ``None`` for perfectly
ordinary, novel wording with no capability-word overlap at all - exactly
the open-ended-reasoning case this checkpoint's own "no hardcoded
example dependency" requirement (Test F) exists to cover - which makes
``HANDLE_SELF`` refuse work the ordinary production path would happily
take on. Activating ``CognitiveLoop`` today would inherit that gap, and
fixing it there would mean editing a component this project's own
Trust-in-Stages rule keeps deliberately unreachable from production, for
no benefit this loop needs: ``answer_with_collaboration`` already uses
the correct, full ``decide()`` for every step, is already
production-reachable and already tested. So each step here is executed
through it, unchanged, and ``CognitiveLoop``/``ActionExecutor`` remain
exactly as staged as they were before this file existed - not activated,
not modified, not depended upon.

**Why this is not a workflow catalogue.** There is exactly one function,
``pursue_objective``, and it contains no branch keyed on subject, topic
or wording. What happens next at every point - whether another step is
needed, what that step should attempt, whether to ask the person
something, or whether to report success, partial progress or failure -
is decided by ``ManagerBrain.assess_objective`` from the objective and
the real evidence gathered, fresh each time. The loop itself only
enforces the bound (``max_steps``) and the honesty rules (never invent a
result, never call something complete that was not verified against
real evidence).

**Half Brain is unaffected, and costs nothing extra.** Without a real
Brain, there is no way to judge progress without fabricating it, so this
stops after exactly the one step an ordinary request already runs
today - identical output, identical cost, identical honesty. Full Brain
pays one additional narrow judgement call per turn, the same shape and
the same class of cost ``discern_retention``/``discern_ambiguity``/
``discern_activity_start`` already established as acceptable for a
single-purpose progress question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.core.contracts import Task, TaskResult
from src.manager.collaboration_entry import answer_with_collaboration
from src.manager.manager import ManagerAgent
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# A small, fixed safety bound - Trust-in-Stages applied to this loop's own
# autonomy rather than to a separate mechanism: however many steps the
# Brain believes are needed, this stops and honestly reports partial
# progress well short of anything resembling unrestricted iteration.
DEFAULT_MAX_STEPS = 5


class ObjectiveStatus(str, Enum):
    """What became of an objective once it was actually pursued."""

    COMPLETED = "completed"    # verified against real evidence
    PARTIAL = "partial"        # genuine progress; not confirmed complete
    NEEDS_INPUT = "needs_input"  # honestly paused, waiting on the person
    FAILED = "failed"          # could not be completed with what exists

    @property
    def label(self) -> str:
        return {
            "completed": "COMPLETED",
            "partial": "PARTIAL",
            "needs_input": "NEEDS INPUT",
            "failed": "FAILED",
        }[self.value]


@dataclass(frozen=True)
class ObjectiveStep:
    """One attempt within the loop: the task it was given, and the real
    result that came back - never collapsed into a summary, so a caller
    that wants the full story can read every step exactly as it ran.

    **Goal-level planning checkpoint: two read-only views, never a
    second store.** Both properties below are *derived*, purely for
    observability (Control Center, a caller trying to understand what a
    pursuit actually did) - neither one changes what
    ``pursue_objective`` decides or executes, and neither duplicates a
    rule that already exists elsewhere. ``operation`` reads the
    identical payload ``connected_action`` (``src/manager/
    capabilities.py``) already populates; ``declared_values`` reuses
    the identical, single explicit-value extraction
    ``connected_action`` itself resolves a real operation parameter
    from (``_declared_argument_values``) - imported lazily, the same
    reason every other cross-module import in this file already is.
    """

    task: Task
    result: TaskResult

    def describe(self) -> str:
        return f"{self.task.description!r} -> {self.result.status.value}"

    @property
    def operation(self) -> "str | None":
        """Which discovered operation this step actually reached, if
        any - ``None`` for a step ``connected_action`` never answered
        (handled by a different capability, or refused before ever
        selecting one)."""
        payload = self.result.payload or {}
        operation = payload.get("operation")
        return operation if isinstance(operation, str) else None

    @property
    def declared_values(self) -> "dict[str, str]":
        """Every explicit ``name=value`` this step's own request text
        stated - read here only for observability, never a second
        extraction rule and never used to decide anything this class
        itself does."""
        from src.manager.capabilities import _declared_argument_values

        return _declared_argument_values(self.task.description)


@dataclass(frozen=True)
class ObjectiveOutcome:
    """The whole pursuit, kept together the same way CognitiveOutcome
    keeps a single reasoning cycle together: every step is retained, not
    collapsed, so a caller trying to understand *why* an objective ended
    where it did has the full story, not just a final line."""

    objective: str
    status: ObjectiveStatus
    result: TaskResult
    steps: tuple[ObjectiveStep, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.status is ObjectiveStatus.COMPLETED

    def describe(self) -> str:
        return f"objective={self.status.value} steps={len(self.steps)}"

    def step_summaries(self) -> "tuple[str, ...]":
        """One line per step: the operation it reached (or
        "conversation" for a step that reached something else), and -
        the genuine dependency evidence this checkpoint adds - whether
        any value it explicitly declared actually appeared in an
        *earlier* step's own real output. Built entirely from what each
        step already recorded; nothing here is re-executed, inferred
        from wording, or guessed. A step with no operation and nothing
        carried forward reports just its own label - the identical
        shape a genuinely simple, one-step pursuit already has.
        """
        summaries: "list[str]" = []
        for index, step in enumerate(self.steps):
            label = step.operation or "conversation"
            carried = [
                f"{name}={value}"
                for name, value in step.declared_values.items()
                if any(
                    value and value in (earlier.result.output or "")
                    for earlier in self.steps[:index]
                )
            ]
            if carried:
                summaries.append(f"{label} (using {', '.join(carried)} from an earlier step)")
            else:
                summaries.append(label)
        return tuple(summaries)


def _agent_name(manager: ManagerAgent) -> str:
    return manager.own_skills.name if manager.own_skills is not None else "manager"


def _evidence_line(step_number: int, step_task: Task, result: TaskResult) -> str:
    """One step's real outcome, phrased as evidence for the next
    judgement - never an instruction, the same "notes, not instructions"
    contract every Brain consultation in this project already honours."""
    if result.succeeded and result.output.strip():
        return f"Step {step_number} ({step_task.description!r}) succeeded: {result.output}"
    return (
        f"Step {step_number} ({step_task.description!r}) did not produce a "
        f"usable result: {result.error or 'no output was produced'}"
    )


def _partial_summary(steps: tuple[ObjectiveStep, ...], reason: str) -> str:
    """An honest account of a stopped-short pursuit: what was actually
    accomplished, never presented as the objective being done."""
    findings = [
        step.result.output.strip()
        for step in steps
        if step.result.succeeded and step.result.output.strip()
    ]
    if not findings:
        return f"I was not able to make confirmed progress on this ({reason})."
    joined = " ".join(findings)
    return (
        f"I made some progress on this but cannot confirm the objective was "
        f"fully completed ({reason}). What I found so far: {joined}"
    )


def pursue_objective(
    manager: ManagerAgent, task: Task, max_steps: int = DEFAULT_MAX_STEPS
) -> ObjectiveOutcome:
    """Pursue ``task`` as an objective that may take one step or several,
    reasoning dynamically about what is actually needed rather than
    following any fixed sequence.

    Every step is executed through ``answer_with_collaboration`` -
    unchanged, unmodified, exactly the path an ordinary single request
    already takes - so this function adds nothing to what any one step
    can do or who it can reach; it only decides, honestly and
    dynamically, whether another step is warranted.

    Never raises for an ordinary failure. A brain that cannot judge
    progress, a step that fails, or the bound being reached all produce a
    structured, honest outcome - never a fabricated success and never an
    exception escaping to the caller.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    log = task_logger(logger, task.id)

    first_result = answer_with_collaboration(manager, task)
    steps: list[ObjectiveStep] = [ObjectiveStep(task, first_result)]

    if not manager.can_reason:
        # Half Brain: no genuine way to judge whether more is needed
        # without guessing, so this stops exactly where an ordinary
        # single request already would - identical output, identical
        # cost, identical honesty.
        status = ObjectiveStatus.COMPLETED if first_result.succeeded else ObjectiveStatus.FAILED
        outcome = ObjectiveOutcome(
            objective=task.description, status=status, result=first_result, steps=tuple(steps)
        )
        log.info("objective pursuit (half brain): %s", outcome.describe())
        return outcome

    evidence: list[str] = [_evidence_line(1, task, first_result)]
    step_number = 1

    while True:
        verdict = manager.brain.assess_objective(task, tuple(evidence))

        if not verdict.decided:
            # No positive evidence the objective needs more, only a
            # judgement that could not be formed - a provider hiccup, a
            # malformed response, an older/scripted Brain double that has
            # no opinion about this new, narrow question. Failing open to
            # the latest step's own real result is the safe reading: it
            # is what every ordinary single-step request already
            # produced before this loop existed, and inventing a
            # "partial" disclaimer over a result nothing has actually
            # cast doubt on would be a fabrication in its own right.
            outcome = _fallback_to_latest(task, steps)
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        if verdict.status == "complete":
            if step_number == 1:
                # Nothing beyond the ordinary single step happened - the
                # step's own real result is returned unchanged, never
                # replaced with a separately-composed "final answer" that
                # risks paraphrasing (or worse, embellishing) a response
                # that was already correct and complete.
                outcome = _fallback_to_latest(task, steps)
            else:
                outcome = ObjectiveOutcome(
                    objective=task.description,
                    status=ObjectiveStatus.COMPLETED,
                    result=TaskResult.success(task, _agent_name(manager), verdict.final_answer),
                    steps=tuple(steps),
                )
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        if verdict.status == "needs_input":
            # The same honest, non-fabricated pause every clarification
            # in this project already reports as a success: the system
            # correctly recognised it needs something only the person can
            # supply, which is the request working as intended, not a
            # failure - see src/manager/guided_interaction.py's identical
            # TaskResult.success(...) treatment of an asked question.
            outcome = ObjectiveOutcome(
                objective=task.description,
                status=ObjectiveStatus.NEEDS_INPUT,
                result=TaskResult.success(task, _agent_name(manager), verdict.question),
                steps=tuple(steps),
            )
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        if verdict.status == "failed":
            outcome = ObjectiveOutcome(
                objective=task.description,
                status=ObjectiveStatus.FAILED,
                result=TaskResult.failure(
                    task,
                    _agent_name(manager),
                    verdict.final_answer or "the objective could not be completed with what is available",
                ),
                steps=tuple(steps),
            )
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        if verdict.status != "continue" or not verdict.next_step:
            # The model answered, but not usably (an invalid status, or a
            # "continue" naming no actual next step) - the identical
            # honest-absence floor as an undecided verdict, and the
            # identical fail-open reasoning.
            outcome = _fallback_to_latest(task, steps)
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        if step_number >= max_steps:
            outcome = _stop(
                task,
                steps,
                f"stopped after {max_steps} step(s) without confirming the objective was complete",
            )
            log.info("objective pursuit: %s", outcome.describe())
            return outcome

        step_number += 1
        next_task = Task(description=verdict.next_step)
        latest_result = answer_with_collaboration(manager, next_task)
        steps.append(ObjectiveStep(next_task, latest_result))
        evidence.append(_evidence_line(step_number, next_task, latest_result))


def _fallback_to_latest(task: Task, steps: list[ObjectiveStep]) -> ObjectiveOutcome:
    """Fail open to the most recent step's own real, unmodified result -
    used whenever progress could not be judged at all, never to signal a
    confirmed shortfall (see ``_stop`` for that). For an ordinary,
    single-step request (the overwhelming majority) this reproduces
    exactly the result that step already produced, unchanged."""
    latest = steps[-1].result
    status = ObjectiveStatus.COMPLETED if latest.succeeded else ObjectiveStatus.FAILED
    return ObjectiveOutcome(
        objective=task.description, status=status, result=latest, steps=tuple(steps)
    )


def _stop(task: Task, steps: list[ObjectiveStep], reason: str) -> ObjectiveOutcome:
    """The one place a pursuit ends short on a *confirmed* shortfall - the
    safety bound was reached while the Brain still said more was needed.
    An honest partial report, built only from what was genuinely
    gathered, never a fabricated completion."""
    summary = _partial_summary(tuple(steps), reason)
    return ObjectiveOutcome(
        objective=task.description,
        status=ObjectiveStatus.PARTIAL,
        result=TaskResult.failure(task, steps[-1].result.agent_name, summary),
        steps=tuple(steps),
    )
