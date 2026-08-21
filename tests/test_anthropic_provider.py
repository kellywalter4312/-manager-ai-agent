"""Tests for the Claude provider.

Every test injects a fake client that mimics the SDK's response shape, so the
suite still needs no API key, no network and no credentials of any kind.
"""

import socket
from types import SimpleNamespace

import pytest

from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.llm_router import LLMRouter
from src.providers.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from src.providers.base import Completion, LLMProvider
from src.providers.factory import available_providers, build_provider
from src.providers.null_provider import NullProvider


def block(type_: str, text: str = ""):
    return SimpleNamespace(type=type_, text=text)


def message(
    blocks=None,
    stop_reason="end_turn",
    model="claude-opus-5",
    input_tokens=120,
    output_tokens=8,
):
    """Build a stand-in for an SDK response object."""
    return SimpleNamespace(
        content=blocks if blocks is not None else [block("text", "alpha_agent")],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeClient:
    """Records the request it was given and returns a canned response."""

    def __init__(self, response=None, error=None):
        self._response = response if response is not None else message()
        self._error = error
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def provider(client):
    return AnthropicProvider(client=client)


class TestInterface:
    def test_satisfies_the_provider_interface(self, provider):
        assert isinstance(provider, LLMProvider)

    def test_is_named_anthropic(self, provider):
        assert provider.name == "anthropic"

    def test_defaults_to_the_current_model(self, provider):
        assert provider.model == DEFAULT_MODEL == "claude-opus-5"

    def test_model_is_configurable(self, client):
        assert AnthropicProvider(model="claude-sonnet-5", client=client).model == "claude-sonnet-5"

    def test_rejects_an_empty_model(self):
        with pytest.raises(ValueError):
            AnthropicProvider(model="  ")

    def test_rejects_a_non_positive_max_tokens(self):
        with pytest.raises(ValueError):
            AnthropicProvider(max_tokens=0)


class TestRequestShape:
    def test_sends_the_prompt_as_a_user_message(self, provider, client):
        provider.complete("who handles email?")
        assert client.requests[0]["messages"] == [
            {"role": "user", "content": "who handles email?"}
        ]

    def test_sends_the_model_and_a_token_budget(self, provider, client):
        provider.complete("hello")
        assert client.requests[0]["model"] == DEFAULT_MODEL
        assert client.requests[0]["max_tokens"] > 0

    def test_passes_the_system_instruction_when_given(self, provider, client):
        provider.complete("hello", system="You route tasks.")
        assert client.requests[0]["system"] == "You route tasks."

    def test_omits_system_when_not_given(self, provider, client):
        provider.complete("hello")
        assert "system" not in client.requests[0]

    def test_never_sends_sampling_parameters(self, provider, client):
        """This model family rejects temperature / top_p / top_k outright."""
        provider.complete("hello")
        request = client.requests[0]
        assert not {"temperature", "top_p", "top_k"} & set(request)

    def test_rejects_an_empty_prompt_without_calling_the_api(self, provider, client):
        with pytest.raises(ValueError):
            provider.complete("   ")
        assert client.requests == []


class TestResponseHandling:
    def test_returns_a_completion_with_the_text(self, provider):
        completion = provider.complete("hello")
        assert isinstance(completion, Completion)
        assert completion.text == "alpha_agent"

    def test_reports_provider_model_and_token_usage(self, provider):
        completion = provider.complete("hello")
        assert completion.provider == "anthropic"
        assert completion.model == "claude-opus-5"
        assert completion.input_tokens == 120
        assert completion.output_tokens == 8

    def test_joins_multiple_text_blocks(self):
        provider = AnthropicProvider(
            client=FakeClient(message(blocks=[block("text", "alpha"), block("text", "_agent")]))
        )
        assert provider.complete("hello").text == "alpha_agent"

    def test_ignores_non_text_blocks(self):
        """A response may also carry thinking blocks; only text is the answer."""
        provider = AnthropicProvider(
            client=FakeClient(
                message(blocks=[block("thinking", "hmm..."), block("text", "beta_agent")])
            )
        )
        assert provider.complete("hello").text == "beta_agent"

    def test_handles_a_response_with_no_text_blocks(self):
        provider = AnthropicProvider(client=FakeClient(message(blocks=[])))
        assert provider.complete("hello").is_empty

    def test_a_refusal_yields_an_empty_completion_not_a_crash(self):
        """A declined request arrives as a normal response, not an exception."""
        provider = AnthropicProvider(
            client=FakeClient(message(blocks=[], stop_reason="refusal"))
        )
        completion = provider.complete("hello")
        assert completion.is_empty
        assert completion.provider == "anthropic"

    def test_a_refusal_never_returns_partial_content_as_an_answer(self):
        provider = AnthropicProvider(
            client=FakeClient(
                message(blocks=[block("text", "I can't help")], stop_reason="refusal")
            )
        )
        assert provider.complete("hello").is_empty

    def test_sdk_errors_propagate_to_the_caller(self):
        provider = AnthropicProvider(client=FakeClient(error=RuntimeError("api down")))
        with pytest.raises(RuntimeError):
            provider.complete("hello")


class TestCredentials:
    def test_no_key_is_needed_to_construct_the_provider(self):
        """Building it must not demand a credential - only calling it does."""
        assert AnthropicProvider().name == "anthropic"

    def test_no_key_is_stored_in_the_repr(self):
        provider = AnthropicProvider(api_key="sk-ant-SECRET-VALUE")
        assert "SECRET" not in repr(provider)

    def test_an_injected_client_never_touches_the_network(self, provider, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert provider.complete("hello").text == "alpha_agent"


class TestFactoryIntegration:
    def test_anthropic_is_registered(self):
        assert "anthropic" in available_providers()

    def test_null_is_still_the_default(self):
        assert isinstance(build_provider(Settings()), NullProvider)

    def test_factory_builds_it_from_settings(self):
        provider = build_provider(
            Settings(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-test")
        )
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-sonnet-5"

    def test_building_it_without_a_key_fails_clearly(self):
        """Retargeted. This previously asserted that selecting the provider
        succeeded with no key. It did - and the missing key then surfaced much
        later, inside LLMManagerBrain, as an UNKNOWN decision indistinguishable
        from a model that had nothing to say. A misconfiguration is now
        reported at startup instead. See test_anthropic_wiring.py.
        """
        from src.providers.factory import ProviderConfigurationError

        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="anthropic"), env={})

    def test_building_it_with_a_key_still_works(self):
        provider = build_provider(Settings(provider="anthropic"), env={"ANTHROPIC_API_KEY": "k"})
        assert isinstance(provider, AnthropicProvider)

    def test_settings_read_model_and_key_from_the_environment(self):
        settings = Settings.from_env(
            env={"MANAGER_MODEL": "claude-sonnet-5", "MANAGER_API_KEY": "sk-ant-test"}
        )
        assert settings.model == "claude-sonnet-5"
        assert settings.api_key == "sk-ant-test"

    def test_a_blank_key_counts_as_no_key(self):
        assert Settings.from_env(env={"MANAGER_API_KEY": "   "}).api_key is None

    def test_settings_never_print_the_key(self):
        settings = Settings(api_key="sk-ant-SECRET-VALUE")
        assert "SECRET" not in settings.describe()
        assert "SECRET" not in repr(settings)
        assert settings.has_api_key


class TestDrivesTheRouter:
    """The payoff: a real provider plugs into the existing Router unchanged."""

    def test_the_llm_router_can_route_using_this_provider(self):
        from src.agents.echo_agent import EchoAgent
        from src.manager.manager import ManagerAgent

        provider = AnthropicProvider(client=FakeClient(message(blocks=[block("text", "beta_agent")])))
        manager = ManagerAgent(router=LLMRouter(provider))
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))
        manager.register_agent(EchoAgent(name="beta_agent", capabilities={"calendar"}))

        result = manager.handle_task(Task(description="send an email"))

        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "beta_agent"  # the model's choice, not capability matching

    def test_a_refusal_makes_the_router_decline_rather_than_guess(self):
        from src.agents.echo_agent import EchoAgent

        provider = AnthropicProvider(
            client=FakeClient(message(blocks=[], stop_reason="refusal"))
        )
        agents = [EchoAgent(name="alpha_agent", capabilities={"email"})]
        assert LLMRouter(provider).route(Task(description="send an email"), agents) is None
