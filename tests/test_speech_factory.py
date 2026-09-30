"""Stage 10 - the speech provider factory.

Mirrors tests/test_providers.py's own TestFactory class deliberately: that
file proves build_provider selects an LLMProvider by name, honestly, with no
fallback-by-inference; this file proves build_speech does the exact same
thing for a SpeechAdapter. "none" (NoSpeech) and "windows"
(WindowsSpeechAdapter, see src/providers/windows_speech_provider.py and its
own test file) are the two registered entries - still no cloud vendor SDK,
by deliberate choice documented in speech_factory.py's own docstring.
"""

from __future__ import annotations

import platform

import pytest

from src.channels.speech import Listener, NoSpeech, Speaker, SpeechAdapter
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.providers.speech_factory import (
    SpeechConfigurationError,
    available_speech_providers,
    build_speech,
)


class TestSettingsSpeechField:
    def test_defaults_to_none(self):
        assert Settings().speech == "none"

    def test_configurable_via_environment(self):
        assert Settings.from_env(env={"MANAGER_SPEECH": "none"}).speech == "none"

    def test_rejects_a_blank_value(self):
        with pytest.raises(ValueError):
            Settings(speech="   ")

    def test_is_lower_cased(self):
        assert Settings(speech="NONE").speech == "none"

    def test_describe_reports_it(self):
        assert "speech=none" in Settings().describe()


class TestFactory:
    def test_builds_no_speech_by_default(self):
        assert isinstance(build_speech(Settings()), NoSpeech)

    def test_lists_available_speech_providers(self):
        assert "none" in available_speech_providers()

    def test_rejects_an_unknown_speech_provider_with_a_helpful_message(self):
        with pytest.raises(ValueError) as exc:
            build_speech(Settings(speech="magic-mic-9000"))
        message = str(exc.value)
        assert "magic-mic-9000" in message
        assert "none" in message  # tells the caller what IS available

    def test_an_unregistered_name_never_silently_falls_back(self):
        """A misconfiguration must fail loudly, not quietly become 'none' -
        the same rule build_provider already lives under for an unknown
        model provider name."""
        with pytest.raises(ValueError):
            build_speech(Settings(speech="cloud-vendor-nobody-configured"))

    def test_a_custom_speech_provider_satisfies_the_interface(self):
        class FakeSpeech(SpeechAdapter):
            @property
            def name(self):
                return "fake"

            @property
            def availability(self):
                return Availability(state=AvailabilityState.AVAILABLE)

            def listen(self, timeout=None):
                return "hello"

            def speak(self, text, voice=None):
                return True

        engine = FakeSpeech()
        assert isinstance(engine, SpeechAdapter)
        assert isinstance(engine, Listener)
        assert isinstance(engine, Speaker)
        assert engine.available is True


class TestWindowsProvider:
    def test_windows_is_listed(self):
        assert "windows" in available_speech_providers()

    def test_selecting_windows_on_a_non_windows_platform_fails_closed(self, monkeypatch):
        """A structural, deterministic configuration check - not
        availability - the same role _anthropic_credential plays for the
        LLM factory. Monkeypatched so this proves the guard exists
        regardless of which platform actually runs the suite."""
        import src.providers.speech_factory as speech_factory_module

        monkeypatch.setattr(speech_factory_module.platform, "system", lambda: "Linux")
        with pytest.raises(SpeechConfigurationError) as exc:
            build_speech(Settings(speech="windows"))
        assert "not a windows machine" in str(exc.value).lower()

    @pytest.mark.skipif(
        platform.system() != "Windows", reason="the windows provider only builds on Windows"
    )
    def test_builds_a_real_windowsspeechadapter_on_windows(self):
        from src.providers.windows_speech_provider import WindowsSpeechAdapter

        speech = build_speech(Settings(speech="windows"))
        assert isinstance(speech, WindowsSpeechAdapter)
        # Constructing it makes no call and proves no usability by itself.
        assert speech.availability.state is AvailabilityState.UNKNOWN


class TestElevenLabsProvider:
    def test_elevenlabs_is_listed(self):
        assert "elevenlabs" in available_speech_providers()

    def test_selecting_it_without_a_key_fails_closed(self):
        with pytest.raises(SpeechConfigurationError) as exc:
            build_speech(Settings(speech="elevenlabs"), env={})
        message = str(exc.value)
        assert "ELEVENLABS_API_KEY" in message
        assert "MANAGER_SPEECH=none" in message

    def test_a_blank_key_counts_as_missing(self):
        with pytest.raises(SpeechConfigurationError):
            build_speech(Settings(speech="elevenlabs"), env={"ELEVENLABS_API_KEY": "   "})

    def test_building_it_with_a_key_works(self):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter

        speech = build_speech(Settings(speech="elevenlabs"), env={"ELEVENLABS_API_KEY": "fake"})
        assert isinstance(speech, ElevenLabsSpeechAdapter)
        # Constructing it makes no call and proves no usability by itself.
        assert speech.availability.state is AvailabilityState.UNKNOWN

    def test_the_failure_happens_before_the_vendor_sdk_is_imported(self):
        import sys

        for name in list(sys.modules):
            if name == "elevenlabs" or name.startswith("elevenlabs."):
                del sys.modules[name]

        with pytest.raises(SpeechConfigurationError):
            build_speech(Settings(speech="elevenlabs"), env={})
        assert "elevenlabs" not in sys.modules

    def test_windows_and_elevenlabs_remain_independently_selectable(self):
        """Requirement 7: adding elevenlabs must not disturb the existing
        provider - both stay real, distinct, registered entries."""
        assert {"none", "windows", "elevenlabs"} <= set(available_speech_providers())


class TestElevenLabsAccountSeam:
    """Universal Ecosystem checkpoint: the same optional ``account``
    seam ``build_provider``/``build_media`` already give the Brain's and
    video-generation's own credentials, extended to voice - completing
    Section 6's own request that voice support account-managed
    credentials, not merely a raw environment variable."""

    def test_an_account_is_sufficient_with_no_environment_credential_at_all(self):
        """Proves the account path is genuinely used, not merely
        accepted and ignored: with no ELEVENLABS_API_KEY in the
        environment, construction would otherwise fail closed
        (test_selecting_it_without_a_key_fails_closed above) - it
        succeeds here only because the account supplied one."""
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter
        from src.services.credentials.account import ProviderAccount

        account = ProviderAccount(provider="elevenlabs", account_id="default", credential="account-key")
        speech = build_speech(Settings(speech="elevenlabs"), env={}, account=account)
        assert isinstance(speech, ElevenLabsSpeechAdapter)

    def test_a_mismatched_account_is_refused_not_silently_accepted(self):
        from src.services.credentials.account import ProviderAccount

        wrong_account = ProviderAccount(provider="anthropic", account_id="default", credential="claude-key")
        with pytest.raises(SpeechConfigurationError):
            build_speech(Settings(speech="elevenlabs"), env={}, account=wrong_account)

    def test_omitting_the_account_reproduces_prior_behaviour_exactly(self):
        pytest.importorskip("elevenlabs", reason="elevenlabs SDK not installed")
        from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter

        speech = build_speech(Settings(speech="elevenlabs"), env={"ELEVENLABS_API_KEY": "env-key"})
        assert isinstance(speech, ElevenLabsSpeechAdapter)


class TestKokoroProvider:
    def test_kokoro_is_listed(self):
        assert "kokoro" in available_speech_providers()

    def test_building_it_needs_no_credential(self):
        from src.providers.kokoro_speech_provider import KokoroSpeechAdapter

        speech = build_speech(Settings(speech="kokoro"), env={})
        assert isinstance(speech, KokoroSpeechAdapter)
        # Constructing it makes no call and proves no usability by itself.
        assert speech.availability.state is AvailabilityState.UNKNOWN

    def test_the_failure_happens_before_the_engine_package_is_imported(self):
        """Selecting kokoro must not eagerly import kokoro_onnx - the same
        deferred-import discipline every other builder here follows."""
        import sys

        had = "kokoro_onnx" in sys.modules
        build_speech(Settings(speech="kokoro"), env={})
        if not had:
            assert "kokoro_onnx" not in sys.modules

    def test_windows_elevenlabs_and_kokoro_remain_independently_selectable(self):
        """Adding kokoro must not disturb the existing providers - all
        three stay real, distinct, registered entries."""
        assert {"none", "windows", "elevenlabs", "kokoro"} <= set(available_speech_providers())


class TestConfigurationIsNeverTreatedAsProofOfWorkingVoice:
    """Item 19: 'API key exists' (or 'a provider name was selected') must
    never be treated as 'STT/TTS works' - only an actual call's outcome is."""

    def test_selecting_a_provider_reports_availability_not_success(self):
        speech = build_speech(Settings(speech="none"))
        # Constructing/selecting the adapter proves nothing about whether it
        # can hear or speak - only availability, evidence from an actual
        # call, can say that.
        assert speech.available is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE

    def test_building_the_adapter_makes_no_call_and_no_network_access(self, monkeypatch):
        import socket

        def _no_network(*a, **k):
            raise AssertionError("build_speech must not touch the network")

        monkeypatch.setattr(socket, "socket", _no_network)
        build_speech(Settings())  # must not raise
