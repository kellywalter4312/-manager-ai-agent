"""Tests for configuration.

Every test passes an explicit ``env`` dict. None of them touch os.environ, so
they cannot interfere with each other or with the machine they run on.
"""

import pytest

from src.config.settings import Settings


class TestDefaults:
    def test_works_with_no_configuration_at_all(self):
        settings = Settings.from_env(env={})
        assert settings.log_level == "INFO"
        assert settings.provider == "null"

    def test_ignores_unrelated_environment_variables(self):
        settings = Settings.from_env(env={"PATH": "/usr/bin", "LOG_LEVEL": "DEBUG"})
        # Unprefixed LOG_LEVEL belongs to something else and must not be read.
        assert settings.log_level == "INFO"


class TestEnvironment:
    def test_reads_prefixed_variables(self):
        settings = Settings.from_env(env={"MANAGER_LOG_LEVEL": "DEBUG"})
        assert settings.log_level == "DEBUG"

    def test_normalizes_case_and_whitespace(self):
        settings = Settings.from_env(env={"MANAGER_LOG_LEVEL": "  debug  "})
        assert settings.log_level == "DEBUG"

    def test_reads_the_provider_name(self):
        assert Settings.from_env(env={"MANAGER_PROVIDER": "NULL"}).provider == "null"


class TestPrecedence:
    def test_explicit_argument_beats_environment(self):
        settings = Settings.from_env(
            env={"MANAGER_LOG_LEVEL": "DEBUG"}, log_level="ERROR"
        )
        assert settings.log_level == "ERROR"

    def test_environment_beats_default(self):
        assert Settings.from_env(env={"MANAGER_LOG_LEVEL": "ERROR"}).log_level == "ERROR"

    def test_none_override_does_not_win(self):
        """None means 'not specified' and must fall through to the env value."""
        settings = Settings.from_env(env={"MANAGER_LOG_LEVEL": "DEBUG"}, log_level=None)
        assert settings.log_level == "DEBUG"


class TestValidation:
    def test_rejects_an_invalid_log_level(self):
        with pytest.raises(ValueError) as exc:
            Settings(log_level="CHATTY")
        assert "CHATTY" in str(exc.value)

    def test_rejects_an_empty_provider(self):
        with pytest.raises(ValueError):
            Settings(provider="   ")

    def test_invalid_environment_value_fails_at_startup(self):
        with pytest.raises(ValueError):
            Settings.from_env(env={"MANAGER_LOG_LEVEL": "loud"})

    def test_does_not_validate_that_the_provider_exists(self):
        """Resolving a provider name is the factory's job, not config's."""
        assert Settings(provider="not_a_real_provider").provider == "not_a_real_provider"


class TestImmutabilityAndSafety:
    def test_settings_are_frozen(self):
        settings = Settings()
        with pytest.raises(Exception):
            settings.log_level = "DEBUG"

    def test_describe_is_a_short_single_line(self):
        described = Settings().describe()
        assert "\n" not in described
        assert "log_level=INFO" in described and "provider=null" in described
