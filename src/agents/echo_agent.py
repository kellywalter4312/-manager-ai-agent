"""A generic test-double agent used to prove the orchestration loop works.

This is deliberately NOT a domain agent. It has no subject-matter behaviour of
its own; its identity and capabilities are supplied at construction time. That
makes it usable as a stand-in for any future specialized agent, in tests and in
the demo, without the Manager knowing anything about email, coding, or any
other domain.

Real specialized agents come later. They will implement the same BaseAgent
interface, and the Manager will not need to change to accommodate them.
"""

from __future__ import annotations

from typing import Iterable

from src.agents.base_agent import BaseAgent
from src.core.contracts import AgentSpec, Task, TaskResult


class EchoAgent(BaseAgent):
    """An agent that reports back what it was asked to do.

    Dependency injection: the spec is passed in rather than hardcoded, so one
    class can play many roles. Tests can register several EchoAgents with
    different capabilities to exercise routing.
    """

    def __init__(
        self,
        name: str,
        capabilities: Iterable[str],
        description: str = "A generic echo agent used for testing orchestration.",
    ) -> None:
        self._spec = AgentSpec(
            name=name,
            description=description,
            capabilities=frozenset(capabilities),
        )

    @property
    def spec(self) -> AgentSpec:
        return self._spec

    def execute(self, task: Task) -> TaskResult:
        return TaskResult.success(
            task=task,
            agent_name=self.name,
            output=f"[echo:{self.name}] handled task: {task.description}",
        )
