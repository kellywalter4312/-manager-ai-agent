"""Tests for configurations that are individually valid and jointly wrong.

Two combinations run perfectly while doing something other than what was asked:

    provider set, nothing consumes it    the model is never called
    llm component, provider=null         every task falls back, forever

Neither used to say anything. The tests that matter most here are the ones
proving the *default* stays silent - a check that cries wolf on the normal
configuration would be turned off within a day, and then it protects nothing.

Nothing in this file builds a provider, a router or a brain. The check works on
configured names alone, which is what lets it run before anything is
constructed.
"""

import inspect

import pytest

from src.config.coherence import ConfigurationError, check_configuration
from src.config.settings import Settings


def settings(**overrides):
    """Settings from an empty environment, so tests never read os.environ."""
    return Settings.from_env(env={}, **overrides)


def error_from(**overrides) -> str:
    with pytest.raises(ConfigurationError) as exc:
        check_configuration(settings(**overrides))
    return str(exc.value)


# ----------------------------------------------------------------------
# Valid configurations stay silent
# ----------------------------------------------------------------------


class TestValidConfigurationsPass:
    def test_the_default_is_coherent(self):
        """provider=null, router=capability, brain=null - nothing asks for a
        model and none is configured."""
        assert check_configuration(settings()) is None

    def test_the_default_is_what_we_think_it_is(self):
        config = settings()
        assert (config.provider, config.router, config.brain) == ("null", "capability", "null")

    def test_provider_with_an_llm_router(self):
        assert check_configuration(settings(provider="anthropic", router="llm")) is None

    def test_provider_with_an_llm_brain(self):
        assert check_configuration(settings(provider="anthropic", brain="llm")) is None

    def test_provider_with_both(self):
        config = settings(provider="anthropic", router="llm", brain="llm")
        assert check_configuration(config) is None

    def test_deterministic_routing_with_no_provider(self):
        assert check_configuration(settings(router="capability", brain="null")) is None

    def test_an_unknown_provider_name_is_not_this_check_s_business(self):
        """Resolving names belongs to the factory. This check only asks whether
        the pieces fit together."""
        assert check_configuration(settings(provider="telepathy", router="llm")) is None


# ----------------------------------------------------------------------
# A provider nothing consumes
# ----------------------------------------------------------------------


class TestAProviderNothingConsumes:
    def test_it_is_rejected(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(provider="anthropic"))

    def test_the_message_says_the_provider_would_be_discarded(self):
        assert "discarded" in error_from(provider="anthropic")

    def test_the_message_names_the_provider(self):
        assert "MANAGER_PROVIDER=anthropic" in error_from(provider="anthropic")

    def test_the_message_offers_both_consumers(self):
        message = error_from(provider="anthropic")
        assert "MANAGER_ROUTER=llm" in message
        assert "MANAGER_BRAIN=llm" in message

    def test_the_message_offers_the_deterministic_way_out(self):
        assert "MANAGER_PROVIDER=null" in error_from(provider="anthropic")

    def test_it_applies_to_any_non_null_provider(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(provider="some-future-vendor"))

    def test_an_explicitly_deterministic_pair_is_still_rejected(self):
        with pytest.raises(ConfigurationError):
            check_configuration(
                settings(provider="anthropic", router="capability", brain="null")
            )


# ----------------------------------------------------------------------
# An LLM component with no model behind it
# ----------------------------------------------------------------------


class TestAnLLMComponentWithNoProvider:
    def test_an_llm_router_is_rejected(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(router="llm"))

    def test_an_llm_brain_is_rejected(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(brain="llm"))

    def test_both_are_rejected_together(self):
        with pytest.raises(ConfigurationError):
            check_configuration(settings(router="llm", brain="llm"))

    def test_the_message_names_the_selected_component(self):
        assert "MANAGER_ROUTER=llm" in error_from(router="llm")
        assert "MANAGER_BRAIN=llm" in error_from(brain="llm")

    def test_the_message_names_only_what_was_selected(self):
        """Reporting a component the user did not configure would send them
        looking for a setting they never wrote."""
        assert "MANAGER_BRAIN" not in error_from(router="llm")

    def test_the_message_names_both_when_both_are_selected(self):
        message = error_from(router="llm", brain="llm")
        assert "MANAGER_ROUTER=llm" in message
        assert "MANAGER_BRAIN=llm" in message

    def test_the_message_explains_the_silent_failure(self):
        message = error_from(router="llm")
        assert "indistinguishable" in message

    def test_the_message_says_how_to_fix_it(self):
        assert "MANAGER_PROVIDER=anthropic" in error_from(router="llm")


# ----------------------------------------------------------------------
# Secrets never appear
# ----------------------------------------------------------------------


class TestNoSecretsLeak:
    def test_the_key_is_absent_from_a_discarded_provider_message(self):
        message = error_from(provider="anthropic", api_key="sk-ant-SECRET-VALUE")
        assert "SECRET" not in message
        assert "sk-ant" not in message

    def test_the_key_is_absent_from_a_missing_provider_message(self):
        message = error_from(router="llm", api_key="sk-ant-SECRET-VALUE")
        assert "SECRET" not in message

    def test_the_module_never_reads_the_key(self):
        """Structural, not incidental: it cannot leak what it does not touch."""
        import src.config.coherence as module

        assert "api_key" not in inspect.getsource(module.check_configuration)


# ----------------------------------------------------------------------
# The check stays out of the lower layers
# ----------------------------------------------------------------------


class TestItLivesOnlyAtTheCompositionRoots:
    def test_the_provider_factory_does_not_call_it(self):
        """Constructing a provider in isolation must stay legal - every
        provider test does it."""
        import src.providers.factory as module

        assert "check_configuration" not in inspect.getsource(module)

    def test_the_router_factory_does_not_call_it(self):
        import src.manager.router_factory as module

        assert "check_configuration" not in inspect.getsource(module)

    def test_the_brain_factory_does_not_call_it(self):
        import src.manager.brain_factory as module

        assert "check_configuration" not in inspect.getsource(module)

    def test_the_factories_still_build_incoherent_combinations(self):
        """The check is a startup policy, not a construction rule."""
        from src.manager.router_factory import build_router
        from src.providers.factory import build_provider

        config = settings(router="llm")
        assert build_router(config, build_provider(config, env={})) is not None

    def test_it_imports_no_provider_or_router_package(self):
        """It reasons about configured names as strings. Importing what it
        validates would rebuild the coupling it exists to avoid."""
        import src.config.coherence as module

        source = inspect.getsource(module)
        assert "from src.providers" not in source
        assert "from src.manager" not in source

    def test_settings_itself_is_unchanged_by_this(self):
        assert not hasattr(Settings, "check_configuration")


# ----------------------------------------------------------------------
# The composition roots use it
# ----------------------------------------------------------------------


class TestTheCompositionRootsUseIt:
    def test_main_exits_with_a_readable_message(self, monkeypatch, capsys):
        import main as main_module

        broken = settings(provider="anthropic")
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)

        with pytest.raises(SystemExit) as exit_info:
            main_module.main()

        assert exit_info.value.code == 2
        assert "configuration error" in capsys.readouterr().out

    def test_main_does_not_traceback(self, monkeypatch, capsys):
        import main as main_module

        broken = settings(router="llm")
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            main_module.main()
        assert "Traceback" not in capsys.readouterr().out

    def test_main_checks_before_building_anything(self, monkeypatch, capsys):
        """A provider that would have been discarded is never constructed."""
        import main as main_module

        broken = settings(provider="anthropic")
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)

        def refuse(*args, **kwargs):
            raise AssertionError("a provider was built despite an invalid configuration")

        monkeypatch.setattr(main_module, "build_provider", refuse)

        with pytest.raises(SystemExit):
            main_module.main()

    def test_the_heartbeat_stops_before_touching_the_mailbox(self, monkeypatch, capsys):
        import sys

        sys.path.insert(0, ".")
        import scripts.heartbeat as heartbeat

        broken = settings(provider="anthropic")
        monkeypatch.setattr(
            heartbeat.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(heartbeat, "configure_logging", lambda *a, **k: None)

        assert heartbeat.main(["--dry-run"]) == 2
        assert "configuration error" in capsys.readouterr().out

    def test_the_heartbeat_rejects_an_llm_router_with_no_provider(self, monkeypatch, capsys):
        import sys

        sys.path.insert(0, ".")
        import scripts.heartbeat as heartbeat

        broken = settings(router="llm")
        monkeypatch.setattr(
            heartbeat.Settings, "from_env", staticmethod(lambda *a, **k: broken)
        )
        monkeypatch.setattr(heartbeat, "configure_logging", lambda *a, **k: None)

        assert heartbeat.main(["--dry-run"]) == 2

    def test_the_default_still_runs_both_roots(self, monkeypatch):
        """The check must not have broken the ordinary path."""
        import main as main_module

        # Built before patching: settings() calls from_env, so constructing it
        # inside the lambda would recurse into the patch.
        default = settings()
        monkeypatch.setattr(
            main_module.Settings, "from_env", staticmethod(lambda *a, **k: default)
        )
        monkeypatch.setattr(main_module, "configure_logging", lambda *a, **k: None)
        main_module.main()  # no SystemExit


# ----------------------------------------------------------------------
# The error type
# ----------------------------------------------------------------------


class TestTheErrorType:
    def test_it_is_a_runtime_error(self):
        assert issubclass(ConfigurationError, RuntimeError)

    def test_it_is_distinct_from_a_provider_configuration_error(self):
        from src.providers.factory import ProviderConfigurationError

        assert not issubclass(ConfigurationError, ProviderConfigurationError)
        assert not issubclass(ProviderConfigurationError, ConfigurationError)
