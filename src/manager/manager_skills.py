"""The Manager's own capabilities, expressed as an ordinary agent.

This is what makes "can I handle this myself?" a real question rather than a
hardcoded branch. ManagerSkills implements BaseAgent and is registered in the
same registry as every specialist, so the existing capability router decides
self-vs-delegate by the same scoring it already uses. No parallel mechanism,
no hidden self-execution path, and no change to Router, Registry or Assessor.

Its area of responsibility is coordination: knowing what it and its specialists
can do, and reporting on that. It does not attempt a specialist's work - and it
is not obliged to delegate merely because a specialist exists.

The spec comes from IDENTITY.md, so what the Manager takes on itself is a
document edit rather than a code change.
"""

from __future__ import annotations

from typing import Callable, Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult
from src.services.identity.identity import AgentIdentity
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)


class ManagerSkills(BaseAgent):
    """Work the Manager performs directly."""

    def __init__(
        self,
        identity: AgentIdentity,
        roster: Callable[[], Sequence[BaseAgent]] | None = None,
    ) -> None:
        """``roster`` supplies the currently registered agents.

        A callable rather than a registry reference: the Manager registers this
        object *into* the registry, so holding the registry directly would be a
        cycle. A late-bound callable keeps the dependency one-way and lets the
        roster answer reflect agents registered after construction.
        """
        self._identity = identity
        self._roster = roster or (lambda: ())

    @property
    def spec(self) -> AgentSpec:
        return self._identity.to_spec()

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    def execute(self, task: Task) -> TaskResult:
        log = task_logger(logger, task.id)
        log.debug("manager handling '%s' itself", task.description)

        others = [a for a in self._roster() if a.name != self.name]
        own = sorted(self.spec.capabilities)

        lines = [
            f"{self._identity.name}: {self._identity.role or 'orchestrator'}",
            f"  handles directly : {', '.join(own)}",
        ]
        if others:
            lines.append(f"  specialists ({len(others)}):")
            for agent in sorted(others, key=lambda a: a.name):
                caps = ", ".join(sorted(agent.spec.capabilities))
                lines.append(f"    - {agent.name}: {caps}")
        else:
            lines.append("  specialists      : none registered")

        payload = {
            "identity": self._identity.name,
            "own_capabilities": tuple(own),
            "specialists": {
                agent.name: tuple(sorted(agent.spec.capabilities)) for agent in others
            },
        }
        return TaskResult.success(task, self.name, "\n".join(lines), payload=payload)
