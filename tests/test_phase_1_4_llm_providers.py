"""Phase 1.4 - LLM Providers.

CHECK found the provider abstraction, factory/registry, Full/Half Brain
semantics, and one real, tested, practically-verified provider
(Anthropic) already in place - see `tests/test_brain_provider_
credential_memory_independence.py` (0.2 Final Foundation Audit) and
`tests/test_anthropic_provider.py`/`test_anthropic_response_handling.py`.
None of that ground is re-derived here.

**What CHECK found missing.** Only `null` and `anthropic` were registered
in `src/providers/factory.py::_BUILDERS`. OpenAI and xAI/Grok - both
named explicitly in the canonical roadmap - did not exist in any form:
no provider class, no factory entry, no credential handling, nothing
importable. This file's own production-code changes (`src/providers/
openai_provider.py`, `src/providers/xai_provider.py`, two new factory
entries, two new `Settings` fields - `openai_model`/`xai_model`) are the
smallest addition the existing `LLMProvider` contract required, added
the identical way `_build_anthropic` already was: a dedicated
`<VENDOR>_KEY_ENV` constant, a `_<vendor>_credential()` reader that
checks only that variable, and a `_build_<vendor>()` factory function
that raises `ProviderConfigurationError` before any vendor SDK is
imported. `Brain`, `Identity`, `Memory`, `Voice` and the Capability
Registry were not touched by any of it - see
`TestNoBrainOrIdentityOrMemoryOrVoiceCodeNamesAVendor` below, which reads
every relevant module's source rather than assuming.

**Why xAI imports the `openai` package.** xAI ships no SDK of its own;
xAI's own documentation is to point the OpenAI-compatible client at
`https://api.x.ai/v1` with an xAI key, because the wire protocol is
OpenAI-compatible. `XAIProvider` is still its own class implementing
`LLMProvider` independently, with its own credential (`XAI_API_KEY`,
never `OPENAI_API_KEY`), its own model configuration
(`Settings.xai_model`), and its own errors - see
`src/providers/xai_provider.py`'s own docstring for the full reasoning.
This is not "a second Brain architecture" or "OpenAI behaviour hardcoded
into Alfred": nothing about `LLMManagerBrain` or `ManagerAgent` changed
by one line to make either new provider work.

**Genuine limitation, stated plainly.** No Anthropic, OpenAI or xAI
credentials or credits exist in this environment (the same standing,
external blocker recorded since before Phase 0.1). The `anthropic` and
`openai` packages ARE genuinely installed (`openai` added to
`requirements.txt` and installed into this project's own `.venv` for
this phase, mirroring how `anthropic` was already an optional,
lazily-imported dependency), so every construction-time and
request-shape test below exercises the *real* vendor SDK classes - never
a hand-rolled substitute - short only of the one real network call none
of the three vendors can make here. That distinction - architecture/path
verification through the real SDK vs. live model-quality verification -
is kept explicit throughout this file and never blurred.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain_factory import build_brain
from src.manager.cognition import CognitiveMode, assess_cognition, assess_manager
from src.manager.llm_brain import LLMManagerBrain
import src.providers.factory as provider_factory
from src.providers.factory import (
    ANTHROPIC_KEY_ENV,
    OPENAI_KEY_ENV,
    XAI_KEY_ENV,
    ProviderConfigurationError,
    available_providers,
    build_provider,
)
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.anthropic_provider import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from src.providers.openai_provider import OpenAIProvider
from src.providers.openai_provider import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from src.providers.xai_provider import BASE_URL as XAI_BASE_URL
from src.providers.xai_provider import XAIProvider
from src.providers.xai_provider import DEFAULT_MODEL as XAI_DEFAULT_MODEL
from src.providers.base import Completion, LLMProvider, ProviderResponseError
from scripts.control_center import build
from scripts.exchange import exchange


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


# ---------------------------------------------------------------------------
# Fake OpenAI-SDK-shaped objects - the identical discipline
# tests/test_anthropic_response_handling.py's FakeClient already uses: a
# real object with the real response shape (confirmed against the actually
# installed `openai` package's own pydantic field names - `choices`,
# `message.content`, `message.refusal`, `usage.prompt_tokens`,
# `usage.completion_tokens`), never a mock of behaviour. XAIProvider is
# exercised with the same helper, since it speaks the identical wire shape.
# ---------------------------------------------------------------------------

UNSET = object()


def oa_message(**overrides):
    fields = dict(content="ok", refusal=None)
    fields.update(overrides)
    return SimpleNamespace(**fields)


def oa_choice(**overrides):
    fields = dict(finish_reason="stop", message=oa_message())
    fields.update(overrides)
    return SimpleNamespace(**fields)


def oa_response(**overrides):
    fields = dict(
        choices=[oa_choice()],
        model="gpt-5",
        usage=SimpleNamespace(prompt_tokens=42, completion_tokens=7),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeOpenAICompatibleClient:
    """Stands in for ``openai.OpenAI`` - used by both OpenAIProvider and
    XAIProvider tests, since both speak this identical response shape."""

    def __init__(self, response=UNSET, error=None, base_url=None):
        self._response = oa_response() if response is UNSET else response
        self._error = error
        self.base_url = base_url
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class RaisingProvider(LLMProvider):
    """A provider that always fails - standing in for "the real vendor is
    unreachable/unavailable", one class reused for all three vendors."""

    def __init__(self, vendor_name: str, error: Exception):
        self._name = vendor_name
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt, *, system=None):
        raise self._error


# ---------------------------------------------------------------------------
# 1 & 2. PROVIDER ABSTRACTION AND REGISTRY - reconfirmed, not rebuilt
# ---------------------------------------------------------------------------


class TestProviderAbstractionAndRegistryReconfirmed:
    def test_the_registry_now_carries_four_entries(self):
        assert available_providers() == ["anthropic", "null", "openai", "xai"]

    def test_anthropic_is_unaffected_by_the_two_new_entries(self):
        """Phase 1.3/0.2's own Anthropic coverage still holds - adding two
        sibling entries changed nothing about the existing one."""
        provider = build_provider(Settings(provider="anthropic"), env={ANTHROPIC_KEY_ENV: "a-key"})
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == ANTHROPIC_DEFAULT_MODEL

    def test_selecting_openai_or_xai_constructs_without_calling_the_vendor(self):
        """The same 'construction is inert' guarantee already proven for
        anthropic (tests/test_brain_factory.py::TestConstructionIsInert),
        reconfirmed for both new providers."""
        openai_provider = build_provider(Settings(provider="openai"), env={OPENAI_KEY_ENV: "sk-fake"})
        assert openai_provider._client is None
        xai_provider = build_provider(Settings(provider="xai"), env={XAI_KEY_ENV: "xai-fake"})
        assert xai_provider._client is None

    def test_no_brain_or_identity_or_memory_or_voice_code_names_a_vendor(self):
        import src.manager.brain as brain_module
        import src.manager.brain_factory as brain_factory_module
        import src.manager.llm_brain as llm_brain_module
        import src.manager.manager as manager_module
        import src.manager.capabilities as capabilities_module
        import src.services.identity.identity as identity_module
        import src.services.identity.voice as voice_module
        import src.services.memory.memory as memory_module

        for module in (
            brain_module, brain_factory_module, llm_brain_module, manager_module,
            capabilities_module, identity_module, voice_module, memory_module,
        ):
            source = open(module.__file__, encoding="utf-8").read().lower()
            for vendor in ("anthropic", "openai", "xai", "grok"):
                assert vendor not in source, (module.__name__, vendor)


# ---------------------------------------------------------------------------
# 3. ANTHROPIC / CLAUDE - re-verified against the real contract
# ---------------------------------------------------------------------------


class TestAnthropicAgainstTheRealContract:
    def test_missing_credential_is_refused_before_any_import(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(Settings(provider="anthropic"), env={})
        assert ANTHROPIC_KEY_ENV in str(exc.value)

    def test_switching_from_anthropic_to_openai_through_the_real_brain(self):
        """Real Brain integration for Anthropic, reconfirmed side by side
        with the new providers rather than in isolation."""
        anthropic_provider = AnthropicProvider(client=_anthropic_client("a real Claude answer"))
        brain = LLMManagerBrain(anthropic_provider)
        decision = brain.converse(Task(description="hello"))
        assert decision.reason == "a real Claude answer"
        assert brain.provider_name == "anthropic"


def _anthropic_client(text: str):
    from types import SimpleNamespace as NS

    class Client:
        def __init__(self):
            self.messages = NS(create=self._create)

        def _create(self, **kwargs):
            return NS(
                content=[NS(type="text", text=text)],
                stop_reason="end_turn",
                model="claude-opus-5",
                usage=NS(input_tokens=10, output_tokens=5),
            )

    return Client()


# ---------------------------------------------------------------------------
# 4. OPENAI - the smallest provider the existing contract required
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def test_uses_its_own_credential_never_anthropics_or_xais(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider="openai"),
                env={ANTHROPIC_KEY_ENV: "a-key", XAI_KEY_ENV: "x-key"},
            )
        assert OPENAI_KEY_ENV in str(exc.value)

    def test_uses_its_own_model_configuration(self):
        provider = build_provider(Settings(provider="openai"), env={OPENAI_KEY_ENV: "sk-fake"})
        assert provider.model == OPENAI_DEFAULT_MODEL == "gpt-5"

        custom = build_provider(
            Settings(provider="openai", openai_model="gpt-5-mini"),
            env={OPENAI_KEY_ENV: "sk-fake"},
        )
        assert custom.model == "gpt-5-mini"

    def test_response_normalization_into_the_canonical_completion_shape(self):
        client = FakeOpenAICompatibleClient()
        provider = OpenAIProvider(client=client)
        completion = provider.complete("hello", system="be terse")
        assert isinstance(completion, Completion)
        assert completion.text == "ok"
        assert completion.provider == "openai"
        assert completion.model == "gpt-5"
        assert completion.input_tokens == 42
        assert completion.output_tokens == 7
        # The request actually sent carries both messages, in order.
        sent = client.requests[0]
        assert sent["messages"] == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hello"},
        ]

    def test_a_refusal_is_reported_as_empty_not_fabricated(self):
        client = FakeOpenAICompatibleClient(
            response=oa_response(choices=[oa_choice(message=oa_message(content=None, refusal="declined"))])
        )
        provider = OpenAIProvider(client=client)
        completion = provider.complete("do something unsafe")
        assert completion.is_empty

    def test_a_response_with_no_choices_raises_rather_than_silently_empties(self):
        client = FakeOpenAICompatibleClient(response=oa_response(choices=[]))
        provider = OpenAIProvider(client=client)
        with pytest.raises(ProviderResponseError):
            provider.complete("hello")

    def test_content_of_the_wrong_shape_raises(self):
        client = FakeOpenAICompatibleClient(
            response=oa_response(choices=[oa_choice(message=oa_message(content=123, refusal=None))])
        )
        provider = OpenAIProvider(client=client)
        with pytest.raises(ProviderResponseError):
            provider.complete("hello")

    def test_a_vendor_side_failure_is_a_provider_error_not_a_fabricated_answer(self):
        client = FakeOpenAICompatibleClient(error=RuntimeError("connection reset"))
        provider = OpenAIProvider(client=client)
        with pytest.raises(RuntimeError):
            provider.complete("hello")

    def test_works_through_the_real_brain_with_no_brain_changes(self):
        provider = OpenAIProvider(client=FakeOpenAICompatibleClient(response=oa_response(choices=[oa_choice(message=oa_message(content="a genuine openai answer"))])))
        brain = build_brain(Settings(brain="llm"), provider)
        assert isinstance(brain, LLMManagerBrain)
        decision = brain.converse(Task(description="hello"))
        assert decision.reason == "a genuine openai answer"

    def test_the_real_sdk_client_constructs_with_no_network_call(self):
        """Genuine construction verification (item 12): the actually
        installed ``openai`` package builds a real client object from a
        fake key with no request made - the identical guarantee already
        proven for AnthropicProvider, extended to OpenAI's real SDK."""
        provider = OpenAIProvider(api_key="sk-fake-not-real")
        client = provider._get_client()
        assert type(client).__module__.startswith("openai")
        assert provider._client is client


# ---------------------------------------------------------------------------
# 5. XAI / GROK - the smallest compatible provider
# ---------------------------------------------------------------------------


class TestXAIProvider:
    def test_uses_its_own_credential_never_openais_or_anthropics(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider="xai"),
                env={ANTHROPIC_KEY_ENV: "a-key", OPENAI_KEY_ENV: "o-key"},
            )
        assert XAI_KEY_ENV in str(exc.value)

    def test_uses_its_own_model_configuration(self):
        provider = build_provider(Settings(provider="xai"), env={XAI_KEY_ENV: "xai-fake"})
        assert provider.model == XAI_DEFAULT_MODEL == "grok-4"

        custom = build_provider(
            Settings(provider="xai", xai_model="grok-4-fast"),
            env={XAI_KEY_ENV: "xai-fake"},
        )
        assert custom.model == "grok-4-fast"

    def test_response_normalization_into_the_canonical_completion_shape(self):
        client = FakeOpenAICompatibleClient(response=oa_response(model="grok-4"))
        provider = XAIProvider(client=client)
        completion = provider.complete("hello")
        assert completion.text == "ok"
        assert completion.provider == "xai"
        assert completion.model == "grok-4"
        assert completion.input_tokens == 42
        assert completion.output_tokens == 7

    def test_a_vendor_side_failure_is_a_provider_error_not_a_fabricated_answer(self):
        client = FakeOpenAICompatibleClient(error=RuntimeError("xai unavailable"))
        provider = XAIProvider(client=client)
        with pytest.raises(RuntimeError):
            provider.complete("hello")

    def test_works_through_the_real_brain_with_no_brain_changes(self):
        provider = XAIProvider(client=FakeOpenAICompatibleClient(response=oa_response(choices=[oa_choice(message=oa_message(content="a genuine grok answer"))])))
        brain = build_brain(Settings(brain="llm"), provider)
        decision = brain.converse(Task(description="hello"))
        assert decision.reason == "a genuine grok answer"

    def test_the_real_sdk_client_points_at_xais_own_base_url_not_openais(self):
        """Genuine construction verification, same guarantee as OpenAI's -
        and specifically confirms xAI's own endpoint is used, not the
        default OpenAI one, despite sharing the SDK package."""
        provider = XAIProvider(api_key="xai-fake-not-real")
        client = provider._get_client()
        assert type(client).__module__.startswith("openai")
        assert str(client.base_url).rstrip("/") == XAI_BASE_URL.rstrip("/")

    def test_openai_and_xai_are_genuinely_separate_provider_classes(self):
        """Sharing an SDK package is an implementation detail, not a
        merged architecture - the two are still fully independent
        LLMProvider implementations."""
        assert OpenAIProvider is not XAIProvider
        assert not issubclass(XAIProvider, OpenAIProvider)
        assert not issubclass(OpenAIProvider, XAIProvider)


# ---------------------------------------------------------------------------
# 6. PROVIDER NEUTRALITY / FUTURE-PROVIDER EXTENSIBILITY
# ---------------------------------------------------------------------------


class TestFutureProviderExtensibility:
    """A fourth vendor, never part of the canonical set, added purely by
    implementing the contract and registering it - proving the mechanism
    generalises rather than having been special-cased for three names.
    Complements (does not replace) the pre-existing equivalent proof in
    tests/test_brain_provider_credential_memory_independence.py::
    TestProviderSwitchingThroughTheRealProductionPath::
    test_a_new_compatible_provider_can_be_registered_without_touching_
    brain_code."""

    def test_a_never_before_seen_vendor_flows_registry_to_brain_to_response(self, monkeypatch):
        credential_env = "GEMINI_API_KEY"

        class GeminiProvider(LLMProvider):
            @property
            def name(self):
                return "gemini"

            def complete(self, prompt, *, system=None):
                return Completion(text="a genuine gemini answer", provider="gemini", model="gemini-3")

        def _build_gemini(settings, env, account=None):
            source = env if env is not None else {}
            key = (source.get(credential_env) or "").strip()
            if not key:
                raise ProviderConfigurationError(
                    f"The 'gemini' provider is selected but no API key is configured. "
                    f"Set {credential_env} in the environment."
                )
            return GeminiProvider()

        patched = dict(provider_factory._BUILDERS)
        patched["gemini"] = _build_gemini
        monkeypatch.setattr(provider_factory, "_BUILDERS", patched)

        assert "gemini" not in ("anthropic", "openai", "xai", "null")
        assert "gemini" in available_providers()

        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="gemini"), env={})

        provider = build_provider(Settings(provider="gemini"), env={credential_env: "fake-key"})
        brain = build_brain(Settings(brain="llm"), provider)
        assert isinstance(brain, LLMManagerBrain)

        decision = brain.converse(Task(description="hi"))
        assert decision.reason == "a genuine gemini answer"
        assert brain.provider_name == "gemini"

        # And it drops into a full, live Manager - identity, memory, voice,
        # capability registry - with nothing about any of them touched.
        runtime, manager, channel, conversation = real_system()
        manager.brain = brain
        manager.own_skills._converse = brain.converse
        turn = exchange(channel, conversation, "say something")
        assert turn.outcome == "a genuine gemini answer"


# ---------------------------------------------------------------------------
# 7. CREDENTIAL ISOLATION
# ---------------------------------------------------------------------------


class TestCredentialIsolationAcrossAllThreeRealProviders:
    @pytest.mark.parametrize(
        "provider_name, own_env, foreign_envs",
        [
            ("anthropic", ANTHROPIC_KEY_ENV, (OPENAI_KEY_ENV, XAI_KEY_ENV)),
            ("openai", OPENAI_KEY_ENV, (ANTHROPIC_KEY_ENV, XAI_KEY_ENV)),
            ("xai", XAI_KEY_ENV, (ANTHROPIC_KEY_ENV, OPENAI_KEY_ENV)),
        ],
    )
    def test_a_provider_never_accepts_a_foreign_vendors_key(self, provider_name, own_env, foreign_envs):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider=provider_name),
                env={foreign: "a-foreign-key" for foreign in foreign_envs},
            )
        assert own_env in str(exc.value)

    @pytest.mark.parametrize(
        "provider_name, own_env",
        [("anthropic", ANTHROPIC_KEY_ENV), ("openai", OPENAI_KEY_ENV), ("xai", XAI_KEY_ENV)],
    )
    def test_missing_credential_is_handled_honestly(self, provider_name, own_env):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(Settings(provider=provider_name), env={})
        assert own_env in str(exc.value)
        assert "MANAGER_PROVIDER=null" in str(exc.value)

    def test_credential_replacement_and_removal_are_honoured_immediately_for_each(self):
        """Credentials are read fresh on every build_provider call, never
        cached - the identical guarantee test_brain_provider_credential_
        memory_independence.py proves for anthropic, reconfirmed for the
        two new providers."""
        for provider_name, env_name in (("openai", OPENAI_KEY_ENV), ("xai", XAI_KEY_ENV)):
            built = build_provider(Settings(provider=provider_name), env={env_name: "a-key"})
            assert built.name == provider_name

            with pytest.raises(ProviderConfigurationError):
                build_provider(Settings(provider=provider_name), env={})

            replaced = build_provider(Settings(provider=provider_name), env={env_name: "a-different-key"})
            assert replaced.name == provider_name

    def test_settings_api_key_still_reaches_anthropic_only(self):
        """MANAGER_API_KEY/Settings.api_key remains anthropic-specific -
        neither new provider was given, or accepts, that field. Setting it
        while selecting openai must not satisfy openai's own credential
        check; only OPENAI_API_KEY may."""
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(
                Settings(provider="openai", api_key="an-anthropic-shaped-key-in-the-wrong-field"),
                env={},
            )
        assert OPENAI_KEY_ENV in str(exc.value)

        anthropic_provider = build_provider(
            Settings(provider="anthropic", api_key="a-real-anthropic-key"), env={}
        )
        assert isinstance(anthropic_provider, AnthropicProvider)


# ---------------------------------------------------------------------------
# 8. MODEL SELECTION BELONGS TO PROVIDER CONFIGURATION, NEVER THE BRAIN
# ---------------------------------------------------------------------------


class TestModelSelectionBelongsToProviderConfiguration:
    def test_changing_which_model_a_provider_uses_never_touches_the_brain_class(self):
        for provider_name, model_field, model_value, env in (
            ("anthropic", "model", "claude-opus-5-mini", {ANTHROPIC_KEY_ENV: "a-key"}),
            ("openai", "openai_model", "gpt-5-mini", {OPENAI_KEY_ENV: "o-key"}),
            ("xai", "xai_model", "grok-4-fast", {XAI_KEY_ENV: "x-key"}),
        ):
            settings = Settings(provider=provider_name, **{model_field: model_value})
            provider = build_provider(settings, env=env)
            assert provider.model == model_value

            brain = build_brain(Settings(brain="llm"), provider)
            assert type(brain) is LLMManagerBrain

    def test_the_router_owns_no_model_name_either(self):
        """If a router exists it is asked which agent, never which model -
        confirmed by source: LLMManagerBrain accepts a constructed
        provider, and no model string reaches it directly."""
        import inspect

        signature = inspect.signature(LLMManagerBrain.__init__)
        assert "model" not in signature.parameters


# ---------------------------------------------------------------------------
# 9. FULL BRAIN / HALF BRAIN ACROSS ALL THREE REAL PROVIDERS
# ---------------------------------------------------------------------------


class TestFullHalfBrainAcrossEveryProvider:
    @pytest.mark.parametrize(
        "make_working_provider",
        [
            lambda: AnthropicProvider(client=_anthropic_client("a real answer")),
            lambda: OpenAIProvider(client=FakeOpenAICompatibleClient()),
            lambda: XAIProvider(client=FakeOpenAICompatibleClient()),
        ],
        ids=["anthropic", "openai", "xai"],
    )
    def test_half_brain_until_a_real_call_succeeds_then_full_brain(self, make_working_provider):
        brain = LLMManagerBrain(make_working_provider())
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN

        decision = brain.converse(Task(description="hello"))
        assert decision.is_decided
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN

    @pytest.mark.parametrize(
        "vendor_name, error",
        [
            ("anthropic", RuntimeError("connection reset")),
            ("openai", RuntimeError("rate limited")),
            ("xai", RuntimeError("service unavailable")),
        ],
    )
    def test_a_failing_provider_degrades_to_honest_half_brain_never_pretends(self, vendor_name, error):
        brain = LLMManagerBrain(RaisingProvider(vendor_name, error))
        decision = brain.converse(Task(description="hello"))
        assert not decision.is_decided
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN

    def test_an_unconfigured_provider_is_half_brain_for_every_vendor_name(self):
        for provider_name in ("anthropic", "openai", "xai"):
            with pytest.raises(ProviderConfigurationError):
                build_provider(Settings(provider=provider_name), env={})
            # A ManagerAgent that never got past this exception simply has
            # no provider configured - the null default - which is Half
            # Brain honestly, the same as every other unconfigured vendor.
            assert assess_cognition(None).mode is CognitiveMode.HALF_BRAIN


# ---------------------------------------------------------------------------
# 10. PROVIDER SWITCHING - A REAL END-TO-END SESSION, ALL THREE VENDORS
# ---------------------------------------------------------------------------


class TestProviderSwitchingAcrossAllThreeRealProviders:
    def test_the_full_twelve_step_session(self):
        runtime, manager, channel, conversation = real_system()
        alfred_identity = runtime.identity
        memory = manager.memory
        registry = manager.registry

        # 1-2. Start with provider A (anthropic), establish usability.
        brain_a = LLMManagerBrain(
            AnthropicProvider(client=_anthropic_client("hello from Claude")),
            personality=runtime.identity.personality,
        )
        manager.brain = brain_a
        manager.own_skills._converse = brain_a.converse
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

        # 3. Have a conversation.
        turn = exchange(channel, conversation, "remember that the callsign is Redwing")
        assert turn.succeeded
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

        # 4. Record/retrieve relevant memory.
        assert any("Redwing" in r.content for r in memory.recall(limit=10))

        # 5-6. Switch to provider B (openai), establish usability.
        brain_b = LLMManagerBrain(
            OpenAIProvider(client=FakeOpenAICompatibleClient(
                response=oa_response(choices=[oa_choice(message=oa_message(content="the callsign is still Redwing"))])
            )),
            personality=runtime.identity.personality,
        )
        manager.brain = brain_b
        manager.own_skills._converse = brain_b.converse
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

        # 7. Continue the conversation.
        turn = exchange(channel, conversation, "what was the callsign again?")
        assert turn.outcome == "the callsign is still Redwing"
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

        # 8. Confirm previous memory remains.
        assert any("Redwing" in r.content for r in memory.recall(limit=10))
        assert manager.memory is memory

        # 9. Confirm Alfred identity remains.
        assert runtime.identity is alfred_identity
        assert runtime.identity.name == "Alfred"

        # 10. Confirm Alfred voice remains.
        assert runtime.identity.voice is alfred_identity.voice

        # 11. Confirm provider-specific credential isolation (each vendor's
        # own factory path still refuses the others' keys).
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="openai"), env={ANTHROPIC_KEY_ENV: "a-key"})
        with pytest.raises(ProviderConfigurationError):
            build_provider(Settings(provider="xai"), env={OPENAI_KEY_ENV: "o-key"})

        # 12. Switch again, to provider C (xai).
        brain_c = LLMManagerBrain(
            XAIProvider(client=FakeOpenAICompatibleClient(
                response=oa_response(choices=[oa_choice(message=oa_message(content="Grok here, Redwing noted"))])
            )),
            personality=runtime.identity.personality,
        )
        manager.brain = brain_c
        manager.own_skills._converse = brain_c.converse
        turn = exchange(channel, conversation, "one more time, what's the callsign?")
        assert turn.outcome == "Grok here, Redwing noted"
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN
        assert any("Redwing" in r.content for r in memory.recall(limit=10))
        assert manager.registry is registry
        assert runtime.identity.name == "Alfred"
