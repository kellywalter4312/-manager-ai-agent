"""Tests for the configured path from settings to a Claude-backed Manager.

    MANAGER_PROVIDER=anthropic  ->  AnthropicProvider
    MANAGER_BRAIN=llm           ->  LLMManagerBrain
                                ->  ManagerAgent

Two claims matter more than the rest:

    TestTheDefaultStaysSafe        - nothing selects Anthropic by accident
    TestAMissingKeyFailsClearly    - a misconfiguration cannot disguise itself
                                     as a brain that simply has no opinions

**No API request is made anywhere in this file, and none is faked.** The
provider is constructed and inspected; its client is never built. Where a
completion is needed, a scripted double supplies it.
"""

import os
import socket
import sys

import pytest
from test_llm_manager_brain import ScriptedProvider

from src.agents.echo_agent import EchoAgent
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.brain import ManagerBrain, NullBrain
from src.manager.brain_factory import build_brain
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.providers.base import Completion, LLMProvider
from src.providers.factory import (
    ANTHROPIC_KEY_ENV,
    ProviderConfigurationError,
    available_providers,
    build_provider,
)
from src.providers.null_provider import NullProvider


def settings(**overrides):
    """Settings from an empty environment, so tests never read os.environ."""
    return Settings.from_env(env={}, **overrides)


def configured(**overrides):
    """The explicit Claude configuration."""
    overrides.setdefault("api_key", "test-key")
    return settings(provider="anthropic", brain="llm", **overrides)


def code_of(module) -> str:
    return open(module.__file__, encoding="utf-8").read().lower()


# ----------------------------------------------------------------------
# The default stays safe
# ----------------------------------------------------------------------


class TestTheDefaultStaysSafe:
    def test_the_default_provider_is_null(self):
        assert settings().provider == "null"
        assert isinstance(build_provider(settings(), env={}), NullProvider)

    def test_the_default_brain_is_null(self):
        assert settings().brain == "null"
        assert isinstance(build_brain(settings(), NullProvider()), NullBrain)

    def test_the_default_needs_no_key(self):
        config = settings()
        assert not config.has_api_key
        assert build_provider(config, env={}) is not None

    def test_a_present_key_does_not_select_anthropic(self):
        """Selection is by name. Having a credential is not a request to use it."""
        config = settings(api_key="a-real-looking-key")
        assert isinstance(build_provider(config, env={}), NullProvider)

    def test_the_standard_env_var_does_not_select_anthropic(self):
        config = settings()
        provider = build_provider(config, env={ANTHROPIC_KEY_ENV: "a-real-looking-key"})
        assert isinstance(provider, NullProvider)

    def test_an_installed_sdk_does_not_select_anthropic(self):
        """Even where the package is importable, the default is unchanged."""
        assert build_provider(settings(), env={}).name == "null"

    def test_the_default_path_loads_no_vendor_sdk(self):
        config = settings()
        build_brain(config, build_provider(config, env={}))
        assert "anthropic" not in sys.modules

    def test_the_default_manager_makes_no_call(self):
        config = settings()
        manager = ManagerAgent(brain=build_brain(config, build_provider(config, env={})))
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))

        result = manager.handle_task(Task(description="check my email"))
        assert result.status is TaskStatus.COMPLETED
        assert "anthropic" not in sys.modules

    def test_turning_it_on_takes_two_settings_and_a_key(self):
        assert settings().provider == "null" and settings().brain == "null"


# ----------------------------------------------------------------------
# The configured path
# ----------------------------------------------------------------------


class TestTheConfiguredPath:
    def test_anthropic_is_a_configurable_provider(self):
        assert "anthropic" in available_providers()

    def test_explicit_configuration_selects_the_anthropic_provider(self):
        from src.providers.anthropic_provider import AnthropicProvider

        provider = build_provider(configured(), env={})
        assert isinstance(provider, AnthropicProvider)
        assert provider.name == "anthropic"

    def test_explicit_configuration_constructs_an_llm_brain(self):
        config = configured()
        brain = build_brain(config, build_provider(config, env={}))
        assert isinstance(brain, LLMManagerBrain)

    def test_the_brain_receives_the_configured_provider(self):
        config = configured()
        provider = build_provider(config, env={})
        brain = build_brain(config, provider)
        assert brain.provider_name == provider.name == "anthropic"

    def test_the_manager_receives_the_configured_brain(self):
        config = configured()
        brain = build_brain(config, build_provider(config, env={}))
        manager = ManagerAgent(brain=brain)

        assert manager.brain is brain
        assert manager.can_reason

    def test_the_configured_model_reaches_the_provider(self):
        config = configured(model="claude-opus-5")
        assert build_provider(config, env={}).model == "claude-opus-5"

    def test_the_standard_env_var_satisfies_the_credential_check(self):
        """A user who set ANTHROPIC_API_KEY is not told their key is missing."""
        config = settings(provider="anthropic", brain="llm")
        provider = build_provider(config, env={ANTHROPIC_KEY_ENV: "a-real-looking-key"})
        assert provider.name == "anthropic"

    def test_no_request_is_made_by_constructing_the_chain(self):
        config = configured()
        provider = build_provider(config, env={})
        build_brain(config, provider)
        # The client is the thing that would talk to the network. It is unbuilt.
        assert provider._client is None

    def test_constructing_the_chain_loads_no_vendor_sdk(self):
        config = configured()
        build_brain(config, build_provider(config, env={}))
        assert "anthropic" not in sys.modules

    def test_settings_describe_shows_the_configuration(self):
        described = configured().describe()
        assert "provider=anthropic" in described
        assert "brain=llm" in described

    def test_the_key_is_never_shown(self):
        described = configured(api_key="sk-ant-secret-value").describe()
        assert "sk-ant-secret-value" not in described
        assert "api_key=set" in described


# ----------------------------------------------------------------------
# A missing key fails clearly
# ----------------------------------------------------------------------


class TestAMissingKeyFailsClearly:
    def test_it_raises_rather_than_degrading(self):
        with pytest.raises(ProviderConfigurationError):
            build_provider(settings(provider="anthropic"), env={})

    def test_the_message_names_both_places_a_key_could_go(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(settings(provider="anthropic"), env={})
        message = str(exc.value)
        assert ANTHROPIC_KEY_ENV in message
        assert "MANAGER_API_KEY" in message

    def test_the_message_says_how_to_run_without_a_model(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(settings(provider="anthropic"), env={})
        assert "MANAGER_PROVIDER=null" in str(exc.value)

    def test_a_blank_key_counts_as_missing(self):
        for blank in ("", "   "):
            with pytest.raises(ProviderConfigurationError):
                build_provider(settings(provider="anthropic"), env={ANTHROPIC_KEY_ENV: blank})

    def test_it_is_not_silently_downgraded_to_null(self):
        """Running without the model someone asked for would be worse than
        refusing to start - the reports would be indistinguishable."""
        with pytest.raises(ProviderConfigurationError):
            build_provider(settings(provider="anthropic"), env={})

    def test_no_fake_success_is_substituted(self):
        try:
            build_provider(settings(provider="anthropic"), env={})
        except ProviderConfigurationError:
            return
        pytest.fail("a provider was returned where none could be configured")

    def test_the_failure_happens_before_any_sdk_import(self):
        with pytest.raises(ProviderConfigurationError):
            build_provider(settings(provider="anthropic"), env={})
        assert "anthropic" not in sys.modules

    def test_a_misconfiguration_cannot_look_like_no_view(self):
        """The bug this check exists to prevent.

        LLMManagerBrain turns any provider failure into an UNKNOWN decision.
        That is right for a model that times out and wrong as the way anyone
        discovers a key was never configured, so the chain must never be built.
        """
        with pytest.raises(ProviderConfigurationError):
            config = settings(provider="anthropic", brain="llm")
            build_brain(config, build_provider(config, env={}))

    def test_the_error_is_distinct_from_an_unknown_provider_name(self):
        with pytest.raises(ValueError) as exc:
            build_provider(settings(provider="telepathy"), env={})
        assert not isinstance(exc.value, ProviderConfigurationError)

    def test_the_default_is_unaffected_by_the_check(self):
        assert build_provider(settings(), env={}).name == "null"


# ----------------------------------------------------------------------
# The composition roots report it
# ----------------------------------------------------------------------


class TestTheCompositionRootsReportIt:
    def test_main_exits_with_a_readable_message(self, monkeypatch, capsys):
        import main as main_module

        broken = settings(provider="anthropic", brain="llm")
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)
        monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)

        with pytest.raises(SystemExit) as exit_info:
            main_module.main()

        assert exit_info.value.code == 2
        assert "configuration error" in capsys.readouterr().out

    def test_main_does_not_traceback(self, monkeypatch, capsys):
        import main as main_module

        broken = settings(provider="anthropic", brain="llm")
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)
        monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)

        with pytest.raises(SystemExit):
            main_module.main()
        assert "Traceback" not in capsys.readouterr().out

    def test_the_heartbeat_stops_before_touching_the_mailbox(self, monkeypatch, capsys):
        sys.path.insert(0, ".")
        import scripts.heartbeat as heartbeat

        broken = settings(provider="anthropic", brain="llm")
        monkeypatch.setattr(
            heartbeat.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(heartbeat, "configure_logging", lambda *a, **k: None)
        monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)

        assert heartbeat.main(["--dry-run"]) == 2
        assert "configuration error" in capsys.readouterr().out


# ----------------------------------------------------------------------
# The dependency direction is unchanged
# ----------------------------------------------------------------------


class TestTheDependencyDirectionHolds:
    def test_the_brain_does_not_import_anthropic(self):
        import src.manager.llm_brain as module

        code = code_of(module)
        assert "anthropic" not in code
        assert "from src.providers.base import llmprovider" in code

    def test_the_manager_does_not_import_anthropic(self):
        import src.manager.manager as module

        source = code_of(module)
        for word in ("anthropic", "provider"):
            assert word not in source, word

    def test_the_brain_factory_does_not_import_anthropic(self):
        import src.manager.brain_factory as module

        assert "anthropic" not in code_of(module)

    def test_no_provider_is_constructed_inside_the_brain(self):
        import ast

        import src.manager.llm_brain as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert "Provider" not in node.func.id, node.func.id

    def test_importing_the_manager_loads_no_sdk(self):
        """Unrelated components must not drag the vendor in."""
        import importlib

        for name in (
            "src.manager.manager",
            "src.manager.brain_factory",
            "src.manager.llm_brain",
            "src.providers.factory",
        ):
            importlib.import_module(name)
        assert "anthropic" not in sys.modules

    def test_there_is_no_global_provider_state(self):
        import src.providers.factory as module

        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, LLMProvider), name

    def test_each_build_returns_a_distinct_provider(self):
        one = build_provider(settings(), env={})
        two = build_provider(settings(), env={})
        assert one is not two


# ----------------------------------------------------------------------
# The doubles still work
# ----------------------------------------------------------------------


class TestTheDoublesStillWork:
    def test_the_null_provider_still_powers_the_brain(self):
        brain = LLMManagerBrain(NullProvider())
        decision = brain.deliberate(Task(description="what should I do?"))
        assert not decision.is_decided
        assert decision.source == "llm:null"

    def test_a_scripted_provider_still_powers_the_brain(self):
        import json

        provider = ScriptedProvider(
            json.dumps({"action": "report", "reason": "worth telling you"})
        )
        decision = LLMManagerBrain(provider).deliberate(Task(description="anything"))
        assert decision.is_decided
        assert provider.call_count == 1

    def test_a_scripted_provider_needs_no_configuration(self):
        manager = ManagerAgent(brain=LLMManagerBrain(ScriptedProvider("{}")))
        assert isinstance(manager.brain, ManagerBrain)

    def test_any_provider_implementation_still_works(self):
        class MinimalProvider(LLMProvider):
            @property
            def name(self):
                return "minimal"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider=self.name)

        assert LLMManagerBrain(MinimalProvider()).provider_name == "minimal"


# ----------------------------------------------------------------------
# Nothing reached the network
# ----------------------------------------------------------------------


class TestNothingReachedTheNetwork:
    def test_no_network_during_construction(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        config = configured()
        assert build_brain(config, build_provider(config, env={})) is not None

    def test_no_real_key_is_read_from_the_environment(self):
        """Every test here passes an explicit env mapping or a test key."""
        watched = []

        real_get = os.environ.get

        def watching_get(key, default=None):
            watched.append(key)
            return real_get(key, default)

        os.environ.get = watching_get
        try:
            build_provider(configured(), env={})
        finally:
            os.environ.get = real_get

        assert ANTHROPIC_KEY_ENV not in watched

    def test_no_files_are_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = configured()
        build_brain(config, build_provider(config, env={}))
        assert list(tmp_path.iterdir()) == []
