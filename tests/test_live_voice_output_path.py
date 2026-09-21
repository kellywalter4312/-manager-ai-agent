"""The live, user-facing voice path (2026-09-21).

Found at runtime: the page voiced every ambient reply with the browser's own
default voice (``speechSynthesis`` with no voice set) - Alfred, declared as a
male voice, was heard as a female one - while the server separately played
the configured provider on this machine's speakers and held the written reply
until that playback had finished. And a voice chosen on one provider, used on
another with no accent to carry, erased the agent's declared voice for that
provider, so a name-matched engine picked one of its own at random.

What these tests hold in place:

- ``POST /speech`` voices words in the named agent's own configured voice,
  through the same resolution a spoken delivery uses, and reports the
  provider and the voice the provider itself says it used;
- with no voice provider it refuses with the reason - no audio, no substitute;
- ``POST /message`` with ``voice_output="device"`` never also plays on this
  machine, and returns the spoken text the device should voice;
- each provider reports the voice it will actually use;
- the page's ambient loop never falls back to the browser's own voice.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from src.channels.channel import VoiceChannel, speakable
from src.channels.speech import SpeechAdapter
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.providers.windows_speech_provider import _select_voice_name
from src.services.identity.voice import VoiceProfile
from scripts.serve_ui import build_live_system, route_message, route_speech

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")


def settings(**overrides):
    return Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)


class _SynthesizingSpeaker(SpeechAdapter):
    """A speech engine at its own boundary: records what it was asked to say,
    in which profile, and whether anything was played here."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.synthesized: list[tuple[str, VoiceProfile | None]] = []

    @property
    def name(self) -> str:
        return "scripted-voice"

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    def listen(self, timeout: float | None = None) -> str:
        raise NotImplementedError

    def speak(self, text: str, voice=None) -> bool:
        self.spoken.append(text)
        return True

    def synthesize(self, text: str, voice: VoiceProfile | None = None) -> bytes:
        self.synthesized.append((text, voice))
        return b"RIFF" + text.encode("utf-8")

    def voice_in_use(self, voice: VoiceProfile | None) -> str:
        return f"voice-of-{voice.key}" if voice is not None else ""


def _live(speaker=None):
    live = build_live_system(settings())
    if speaker is not None:
        live.channel = VoiceChannel(live.manager, speech=speaker, presence=live.runtime.presence_registry)
    return live


def _json(response):
    status, body, _ = response
    return status, json.loads(body)


class TestSpeechRoute:
    def test_the_agents_own_voice_is_used_and_reported(self):
        speaker = _SynthesizingSpeaker()
        live = _live(speaker)
        manager_name = live.manager.own_skills.name
        status, data = _json(route_speech(manager_name, "Good evening.", live))
        assert status == 200
        assert base64.b64decode(data["audio_base64"]) == b"RIFFGood evening."
        assert data["provider"] == "scripted-voice"
        assert data["voice"] == f"voice-of-{manager_name.lower()}"
        assert isinstance(data["synthesis_seconds"], float)
        assert speaker.spoken == []  # synthesized for the device, never played here

    def test_each_agent_is_voiced_in_its_own_profile(self):
        speaker = _SynthesizingSpeaker()
        live = _live(speaker)
        names = [a.name for a in live.manager.registry.all_agents()]
        assert len(names) >= 2
        voices = {_json(route_speech(name, "Hello.", live))[1]["voice"] for name in names}
        assert len(voices) == len(names)

    def test_no_voice_provider_is_refused_with_the_reason_and_no_substitute(self):
        live = _live()  # the default: speech 'none'
        status, data = _json(route_speech(live.manager.own_skills.name, "Hello.", live))
        assert status == 409
        assert "no voice provider is configured" in data["error"]
        assert "provider speech_provider use" in data["error"]
        assert "audio_base64" not in data

    def test_unknown_agent_and_empty_text_are_refused(self):
        live = _live(_SynthesizingSpeaker())
        assert route_speech("Nobody At All", "Hello.", live)[0] == 404
        assert route_speech(live.manager.own_skills.name, "  ", live)[0] == 400


class TestMessageForADeviceThatVoicesTheReply:
    def test_device_output_never_plays_on_this_machine(self):
        speaker = _SynthesizingSpeaker()
        live = _live(speaker)
        status, data = _json(route_message("Who are you?", live, voice_output="device"))
        assert status == 200 and data["outcome"]
        assert speaker.spoken == []

    def test_the_default_still_speaks_here(self):
        speaker = _SynthesizingSpeaker()
        live = _live(speaker)
        route_message("Who are you?", live)
        assert len(speaker.spoken) == 1

    def test_the_spoken_text_is_the_same_words_a_delivery_would_say(self):
        live = _live(_SynthesizingSpeaker())
        status, data = _json(route_message("Who are you?", live, voice_output="device"))
        turn = type("T", (), {"outcome": data["outcome"], "speak_in_full": False})()
        assert data["speech"] == speakable(turn)


class TestProvidersReportTheVoiceTheyUse:
    def test_windows_reports_exactly_its_own_selection(self):
        from src.providers.windows_speech_provider import WindowsSpeechAdapter

        adapter = WindowsSpeechAdapter.__new__(WindowsSpeechAdapter)
        installed = ("Microsoft David Desktop", "Microsoft Hazel Desktop", "Microsoft Zira Desktop")
        adapter.installed_voice_names = lambda: installed
        profile = VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop")
        assert adapter.voice_in_use(profile) == _select_voice_name(profile, installed) == "Microsoft David Desktop"

    def test_kokoro_reports_the_voice_id_it_synthesizes_with(self):
        from src.providers.kokoro_speech_provider import KokoroSpeechAdapter

        adapter = KokoroSpeechAdapter.__new__(KokoroSpeechAdapter)
        assert adapter.voice_in_use(VoiceProfile(key="alfred", style={"kokoro_voice_id": "am_eric"})) == "am_eric"


class TestThePageNeverUsesTheBrowsersOwnVoiceForReplies:
    def _ambient_block(self) -> str:
        start = PAGE.index("function initVoiceActivation(")
        return PAGE[start:PAGE.index("indicator.addEventListener(\"click\", startListening);", start)]

    def test_the_ambient_loop_has_no_browser_speech_synthesis(self):
        assert "speechSynthesis" not in self._ambient_block()
        assert "SpeechSynthesisUtterance" not in self._ambient_block()

    def test_the_ambient_loop_asks_the_server_for_the_agents_voice(self):
        block = self._ambient_block()
        assert 'voice_output: "device"' in block
        assert 'fetch("speech"' in block
        assert "novoice" in block
