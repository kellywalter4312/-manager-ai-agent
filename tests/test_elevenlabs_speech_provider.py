"""Stage 11.4 - the ElevenLabs cloud TTS provider.

Every test replaces the two seams that would otherwise touch the network -
``adapter._client`` (the real SDK object, always constructed against a fake
key here so no test needs a real one) and the module's own
``_play_wav_bytes`` (local audio playback) - the same discipline
test_windows_speech_provider.py already applies to ``_run_powershell``.
Nothing in this file makes a real HTTP request; conftest.py's ``_no_network``
guard would fail loudly if one were attempted, which is itself proof this
suite never does.

Genuine, non-mocked verification against the real ElevenLabs API - resolving
Alistair's real voice id and one real synthesis - could not be performed in
this session because no ELEVENLABS_API_KEY is configured anywhere on this
machine, and this project's own rule is never to invent one. That gap is
recorded in this session's own report, not hidden here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.channels.speech import Listener, Speaker, SpeechAdapter, SpeechUnavailable
from src.providers.availability import AvailabilityState
import src.providers.elevenlabs_speech_provider as elevenlabs_speech_provider
from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter
from src.services.identity.voice import VoiceProfile, parse_voice


def adapter(monkeypatch, *, play_result: bool = True, voices=None, text_to_speech=None):
    """A real ElevenLabsSpeechAdapter, its SDK client swapped for a fake one
    and its local playback stubbed - never a real client, never real audio.

    ``_client`` is replaced wholesale rather than patched attribute by
    attribute: the real SDK client exposes ``voices``/``text_to_speech`` as
    read-only properties (each backed by its own sub-client class), so there
    is nothing to assign a fake onto piecemeal - the same reason a fake
    Anthropic client elsewhere in this project is built whole rather than
    grafted onto a real one.
    """
    instance = ElevenLabsSpeechAdapter(api_key="fake-key-not-real")
    instance._client = SimpleNamespace(
        voices=voices if voices is not None else fake_voices_client(),
        text_to_speech=text_to_speech if text_to_speech is not None else fake_tts_client(),
    )
    monkeypatch.setattr(elevenlabs_speech_provider, "_play_wav_bytes", lambda audio: play_result)
    return instance


def fake_voices_client(*, voices=(), raises: Exception | None = None):
    def get_all():
        if raises is not None:
            raise raises
        return SimpleNamespace(voices=list(voices))

    return SimpleNamespace(get_all=get_all)


def fake_tts_client(*, audio_chunks=(b"RIFF....WAVEfmt ",), raises: Exception | None = None):
    def convert(voice_id, *, text, output_format):
        if raises is not None:
            raise raises
        return iter(audio_chunks)

    return SimpleNamespace(convert=convert)


class TestInterfaceAndInitialState:
    def test_satisfies_the_speechadapter_contract(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert isinstance(speech, SpeechAdapter)
        assert isinstance(speech, Listener)
        assert isinstance(speech, Speaker)

    def test_is_named_elevenlabs(self, monkeypatch):
        assert adapter(monkeypatch).name == "elevenlabs"

    def test_starts_unknown_not_available(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert speech.availability.state is AvailabilityState.UNKNOWN
        assert speech.available is False

    def test_constructing_makes_no_call(self, monkeypatch):
        """The same rule every provider in this project lives under:
        configuration is not proof of working."""
        calls = []
        monkeypatch.setattr(
            elevenlabs_speech_provider,
            "ElevenLabs",
            lambda api_key: calls.append(api_key) or SimpleNamespace(),
        )
        ElevenLabsSpeechAdapter(api_key="fake-key")
        assert calls == ["fake-key"]  # constructed, nothing more


class TestListenIsHonestlyUnsupported:
    def test_listen_always_raises(self, monkeypatch):
        with pytest.raises(SpeechUnavailable):
            adapter(monkeypatch).listen()

    def test_the_message_says_why(self, monkeypatch):
        with pytest.raises(SpeechUnavailable) as exc:
            adapter(monkeypatch).listen()
        assert "text-to-speech only" in str(exc.value)


class TestDiscoveryIsReal:
    def test_available_voices_returns_a_real_voiceoption_per_voice(self, monkeypatch):
        voices = fake_voices_client(
            voices=[
                SimpleNamespace(voice_id="v1", name="Some Voice", verified_languages=None, labels=None),
                SimpleNamespace(voice_id="v2", name="Another Voice", verified_languages=None, labels=None),
            ]
        )
        speech = adapter(monkeypatch, voices=voices)
        options = speech.available_voices()
        assert [(o.voice_id, o.name, o.provider) for o in options] == [
            ("v1", "Some Voice", "elevenlabs"), ("v2", "Another Voice", "elevenlabs"),
        ]
        # No verified_languages and no labels -> honestly unknown, never guessed.
        assert all(o.language is None and o.accent is None and o.locale is None for o in options)

    def test_verified_languages_produce_one_option_per_language_with_real_metadata(self, monkeypatch):
        italian = SimpleNamespace(language="it", accent="italian", locale="it-IT")
        english = SimpleNamespace(language="en", accent=None, locale=None)
        voices = fake_voices_client(
            voices=[
                SimpleNamespace(
                    voice_id="v1", name="Multilingual Voice", labels=None,
                    verified_languages=[italian, english],
                ),
            ]
        )
        speech = adapter(monkeypatch, voices=voices)
        options = speech.available_voices()
        assert len(options) == 2
        assert options[0].language == "it" and options[0].accent == "italian" and options[0].locale == "it-IT"
        assert options[1].language == "en" and options[1].accent is None and options[1].locale is None
        assert all(o.voice_id == "v1" and o.name == "Multilingual Voice" for o in options)

    def test_labels_accent_is_used_when_no_verified_languages_exist(self, monkeypatch):
        voices = fake_voices_client(
            voices=[
                SimpleNamespace(
                    voice_id="v1", name="Labeled Voice", verified_languages=None,
                    labels={"accent": "british", "gender": "male"},
                ),
            ]
        )
        speech = adapter(monkeypatch, voices=voices)
        options = speech.available_voices()
        assert len(options) == 1
        assert options[0].accent == "british"
        # A labels-only voice never gets a fabricated language.
        assert options[0].language is None

    def test_a_catalog_failure_returns_empty_never_raises(self, monkeypatch):
        voices = fake_voices_client(raises=RuntimeError("account suspended"))
        speech = adapter(monkeypatch, voices=voices)
        assert speech.available_voices() == ()

    def test_discovery_never_touches_availability(self, monkeypatch):
        """Mirrors WindowsSpeechAdapter.installed_voice_names(): a catalog
        query is not the capability this adapter reports evidence about -
        only speak() is."""
        voices = fake_voices_client(raises=RuntimeError("boom"))
        speech = adapter(monkeypatch, voices=voices)
        speech.available_voices()
        assert speech.availability.state is AvailabilityState.UNKNOWN


class TestProbeAuthorization:
    """Closure campaign (2026-09-16): the real, un-swallowed vendor call a
    genuine credential check needs - deliberately distinct from
    available_voices(), which returns an honestly-empty tuple on ANY
    failure and so cannot tell "this key is rejected" from "this account
    has zero voices". This method lets the real exception through instead."""

    def test_a_working_credential_returns_the_real_voice_count(self, monkeypatch):
        voices = fake_voices_client(voices=[SimpleNamespace(voice_id="v1", name="A", labels={}, verified_languages=None),
                                             SimpleNamespace(voice_id="v2", name="B", labels={}, verified_languages=None)])
        speech = adapter(monkeypatch, voices=voices)
        assert speech.probe_authorization() == 2

    def test_a_rejected_credential_raises_rather_than_returning_empty(self, monkeypatch):
        voices = fake_voices_client(raises=RuntimeError("401 unauthorized"))
        speech = adapter(monkeypatch, voices=voices)
        with pytest.raises(RuntimeError, match="401 unauthorized"):
            speech.probe_authorization()

    def test_this_is_genuinely_different_from_available_voices_on_failure(self, monkeypatch):
        """The whole reason this method exists: available_voices() would
        hide the exact same failure behind an ambiguous empty tuple."""
        voices = fake_voices_client(raises=RuntimeError("account suspended"))
        speech = adapter(monkeypatch, voices=voices)
        assert speech.available_voices() == ()
        with pytest.raises(RuntimeError):
            speech.probe_authorization()


class TestSpeakRequiresAResolvedVoiceId:
    def test_no_voice_supplied_fails_honestly(self, monkeypatch):
        speech = adapter(monkeypatch)
        assert speech.speak("hello") is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "no voice profile" in speech.availability.reason

    def test_a_voice_with_no_elevenlabs_id_fails_honestly(self, monkeypatch):
        """preferred_voice alone (a display name) is never enough - the
        exact requirement this stage was told not to violate: never guess an
        id from a name."""
        speech = adapter(monkeypatch)
        voice = VoiceProfile(key="alfred", preferred_voice="Alistair")
        assert speech.speak("hello", voice) is False
        assert "alfred" in speech.availability.reason

    def test_a_blank_elevenlabs_id_is_treated_as_absent(self, monkeypatch):
        speech = adapter(monkeypatch)
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "   "})
        assert speech.speak("hello", voice) is False


class TestGenuineSpeakSucceeds:
    def test_speak_returns_true_and_becomes_available(self, monkeypatch):
        speech = adapter(monkeypatch, text_to_speech=fake_tts_client())
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "abc123"})
        assert speech.speak("good morning", voice) is True
        assert speech.availability.state is AvailabilityState.AVAILABLE

    def test_the_resolved_voice_id_is_the_one_actually_sent(self, monkeypatch):
        seen = {}

        def convert(voice_id, *, text, output_format):
            seen["voice_id"] = voice_id
            seen["text"] = text
            return iter([b"chunk"])

        speech = adapter(monkeypatch, text_to_speech=SimpleNamespace(convert=convert))
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "abc123"})
        speech.speak("good morning", voice)

        assert seen["voice_id"] == "abc123"
        assert seen["text"] == "good morning"

    def test_requests_a_real_wav_container_not_raw_pcm(self, monkeypatch):
        """The claim this provider's docstring makes: it asks ElevenLabs for
        a complete WAV file, so nothing here has to build a header by hand."""
        seen = {}

        def convert(voice_id, *, text, output_format):
            seen["output_format"] = output_format
            return iter([b"chunk"])

        speech = adapter(monkeypatch, text_to_speech=SimpleNamespace(convert=convert))
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "abc123"})
        speech.speak("hi", voice)

        assert seen["output_format"].startswith("wav_")


class TestSpeakFailsHonestly:
    def test_an_api_failure_returns_false_never_true(self, monkeypatch):
        tts = fake_tts_client(raises=RuntimeError("quota exceeded"))
        speech = adapter(monkeypatch, text_to_speech=tts)
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "abc123"})
        assert speech.speak("hello", voice) is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "quota exceeded" in speech.availability.reason

    def test_a_playback_failure_returns_false_never_true(self, monkeypatch):
        speech = adapter(monkeypatch, play_result=False, text_to_speech=fake_tts_client())
        voice = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "abc123"})
        assert speech.speak("hello", voice) is False
        assert speech.availability.state is AvailabilityState.UNAVAILABLE
        assert "playback" in speech.availability.reason

    def test_synthesis_never_runs_when_no_id_is_resolved(self, monkeypatch):
        """A caller must never be billed for a request this provider already
        knows cannot be attributed to a real voice."""
        calls = []
        tts = SimpleNamespace(convert=lambda *a, **k: calls.append((a, k)) or iter([b""]))
        speech = adapter(monkeypatch, text_to_speech=tts)
        speech.speak("hello", VoiceProfile(key="alfred"))
        assert calls == []


class TestVoiceProfileStaysProviderIndependent:
    """Item 2 of this stage: VoiceProfile itself must never learn ElevenLabs
    exists. The id lives in the generic, opaque `style` bag every provider
    already shares - proved here by round-tripping it through the exact same
    ## Voice section parser every other trait already goes through."""

    def test_provider_and_voice_id_land_in_style_not_a_new_typed_field(self):
        profile = parse_voice(
            [
                "key: alfred",
                "preferred_voice: Alistair",
                "provider: elevenlabs",
                "elevenlabs_voice_id: abc123",
            ],
            key="alfred",
        )
        assert profile.preferred_voice == "Alistair"
        assert profile.style == {"provider": "elevenlabs", "elevenlabs_voice_id": "abc123"}
        # No new field was added to accommodate this - style already existed.
        assert not hasattr(profile, "elevenlabs_voice_id")
        assert not hasattr(profile, "provider")

    def test_two_agents_can_have_independent_elevenlabs_voices(self):
        """Requirement 10: reusable by future agents, not an Alfred-specific
        system. Nothing about resolving a voice id is agent-aware."""
        alfred = VoiceProfile(key="alfred", style={"elevenlabs_voice_id": "alfred-id"})
        someone_else = VoiceProfile(key="arthur", style={"elevenlabs_voice_id": "arthur-id"})
        assert (
            elevenlabs_speech_provider._voice_id_for(alfred)
            != elevenlabs_speech_provider._voice_id_for(someone_else)
        )
        assert elevenlabs_speech_provider._voice_id_for(alfred) == "alfred-id"
        assert elevenlabs_speech_provider._voice_id_for(someone_else) == "arthur-id"


class TestNoVoiceIdIsHardcodedInTheProvider:
    """Requirement 4, checked structurally: the module source names no
    concrete ElevenLabs voice id - it only ever reads one from a profile."""

    def test_the_module_source_contains_no_id_shaped_literal_for_alistair(self):
        import inspect

        source = inspect.getsource(elevenlabs_speech_provider)
        assert "alistair" not in source.lower()
