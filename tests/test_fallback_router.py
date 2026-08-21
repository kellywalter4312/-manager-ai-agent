"""Tests for the fallback composite router."""

import logging

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import RoutingDecision, Task
from src.manager.fallback_router import FallbackRouter
from src.manager.router import CapabilityRouter, Router


class RecordingRouter(Router):
    """A router with a scripted answer that remembers whether it was called."""

    def __init__(self, decision=None, fail=False):
        self._decision = decision
        self._fail = fail
        self.calls = 0

    def route(self, task, agents):
        self.calls += 1
        if self._fail:
            raise RuntimeError("router exploded")
        return self._decision


@pytest.fixture
def agents():
    return [EchoAgent(name="alpha_agent", capabilities={"email"})]


@pytest.fixture
def task():
    return Task(description="send an email")


def decision(name="alpha_agent", reason="scripted"):
    return RoutingDecision(agent_name=name, score=1, reason=reason)


class TestInterface:
    def test_is_a_router(self):
        assert isinstance(
            FallbackRouter(CapabilityRouter(), CapabilityRouter()), Router
        )

    def test_exposes_both_legs(self):
        primary, fallback = RecordingRouter(), CapabilityRouter()
        composite = FallbackRouter(primary, fallback)
        assert composite.primary is primary and composite.fallback is fallback


class TestPrimaryWins:
    def test_uses_the_primary_decision(self, agents, task):
        composite = FallbackRouter(RecordingRouter(decision()), RecordingRouter())
        assert composite.route(task, agents).reason == "scripted"

    def test_does_not_consult_the_fallback(self, agents, task):
        fallback = RecordingRouter(decision(reason="fallback"))
        FallbackRouter(RecordingRouter(decision()), fallback).route(task, agents)
        assert fallback.calls == 0


class TestFallbackUsed:
    def test_falls_back_when_primary_declines(self, agents, task):
        primary = RecordingRouter(None)
        composite = FallbackRouter(primary, CapabilityRouter())
        result = composite.route(task, agents)
        assert primary.calls == 1
        assert result.agent_name == "alpha_agent"
        assert "matched capabilities" in result.reason  # came from the fallback

    def test_falls_back_when_primary_raises(self, agents, task):
        composite = FallbackRouter(RecordingRouter(fail=True), CapabilityRouter())
        assert composite.route(task, agents).agent_name == "alpha_agent"

    def test_a_raising_primary_is_logged(self, agents, task, caplog):
        with caplog.at_level(logging.ERROR, logger="src"):
            FallbackRouter(RecordingRouter(fail=True), CapabilityRouter()).route(task, agents)
        assert "RuntimeError" in caplog.text and "router exploded" in caplog.text

    def test_returns_none_when_both_decline(self, agents, task):
        composite = FallbackRouter(RecordingRouter(None), RecordingRouter(None))
        assert composite.route(task, agents) is None

    def test_fallback_still_declines_an_unroutable_task(self, agents):
        composite = FallbackRouter(RecordingRouter(None), CapabilityRouter())
        assert composite.route(Task(description="book a flight"), agents) is None
