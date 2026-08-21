"""Tests for the Diagnose phase (Router strategies).

Note these test the router directly, with a plain list of agents. No Manager
and no registry are involved - that isolation is the payoff from extracting
the strategy behind an interface.
"""

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import RoutingDecision, Task
from src.manager.router import CapabilityRouter, Router


@pytest.fixture
def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email", "inbox"}),
        EchoAgent(name="beta_agent", capabilities={"calendar"}),
    ]


@pytest.fixture
def router():
    return CapabilityRouter()


def test_is_a_router(router):
    assert isinstance(router, Router)


def test_picks_the_matching_agent(router, agents):
    decision = router.route(Task(description="send an email"), agents)
    assert decision.agent_name == "alpha_agent"
    assert decision.score == 1


def test_returns_none_when_nothing_matches(router, agents):
    assert router.route(Task(description="book a flight"), agents) is None


def test_returns_none_for_an_empty_agent_list(router):
    assert router.route(Task(description="send an email"), []) is None


def test_prefers_the_strongest_match(router, agents):
    decision = router.route(Task(description="email me my inbox"), agents)
    assert decision.agent_name == "alpha_agent"
    assert decision.score == 2


def test_is_case_insensitive(router, agents):
    assert router.route(Task(description="SEND AN EMAIL"), agents).agent_name == "alpha_agent"


def test_matches_whole_words_only(router, agents):
    """'emailing' should not match the capability 'email'."""
    assert router.route(Task(description="stop emailingme"), agents) is None


def test_explains_its_decision(router, agents):
    decision = router.route(Task(description="email me my inbox"), agents)
    assert "email" in decision.reason and "inbox" in decision.reason


def test_ties_break_alphabetically_and_deterministically(router):
    tied = [
        EchoAgent(name="zulu_agent", capabilities={"report"}),
        EchoAgent(name="alpha_agent", capabilities={"report"}),
    ]
    task = Task(description="write a report")
    assert router.route(task, tied).agent_name == "alpha_agent"
    # Same inputs in the opposite order must still give the same answer.
    assert router.route(task, list(reversed(tied))).agent_name == "alpha_agent"


def test_a_custom_router_can_replace_the_default():
    """The extensibility property: any Router works, no Manager change."""

    class AlwaysBetaRouter(Router):
        def route(self, task, agents):
            return RoutingDecision(agent_name="beta_agent", score=99, reason="always beta")

    decision = AlwaysBetaRouter().route(Task(description="send an email"), [])
    assert decision.agent_name == "beta_agent"
