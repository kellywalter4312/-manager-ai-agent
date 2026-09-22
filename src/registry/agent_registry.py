"""The agent registry: a lookup table of available agents.

Separating this from the Manager is a separation-of-concerns decision. The
Manager's job is to run the DATA loop; holding a collection of agents is a
different job, so it lives in its own type that can be tested on its own.

Scope note: this registry only registers, retrieves and lists. Matching a task
to an agent is routing logic and belongs to the Manager, not here.
"""

from __future__ import annotations

from src.agents.base_agent import BaseAgent
from src.utils.logging import get_logger

logger = get_logger(__name__)


class AgentRegistry:
    """An in-memory collection of registered agents, keyed by agent name.

    Dict lookup by name is O(1). The registry never hardcodes which agents
    exist: agents are added at runtime, which is what makes the system
    extensible without editing the Manager.
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Add an agent. The agent supplies its own name via its spec.

        Validating at the boundary means bad input is rejected here, once,
        rather than causing a confusing failure deep inside the DATA loop.
        """
        if not isinstance(agent, BaseAgent):
            raise TypeError(f"Expected a BaseAgent, got {type(agent).__name__}")

        name = agent.spec.name
        if name in self._agents:
            raise ValueError(f"An agent named '{name}' is already registered")

        self._agents[name] = agent
        # Registration happens outside any single task, so this record carries
        # no task id - it describes the system, not a task's journey.
        logger.info(
            "registered '%s' with capabilities: %s",
            name,
            ", ".join(sorted(agent.spec.capabilities)),
        )

    def unregister(self, name: str) -> BaseAgent | None:
        """Remove the agent with this name and return it, or None if no agent
        by that name is registered - an owner withdrawing a specialist they
        created. What the agent kept stays wherever its own memory keeps it."""
        agent = self._agents.pop(name, None)
        if agent is not None:
            logger.info("unregistered '%s'", name)
        return agent

    def get(self, name: str) -> BaseAgent | None:
        """Return the agent with this name, or None if it is not registered."""
        return self._agents.get(name)

    def all_agents(self) -> list[BaseAgent]:
        """Every registered agent, in registration order."""
        return list(self._agents.values())

    def __contains__(self, name: object) -> bool:
        return name in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    def __repr__(self) -> str:
        return f"<AgentRegistry agents={sorted(self._agents)}>"
