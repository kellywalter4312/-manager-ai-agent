"""Whole-ecosystem closure campaign 2 - voice configuration, translation outputs
and the video-clipping false positive.

Unit and integration tests with in-process doubles for recognition, translation
and synthesis. The same paths were verified end to end with the real local
models (Kokoro speech, Whisper recognition, Argos translation, FFmpeg) through
the served system; those runs are recorded in REQUIREMENT_LEDGER.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

import scripts.serve_ui as su
from src.agents.tools.translation_output import TranslationOutputTool
from src.core.contracts import Task
from src.manager.capabilities import (
    CLIP_VIDEO,
    MANAGER_CAPABILITIES,
    TRANSLATE_MEDIA,
    clip_video_intent,
    select_capability,
    translate_media_intent,
)
from src.providers.media.transcription import Transcript, TranscriptSegment
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactStore
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_configuration import (
    VoiceFacts,
    parse_voice_configuration,
    plan_voice,
    set_voice_setting,
    voice_setting_for,
)
from src.services.identity.voice_options import VoiceOption
from src.services.identity.voice_preference import effective_voice_for, set_voice_preference
from src.services.memory.ephemeral import EphemeralMemory
from src.services.translation.outputs import (
    OutputContext,
    TimedLine,
    TranslatedMedia,
    TranslationDisplay,
    register_translation_output,
    route_translation,
    to_srt,
    to_webvtt,
    translation_outputs,
)


# ---------------------------------------------------------------------------
# Video clipping: a time range in ordinary conversation is not a clip request
# ---------------------------------------------------------------------------


class TestClipIntentFalsePositive:
    @pytest.mark.parametrize("text", [
        "The meeting video ran from 2:00 to 3:30 and nobody spoke.",
        "I watched a video about cooking for 10-15 minutes yesterday.",
        "My shift runs 9:00-17:00 and I record a video diary after.",
        "That clip was funny, it went from 0:10 to 0:40.",
    ])
    def test_a_statement_mentioning_a_video_and_a_range_is_not_a_clip_request(self, text):
        assert not clip_video_intent(Task(description=text))

    @pytest.mark.parametrize("text", [
        "Clip 1:05 to 1:40 from this video.",
        "Can you cut the video from 0:30 to 0:55?",
        "After the intro, pull out 2:10-2:45 of the video for me.",
        "I'd like the part of the video from 12:00 to 12:30.",
    ])
    def test_a_real_clip_request_still_reaches_clipping(self, text):
        assert clip_video_intent(Task(description=text))
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES) is CLIP_VIDEO


# ---------------------------------------------------------------------------
# Voice: language, accent and pace are separate facts, planned per provider
# ---------------------------------------------------------------------------


def _option(voice_id, provider, language, accent, locale, gender):
    return VoiceOption(voice_id=voice_id, name=voice_id, provider=provider, language=language,
                       accent=accent, locale=locale, gender=gender)


MULTILINGUAL = VoiceFacts(
    provider="multi",
    voices=(
        _option("amer_f", "multi", "english", "american", "en-us", "female"),
        _option("brit_m", "multi", "english", "british", "en-gb", "male"),
        _option("ital_m", "multi", "italian", "italian", "it", "male"),
        _option("port_f", "multi", "portuguese", "brazilian", "pt-br", "female"),
    ),
    spoken_languages=("english", "italian", "portuguese"),
    across_languages=True, paces=("slow", "normal", "fast"), can_synthesize=True,
)
MONOLINGUAL = VoiceFacts(
    provider="mono",
    voices=(_option("Mono Hazel", "mono", "english", None, "en-GB", "female"),
            _option("Mono David", "mono", "english", None, "en-US", "male")),
    spoken_languages=("english",), across_languages=False, paces=(), can_synthesize=True,
)


class TestVoicePlanning:
    def test_parsing_keeps_language_accent_pace_and_questions_apart(self):
        assert parse_voice_configuration("Speak Portuguese.").language == "portuguese"
        both = parse_voice_configuration("Speak English with an Italian accent.")
        assert (both.language, both.accent) == ("english", "italian")
        place = parse_voice_configuration("Sound like someone from Glasgow.")
        assert (place.language, place.accent) == (None, "glasgow")
        assert parse_voice_configuration("Could you talk a bit slower?").pace == "slow"
        assert parse_voice_configuration("Can you speak Japanese?").question

    def test_a_language_picks_a_native_voice_and_an_accent_keeps_the_language(self):
        current = MULTILINGUAL.voices[0]
        plan = plan_voice(MULTILINGUAL, current=current, language="portuguese")
        assert plan.ok and plan.voice.voice_id == "port_f" and plan.language == "portuguese"
        accent = plan_voice(MULTILINGUAL, current=current, accent="italian")
        assert accent.ok and accent.voice.voice_id == "ital_m" and accent.language is None
        assert "Italian accent" in accent.message

    def test_what_a_provider_cannot_do_is_refused_with_what_it_can(self):
        refused = plan_voice(MONOLINGUAL, current=None, language="italian")
        assert not refused.ok and "It speaks: English" in refused.message
        dialect = plan_voice(MULTILINGUAL, current=None, accent="glaswegian")
        assert not dialect.ok and "American" in dialect.message and "British" in dialect.message
        no_pace = plan_voice(MONOLINGUAL, current=None, pace="slow")
        assert no_pace.ok  # a provider reporting no paces does not refuse one it cannot check

    def test_an_accent_from_a_locale_only_provider_is_matched_by_region(self):
        plan = plan_voice(MONOLINGUAL, current=None, accent="british")
        assert plan.ok and plan.voice.voice_id == "Mono Hazel"


class _Agent:
    def __init__(self, name):
        self.name = name
        self.voice = VoiceProfile(key=name.lower())


class TestVoiceSettingsCarryAcrossProviders:
    def test_settings_are_stored_per_agent_and_reach_the_profile(self):
        memory = EphemeralMemory()
        for agent, language in (("Alfred", "italian"), ("Arthur", "portuguese")):
            set_voice_setting(agent, "language", language, recall=memory.recall, remember=memory.remember, forget=memory.forget)
        set_voice_setting("Alfred", "pace", "slow", recall=memory.recall, remember=memory.remember, forget=memory.forget)
        alfred = effective_voice_for(_Agent("Alfred"), recall=memory.recall)
        arthur = effective_voice_for(_Agent("Arthur"), recall=memory.recall)
        assert (alfred.language, alfred.pace) == ("italian", "slow")
        assert arthur.language == "portuguese" and arthur.pace != "slow"
        set_voice_setting("Alfred", "language", "", recall=memory.recall, remember=memory.remember, forget=memory.forget)
        assert voice_setting_for("Alfred", "language", recall=memory.recall) is None

    def test_a_voice_from_another_provider_is_re_resolved_from_the_accent_asked_for(self):
        memory = EphemeralMemory()
        set_voice_preference("Alfred", "Mono Hazel", available=("Mono Hazel",),
                             recall=memory.recall, remember=memory.remember, forget=memory.forget)
        set_voice_setting("Alfred", "accent", "british", recall=memory.recall, remember=memory.remember, forget=memory.forget)
        profile = effective_voice_for(_Agent("Alfred"), recall=memory.recall, discover_voices=lambda: MULTILINGUAL.voices)
        assert profile.style.get("multi_voice_id") == "brit_m" or profile.preferred_voice == "brit_m"
        # the same stored voice on its own provider is used as chosen
        same = effective_voice_for(_Agent("Alfred"), recall=memory.recall, discover_voices=lambda: MONOLINGUAL.voices)
        assert same.preferred_voice == "Mono Hazel"

    def test_with_no_accent_asked_for_nothing_is_invented(self):
        """A voice chosen on another provider, with no accent to carry, leaves
        this provider speaking the agent's own declared voice - never the
        foreign name, which this provider cannot match and would replace with
        a voice of its own choosing (2026-09-21: heard live as Zira)."""
        memory = EphemeralMemory()
        set_voice_preference("Alfred", "Mono Hazel", available=("Mono Hazel",),
                             recall=memory.recall, remember=memory.remember, forget=memory.forget)
        agent = _Agent("Alfred")
        agent.voice = VoiceProfile(key="alfred", preferred_voice="Declared David")
        profile = effective_voice_for(agent, recall=memory.recall, discover_voices=lambda: MULTILINGUAL.voices)
        assert profile.preferred_voice == "Declared David"
        assert not any(k.endswith("_voice_id") for k in profile.style)


# ---------------------------------------------------------------------------
# Translation outputs: one translated result, any registered output
# ---------------------------------------------------------------------------


MEDIA = TranslatedMedia("es", "en", (
    TimedLine(0.0, 2.4, "La biblioteca abre a las nueve.", "The library opens at nine."),
    TimedLine(2.4, 4.0, "Los lunes está cerrada.", "It is closed on Mondays."),
), ("es", "en"))


class TestSubtitleFormats:
    def test_webvtt_and_srt_keep_the_timings_of_the_speech(self):
        vtt, srt = to_webvtt(MEDIA), to_srt(MEDIA)
        assert vtt.startswith("WEBVTT") and "00:00:00.000 --> 00:00:02.400" in vtt and "00:00:02.400 --> 00:00:04.000" in vtt
        assert "00:00:02,400 --> 00:00:04,000" in srt and "It is closed on Mondays." in srt

    def test_a_zero_length_segment_still_shows_for_a_moment(self):
        vtt = to_webvtt(replace(MEDIA, lines=(TimedLine(5.0, 5.0, "x", "y"),)))
        assert "00:00:05.000 --> 00:00:05.500" in vtt


class TestOutputRouting:
    def test_the_registry_is_open_and_one_failing_output_hides_nothing(self, tmp_path):
        seen = []
        register_translation_output("Test Screen", lambda ctx, media: seen.append(media.target_language) or {"lines": 2})
        try:
            assert "test_screen" in translation_outputs()
            display = TranslationDisplay()
            ctx = OutputContext(artifacts=ArtifactStore(tmp_path), owner_agent="Alfred", source_title="talk.wav", display=display)
            results = route_translation(MEDIA, ["subtitles", "captioned_video", "display", "test_screen", "hologram"], ctx)
        finally:
            from src.services.translation import outputs

            outputs._OUTPUTS.pop("test_screen", None)
        assert seen == ["en"]
        assert results["subtitles"]["cues"] == 2 and results["captioned_video"] == {"skipped": "the source is not a video"}
        assert "no output called 'hologram'" in results["hologram"]["error"]
        state = display.state()
        assert (state["source"], state["target"]) == ("es", "en") and state["lines"][-1]["text"] == "It is closed on Mondays."
        stored = ArtifactStore(tmp_path).get(results["subtitles"]["artifact_id"])
        assert stored.type == "subtitles" and open(stored.latest.renditions["vtt"], encoding="utf-8").read().startswith("WEBVTT")

    def test_audio_is_produced_only_when_a_voice_really_speaks(self, tmp_path):
        ctx = OutputContext(artifacts=ArtifactStore(tmp_path), owner_agent="Alfred", source_title="talk.wav",
                            speak=lambda text, lang: (None, "", "device"))
        assert "no local voice speaks en" in route_translation(MEDIA, ["audio"], ctx)["audio"]["skipped"]
        ctx.speak = lambda text, lang: (b"RIFF" + text.encode(), f"{lang}_voice", "local_voice")
        assert route_translation(MEDIA, ["audio"], ctx)["audio"]["voice"] == "en_voice"


class TestMediaTranslationIntent:
    @pytest.mark.parametrize("text", [
        "Translate this video into English subtitles.",
        "Give me Japanese captions for this recording.",
        "Can you add captions to this video?",
        "Translate this interview into Swahili and read it to me.",
    ])
    def test_media_translation_requests_are_claimed(self, text):
        assert translate_media_intent(Task(description=text))
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES) is TRANSLATE_MEDIA

    @pytest.mark.parametrize("text", [
        "Translate good morning into French.",
        "The video had subtitles I couldn't read.",  # a statement: claimed, then asks for the media honestly
        "Clip 0:10 to 0:20 from this video.",
    ])
    def test_neighbouring_requests_keep_their_own_capability(self, text):
        chosen = select_capability(Task(description=text), MANAGER_CAPABILITIES)
        if "subtitles" in text:
            assert chosen is TRANSLATE_MEDIA
        else:
            assert chosen is not TRANSLATE_MEDIA


@dataclass(frozen=True)
class _Result:
    text: str
    source: str
    target: str
    route: tuple


class _Interpreter:
    translates = True

    def _resolve(self, name):
        return SimpleNamespace(code={"german": "de", "english": "en"}.get(str(name).lower(), str(name).lower()))

    def translate_text(self, text, target, source=None):
        return _Result(f"[{target}] {text}", source or "es", target, (source or "es", target))

    def speak(self, text, language):
        return b"RIFF" + text.encode(), f"{language}_voice", "local_voice"


class _Transcriber:
    name = "fake_listener"

    def transcribe(self, audio):
        assert audio
        return Transcript(segments=(TranscriptSegment(0.0, 1.5, "hola"), TranscriptSegment(1.5, 3.0, "adiós")), language="es")


class TestTheTranslationOutputTool:
    def _tool(self, tmp_path, display=None, transcriber=None):
        store = ArtifactStore(tmp_path)
        recording = register_new_artifact(store, type="audio", owner_agent="Alfred", title="note.wav", content_bytes=b"x",
                                          rendition_format="wav", rendition_bytes=b"RIFFfakewav",
                                          metadata={"content_kind": "audio"})
        tool = TranslationOutputTool(interpreter=_Interpreter(), transcriber=transcriber or _Transcriber(),
                                     artifacts=store, owner_agent="Alfred", display=display)
        return tool, store, recording.artifact_id

    def test_a_recording_is_translated_with_timings_and_routed(self, tmp_path):
        display = TranslationDisplay()
        tool, store, rid = self._tool(tmp_path, display)
        out = json.loads(tool.run(operation="translate_media",
                                  content=json.dumps({"artifact_id": rid, "target": "German", "outputs": ["subtitles", "audio", "display"]})))
        assert (out["source_language"], out["target_language"]) == ("es", "de")
        assert [l["text"] for l in out["lines"]] == ["[de] hola", "[de] adiós"] and out["lines"][1]["start"] == 1.5
        assert out["outputs"]["captioned_video" if False else "audio"]["voice"] == "de_voice"
        assert display.state()["lines"][0]["text"] == "[de] hola"
        assert store.get(rid).latest.renditions["wav"]  # the source is untouched

    def test_captions_without_a_target_keep_the_words_as_spoken(self, tmp_path):
        tool, _store, rid = self._tool(tmp_path)
        out = json.loads(tool.run(operation="translate_media", content=json.dumps({"artifact_id": rid, "outputs": ["subtitles"]})))
        assert [l["text"] for l in out["lines"]] == ["hola", "adiós"] and out["target_language"] == "es"

    def test_missing_pieces_are_named(self, tmp_path):
        tool, _store, rid = self._tool(tmp_path, transcriber=SimpleNamespace(name="null"))
        with pytest.raises(ValueError, match="video_understanding_provider"):
            tool.run(operation="translate_media", content=json.dumps({"artifact_id": rid, "target": "German"}))
        tool, _store, _rid = self._tool(tmp_path / "other")
        with pytest.raises(ValueError, match="no shared media"):
            tool.run(operation="translate_media", content=json.dumps({"artifact_id": "000000000000", "target": "German"}))


class TestServedSurfaces:
    def test_live_speech_can_be_routed_to_the_display_and_the_snapshot_shows_it(self, tmp_path, monkeypatch):
        import scripts.control_center as cc
        from src.config.settings import Settings
        from src.control_center.snapshot import snapshot

        for name in ("DEFAULT_ACCOUNTS_PATH", "DEFAULT_CONFIG_PATH", "DEFAULT_LEDGER_PATH"):
            monkeypatch.setattr(cc, name, tmp_path / f"{name}.json")
        monkeypatch.setattr(cc, "DEFAULT_STAGING_DIR", tmp_path / "staging")
        monkeypatch.setattr(cc, "DEFAULT_BACKUPS_DIR", tmp_path / "backups")
        system = su.build_live_system(Settings.from_env(
            env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
            artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
            knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
        ))
        assert snapshot(system.centre)["translation"]["outputs"] == list(translation_outputs())

        from tests.test_universal_voice_translation import _Heard, _interpreter

        system.runtime = replace(system.runtime, translation=_interpreter(_Heard("¿Dónde está la estación?", "es")))
        import base64

        status, body, _ = su.route_translate_speech(base64.b64encode(b"audio").decode(), None, ["en", "es"], "device",
                                                    system, outputs=["display"])
        data = json.loads(body)
        assert status == 200 and data["routed_to"] == ["display"]
        shown = snapshot(system.centre)["translation"]["display"]
        assert shown["target"] == "en" and shown["lines"][-1]["heard"] == "¿Dónde está la estación?"


# ---------------------------------------------------------------------------
# The Control Center voice commands reach what actually speaks
# ---------------------------------------------------------------------------


class _ScriptedVoices:
    """A speech engine standing in for a provider: real facts to plan against,
    and every utterance recorded with the profile it was spoken in."""

    speaks_across_languages = False

    def __init__(self):
        from src.channels.speech import Availability

        self._availability = Availability()
        self.spoken = []

    name = "scripted"

    @property
    def availability(self):
        return self._availability

    @property
    def available(self):
        return True

    def listen(self, timeout=None):
        return ""

    def available_voices(self):
        return (_option("Aria", "scripted", "english", None, "en-US", "female"),
                _option("Oliver", "scripted", "english", None, "en-GB", "male"),
                _option("Chiara", "scripted", "italian", None, "it-IT", "female"))

    def spoken_languages(self):
        return ("english", "italian")

    def pace_words(self):
        return ("slow", "normal", "fast")

    def synthesize(self, text, voice=None):
        return b"RIFF" + text.encode("utf-8")

    def speak(self, text, voice=None):
        self.spoken.append((text, voice))
        return True


class TestControlCenterVoiceCommands:
    def test_language_accent_pace_and_a_sample_are_managed_per_agent(self, tmp_path, monkeypatch):
        import scripts.control_center as cc
        from src.config.settings import Settings

        for name in ("DEFAULT_ACCOUNTS_PATH", "DEFAULT_CONFIG_PATH", "DEFAULT_LEDGER_PATH"):
            monkeypatch.setattr(cc, name, tmp_path / f"{name}.json")
        monkeypatch.setattr(cc, "DEFAULT_STAGING_DIR", tmp_path / "staging")
        monkeypatch.setattr(cc, "DEFAULT_BACKUPS_DIR", tmp_path / "backups")
        system = su.build_live_system(Settings.from_env(
            env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
            artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
            knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
        ))
        voices = _ScriptedVoices()
        system.channel._speech = voices

        def run(command):
            return cc.run_command(system.centre, command, 100, system.channel, system.conversation)

        providers = run("voice providers")
        assert "active: scripted" in providers and "speaks: english, italian" in providers and "paces: slow, normal, fast" in providers
        # U-VOICE-05: a provider's real style capability (or its honest
        # absence) is now shown here too, the same as accents and paces -
        # this scripted fixture reports none, truthfully.
        assert "styles: none reported" in providers
        assert run("voice Alfred language german").startswith("FAILED") and "It speaks: English, Italian" in run("voice Alfred language german")
        assert "Chiara" in run("voice Alfred language italian")
        # this provider's voices speak only their own language: no British accent in Italian
        assert "isn't something it can do" in run("voice Alfred accent british")
        assert "language replies are written in" in run("voice Alfred language clear")
        assert "Oliver" in run("voice Alfred accent british")
        assert "slow" in run("voice Alfred pace slow")
        listing = run("voice Alfred")
        assert "speaking with (scripted): Oliver" in listing and "pace: slow" in listing and "accent: british" in listing
        sample = run("voice Alfred test Good evening.")
        assert "Voice sample saved: artifact" in sample
        arthur = run("voice Arthur")  # another agent's voice is its own
        assert "accent: the chosen voice's own" in arthur and "pace: slow" not in arthur
