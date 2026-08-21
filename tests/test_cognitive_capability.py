"""Tests for Full Brain / Half Brain capability selection.

The claim being protected is narrow and easy to get wrong: whether the Manager
has a model is not the same question as whether a brain was injected, and not
the same as whether an API key string exists somewhere. It is whether a model
is *reachable right now*.

Three ways that can be false, all of which used to look alike:

    no brain configured                      -> Half Brain
    a brain with the null provider behind it -> Half Brain (a reasoning shell)
    a model configured but failing           -> Half Brain, degraded

The last one is the one with teeth. A provider that raises is unavailable, not
a model exercising judgement, and the previous milestone made that visible
precisely so this layer could read it.

The other half of the concept gets equal weight here: Half Brain is not broken.
TestHalfBrainStillOperates drives real tasks end to end with no model at all.

Nothing in this file constructs a real client or a real key. Every provider is
scripted, null, or a deliberate failure.
"""

import socket

import pytest
from conftest import ScriptedProvider

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task, TaskStatus
from src.manager.brain import NullBrain
from src.manager.cognition import (
    CORE_CAPABILITIES,
    MODEL_CAPABILITIES,
    CognitiveMode,
    CognitiveProfile,
    assess_cognition,
    assess_manager,
)
from src.manager.fallback_router import FallbackRouter
from src.manager.llm_brain import LLMManagerBrain
from src.manager.llm_router import LLMRouter
from src.manager.manager import ManagerAgent
from src.manager.router import CapabilityRouter
from src.providers.base import Completion, LLMProvider
from src.providers.null_provider import NullProvider


class FailingProvider(LLMProvider):
    """Fails when asked - the shape of an exhausted balance."""

    def __init__(self, error=None):
        self._error = error or RuntimeError("credit balance is too low")

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt, *, system=None) -> Completion:
        raise self._error


def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email"}),
        EchoAgent(name="beta_agent", capabilities={"calendar"}),
    ]


def manager_with(brain=None, router=None) -> ManagerAgent:
    manager = ManagerAgent(brain=brain, router=router)
    for agent in agents():
        manager.register_agent(agent)
    return manager


def llm_router_over(provider) -> FallbackRouter:
    return FallbackRouter(primary=LLMRouter(provider), fallback=CapabilityRouter())


# ----------------------------------------------------------------------
# (a) LLM available -> Full Brain
# ----------------------------------------------------------------------


class TestFullBrain:
    def test_a_brain_with_a_model_is_full_brain(self):
        profile = assess_cognition(LLMManagerBrain(ScriptedProvider("{}")))
        assert profile.mode is CognitiveMode.FULL_BRAIN
        assert profile.is_full_brain

    def test_a_model_driven_router_alone_is_full_brain(self):
        """Cognition can arrive through routing as well as deliberation."""
        profile = assess_cognition(NullBrain(), llm_router_over(ScriptedProvider("alpha_agent")))
        assert profile.mode is CognitiveMode.FULL_BRAIN

    def test_both_sources_together_are_full_brain(self):
        profile = assess_cognition(
            LLMManagerBrain(ScriptedProvider("{}")),
            llm_router_over(ScriptedProvider("alpha_agent")),
        )
        assert profile.mode is CognitiveMode.FULL_BRAIN

    def test_it_names_the_provider_supplying_cognition(self):
        profile = assess_cognition(LLMManagerBrain(ScriptedProvider("{}")))
        assert "scripted" in profile.reason

    def test_full_brain_has_the_model_capabilities(self):
        profile = assess_cognition(LLMManagerBrain(ScriptedProvider("{}")))
        for capability in MODEL_CAPABILITIES:
            assert capability in profile.capabilities

    def test_full_brain_keeps_every_core_capability(self):
        """The model adds; it never replaces."""
        profile = assess_cognition(LLMManagerBrain(ScriptedProvider("{}")))
        for capability in CORE_CAPABILITIES:
            assert capability in profile.capabilities

    def test_full_brain_is_missing_nothing(self):
        assert assess_cognition(LLMManagerBrain(ScriptedProvider("{}"))).missing == ()

    def test_it_is_not_marked_degraded(self):
        assert not assess_cognition(LLMManagerBrain(ScriptedProvider("{}"))).is_degraded

    def test_the_description_says_full_brain(self):
        assert "FULL BRAIN" in assess_cognition(LLMManagerBrain(ScriptedProvider("{}"))).describe()


# ----------------------------------------------------------------------
# (b) LLM unavailable -> Half Brain
# ----------------------------------------------------------------------


class TestHalfBrain:
    def test_no_brain_is_half_brain(self):
        assert assess_cognition(NullBrain()).mode is CognitiveMode.HALF_BRAIN

    def test_the_default_manager_is_half_brain(self):
        assert assess_manager(ManagerAgent()).mode is CognitiveMode.HALF_BRAIN

    def test_a_brain_with_the_null_provider_is_half_brain(self):
        """The case ManagerAgent.can_reason gets wrong: a reasoning shell with
        no model behind it is not Full Brain."""
        brain = LLMManagerBrain(NullProvider())
        manager = manager_with(brain=brain)

        assert manager.can_reason is True
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

    def test_that_case_is_reported_as_a_configuration_problem(self):
        profile = assess_cognition(LLMManagerBrain(NullProvider()))
        assert "no model backs it" in profile.reason

    def test_a_deliberately_deterministic_setup_reads_as_healthy(self):
        """Half Brain by choice is not a fault and must not be described as one."""
        profile = assess_cognition(NullBrain(), CapabilityRouter())
        assert "deterministically" in profile.reason
        assert not profile.is_degraded

    def test_an_llm_router_over_the_null_provider_is_half_brain(self):
        profile = assess_cognition(NullBrain(), llm_router_over(NullProvider()))
        assert profile.mode is CognitiveMode.HALF_BRAIN

    def test_half_brain_keeps_every_core_capability(self):
        """The headline claim: Half Brain is not a reduced Manager."""
        profile = assess_cognition(NullBrain())
        assert profile.capabilities == CORE_CAPABILITIES

    def test_half_brain_reports_what_a_model_would_add(self):
        assert assess_cognition(NullBrain()).missing == MODEL_CAPABILITIES

    def test_the_description_says_half_brain(self):
        assert "HALF BRAIN" in assess_cognition(NullBrain()).describe()


# ----------------------------------------------------------------------
# (c) Half Brain still runs the Manager's core operation
# ----------------------------------------------------------------------


class TestHalfBrainStillOperates:
    def test_it_still_routes_and_delegates(self):
        manager = manager_with()
        result = manager.handle_task(Task(description="check my email"))

        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "alpha_agent"

    def test_it_still_picks_the_right_specialist(self):
        manager = manager_with()
        result = manager.handle_task(Task(description="check my calendar"))
        assert result.agent_name == "beta_agent"

    def test_it_still_declines_work_nobody_can_do(self):
        """Deterministic judgement is still judgement."""
        manager = manager_with()
        result = manager.handle_task(Task(description="book me a flight to Lagos"))
        assert result.status is TaskStatus.NO_AGENT_FOUND

    def test_it_still_assesses_results(self):
        manager = manager_with()
        result = manager.handle_task(Task(description="check my email"))
        assert result.status is TaskStatus.COMPLETED
        assert result.output

    def test_it_still_answers_deliberation_honestly(self):
        """No view is a valid answer, not a crash and not a fabrication."""
        manager = manager_with()
        decision = manager.deliberate(Task(description="what should I do?"))
        assert not decision.is_decided

    def test_a_degraded_manager_still_completes_tasks(self):
        """The real-world case: the account died, the work continues."""
        manager = manager_with(router=llm_router_over(FailingProvider()))
        result = manager.handle_task(Task(description="check my email"))

        assert result.status is TaskStatus.COMPLETED
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

    def test_every_core_capability_is_exercised_without_a_model(self):
        manager = manager_with()
        assert manager.handle_task(Task(description="check my email")).status is TaskStatus.COMPLETED
        assert manager.recall_context(Task(description="check my email")) is not None
        assert assess_manager(manager).capabilities == CORE_CAPABILITIES


# ----------------------------------------------------------------------
# (d) A provider failure is unavailability, not a model decision
# ----------------------------------------------------------------------


class TestProviderFailureIsUnavailability:
    def test_a_failing_provider_drops_the_manager_to_half_brain(self):
        router = llm_router_over(FailingProvider())
        router.route(Task(description="check my email"), agents())
        assert assess_cognition(NullBrain(), router).mode is CognitiveMode.HALF_BRAIN

    def test_it_is_marked_degraded_rather_than_merely_deterministic(self):
        router = llm_router_over(FailingProvider())
        router.route(Task(description="check my email"), agents())
        profile = assess_cognition(NullBrain(), router)

        assert profile.is_degraded
        assert "unavailable" in profile.reason

    def test_a_configured_model_that_fails_is_not_reported_as_absent(self):
        """"Never configured" and "configured but dead" are different problems
        and must not produce the same message."""
        router = llm_router_over(FailingProvider())
        router.route(Task(description="check my email"), agents())

        failing = assess_cognition(NullBrain(), router)
        absent = assess_cognition(NullBrain(), CapabilityRouter())
        assert failing.reason != absent.reason
        assert failing.is_degraded and not absent.is_degraded

    def test_the_underlying_error_is_named(self):
        router = llm_router_over(FailingProvider())
        router.route(Task(description="check my email"), agents())
        assert "RuntimeError" in assess_cognition(NullBrain(), router).reason

    def test_a_model_that_declines_is_still_full_brain(self):
        """The mirror image, and the distinction that matters: a model
        answering "no suitable agent" is working, not unavailable."""
        router = llm_router_over(ScriptedProvider("NONE"))
        router.route(Task(description="check my email"), agents())

        profile = assess_cognition(NullBrain(), router)
        assert profile.mode is CognitiveMode.FULL_BRAIN
        assert not profile.is_degraded

    def test_recovery_restores_full_brain(self):
        router = llm_router_over(FailingProvider())
        router.route(Task(description="check my email"), agents())
        assert assess_cognition(NullBrain(), router).mode is CognitiveMode.HALF_BRAIN

        router._primary = LLMRouter(ScriptedProvider("alpha_agent"))
        router.route(Task(description="check my email"), agents())
        assert assess_cognition(NullBrain(), router).mode is CognitiveMode.FULL_BRAIN

    def test_a_declining_null_provider_records_no_degradation_at_all(self):
        """NullProvider has no opinion; it does not fail. Nothing to degrade."""
        router = llm_router_over(NullProvider())
        router.route(Task(description="check my email"), agents())
        assert router.last_degradation is None
        assert assess_cognition(NullBrain(), router).mode is CognitiveMode.HALF_BRAIN

    def test_a_broken_router_with_no_model_is_not_called_model_unavailable(self):
        """A degradation exists here, but no model was ever configured, so
        calling it "the model is unavailable" would be a fabrication - it would
        report a model that does not exist as having failed.

        This is what the `and configured` guard in assess_cognition is for.
        """
        from src.manager.router import Router

        class BrokenPrimary(Router):
            def route(self, task, agents):
                raise ValueError("this router is broken")

        router = FallbackRouter(primary=BrokenPrimary(), fallback=CapabilityRouter())
        router.route(Task(description="check my email"), agents())

        # The degradation is real and recorded by the previous milestone...
        assert router.last_degradation is not None
        # ...but it is not a *model* problem, and must not be reported as one.
        profile = assess_cognition(NullBrain(), router)
        assert profile.mode is CognitiveMode.HALF_BRAIN
        assert not profile.is_degraded
        assert "unavailable" not in profile.reason
        assert "no model is configured" in profile.reason


# ----------------------------------------------------------------------
# (e) Nothing is called, nothing is requested
# ----------------------------------------------------------------------


class TestNothingIsCalled:
    def test_assessing_never_consults_the_provider(self):
        provider = ScriptedProvider("alpha_agent")
        assess_cognition(LLMManagerBrain(provider), llm_router_over(provider))
        assert provider.call_count == 0

    def test_assessing_never_deliberates(self):
        class ExplodingBrain(NullBrain):
            @property
            def provider_name(self):
                return "anthropic"

            def deliberate(self, *args, **kwargs):
                raise AssertionError("deliberation was triggered by an assessment")

        assert assess_cognition(ExplodingBrain()).mode is CognitiveMode.FULL_BRAIN

    def test_assessing_never_routes(self):
        class ExplodingRouter(CapabilityRouter):
            def route(self, task, agents):
                raise AssertionError("routing was triggered by an assessment")

        assert assess_cognition(NullBrain(), ExplodingRouter()) is not None

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        assert assess_cognition(LLMManagerBrain(ScriptedProvider("{}"))).is_full_brain

    def test_no_vendor_sdk_is_imported(self):
        import sys

        assess_cognition(LLMManagerBrain(ScriptedProvider("{}")), llm_router_over(NullProvider()))
        assert "anthropic" not in sys.modules

    def test_no_api_key_is_involved_in_the_decision(self):
        """Capability is read from the constructed provider, never from a key."""
        import inspect

        import src.manager.cognition as module

        source = inspect.getsource(module)
        assert "api_key" not in source
        assert "ANTHROPIC" not in source


# ----------------------------------------------------------------------
# (f) Existing behaviour is untouched
# ----------------------------------------------------------------------


class TestExistingBehaviourIntact:
    def test_can_reason_still_answers_its_own_question(self):
        """Not replaced, not redefined - it answers "was a brain injected?".
        This layer answers a different one and leaves that alone."""
        assert ManagerAgent().can_reason is False
        assert manager_with(brain=LLMManagerBrain(NullProvider())).can_reason is True

    def test_full_brain_deliberation_still_works(self):
        import json

        brain = LLMManagerBrain(
            ScriptedProvider(json.dumps({"action": "report", "reason": "worth telling you"}))
        )
        manager = manager_with(brain=brain)

        assert assess_manager(manager).is_full_brain
        assert manager.deliberate(Task(description="anything")).is_decided

    def test_full_brain_routing_still_works(self):
        manager = manager_with(router=llm_router_over(ScriptedProvider("beta_agent")))
        result = manager.handle_task(Task(description="check my email"))

        assert result.agent_name == "beta_agent"
        assert assess_manager(manager).is_full_brain

    def test_the_previous_milestone_s_degradation_semantics_are_unchanged(self):
        router = llm_router_over(FailingProvider())
        decision = router.route(Task(description="check my email"), agents())

        assert "llm unavailable" in decision.reason
        assert router.last_degradation.expected is True

    def test_the_manager_is_not_aware_of_this_layer(self):
        """Read-only observer: ManagerAgent must not have learned anything."""
        import inspect

        import src.manager.manager as module

        assert "cognition" not in inspect.getsource(module).lower()

    def test_the_brain_contract_is_unchanged(self):
        import inspect

        from src.manager.brain import ManagerBrain

        signature = inspect.signature(ManagerBrain.deliberate)
        assert list(signature.parameters) == ["self", "task", "agents", "notes"]

    def test_a_profile_is_immutable(self):
        profile = assess_cognition(NullBrain())
        with pytest.raises(Exception):
            profile.mode = CognitiveMode.FULL_BRAIN


# ----------------------------------------------------------------------
# Odd shapes do not break the observer
# ----------------------------------------------------------------------


class TestRobustness:
    def test_a_brain_with_no_name_is_tolerated(self):
        assert assess_cognition(object()).mode is CognitiveMode.HALF_BRAIN

    def test_none_is_tolerated(self):
        assert assess_cognition(None, None).mode is CognitiveMode.HALF_BRAIN

    def test_a_blank_provider_name_counts_as_no_model(self):
        class Blank(NullBrain):
            @property
            def provider_name(self):
                return "   "

        assert assess_cognition(Blank()).mode is CognitiveMode.HALF_BRAIN

    def test_nested_composite_routers_are_followed(self):
        inner = llm_router_over(ScriptedProvider("alpha_agent"))
        outer = FallbackRouter(primary=inner, fallback=CapabilityRouter())
        assert assess_cognition(NullBrain(), outer).mode is CognitiveMode.FULL_BRAIN

    def test_a_cyclic_router_does_not_hang(self):
        """Bounded depth, so a pathological wiring fails to find a provider
        rather than looping forever."""

        class Cyclic:
            pass

        cyclic = Cyclic()
        cyclic.primary = cyclic
        assert assess_cognition(NullBrain(), cyclic).mode is CognitiveMode.HALF_BRAIN

    def test_the_profile_type_is_what_callers_expect(self):
        assert isinstance(assess_cognition(NullBrain()), CognitiveProfile)
