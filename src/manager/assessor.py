"""Assessment strategies - the final A (Assess) phase of the DATA loop.

An agent's word that it succeeded is not proof that it did. The Assessor is the
Manager's quality gate: it inspects what came back before the Manager passes it
on to the caller.

Like Router, this is the Strategy pattern. Today the only strategy checks the
*structure* of a result. A later strategy could check its *meaning*, and the
Manager would not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.contracts import Task, TaskResult, TaskStatus


class Assessor(ABC):
    """Validates what an agent returned for a task."""

    @abstractmethod
    def assess(self, task: Task, agent_name: str, result: object) -> TaskResult:
        """Return a trustworthy TaskResult for this task.

        ``result`` is typed ``object`` on purpose: the whole point of this
        component is that we do not yet trust it to be a TaskResult. Narrowing
        that type is this method's job.

        The agent is identified by name rather than passed as an object, so an
        Assessor depends only on the contracts module - not on BaseAgent.
        """


class ContractAssessor(Assessor):
    """Checks that a result structurally honours the agent contract.

    Three failure modes, each turned into an explicit REJECTED result rather
    than an exception, so the Manager's caller always receives a TaskResult:

      1. wrong type       - the agent did not return a TaskResult at all
      2. wrong task       - the result belongs to a different task
      3. empty success    - the agent claimed success with nothing to show

    Rejecting is not the same as failing. FAILED means the agent tried and the
    work did not succeed; REJECTED means the agent broke the contract.
    """

    def assess(self, task: Task, agent_name: str, result: object) -> TaskResult:
        if not isinstance(result, TaskResult):
            return TaskResult.rejected(
                task,
                agent_name=agent_name,
                error=f"Agent returned {type(result).__name__}, expected TaskResult.",
            )

        if result.task_id != task.id:
            return TaskResult.rejected(
                task,
                agent_name=agent_name,
                error=(
                    f"Agent returned a result for task '{result.task_id}', "
                    f"expected '{task.id}'."
                ),
            )

        if result.status is TaskStatus.COMPLETED and not result.output:
            return TaskResult.rejected(
                task,
                agent_name=agent_name,
                error="Agent reported success but produced no output.",
            )

        return result
