"""Tests for the difference between "no opinion" and "could not ask".

The bug being prevented, stated once: an exhausted Anthropic balance makes
every routing call fail, FallbackRouter answers with CapabilityRouter, and the
system produces correct-looking decisions forever without anyone noticing the
model stopped being consulted. Before this milestone those two situations were
the same `None` and the same DEBUG log line.

So the assertions worth reading first are the ones that pin the *difference*:

    TestNoneMeansNoOpinion          the ordinary path stays quiet
    TestFailureIsDistinctFromNone   a fault is not silence
    TestTheResultSaysSo             the degradation reaches the output

No model, no key, no network anywhere in this file.
"""

import logging

import pytest
from conftest import ScriptedProvider

from src.agents.echo_agent import EchoAgent
from src.core.contracts import RoutingDecision, Task
from src.manager.fallback_router import Degradation, FallbackRouter
from src.manager.llm_router import LLMRouter
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter, Router, RoutingUnavailable
from src.providers.base import Completion, LLMProvider
from src.providers.null_provider import NullProvider


@pytest.fixture
def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email"}),
        EchoAgent(name="beta_agent", capabilities={"calendar"}),
    ]


@pytest.fixture
def task():
    return Task(description="check my email")


class FailingProvider(LLMProvider):
    """A provider that fails the way a real one does - after being asked."""

    def __init__(self, error):
        self._error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return "failing"

    def complete(self, prompt, *, system=None) -> Completion:
        self.calls += 1
        raise self._error


class BillingExhausted(Exception):
    """Shaped like anthropic.BadRequestError for an exhausted balance.

    Constructed locally rather than imported: naming the real class here would
    put the vendor SDK on this test file's import path for no gain. What is
    being tested is how the router treats an arbitrary provider exception, not
    the vendor's class hierarchy.
    """

    def __init__(self):
        super().__init__(
            "Error code: 400 - {'type': 'error', 'error': {'type': "
            "'invalid_request_error', 'message': 'Your credit balance is too "
            "low to access the Anthropic API.'}}"
        )


def llm_over(provider) -> FallbackRouter:
    return FallbackRouter(primary=LLMRouter(provider), fallback=CapabilityRouter())


# ----------------------------------------------------------------------
# None still means "asked, no usable opinion"
# ----------------------------------------------------------------------


class TestNoneMeansNoOpinion:
    def test_a_declining_model_falls_back_normally(self, agents, task):
        decision = llm_over(ScriptedProvider("NONE")).route(task, agents)
        assert decision.agent_name == "alpha_agent"

    def test_the_fallback_reason_is_not_annotated(self, agents, task):
        """Nothing went wrong, so nothing should be reported as wrong."""
        decision = llm_over(ScriptedProvider("NONE")).route(task, agents)
        assert decision.reason == "matched capabilities: email"
        assert "unavailable" not in decision.reason

    def test_no_degradation_is_recorded(self, agents, task):
        router = llm_over(ScriptedProvider("NONE"))
        router.route(task, agents)
        assert router.last_degradation is None

    def test_the_null_provider_path_is_still_silent(self, agents, task, caplog):
        """The default configuration must not start emitting warnings."""
        router = llm_over(NullProvider())
        with caplog.at_level(logging.WARNING, logger="src"):
            decision = router.route(task, agents)
        assert decision.agent_name == "alpha_agent"
        assert caplog.text == ""
        assert router.last_degradation is None

    def test_an_unusable_answer_is_not_a_failure(self, agents, task):
        router = llm_over(ScriptedProvider("Sure! Try alpha_agent."))
        router.route(task, agents)
        assert router.last_degradation is None


# ----------------------------------------------------------------------
# A provider failure is not silence
# ----------------------------------------------------------------------


class TestFailureIsDistinctFromNone:
    def test_the_llm_router_raises_rather_than_returning_none(self, agents, task):
        with pytest.raises(RoutingUnavailable):
            LLMRouter(FailingProvider(BillingExhausted())).route(task, agents)

    def test_the_fallback_router_still_answers(self, agents, task):
        """Availability is the whole point of falling back - it must survive."""
        decision = llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert isinstance(decision, RoutingDecision)
        assert decision.agent_name == "alpha_agent"

    def test_a_degradation_is_recorded(self, agents, task):
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        assert isinstance(router.last_degradation, Degradation)
        assert router.last_degradation.expected is True

    def test_the_degradation_names_the_underlying_error(self, agents, task):
        """Not RoutingUnavailable - the cause, which is what identifies it."""
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        assert router.last_degradation.error_type == "BillingExhausted"

    def test_the_degradation_keeps_the_diagnostic_text(self, agents, task):
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        assert "credit balance is too low" in router.last_degradation.detail

    def test_the_degradation_is_attributed_to_the_task(self, agents, task):
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        assert router.last_degradation.task_id == task.id

    def test_a_billing_failure_is_not_treated_as_model_silence(self, agents, task):
        """The headline case, asserted directly."""
        failing = llm_over(FailingProvider(BillingExhausted()))
        silent = llm_over(ScriptedProvider("NONE"))
        failing.route(task, agents)
        silent.route(task, agents)

        assert failing.last_degradation is not None
        assert silent.last_degradation is None


# ----------------------------------------------------------------------
# It is logged loudly enough to be noticed
# ----------------------------------------------------------------------


class TestItIsLoggedLoudly:
    def test_a_provider_failure_warns(self, agents, task, caplog):
        with caplog.at_level(logging.WARNING, logger="src"):
            llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert "unavailable" in caplog.text

    def test_the_warning_says_routing_is_no_longer_model_driven(self, agents, task, caplog):
        with caplog.at_level(logging.WARNING, logger="src"):
            llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert "no longer model-driven" in caplog.text

    def test_a_decline_does_not_warn(self, agents, task, caplog):
        with caplog.at_level(logging.WARNING, logger="src"):
            llm_over(ScriptedProvider("NONE")).route(task, agents)
        assert caplog.text == ""

    def test_the_warning_does_not_require_debug_logging(self, agents, task, caplog):
        """The old behaviour hid this at DEBUG, which nobody runs in production."""
        with caplog.at_level(logging.WARNING, logger="src"):
            llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert caplog.records
        assert max(r.levelno for r in caplog.records) >= logging.WARNING


# ----------------------------------------------------------------------
# The result itself says so
# ----------------------------------------------------------------------


class TestTheResultSaysSo:
    def test_the_reason_reports_the_unavailability(self, agents, task):
        decision = llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert "llm unavailable" in decision.reason

    def test_the_reason_keeps_the_capability_explanation(self, agents, task):
        """Annotated, not replaced: why this agent was chosen still matters."""
        decision = llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert "matched capabilities: email" in decision.reason

    def test_the_reason_names_the_error_type(self, agents, task):
        decision = llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert "BillingExhausted" in decision.reason

    def test_an_unroutable_task_still_returns_none(self, agents):
        """Annotation must not invent a decision where there was none."""
        router = llm_over(FailingProvider(BillingExhausted()))
        assert router.route(Task(description="book me a flight"), agents) is None

    def test_the_decision_remains_a_valid_routing_decision(self, agents, task):
        decision = llm_over(FailingProvider(BillingExhausted())).route(task, agents)
        assert decision.agent_name == "alpha_agent"
        assert decision.score > 0

    def test_the_manager_reports_it_without_any_change(self, agents, task, caplog):
        """The payoff: the Manager's own log line carries the degradation, and
        the Manager was not modified to make that happen."""
        manager = ManagerAgent(router=llm_over(FailingProvider(BillingExhausted())))
        for agent in agents:
            manager.register_agent(agent)

        with caplog.at_level(logging.INFO, logger="src"):
            manager.handle_task(task)
        assert "llm unavailable" in caplog.text


# ----------------------------------------------------------------------
# last_degradation tracks the latest attempt
# ----------------------------------------------------------------------


class TestLastDegradationLifecycle:
    def test_it_starts_empty(self):
        assert llm_over(ScriptedProvider("NONE")).last_degradation is None

    def test_it_clears_after_a_successful_route(self, agents, task):
        """A recovered provider must not leave a stale outage on the record."""
        provider = FailingProvider(BillingExhausted())
        router = llm_over(provider)
        router.route(task, agents)
        assert router.last_degradation is not None

        router._primary = LLMRouter(ScriptedProvider("beta_agent"))
        decision = router.route(task, agents)
        assert decision.agent_name == "beta_agent"
        assert router.last_degradation is None

    def test_it_clears_after_an_ordinary_decline(self, agents, task):
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        router._primary = LLMRouter(ScriptedProvider("NONE"))
        router.route(task, agents)
        assert router.last_degradation is None

    def test_it_updates_to_the_most_recent_failure(self, agents, task):
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        router._primary = LLMRouter(FailingProvider(TimeoutError("timed out")))
        router.route(task, agents)
        assert router.last_degradation.error_type == "TimeoutError"

    def test_it_is_not_a_history(self, agents, task):
        """Deliberately one record. Anything more invites use as a metrics store."""
        router = llm_over(FailingProvider(BillingExhausted()))
        router.route(task, agents)
        first = router.last_degradation
        router.route(task, agents)
        assert router.last_degradation is not first


# ----------------------------------------------------------------------
# Unexpected breakage is louder still
# ----------------------------------------------------------------------


class TestUnexpectedErrors:
    def test_an_arbitrary_primary_failure_still_falls_back(self, agents, task):
        class Broken(Router):
            def route(self, task, agents):
                raise ValueError("router is broken")

        router = FallbackRouter(primary=Broken(), fallback=CapabilityRouter())
        assert router.route(task, agents).agent_name == "alpha_agent"

    def test_it_is_recorded_as_unexpected(self, agents, task):
        class Broken(Router):
            def route(self, task, agents):
                raise ValueError("router is broken")

        router = FallbackRouter(primary=Broken(), fallback=CapabilityRouter())
        router.route(task, agents)
        assert router.last_degradation.expected is False

    def test_it_is_described_differently_from_unavailability(self, agents, task):
        class Broken(Router):
            def route(self, task, agents):
                raise ValueError("router is broken")

        router = FallbackRouter(primary=Broken(), fallback=CapabilityRouter())
        decision = router.route(task, agents)
        assert "unexpected error" in decision.reason


# ----------------------------------------------------------------------
# Existing behaviour is intact
# ----------------------------------------------------------------------


class TestSuccessIsUnchanged:
    def test_a_working_model_still_decides(self, agents, task):
        decision = llm_over(ScriptedProvider("beta_agent")).route(task, agents)
        assert decision.agent_name == "beta_agent"

    def test_the_fallback_is_not_consulted_on_success(self, agents, task):
        decision = llm_over(ScriptedProvider("beta_agent")).route(task, agents)
        # CapabilityRouter would have said alpha_agent for "check my email".
        assert decision.agent_name == "beta_agent"

    def test_the_success_reason_is_untouched(self, agents, task):
        decision = llm_over(ScriptedProvider("beta_agent")).route(task, agents)
        assert decision.reason == "selected by scripted-1"
        assert "unavailable" not in decision.reason

    def test_the_provider_is_asked_exactly_once(self, agents, task):
        provider = ScriptedProvider("beta_agent")
        llm_over(provider).route(task, agents)
        assert provider.call_count == 1

    def test_a_failure_does_not_retry_the_provider(self, agents, task):
        """Falling back must not turn one failed call into several."""
        provider = FailingProvider(BillingExhausted())
        llm_over(provider).route(task, agents)
        assert provider.calls == 1

    def test_routing_still_declines_when_nothing_matches(self, agents):
        router = llm_over(ScriptedProvider("NONE"))
        assert router.route(Task(description="book me a flight"), agents) is None
