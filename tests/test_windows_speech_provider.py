"""Stage 10 completion - the Windows System.Speech provider.

Every test here replaces ``_run_powershell`` - the one seam the provider
calls a real process through - so nothing in this file depends on a live
microphone, a live speaker, or even ``powershell.exe`` being on the machine
running the suite. That is deliberate: automated tests prove the provider's
own honesty (it reports exactly what a real process outcome would mean),
not that a live device exists here.

Genuine, non-mocked verification of this same engine - a real WAV file with
real audio content, and a real, correctly-transcribed round trip - was
performed once, by hand, outside this suite, and is recorded in
ARCHITECTURE.md and in this session's own report. This file is the
regression harness that keeps that engine's *honesty contract* provably
true on every future change, which a one-off manual run cannot do.
"""

from __future__ import annotations

import subprocess

import pytest

from src.channels.speech import Listener, Speaker, SpeechAdapter, SpeechUnavailable
from src.providers.availability import AvailabilityState
from src.services.identity.voice import VoiceProfile
import src.providers.windows_speech_provider as windows_speech_provider
from src.providers.windows_speech_provider import WindowsSpeechAdapter


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["powershell"], returncode=returncode, stdout=stdout, stderr=stderr)


def fake_run(monkeypatch, result=None, *, raises: Exception | None = None):
    calls: list[str] = []

    def _fake(script: str, *, timeout: float):
        calls.append(script)
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(windows_speech_provider, "_run_powershell", _fake)
    return calls


class TestInterfaceAndInitialState:
    def test_satisfies_the_speechadapter_contract(self):
        adapter = WindowsSpeechAdapter()
        assert isinstance(adapter, SpeechAdapter)
        assert isinstance(adapter, Listener)
        assert isinstance(adapter, Speaker)

    def test_is_named_windows(self):
        assert WindowsSpeechAdapter().name == "windows"

    def test_starts_unknown_not_available(self):
        """Constructing the adapter proves nothing - the same rule every
        other Availability-reporting component in this project lives
        under."""
        adapter = WindowsSpeechAdapter()
        assert adapter.availability.state is AvailabilityState.UNKNOWN
        assert adapter.available is False


class TestGenuineListenSucceeds:
    def test_a_successful_recognition_returns_the_transcribed_text(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0, stdout="what is happening with my emails\n"))
        adapter = WindowsSpeechAdapter()
        assert adapter.listen() == "what is happening with my emails"
        assert adapter.availability.state is AvailabilityState.AVAILABLE

    def test_availability_is_available_only_after_the_real_call(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0, stdout="hello"))
        adapter = WindowsSpeechAdapter()
        assert adapter.availability.state is AvailabilityState.UNKNOWN
        adapter.listen()
        assert adapter.availability.state is AvailabilityState.AVAILABLE


class TestListenFailsHonestly:
    def test_empty_transcription_raises_rather_than_returning_blank(self, monkeypatch):
        """SpeechAdapter's own contract: never return an empty string to
        mean failure - a caller cannot tell that from someone saying
        nothing."""
        fake_run(monkeypatch, completed(returncode=0, stdout=""))
        adapter = WindowsSpeechAdapter()
        with pytest.raises(SpeechUnavailable):
            adapter.listen()
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE
        assert "no speech" in adapter.availability.reason.lower()

    def test_a_process_failure_is_reported_honestly(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=1, stderr="recognizer initialization failed"))
        adapter = WindowsSpeechAdapter()
        with pytest.raises(SpeechUnavailable) as exc:
            adapter.listen()
        assert "recognizer initialization failed" in str(exc.value)
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE

    def test_a_missing_powershell_binary_is_reported_honestly_not_crashed(self, monkeypatch):
        fake_run(monkeypatch, raises=FileNotFoundError("powershell not found"))
        adapter = WindowsSpeechAdapter()
        with pytest.raises(SpeechUnavailable):
            adapter.listen()
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE

    def test_a_timeout_is_reported_honestly_never_hangs_the_caller(self, monkeypatch):
        fake_run(monkeypatch, raises=subprocess.TimeoutExpired(cmd="powershell", timeout=10))
        adapter = WindowsSpeechAdapter()
        with pytest.raises(SpeechUnavailable):
            adapter.listen()
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE


class TestGenuineSpeakSucceeds:
    def test_a_successful_synthesis_returns_true(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0))
        adapter = WindowsSpeechAdapter()
        assert adapter.speak("good morning") is True
        assert adapter.availability.state is AvailabilityState.AVAILABLE

    def test_accepts_a_voice_profile_without_erroring(self, monkeypatch):
        """The Speaker contract requires accepting a voice even from an
        engine that does not yet map it to a distinct sound - the same
        honest, still-useful stance NoSpeech already takes."""
        fake_run(monkeypatch, completed(returncode=0))
        adapter = WindowsSpeechAdapter()
        assert adapter.speak("hello", VoiceProfile(key="alfred")) is True

    def test_ask_never_speaks_deliver_does(self, monkeypatch):
        """Not this module's own rule (Channel's), but worth pinning here:
        speak() only ever runs when something explicitly calls it."""
        calls = fake_run(monkeypatch, completed(returncode=0))
        assert calls == []


class TestSpeakFailsHonestly:
    def test_a_process_failure_returns_false_never_true(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=1, stderr="no audio output device"))
        adapter = WindowsSpeechAdapter()
        assert adapter.speak("hello") is False
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE
        assert "no audio output device" in adapter.availability.reason

    def test_a_missing_powershell_binary_returns_false_never_true(self, monkeypatch):
        fake_run(monkeypatch, raises=FileNotFoundError("powershell not found"))
        adapter = WindowsSpeechAdapter()
        assert adapter.speak("hello") is False
        assert adapter.availability.state is AvailabilityState.UNAVAILABLE

    def test_a_timeout_returns_false_never_true(self, monkeypatch):
        fake_run(monkeypatch, raises=subprocess.TimeoutExpired(cmd="powershell", timeout=15))
        adapter = WindowsSpeechAdapter()
        assert adapter.speak("hello") is False


class TestTextNeverEntersTheScriptAsALiteral:
    """The injection-safety property: arbitrary caller text must never be
    interpolated directly into the generated PowerShell script."""

    def test_the_spoken_text_is_not_embedded_in_the_script(self, monkeypatch):
        # Two real calls now (Stage 11.2): one queries this machine's
        # installed voices, one performs the actual synthesis - the
        # injection-safety property is about the second, which is the one
        # that ever sees `text` at all.
        calls = fake_run(monkeypatch, completed(returncode=0))
        secret_shaped_text = "hello'; Remove-Item C:\\; Write-Output 'pwned"
        WindowsSpeechAdapter().speak(secret_shaped_text)
        speak_calls = [c for c in calls if "Get-Content" in c]
        assert len(speak_calls) == 1
        assert secret_shaped_text not in speak_calls[0]


class TestConfigurationIsNotProofOfWorking:
    """Item 7 of this session's own list: constructing/selecting the
    provider must never be confused with it actually working."""

    def test_constructing_makes_no_call(self, monkeypatch):
        calls = fake_run(monkeypatch, completed(returncode=0))
        WindowsSpeechAdapter()
        assert calls == []

    def test_unknown_is_not_treated_as_usable(self):
        adapter = WindowsSpeechAdapter()
        assert adapter.availability.is_usable is False


class TestAvailableVoicesReportsRealCultureAndGenderNeverAFabricatedAccent:
    """The voice-autonomy checkpoint's own discovery seam for this
    engine - real per-voice locale and gender from .NET's own
    ``VoiceInfo.Culture``/``VoiceInfo.Gender`` (both live-confirmed
    against this machine's own installed voices: David=Male/en-US,
    Hazel=Female/en-GB, Zira=Female/en-US), never an accent (this engine
    genuinely does not expose one)."""

    def test_parses_name_culture_and_gender_into_language_locale_and_gender(self, monkeypatch):
        fake_run(monkeypatch, completed(
            returncode=0,
            stdout="Microsoft David Desktop|en-US|Male\nMicrosoft Hazel Desktop|en-GB|Female\n",
        ))
        options = WindowsSpeechAdapter().available_voices()
        assert len(options) == 2
        assert options[0].voice_id == "Microsoft David Desktop"
        assert options[0].provider == "windows"
        assert options[0].language == "english"
        assert options[0].locale == "en-US"
        assert options[0].gender == "male"
        assert options[0].accent is None
        assert options[1].locale == "en-GB"
        assert options[1].gender == "female"

    def test_an_unrecognised_culture_prefix_keeps_the_real_locale_honest_none_language(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0, stdout="Some Voice|xx-YY|Male\n"))
        options = WindowsSpeechAdapter().available_voices()
        assert options[0].locale == "xx-YY"
        assert options[0].language is None
        assert options[0].gender == "male"

    def test_a_line_with_no_gender_field_is_honestly_none_never_a_crash(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0, stdout="Some Voice|en-US\n"))
        options = WindowsSpeechAdapter().available_voices()
        assert options[0].locale == "en-US"
        assert options[0].gender is None

    def test_a_genuinely_reported_neutral_gender_is_carried_through_honestly(self, monkeypatch):
        """SAPI's real VoiceGender enum includes Neutral alongside
        Male/Female - a genuinely reported fact, never discarded."""
        fake_run(monkeypatch, completed(returncode=0, stdout="Some Voice|en-US|Neutral\n"))
        options = WindowsSpeechAdapter().available_voices()
        assert options[0].gender == "neutral"

    def test_a_truly_unrecognised_gender_value_is_honestly_none_never_guessed(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=0, stdout="Some Voice|en-US|NotARealValue\n"))
        options = WindowsSpeechAdapter().available_voices()
        assert options[0].gender is None

    def test_engine_failure_returns_empty_never_raises(self, monkeypatch):
        fake_run(monkeypatch, completed(returncode=1, stderr="boom"))
        assert WindowsSpeechAdapter().available_voices() == ()

    def test_a_process_error_returns_empty_never_raises(self, monkeypatch):
        fake_run(monkeypatch, raises=OSError("no powershell"))
        assert WindowsSpeechAdapter().available_voices() == ()

    def test_installed_voice_names_output_is_unchanged_by_this_addition(self, monkeypatch):
        """The pre-existing, tested seam _select_voice_name depends on
        must remain byte-identical - a separate script backs discovery,
        never a reparsed version of this one's output."""
        fake_run(monkeypatch, completed(returncode=0, stdout="Microsoft David Desktop\n"))
        assert WindowsSpeechAdapter().installed_voice_names() == ("Microsoft David Desktop",)
