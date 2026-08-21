"""The contract every specialized agent must satisfy."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.contracts import AgentSpec, Task, TaskResult


class BaseAgent(ABC):
    """Abstract base class defining the agent interface.

    Two required members:
      - ``spec``:    who the agent is and what it can do (declared, not inferred)
      - ``execute``: do the work and return a TaskResult

    The Manager only ever depends on this abstraction, never on a concrete
    agent class. That is the Dependency Inversion Principle: high-level policy
    (orchestration) does not depend on low-level detail (any specific agent).
    """

    @property
    @abstractmethod
    def spec(self) -> AgentSpec:
        """The agent's identity and declared capabilities."""

    @abstractmethod
    def execute(self, task: Task) -> TaskResult:
        """Perform the task and return a result in the standard shape."""

    @property
    def name(self) -> str:
        """Convenience accessor so callers can avoid reaching through .spec."""
        return self.spec.name

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
