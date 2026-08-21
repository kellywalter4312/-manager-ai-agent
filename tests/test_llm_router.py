"""Tests for the LLM-driven router.

Every test uses a ScriptedProvider or NullProvider. No model, no key, no
network is involved anywhere in this file.
"""

import logging

import pytest
from conftest import ScriptedProvider

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task
from src.manager.llm_router import LLMRouter
from src.manager.router import Router, RoutingUnavailable
from src.providers.null_provider import NullProvider


@pytest.fixture
def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email"}),
        EchoAgent(name="beta_agent", capabilities={"calendar"}),
    ]


@pytest.fixture
def task():
    return Task(description="send an email to my team")


class TestInterface:
    def test_is_a_router(self):
        assert isinstance(LLMRouter(NullProvider()), Router)

    def test_exposes_the_provider_name(self):
        assert LLMRouter(NullProvider()).provider_name == "null"


class TestHappyPath:
    def test_routes_to_the_agent_the_model_names(self, agents, task):
        router = LLMRouter(ScriptedProvider("beta_agent"))
        decision = router.route(task, agents)
        assert decision.agent_name == "beta_agent"

    def test_decision_credits_the_model(self, agents, task):
        router = LLMRouter(ScriptedProvider("alpha_agent", model="scripted-1"))
        assert "scripted-1" in router.route(task, agents).reason

    def test_falls_back_to_provider_name_when_no_model_is_reported(self, agents, task):
        router = LLMRouter(ScriptedProvider("alpha_agent", model=None))
        assert "scripted" in router.route(task, agents).reason

    def test_sends_the_routing_system_prompt(self, agents, task):
        provider = ScriptedProvider("alpha_agent")
        LLMRouter(provider).route(task, agents)
        assert provider.systems[0] and "rout" in provider.systems[0].lower()

    def test_prompt_describes_the_available_agents(self, agents, task):
        provider = ScriptedProvider("alpha_agent")
        LLMRouter(provider).route(task, agents)
        assert "alpha_agent" in provider.prompts[0]
        assert "beta_agent" in provider.prompts[0]


class TestItNeverGuesses:
    def test_declines_when_the_model_names_an_unregistered_agent(self, agents, task):
        """The hallucination guard, end to end through the router."""
        router = LLMRouter(ScriptedProvider("gmail_agent"))
        assert router.route(task, agents) is None

    def test_declines_on_none(self, agents, task):
        assert LLMRouter(ScriptedProvider("NONE")).route(task, agents) is None

    def test_declines_on_an_empty_answer(self, agents, task):
        assert LLMRouter(ScriptedProvider("")).route(task, agents) is None

    def test_declines_on_prose(self, agents, task):
        router = LLMRouter(ScriptedProvider("Sure! I would suggest alpha_agent here."))
        assert router.route(task, agents) is None

    def test_declines_with_the_null_provider(self, agents, task):
        """The default path: no model configured, so no opinion offered."""
        assert LLMRouter(NullProvider()).route(task, agents) is None


class TestFaultIsolation:
    """Retargeted.

    Both tests previously asserted that a failing provider returned None. It
    did - and that None was indistinguishable from a model answering "no
    suitable agent", so an exhausted API balance degraded to deterministic
    routing without anything ever reporting a fault.

    The isolation these tests were written to protect is unchanged: no vendor
    exception escapes this router. What changed is that the failure now has a
    type, so FallbackRouter can tell disappointment from breakage.
    """

    def test_a_failing_provider_raises_routing_unavailable(self, agents, task):
        with pytest.raises(RoutingUnavailable):
            LLMRouter(ScriptedProvider(fail=True)).route(task, agents)

    def test_a_failing_provider_is_logged_with_a_traceback(self, agents, task, caplog):
        with caplog.at_level(logging.ERROR, logger="src"):
            with pytest.raises(RoutingUnavailable):
                LLMRouter(ScriptedProvider(fail=True)).route(task, agents)
        assert "RuntimeError" in caplog.text
        assert "provider exploded" in caplog.text

    def test_the_original_exception_is_preserved_as_the_cause(self, agents, task):
        """`from exc` is what keeps the diagnosis: an exhausted balance and a
        revoked key are the same exception type until you read the text."""
        with pytest.raises(RoutingUnavailable) as exc:
            LLMRouter(ScriptedProvider(fail=True)).route(task, agents)
        assert isinstance(exc.value.__cause__, RuntimeError)
        assert "provider exploded" in str(exc.value)

    def test_no_vendor_exception_escapes_untyped(self, agents, task):
        """The fault isolation the original tests protected, still holding."""
        try:
            LLMRouter(ScriptedProvider(fail=True)).route(task, agents)
        except RoutingUnavailable:
            pass
        else:
            pytest.fail("the provider failure was swallowed")


class TestEfficiency:
    def test_does_not_call_the_model_when_there_are_no_agents(self, task):
        provider = ScriptedProvider("alpha_agent")
        assert LLMRouter(provider).route(task, []) is None
        assert provider.call_count == 0


class TestObservability:
    def test_logs_carry_the_task_id(self, agents, task, caplog):
        with caplog.at_level(logging.DEBUG, logger="src"):
            LLMRouter(ScriptedProvider("alpha_agent")).route(task, agents)
        records = [r for r in caplog.records if r.name == "src.manager.llm_router"]
        assert records and all(f"[task={task.id}]" in r.getMessage() for r in records)

    def test_logs_do_not_leak_the_model_answer_or_payload(self, agents, caplog):
        task = Task(description="send an email", payload={"api_key": "SUPER_SECRET_VALUE"})
        with caplog.at_level(logging.DEBUG, logger="src"):
            LLMRouter(ScriptedProvider("alpha_agent")).route(task, agents)
        assert "SUPER_SECRET_VALUE" not in caplog.text
