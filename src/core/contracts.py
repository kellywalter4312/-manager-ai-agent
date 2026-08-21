"""Shared data contracts for the orchestration system.

This module is the innermost layer of the architecture: it depends on nothing
except the standard library, and every other layer speaks in these types.
Keeping it free of I/O (no network, no disk, no logging) is what makes the
rest of the system straightforward to test.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# Splits free text into lowercase word tokens: "Send an e-mail!" -> {send, an, e, mail}
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> frozenset[str]:
    """Reduce arbitrary text to a set of lowercase word tokens."""
    return frozenset(_TOKEN_PATTERN.findall(text.lower()))


# The agreed key under which any specialist reports structured findings in a
# TaskResult payload. Domain-neutral on purpose: the Manager must be able to
# ask "did this turn anything up?" without knowing what the specialist hunts
# for - opportunities, invoices, alerts or anything added later.
FINDINGS_KEY = "findings"


class TaskStatus(str, Enum):
    """Every possible outcome of a task, enumerated in one place.

    Inheriting from ``str`` means a TaskStatus is also a plain string, so it
    prints and serializes cleanly while still being a closed set of values.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    NO_AGENT_FOUND = "no_agent_found"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Task:
    """A unit of work handed to the Manager.

    ``frozen=True`` makes instances immutable: once a Task exists, no agent can
    quietly mutate it mid-flight. That keeps the task a stable reference point
    when the Manager assesses the result afterwards.
    """

    description: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("Task.description must not be empty")

    @property
    def tokens(self) -> frozenset[str]:
        """The description as word tokens, used for capability matching."""
        return tokenize(self.description)


@dataclass(frozen=True)
class AgentSpec:
    """An agent's self-description: its identity and what it claims to do.

    This is the AGENT framework expressed as data:
      Aim        -> description
      Give ID    -> name
      Establish  -> capabilities
    Because each agent carries its own spec, the Manager never needs to hold a
    hardcoded list of who does what.
    """

    name: str
    description: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("AgentSpec.name must not be empty")
        if not self.capabilities:
            raise ValueError(f"AgentSpec '{self.name}' must declare at least one capability")

        # Normalize so matching is predictable regardless of how an agent author
        # wrote its capabilities. object.__setattr__ is the sanctioned escape
        # hatch for assigning to a frozen dataclass during __post_init__.
        normalized = frozenset(c.strip().lower() for c in self.capabilities if c.strip())
        if not normalized:
            raise ValueError(f"AgentSpec '{self.name}' has only blank capabilities")
        object.__setattr__(self, "capabilities", normalized)

    def match_score(self, tokens: frozenset[str]) -> int:
        """How many declared capabilities appear in the given tokens."""
        return len(self.capabilities & tokens)


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of the Diagnose phase: who should handle a task, and why.

    Returning a small object instead of a bare agent name means a routing
    choice can explain itself. ``score`` says how strong the match was and
    ``reason`` says what drove it, which is what makes routing debuggable
    (and, later, loggable) rather than a black box.
    """

    agent_name: str
    score: int
    reason: str

    def __post_init__(self) -> None:
        if not self.agent_name.strip():
            raise ValueError("RoutingDecision.agent_name must not be empty")
        if self.score <= 0:
            raise ValueError("RoutingDecision.score must be positive; use None for no match")


@dataclass(frozen=True)
class TaskResult:
    """The single shape every agent must return.

    A fixed result shape is what lets the Manager assess outcomes without
    knowing anything about the agent that produced them.
    """

    task_id: str
    status: TaskStatus
    agent_name: str | None = None
    output: str = ""
    error: str | None = None

    # Optional structured result, added in Milestone 8. ``output`` stays the
    # human-readable rendering; this carries the same information in a form the
    # Manager can actually reason about - assessing a text blob is guesswork,
    # assessing structure is not. Defaulted and additive: every existing caller
    # goes through the factory methods below and is unaffected.
    payload: Mapping[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is TaskStatus.COMPLETED

    @classmethod
    def success(
        cls,
        task: Task,
        agent_name: str,
        output: str,
        payload: Mapping[str, Any] | None = None,
    ) -> TaskResult:
        return cls(
            task_id=task.id,
            status=TaskStatus.COMPLETED,
            agent_name=agent_name,
            output=output,
            payload=payload,
        )

    @classmethod
    def failure(cls, task: Task, agent_name: str | None, error: str) -> TaskResult:
        return cls(
            task_id=task.id,
            status=TaskStatus.FAILED,
            agent_name=agent_name,
            error=error,
        )

    @classmethod
    def no_agent(cls, task: Task, error: str) -> TaskResult:
        return cls(
            task_id=task.id,
            status=TaskStatus.NO_AGENT_FOUND,
            agent_name=None,
            error=error,
        )

    @classmethod
    def rejected(cls, task: Task, agent_name: str | None, error: str) -> TaskResult:
        return cls(
            task_id=task.id,
            status=TaskStatus.REJECTED,
            agent_name=agent_name,
            error=error,
        )
