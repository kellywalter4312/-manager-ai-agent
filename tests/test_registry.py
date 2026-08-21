"""Tests for the agent registry."""

import pytest

from src.agents.echo_agent import EchoAgent
from src.registry.agent_registry import AgentRegistry


def test_starts_empty():
    assert len(AgentRegistry()) == 0


def test_register_then_retrieve():
    registry = AgentRegistry()
    agent = EchoAgent(name="alpha", capabilities={"email"})
    registry.register(agent)

    assert registry.get("alpha") is agent
    assert "alpha" in registry
    assert len(registry) == 1


def test_get_returns_none_for_unknown_agent():
    assert AgentRegistry().get("nobody") is None


def test_rejects_duplicate_names():
    registry = AgentRegistry()
    registry.register(EchoAgent(name="alpha", capabilities={"email"}))

    with pytest.raises(ValueError):
        registry.register(EchoAgent(name="alpha", capabilities={"other"}))


def test_rejects_objects_that_are_not_agents():
    with pytest.raises(TypeError):
        AgentRegistry().register("not an agent")


def test_lists_all_agents_in_registration_order():
    registry = AgentRegistry()
    first = EchoAgent(name="alpha", capabilities={"email"})
    second = EchoAgent(name="beta", capabilities={"calendar"})
    registry.register(first)
    registry.register(second)

    assert registry.all_agents() == [first, second]
