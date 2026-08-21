"""Tests for the provider abstraction, the null implementation and the factory.

The last test in this file is the important one: it proves a provider can reach
the Manager through the existing Router interface, without any LLM routing code
existing in src/.
"""

import socket

import pytest

from src.agents.echo_agent import EchoAgent
from src.config.settings import Settings
from src.core.contracts import RoutingDecision, Task, TaskStatus
from src.manager.manager import ManagerAgent
from src.manager.router import Router
from src.providers.base import Completion, LLMProvider
from src.providers.factory import available_providers, build_provider
from src.providers.null_provider import NullProvider


class TestCompletion:
    def test_carries_text_and_provider(self):
        completion = Completion(text="hi", provider="null")
        assert completion.text == "hi"
        assert completion.provider == "null"

    def test_metadata_is_optional(self):
        completion = Completion(text="hi", provider="null")
        assert completion.model is None
        assert completion.input_tokens is None

    def test_carries_metadata_when_a_provider_has_it(self):
        completion = Completion(
            text="hi", provider="fake", model="fake-1", input_tokens=10, output_tokens=2
        )
        assert "model=fake-1" in completion.describe()
        assert "tokens=10/2" in completion.describe()

    def test_requires_a_provider_name(self):
        with pytest.raises(ValueError):
            Completion(text="hi", provider="  ")

    def test_rejects_negative_token_counts(self):
        with pytest.raises(ValueError):
            Completion(text="hi", provider="null", input_tokens=-1)

    def test_is_immutable(self):
        with pytest.raises(Exception):
            Completion(text="hi", provider="null").text = "changed"

    def test_knows_when_it_is_empty(self):
        assert Completion(text="   ", provider="null").is_empty
        assert not Completion(text="hi", provider="null").is_empty

    def test_describe_never_includes_the_text(self):
        """describe() goes to logs; generated text must not ride along."""
        completion = Completion(text="SUPER_SECRET_VALUE", provider="null")
        assert "SUPER_SECRET_VALUE" not in completion.describe()


class TestNullProvider:
    def test_satisfies_the_provider_interface(self):
        assert isinstance(NullProvider(), LLMProvider)

    def test_is_named_null(self):
        assert NullProvider().name == "null"

    def test_returns_a_completion(self):
        completion = NullProvider().complete("hello")
        assert isinstance(completion, Completion)
        assert completion.text.strip()

    def test_reports_itself_as_the_provider(self):
        assert NullProvider().complete("hello").provider == "null"

    def test_reports_no_model_and_no_tokens(self):
        """Nothing to report is better than plausible-looking invention."""
        completion = NullProvider().complete("hello")
        assert completion.model is None
        assert completion.input_tokens is None and completion.output_tokens is None

    def test_is_deterministic(self):
        assert NullProvider().complete("hello") == NullProvider().complete("hello")

    def test_accepts_an_optional_system_instruction(self):
        assert NullProvider().complete("hello", system="you are a router").text.strip()

    def test_rejects_an_empty_prompt(self):
        with pytest.raises(ValueError):
            NullProvider().complete("   ")

    def test_does_not_echo_the_prompt(self):
        """Completions reach logs; a prompt's contents must not ride along."""
        secret = "SUPER_SECRET_VALUE"
        assert secret not in NullProvider().complete(f"summarise {secret}").text

    def test_makes_no_network_call(self, monkeypatch):
        """The 'no API access required' claim, enforced rather than asserted."""

        def explode(*args, **kwargs):
            raise AssertionError("NullProvider attempted a network connection")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert NullProvider().complete("hello").text.strip()

    def test_needs_no_environment(self):
        """No key, no variables, nothing configured."""
        settings = Settings.from_env(env={})
        assert build_provider(settings).complete("hello").text.strip()


class TestFactory:
    def test_builds_the_null_provider_by_default(self):
        assert isinstance(build_provider(Settings()), NullProvider)

    def test_lists_available_providers(self):
        assert "null" in available_providers()

    def test_rejects_an_unknown_provider_with_a_helpful_message(self):
        with pytest.raises(ValueError) as exc:
            build_provider(Settings(provider="gpt9000"))
        message = str(exc.value)
        assert "gpt9000" in message
        assert "null" in message  # tells the user what IS available

    def test_a_custom_provider_satisfies_the_interface(self):
        class FakeProvider(LLMProvider):
            @property
            def name(self):
                return "fake"

            def complete(self, prompt, *, system=None):
                return Completion(text="canned answer", provider="fake", model="fake-1")

        assert isinstance(FakeProvider(), LLMProvider)
        assert FakeProvider().complete("anything").text == "canned answer"


class TestProviderReachesTheManager:
    """The seam: how a provider will eventually drive routing.

    This router lives in the test suite on purpose. A real LLMRouter is
    Milestone 4; what Milestone 3 must prove is only that the architecture
    already has a place for it.
    """

    def test_a_provider_backed_router_can_drive_the_manager(self):
        class ProviderBackedRouter(Router):
            """Consults a provider, then decides. Shape of a future LLMRouter."""

            def __init__(self, provider: LLMProvider) -> None:
                self._provider = provider

            def route(self, task, agents):
                if not agents:
                    return None
                # A real LLMRouter would put the agent specs in the prompt and
                # parse the model's choice out of the answer. NullProvider has
                # no opinion, so we fall back to a deterministic pick.
                completion = self._provider.complete(
                    f"Choose an agent for: {task.description}",
                    system="You route tasks to agents.",
                )
                assert completion.text  # the provider answered something
                chosen = agents[0]
                return RoutingDecision(
                    agent_name=chosen.name,
                    score=1,
                    reason=f"provider '{self._provider.name}' consulted",
                )

        manager = ManagerAgent(router=ProviderBackedRouter(NullProvider()))
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))

        # A task the default CapabilityRouter could never route.
        result = manager.handle_task(Task(description="something entirely unrelated"))

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "alpha_agent"

    def test_the_manager_never_imports_a_provider(self):
        """Provider-agnostic by construction, not by discipline."""
        import src.manager.manager as manager_module

        source = open(manager_module.__file__, encoding="utf-8").read()
        assert "provider" not in source.lower()
