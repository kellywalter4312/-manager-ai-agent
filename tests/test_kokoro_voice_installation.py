"""0.2 Final Voice Installation - Alfred/am_eric, Arthur/bm_daniel, wired
through the real production path.

The two identity documents now each declare a real Kokoro voice id
(``IDENTITY.md``'s own ``## Voice`` section: ``kokoro_voice_id: am_eric``;
``identities/email_agent.md``'s own: ``kokoro_voice_id: bm_daniel``).
This file proves that declaration actually reaches
``kokoro_speech_provider.KokoroSpeechAdapter.speak()`` through the same
``scripts/control_center.py::build()`` + ``scripts/exchange.py::exchange()``
path any real conversation uses when ``MANAGER_SPEECH=kokoro`` - never a
bare call to the adapter standing in for "the production path selected
this voice."

``TestGenuineEndToEndSynthesis`` makes no substitution beyond local audio
playback (there is no speaker in an automated run to assert against) - the
real ONNX engine and the real two-voice archive both run, and the
resulting WAV is inspected as real, non-silent audio. Every other class
substitutes a fake engine (recording which voice id it was asked for)
so the wiring checks below run fast and do not repeat that same genuine
synthesis proof on every assertion.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest
from types import SimpleNamespace

from src.channels.channel import VoiceChannel
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
import src.providers.kokoro_speech_provider as kokoro_speech_provider
from src.providers.kokoro_speech_provider import KokoroSpeechAdapter
from src.services.identity.identity import load_identity
from src.services.identity.voice_preference import effective_voice_for
from scripts.control_center import build, run_command
from scripts.exchange import exchange


class RecordingProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    @property
    def name(self):
        return "anthropic"

    def complete(self, prompt, *, system=None):
        self.calls += 1
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")
        return Completion(text="a full-brain answer", provider="anthropic")


def real_system(**env_overrides):
    env = {"MANAGER_SPEECH": "kokoro", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


def _install_fake_engine(monkeypatch, calls: list[str]):
    """Replace only the ONNX engine construction and local playback - the
    real Manager, real registry, real VoiceChannel, real identity-declared
    VoiceProfiles and the real KokoroSpeechAdapter.speak() all still run;
    only the two things an automated wiring test cannot depend on
    (loading a real model, a real speaker) are stubbed.
    """

    # The real engine's signature: a declared pace ("measured") now reaches it
    # as a speaking speed.
    def create(text, *, voice, lang="en-us", speed=1.0):
        calls.append(voice)
        return [0.0, 0.1, -0.1], 24000

    monkeypatch.setattr(
        KokoroSpeechAdapter, "_engine_or_raise", lambda self: SimpleNamespace(create=create)
    )
    monkeypatch.setattr(kokoro_speech_provider, "_play_wav_bytes", lambda audio: True)


class TestIdentityDocumentsDeclareTheExactVoices:
    def test_alfred_declares_am_eric(self):
        identity = load_identity("IDENTITY.md")
        assert identity.voice.style.get("kokoro_voice_id") == "am_eric"

    def test_arthur_declares_bm_daniel(self):
        _, manager, _, _ = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur is not None
        assert arthur.voice.style.get("kokoro_voice_id") == "bm_daniel"

    def test_the_two_declared_voices_are_distinct(self):
        identity = load_identity("IDENTITY.md")
        _, manager, _, _ = real_system()
        arthur = manager.registry.get("Arthur")
        assert identity.voice.style["kokoro_voice_id"] != arthur.voice.style["kokoro_voice_id"]


class TestGenuineEndToEndSynthesis:
    """No substitution beyond local playback - real build(), real
    exchange(speak=True), real ONNX inference, real two-voice archive."""

    def test_alfred_speaks_with_am_eric_through_the_real_production_path(self, monkeypatch):
        pytest.importorskip("kokoro_onnx", reason="kokoro_onnx not installed")
        captured = {}
        monkeypatch.setattr(
            kokoro_speech_provider,
            "_play_wav_bytes",
            lambda audio: captured.setdefault("wav", audio) or True,
        )
        runtime, manager, channel, conversation = real_system()

        turn = exchange(channel, conversation, "who are you?", speak=True)

        assert turn.handled_by == manager.own_skills.name
        assert "wav" in captured, "speak() was never reached through the production path"
        with wave.open(io.BytesIO(captured["wav"])) as wav:
            frames = wav.readframes(wav.getnframes())
            assert wav.getframerate() == 24000
            assert len(frames) > 0
            assert any(b != 0 for b in frames)  # genuinely not silence


class TestPerAgentVoiceIndependenceThroughTheRealChannel:
    def test_alfred_and_arthur_are_synthesized_with_their_own_distinct_voice_ids(self, monkeypatch):
        calls: list[str] = []
        _install_fake_engine(monkeypatch, calls)
        runtime, manager, channel, conversation = real_system()

        exchange(channel, conversation, "who are you?", speak=True)
        assert calls == ["am_eric"]

        exchange(channel, conversation, "check my email", addressed="Arthur", speak=True)
        assert calls[-1] == "bm_daniel"
        assert calls == ["am_eric", "bm_daniel"]

    def test_not_silently_replaced_by_a_windows_voice(self, monkeypatch):
        """The exact requirement this turn was told to satisfy: the
        selected Kokoro voices are what is actually used, never a
        Windows David/Zira/Hazel voice standing in unnoticed."""
        calls: list[str] = []
        _install_fake_engine(monkeypatch, calls)
        runtime, manager, channel, conversation = real_system()
        assert isinstance(channel, VoiceChannel)
        assert channel.speech.name == "kokoro"

        exchange(channel, conversation, "who are you?", speak=True)
        assert calls == ["am_eric"]
        assert "david" not in calls[0].lower()
        assert "zira" not in calls[0].lower()


class TestVoiceIndependenceFromBrainAndProvider:
    def test_switching_to_a_verified_full_brain_does_not_change_the_kokoro_voice(self, monkeypatch):
        runtime, manager, channel, conversation = real_system()

        provider = RecordingProvider()
        brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        brain.deliberate(Task(description="warm up"))
        manager.brain = brain
        manager.own_skills._converse = brain.converse
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.style["kokoro_voice_id"] == "am_eric"

    def test_changing_the_speech_provider_selection_does_not_touch_the_brain(self):
        """The inverse direction of the same independence: selecting
        kokoro must not disturb whichever brain mode was already
        assessed - the two are wired through entirely separate factories
        (brain_factory.py, speech_factory.py), never one composing the
        other."""
        runtime, manager, channel, conversation = real_system()
        mode_with_kokoro = assess_manager(manager).mode

        runtime2, manager2, channel2, conversation2 = real_system(MANAGER_SPEECH="none")
        mode_with_none = assess_manager(manager2).mode

        assert mode_with_kokoro == mode_with_none


class TestUserVoiceOverrideCompatibility:
    """The existing, governed voice_preference mechanism
    (services/identity/voice_preference.py) overrides only
    VoiceProfile.preferred_voice - a display name meant for
    WindowsSpeechAdapter. KokoroSpeechAdapter reads a different field
    (style['kokoro_voice_id']) entirely, exactly as ElevenLabsSpeechAdapter
    already does for its own id. This proves that existing mechanism keeps
    working, unregressed, and that it coexists with - rather than
    conflicts with - the declared Kokoro id, never silently overriding it."""

    def test_setting_a_preferred_voice_leaves_the_kokoro_voice_id_untouched(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        assert "now" in result and "Zira" in result

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.preferred_voice == "Microsoft Zira Desktop"  # the override took
        assert resolved.style["kokoro_voice_id"] == "am_eric"  # untouched

    def test_the_override_mechanism_itself_still_functions_with_kokoro_selected(self):
        """Regression check: nothing about wiring Kokoro in broke the
        pre-existing override round-trip."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        confirmed = run_command(centre, "voice Alfred", 80, channel, conversation)
        assert "Microsoft Zira Desktop" in confirmed

    def test_arthurs_override_does_not_touch_alfreds_kokoro_voice(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Arthur Microsoft Hazel Desktop", 80, channel, conversation)

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.style["kokoro_voice_id"] == "am_eric"


class TestAvailableVoicesDiscoversTheRealLocalArchiveHonestly:
    """Agent-autonomous voice selection checkpoint: Kokoro must
    participate in the same VoiceOption architecture ElevenLabs/Windows
    already do, not remain a special-case dead end - but never by
    guessing the vendor's own (undocumented, in this repo) voice-naming
    convention. What IS asserted (language=english, locale=en-US) is a
    genuine, verifiable fact about this project's own speak() call
    (always lang="en-us"), not a claim about the vendor's catalog."""

    def test_returns_every_voice_in_every_archive_this_machine_actually_has(self):
        import numpy as np

        adapter = KokoroSpeechAdapter()
        options = adapter.available_voices()
        ids = {o.voice_id for o in options}
        on_disk = set()
        for archive in Path("data/voices/kokoro").glob("*.npz"):
            on_disk |= set(np.load(archive).files)
        # Read from the real archives, never a list: the default two-voice
        # archive plus every other archive installed beside it.
        assert ids == on_disk and {"am_eric", "bm_daniel"} <= ids
        assert all(o.provider == "kokoro" for o in options)

    def test_language_accent_and_gender_come_from_kokoros_published_convention(self):
        # Whole-ecosystem campaign 2: voice, spoken language and accent are
        # separate. Kokoro's own naming convention (first letter language and
        # variety, second gender) is the source - the same convention
        # translation's language table already relies on (if_sara Italian,
        # ef_dora Spanish, bf_emma British English).
        by_id = {o.voice_id: o for o in KokoroSpeechAdapter().available_voices()}
        assert (by_id["am_eric"].language, by_id["am_eric"].accent, by_id["am_eric"].gender) == ("english", "american", "male")
        assert (by_id["bm_daniel"].language, by_id["bm_daniel"].accent) == ("english", "british")

    def test_a_voice_outside_the_convention_is_offered_with_nothing_guessed(self, tmp_path):
        import numpy as np

        np.savez(tmp_path / "voices.npz", **{"custom_voice": np.zeros((4, 1, 256), dtype=np.float32)})
        (option,) = KokoroSpeechAdapter(voices_path=str(tmp_path / "voices.npz")).available_voices()
        assert option.voice_id == "custom_voice"
        assert option.language is None and option.accent is None and option.gender is None

    def test_a_missing_or_unreadable_archive_returns_empty_never_raises(self):
        adapter = KokoroSpeechAdapter(voices_path=str(Path("does") / "not" / "exist.npz"))
        assert adapter.available_voices() == ()


class TestExplicitOverrideResolvesToTheCorrectKokoroField:
    """Requirement 7/production-completeness: a person typing a real
    Kokoro voice's own id/name into the explicit-override command must
    have it applied to style["kokoro_voice_id"] - the field
    KokoroSpeechAdapter.speak() actually reads - not silently left in
    preferred_voice, where this engine never looks."""

    def test_an_explicit_override_by_real_kokoro_id_is_applied_to_the_correct_field(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.identity.voice_preference import set_voice_preference

        set_voice_preference(
            manager.own_skills.name, "bm_daniel",
            recall=manager.memory.recall, remember=manager.memory.remember, forget=manager.memory.forget,
        )
        discover = KokoroSpeechAdapter().available_voices
        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall, discover_voices=discover)
        assert resolved.style["kokoro_voice_id"] == "bm_daniel"

    def test_without_discovery_the_same_override_falls_back_to_preferred_voice_honestly(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.identity.voice_preference import set_voice_preference

        set_voice_preference(
            manager.own_skills.name, "bm_daniel",
            recall=manager.memory.recall, remember=manager.memory.remember, forget=manager.memory.forget,
        )
        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.preferred_voice == "bm_daniel"
        # Unresolved without discovery - the pre-existing, unchanged
        # behaviour, never silently promoted to style without evidence.
        assert resolved.style["kokoro_voice_id"] == "am_eric"
