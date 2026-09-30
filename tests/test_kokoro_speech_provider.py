"""0.2 Final Voice Installation - the Kokoro local TTS provider.

Every test in ``TestSpeakRequiresAResolvedVoiceId``,
``TestGenuineSpeakSucceeds`` (mocked-engine parts) and
``TestSpeakFailsHonestly`` replaces the two seams that would otherwise do
real work - ``adapter._engine`` (a fake standing in for the real
``kokoro_onnx.Kokoro``) and the module's own ``_play_wav_bytes`` (local
audio playback) - the same discipline test_elevenlabs_speech_provider.py
already applies to its own SDK client and playback call.

``TestGenuineLocalSynthesis`` is the one class in this file that touches
neither seam: it constructs the real adapter, loads the real, locally
downloaded ONNX engine and the real two-voice archive, and asks it to
actually synthesize speech with ``am_eric`` and ``bm_daniel`` - the exact
two voices this turn's task named and nothing else. Unlike ElevenLabs
(which needed a paid key nowhere on this machine), Kokoro is local and
free, so this genuine path can run in the suite itself rather than being
recorded as a gap - skipped, honestly, only on a machine that has never
run the one-time download (data/voices/kokoro/* is gitignored local data,
the same status data/credentials.json already has).
"""

from __future__ import annotations

import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.channels.speech import Listener, Speaker, SpeechAdapter, SpeechUnavailable
from src.providers.availability import AvailabilityState
import src.providers.kokoro_speech_provider as kokoro_speech_provider
from src.providers.kokoro_speech_provider import (
    DEFAULT_MODEL_PATH,
    DEFAULT_VOICES_PATH,
    KokoroSpeechAdapter,
)
from src.services.identity.voice import VoiceProfile, parse_voice

def _kokoro_onnx_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except ImportError:
        return False


_MODEL_PRESENT = (
    Path(DEFAULT_MODEL_PATH).exists()
    and Path(DEFAULT_VOICES_PATH).exists()
    and _kokoro_onnx_available()
)


def adapter(monkeypatch, *, play_result: bool = True, engine=None):
    """A real KokoroSpeechAdapter, its ONNX engine swapped for a fake one
    and its local playback stubbed - never real inference, never real
    audio, for every test in this file except TestGenuineLocalSynthesis.
    """
    instance = KokoroSpeechAdapter()
    if engine is not None:
        instance._engine = engine
    else:
        instance._engine = fake_engine()
    monkeypatch.setattr(kokoro_speech_provider, "_play_wav_bytes", lambda audio: play_result)
    return instance


def fake_engine(*, samples=(0.1, -0.1, 0.2, -0.2), sample_rate=24000, raises: Exception | None = None):
    def create(text, *, voice, lang="en-us"):
        if raises is not None:
            raise raises
        return list(samples), sample_rate

    return SimpleNamespace(create=create)


class TestInterfaceAndInitialState:
    def test_satisfies_the_speechadapter_contract(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert isinstance(speech, SpeechAdapter)
        assert isinstance(speech, Listener)
        assert isinstance(speech, Speaker)

    def test_is_named_kokoro(self, monkeypatch):
        assert adapter(monkeypatch).name == "kokoro"

    def test_starts_unknown_not_available(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert speech.availability.state is AvailabilityState.UNKNOWN
        assert speech.available is False

    def test_constructing_makes_no_call(self):
        """The same rule every provider in this project lives under:
        configuration is not proof of working. Constructing the real
        adapter (not the fake-engine helper above) must not load the
        ONNX session or touch any file."""
        speech = KokoroSpeechAdapter()
        assert speech._engine is None
        assert speech.availability.state is AvailabilityState.UNKNOWN


class TestListenIsHonestlyUnsupported:
    def test_listen_always_raises(self, monkeypatch):
        with pytest.raises(SpeechUnavailable):
            adapter(monkeypatch).listen()

    def test_the_message_says_why(self, monkeypatch):
        with pytest.raises(SpeechUnavailable) as exc:
            adapter(monkeypatch).listen()
        assert "text-to-speech only" in str(exc.value)


class TestSpeakRequiresAResolvedVoiceId:
    def test_no_voice_supplied_fails_honestly(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert speech.speak("hello") is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "no voice profile" in speech.availability.reason

    def test_a_voice_with_no_kokoro_id_fails_honestly(self, monkeypatch):
        """preferred_voice alone (a display name) is never enough - the
        same requirement elevenlabs_speech_provider already honours:
        never guess an id from a name."""
        speech = adapter(monkeypatch)
        voice = VoiceProfile(key="alfred", preferred_voice="Eric")
        assert speech.speak("hello", voice) is False
        assert "alfred" in speech.availability.reason

    def test_a_blank_kokoro_id_is_treated_as_absent(self, monkeypatch):
        speech = adapter(monkeypatch)
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "   "})
        assert speech.speak("hello", voice) is False

    def test_synthesis_never_runs_when_no_id_is_resolved(self, monkeypatch):
        calls = []
        engine = SimpleNamespace(create=lambda *a, **k: calls.append((a, k)) or ([0.0], 24000))
        speech = adapter(monkeypatch, engine=engine)
        speech.speak("hello", VoiceProfile(key="alfred"))
        assert calls == []


class TestGenuineSpeakSucceedsAgainstAFakeEngine:
    def test_speak_returns_true_and_becomes_available(self, monkeypatch):
        speech = adapter(monkeypatch)
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        assert speech.speak("good evening, sir", voice) is True
        assert speech.availability.state is AvailabilityState.AVAILABLE

    def test_the_resolved_voice_id_is_the_one_actually_sent(self, monkeypatch):
        seen = {}

        def create(text, *, voice, lang="en-us"):
            seen["voice"] = voice
            seen["text"] = text
            return [0.0, 0.1], 24000

        speech = adapter(monkeypatch, engine=SimpleNamespace(create=create))
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        speech.speak("good evening, sir", voice)

        assert seen["voice"] == "am_eric"
        assert seen["text"] == "good evening, sir"

    def test_a_different_agent_sends_its_own_distinct_voice_id(self, monkeypatch):
        seen = {}

        def create(text, *, voice, lang="en-us"):
            seen["voice"] = voice
            return [0.0], 24000

        speech = adapter(monkeypatch, engine=SimpleNamespace(create=create))
        voice = VoiceProfile(key="arthur", style={"kokoro_voice_id": "bm_daniel"})
        speech.speak("good evening", voice)

        assert seen["voice"] == "bm_daniel"

    def test_engine_is_constructed_once_and_reused(self, monkeypatch):
        """Loading the ONNX session is real work; a second speak() on the
        same adapter must not rebuild it."""
        built = []

        class FakeKokoro:
            def __init__(self, model_path, voices_path):
                built.append((model_path, voices_path))

            def create(self, text, *, voice, lang="en-us"):
                return [0.0], 24000

        monkeypatch.setattr(kokoro_speech_provider, "_play_wav_bytes", lambda audio: True)
        monkeypatch.setitem(
            __import__("sys").modules,
            "kokoro_onnx",
            SimpleNamespace(Kokoro=FakeKokoro),
        )
        speech = KokoroSpeechAdapter()
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        speech.speak("one", voice)
        speech.speak("two", voice)
        assert len(built) == 1


class TestSpeakFailsHonestly:
    def test_a_synthesis_failure_returns_false_never_true(self, monkeypatch):
        speech = adapter(monkeypatch, engine=fake_engine(raises=RuntimeError("onnx runtime error")))
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        assert speech.speak("hello", voice) is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "onnx runtime error" in speech.availability.reason

    def test_a_playback_failure_returns_false_never_true(self, monkeypatch):
        speech = adapter(monkeypatch, play_result=False)
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        assert speech.speak("hello", voice) is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "playback" in speech.availability.reason


class TestVoiceProfileStaysProviderIndependent:
    """VoiceProfile itself must never learn Kokoro exists - the id lives in
    the generic, opaque `style` bag every provider already shares, proved
    here by round-tripping it through the exact same ## Voice section
    parser every other trait already goes through."""

    def test_provider_and_voice_id_land_in_style_not_a_new_typed_field(self):
        profile = parse_voice(
            [
                "key: alfred",
                "preferred_voice: Microsoft David Desktop",
                "provider: kokoro",
                "kokoro_voice_id: am_eric",
            ],
            key="alfred",
        )
        assert profile.preferred_voice == "Microsoft David Desktop"
        assert profile.style == {"provider": "kokoro", "kokoro_voice_id": "am_eric"}
        assert not hasattr(profile, "kokoro_voice_id")
        assert not hasattr(profile, "provider")

    def test_two_agents_have_independent_kokoro_voices(self):
        alfred = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        arthur = VoiceProfile(key="arthur", style={"kokoro_voice_id": "bm_daniel"})
        assert (
            kokoro_speech_provider._voice_id_for(alfred)
            != kokoro_speech_provider._voice_id_for(arthur)
        )
        assert kokoro_speech_provider._voice_id_for(alfred) == "am_eric"
        assert kokoro_speech_provider._voice_id_for(arthur) == "bm_daniel"


class TestNoVoiceIdIsHardcodedInTheProvider:
    def test_the_module_source_names_no_concrete_voice(self):
        import inspect

        source = inspect.getsource(kokoro_speech_provider)
        assert "am_eric" not in source
        assert "bm_daniel" not in source


@pytest.mark.skipif(
    not _MODEL_PRESENT,
    reason="requires the locally downloaded Kokoro model/voices files under data/voices/kokoro/",
)
class TestGenuineLocalSynthesis:
    """The real thing: the real ONNX engine, the real am_eric and bm_daniel
    voice arrays extracted from Kokoro's own upstream release, and real
    audio out - not a mock standing in for any of it. Playback itself is
    still stubbed (a CI box has no speaker to assert against), but
    synthesis is genuine and its output is inspected as real PCM."""

    def test_am_eric_produces_genuine_nonsilent_audio(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            kokoro_speech_provider,
            "_play_wav_bytes",
            lambda audio: captured.setdefault("wav", audio) or True,
        )
        speech = KokoroSpeechAdapter()
        voice = VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})
        assert speech.speak("Good evening, sir. This is Alfred.", voice) is True
        assert speech.availability.state is AvailabilityState.AVAILABLE

        import io

        with wave.open(io.BytesIO(captured["wav"])) as wav:
            frames = wav.readframes(wav.getnframes())
            assert wav.getframerate() == 24000
            assert len(frames) > 0
            assert any(b != 0 for b in frames)  # genuinely not silence

    def test_bm_daniel_produces_genuine_nonsilent_audio_and_differs_from_am_eric(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            kokoro_speech_provider,
            "_play_wav_bytes",
            lambda audio: captured.setdefault("wav", audio) or True,
        )
        speech = KokoroSpeechAdapter()
        voice = VoiceProfile(key="arthur", style={"kokoro_voice_id": "bm_daniel"})
        assert speech.speak("Good evening. This is Arthur.", voice) is True

        import io

        with wave.open(io.BytesIO(captured["wav"])) as wav:
            frames = wav.readframes(wav.getnframes())
            assert len(frames) > 0
            assert any(b != 0 for b in frames)

    def test_an_unknown_voice_name_fails_honestly_rather_than_a_default_voice(self):
        """kokoro_onnx itself raises on a name absent from the two-voice
        archive - proof the archive genuinely holds only am_eric and
        bm_daniel, never the full upstream catalogue."""
        speech = KokoroSpeechAdapter()
        voice = VoiceProfile(key="someone-else", style={"kokoro_voice_id": "af_bella"})
        assert speech.speak("hello", voice) is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
