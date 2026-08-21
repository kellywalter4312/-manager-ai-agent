"""Tests for brain construction and the wiring at the composition roots.

The chain under test:

    Settings -> build_provider -> LLMProvider -> build_brain -> ManagerAgent

Two things are being proved. First, that the chain assembles. Second - and this
is the one that matters - that the default configuration assembles a fully
working Manager with no API key, no vendor SDK and no network, and that even
selecting the real provider constructs everything without calling anything.

No Anthropic API call is made anywhere in this file, and none is faked.
"""

import socket
import sys

import pytest

from src.agents.echo_agent import EchoAgent
from src.config.settings import Settings
from src.core.contracts import Task, TaskStatus
from src.manager.brain import BrainAction, ManagerBrain, NullBrain
from src.manager.brain_factory import available_brains, build_brain
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.providers.base import LLMProvider
from src.providers.factory import ProviderConfigurationError, build_provider
from src.providers.null_provider import NullProvider


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    return Settings.from_env(env={}, **overrides)


# ----------------------------------------------------------------------
# The factory
# ----------------------------------------------------------------------


class TestTheBrainFactory:
    def test_the_default_is_a_null_brain(self):
        brain = build_brain(settings(), NullProvider())
        assert isinstance(brain, NullBrain)

    def test_llm_builds_an_llm_brain(self):
        brain = build_brain(settings(brain="llm"), NullProvider())
        assert isinstance(brain, LLMManagerBrain)

    def test_every_brain_satisfies_the_contract(self):
        for name in available_brains():
            assert isinstance(build_brain(settings(brain=name), NullProvider()), ManagerBrain)

    def test_an_unknown_name_fails_fast_and_readably(self):
        with pytest.raises(ValueError) as exc:
            build_brain(settings(brain="telepathy"), NullProvider())
        assert "telepathy" in str(exc.value)
        assert "null" in str(exc.value) and "llm" in str(exc.value)

    def test_the_available_names_are_listed(self):
        assert available_brains() == ["llm", "null"]

    def test_the_brain_receives_the_injected_provider(self):
        """The factory wires the provider in; it never builds one itself."""
        provider = NullProvider()
        brain = build_brain(settings(brain="llm"), provider)
        assert brain.provider_name == provider.name

    def test_the_factory_does_not_construct_a_provider(self):
        """A provider arrives as an argument. This is the only honest way to
        keep 'which provider' a decision made once, at the root."""
        import src.manager.brain_factory as module

        # Imports, not prose - the docstring legitimately draws the chain
        # Settings -> build_provider -> LLMProvider -> brain.
        source = open(module.__file__, encoding="utf-8").read()
        assert "from src.providers.factory import" not in source
        assert "from src.providers.null_provider import" not in source
        assert "from src.providers.anthropic_provider import" not in source
        assert "from src.providers.base import LLMProvider" in source


# ----------------------------------------------------------------------
# The full chain: Settings -> provider -> brain -> Manager
# ----------------------------------------------------------------------


class TestTheChainAssembles:
    def _assemble(self, **overrides):
        config = settings(**overrides)
        provider = build_provider(config)
        return ManagerAgent(brain=build_brain(config, provider)), provider

    def test_the_manager_is_constructed_with_a_brain(self):
        manager, _ = self._assemble()
        assert isinstance(manager.brain, ManagerBrain)

    def test_the_brain_receives_the_factory_built_provider(self):
        manager, provider = self._assemble(brain="llm")
        assert manager.brain.provider_name == provider.name

    def test_the_default_chain_needs_no_api_key(self):
        config = settings()
        assert not config.has_api_key
        manager, _ = self._assemble()
        assert manager.brain is not None

    def test_the_default_chain_produces_a_working_manager(self):
        """Assembled from configuration alone, it still runs the DATA loop."""
        manager, _ = self._assemble()
        manager.register_agent(EchoAgent(name="alpha_agent", capabilities={"email"}))
        result = manager.handle_task(Task(description="check my email"))
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "alpha_agent"

    def test_an_llm_brain_on_the_default_provider_is_still_offline(self):
        """MANAGER_BRAIN=llm alone does not turn anything on."""
        manager, provider = self._assemble(brain="llm")
        assert isinstance(provider, NullProvider)
        decision = manager.deliberate(Task(description="what should I do?"))
        assert decision.action is BrainAction.UNKNOWN

    def test_turning_on_reasoning_takes_two_settings(self):
        assert settings().brain == "null"
        assert settings().provider == "null"


# ----------------------------------------------------------------------
# Nothing is loaded, called or required at construction time
# ----------------------------------------------------------------------


class TestConstructionIsInert:
    def test_no_anthropic_sdk_is_imported_on_the_default_path(self):
        config = settings()
        build_brain(config, build_provider(config))
        assert "anthropic" not in sys.modules

    def test_no_network_access_during_construction(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)

        config = settings(brain="llm")
        manager = ManagerAgent(brain=build_brain(config, build_provider(config)))
        assert manager.brain is not None

    def test_selecting_anthropic_constructs_without_calling_it(self):
        """The provider and brain are built; no request is made. The SDK client
        is created on first use, not at construction."""
        config = settings(provider="anthropic", brain="llm", api_key="test-key")
        provider = build_provider(config)
        brain = build_brain(config, provider)

        assert isinstance(brain, LLMManagerBrain)
        assert brain.provider_name == "anthropic"
        # The client is still unbuilt: nothing has been sent, nothing asked for.
        assert provider._client is None

    def test_selecting_anthropic_does_not_even_load_the_sdk(self):
        """Stronger than "no call": the vendor package is not imported at all.

        The import lives inside AnthropicProvider._build_client, so choosing the
        provider costs nothing until something actually asks it to complete.
        The credential check added for fail-fast reads the environment rather
        than asking the SDK, precisely so this stays true.
        """
        config = settings(provider="anthropic", brain="llm", api_key="test-key")
        build_brain(config, build_provider(config))
        assert "anthropic" not in sys.modules

    def test_selecting_anthropic_without_a_key_fails_clearly(self):
        """Retargeted. This previously asserted construction succeeded with no
        key, which let a misconfiguration disguise itself as a brain with no
        opinions - LLMManagerBrain turns any provider failure into "no view".
        A missing credential is now reported at startup instead.
        """
        config = settings(provider="anthropic", brain="llm")
        assert not config.has_api_key

        with pytest.raises(ProviderConfigurationError) as exc:
            build_provider(config, env={})

        message = str(exc.value)
        assert "ANTHROPIC_API_KEY" in message and "MANAGER_PROVIDER=null" in message

    def test_an_api_call_would_only_happen_on_deliberate(self, monkeypatch):
        """Proves *when* a call would occur, without making one.

        A provider that raises on use stands in for the real one. Construction
        passes; the first call to deliberate() is where it reaches out - and
        even then the failure is contained rather than raised.
        """

        class WouldCallOut(LLMProvider):
            def __init__(self):
                self.calls = 0

            @property
            def name(self):
                return "would-call-out"

            def complete(self, prompt, *, system=None):
                self.calls += 1
                raise AssertionError("a request would have been sent here")

        provider = WouldCallOut()
        manager = ManagerAgent(brain=build_brain(settings(brain="llm"), provider))
        assert provider.calls == 0                      # construction called nothing

        decision = manager.deliberate(Task(description="what should I do?"))
        assert provider.calls == 1                      # deliberate() is the moment
        assert decision.action is BrainAction.UNKNOWN   # and it stays contained


# ----------------------------------------------------------------------
# The Manager stays vendor-agnostic
# ----------------------------------------------------------------------


class TestTheManagerStaysVendorAgnostic:
    def test_the_manager_module_still_names_no_provider(self):
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read().lower()
        assert "provider" not in source
        assert "anthropic" not in source

    def test_the_manager_module_was_not_modified_for_this_wiring(self):
        """The brain parameter already existed; wiring needed no change here."""
        import src.manager.manager as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "build_brain" not in source
        assert "brain_factory" not in source

    def test_the_manager_receives_a_finished_brain(self):
        """It is handed a collaborator, never the means to build one."""
        config = settings(brain="llm")
        manager = ManagerAgent(brain=build_brain(config, build_provider(config)))
        for attribute in ("settings", "provider", "_provider", "config"):
            assert not hasattr(manager, attribute), attribute


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


class TestTheBrainSetting:
    def test_it_defaults_to_null(self):
        assert Settings().brain == "null"

    def test_it_is_read_from_the_environment(self):
        assert Settings.from_env(env={"MANAGER_BRAIN": "llm"}).brain == "llm"

    def test_it_is_normalised(self):
        assert Settings.from_env(env={"MANAGER_BRAIN": "  LLM  "}).brain == "llm"

    def test_an_empty_value_is_rejected(self):
        with pytest.raises(ValueError):
            Settings(brain="   ")

    def test_it_appears_in_describe(self):
        assert "brain=null" in Settings().describe()

    def test_settings_still_does_not_know_which_brains_exist(self):
        """Resolving a name is the factory's job, not configuration's."""
        assert Settings(brain="telepathy").brain == "telepathy"


# ----------------------------------------------------------------------
# The composition roots
# ----------------------------------------------------------------------


class TestTheCompositionRoots:
    def test_main_builds_a_brain_and_passes_it_to_the_manager(self, monkeypatch, capsys):
        """Runs main() for real, intercepting only the Manager constructor."""
        import main as main_module

        captured = {}
        real_manager = main_module.ManagerAgent

        class RecordingManager(real_manager):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(main_module, "ManagerAgent", RecordingManager)
        # A fixed Settings object, so main() reads no environment at all.
        fixed = settings()
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: fixed)
        )
        # Logging configuration is global process state; a wiring test must not
        # reach out and reconfigure it for every test that runs afterwards.
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)

        main_module.main()
        capsys.readouterr()

        assert "brain" in captured
        assert isinstance(captured["brain"], ManagerBrain)

    def test_main_reaches_the_brain_through_the_factory(self, monkeypatch, capsys):
        import main as main_module

        seen = {}
        real_build = main_module.build_brain

        def recording_build(config, provider):
            seen["provider"] = provider
            return real_build(config, provider)

        monkeypatch.setattr(main_module, "build_brain", recording_build)
        # A fixed Settings object, so main() reads no environment at all.
        fixed = settings()
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: fixed)
        )
        # Logging configuration is global process state; a wiring test must not
        # reach out and reconfigure it for every test that runs afterwards.
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)

        main_module.main()
        capsys.readouterr()

        # The provider handed to the brain is the one the provider factory made.
        assert isinstance(seen["provider"], LLMProvider)
        assert seen["provider"].name == "null"

    def test_the_heartbeat_root_wires_a_brain_too(self):
        """The second composition root, assembled for real with no Gmail."""
        sys.path.insert(0, ".")
        from scripts.heartbeat import build_manager_factory

        from src.services.profile.profile import UserProfile

        build = build_manager_factory(settings(brain="llm"), dry_run=True)
        manager = build(UserProfile())

        assert isinstance(manager.brain, LLMManagerBrain)
        assert manager.brain.provider_name == "null"

    def test_the_heartbeat_root_defaults_to_a_null_brain(self):
        sys.path.insert(0, ".")
        from scripts.heartbeat import build_manager_factory

        from src.services.profile.profile import UserProfile

        manager = build_manager_factory(settings(), dry_run=True)(UserProfile())
        assert isinstance(manager.brain, NullBrain)
