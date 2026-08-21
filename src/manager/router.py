"""Routing strategies - the D (Diagnose) phase of the DATA loop.

The Manager no longer knows *how* an agent is chosen; it only knows that some
Router will choose one. Swapping the algorithm therefore means writing a new
Router, not editing the orchestrator.

This is the Strategy pattern: a family of interchangeable algorithms behind one
interface, selected by the caller at wiring time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from src.agents.base_agent import BaseAgent
from src.core.contracts import RoutingDecision, Task


class RoutingUnavailable(RuntimeError):
    """A router could not reach a verdict because a dependency failed.

    This exists to keep two very different situations apart, both of which
    used to arrive as a bare ``None``:

        None                 the router was asked and has no opinion
        RoutingUnavailable   the router could not form an opinion at all

    The distinction is not academic. A model that answers "no suitable agent"
    is working correctly; a model that cannot be reached - an exhausted
    account, a network fault, a revoked key - is not. Collapsing the second
    into the first makes an outage indistinguishable from normal operation,
    which is precisely how a dead integration survives unnoticed in a system
    that degrades gracefully.

    It lives beside Router rather than in the providers package because it is
    a *routing* concept. A Router may fail for reasons that have nothing to do
    with a language model, and callers should not have to import a provider
    module to catch one.
    """


class Router(ABC):
    """Decides which agent should handle a task."""

    @abstractmethod
    def route(self, task: Task, agents: Sequence[BaseAgent]) -> RoutingDecision | None:
        """Return the chosen agent as a RoutingDecision, or None if none fits.

        Candidate agents are passed in rather than the Router reaching for a
        registry. A Router that cannot look anything up can only reason about
        what it was given, which keeps it simple to test: pass a list, assert
        on the decision.
        """


class CapabilityRouter(Router):
    """Chooses the agent whose declared capabilities best match the task text.

    The algorithm is a set intersection: an agent's capabilities are matched
    against the task's word tokens, and the agent with the most overlap wins.
    Ties break alphabetically on agent name so the result is deterministic.

    Note the routing rules are *data* (each agent's capabilities), not code, so
    registering a new agent changes routing without changing this class.
    """

    def route(self, task: Task, agents: Sequence[BaseAgent]) -> RoutingDecision | None:
        tokens = task.tokens
        best: RoutingDecision | None = None

        for agent in agents:
            matched = agent.spec.capabilities & tokens
            score = len(matched)
            if score == 0:
                continue

            if best is None or score > best.score or (
                score == best.score and agent.name < best.agent_name
            ):
                best = RoutingDecision(
                    agent_name=agent.name,
                    score=score,
                    reason=f"matched capabilities: {', '.join(sorted(matched))}",
                )

        return best
